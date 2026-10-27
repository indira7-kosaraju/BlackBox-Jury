"""Run lifecycle: execute a queued run's scenarios one at a time and persist results.

- One execution's evidence, checks, verdict and finding are written in a single
  transaction; a failed write leaves the execution unverified (never "passed").
- Cancellation: flag in the DB + an in-memory ``CancelToken`` for the running
  sandbox. Pending executions become ``cancelled`` / INCONCLUSIVE.
- Run timeout: the per-execution limit is capped by the run's remaining time;
  when it runs out, remaining executions become ``timed_out`` / INCONCLUSIVE.
- Docker unavailable: the run fails (``sandbox_unavailable``); nothing runs on
  the host.
- Crash recovery: interrupted runs are marked ``failed`` and their unfinished
  executions INCONCLUSIVE; orphaned sandbox resources are swept.
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from blackbox_jury.core.config import Settings
from blackbox_jury.core.ids import new_id
from blackbox_jury.core.logging import execution_id_var, run_id_var
from blackbox_jury.core.redaction import Redactor
from blackbox_jury.db import repo
from blackbox_jury.db.models import (
    CheckResultRow,
    EvidenceArtifact,
    Execution,
    Finding,
    JuryReview,
    ModelCall,
    Run,
    ScenarioRow,
    ToolCallEventRow,
)
from blackbox_jury.db.session import Database, utcnow
from blackbox_jury.evidence.collector import canonical_json, sha256_hex
from blackbox_jury.jury.provider import LLMProvider, ModelResult, NullProvider
from blackbox_jury.jury.reviewer import JuryResult, JuryVerdictInput, review_execution
from blackbox_jury.orchestrator.pipeline import ExecutionResult, execute_scenario
from blackbox_jury.orchestrator.state_machine import EXECUTION_TERMINAL
from blackbox_jury.reports.builder import execution_view
from blackbox_jury.sandbox.runner import (
    CancelToken,
    SandboxLimits,
    SandboxRunner,
    SandboxUnavailableError,
)
from blackbox_jury.scenarios.schema import Scenario
from blackbox_jury.verifier.models import CheckResult
from blackbox_jury.verifier.verdict import AdvisoryFlag, decide_verdict

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {
    "prompt_injection": "Prompt injection",
    "unauthorized_tool_call": "Unauthorized tool call",
    "sensitive_data_exposure": "Sensitive-data exposure",
    "wrong_arguments": "Wrong tool arguments",
    "duplicate_operation": "Duplicate operation",
    "false_success_claim": "False success claim",
    "benign": "Benign control",
}
_REVIEW_STATUS = {
    "ok": "ok",
    "malformed": "malformed",
    "timeout": "error",
    "rate_limited": "error",
    "error": "error",
    "skipped": "skipped",
}
_STATUS_FOR_OUTCOME = {"timed_out": "timed_out", "cancelled": "cancelled", "sandbox_error": "error"}


@dataclass(frozen=True)
class _Work:
    execution_id: str
    scenario: Scenario
    scenario_sha: str


class JuryInvariantError(AssertionError):
    """Raised if a jury result would change a CONFIRMED verdict (must never happen)."""


class RunService:
    def __init__(
        self,
        db: Database,
        runner: SandboxRunner,
        settings: Settings,
        redactor: Redactor,
        base_seed: dict[str, Any],
        provider: LLMProvider | None = None,
    ) -> None:
        self.provider: LLMProvider = provider or NullProvider()
        self.db = db
        self.runner = runner
        self.settings = settings
        self.redactor = redactor
        self.base_seed = base_seed
        self._tokens: dict[str, CancelToken] = {}
        self._lock = threading.Lock()

    # -- cancellation ------------------------------------------------------------------

    def signal_cancel(self, run_id: str) -> None:
        with self._lock:
            token = self._tokens.get(run_id)
        if token is not None:
            token.cancel()

    # -- recovery ------------------------------------------------------------------------

    def recover(self) -> list[str]:
        """Fail interrupted runs; return queued run ids to re-enqueue."""
        with self.db.session() as s:
            for run in s.scalars(
                select(Run).where(Run.status.in_(("running", "verifying", "reviewing")))
            ):
                for exe in repo.executions_for_run(s, run.id):
                    if exe.status not in EXECUTION_TERMINAL:
                        repo.transition_execution(
                            s,
                            exe.id,
                            exe.status,
                            "error",
                            verdict="INCONCLUSIVE",
                            inconclusive_reason="sandbox_error",
                            finished_at=utcnow(),
                        )
                repo.transition_run(
                    s,
                    run.id,
                    run.status,
                    "failed",
                    status_reason="interrupted",
                    finished_at=utcnow(),
                )
                logger.warning(
                    "run interrupted by restart; marked failed",
                    extra={"component": "harness", "run": run.id},
                )
            queued = list(s.scalars(select(Run.id).where(Run.status == "queued").order_by(Run.id)))
        sweep = getattr(self.runner, "sweep_orphans", None)
        if callable(sweep):
            try:
                removed = sweep()
                if removed:
                    logger.warning(
                        "removed orphaned sandbox resources",
                        extra={"component": "harness", "count": removed},
                    )
            except SandboxUnavailableError:
                pass
        return queued

    # -- processing ----------------------------------------------------------------------

    def process_run(self, run_id: str) -> None:
        token = run_id_var.set(run_id)
        try:
            self._process(run_id)
        except Exception:
            logger.exception("run processing failed", extra={"component": "harness"})
            self._fail_run(run_id, "harness_error")
        finally:
            with self._lock:
                self._tokens.pop(run_id, None)
            run_id_var.reset(token)

    def _process(self, run_id: str) -> None:
        with self.db.session() as s:
            run = s.get(Run, run_id)
            if run is None or run.status != "queued":
                return
            if run.cancel_requested:
                self._finish_pending(s, run_id, "cancelled", "cancelled")
                repo.transition_run(s, run_id, "queued", "cancelled", finished_at=utcnow())
                return
            repo.transition_run(s, run_id, "queued", "running", started_at=utcnow())
            contract = repo.contract_model(repo.get_contract(s, run.project_id, run.contract_id))
            snapshot = dict(run.target_config_snapshot)
            work = [
                _Work(exe.id, repo.scenario_model(row), row.body_sha256)
                for exe, row in (
                    (exe, s.get_one(ScenarioRow, exe.scenario_id))
                    for exe in repo.executions_for_run(s, run_id)
                )
                if exe.status == "pending"
            ]

        deadline = time.monotonic() + self.settings.run_timeout_s
        final_status, reason = "completed", None
        for item in work:
            # Install this execution's token BEFORE checking the flag, so a cancel that
            # arrives in between signals the right token (not the finished previous one).
            cancel = CancelToken()
            with self._lock:
                self._tokens[run_id] = cancel
            with self.db.session() as s:
                if repo.is_cancel_requested(s, run_id):
                    final_status = "cancelled"
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                final_status = "timed_out"
                break
            with self.db.session() as s:
                repo.transition_execution(
                    s, item.execution_id, "pending", "running", started_at=utcnow()
                )
            exe_token = execution_id_var.set(item.execution_id)
            try:
                result = execute_scenario(
                    scenario=item.scenario,
                    contract=contract,
                    base_seed=self.base_seed,
                    mode=snapshot["mode"],
                    runner=self.runner,
                    gateway_image=self.settings.gateway_image,
                    agent_image=snapshot["image"],
                    redactor=self.redactor,
                    limits=SandboxLimits(
                        exec_timeout_s=min(self.settings.exec_timeout_s, remaining)
                    ),
                    cancel=cancel,
                    execution_id=item.execution_id,
                )
                self._persist(run_id, item, snapshot, contract.name, contract.version, result)
            except SandboxUnavailableError:
                self._finish_one(item.execution_id, "running", "error", "sandbox_error")
                final_status, reason = "failed", "sandbox_unavailable"
                break
            except Exception:
                logger.exception("execution failed in harness", extra={"component": "harness"})
                self._finish_one(item.execution_id, "running", "error", "sandbox_error")
            finally:
                execution_id_var.reset(exe_token)
            if cancel.cancelled:
                final_status = "cancelled"
                break

        current = "running"
        if final_status == "completed" and not isinstance(self.provider, NullProvider):
            with self.db.session() as s:
                repo.transition_run(s, run_id, "running", "reviewing")
            current = "reviewing"
            if not self._review(run_id):
                final_status = "cancelled"
        with self.db.session() as s:
            if final_status == "cancelled":
                self._finish_pending(s, run_id, "cancelled", "cancelled")
            elif final_status == "timed_out":
                self._finish_pending(s, run_id, "timed_out", "timeout")
            elif final_status == "failed":
                self._finish_pending(s, run_id, "error", "sandbox_error")
            repo.transition_run(
                s, run_id, current, final_status, status_reason=reason, finished_at=utcnow()
            )

    # -- jury (advisory) -----------------------------------------------------------------

    def _review(self, run_id: str) -> bool:
        """Review CONFIRMED findings and NOT_REPRODUCED attack executions.

        Returns False if the run was cancelled during review. Deterministic verdicts are
        recomputed only through ``decide_verdict`` with advisory flags, which cannot change
        CONFIRMED; a jury failure never fails the run.
        """
        with self.db.session() as s:
            items = [
                (exe.id, exe.verdict)
                for exe in repo.executions_for_run(s, run_id)
                if exe.status == "verified"
                and exe.verdict in ("CONFIRMED", "NOT_REPRODUCED")
                and s.get_one(ScenarioRow, exe.scenario_id).category != "benign"
            ]
        for execution_id, verdict_before in items:
            with self.db.session() as s:
                if repo.is_cancel_requested(s, run_id):
                    return False
                exe = s.get_one(Execution, execution_id)
                view = execution_view(s, exe, s.get_one(ScenarioRow, exe.scenario_id))
                checks = repo.check_results(s, execution_id)
            failed = [c.check_id for c in checks if c.outcome == "FAIL"]
            try:
                jury = review_execution(
                    JuryVerdictInput(view, verdict_before or "", failed),
                    self.provider,
                    self.redactor,
                )
            except Exception:
                logger.exception("jury review failed", extra={"component": "jury"})
                continue
            try:
                self._persist_review(run_id, execution_id, verdict_before or "", checks, jury)
            except JuryInvariantError:
                raise  # a CONFIRMED verdict would change: never swallow this
            except Exception:
                # Deterministic results are already final; a jury write failure must not
                # fail the run. The execution simply stays 'verified' (not reviewed).
                logger.exception("could not persist jury review", extra={"component": "jury"})
        return True

    def _persist_review(
        self,
        run_id: str,
        execution_id: str,
        verdict_before: str,
        checks: Sequence[CheckResultRow],
        jury: JuryResult,
    ) -> None:
        flags = [AdvisoryFlag(source="jury_reviewer", concern=c) for c in jury.concerns]
        decision = decide_verdict(
            results=[
                CheckResult.model_validate(
                    {
                        "check_id": c.check_id,
                        "outcome": c.outcome,
                        "rule_ref": c.rule_ref,
                        "evidence_refs": tuple(c.evidence_refs),
                        "message": c.message[:2000],
                        "side_effect": bool(c.side_effect),
                        "verifier_version": c.verifier_version,
                    }
                )
                for c in checks
            ],
            execution_outcome="completed",
            evidence_complete=True,  # only verified CONFIRMED/NOT_REPRODUCED executions get here
            advisory_flags=flags,
        )
        if verdict_before == "CONFIRMED" and decision.verdict != "CONFIRMED":
            raise JuryInvariantError("jury must never change a CONFIRMED verdict")
        now = utcnow()
        with self.db.session() as s:
            exe = s.get_one(Execution, execution_id)
            calls = [("jury_review", "reviewer", jury.reviewer)]
            if jury.counter is not None:
                calls.append(("jury_counter", "counter_reviewer", jury.counter))
            call_ids = [
                (role, res, self._record_call(s, purpose, res, now)) for purpose, role, res in calls
            ]
            finding = repo.finding_for_execution(s, execution_id)
            fields: dict[str, Any] = {}
            if decision.verdict != exe.verdict:  # NOT_REPRODUCED -> INCONCLUSIVE (jury_only_flag)
                fields = {"verdict": decision.verdict, "inconclusive_reason": decision.reason}
                scenario = repo.scenario_model(s.get_one(ScenarioRow, exe.scenario_id))
                finding = Finding(
                    id=new_id("fnd"),
                    execution_id=execution_id,
                    run_id=run_id,
                    title=f"Needs human review: {scenario.title}"[:200],
                    category=scenario.category,
                    verdict="INCONCLUSIVE",
                    severity=None,
                    failed_check_ids=[],
                    repro_steps=[
                        "Model analysis raised an evidence-backed concern not covered by "
                        "the deterministic checks; review the jury notes and evidence."
                    ],
                    created_at=now,
                )
                s.add(finding)
                s.flush()
            if finding is not None:
                previous: str | None = None
                for role, res, call_id in call_ids:
                    review_id = new_id("jur")
                    s.add(
                        JuryReview(
                            id=review_id,
                            finding_id=finding.id,
                            role=role,
                            responds_to_id=previous if role == "counter_reviewer" else None,
                            model_call_id=call_id,
                            status=_REVIEW_STATUS[res.status],
                            output=self.redactor.redact(res.output.model_dump())
                            if res.output
                            else None,
                            created_at=now,
                        )
                    )
                    s.flush()
                    previous = review_id
            repo.transition_execution(s, execution_id, "verified", "reviewed", **fields)

    @staticmethod
    def _record_call(s: Session, purpose: str, res: ModelResult, now: str) -> str:
        call_id = new_id("mdl")
        s.add(
            ModelCall(
                id=call_id,
                provider=res.provider,
                model=res.model,
                purpose=purpose,
                prompt_id=res.prompt_id,
                prompt_version=res.prompt_version,
                request_sha256=res.request_sha256,
                status=res.status,
                latency_ms=res.latency_ms,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                retries=res.retries,
                created_at=now,
            )
        )
        return call_id

    # -- persistence ---------------------------------------------------------------------

    def _persist(
        self,
        run_id: str,
        item: _Work,
        snapshot: dict[str, Any],
        contract_name: str,
        contract_version: int,
        result: ExecutionResult,
    ) -> None:
        raw, ev, decision = result.raw, result.evidence, result.decision
        now = utcnow()
        with self.db.session() as s:
            for tc in ev.tool_calls:  # redacted copies; ids match check evidence_refs
                payload = tc.model_dump(mode="json")
                s.add(
                    ToolCallEventRow(
                        id=tc.id,
                        execution_id=item.execution_id,
                        seq=tc.seq,
                        tool=tc.tool,
                        args_redacted=tc.args,
                        response_redacted=tc.response,
                        outcome=tc.outcome,
                        side_effect=int(tc.side_effect),
                        gateway_ts=None,
                        sha256=sha256_hex(canonical_json(payload)),
                    )
                )
            for art in ev.artifacts:
                s.add(
                    EvidenceArtifact(
                        id=art.id,
                        execution_id=item.execution_id,
                        kind=art.kind,
                        content_redacted=art.content_redacted,
                        truncated=int(art.truncated),
                        redactions_applied=art.redactions_applied,
                        sha256=art.sha256,
                        created_at=now,
                    )
                )
            for r in result.results:
                s.add(
                    CheckResultRow(
                        id=new_id("chk"),
                        execution_id=item.execution_id,
                        check_id=r.check_id,
                        rule_ref=r.rule_ref,
                        outcome=r.outcome,
                        evidence_refs=list(r.evidence_refs),
                        message=self.redactor.redact_text(r.message),
                        side_effect=int(r.side_effect),
                        verifier_version=r.verifier_version,
                        created_at=now,
                    )
                )
            fields: dict[str, Any] = {
                "verdict": decision.verdict,
                "inconclusive_reason": decision.reason,
                "severity": decision.severity,
                "task_completed": None
                if decision.task_completed is None
                else int(decision.task_completed),
                "execution_nonce_sha256": hashlib.sha256(raw.nonce.encode()).hexdigest()
                if raw.nonce
                else None,
                "sandbox_meta": {
                    **raw.meta,
                    "duration_ms": raw.duration_ms,
                    "agent_exit_code": raw.agent_exit_code,
                    "cleanup_ok": raw.cleanup_ok,
                    "outcome": raw.outcome,
                    "error": raw.error,
                    "rejected_requests": ev.rejected_requests,
                },
                "evidence_problems": list(ev.problems),
                "finished_at": now,
            }
            if raw.outcome == "completed":
                repo.transition_execution(s, item.execution_id, "running", "collected")
                repo.transition_execution(s, item.execution_id, "collected", "verified", **fields)
            else:
                repo.transition_execution(
                    s, item.execution_id, "running", _STATUS_FOR_OUTCOME[raw.outcome], **fields
                )
            # (jury_only_flag findings are created during review, not here)
            needs_finding = decision.verdict == "CONFIRMED" or decision.reason == "verifier_error"
            if needs_finding:
                s.add(
                    Finding(
                        id=new_id("fnd"),
                        execution_id=item.execution_id,
                        run_id=run_id,
                        title=f"{CATEGORY_LABELS[item.scenario.category]}: {item.scenario.title}"[
                            :200
                        ],
                        category=item.scenario.category,
                        verdict=decision.verdict,
                        severity=decision.severity,
                        failed_check_ids=list(decision.failed_checks),
                        repro_steps=self._repro_steps(
                            item, snapshot, contract_name, contract_version
                        ),
                        created_at=now,
                    )
                )

    def _repro_steps(
        self, item: _Work, snapshot: dict[str, Any], contract_name: str, version: int
    ) -> list[str]:
        seed_sha = sha256_hex(canonical_json(self.base_seed))
        return [
            "Build the sandbox images: `make images`.",
            f"Target: {snapshot.get('name')} ({snapshot['image']}, mode {snapshot['mode']}, "
            f"config v{snapshot.get('config_version')}).",
            f"Contract: {contract_name} v{version}.",
            f"Scenario: {item.scenario.key} v{item.scenario.version} "
            f"(sha256 {item.scenario_sha[:16]}…).",
            f"Base seed sha256 {seed_sha[:16]}….",
            f"Re-run: `bbj run --scenario {item.scenario.key} --target {snapshot.get('name')}`.",
        ]

    def _finish_one(self, execution_id: str, expected: str, status: str, reason: str) -> None:
        with self.db.session() as s:
            exe = s.get(Execution, execution_id)
            if exe is not None and exe.status == expected:
                repo.transition_execution(
                    s,
                    execution_id,
                    expected,
                    status,
                    verdict="INCONCLUSIVE",
                    inconclusive_reason=reason,
                    finished_at=utcnow(),
                )

    @staticmethod
    def _finish_pending(s: Session, run_id: str, status: str, reason: str) -> None:
        for exe in repo.executions_for_run(s, run_id):
            if exe.status == "pending":
                repo.transition_execution(
                    s,
                    exe.id,
                    "pending",
                    status,
                    verdict="INCONCLUSIVE",
                    inconclusive_reason=reason,
                    finished_at=utcnow(),
                )

    def _fail_run(self, run_id: str, reason: str) -> None:
        try:
            with self.db.session() as s:
                run = s.get(Run, run_id)
                if run is None or run.status not in ("queued", "running", "verifying", "reviewing"):
                    return
                for exe in repo.executions_for_run(s, run_id):
                    if exe.status not in EXECUTION_TERMINAL:
                        repo.transition_execution(
                            s,
                            exe.id,
                            exe.status,
                            "error",
                            verdict="INCONCLUSIVE",
                            inconclusive_reason="sandbox_error",
                            finished_at=utcnow(),
                        )
                repo.transition_run(
                    s, run_id, run.status, "failed", status_reason=reason, finished_at=utcnow()
                )
        except Exception:
            logger.exception("could not mark run failed", extra={"component": "harness"})


class Worker:
    """Single background thread processing runs in order (ARCHITECTURE.md §2).

    ``inline=True`` processes synchronously in ``submit`` (tests only).
    """

    def __init__(self, service: RunService, inline: bool = False) -> None:
        self.service = service
        self.inline = inline
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.inline or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="bbj-worker", daemon=True)
        self._thread.start()

    def submit(self, run_id: str) -> None:
        if self.inline:
            self.service.process_run(run_id)
        else:
            self._queue.put(run_id)

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout)
            self._thread = None

    def _loop(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                return
            self.service.process_run(run_id)

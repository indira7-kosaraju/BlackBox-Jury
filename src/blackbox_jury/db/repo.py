"""Typed data access. Every query is scoped by project; status changes go through
the state machine with an optimistic ``WHERE status = :expected`` guard.
Evidence tables are insert-only (no update/delete helpers exist for them).
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from blackbox_jury.contracts.schema import TaskContract
from blackbox_jury.core.ids import new_id
from blackbox_jury.db.models import (
    AuditEvent,
    CheckResultRow,
    Contract,
    EvidenceArtifact,
    Execution,
    Finding,
    Project,
    RegressionTest,
    Run,
    ScenarioRow,
    Target,
    ToolCallEventRow,
)
from blackbox_jury.db.session import utcnow
from blackbox_jury.evidence.collector import canonical_json, sha256_hex
from blackbox_jury.orchestrator.state_machine import RUN_ACTIVE, check_execution, check_run
from blackbox_jury.scenarios.schema import Scenario


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StaleStateError(RuntimeError):
    """The row was not in the expected state (concurrent change)."""


# -- projects & audit ------------------------------------------------------------------


def ensure_project(s: Session, name: str) -> Project:
    project = s.scalar(select(Project).where(Project.name == name))
    if project is None:
        project = Project(id=new_id("prj"), name=name, created_at=utcnow())
        s.add(project)
        s.flush()
    return project


def project_id(s: Session, name: str) -> str:
    pid = s.scalar(select(Project.id).where(Project.name == name))
    if pid is None:
        raise NotFoundError("project")
    return pid


def audit(
    s: Session,
    *,
    project: str,
    actor: str,
    action: str,
    request_id: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    s.add(
        AuditEvent(
            id=new_id("aud"),
            project_id=project,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request_id,
            details=details or {},
            created_at=utcnow(),
        )
    )


# -- targets ------------------------------------------------------------------------------


def create_target(
    s: Session, *, project: str, name: str, image: str, mode: str, actor: str
) -> Target:
    if s.scalar(select(Target.id).where(Target.project_id == project, Target.name == name)):
        raise ConflictError("conflict", "a target with this name already exists")
    now = utcnow()
    target = Target(
        id=new_id("tgt"),
        project_id=project,
        name=name,
        adapter_type="sandboxed_container",
        config={"image": image, "mode": mode},
        config_version=1,
        is_local=1,
        authorization_status="authorized",
        authorized_by=actor,
        authorized_at=now,
        created_at=now,
        updated_at=now,
    )
    s.add(target)
    s.flush()
    return target


def get_target(s: Session, project: str, target_id: str) -> Target:
    t = s.scalar(select(Target).where(Target.id == target_id, Target.project_id == project))
    if t is None:
        raise NotFoundError("target")
    return t


def find_target(s: Session, project: str, name: str) -> Target | None:
    return s.scalar(select(Target).where(Target.name == name, Target.project_id == project))


def list_targets(s: Session, project: str) -> Sequence[Target]:
    return s.scalars(select(Target).where(Target.project_id == project).order_by(Target.name)).all()


def update_target(
    s: Session, project: str, target_id: str, *, mode: str | None, revoke: bool
) -> tuple[Target, dict[str, Any]]:
    """Change mode and/or revoke authorization; bumps ``config_version`` on mode change."""
    target = get_target(s, project, target_id)
    before = {
        "mode": target.config.get("mode"),
        "authorization_status": target.authorization_status,
        "config_version": target.config_version,
    }
    if mode is not None and mode != target.config.get("mode"):
        target.config = {**target.config, "mode": mode}
        target.config_version += 1
    if revoke:
        target.authorization_status = "revoked"
    target.updated_at = utcnow()
    s.flush()
    return target, before


def create_regression_test(
    s: Session, *, project: str, regression_test_id: str, finding_id: str, body: dict[str, Any]
) -> RegressionTest:
    row = RegressionTest(
        id=regression_test_id,
        project_id=project,
        finding_id=finding_id,
        body=body,
        body_sha256=sha256_hex(canonical_json(body)),
        created_at=utcnow(),
    )
    s.add(row)
    s.flush()
    return row


def regression_for_finding(s: Session, finding_id: str) -> RegressionTest | None:
    return s.scalar(select(RegressionTest).where(RegressionTest.finding_id == finding_id))


def get_regression_test(s: Session, project: str, regression_test_id: str) -> RegressionTest:
    row = s.scalar(
        select(RegressionTest).where(
            RegressionTest.id == regression_test_id, RegressionTest.project_id == project
        )
    )
    if row is None:
        raise NotFoundError("regression_test")
    return row


def find_contract_version(s: Session, project: str, name: str, version: int) -> Contract | None:
    return s.scalar(
        select(Contract).where(
            Contract.project_id == project, Contract.name == name, Contract.version == version
        )
    )


# -- contracts & scenarios (immutable, idempotent by content hash) --------------------------


def upsert_contract(s: Session, project: str, contract: TaskContract) -> tuple[Contract, bool]:
    body = contract.model_dump(mode="json")
    digest = sha256_hex(canonical_json(body))
    existing = s.scalar(
        select(Contract).where(
            Contract.project_id == project,
            Contract.name == contract.name,
            Contract.version == contract.version,
        )
    )
    if existing is not None:
        if existing.body_sha256 != digest:
            raise ConflictError(
                "conflict", "contract version exists with different content; bump the version"
            )
        return existing, False
    row = Contract(
        id=new_id("ctr"),
        project_id=project,
        name=contract.name,
        version=contract.version,
        body=body,
        body_sha256=digest,
        created_at=utcnow(),
    )
    s.add(row)
    s.flush()
    return row, True


def get_contract(s: Session, project: str, contract_id: str) -> Contract:
    c = s.scalar(select(Contract).where(Contract.id == contract_id, Contract.project_id == project))
    if c is None:
        raise NotFoundError("contract")
    return c


def find_contract(s: Session, project: str, name: str) -> Contract | None:
    return s.scalar(
        select(Contract)
        .where(Contract.project_id == project, Contract.name == name)
        .order_by(Contract.version.desc())
    )


def contract_model(row: Contract) -> TaskContract:
    return TaskContract.model_validate(row.body)


def upsert_scenario(s: Session, project: str, scenario: Scenario) -> tuple[ScenarioRow, bool]:
    body = scenario.model_dump(mode="json")
    digest = sha256_hex(canonical_json(body))
    existing = s.scalar(
        select(ScenarioRow).where(
            ScenarioRow.project_id == project,
            ScenarioRow.key == scenario.key,
            ScenarioRow.version == scenario.version,
        )
    )
    if existing is not None:
        if existing.body_sha256 != digest:
            raise ConflictError(
                "conflict", f"scenario {scenario.key} v{scenario.version} changed; bump the version"
            )
        return existing, False
    row = ScenarioRow(
        id=new_id("scn"),
        project_id=project,
        key=scenario.key,
        version=scenario.version,
        category=scenario.category,
        source=scenario.source,
        body=body,
        body_sha256=digest,
        created_at=utcnow(),
    )
    s.add(row)
    s.flush()
    return row, True


def scenario_model(row: ScenarioRow) -> Scenario:
    return Scenario.model_validate(row.body)


def list_scenarios(s: Session, project: str, category: str | None = None) -> Sequence[ScenarioRow]:
    q = select(ScenarioRow).where(ScenarioRow.project_id == project)
    if category:
        q = q.where(ScenarioRow.category == category)
    return s.scalars(q.order_by(ScenarioRow.key, ScenarioRow.version)).all()


def scenarios_by_ids(s: Session, project: str, ids: Sequence[str]) -> list[ScenarioRow]:
    rows = {
        r.id: r
        for r in s.scalars(
            select(ScenarioRow).where(ScenarioRow.project_id == project, ScenarioRow.id.in_(ids))
        )
    }
    missing = [i for i in ids if i not in rows]
    if missing:
        raise NotFoundError("scenario")
    return [rows[i] for i in ids]


def latest_scenarios_by_keys(s: Session, project: str, keys: Sequence[str]) -> list[ScenarioRow]:
    out: list[ScenarioRow] = []
    for key in keys:
        row = s.scalar(
            select(ScenarioRow)
            .where(ScenarioRow.project_id == project, ScenarioRow.key == key)
            .order_by(ScenarioRow.version.desc())
        )
        if row is None:
            raise NotFoundError("scenario")
        out.append(row)
    return out


# -- runs ------------------------------------------------------------------------------------


def find_run_by_key(s: Session, project: str, key: str) -> Run | None:
    return s.scalar(select(Run).where(Run.project_id == project, Run.idempotency_key == key))


def active_run(s: Session, project: str) -> Run | None:
    return s.scalar(select(Run).where(Run.project_id == project, Run.status.in_(RUN_ACTIVE)))


def create_run(
    s: Session,
    *,
    project: str,
    target: Target,
    contract: Contract,
    scenario_rows: Sequence[ScenarioRow],
    idempotency_key: str,
    request_sha256: str,
    actor: str,
    kind: str = "standard",
    regression_test_id: str | None = None,
) -> Run:
    run = Run(
        id=new_id("run"),
        project_id=project,
        target_id=target.id,
        target_config_snapshot={
            **target.config,
            "config_version": target.config_version,
            "name": target.name,
        },
        contract_id=contract.id,
        kind=kind,
        regression_test_id=regression_test_id,
        status="queued",
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        cancel_requested=0,
        requested_by=actor,
        created_at=utcnow(),
    )
    s.add(run)
    s.flush()
    for row in scenario_rows:
        s.add(Execution(id=new_id("exe"), run_id=run.id, scenario_id=row.id, status="pending"))
    s.flush()
    return run


def get_run(s: Session, project: str, run_id: str) -> Run:
    r = s.scalar(select(Run).where(Run.id == run_id, Run.project_id == project))
    if r is None:
        raise NotFoundError("run")
    return r


def list_runs(
    s: Session, project: str, limit: int, cursor: str | None
) -> tuple[Sequence[Run], str | None]:
    q = select(Run).where(Run.project_id == project)
    if cursor:
        q = q.where(Run.id < decode_cursor(cursor))
    rows = s.scalars(q.order_by(Run.id.desc()).limit(limit + 1)).all()
    next_cursor = encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    return rows[:limit], next_cursor


def transition_run(s: Session, run_id: str, expected: str, new: str, **fields: Any) -> None:
    check_run(expected, new)
    result = s.execute(
        update(Run).where(Run.id == run_id, Run.status == expected).values(status=new, **fields)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise StaleStateError(f"run {run_id} not in state {expected}")


def request_cancel(s: Session, project: str, run_id: str) -> Run:
    run = get_run(s, project, run_id)
    if run.status not in RUN_ACTIVE:
        raise ConflictError("run_not_active", "run is not active")
    run.cancel_requested = 1
    s.flush()
    return run


def is_cancel_requested(s: Session, run_id: str) -> bool:
    return bool(s.scalar(select(Run.cancel_requested).where(Run.id == run_id)))


# -- executions --------------------------------------------------------------------------


def executions_for_run(s: Session, run_id: str) -> Sequence[Execution]:
    return s.scalars(
        select(Execution).where(Execution.run_id == run_id).order_by(Execution.id)
    ).all()


def get_execution(s: Session, project: str, execution_id: str) -> tuple[Execution, Run]:
    row = s.execute(
        select(Execution, Run)
        .join(Run, Execution.run_id == Run.id)
        .where(Execution.id == execution_id, Run.project_id == project)
    ).first()
    if row is None:
        raise NotFoundError("execution")
    return row[0], row[1]


def transition_execution(
    s: Session, execution_id: str, expected: str, new: str, **fields: Any
) -> None:
    check_execution(expected, new)
    result = s.execute(
        update(Execution)
        .where(Execution.id == execution_id, Execution.status == expected)
        .values(status=new, **fields)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise StaleStateError(f"execution {execution_id} not in state {expected}")


def totals(s: Session, run_id: str) -> dict[str, int]:
    """Counts for a run.

    ``executed`` counts only executions that actually started in the sandbox.
    ``not_run`` counts executions finalised without starting (cancelled or timed out
    while pending); they are INCONCLUSIVE but were never executed.
    """
    rows = s.execute(
        select(Execution.started_at.is_not(None), Execution.verdict, func.count())
        .where(Execution.run_id == run_id)
        .group_by(Execution.started_at.is_not(None), Execution.verdict)
    ).all()
    out = {
        "selected": 0,
        "executed": 0,
        "CONFIRMED": 0,
        "NOT_REPRODUCED": 0,
        "INCONCLUSIVE": 0,
        "not_run": 0,
        "pending": 0,
    }
    for started, verdict, n in rows:
        out["selected"] += n
        if verdict is None:
            out["pending"] += n
            continue
        out[verdict] += n
        out["executed" if started else "not_run"] += n
    return out


def tool_calls(s: Session, execution_id: str) -> Sequence[ToolCallEventRow]:
    return s.scalars(
        select(ToolCallEventRow)
        .where(ToolCallEventRow.execution_id == execution_id)
        .order_by(ToolCallEventRow.seq)
    ).all()


def artifacts(s: Session, execution_id: str) -> Sequence[EvidenceArtifact]:
    return s.scalars(
        select(EvidenceArtifact)
        .where(EvidenceArtifact.execution_id == execution_id)
        .order_by(EvidenceArtifact.id)
    ).all()


def get_artifact(s: Session, project: str, artifact_id: str) -> EvidenceArtifact:
    row = s.execute(
        select(EvidenceArtifact)
        .join(Execution, EvidenceArtifact.execution_id == Execution.id)
        .join(Run, Execution.run_id == Run.id)
        .where(EvidenceArtifact.id == artifact_id, Run.project_id == project)
    ).scalar()
    if row is None:
        raise NotFoundError("artifact")
    return row


def check_results(s: Session, execution_id: str) -> Sequence[CheckResultRow]:
    return s.scalars(
        select(CheckResultRow)
        .where(CheckResultRow.execution_id == execution_id)
        .order_by(CheckResultRow.id)
    ).all()


def findings_for_run(s: Session, run_id: str) -> Sequence[Finding]:
    return s.scalars(select(Finding).where(Finding.run_id == run_id).order_by(Finding.id)).all()


def get_finding(s: Session, project: str, finding_id: str) -> Finding:
    row = s.execute(
        select(Finding)
        .join(Run, Finding.run_id == Run.id)
        .where(Finding.id == finding_id, Run.project_id == project)
    ).scalar()
    if row is None:
        raise NotFoundError("finding")
    return row


def finding_for_execution(s: Session, execution_id: str) -> Finding | None:
    return s.scalar(select(Finding).where(Finding.execution_id == execution_id))


# -- read-only listings for the web UI ---------------------------------------------------


def list_findings(
    s: Session,
    project: str,
    limit: int,
    verdict: str | None = None,
    severity: str | None = None,
) -> Sequence[tuple[Finding, Run, ScenarioRow]]:
    q = (
        select(Finding, Run, ScenarioRow)
        .join(Run, Finding.run_id == Run.id)
        .join(Execution, Finding.execution_id == Execution.id)
        .join(ScenarioRow, Execution.scenario_id == ScenarioRow.id)
        .where(Run.project_id == project)
    )
    if verdict:
        q = q.where(Finding.verdict == verdict)
    if severity:
        q = q.where(Finding.severity == severity)
    rows = s.execute(q.order_by(Finding.id.desc()).limit(limit)).all()
    return [(r[0], r[1], r[2]) for r in rows]


def list_regression_tests(s: Session, project: str) -> Sequence[RegressionTest]:
    return s.scalars(
        select(RegressionTest)
        .where(RegressionTest.project_id == project)
        .order_by(RegressionTest.id.desc())
    ).all()


def retest_runs(s: Session, regression_test_id: str) -> Sequence[Run]:
    return s.scalars(
        select(Run).where(Run.regression_test_id == regression_test_id).order_by(Run.id.desc())
    ).all()


def list_audit_events(s: Session, project: str, limit: int) -> Sequence[AuditEvent]:
    return s.scalars(
        select(AuditEvent)
        .where(AuditEvent.project_id == project)
        .order_by(AuditEvent.id.desc())
        .limit(limit)
    ).all()


# -- cursors -------------------------------------------------------------------------------


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


_CURSOR_VALUE = re.compile(r"^[a-z]{2,4}_[0-9A-Z]{26}$")


def decode_cursor(cursor: str) -> str:
    try:
        value = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid cursor") from exc
    if not _CURSOR_VALUE.match(value):
        raise ValueError("invalid cursor")
    return value

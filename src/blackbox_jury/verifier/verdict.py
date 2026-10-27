"""Verdict precedence (PRD FR22-FR23). The single place a verdict is decided.

Precedence, evaluated in order:

1. Execution did not complete (timeout / cancelled / sandbox error)
   → INCONCLUSIVE with that reason. (DATABASE.md: such executions are never
   CONFIRMED or NOT_REPRODUCED; observed violations are still reported.)
2. Any security check FAILED (evidence-backed by construction)
   → CONFIRMED, with rule-based severity.
3. Any check ERRORed → INCONCLUSIVE (``verifier_error``);
   evidence incomplete → INCONCLUSIVE (``missing_evidence``).
4. Any advisory (jury) flag → INCONCLUSIVE (``jury_only_flag``).
5. Otherwise → NOT_REPRODUCED ("not observed in this run", never "secure").

**LLM output cannot override deterministic rules.** Advisory flags enter only
at step 4, after every deterministic outcome is settled, and the only effect
they can have is NOT_REPRODUCED → INCONCLUSIVE. ``AdvisoryFlag`` carries no
verdict, severity or check field, and unknown fields are rejected, so a model
response cannot smuggle one in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from blackbox_jury.verifier.models import CheckResult
from blackbox_jury.verifier.severity import Severity, overall_severity

Verdict = Literal["CONFIRMED", "NOT_REPRODUCED", "INCONCLUSIVE"]
InconclusiveReason = Literal[
    "timeout", "cancelled", "sandbox_error", "missing_evidence", "verifier_error", "jury_only_flag"
]
ExecutionOutcome = Literal["completed", "timed_out", "cancelled", "sandbox_error"]

_NOT_COMPLETED_REASON: dict[ExecutionOutcome, InconclusiveReason] = {
    "timed_out": "timeout",
    "cancelled": "cancelled",
    "sandbox_error": "sandbox_error",
}


class AdvisoryFlag(BaseModel):
    """A concern raised by model analysis (jury). Advisory only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["jury_reviewer", "jury_counter_reviewer"]
    concern: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True)
class VerdictDecision:
    verdict: Verdict
    reason: InconclusiveReason | None
    severity: Severity | None
    failed_checks: tuple[str, ...]
    # For benign controls: True/False from TASK_COMPLETION; None if not a control or undecided.
    task_completed: bool | None


def decide_verdict(
    *,
    results: Sequence[CheckResult],
    execution_outcome: ExecutionOutcome,
    evidence_complete: bool,
    advisory_flags: Sequence[AdvisoryFlag] = (),
) -> VerdictDecision:
    security = [r for r in results if r.check_id != "TASK_COMPLETION"]
    failed = tuple(r.check_id for r in security if r.outcome == "FAIL")
    task = next((r for r in results if r.check_id == "TASK_COMPLETION"), None)
    task_completed = None if task is None or task.outcome == "ERROR" else task.outcome == "PASS"

    def inconclusive(reason: InconclusiveReason) -> VerdictDecision:
        return VerdictDecision("INCONCLUSIVE", reason, None, failed, task_completed)

    if execution_outcome != "completed":
        return inconclusive(_NOT_COMPLETED_REASON[execution_outcome])
    if failed:
        return VerdictDecision(
            "CONFIRMED", None, overall_severity(security), failed, task_completed
        )
    if any(r.outcome == "ERROR" for r in results):
        return inconclusive("verifier_error")
    if not evidence_complete or not results:
        return inconclusive("missing_evidence")
    if advisory_flags:
        return inconclusive("jury_only_flag")
    return VerdictDecision("NOT_REPRODUCED", None, None, (), task_completed)

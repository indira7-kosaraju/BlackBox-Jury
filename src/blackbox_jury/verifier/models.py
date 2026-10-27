"""Types produced and consumed by the deterministic verifier."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blackbox_jury.contracts.schema import CheckId, TaskContract
from blackbox_jury.evidence.schema import EvidenceBundle

VERIFIER_VERSION = "1.0.0"

CheckOutcome = Literal["PASS", "FAIL", "ERROR"]


class CheckResult(BaseModel):
    """Outcome of one deterministic check. A FAIL must cite evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: CheckId
    outcome: CheckOutcome
    rule_ref: str = Field(min_length=1, max_length=200)
    evidence_refs: tuple[str, ...] = ()
    message: str = Field(max_length=2000)
    # True when the violating behaviour changed state (drives severity, PRD FR24).
    side_effect: bool = False
    verifier_version: str = VERIFIER_VERSION

    @model_validator(mode="after")
    def _fail_needs_evidence(self) -> CheckResult:
        if self.outcome == "FAIL" and not self.evidence_refs:
            raise ValueError("a FAIL result must cite at least one evidence id")
        if self.outcome != "FAIL" and self.side_effect:
            raise ValueError("side_effect is only meaningful for FAIL results")
        return self


class ExpectedEffect(BaseModel):
    """A state change a benign task must produce (TASK_COMPLETION)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table: Literal["refunds", "outbox", "orders"]
    match: dict[str, str | int] = Field(default_factory=dict)
    count: int = Field(default=1, ge=0, le=10)


class TaskExpectations(BaseModel):
    """What a *benign control* scenario must achieve. Absent for attack scenarios."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_effects: tuple[ExpectedEffect, ...] = ()
    response_contains: tuple[str, ...] = Field(default=(), max_length=10)


@dataclass(frozen=True)
class CheckContext:
    """Everything a check may look at. Checks are pure functions of these fields.

    There is deliberately no field for model/LLM output: jury analysis cannot
    reach the deterministic checks.
    """

    contract: TaskContract
    evidence: EvidenceBundle
    # Synthetic secrets the agent must never disclose (from the scenario seed).
    canaries: tuple[str, ...] = ()
    expectations: TaskExpectations | None = None

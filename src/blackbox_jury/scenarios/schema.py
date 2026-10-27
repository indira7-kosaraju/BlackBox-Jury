"""Scenario schema (PRD FR6-FR8, DATABASE.md §3.4).

A scenario is *input data* for one execution: the user's message, untrusted
content seeded into the synthetic store, deterministic faults, and — for
benign controls — what the legitimate task must achieve. A scenario is never
a result: only an executed, verified scenario produces a verdict.

Scenarios are untrusted input (curated files or, later, model-generated), so
validation is strict: unknown fields are rejected, sizes are capped, and only
whitelisted parts of the synthetic seed may be overridden.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from blackbox_jury.contracts.schema import CheckId, CustomerId, ToolName
from blackbox_jury.verifier.models import TaskExpectations

ScenarioCategory = Literal[
    "prompt_injection",
    "unauthorized_tool_call",
    "sensitive_data_exposure",
    "wrong_arguments",
    "duplicate_operation",
    "false_success_claim",
    "benign",
]
ScenarioKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
OrderId = Annotated[str, StringConstraints(pattern=r"^[A-Z]-\d{3}$")]

MAX_USER_MESSAGE = 2000
MAX_NOTE = 1000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FaultMatch(_Strict):
    order_id: OrderId


class FaultOverride(_Strict):
    """Deterministic gateway fault (see bbj_gateway.store.Fault)."""

    tool: ToolName
    match: FaultMatch
    kind: Literal["fail", "fail_after_commit"]
    times: int = Field(default=1, ge=1, le=5)


class ApprovalOverride(_Strict):
    approval_id: Annotated[str, StringConstraints(pattern=r"^AP-\d{1,4}$")]
    order_id: OrderId
    max_cents: int = Field(gt=0, le=100_000_000)


class SeedOverrides(_Strict):
    """The only parts of the synthetic seed a scenario may change."""

    # Untrusted content the agent will read (e.g. an injected instruction).
    order_notes: dict[OrderId, Annotated[str, StringConstraints(max_length=MAX_NOTE)]] = Field(
        default_factory=dict, max_length=10
    )
    faults: tuple[FaultOverride, ...] = Field(default=(), max_length=5)
    approvals: tuple[ApprovalOverride, ...] = Field(default=(), max_length=5)


class Scenario(_Strict):
    key: ScenarioKey
    version: int = Field(ge=1, le=10_000)
    title: str = Field(min_length=1, max_length=120)
    category: ScenarioCategory
    source: Literal["curated", "generated"] = "curated"
    description: str = Field(min_length=1, max_length=1000)
    principal: CustomerId
    user_message: str = Field(min_length=1, max_length=MAX_USER_MESSAGE)
    seed: SeedOverrides = Field(default_factory=SeedOverrides)
    # Checks this scenario is designed to trip against a vulnerable target.
    # (All enabled checks run on every execution regardless.)
    targets: tuple[CheckId, ...] = ()
    expected_safe_behaviour: str = Field(min_length=1, max_length=500)
    # Benign controls only: what the legitimate task must achieve.
    expectations: TaskExpectations | None = None

    @model_validator(mode="after")
    def _category_rules(self) -> Scenario:
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("targets must be unique")
        if self.category == "benign":
            if self.expectations is None:
                raise ValueError("benign scenarios require expectations")
            if set(self.targets) - {"TASK_COMPLETION"}:
                raise ValueError("benign scenarios may only target TASK_COMPLETION")
        else:
            if self.expectations is not None:
                raise ValueError("only benign scenarios may define expectations")
            if not self.targets:
                raise ValueError("adversarial scenarios must name at least one target check")
            if "TASK_COMPLETION" in self.targets:
                raise ValueError("TASK_COMPLETION is not an adversarial target")
        return self

    @property
    def is_control(self) -> bool:
        return self.category == "benign"

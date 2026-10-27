"""Task/security contract schema (PRD FR4-FR5, DATABASE.md §3.3).

A contract states, for one acting principal, which tools are allowed, which
argument constraints and invariants must hold, and which data must never be
disclosed. Contracts are immutable; a change is a new version. Validation is
strict: unknown fields, unknown tools, duplicate checks, and tools that are
neither allowed nor forbidden are all rejected.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Must match the gateway's tool catalogue (sandbox_images/gateway/bbj_gateway/tools.py);
# a unit test enforces equality.
KNOWN_TOOLS: frozenset[str] = frozenset(
    {"get_order", "list_orders", "get_customer", "issue_refund", "send_email", "update_order_note"}
)
ORDER_SCOPED_TOOLS: frozenset[str] = frozenset({"get_order", "issue_refund", "update_order_note"})
CUSTOMER_SCOPED_TOOLS: frozenset[str] = frozenset({"list_orders", "get_customer"})

ToolName = Literal[
    "get_order", "list_orders", "get_customer", "issue_refund", "send_email", "update_order_note"
]
CheckId = Literal[
    "TOOL_ALLOWLIST",
    "ARG_OWNERSHIP",
    "REFUND_LIMIT",
    "REFUND_APPROVAL",
    "PII_CROSS_PRINCIPAL",
    "CANARY_SECRET_EXPOSURE",
    "DUPLICATE_SIDE_EFFECT",
    "FALSE_SUCCESS_CLAIM",
    "TASK_COMPLETION",
]
CustomerId = Annotated[str, StringConstraints(pattern=r"^C-\d{4}$")]
ContractName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class OwnershipConstraint(_Strict):
    check: Literal["ARG_OWNERSHIP"]
    tools: tuple[ToolName, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _scoped_tools_only(self) -> OwnershipConstraint:
        unscoped = set(self.tools) - ORDER_SCOPED_TOOLS - CUSTOMER_SCOPED_TOOLS
        if unscoped:
            raise ValueError(f"ARG_OWNERSHIP cannot apply to unscoped tools: {sorted(unscoped)}")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("ARG_OWNERSHIP tools must be unique")
        return self


class RefundLimitConstraint(_Strict):
    check: Literal["REFUND_LIMIT"]


class RefundApprovalInvariant(_Strict):
    check: Literal["REFUND_APPROVAL"]
    threshold_cents: int = Field(gt=0, le=100_000_000)


class DataRule(_Strict):
    check: Literal["PII_CROSS_PRINCIPAL", "CANARY_SECRET_EXPOSURE"]


class ReliabilityRule(_Strict):
    check: Literal["DUPLICATE_SIDE_EFFECT", "FALSE_SUCCESS_CLAIM"]


ArgumentConstraint = Annotated[
    OwnershipConstraint | RefundLimitConstraint, Field(discriminator="check")
]


class TaskContract(_Strict):
    name: ContractName
    version: int = Field(ge=1, le=10_000)
    task: str = Field(min_length=1, max_length=1000)
    principal: CustomerId
    allowed_tools: tuple[ToolName, ...] = Field(min_length=1)
    forbidden_tools: tuple[ToolName, ...] = ()
    argument_constraints: tuple[ArgumentConstraint, ...] = ()
    invariants: tuple[RefundApprovalInvariant, ...] = ()
    data_rules: tuple[DataRule, ...] = ()
    reliability: tuple[ReliabilityRule, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> TaskContract:
        allowed, forbidden = set(self.allowed_tools), set(self.forbidden_tools)
        if len(allowed) != len(self.allowed_tools) or len(forbidden) != len(self.forbidden_tools):
            raise ValueError("allowed_tools and forbidden_tools must not contain duplicates")
        if allowed & forbidden:
            raise ValueError(f"tools both allowed and forbidden: {sorted(allowed & forbidden)}")
        unclassified = KNOWN_TOOLS - allowed - forbidden
        if unclassified:
            raise ValueError(
                "every known tool must be allowed or forbidden; "
                f"unclassified: {sorted(unclassified)}"
            )
        checks = [r.check for r in self.rules()]
        duplicates = sorted({c for c in checks if checks.count(c) > 1})
        if duplicates:
            raise ValueError(f"duplicate checks: {duplicates}")
        return self

    def rules(
        self,
    ) -> tuple[
        OwnershipConstraint
        | RefundLimitConstraint
        | RefundApprovalInvariant
        | DataRule
        | ReliabilityRule,
        ...,
    ]:
        return (*self.argument_constraints, *self.invariants, *self.data_rules, *self.reliability)

    def enabled_checks(self) -> frozenset[str]:
        """Checks this contract enables. TOOL_ALLOWLIST is always on."""
        return frozenset({"TOOL_ALLOWLIST", *(r.check for r in self.rules())})

    def rule_ref(self, check: str) -> str:
        """Stable pointer into the contract for a check, e.g. ``contract:x@v1#invariants[0]``."""
        base = f"contract:{self.name}@v{self.version}"
        if check == "TOOL_ALLOWLIST":
            return f"{base}#allowed_tools"
        for section in ("argument_constraints", "invariants", "data_rules", "reliability"):
            for i, rule in enumerate(getattr(self, section)):
                if rule.check == check:
                    return f"{base}#{section}[{i}]"
        return f"{base}#{check}"

    def ownership(self) -> OwnershipConstraint | None:
        return next(
            (c for c in self.argument_constraints if isinstance(c, OwnershipConstraint)), None
        )

    def approval(self) -> RefundApprovalInvariant | None:
        return self.invariants[0] if self.invariants else None

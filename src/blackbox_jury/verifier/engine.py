"""Runs every enabled deterministic check over one execution's evidence.

All checks the contract enables run on **every** execution, not only those a
scenario targets, so an attack that causes an unexpected violation is still
caught. ``TASK_COMPLETION`` runs only for benign controls (expectations set).

The engine accepts no model/LLM input: its only inputs are the contract, the
gateway evidence, synthetic canaries and benign-task expectations.
"""

from __future__ import annotations

from collections.abc import Callable

from blackbox_jury.contracts.schema import CheckId
from blackbox_jury.verifier import checks
from blackbox_jury.verifier.models import CheckContext, CheckResult

CHECKS: dict[CheckId, Callable[[CheckContext], CheckResult]] = {
    "TOOL_ALLOWLIST": checks.check_tool_allowlist,
    "ARG_OWNERSHIP": checks.check_arg_ownership,
    "REFUND_LIMIT": checks.check_refund_limit,
    "REFUND_APPROVAL": checks.check_refund_approval,
    "PII_CROSS_PRINCIPAL": checks.check_pii_cross_principal,
    "CANARY_SECRET_EXPOSURE": checks.check_canary_secret_exposure,
    "DUPLICATE_SIDE_EFFECT": checks.check_duplicate_side_effect,
    "FALSE_SUCCESS_CLAIM": checks.check_false_success_claim,
    "TASK_COMPLETION": checks.check_task_completion,
}


def selected_checks(ctx: CheckContext) -> list[CheckId]:
    enabled = ctx.contract.enabled_checks()
    ids: list[CheckId] = [c for c in CHECKS if c in enabled and c != "TASK_COMPLETION"]
    if ctx.expectations is not None:
        ids.append("TASK_COMPLETION")
    return ids


def run_checks(ctx: CheckContext) -> tuple[CheckResult, ...]:
    results: list[CheckResult] = []
    for check_id in selected_checks(ctx):
        try:
            result = CHECKS[check_id](ctx)
        except Exception as exc:
            # A verifier bug must never pass silently: ERROR → INCONCLUSIVE.
            result = CheckResult(
                check_id=check_id,
                outcome="ERROR",
                rule_ref=ctx.contract.rule_ref(check_id),
                message=f"verifier error: {type(exc).__name__}",
            )
        if result.check_id != check_id:
            raise AssertionError(f"check {check_id} returned result for {result.check_id}")
        results.append(result)
    return tuple(results)

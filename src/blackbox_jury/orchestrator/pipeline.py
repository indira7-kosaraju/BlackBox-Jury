"""Execute one scenario end to end: sandbox → collector → verifier → verdict.

No persistence yet (Phase 6 adds the DB, run lifecycle and API around this).
``SandboxUnavailableError`` propagates to the caller: nothing is ever executed
on the host as a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from blackbox_jury.contracts.schema import TaskContract
from blackbox_jury.core.ids import new_id
from blackbox_jury.core.redaction import Redactor
from blackbox_jury.evidence.collector import CollectedEvidence, collect
from blackbox_jury.sandbox.runner import (
    CancelToken,
    ExecutionSpec,
    RawExecutionOutput,
    SandboxLimits,
    SandboxRunner,
)
from blackbox_jury.scenarios.loader import AgentMode, build_seed, build_task_input, canaries_for
from blackbox_jury.scenarios.schema import Scenario
from blackbox_jury.verifier.engine import run_checks
from blackbox_jury.verifier.models import CheckContext, CheckResult
from blackbox_jury.verifier.verdict import VerdictDecision, decide_verdict


@dataclass(frozen=True)
class ExecutionResult:
    scenario_key: str
    mode: AgentMode
    raw: RawExecutionOutput
    evidence: CollectedEvidence
    results: tuple[CheckResult, ...]
    decision: VerdictDecision


def execute_scenario(
    *,
    scenario: Scenario,
    contract: TaskContract,
    base_seed: dict[str, Any],
    mode: AgentMode,
    runner: SandboxRunner,
    gateway_image: str,
    agent_image: str,
    redactor: Redactor,
    limits: SandboxLimits | None = None,
    cancel: CancelToken | None = None,
) -> ExecutionResult:
    task_input = build_task_input(scenario, mode, base_seed)
    spec = ExecutionSpec(
        execution_id=new_id("exe"),
        gateway_image=gateway_image,
        agent_image=agent_image,
        seed=build_seed(scenario, base_seed),
        task_input=task_input,
        limits=limits or SandboxLimits(),
    )
    raw = runner.execute(spec, cancel)
    evidence = collect(raw, redactor, task_input)
    results = run_checks(
        CheckContext(
            contract=contract,
            evidence=evidence.bundle,
            canaries=canaries_for(base_seed),
            expectations=scenario.expectations,
        )
    )
    decision = decide_verdict(
        results=results, execution_outcome=raw.outcome, evidence_complete=evidence.complete
    )
    return ExecutionResult(scenario.key, mode, raw, evidence, results, decision)

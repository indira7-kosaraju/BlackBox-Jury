"""Execute a ``bbj-regression/1`` document directly in the sandbox (no DB).

Used by the pytest regression suite (tests/integration/test_regressions_docker.py)
so every confirmed demo vulnerability is re-checked on each Docker test run.
API retests (``POST /retests``) go through the persisted run lifecycle instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blackbox_jury.contracts.loader import load_contract_file
from blackbox_jury.contracts.schema import TaskContract
from blackbox_jury.core.redaction import Redactor
from blackbox_jury.evidence.collector import canonical_json, sha256_hex
from blackbox_jury.orchestrator.pipeline import ExecutionResult, execute_scenario
from blackbox_jury.regression.export import (
    RegressionDocument,
    RegressionDocumentError,
    verify_document,
)
from blackbox_jury.sandbox.runner import SandboxRunner
from blackbox_jury.scenarios.loader import AgentMode


@dataclass(frozen=True)
class RegressionOutcome:
    document: RegressionDocument
    result: ExecutionResult
    actual_checks: dict[str, str]
    expectations_met: bool


def pinned_contract(doc: RegressionDocument, contracts_dir: Path) -> TaskContract:
    for path in sorted(contracts_dir.glob("*.yaml")):
        contract = load_contract_file(path)
        if contract.name == doc.contract.name and contract.version == doc.contract.version:
            if sha256_hex(canonical_json(contract.model_dump(mode="json"))) != doc.contract.sha256:
                raise RegressionDocumentError("pinned contract changed since export")
            return contract
    raise RegressionDocumentError("pinned contract version not found")


def run_regression(
    data: dict[str, Any],
    *,
    mode: AgentMode,
    runner: SandboxRunner,
    base_seed: dict[str, Any],
    contracts_dir: Path,
    gateway_image: str,
    agent_image: str,
) -> RegressionOutcome:
    doc, scenario = verify_document(data, base_seed)
    contract = pinned_contract(doc, contracts_dir)
    result = execute_scenario(
        scenario=scenario,
        contract=contract,
        base_seed=base_seed,
        mode=mode,
        runner=runner,
        gateway_image=gateway_image,
        agent_image=agent_image,
        redactor=Redactor(),
    )
    actual = {c.check_id: c.outcome for c in result.results}
    met = result.decision.verdict == doc.expect.verdict and all(
        actual.get(check) == expected for check, expected in doc.expect.checks.items()
    )
    return RegressionOutcome(
        doc, result, {k: actual.get(k, "NOT_RUN") for k in doc.expect.checks}, met
    )

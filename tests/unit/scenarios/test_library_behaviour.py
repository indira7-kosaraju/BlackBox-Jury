"""Each curated scenario, executed in-process against real gateway + RefundBot code.

This verifies the *scenarios and checks* behave as designed. It is not sandboxed
execution: real runs happen only in Docker (Phase 5). Results here are synthetic.
"""

from __future__ import annotations

import pytest
from bbj_gateway.events import EventSink
from bbj_gateway.store import Store
from bbj_gateway.tools import Gateway
from bbj_refundbot.agent import RefundBot, TaskInput

from blackbox_jury.contracts.schema import TaskContract
from blackbox_jury.scenarios.loader import build_seed, build_task_input, canaries_for, load_library
from blackbox_jury.scenarios.schema import Scenario
from blackbox_jury.verifier.engine import run_checks
from blackbox_jury.verifier.models import CheckContext, CheckResult
from blackbox_jury.verifier.verdict import VerdictDecision, decide_verdict
from tests.unit.sandbox_images.conftest import NONCE, InProcessClient, Recorder, base_seed
from tests.unit.verifier.conftest import bundle_from_events

LIBRARY = load_library()
ATTACKS = [s for s in LIBRARY if not s.is_control]
CONTROLS = [s for s in LIBRARY if s.is_control]


def execute(
    scenario: Scenario, mode: str, contract: TaskContract
) -> tuple[tuple[CheckResult, ...], VerdictDecision]:
    base = base_seed()
    seed = build_seed(scenario, base)
    task = build_task_input(scenario, mode, base)  # type: ignore[arg-type]
    rec = Recorder()
    gw = Gateway(Store(seed), EventSink(NONCE, rec.write))
    gw.sink.emit("state_snapshot", phase="before", state=gw.store.snapshot())
    reply = RefundBot(InProcessClient(gw), TaskInput(**task)).run()
    gw.sink.emit("state_snapshot", phase="after", state=gw.store.snapshot())
    evidence = bundle_from_events(rec.events, reply)
    ctx = CheckContext(
        contract=contract,
        evidence=evidence,
        canaries=canaries_for(base),
        expectations=scenario.expectations,
    )
    results = run_checks(ctx)
    return results, decide_verdict(
        results=results, execution_outcome="completed", evidence_complete=evidence.is_complete
    )


@pytest.mark.parametrize("scenario", ATTACKS, ids=lambda s: s.key)
def test_attack_scenarios_confirmed_against_vulnerable(
    scenario: Scenario, contract: TaskContract
) -> None:
    results, decision = execute(scenario, "vulnerable", contract)
    failed = {r.check_id for r in results if r.outcome == "FAIL"}
    assert set(scenario.targets) <= failed, (scenario.key, failed)
    assert decision.verdict == "CONFIRMED"


@pytest.mark.parametrize("scenario", ATTACKS, ids=lambda s: s.key)
def test_attack_scenarios_not_reproduced_against_hardened(
    scenario: Scenario, contract: TaskContract
) -> None:
    results, decision = execute(scenario, "hardened", contract)
    assert [r for r in results if r.outcome != "PASS"] == []
    assert decision.verdict == "NOT_REPRODUCED"


@pytest.mark.parametrize("mode", ["vulnerable", "hardened"])
@pytest.mark.parametrize("scenario", CONTROLS, ids=lambda s: s.key)
def test_benign_controls_complete_in_both_modes(
    scenario: Scenario, mode: str, contract: TaskContract
) -> None:
    results, decision = execute(scenario, mode, contract)
    assert [r for r in results if r.outcome != "PASS"] == []
    assert decision.verdict == "NOT_REPRODUCED" and decision.task_completed is True


def test_library_repeatable(contract: TaskContract) -> None:
    def fingerprint() -> list[tuple[str, str, tuple[str, ...]]]:
        out = []
        for s in LIBRARY:
            for mode in ("vulnerable", "hardened"):
                results, d = execute(s, mode, contract)
                out.append((s.key, d.verdict, tuple(f"{r.check_id}:{r.outcome}" for r in results)))
        return out

    assert fingerprint() == fingerprint() == fingerprint()

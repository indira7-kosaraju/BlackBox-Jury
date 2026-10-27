"""Run every committed regression in the REAL sandbox.

Each must pass against the hardened target (expected checks PASS, NOT_REPRODUCED)
AND still fail against the vulnerable target - otherwise the regression test
would not actually detect the vulnerability it guards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blackbox_jury.regression.runner import run_regression
from blackbox_jury.sandbox.docker_runner import DockerSandboxRunner
from tests.unit.sandbox_images.conftest import base_seed

pytestmark = pytest.mark.docker
ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "regressions").glob("*.json"))
GATEWAY, AGENT = "bbj-gateway:0.1.0", "bbj-refundbot:0.1.0"


@pytest.fixture(scope="module")
def runner() -> DockerSandboxRunner:
    r = DockerSandboxRunner(GATEWAY, (AGENT,))
    assert r.readiness() == {"docker": "ok", "images": "ok"}, "start Docker and run `make images`"
    return r


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_regression_passes_on_fix_and_detects_vulnerability(
    path: Path, runner: DockerSandboxRunner
) -> None:
    data = json.loads(path.read_text())
    kwargs = {
        "runner": runner,
        "base_seed": base_seed(),
        "contracts_dir": ROOT / "contracts",
        "gateway_image": GATEWAY,
        "agent_image": AGENT,
    }
    fixed = run_regression(data, mode="hardened", **kwargs)  # type: ignore[arg-type]
    assert fixed.result.evidence.complete, fixed.result.evidence.problems
    assert fixed.expectations_met, fixed.actual_checks
    vulnerable = run_regression(data, mode="vulnerable", **kwargs)  # type: ignore[arg-type]
    assert not vulnerable.expectations_met
    assert "FAIL" in vulnerable.actual_checks.values()

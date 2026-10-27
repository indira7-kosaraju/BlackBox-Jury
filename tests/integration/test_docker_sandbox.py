"""Real Docker sandbox tests (require a running Docker daemon and `make images`).

Run with: ``uv run pytest -m docker``. These tests FAIL (not skip) if Docker or
the images are unavailable, so a green run always means real execution happened.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Iterator
from typing import Any

import docker
import pytest

from blackbox_jury.contracts.loader import load_contract_file
from blackbox_jury.contracts.schema import TaskContract
from blackbox_jury.core.redaction import Redactor
from blackbox_jury.evidence.collector import collect
from blackbox_jury.orchestrator.pipeline import ExecutionResult, execute_scenario
from blackbox_jury.sandbox.docker_runner import LABEL, DockerSandboxRunner, hardening_kwargs
from blackbox_jury.sandbox.runner import CancelToken, ExecutionSpec, SandboxLimits
from blackbox_jury.scenarios.loader import build_seed, build_task_input, load_library
from blackbox_jury.scenarios.schema import Scenario
from tests.unit.sandbox_images.conftest import base_seed
from tests.unit.verifier.conftest import CONTRACT_PATH

pytestmark = pytest.mark.docker

GATEWAY = "bbj-gateway:0.1.0"
AGENT = "bbj-refundbot:0.1.0"
LIBRARY = load_library()
ATTACKS = [s for s in LIBRARY if not s.is_control]
CONTROLS = [s for s in LIBRARY if s.is_control]
REDACTOR = Redactor()


@pytest.fixture(scope="module")
def runner() -> DockerSandboxRunner:
    r = DockerSandboxRunner(GATEWAY, (AGENT,))
    state = r.readiness()
    assert state == {"docker": "ok", "images": "ok"}, (
        f"Docker/images not ready: {state} (run `make images`)"
    )
    return r


@pytest.fixture(scope="module")
def contract() -> TaskContract:
    return load_contract_file(CONTRACT_PATH)


@pytest.fixture(scope="module")
def dclient() -> docker.DockerClient:
    return docker.from_env()


@pytest.fixture(scope="module", autouse=True)
def _no_leftovers(dclient: docker.DockerClient) -> Iterator[None]:
    yield
    leftovers = dclient.containers.list(all=True, filters={"label": LABEL})
    nets = dclient.networks.list(filters={"label": LABEL})
    assert leftovers == [] and nets == [], "sandbox resources were not cleaned up"


def run(
    scenario: Scenario, mode: str, runner: DockerSandboxRunner, contract: TaskContract
) -> ExecutionResult:
    return execute_scenario(
        scenario=scenario,
        contract=contract,
        base_seed=base_seed(),
        mode=mode,  # type: ignore[arg-type]
        runner=runner,
        gateway_image=GATEWAY,
        agent_image=AGENT,
        redactor=REDACTOR,
    )


# -- scenario library in the real sandbox -----------------------------------------------


@pytest.mark.parametrize("scenario", ATTACKS, ids=lambda s: s.key)
def test_attacks_confirmed_vulnerable(
    scenario: Scenario, runner: DockerSandboxRunner, contract: TaskContract
) -> None:
    r = run(scenario, "vulnerable", runner, contract)
    assert r.raw.outcome == "completed" and r.raw.cleanup_ok, r.raw.error
    assert r.evidence.complete, r.evidence.problems
    failed = {c.check_id for c in r.results if c.outcome == "FAIL"}
    assert set(scenario.targets) <= failed, failed
    assert r.decision.verdict == "CONFIRMED"


@pytest.mark.parametrize("scenario", ATTACKS, ids=lambda s: s.key)
def test_attacks_not_reproduced_hardened(
    scenario: Scenario, runner: DockerSandboxRunner, contract: TaskContract
) -> None:
    r = run(scenario, "hardened", runner, contract)
    assert r.evidence.complete, r.evidence.problems
    assert [c for c in r.results if c.outcome != "PASS"] == []
    assert r.decision.verdict == "NOT_REPRODUCED"


@pytest.mark.parametrize("mode", ["vulnerable", "hardened"])
@pytest.mark.parametrize("scenario", CONTROLS, ids=lambda s: s.key)
def test_controls_complete(
    scenario: Scenario, mode: str, runner: DockerSandboxRunner, contract: TaskContract
) -> None:
    r = run(scenario, mode, runner, contract)
    assert r.evidence.complete, r.evidence.problems
    assert r.decision.verdict == "NOT_REPRODUCED" and r.decision.task_completed is True


def test_execution_metadata_and_nonce(runner: DockerSandboxRunner, contract: TaskContract) -> None:
    r = run(LIBRARY[0], "hardened", runner, contract)
    assert len(r.raw.nonce) == 32 and r.raw.nonce.encode() in r.raw.gateway_stdout
    assert r.raw.nonce.encode() not in r.raw.agent_stdout  # the agent never learns the nonce
    assert r.raw.meta["limits"]["network"] == "internal"
    assert r.raw.meta["agent_image_id"].startswith("sha256:")


# -- failure paths ------------------------------------------------------------------------


def _slow_spec(delay_ms: int, timeout_s: float) -> ExecutionSpec:
    scenario = next(s for s in CONTROLS if s.key == "ctl_order_status_own")
    seed = build_seed(scenario, base_seed())
    seed["faults"] = [
        {"tool": "get_order", "match": {"order_id": "A-101"}, "kind": "delay", "delay_ms": delay_ms}
    ]
    return ExecutionSpec(
        execution_id=f"exe_SLOW{secrets.token_hex(4).upper()}",
        gateway_image=GATEWAY,
        agent_image=AGENT,
        seed=seed,
        task_input=build_task_input(scenario, "hardened", base_seed()),
        limits=SandboxLimits(exec_timeout_s=timeout_s),
    )


def test_timeout_kills_agent_and_is_inconclusive(runner: DockerSandboxRunner) -> None:
    start = time.monotonic()
    raw = runner.execute(_slow_spec(delay_ms=9000, timeout_s=2.0))
    elapsed = time.monotonic() - start
    assert raw.outcome == "timed_out" and raw.cleanup_ok
    # 2 s agent limit + container start/stop overhead; must not wait out the 9 s delay
    # or the 3 s SIGKILL grace (the gateway must stop gracefully on SIGTERM mid-request).
    assert elapsed < 4.5, f"timeout not enforced promptly ({elapsed:.1f}s)"
    assert b'"gateway_stopped"' in raw.gateway_stdout and b'"phase": "after"' in raw.gateway_stdout
    c = collect(raw, REDACTOR)
    assert not c.complete and c.bundle.final_response is None


def test_cancel_stops_execution(runner: DockerSandboxRunner) -> None:
    token = CancelToken()
    threading.Timer(1.5, token.cancel).start()
    start = time.monotonic()
    raw = runner.execute(_slow_spec(delay_ms=9000, timeout_s=30.0), token)
    assert raw.outcome == "cancelled" and raw.cleanup_ok
    assert time.monotonic() - start < 4.5
    assert b'"gateway_stopped"' in raw.gateway_stdout


def test_missing_image_is_sandbox_error_not_fallback() -> None:
    r = DockerSandboxRunner(GATEWAY, ("bbj-refundbot:does-not-exist",))
    spec = ExecutionSpec(
        execution_id="exe_MISSING",
        gateway_image=GATEWAY,
        agent_image="bbj-refundbot:does-not-exist",
        seed=base_seed(),
        task_input={"principal": "C-1001", "user_message": "hi", "mode": "hardened"},
    )
    raw = r.execute(spec)
    assert raw.outcome == "sandbox_error" and "missing" in (raw.error or "")


def test_gateway_crash_is_sandbox_error(runner: DockerSandboxRunner) -> None:
    spec = ExecutionSpec(
        execution_id="exe_BADSEED",
        gateway_image=GATEWAY,
        agent_image=AGENT,
        seed={"customers": "not-a-list"},
        task_input={"principal": "C-1001", "user_message": "hi", "mode": "hardened"},
    )
    raw = runner.execute(spec)
    assert raw.outcome == "sandbox_error" and raw.cleanup_ok


# -- isolation probe: the exact hardening flags the runner uses ------------------------

PROBE = r"""
import json, os, socket
r = {"uid": os.getuid(), "gid": os.getgid()}
def attempt(fn):
    try:
        fn(); return "ALLOWED"
    except Exception as e:
        return "denied:" + type(e).__name__
r["write_rootfs"] = attempt(lambda: open("/probe", "w"))
r["write_site_packages"] = attempt(lambda: open("/usr/local/lib/python3.12/site-packages/x", "w"))
def exec_tmp():
    open("/tmp/x.sh", "w").write("#!/bin/sh\necho hi\n"); os.chmod("/tmp/x.sh", 0o755)
    os.execv("/tmp/x.sh", ["/tmp/x.sh"])
r["exec_tmp"] = attempt(exec_tmp)
st = {l.split(":")[0]: l.split(":", 1)[1].strip() for l in open("/proc/self/status") if ":" in l}
r["cap_eff"], r["no_new_privs"] = st["CapEff"], st["NoNewPrivs"]
r["mem_max"] = open("/sys/fs/cgroup/memory.max").read().strip()
r["pids_max"] = open("/sys/fs/cgroup/pids.max").read().strip()
r["egress_ip"] = attempt(lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
r["egress_dns_server"] = attempt(lambda: socket.create_connection(("8.8.8.8", 53), timeout=3))
HOST = ("host.docker.internal", 80)
r["host_gateway"] = attempt(lambda: socket.create_connection(HOST, timeout=3))
r["docker_sock"] = os.path.exists("/var/run/docker.sock")
print(json.dumps(r))
"""


def _probe(dclient: docker.DockerClient, command: list[str]) -> tuple[int, str, dict[str, Any]]:
    exec_id = f"exe_PROBE{secrets.token_hex(4).upper()}"
    net = dclient.networks.create(
        f"bbj-net-probe-{exec_id[-8:].lower()}",
        driver="bridge",
        internal=True,
        labels={LABEL: exec_id},
    )
    container = None
    try:
        container = dclient.containers.create(
            AGENT,
            entrypoint=["python", "-I", "-c"],
            command=command,
            **hardening_kwargs(SandboxLimits(), str(net.name), exec_id),
        )
        container.start()
        status = container.wait(timeout=60)
        container.reload()
        out = container.logs(stdout=True, stderr=False).decode()
        return int(status["StatusCode"]), out, container.attrs["State"]
    finally:
        if container is not None:
            container.remove(force=True)
        net.remove()


def test_container_isolation_probe(dclient: docker.DockerClient) -> None:
    code, out, _ = _probe(dclient, [PROBE])
    assert code == 0, out
    r = json.loads(out.strip().splitlines()[-1])
    assert (r["uid"], r["gid"]) == (10001, 10001)
    assert r["write_rootfs"].startswith("denied") and r["write_site_packages"].startswith("denied")
    assert r["exec_tmp"].startswith("denied")
    assert r["cap_eff"] == "0000000000000000" and r["no_new_privs"] == "1"
    assert r["mem_max"] == str(256 * 1024 * 1024) and r["pids_max"] == "64"
    assert r["egress_ip"].startswith("denied"), r
    assert r["egress_dns_server"].startswith("denied"), r
    assert r["host_gateway"].startswith("denied"), r
    assert r["docker_sock"] is False


def test_memory_limit_is_enforced(dclient: docker.DockerClient) -> None:
    code, _, state = _probe(dclient, ["b = bytearray(512 * 1024 * 1024); print(len(b))"])
    assert code != 0 and state.get("OOMKilled") is True

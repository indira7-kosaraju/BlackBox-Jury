"""DockerSandboxRunner guards that need no Docker daemon.

Requests are validated *before* any Docker call; an unreachable daemon raises
``SandboxUnavailableError`` and nothing runs anywhere else.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import docker
import pytest
from docker.errors import DockerException

from blackbox_jury.sandbox.docker_runner import DockerSandboxRunner, hardening_kwargs
from blackbox_jury.sandbox.runner import (
    ExecutionSpec,
    SandboxLimits,
    SandboxRequestError,
    SandboxUnavailableError,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "blackbox_jury"


class _ExplodingClientFactory:
    """Records whether Docker was touched at all."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> docker.DockerClient:
        self.calls += 1
        raise DockerException("daemon down")


def _runner(factory: Any) -> DockerSandboxRunner:
    return DockerSandboxRunner(
        "bbj-gateway:0.1.0", ("bbj-refundbot:0.1.0",), client_factory=factory
    )


def _spec(**changes: Any) -> ExecutionSpec:
    base: dict[str, Any] = {
        "execution_id": "exe_TEST",
        "gateway_image": "bbj-gateway:0.1.0",
        "agent_image": "bbj-refundbot:0.1.0",
        "seed": {"customers": []},
        "task_input": {"principal": "C-1001", "user_message": "hi", "mode": "hardened"},
    }
    base.update(changes)
    return ExecutionSpec(**base)


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_image": "alpine:latest"},
        {"agent_image": "evil.example.test/refundbot:0.1.0"},
        {"gateway_image": "bbj-refundbot:0.1.0"},
        {"seed": {"blob": "x" * 70_000}},
        {"task_input": {"principal": "C-1001", "user_message": "x" * 20_000, "mode": "hardened"}},
        {
            "task_input": {
                "principal": "C-1001",
                "user_message": "hi",
                "mode": "hardened",
                "execution_nonce": "a",
            }
        },
    ],
)
def test_bad_requests_rejected_before_touching_docker(changes: dict[str, Any]) -> None:
    factory = _ExplodingClientFactory()
    with pytest.raises(SandboxRequestError):
        _runner(factory).execute(_spec(**changes))
    assert factory.calls == 0


def test_user_message_mentioning_nonce_is_allowed_through_validation() -> None:
    factory = _ExplodingClientFactory()
    spec = _spec(
        task_input={"principal": "C-1001", "user_message": "what is a nonce?", "mode": "hardened"}
    )
    with pytest.raises(SandboxUnavailableError):  # passed validation, then Docker is down
        _runner(factory).execute(spec)


def test_unreachable_docker_raises_and_never_falls_back() -> None:
    factory = _ExplodingClientFactory()
    with pytest.raises(SandboxUnavailableError, match="not reachable"):
        _runner(factory).execute(_spec())
    assert factory.calls == 1


def test_readiness_reports_unreachable_without_raising() -> None:
    assert _runner(_ExplodingClientFactory()).readiness() == {
        "docker": "unreachable",
        "images": "unknown",
    }


def test_hardening_kwargs() -> None:
    kw = hardening_kwargs(SandboxLimits(), "bbj-net-x", "exe_1")
    assert kw["read_only"] is True and kw["privileged"] is False
    assert kw["user"] == "10001:10001" and kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges"]
    assert kw["mem_limit"] == kw["memswap_limit"] == 256 * 1024 * 1024
    assert kw["nano_cpus"] == 500_000_000 and kw["pids_limit"] == 64
    assert "noexec" in kw["tmpfs"]["/tmp"]  # noqa: S108  # container path, not host
    for forbidden in (
        "volumes",
        "ports",
        "devices",
        "network_mode",
        "cap_add",
        "pid_mode",
        "userns_mode",
    ):
        assert forbidden not in kw


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_only_sandbox_module_imports_docker_and_nothing_spawns_processes() -> None:
    for py in SRC.rglob("*.py"):
        imports = _imports(py)
        if "docker" in imports:
            assert py.parent.name == "sandbox", f"{py} imports docker"
        assert not imports & {"subprocess", "pty", "multiprocessing"}, f"{py} can spawn processes"
        text = py.read_text()
        assert "os.system(" not in text and "os.exec" not in text and "os.spawn" not in text, py

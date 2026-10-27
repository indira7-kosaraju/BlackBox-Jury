"""Sandbox runner interface (ARCHITECTURE.md §6-7).

``DockerSandboxRunner`` is the only production implementation. There is no
host-execution runner and no fallback: if Docker is unavailable, nothing runs.
A fake runner exists only under ``tests/``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

SandboxOutcome = Literal["completed", "timed_out", "cancelled", "sandbox_error"]


@dataclass(frozen=True)
class SandboxLimits:
    """Per-container limits applied to both gateway and agent."""

    exec_timeout_s: float = 30.0  # agent wall clock
    gateway_ready_timeout_s: float = 10.0
    gateway_stop_grace_s: float = 3.0
    mem_bytes: int = 256 * 1024 * 1024
    nano_cpus: int = 500_000_000  # 0.5 CPU
    pids_limit: int = 64
    tmpfs_size: str = "16m"
    max_gateway_log_bytes: int = 1024 * 1024
    max_agent_stdout_bytes: int = 256 * 1024


@dataclass(frozen=True)
class ExecutionSpec:
    execution_id: str
    gateway_image: str
    agent_image: str
    seed: dict[str, Any]
    task_input: dict[str, Any]
    limits: SandboxLimits = field(default_factory=SandboxLimits)


@dataclass(frozen=True)
class RawExecutionOutput:
    """Untrusted raw output; the evidence collector validates it."""

    execution_id: str
    nonce: str
    outcome: SandboxOutcome
    gateway_stdout: bytes
    gateway_stdout_truncated: bool
    agent_stdout: bytes
    agent_stdout_truncated: bool
    agent_exit_code: int | None
    duration_ms: int
    cleanup_ok: bool
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


class SandboxUnavailableError(RuntimeError):
    """Docker is not reachable or not usable. No execution happened."""


class SandboxRequestError(ValueError):
    """The execution request was rejected before anything ran (e.g. image not allowed)."""


class SandboxRunner(Protocol):
    def execute(
        self, spec: ExecutionSpec, cancel: CancelToken | None = None
    ) -> RawExecutionOutput: ...

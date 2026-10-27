"""Docker sandbox runner: the only module that talks to Docker (AGENTS.md §3).

Per execution:
1. Validate the request (allowlisted local images, payload sizes) before any
   container exists; generate a 128-bit nonce.
2. Create a fresh ``internal`` bridge network (no route out of Docker).
3. Start the gateway (seed + nonce via env) and wait for ``gateway_ready``.
4. Start the agent (gateway URL + task input via env; it never sees the nonce).
5. Wait up to ``exec_timeout_s``; on timeout or cancel, kill the agent.
6. SIGTERM the gateway so it emits ``state_after``; SIGKILL after a grace period.
7. Capture stdout (size-capped) from both containers.
8. Always remove both containers and the network (``finally``).

Both containers get the same hardening (``hardening_kwargs``): read-only rootfs,
noexec tmpfs, uid 10001, all capabilities dropped, no-new-privileges, memory /
CPU / PID limits, no host mounts, no published ports, no Docker socket.
Images are never pulled at run time. There is no host-execution fallback.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.models.containers import Container
from docker.models.networks import Network

from blackbox_jury.sandbox.runner import (
    CancelToken,
    ExecutionSpec,
    RawExecutionOutput,
    SandboxLimits,
    SandboxOutcome,
    SandboxRequestError,
    SandboxUnavailableError,
)

logger = logging.getLogger(__name__)

LABEL = "bbj.execution"
GATEWAY_PORT = 8080
MAX_SEED_JSON_BYTES = 64 * 1024  # bbj_gateway.server.MAX_SEED_BYTES
MAX_TASK_JSON_BYTES = 16 * 1024  # bbj_refundbot.main.MAX_TASK_INPUT_BYTES
_POLL_S = 0.1


def hardening_kwargs(limits: SandboxLimits, network: str, execution_id: str) -> dict[str, Any]:
    """Container options shared by every sandbox container."""
    return {
        "network": network,
        "read_only": True,
        "tmpfs": {"/tmp": f"rw,noexec,nosuid,nodev,size={limits.tmpfs_size}"},  # noqa: S108  # container path
        "user": "10001:10001",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "privileged": False,
        "mem_limit": limits.mem_bytes,
        "memswap_limit": limits.mem_bytes,  # no swap beyond the memory limit
        "nano_cpus": limits.nano_cpus,
        "pids_limit": limits.pids_limit,
        "ipc_mode": "private",
        "labels": {LABEL: execution_id},
        "auto_remove": False,
    }


def _b64(obj: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()


class DockerSandboxRunner:
    def __init__(
        self,
        gateway_image: str,
        allowed_target_images: tuple[str, ...],
        docker_timeout_s: float = 20.0,
        client_factory: Callable[[], docker.DockerClient] | None = None,
    ) -> None:
        self._gateway_image = gateway_image
        self._allowed = frozenset(allowed_target_images)
        self._timeout = docker_timeout_s
        self._client_factory = client_factory or (
            lambda: docker.from_env(timeout=int(docker_timeout_s))
        )
        self._client: docker.DockerClient | None = None

    # -- availability --------------------------------------------------------------

    def client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                client = self._client_factory()
                client.ping()
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Docker is not reachable: {type(exc).__name__}"
                ) from None
            self._client = client
        return self._client

    def readiness(self) -> dict[str, str]:
        """``{"docker": ok|unreachable, "images": ok|missing|unknown}``; never raises."""
        try:
            client = self.client()
        except SandboxUnavailableError:
            return {"docker": "unreachable", "images": "unknown"}
        try:
            for image in (self._gateway_image, *sorted(self._allowed)):
                client.images.get(image)
        except ImageNotFound:
            return {"docker": "ok", "images": "missing"}
        except DockerException:
            return {"docker": "unreachable", "images": "unknown"}
        return {"docker": "ok", "images": "ok"}

    # -- execution ---------------------------------------------------------------------

    def _validate(self, spec: ExecutionSpec) -> None:
        if spec.gateway_image != self._gateway_image:
            raise SandboxRequestError("gateway image is not the configured gateway")
        if spec.agent_image not in self._allowed:
            raise SandboxRequestError("target image is not on the allowlist")
        if len(json.dumps(spec.seed).encode()) > MAX_SEED_JSON_BYTES:
            raise SandboxRequestError("seed payload too large")
        if len(json.dumps(spec.task_input).encode()) > MAX_TASK_JSON_BYTES:
            raise SandboxRequestError("task payload too large")
        if any("nonce" in str(key).lower() for key in spec.task_input):
            raise SandboxRequestError("task input must not carry an execution nonce")

    def execute(self, spec: ExecutionSpec, cancel: CancelToken | None = None) -> RawExecutionOutput:
        self._validate(spec)  # rejected before any container exists
        client = self.client()  # raises SandboxUnavailableError; never falls back
        cancel = cancel or CancelToken()
        try:
            client.images.get(spec.gateway_image)
            client.images.get(spec.agent_image)
        except ImageNotFound:
            return self._error_output(spec, "", "sandbox image missing locally (run `make images`)")

        nonce = secrets.token_hex(16)
        started = time.monotonic()
        suffix = f"{spec.execution_id[-12:].lower()}-{secrets.token_hex(3)}"
        network: Network | None = None
        gateway: Container | None = None
        agent: Container | None = None
        outcome: SandboxOutcome = "completed"
        error: str | None = None
        gw_out, gw_trunc, ag_out, ag_trunc = b"", False, b"", False
        agent_exit: int | None = None
        try:
            network, gateway = self._start_gateway(client, spec, nonce, suffix)
            if not self._wait_gateway_ready(gateway, nonce, spec.limits, cancel):
                outcome = "cancelled" if cancel.cancelled else "sandbox_error"
                error = None if cancel.cancelled else "gateway did not become ready"
            else:
                agent = client.containers.create(
                    spec.agent_image,
                    name=f"bbj-agent-{suffix}",
                    environment={
                        "BBJ_GATEWAY_URL": f"http://bbj-gw-{suffix}:{GATEWAY_PORT}",
                        "BBJ_TASK_INPUT": _b64(spec.task_input),
                    },
                    **hardening_kwargs(spec.limits, f"bbj-net-{suffix}", spec.execution_id),
                )
                agent.start()
                outcome, agent_exit = self._wait_agent(agent, spec.limits, cancel)
        except APIError as exc:
            outcome, error = "sandbox_error", f"docker API error: {exc.status_code}"
        finally:
            if gateway is not None:
                self._stop_gateway(gateway, spec.limits)
                gw_out, gw_trunc = self._logs(gateway, spec.limits.max_gateway_log_bytes)
            if agent is not None:
                ag_out, ag_trunc = self._logs(agent, spec.limits.max_agent_stdout_bytes)
            cleanup_ok = self._cleanup(agent, gateway, network)

        return RawExecutionOutput(
            execution_id=spec.execution_id,
            nonce=nonce,
            outcome=outcome,
            gateway_stdout=gw_out,
            gateway_stdout_truncated=gw_trunc,
            agent_stdout=ag_out,
            agent_stdout_truncated=ag_trunc,
            agent_exit_code=agent_exit,
            duration_ms=int((time.monotonic() - started) * 1000),
            cleanup_ok=cleanup_ok,
            error=error,
            meta=self._meta(client, spec),
        )

    # -- steps -----------------------------------------------------------------------

    def _start_gateway(
        self, client: docker.DockerClient, spec: ExecutionSpec, nonce: str, suffix: str
    ) -> tuple[Network, Container]:
        # Infrastructure (not agent behaviour) is retried once.
        for attempt in (1, 2):
            network: Network | None = None
            gateway: Container | None = None
            try:
                network = client.networks.create(
                    f"bbj-net-{suffix}",
                    driver="bridge",
                    internal=True,
                    labels={LABEL: spec.execution_id},
                    check_duplicate=True,
                )
                gateway = client.containers.create(
                    spec.gateway_image,
                    name=f"bbj-gw-{suffix}",
                    environment={
                        "BBJ_EXECUTION_NONCE": nonce,
                        "BBJ_SEED_STATE": _b64(spec.seed),
                        "BBJ_GATEWAY_PORT": str(GATEWAY_PORT),
                    },
                    **hardening_kwargs(spec.limits, f"bbj-net-{suffix}", spec.execution_id),
                )
                gateway.start()
                return network, gateway
            except APIError:
                self._cleanup(None, gateway, network)  # created-but-not-started included
                if attempt == 2:
                    raise
                logger.warning(
                    "sandbox start failed; retrying once", extra={"component": "harness"}
                )
        raise AssertionError("unreachable")

    def _wait_gateway_ready(
        self, gateway: Container, nonce: str, limits: SandboxLimits, cancel: CancelToken
    ) -> bool:
        marker = b'"type": "gateway_ready"'
        deadline = time.monotonic() + limits.gateway_ready_timeout_s
        while time.monotonic() < deadline and not cancel.cancelled:
            logs = gateway.logs(stdout=True, stderr=False)
            if marker in logs and nonce.encode() in logs:
                return True
            gateway.reload()
            if gateway.status in ("exited", "dead"):
                return False
            cancel.wait(_POLL_S)
        return False

    def _wait_agent(
        self, agent: Container, limits: SandboxLimits, cancel: CancelToken
    ) -> tuple[SandboxOutcome, int | None]:
        deadline = time.monotonic() + limits.exec_timeout_s
        while True:
            agent.reload()
            if agent.status in ("exited", "dead"):
                code = agent.attrs.get("State", {}).get("ExitCode")
                return "completed", int(code) if isinstance(code, int) else None
            if cancel.cancelled:
                self._kill(agent)
                return "cancelled", None
            if time.monotonic() >= deadline:
                self._kill(agent)
                return "timed_out", None
            cancel.wait(_POLL_S)

    def _stop_gateway(self, gateway: Container, limits: SandboxLimits) -> None:
        try:
            gateway.reload()
            if gateway.status == "running":
                gateway.kill(signal="SIGTERM")
            deadline = time.monotonic() + limits.gateway_stop_grace_s
            while time.monotonic() < deadline:
                gateway.reload()
                if gateway.status in ("exited", "dead"):
                    return
                time.sleep(_POLL_S)
            self._kill(gateway)  # evidence will be incomplete → INCONCLUSIVE
        except (NotFound, APIError):
            return

    @staticmethod
    def _kill(container: Container) -> None:
        with contextlib.suppress(NotFound, APIError):
            container.kill()

    @staticmethod
    def _logs(container: Container, cap: int) -> tuple[bytes, bool]:
        try:
            data: bytes = container.logs(stdout=True, stderr=False)
        except (NotFound, APIError):
            return b"", False
        return (data[:cap], True) if len(data) > cap else (data, False)

    @staticmethod
    def _cleanup(
        agent: Container | None, gateway: Container | None, network: Network | None
    ) -> bool:
        ok = True
        for container in (agent, gateway):
            if container is None:
                continue
            try:
                container.remove(force=True)
            except NotFound:
                pass
            except APIError:
                ok = False
        if network is not None:
            try:
                network.remove()
            except NotFound:
                pass
            except APIError:
                ok = False
        if not ok:
            logger.error("sandbox cleanup failed", extra={"component": "harness"})
        return ok

    def _meta(self, client: docker.DockerClient, spec: ExecutionSpec) -> dict[str, Any]:
        def image_id(name: str) -> str | None:
            try:
                return str(client.images.get(name).id)
            except DockerException:
                return None

        lim = spec.limits
        return {
            "gateway_image": spec.gateway_image,
            "gateway_image_id": image_id(spec.gateway_image),
            "agent_image": spec.agent_image,
            "agent_image_id": image_id(spec.agent_image),
            "limits": {
                "exec_timeout_s": lim.exec_timeout_s,
                "mem_bytes": lim.mem_bytes,
                "nano_cpus": lim.nano_cpus,
                "pids_limit": lim.pids_limit,
                "network": "internal",
                "read_only_rootfs": True,
                "user": "10001:10001",
                "cap_drop": "ALL",
            },
        }

    def _error_output(self, spec: ExecutionSpec, nonce: str, error: str) -> RawExecutionOutput:
        return RawExecutionOutput(
            execution_id=spec.execution_id,
            nonce=nonce,
            outcome="sandbox_error",
            gateway_stdout=b"",
            gateway_stdout_truncated=False,
            agent_stdout=b"",
            agent_stdout_truncated=False,
            agent_exit_code=None,
            duration_ms=0,
            cleanup_ok=True,
            error=error,
        )

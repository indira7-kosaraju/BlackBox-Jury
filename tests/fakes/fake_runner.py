"""TEST-ONLY sandbox runner. Never importable from src/ (AGENTS.md §5).

Runs the real gateway + RefundBot code in-process with the spec's seed and task
input and returns a ``RawExecutionOutput`` in exactly the format the Docker
runner produces (nonce-tagged gateway stdout, agent stdout final line). Knobs let
tests simulate outcomes the real sandbox produces (timeout, cancel, Docker down).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field

from bbj_gateway.events import EventSink
from bbj_gateway.store import Store
from bbj_gateway.tools import Gateway
from bbj_refundbot.agent import RefundBot, TaskInput

from blackbox_jury.sandbox.runner import (
    CancelToken,
    ExecutionSpec,
    RawExecutionOutput,
    SandboxOutcome,
    SandboxUnavailableError,
)
from tests.unit.sandbox_images.conftest import InProcessClient


@dataclass
class FakeSandboxRunner:
    ready: dict[str, str] = field(default_factory=lambda: {"docker": "ok", "images": "ok"})
    force_outcome: SandboxOutcome | None = None
    unavailable_after: int | None = None  # raise SandboxUnavailableError after N executions
    cancel_after: int | None = (
        None  # cancel the token after N executions (simulates a cancel mid-run)
    )
    calls: int = 0
    executed: list[str] = field(default_factory=list)
    swept: int = 0

    def readiness(self) -> dict[str, str]:
        return self.ready

    def sweep_orphans(self) -> int:
        self.swept += 1
        return 0

    def execute(self, spec: ExecutionSpec, cancel: CancelToken | None = None) -> RawExecutionOutput:
        if self.unavailable_after is not None and self.calls >= self.unavailable_after:
            raise SandboxUnavailableError("Docker is not reachable: fake")
        self.calls += 1
        self.executed.append(spec.task_input["user_message"])
        nonce = secrets.token_hex(16)
        out: list[str] = []
        gw = Gateway(Store(spec.seed), EventSink(nonce, out.append))
        gw.sink.emit("state_snapshot", phase="before", state=gw.store.snapshot())
        gw.sink.emit("gateway_ready", port=8080)
        reply = RefundBot(InProcessClient(gw), TaskInput(**spec.task_input)).run()
        gw.sink.emit("state_snapshot", phase="after", state=gw.store.snapshot())
        gw.sink.emit("gateway_stopped", reason="SIGTERM", tool_calls=gw.call_count)
        if cancel is not None and self.cancel_after is not None and self.calls >= self.cancel_after:
            cancel.cancel()
        outcome: SandboxOutcome = self.force_outcome or "completed"
        agent = (
            (json.dumps({"final_response": reply}) + "\n").encode()
            if outcome == "completed"
            else b""
        )
        return RawExecutionOutput(
            execution_id=spec.execution_id,
            nonce=nonce,
            outcome=outcome,
            gateway_stdout="".join(out).encode(),
            gateway_stdout_truncated=False,
            agent_stdout=agent,
            agent_stdout_truncated=False,
            agent_exit_code=0 if outcome == "completed" else None,
            duration_ms=5,
            cleanup_ok=True,
            meta={"agent_image": spec.agent_image, "limits": {"network": "internal"}, "fake": True},
        )

"""Fixtures for unit-testing the sandbox images' code in-process.

These tests exercise our own gateway and RefundBot code with synthetic data and
hand-written scenario strings. No container, subprocess or network beyond a
127.0.0.1 test socket is involved; real target execution happens only in the
Docker sandbox (Phase 5).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from bbj_gateway.events import EventSink
from bbj_gateway.store import Store
from bbj_gateway.tools import Gateway
from bbj_refundbot.agent import Mode, RefundBot, TaskInput, ToolResult

SEED_PATH = Path(__file__).resolve().parents[3] / "seeds" / "synthetic_store.json"
NONCE = "0123456789abcdef0123456789abcdef"
PRINCIPAL = "C-1001"


def base_seed() -> dict[str, Any]:
    seed: dict[str, Any] = json.loads(SEED_PATH.read_text())
    return seed


class Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, line: str) -> None:
        self.lines.append(line)

    @property
    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]


class InProcessClient:
    """ToolClient that calls the gateway dispatcher directly (no HTTP)."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway

    def call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        out = self.gateway.handle(tool, json.dumps(args).encode())
        if out.response["ok"]:
            return ToolResult(ok=True, result=out.response["result"])
        return ToolResult(ok=False, error_code=out.response["error"]["code"])


GatewayFactory = Callable[..., tuple[Gateway, Recorder]]


@pytest.fixture
def seed() -> dict[str, Any]:
    return base_seed()


@pytest.fixture
def make_gateway() -> GatewayFactory:
    def _make(seed: dict[str, Any] | None = None, max_calls: int = 100) -> tuple[Gateway, Recorder]:
        rec = Recorder()
        gw = Gateway(
            Store(copy.deepcopy(seed or base_seed())), EventSink(NONCE, rec.write), max_calls
        )
        return gw, rec

    return _make


@pytest.fixture
def run_bot(make_gateway: GatewayFactory) -> Callable[..., tuple[str, Gateway, Recorder]]:
    def _run(
        message: str, mode: Mode, seed: dict[str, Any] | None = None
    ) -> tuple[str, Gateway, Recorder]:
        s = seed or base_seed()
        gw, rec = make_gateway(s)
        task = TaskInput(
            principal=PRINCIPAL,
            user_message=message,
            mode=mode,
            system_context=dict(s.get("agent_context", {})),
        )
        reply = RefundBot(InProcessClient(gw), task).run()
        return reply, gw, rec

    return _run


def calls(rec: Recorder) -> list[tuple[str, dict[str, Any] | None, str]]:
    """(tool, args, outcome) for every recorded tool call, in order."""
    return [(e["tool"], e["args"], e["outcome"]) for e in rec.of_type("tool_call")]

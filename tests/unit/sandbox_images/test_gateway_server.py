"""Gateway HTTP layer and lifecycle, served on a 127.0.0.1 ephemeral port in-process."""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer
from typing import Any

import pytest
from bbj_gateway.server import load_config, main, make_handler
from bbj_refundbot.agent import RefundBot, TaskInput
from bbj_refundbot.main import HttpToolClient

from tests.unit.sandbox_images.conftest import NONCE, GatewayFactory, Recorder, base_seed


@pytest.fixture
def served(make_gateway: GatewayFactory) -> Iterator[tuple[str, Recorder, Any]]:
    gw, rec = make_gateway()
    server = HTTPServer(("127.0.0.1", 0), make_handler(gw))
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", rec, gw
    finally:
        server.shutdown()
        server.server_close()


def _post(url: str, body: bytes) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(  # noqa: S310  # loopback test server
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310  # loopback test server
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_tool_call_over_http(served: tuple[str, Recorder, Any]) -> None:
    base, rec, _ = served
    status, body = _post(f"{base}/tools/get_order", json.dumps({"order_id": "A-100"}).encode())
    assert status == 200 and body["result"]["order"]["order_id"] == "A-100"
    assert rec.of_type("tool_call")[0]["tool"] == "get_order"


def test_non_tool_paths_rejected_and_recorded(served: tuple[str, Recorder, Any]) -> None:
    base, rec, _ = served
    status, _ = _post(f"{base}/admin/reset", b"{}")
    assert status == 404
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/tools/get_order", timeout=5)  # noqa: S310  # loopback
    assert exc.value.code == 404
    rejected = rec.of_type("http_request_rejected")
    assert [(r["method"], r["path"]) for r in rejected] == [
        ("POST", "/admin/reset"),
        ("GET", "/tools/get_order"),
    ]
    assert rec.of_type("tool_call") == []


def test_refundbot_http_client_end_to_end(served: tuple[str, Recorder, Any]) -> None:
    base, rec, gw = served
    task = TaskInput(
        principal="C-1001", user_message="Refund my damaged order A-100.", mode="hardened"
    )
    reply = RefundBot(HttpToolClient(base), task).run()
    assert "has been processed" in reply
    assert [e["tool"] for e in rec.of_type("tool_call")] == [
        "list_orders",
        "get_order",
        "issue_refund",
    ]
    assert gw.store.refunded_cents("A-100") == 4000


def _env(**over: str) -> dict[str, str]:
    env = {
        "BBJ_EXECUTION_NONCE": NONCE,
        "BBJ_SEED_STATE": base64.b64encode(json.dumps(base_seed()).encode()).decode(),
    }
    env.update(over)
    return env


def test_load_config_ok() -> None:
    nonce, seed, port = load_config(_env())
    assert nonce == NONCE and seed["seed_version"] == 1 and port == 8080


@pytest.mark.parametrize(
    "over",
    [
        {"BBJ_EXECUTION_NONCE": "short"},
        {"BBJ_EXECUTION_NONCE": NONCE.upper()},
        {"BBJ_SEED_STATE": "%%%"},
        {"BBJ_SEED_STATE": "A" * 100_000},
        {"BBJ_GATEWAY_PORT": "70000"},
    ],
)
def test_main_rejects_bad_config(over: dict[str, str]) -> None:
    out: list[str] = []
    assert main(_env(**over), out.append) == 2
    assert len(out) == 1 and json.loads(out[0])["type"] == "gateway_error"
    assert json.loads(out[0])["bbj"] is None


def test_main_rejects_bad_seed_with_tagged_error() -> None:
    bad = base64.b64encode(json.dumps({"customers": "x"}).encode()).decode()
    out: list[str] = []
    assert main(_env(BBJ_SEED_STATE=bad), out.append) == 2
    event = json.loads(out[0])
    assert event["type"] == "gateway_error" and event["bbj"] == NONCE


def test_main_lifecycle_emits_before_ready_after_stopped() -> None:
    """Run main() on an ephemeral port and stop it with a real SIGINT-equivalent."""
    import signal

    out: list[str] = []
    ready = threading.Event()

    def write(line: str) -> None:
        out.append(line)
        if '"gateway_ready"' in line:
            ready.set()

    # Signals can only be installed from the main thread, so main() runs here and a
    # helper thread delivers SIGINT once the gateway reports ready.
    def stopper() -> None:
        assert ready.wait(5)
        signal.raise_signal(signal.SIGINT)

    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    previous = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    try:
        code = main(_env(BBJ_GATEWAY_PORT="18765"), write)
    finally:
        signal.signal(signal.SIGINT, previous[0])
        signal.signal(signal.SIGTERM, previous[1])
    t.join(5)
    events = [json.loads(line) for line in out]
    assert code == 0
    assert [e["type"] for e in events] == [
        "state_snapshot",
        "gateway_ready",
        "state_snapshot",
        "gateway_stopped",
    ]
    assert [e.get("phase") for e in events if e["type"] == "state_snapshot"] == ["before", "after"]
    assert events[-1]["reason"] == "SIGINT" and events[-1]["tool_calls"] == 0
    assert all(e["bbj"] == NONCE for e in events)
    assert [e["seq"] for e in events] == [1, 2, 3, 4]


def test_stop_signal_is_not_swallowed_by_socketserver() -> None:
    # socketserver catches Exception during request handling; _Stop must escape it.
    from bbj_gateway.server import _Stop

    assert not issubclass(_Stop, Exception) and issubclass(_Stop, BaseException)

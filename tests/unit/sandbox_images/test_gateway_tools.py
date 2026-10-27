from __future__ import annotations

import json
from typing import Any

import pytest
from bbj_gateway.store import SeedError, Store

from tests.unit.sandbox_images.conftest import NONCE, GatewayFactory, base_seed


def _call(gw: Any, tool: str, args: Any) -> Any:
    raw = args if isinstance(args, bytes) else json.dumps(args).encode()
    return gw.handle(tool, raw)


def test_get_order_includes_refund_and_approval_info(make_gateway: GatewayFactory) -> None:
    gw, _ = make_gateway()
    out = _call(gw, "get_order", {"order_id": "A-100"})
    assert out.status == 200
    order = out.response["result"]["order"]
    assert order["customer_id"] == "C-1001"
    assert order["total_cents"] == 4000
    assert order["refunded_cents"] == 0
    assert order["refund_approval_cents"] is None


def test_gateway_does_not_enforce_ownership(make_gateway: GatewayFactory) -> None:
    # Policy is the verifier's job; the mock backend allows the cross-customer read.
    gw, _ = make_gateway()
    assert _call(gw, "get_order", {"order_id": "B-200"}).outcome == "ok"


def test_list_orders_and_get_customer(make_gateway: GatewayFactory) -> None:
    gw, _ = make_gateway()
    orders = _call(gw, "list_orders", {"customer_id": "C-1001"}).response["result"]["orders"]
    assert [o["order_id"] for o in orders] == ["A-100", "A-101"]
    cust = _call(gw, "get_customer", {"customer_id": "C-2002"}).response["result"]["customer"]
    assert cust["email"].endswith("@example.test")


def test_issue_refund_changes_state(make_gateway: GatewayFactory) -> None:
    gw, rec = make_gateway()
    out = _call(gw, "issue_refund", {"order_id": "A-100", "amount": 40, "reason": "damaged"})
    assert out.outcome == "ok" and out.side_effect
    assert out.response["result"]["refund"] == {
        "refund_id": "R-0001",
        "order_id": "A-100",
        "amount_cents": 4000,
        "reason": "damaged",
        "status": "issued",
    }
    assert gw.store.refunded_cents("A-100") == 4000
    event = rec.of_type("tool_call")[0]
    assert event["side_effect"] is True and event["bbj"] == NONCE


def test_send_email_and_update_note_are_side_effects(make_gateway: GatewayFactory) -> None:
    gw, _ = make_gateway()
    out = _call(gw, "send_email", {"to": "x@example.test", "subject": "s", "body": "b"})
    assert out.side_effect and out.response["result"]["message"]["message_id"] == "M-0001"
    out = _call(gw, "update_order_note", {"order_id": "A-101", "note": "new"})
    assert out.side_effect and gw.store.get_order("A-101")["note"] == "new"  # type: ignore[index]


@pytest.mark.parametrize(
    ("tool", "args", "code"),
    [
        ("get_order", {}, "invalid_args"),
        ("get_order", {"order_id": 5}, "invalid_args"),
        ("get_order", {"order_id": "A-100", "extra": 1}, "invalid_args"),
        ("issue_refund", {"order_id": "A-100", "amount": -1}, "invalid_args"),
        ("issue_refund", {"order_id": "A-100", "amount": 0}, "invalid_args"),
        ("issue_refund", {"order_id": "A-100", "amount": 1.234}, "invalid_args"),
        ("issue_refund", {"order_id": "A-100", "amount": True}, "invalid_args"),
        ("issue_refund", {"order_id": "A-100", "amount": "NaN"}, "invalid_args"),
        ("issue_refund", {"order_id": "A-100", "amount": 1e9}, "invalid_args"),
        ("send_email", {"to": "not-an-email", "subject": "s", "body": "b"}, "invalid_args"),
        ("get_order", {"order_id": "x" * 33}, "invalid_args"),
    ],
)
def test_invalid_args_recorded_without_side_effect(
    make_gateway: GatewayFactory, tool: str, args: dict[str, Any], code: str
) -> None:
    gw, rec = make_gateway()
    before = gw.store.snapshot()
    out = _call(gw, tool, args)
    assert out.status == 400 and out.response["error"]["code"] == code
    assert out.outcome == "invalid_args" and not out.side_effect
    assert gw.store.snapshot() == before
    assert rec.of_type("tool_call")[0]["outcome"] == "invalid_args"


@pytest.mark.parametrize(
    ("raw", "code"), [(b"not json", "invalid_json"), (b"[1,2]", "invalid_json")]
)
def test_malformed_body_recorded(make_gateway: GatewayFactory, raw: bytes, code: str) -> None:
    gw, rec = make_gateway()
    out = _call(gw, "get_order", raw)
    assert out.response["error"]["code"] == code
    assert rec.of_type("tool_call")[0]["args"] is None


def test_oversized_body_rejected(make_gateway: GatewayFactory) -> None:
    gw, _ = make_gateway()
    out = _call(gw, "get_order", b"{" + b" " * 20000 + b"}")
    assert out.status == 413 and out.outcome == "invalid_args"


@pytest.mark.parametrize("tool", ["delete_all_orders", "../etc/passwd", "GET_ORDER", "x" * 200])
def test_unknown_tools_recorded(make_gateway: GatewayFactory, tool: str) -> None:
    gw, rec = make_gateway()
    out = _call(gw, tool, {})
    assert out.status == 404 and out.outcome == "unknown_tool"
    ev = rec.of_type("tool_call")[0]
    assert ev["outcome"] == "unknown_tool" and len(ev["tool"]) <= 64


def test_unknown_order_is_error_without_side_effect(make_gateway: GatewayFactory) -> None:
    gw, _ = make_gateway()
    out = _call(gw, "issue_refund", {"order_id": "Z-999", "amount": 5})
    assert out.outcome == "error" and not out.side_effect and out.status == 404


def test_fault_fail(make_gateway: GatewayFactory) -> None:
    seed = base_seed()
    seed["faults"] = [
        {"tool": "issue_refund", "match": {"order_id": "A-100"}, "kind": "fail", "times": 1}
    ]
    gw, _ = make_gateway(seed)
    first = _call(gw, "issue_refund", {"order_id": "A-100", "amount": 40})
    assert first.status == 503 and not first.side_effect
    assert gw.store.refunded_cents("A-100") == 0
    second = _call(gw, "issue_refund", {"order_id": "A-100", "amount": 40})
    assert second.outcome == "ok"  # fault exhausted after `times`


def test_fault_fail_after_commit(make_gateway: GatewayFactory) -> None:
    seed = base_seed()
    seed["faults"] = [
        {
            "tool": "issue_refund",
            "match": {"order_id": "A-100"},
            "kind": "fail_after_commit",
            "times": 1,
        }
    ]
    gw, _ = make_gateway(seed)
    out = _call(gw, "issue_refund", {"order_id": "A-100", "amount": 40})
    assert out.status == 504 and out.outcome == "error"
    assert out.side_effect  # the refund committed even though the caller saw an error
    assert gw.store.refunded_cents("A-100") == 4000


def test_call_limit(make_gateway: GatewayFactory) -> None:
    gw, rec = make_gateway(max_calls=2)
    for _ in range(3):
        out = _call(gw, "get_order", {"order_id": "A-100"})
    assert out.status == 429
    assert len(rec.of_type("tool_call")) == 3


def test_event_envelope_and_sequence(make_gateway: GatewayFactory) -> None:
    gw, rec = make_gateway()
    _call(gw, "get_order", {"order_id": "A-100"})
    _call(gw, "nope", {})
    events = rec.events
    assert [e["seq"] for e in events] == [1, 2]
    for e in events:
        assert e["bbj"] == NONCE and e["v"] == 1 and e["ts"].endswith("+00:00")
        assert set(e) >= {"call_seq", "tool", "args", "response", "outcome", "side_effect"}


def test_long_strings_clipped_in_evidence(make_gateway: GatewayFactory) -> None:
    gw, rec = make_gateway()
    _call(gw, "update_order_note", {"order_id": "A-101", "note": "n" * 1000})
    _call(gw, "get_order", {"order_id": "A-101", "junk": "j" * 10000})
    ev = rec.of_type("tool_call")[1]
    assert len(ev["args"]["junk"]) < 4200


def test_snapshot_is_deterministic_and_complete() -> None:
    a, b = Store(base_seed()).snapshot(), Store(base_seed()).snapshot()
    assert a == b
    assert set(a) == {"customers", "orders", "refunds", "approvals", "outbox"}
    assert [o["order_id"] for o in a["orders"]] == ["A-100", "A-101", "B-200"]


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"customers": "x"},
        {"customers": [{"customer_id": "C-1", "evil": 1}]},
        {
            "orders": [
                {
                    "order_id": "A-1",
                    "customer_id": "NOPE",
                    "item": "i",
                    "total_cents": 1,
                    "status": "s",
                }
            ]
        },
        {"faults": [{"tool": "issue_refund", "kind": "explode"}]},
    ],
)
def test_bad_seed_rejected(bad: Any) -> None:
    with pytest.raises(SeedError):
        Store(bad)


def test_fault_delay_then_normal(make_gateway: GatewayFactory) -> None:
    import time

    seed = base_seed()
    seed["faults"] = [
        {"tool": "get_order", "match": {"order_id": "A-100"}, "kind": "delay", "delay_ms": 150}
    ]
    gw, _ = make_gateway(seed)
    start = time.monotonic()
    out = _call(gw, "get_order", {"order_id": "A-100"})
    assert time.monotonic() - start >= 0.14 and out.outcome == "ok"


@pytest.mark.parametrize("delay", [-1, 10_001, "5"])
def test_fault_delay_bounds(delay: Any) -> None:
    seed = base_seed()
    seed["faults"] = [{"tool": "get_order", "match": {}, "kind": "delay", "delay_ms": delay}]
    with pytest.raises(SeedError):
        Store(seed)

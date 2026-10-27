"""Run lifecycle through the API (fake in-process runner, inline worker; no Docker).

Real Docker execution through the API is covered in tests/integration/.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from blackbox_jury.core.config import Settings
from blackbox_jury.db import repo
from blackbox_jury.db.models import AuditEvent, Execution, Run
from blackbox_jury.db.seed import seed
from tests.fakes.fake_runner import FakeSandboxRunner

API = "/api/v1"


@pytest.fixture
def ids(
    app: FastAPI, client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> dict[str, Any]:
    seed(app.state.db, settings)
    targets = client.get(f"{API}/targets", headers=auth_headers).json()["items"]
    contract = client.get(f"{API}/contracts?name=refund_support_v1", headers=auth_headers).json()[
        "items"
    ][0]
    scenarios = client.get(f"{API}/scenarios", headers=auth_headers).json()["items"]
    return {
        "target": targets[0]["id"],
        "contract": contract["id"],
        "scenarios": {s["key"]: s["id"] for s in scenarios},
    }


def _key() -> str:
    return uuid.uuid4().hex


def _start(
    client: TestClient, headers: dict[str, str], body: dict[str, Any], key: str | None = None
) -> Any:
    return client.post(
        f"{API}/runs", json=body, headers={**headers, "Idempotency-Key": key or _key()}
    )


def _hardened_target(client: TestClient, headers: dict[str, str]) -> str:
    resp = client.post(
        f"{API}/targets",
        headers=headers,
        json={
            "name": "refundbot-fixed",
            "adapter_type": "sandboxed_container",
            "config": {"image": "bbj-refundbot:0.1.0", "mode": "hardened"},
            "authorization": {"confirm_authorized": True, "statement": "bundled mock"},
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# -- happy path ---------------------------------------------------------------------------------


def test_demo_suite_vulnerable_end_to_end(
    client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]
) -> None:
    resp = _start(
        client,
        auth_headers,
        {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "demo"},
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["id"]
    run = client.get(f"{API}/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "completed"
    assert run["totals"] == {
        "selected": 10,
        "executed": 10,
        "CONFIRMED": 8,
        "NOT_REPRODUCED": 2,
        "INCONCLUSIVE": 0,
        "not_run": 0,
        "pending": 0,
    }
    assert run["target_config_snapshot"]["mode"] == "vulnerable"

    report = client.get(f"{API}/runs/{run_id}/report", headers=auth_headers).json()
    assert report["partial"] is False and report["synthetic"] is True
    assert "do not establish that the target is secure" in report["disclaimer"]
    assert len(report["findings"]) == 8
    assert [f["severity"] for f in report["findings"]][:1] == ["critical"]  # sorted by severity
    assert all(c["task_completed"] is True for c in report["benign_controls"])
    text = json.dumps({k: v for k, v in report.items() if k != "disclaimer"}).lower()
    assert "is secure" not in text and "guarantee" not in text  # never claims security

    pi = next(
        f for f in report["findings"] if f["execution"]["scenario"]["key"] == "pi_order_note_refund"
    )
    assert set(pi["failed_checks"]) == {"ARG_OWNERSHIP", "REFUND_APPROVAL"}
    assert pi["execution"]["agent_claim"]["provenance"] == "AGENT_CLAIM"
    assert any(
        c["provenance"] == "VERIFIED" and c["outcome"] == "FAIL" and c["evidence_refs"]
        for c in pi["execution"]["checks"]
    )
    assert any(
        "bbj run --scenario pi_order_note_refund" in step for step in pi["reproduction_steps"]
    )


def test_hardened_target_not_reproduced(
    client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]
) -> None:
    target = _hardened_target(client, auth_headers)
    run = _start(
        client, auth_headers, {"target_id": target, "contract_id": ids["contract"], "suite": "demo"}
    ).json()
    totals = client.get(f"{API}/runs/{run['id']}", headers=auth_headers).json()["totals"]
    assert totals["NOT_REPRODUCED"] == 10 and totals["CONFIRMED"] == 0
    assert (
        client.get(f"{API}/runs/{run['id']}/report", headers=auth_headers).json()["findings"] == []
    )


def test_evidence_endpoints(
    client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]
) -> None:
    body = {
        "target_id": ids["target"],
        "contract_id": ids["contract"],
        "scenario_ids": [ids["scenarios"]["pi_order_note_refund"]],
    }
    run_id = _start(client, auth_headers, body).json()["id"]
    exes = client.get(f"{API}/runs/{run_id}/executions", headers=auth_headers).json()["items"]
    assert (
        len(exes) == 1 and exes[0]["verdict"] == "CONFIRMED" and exes[0]["severity"] == "critical"
    )
    detail = client.get(f"{API}/executions/{exes[0]['id']}", headers=auth_headers).json()
    tools = [c["tool"] for c in detail["verified_outcome"]["tool_calls"]]
    assert tools == ["get_order", "get_order", "issue_refund"]
    kinds = {a["kind"] for a in detail["artifacts"]}
    assert {"state_before", "state_after", "agent_final_response", "task_input"} <= kinds
    art = next(a for a in detail["artifacts"] if a["kind"] == "agent_final_response")
    content = client.get(f"{API}/artifacts/{art['id']}", headers=auth_headers).json()
    assert "B-200" in content["content_redacted"] and len(content["sha256"]) == 64
    # every evidence ref cited by a FAIL resolves to a stored tool call or artifact
    stored = {c["id"] for c in detail["verified_outcome"]["tool_calls"]} | {
        a["id"] for a in detail["artifacts"]
    }
    for check in detail["checks"]:
        if check["outcome"] == "FAIL":
            assert set(check["evidence_refs"]) <= stored
    finding_id = client.get(f"{API}/runs/{run_id}/report", headers=auth_headers).json()["findings"][
        0
    ]["id"]
    assert (
        client.get(f"{API}/findings/{finding_id}", headers=auth_headers).json()["verdict"]
        == "CONFIRMED"
    )


def test_audit_events_recorded(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]
) -> None:
    _hardened_target(client, auth_headers)
    _start(
        client,
        auth_headers,
        {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "slice"},
    )
    client.get(f"{API}/runs", headers={"Authorization": "Bearer nope"})
    with app.state.db.session() as s:
        actions = [a.action for a in s.scalars(select(AuditEvent))]
    assert {"target.create", "target.authorize", "run.create", "auth.denied"} <= set(actions)


# -- idempotency & quotas ---------------------------------------------------------------------


def test_idempotency(client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]) -> None:
    body = {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "slice"}
    first = _start(client, auth_headers, body, key="same-key-123")
    again = _start(client, auth_headers, body, key="same-key-123")
    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    other = _start(client, auth_headers, {**body, "suite": "demo"}, key="same-key-123")
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_key_reuse"


@pytest.mark.parametrize(
    "headers", [{}, {"Idempotency-Key": "short"}, {"Idempotency-Key": "bad key with spaces"}]
)
def test_idempotency_key_required_and_validated(
    client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any], headers: dict[str, str]
) -> None:
    body = {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "slice"}
    resp = client.post(f"{API}/runs", json=body, headers={**auth_headers, **headers})
    assert resp.status_code == 422


def test_one_active_run_at_a_time(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]
) -> None:
    with app.state.db.session() as s:
        project = app.state.project_id
        repo.create_run(
            s,
            project=project,
            target=repo.get_target(s, project, ids["target"]),
            contract=repo.get_contract(s, project, ids["contract"]),
            scenario_rows=repo.latest_scenarios_by_keys(s, project, ["ctl_order_status_own"]),
            idempotency_key="stuck-queued-run",
            request_sha256="0" * 64,
            actor="test",
        )
    resp = _start(
        client,
        auth_headers,
        {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "slice"},
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "run_already_active"


def test_run_create_rate_limited(
    client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]
) -> None:
    body = {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "slice"}
    codes = [_start(client, auth_headers, body).status_code for _ in range(11)]
    assert codes[:10] == [202] * 10 and codes[10] == 429


# -- failure paths: never reported as passed --------------------------------------------------


def test_sandbox_unavailable_at_create_executes_nothing(
    client: TestClient,
    auth_headers: dict[str, str],
    ids: dict[str, Any],
    fake_runner: FakeSandboxRunner,
) -> None:
    fake_runner.ready = {"docker": "unreachable", "images": "unknown"}
    resp = _start(
        client,
        auth_headers,
        {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "demo"},
    )
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "sandbox_unavailable"
    assert fake_runner.calls == 0
    assert client.get(f"{API}/runs", headers=auth_headers).json()["items"] == []


def test_docker_lost_mid_run_fails_run_and_nothing_passes(
    client: TestClient,
    auth_headers: dict[str, str],
    ids: dict[str, Any],
    fake_runner: FakeSandboxRunner,
) -> None:
    fake_runner.unavailable_after = 3
    run_id = _start(
        client,
        auth_headers,
        {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "demo"},
    ).json()["id"]
    run = client.get(f"{API}/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "failed" and run["status_reason"] == "sandbox_unavailable"
    exes = client.get(f"{API}/runs/{run_id}/executions", headers=auth_headers).json()["items"]
    assert sum(e["verdict"] is not None and e["inconclusive_reason"] is None for e in exes) == 3
    rest = [e for e in exes if e["inconclusive_reason"] == "sandbox_error"]
    assert len(rest) == 7 and all(e["verdict"] == "INCONCLUSIVE" for e in rest)


@pytest.mark.parametrize(
    ("outcome", "status", "reason"),
    [
        ("timed_out", "timed_out", "timeout"),
        ("cancelled", "cancelled", "cancelled"),
        ("sandbox_error", "error", "sandbox_error"),
    ],
)
def test_non_completed_executions_are_inconclusive(
    client: TestClient,
    auth_headers: dict[str, str],
    ids: dict[str, Any],
    fake_runner: FakeSandboxRunner,
    outcome: str,
    status: str,
    reason: str,
) -> None:
    fake_runner.force_outcome = outcome  # type: ignore[assignment]
    body = {
        "target_id": ids["target"],
        "contract_id": ids["contract"],
        "scenario_ids": [ids["scenarios"]["ctl_order_status_own"]],
    }
    run_id = _start(client, auth_headers, body).json()["id"]
    exe = client.get(f"{API}/runs/{run_id}/executions", headers=auth_headers).json()["items"][0]
    assert (exe["status"], exe["verdict"], exe["inconclusive_reason"]) == (
        status,
        "INCONCLUSIVE",
        reason,
    )


def test_cancel_queued_run(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    ids: dict[str, Any],
    fake_runner: FakeSandboxRunner,
) -> None:
    app.state.worker.submit = lambda run_id: None  # leave it queued
    run_id = _start(
        client,
        auth_headers,
        {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "demo"},
    ).json()["id"]
    assert client.post(f"{API}/runs/{run_id}/cancel", headers=auth_headers).status_code == 202
    app.state.service.process_run(run_id)
    run = client.get(f"{API}/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "cancelled" and run["totals"]["INCONCLUSIVE"] == 10
    # cancelled before anything started: nothing was executed
    assert run["totals"]["executed"] == 0 and run["totals"]["not_run"] == 10
    assert fake_runner.calls == 0
    again = client.post(f"{API}/runs/{run_id}/cancel", headers=auth_headers)
    assert again.status_code == 409 and again.json()["error"]["code"] == "run_not_active"


def test_cancel_mid_run(
    client: TestClient,
    auth_headers: dict[str, str],
    ids: dict[str, Any],
    fake_runner: FakeSandboxRunner,
) -> None:
    fake_runner.cancel_after = 2
    run_id = _start(
        client,
        auth_headers,
        {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "demo"},
    ).json()["id"]
    run = client.get(f"{API}/runs/{run_id}", headers=auth_headers).json()
    assert run["status"] == "cancelled" and fake_runner.calls == 2
    # 2 executions really ran; the 8 never started are not counted as executed
    assert run["totals"]["executed"] == 2 and run["totals"]["not_run"] == 8
    assert run["totals"]["INCONCLUSIVE"] == 8


def test_crash_recovery(
    app: FastAPI, client: TestClient, ids: dict[str, Any], fake_runner: FakeSandboxRunner
) -> None:
    with app.state.db.session() as s:
        project = app.state.project_id
        run = repo.create_run(
            s,
            project=project,
            target=repo.get_target(s, project, ids["target"]),
            contract=repo.get_contract(s, project, ids["contract"]),
            scenario_rows=repo.latest_scenarios_by_keys(
                s, project, ["ctl_order_status_own", "pi_order_note_refund"]
            ),
            idempotency_key="crashed-run-key",
            request_sha256="0" * 64,
            actor="test",
        )
        repo.transition_run(s, run.id, "queued", "running")
        first = repo.executions_for_run(s, run.id)[0]
        repo.transition_execution(s, first.id, "pending", "running")
    app.state.service.recover()
    with app.state.db.session() as s:
        r = s.get_one(Run, run.id)
        assert (r.status, r.status_reason) == ("failed", "interrupted")
        exes = s.scalars(select(Execution).where(Execution.run_id == run.id)).all()
        assert all(e.verdict == "INCONCLUSIVE" and e.status == "error" for e in exes)
    assert fake_runner.swept >= 1


# -- validation & authorization ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"config": {"image": "alpine:latest", "mode": "vulnerable"}}, "image_not_allowed"),
        ({"authorization": {"confirm_authorized": False}}, "authorization_required"),
        ({"config": {"image": "bbj-refundbot:0.1.0", "mode": "llm"}}, "provider_not_configured"),
        (
            {
                "config": {
                    "image": "bbj-refundbot:0.1.0",
                    "mode": "vulnerable",
                    "url": "http://evil.example.test",
                }
            },
            "validation_error",
        ),
        ({"url": "https://third-party.example.test/agent"}, "validation_error"),
        ({"adapter_type": "http"}, "validation_error"),
    ],
)
def test_target_validation(
    client: TestClient, auth_headers: dict[str, str], body: dict[str, Any], code: str
) -> None:
    base = {
        "name": "t1",
        "adapter_type": "sandboxed_container",
        "config": {"image": "bbj-refundbot:0.1.0", "mode": "vulnerable"},
        "authorization": {"confirm_authorized": True},
    }
    resp = client.post(f"{API}/targets", headers=auth_headers, json={**base, **body})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == code


def test_contract_endpoints(
    client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]
) -> None:
    contract = client.get(f"{API}/contracts/{ids['contract']}", headers=auth_headers).json()["body"]
    assert (
        client.post(f"{API}/contracts/validate", json=contract, headers=auth_headers).json()[
            "valid"
        ]
        is True
    )
    assert (
        client.post(f"{API}/contracts", json=contract, headers=auth_headers).status_code == 200
    )  # idempotent
    changed = {**contract, "task": "changed"}
    assert client.post(f"{API}/contracts", json=changed, headers=auth_headers).status_code == 409
    bad = {**contract, "allowed_tools": ["drop_db"]}
    resp = client.post(f"{API}/contracts/validate", json=bad, headers=auth_headers)
    assert resp.status_code == 422 and resp.json()["error"]["details"]["fields"]
    ghost = {**contract, "principal": "C-9999", "version": 2}
    assert client.post(f"{API}/contracts", json=ghost, headers=auth_headers).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/targets",
        "/contracts",
        "/scenarios",
        "/runs",
        "/runs/run_01JX00000000000000000000AA",
        "/executions/exe_01JX00000000000000000000AA",
        "/artifacts/art_01JX00000000000000000000AA",
        "/findings/fnd_01JX00000000000000000000AA",
        "/runs/run_01JX00000000000000000000AA/report",
    ],
)
def test_every_protected_route_requires_auth(client: TestClient, path: str) -> None:
    assert client.get(f"{API}{path}").status_code == 401


def test_unknown_or_foreign_ids_are_404(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]
) -> None:
    with app.state.db.session() as s:
        other = repo.ensure_project(s, "other-tenant").id
        foreign = repo.create_target(
            s,
            project=other,
            name="theirs",
            image="bbj-refundbot:0.1.0",
            mode="vulnerable",
            actor="x",
        )
    assert client.get(f"{API}/targets/{foreign.id}", headers=auth_headers).status_code == 404
    body = {"target_id": foreign.id, "contract_id": ids["contract"], "suite": "slice"}
    assert _start(client, auth_headers, body).status_code == 404


def test_body_limits(client: TestClient, auth_headers: dict[str, str]) -> None:
    big = client.post(
        f"{API}/contracts/validate",
        content=b"{" + b" " * 300_000 + b"}",
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert big.status_code == 413

    def chunks() -> Any:
        yield b'{"a":'
        yield b"1}"

    chunked = client.post(
        f"{API}/contracts/validate",
        content=chunks(),
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert chunked.status_code == 411


def test_pagination(client: TestClient, auth_headers: dict[str, str], ids: dict[str, Any]) -> None:
    body = {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "slice"}
    created = [_start(client, auth_headers, body).json()["id"] for _ in range(3)]
    page1 = client.get(f"{API}/runs?limit=2", headers=auth_headers).json()
    page2 = client.get(
        f"{API}/runs?limit=2&cursor={page1['next_cursor']}", headers=auth_headers
    ).json()
    seen = [r["id"] for r in page1["items"] + page2["items"]]
    assert seen == sorted(created, reverse=True) and page2["next_cursor"] is None
    assert client.get(f"{API}/runs?cursor=%%%", headers=auth_headers).status_code == 400


def test_sandbox_receives_the_persisted_execution_id(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    ids: dict[str, Any],
    fake_runner: FakeSandboxRunner,
) -> None:
    seen: list[str] = []
    original = fake_runner.execute

    def spy(spec: Any, cancel: Any = None) -> Any:
        seen.append(spec.execution_id)
        return original(spec, cancel)

    fake_runner.execute = spy  # type: ignore[method-assign]
    body = {"target_id": ids["target"], "contract_id": ids["contract"], "suite": "slice"}
    run_id = _start(client, auth_headers, body).json()["id"]
    exe = client.get(f"{API}/runs/{run_id}/executions", headers=auth_headers).json()["items"][0]
    assert seen == [exe["id"]]

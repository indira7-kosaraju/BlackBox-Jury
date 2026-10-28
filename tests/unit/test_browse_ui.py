"""Read-only UI endpoints (/findings, /regression-tests, /audit-events, /settings)
and serving of the built web UI at /ui."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blackbox_jury.api.app import create_app
from blackbox_jury.core.config import Settings
from blackbox_jury.db.seed import seed
from tests.conftest import TEST_TOKEN
from tests.fakes.fake_runner import FakeSandboxRunner

API = "/api/v1"


def _start(client: TestClient, headers: dict[str, str], path: str, body: dict[str, Any]) -> Any:
    return client.post(
        f"{API}{path}", json=body, headers={**headers, "Idempotency-Key": uuid.uuid4().hex}
    )


@pytest.fixture
def demo(
    app: FastAPI, client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> dict[str, Any]:
    seed(app.state.db, settings)
    target = client.get(f"{API}/targets", headers=auth_headers).json()["items"][0]["id"]
    contract = client.get(f"{API}/contracts?name=refund_support_v1", headers=auth_headers).json()[
        "items"
    ][0]["id"]
    run = _start(
        client,
        auth_headers,
        "/runs",
        {"target_id": target, "contract_id": contract, "suite": "demo"},
    ).json()
    return {"target": target, "contract": contract, "run": run["id"]}


# -- read-only endpoints ------------------------------------------------------------------


def test_findings_list(
    client: TestClient, auth_headers: dict[str, str], demo: dict[str, Any]
) -> None:
    items = client.get(f"{API}/findings", headers=auth_headers).json()["items"]
    assert len(items) == 8 and all(i["verdict"] == "CONFIRMED" for i in items)
    pi = next(i for i in items if i["scenario_key"] == "pi_order_note_refund")
    assert (
        pi["severity"] == "critical"
        and pi["target"] == "refundbot"
        and pi["target_mode"] == "vulnerable"
    )
    assert (
        set(pi["failed_checks"]) == {"ARG_OWNERSHIP", "REFUND_APPROVAL"}
        and pi["regression_test_id"] is None
    )
    crit = client.get(f"{API}/findings?severity=critical", headers=auth_headers).json()["items"]
    assert crit and all(i["severity"] == "critical" for i in crit)
    assert client.get(f"{API}/findings?verdict=PASSED", headers=auth_headers).status_code == 422


def test_regression_tests_list_with_retest_history(
    client: TestClient, auth_headers: dict[str, str], demo: dict[str, Any]
) -> None:
    assert client.get(f"{API}/regression-tests", headers=auth_headers).json()["items"] == []
    finding = next(
        i
        for i in client.get(f"{API}/findings", headers=auth_headers).json()["items"]
        if i["scenario_key"] == "pi_order_note_refund"
    )
    rgt = client.post(
        f"{API}/findings/{finding['id']}/regression-test", headers=auth_headers
    ).json()["id"]
    _start(
        client, auth_headers, "/retests", {"regression_test_id": rgt, "target_id": demo["target"]}
    )
    client.patch(
        f"{API}/targets/{demo['target']}",
        headers=auth_headers,
        json={"config": {"mode": "hardened"}},
    )
    _start(
        client, auth_headers, "/retests", {"regression_test_id": rgt, "target_id": demo["target"]}
    )
    item = client.get(f"{API}/regression-tests", headers=auth_headers).json()["items"][0]
    assert item["id"] == rgt and item["scenario_key"] == "pi_order_note_refund"
    assert item["original_verdict"] == "CONFIRMED" and item["original_severity"] == "critical"
    assert item["expected_checks"] == {"ARG_OWNERSHIP": "PASS", "REFUND_APPROVAL": "PASS"}
    history = item["retests"]  # newest first
    assert [(h["target_mode"], h["verdict"]) for h in history] == [
        ("hardened", "NOT_REPRODUCED"),
        ("vulnerable", "CONFIRMED"),
    ]
    linked = client.get(f"{API}/findings", headers=auth_headers).json()["items"]
    assert next(i for i in linked if i["id"] == finding["id"])["regression_test_id"] == rgt


def test_audit_events_are_real_and_redacted(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], demo: dict[str, Any]
) -> None:
    from blackbox_jury.db import repo

    with app.state.db.session() as s:
        repo.audit(
            s,
            project=app.state.project_id,
            actor="test",
            action="test.secret",
            request_id="r",
            details={"note": f"token {TEST_TOKEN}", "api_key": "abc"},
        )
    items = client.get(f"{API}/audit-events?limit=200", headers=auth_headers).json()["items"]
    actions = [i["action"] for i in items]
    assert "run.create" in actions and "target.create" in actions
    assert TEST_TOKEN not in json.dumps(items)
    secret = next(i for i in items if i["action"] == "test.secret")
    assert secret["details"]["api_key"] == "[REDACTED]"


def test_settings_contain_no_secrets(client: TestClient, auth_headers: dict[str, str]) -> None:
    body = client.get(f"{API}/settings", headers=auth_headers).json()
    assert body["sandbox"]["adapter_types"] == ["sandboxed_container"]
    assert body["sandbox"]["pids_limit"] == 64 and body["sandbox"]["exec_timeout_s"] == 30.0
    assert body["jury"] == {
        "enabled": False,
        "provider": "none",
        "model": None,
        "credentials_configured": None,
    }
    assert body["retention"]["implemented"] is False
    text = json.dumps(body).lower()
    assert TEST_TOKEN.lower() not in text and "api_token" not in text and "sk-" not in text


@pytest.mark.parametrize("path", ["/findings", "/regression-tests", "/audit-events", "/settings"])
def test_new_endpoints_require_auth(client: TestClient, path: str) -> None:
    assert client.get(f"{API}{path}").status_code == 401


# -- /ui static serving -------------------------------------------------------------------


@pytest.fixture
def ui_client(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>Blackbox Jury</title><div id=root></div>"
    )
    (dist / "assets" / "app-abc123.js").write_text("console.log('ui')")
    (tmp_path / "secret.txt").write_text("outside dist")
    app = create_app(
        settings.model_copy(update={"ui_dist_dir": dist}),
        runner=FakeSandboxRunner(),
        inline_worker=True,
    )
    with TestClient(app, follow_redirects=False) as c:
        yield c


def test_ui_index_and_spa_fallback(ui_client: TestClient) -> None:
    for path in ("/ui", "/ui/", "/ui/runs/run_X", "/ui/evidence"):
        resp = ui_client.get(path)
        assert resp.status_code == 200 and "Blackbox Jury" in resp.text, path
        csp = resp.headers["content-security-policy"]
        assert (
            "script-src 'self'" in csp
            and "frame-ancestors 'none'" in csp
            and "unsafe-eval" not in csp
        )
        assert resp.headers["cache-control"] == "no-store"


def test_ui_assets_cached_and_missing_asset_404(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui/assets/app-abc123.js")
    assert resp.status_code == 200 and "immutable" in resp.headers["cache-control"]
    assert ui_client.get("/ui/assets/missing.js").status_code == 404


@pytest.mark.parametrize(
    "path", ["/ui/../secret.txt", "/ui/%2e%2e/secret.txt", "/ui/..%2fsecret.txt"]
)
def test_ui_never_serves_files_outside_dist(ui_client: TestClient, path: str) -> None:
    resp = ui_client.get(path)
    assert "outside dist" not in resp.text


def test_root_redirects_to_ui(ui_client: TestClient) -> None:
    resp = ui_client.get("/")
    assert resp.status_code in (302, 307) and resp.headers["location"] == "/ui/"


def test_ui_not_built_gives_clear_message(client: TestClient) -> None:
    resp = client.get("/ui/")
    assert resp.status_code == 404 and "make ui" in resp.json()["error"]["message"]

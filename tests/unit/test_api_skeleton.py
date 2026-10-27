from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blackbox_jury.api.app import create_app
from blackbox_jury.core.config import Settings
from tests.conftest import TEST_TOKEN


def _assert_error(body: dict[str, object], code: str) -> None:
    err = body["error"]
    assert isinstance(err, dict)
    assert err["code"] == code
    assert isinstance(err["message"], str)
    assert str(err["request_id"]).startswith("req_")


def test_health_is_public(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert resp.headers["X-Request-Id"].startswith("req_")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Basic {TEST_TOKEN}"},
        {"Authorization": f"Bearer {TEST_TOKEN}x"},
        {"Authorization": f"Bearer {TEST_TOKEN[:-1]}"},
    ],
)
def test_protected_route_rejects_bad_auth(client: TestClient, headers: dict[str, str]) -> None:
    resp = client.get("/api/v1/ready", headers=headers)
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    _assert_error(resp.json(), "unauthorized")
    assert TEST_TOKEN not in resp.text


def test_auth_denied_is_logged_without_token(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        client.get("/api/v1/ready", headers={"Authorization": "Bearer wrong-token"})
    assert any(r.getMessage() == "auth.denied" for r in caplog.records)


class _FakeRunner:
    def __init__(self, state: dict[str, str]) -> None:
        self.state = state

    def readiness(self) -> dict[str, str]:
        return self.state


@pytest.mark.parametrize(
    "sandbox",
    [
        {"docker": "ok", "images": "ok"},
        {"docker": "ok", "images": "missing"},
        {"docker": "unreachable", "images": "unknown"},
    ],
)
def test_ready_reports_not_ready_honestly(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], sandbox: dict[str, str]
) -> None:
    # No database until Phase 6, so readiness is never claimed even if Docker is fine.
    app.state.runner = _FakeRunner(sandbox)
    resp = client.get("/api/v1/ready", headers=auth_headers)
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"database": "not_configured", **sandbox}


def test_unknown_route_uses_error_schema(client: TestClient) -> None:
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    _assert_error(resp.json(), "not_found")


def test_unhandled_error_hides_internals(settings: Settings) -> None:
    app: FastAPI = create_app(settings)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError(f"internal detail {TEST_TOKEN} /etc/secret/path")

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/boom")
    assert resp.status_code == 500
    _assert_error(resp.json(), "internal_error")
    assert "internal detail" not in resp.text
    assert TEST_TOKEN not in resp.text
    assert "/etc/secret" not in resp.text


def test_validation_error_does_not_echo_input(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/typed")
    def typed(n: int) -> dict[str, int]:
        return {"n": n}

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/typed", params={"n": "sk-ant-should-not-echo-1234567890"})
    assert resp.status_code == 422
    _assert_error(resp.json(), "validation_error")
    assert "should-not-echo" not in resp.text


def test_docs_only_in_dev() -> None:
    prod = create_app(Settings(api_token=TEST_TOKEN, env="prod"))  # type: ignore[arg-type]
    dev = create_app(Settings(api_token=TEST_TOKEN, env="dev"))  # type: ignore[arg-type]
    with TestClient(prod) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
    with TestClient(dev) as c:
        assert c.get("/docs").status_code == 200

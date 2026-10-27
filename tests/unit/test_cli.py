"""``bbj`` CLI: local commands on a temp DB; API commands routed into the test app."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from blackbox_jury import cli
from blackbox_jury.core.config import Settings
from blackbox_jury.db.seed import seed

runner = CliRunner()


@pytest.fixture
def wired(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, client: TestClient, tmp_path: Path
) -> TestClient:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)

    def http(
        method: str,
        url: str,
        token: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        resp = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}", **(headers or {})}
        )
        return resp.status_code, resp.json()

    monkeypatch.setattr(cli, "_http", http)
    return client


def test_seed_is_idempotent(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    first = runner.invoke(cli.app, ["seed"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output.strip().splitlines()[-1]) == {
        "contracts_created": 1,
        "scenarios_created": 10,
        "target_created": True,
    }
    second = runner.invoke(cli.app, ["seed"])
    assert json.loads(second.output.strip().splitlines()[-1]) == {
        "contracts_created": 0,
        "scenarios_created": 0,
        "target_created": False,
    }


def test_db_upgrade(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    result = runner.invoke(cli.app, ["db", "upgrade"])
    assert result.exit_code == 0 and "up to date" in result.output


def test_run_streams_progress_and_writes_report(
    wired: TestClient, settings: Settings, tmp_path: Path
) -> None:
    seed(wired.app.state.db, settings)  # type: ignore[attr-defined]
    result = runner.invoke(cli.app, ["run", "--suite", "slice"])
    assert result.exit_code == 0, result.output
    assert "■ CONFIRMED CRITICAL  pi_order_note_refund" in result.output
    assert "CONFIRMED 1 · NOT REPRODUCED 0 · INCONCLUSIVE 0" in result.output
    assert "do not establish that the target is secure" in result.output
    reports = list((tmp_path / "reports").glob("*.json"))
    assert len(reports) == 1 and json.loads(reports[0].read_text())["totals"]["CONFIRMED"] == 1


def test_run_by_scenario_key_and_json(wired: TestClient, settings: Settings) -> None:
    seed(wired.app.state.db, settings)  # type: ignore[attr-defined]
    result = runner.invoke(cli.app, ["run", "--scenario", "ctl_order_status_own", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["run"]["totals"]["NOT_REPRODUCED"] == 1


def test_run_unknown_inputs_fail_cleanly(wired: TestClient, settings: Settings) -> None:
    seed(wired.app.state.db, settings)  # type: ignore[attr-defined]
    assert runner.invoke(cli.app, ["run", "--target", "nope"]).exit_code != 0
    assert runner.invoke(cli.app, ["run", "--scenario", "not_a_scenario"]).exit_code != 0


def test_sandbox_unavailable_message(wired: TestClient, settings: Settings) -> None:
    seed(wired.app.state.db, settings)  # type: ignore[attr-defined]
    wired.app.state.runner.ready = {"docker": "unreachable", "images": "unknown"}  # type: ignore[attr-defined]
    result = runner.invoke(cli.app, ["run", "--suite", "slice"])
    assert result.exit_code == 1
    assert "no tests were executed" in result.output


def test_cli_refuses_non_local_api(wired: TestClient, settings: Settings) -> None:
    result = runner.invoke(cli.app, ["status", "run_X", "--api", "https://example.test"])
    assert result.exit_code != 0


def test_cancel_requires_confirmation(wired: TestClient) -> None:
    result = runner.invoke(cli.app, ["cancel", "run_X"], input="n\n")
    assert result.exit_code == 1


def test_fix_and_retest_via_cli(
    wired: TestClient, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    seed(wired.app.state.db, settings)  # type: ignore[attr-defined]
    assert runner.invoke(cli.app, ["run", "--suite", "slice"]).exit_code == 0
    report = json.loads(next((tmp_path / "reports").glob("*.json")).read_text())
    finding_id = report["findings"][0]["id"]

    exported = runner.invoke(cli.app, ["export", finding_id])
    assert exported.exit_code == 0, exported.output
    files = list((tmp_path / "regressions").glob("rgt_*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["format"] == "bbj-regression/1"

    assert (
        runner.invoke(
            cli.app, ["target", "set-mode", "refundbot", "hardened"], input="n\n"
        ).exit_code
        == 1
    )
    fixed = runner.invoke(cli.app, ["target", "set-mode", "refundbot", "hardened", "--yes"])
    assert fixed.exit_code == 0 and "mode hardened (config v2)" in fixed.output

    retested = runner.invoke(cli.app, ["retest", "--file", str(files[0])])
    assert retested.exit_code == 0, retested.output
    assert (
        "original CONFIRMED (critical) -> retest NOT_REPRODUCED: NOT_REPRODUCED" in retested.output
    )
    assert "ARG_OWNERSHIP: expected PASS, got PASS" in retested.output


def test_retest_requires_exactly_one_source(wired: TestClient) -> None:
    assert runner.invoke(cli.app, ["retest"]).exit_code != 0


def test_cli_retries_get_on_429(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    replies = iter([(429, {"error": {"code": "rate_limited"}}), (200, {"ok": True})])
    monkeypatch.setattr(cli, "_http", lambda *a, **k: next(replies))
    assert cli._api("http://127.0.0.1:8000", "GET", "/runs/x") == {"ok": True}


def test_serve_uses_settings_host_and_port(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from blackbox_jury import serve

    captured: dict[str, Any] = {}
    monkeypatch.setattr(serve, "get_settings", lambda: settings.model_copy(update={"port": 8767}))
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kw: captured.update(app=app, **kw))
    serve.main()
    assert (
        captured["host"] == "127.0.0.1" and captured["port"] == 8767 and captured["factory"] is True
    )


def test_cli_default_api_follows_settings(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings.model_copy(update={"port": 8767}))
    seen: list[str] = []
    monkeypatch.setattr(cli, "_http", lambda method, url, *a, **k: (seen.append(url), (200, {}))[1])
    cli._api("", "GET", "/runs")
    assert seen == ["http://127.0.0.1:8767/api/v1/runs"]

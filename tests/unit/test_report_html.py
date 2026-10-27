"""Static HTML report: escaping, CSP, no external resources, states, accessibility."""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blackbox_jury.core.config import Settings
from blackbox_jury.db.seed import seed
from blackbox_jury.reports import render
from blackbox_jury.reports.render import render_html
from tests.fakes.fake_runner import FakeSandboxRunner

API = "/api/v1"
HOSTILE = "<script>alert(1)</script><img src=x onerror=alert(2)>{{ 7*7 }}{% raw %}\"'&"


@pytest.fixture
def demo(
    app: FastAPI, client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> dict[str, Any]:
    seed(app.state.db, settings)
    target = client.get(f"{API}/targets", headers=auth_headers).json()["items"][0]["id"]
    contract = client.get(f"{API}/contracts?name=refund_support_v1", headers=auth_headers).json()[
        "items"
    ][0]["id"]
    run = client.post(
        f"{API}/runs",
        json={"target_id": target, "contract_id": contract, "suite": "demo"},
        headers={**auth_headers, "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    report: dict[str, Any] = client.get(
        f"{API}/runs/{run['id']}/report", headers=auth_headers
    ).json()
    return {"run": run, "report": report, "target": target, "contract": contract}


def test_html_endpoint_headers_and_content(
    client: TestClient, auth_headers: dict[str, str], demo: dict[str, Any]
) -> None:
    resp = client.get(f"{API}/runs/{demo['run']['id']}/report?format=html", headers=auth_headers)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    html = resp.text
    assert html.startswith("<!doctype html>") and '<html lang="en">' in html
    for needle in (
        "VERIFIED",
        "AGENT CLAIM",
        "Evidence timeline",
        "Tool-call trace",
        "State diff",
        "Reproduction steps",
        "Benign controls",
        "Limitations",
        "Jury not run",
        "do not establish that the target is secure",
        "All data is synthetic",
    ):
        assert needle in html, needle
    assert html.count('class="card"') >= 8  # one card per finding (+ summary)


def test_no_scripts_or_external_resources(demo: dict[str, Any]) -> None:
    html = render_html(demo["report"])
    assert "<script" not in html.lower()
    assert not re.search(r"(src|href)\s*=\s*\"?(https?:)?//", html)
    assert "@import" not in html and "url(" not in html


def test_untrusted_content_is_escaped(demo: dict[str, Any]) -> None:
    report = copy.deepcopy(demo["report"])
    f = report["findings"][0]
    f["title"] = HOSTILE
    f["execution"]["agent_claim"]["text"] = HOSTILE
    f["execution"]["checks"][0]["message"] = HOSTILE
    f["execution"]["verified_outcome"]["tool_calls"][0]["args"] = {"note": HOSTILE}
    f["reproduction_steps"] = [HOSTILE]
    html = render_html(report)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert (
        "{{ 7*7 }}" in html
    )  # template syntax inside evidence is shown literally, never evaluated


def test_autoescape_cannot_be_turned_off() -> None:
    assert render._env.autoescape is True


def test_state_diff_rendered(demo: dict[str, Any]) -> None:
    pi = next(
        f
        for f in demo["report"]["findings"]
        if f["execution"]["scenario"]["key"] == "pi_order_note_refund"
    )
    diff = pi["execution"]["state_diff"]
    assert diff and diff[0]["change"] == "+" and diff[0]["table"] == "refunds"
    assert diff[0]["after"]["order_id"] == "B-200" and diff[0]["after"]["amount_cents"] == 95000
    assert "+ added" in render_html(demo["report"])


def test_only_the_disclaimer_mentions_security(demo: dict[str, Any]) -> None:
    html = render_html(demo["report"]).lower()
    assert html.count("secure") == 1  # "...do not establish that the target is secure."
    assert "guarantee" not in html and "fixed" not in html


def test_empty_state_never_says_secure(
    client: TestClient, auth_headers: dict[str, str], demo: dict[str, Any]
) -> None:
    client.patch(
        f"{API}/targets/{demo['target']}",
        headers=auth_headers,
        json={"config": {"mode": "hardened"}},
    )
    run = client.post(
        f"{API}/runs",
        json={"target_id": demo["target"], "contract_id": demo["contract"], "suite": "demo"},
        headers={**auth_headers, "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    html = client.get(f"{API}/runs/{run['id']}/report?format=html", headers=auth_headers).text
    assert "No violations observed in 10 executed scenarios." in html
    assert html.lower().count("secure") == 1


def test_partial_and_failed_banners(demo: dict[str, Any]) -> None:
    partial = copy.deepcopy(demo["report"]) | {"partial": True}
    assert "partial report" in render_html(partial)
    failed = copy.deepcopy(demo["report"])
    failed["run"] = {**failed["run"], "status": "failed", "status_reason": "sandbox_unavailable"}
    assert "Run failed (sandbox_unavailable)" in render_html(failed)


def test_inconclusive_run_renders(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    demo: dict[str, Any],
    fake_runner: FakeSandboxRunner,
) -> None:
    fake_runner.force_outcome = "timed_out"
    run = client.post(
        f"{API}/runs",
        json={"target_id": demo["target"], "contract_id": demo["contract"], "suite": "slice"},
        headers={**auth_headers, "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    html = client.get(f"{API}/runs/{run['id']}/report?format=html", headers=auth_headers).text
    # A timed-out execution must never read as "no violations observed".
    assert "No violations observed" not in html
    assert "1 scenario inconclusive" in html and "not evidence that a behaviour is absent" in html
    assert "INCONCLUSIVE 1" in html and "timeout" in html


def test_cancelled_run_shows_not_run(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], demo: dict[str, Any]
) -> None:
    app.state.worker.submit = lambda run_id: None
    run = client.post(
        f"{API}/runs",
        json={"target_id": demo["target"], "contract_id": demo["contract"], "suite": "demo"},
        headers={**auth_headers, "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    client.post(f"{API}/runs/{run['id']}/cancel", headers=auth_headers)
    app.state.service.process_run(run["id"])
    html = client.get(f"{API}/runs/{run['id']}/report?format=html", headers=auth_headers).text
    assert "Executed 0" in html and "not run 10" in html
    assert "No violations observed" not in html


def test_retest_section(
    client: TestClient, auth_headers: dict[str, str], demo: dict[str, Any]
) -> None:
    finding = demo["report"]["findings"][0]["id"]
    rgt = client.post(f"{API}/findings/{finding}/regression-test", headers=auth_headers).json()[
        "id"
    ]
    client.patch(
        f"{API}/targets/{demo['target']}",
        headers=auth_headers,
        json={"config": {"mode": "hardened"}},
    )
    run = client.post(
        f"{API}/retests",
        json={"regression_test_id": rgt, "target_id": demo["target"]},
        headers={**auth_headers, "Idempotency-Key": uuid.uuid4().hex},
    ).json()
    html = client.get(f"{API}/runs/{run['id']}/report?format=html", headers=auth_headers).text
    assert 'id="retest"' in html and "NOT REPRODUCED" in html and "Expected" in html


def test_accessibility_basics(demo: dict[str, Any]) -> None:
    html = render_html(demo["report"])
    assert 'class="skip" href="#findings"' in html
    assert '<th scope="col">' in html and '<th scope="row">' in html
    assert 'aria-label="Report sections"' in html and "<details>" in html
    assert "prefers-color-scheme: dark" in html and "prefers-reduced-motion" in html
    assert 'name="viewport"' in html


def test_cli_report_html(
    app: FastAPI,
    client: TestClient,
    demo: dict[str, Any],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from typer.testing import CliRunner

    from blackbox_jury import cli
    from tests.unit.test_cli import runner as _  # noqa: F401  # ensure module importable

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_api", lambda api, method, path, *a, **k: demo["report"])
    out = tmp_path / "r.html"
    result = CliRunner().invoke(cli.app, ["report", demo["run"]["id"], "--html", "--out", str(out)])
    assert result.exit_code == 0 and out.read_text().startswith("<!doctype html>")

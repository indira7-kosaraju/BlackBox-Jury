"""Fix -> export -> retest loop (PRD FR29-FR30), via the API with the fake runner."""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from blackbox_jury.core.config import Settings
from blackbox_jury.core.ids import new_id
from blackbox_jury.db.models import AuditEvent, Finding, RegressionTest, Run
from blackbox_jury.db.seed import seed
from blackbox_jury.regression.export import RegressionDocumentError, verify_document
from tests.unit.sandbox_images.conftest import base_seed

API = "/api/v1"


def _post(
    client: TestClient, headers: dict[str, str], path: str, body: dict[str, Any] | None = None
) -> Any:
    return client.post(
        f"{API}{path}", json=body, headers={**headers, "Idempotency-Key": uuid.uuid4().hex}
    )


@pytest.fixture
def confirmed(
    app: FastAPI, client: TestClient, settings: Settings, auth_headers: dict[str, str]
) -> dict[str, Any]:
    """A completed demo run against the vulnerable target and its injection finding."""
    seed(app.state.db, settings)
    target = client.get(f"{API}/targets", headers=auth_headers).json()["items"][0]
    contract = client.get(f"{API}/contracts?name=refund_support_v1", headers=auth_headers).json()[
        "items"
    ][0]
    run = _post(
        client,
        auth_headers,
        "/runs",
        {"target_id": target["id"], "contract_id": contract["id"], "suite": "demo"},
    ).json()
    report = client.get(f"{API}/runs/{run['id']}/report", headers=auth_headers).json()
    finding = next(
        f for f in report["findings"] if f["execution"]["scenario"]["key"] == "pi_order_note_refund"
    )
    return {
        "target": target,
        "contract": contract,
        "run": run,
        "finding": finding,
        "report": report,
    }


def test_full_fix_and_retest_loop(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], confirmed: dict[str, Any]
) -> None:
    # 1. export
    exported = client.post(
        f"{API}/findings/{confirmed['finding']['id']}/regression-test", headers=auth_headers
    )
    assert exported.status_code == 201, exported.text
    body = exported.json()["body"]
    assert body["format"] == "bbj-regression/1"
    assert body["expect"] == {
        "checks": {"ARG_OWNERSHIP": "PASS", "REFUND_APPROVAL": "PASS"},
        "verdict": "NOT_REPRODUCED",
    }
    verify_document(body, base_seed())
    rgt = exported.json()["id"]

    # 2. retest before the fix: still reproduces
    before = _post(
        client,
        auth_headers,
        "/retests",
        {"regression_test_id": rgt, "target_id": confirmed["target"]["id"]},
    ).json()
    cmp_before = client.get(f"{API}/runs/{before['id']}/report", headers=auth_headers).json()[
        "retest_comparison"
    ]
    assert cmp_before[0]["regression_status"] == "still_reproduces"
    assert cmp_before[0]["actual_checks"] == {"ARG_OWNERSHIP": "FAIL", "REFUND_APPROVAL": "FAIL"}

    # 3. apply the fix (hardened mode)
    patched = client.patch(
        f"{API}/targets/{confirmed['target']['id']}",
        headers=auth_headers,
        json={"config": {"mode": "hardened"}},
    ).json()
    assert patched["config"]["mode"] == "hardened" and patched["config_version"] == 2

    # 4. retest after the fix: not reproduced, expected checks pass
    after = _post(
        client,
        auth_headers,
        "/retests",
        {"regression_test_id": rgt, "target_id": confirmed["target"]["id"]},
    )
    assert after.status_code == 202
    report = client.get(f"{API}/runs/{after.json()['id']}/report", headers=auth_headers).json()
    assert report["run"]["kind"] == "retest"
    c = report["retest_comparison"][0]
    assert (c["original_verdict"], c["retest_verdict"], c["regression_status"]) == (
        "CONFIRMED",
        "NOT_REPRODUCED",
        "not_reproduced",
    )
    assert c["actual_checks"] == {"ARG_OWNERSHIP": "PASS", "REFUND_APPROVAL": "PASS"}
    assert c["original_run_id"] == confirmed["run"]["id"]
    assert "fixed" not in str(report["retest_comparison"]).lower()

    # the original run keeps its snapshot; the change is audited with before/after
    with app.state.db.session() as s:
        assert s.get_one(Run, confirmed["run"]["id"]).target_config_snapshot["mode"] == "vulnerable"
        update = s.scalars(select(AuditEvent).where(AuditEvent.action == "target.update")).one()
        assert (
            update.details["before"]["mode"] == "vulnerable"
            and update.details["after"]["mode"] == "hardened"
        )
        actions = {a.action for a in s.scalars(select(AuditEvent))}
    assert {"regression.export", "retest.create"} <= actions


def test_export_is_idempotent_and_downloadable(
    client: TestClient, auth_headers: dict[str, str], confirmed: dict[str, Any]
) -> None:
    path = f"{API}/findings/{confirmed['finding']['id']}/regression-test"
    first, second = client.post(path, headers=auth_headers), client.post(path, headers=auth_headers)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    dl = client.get(
        f"{API}/regression-tests/{first.json()['id']}?download=true", headers=auth_headers
    )
    assert (
        "attachment" in dl.headers["content-disposition"]
        and dl.json()["format"] == "bbj-regression/1"
    )


def test_only_confirmed_findings_export(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], confirmed: dict[str, Any]
) -> None:
    ctl = next(
        e for e in confirmed["report"]["executions"] if e["scenario"] == "ctl_order_status_own"
    )
    with app.state.db.session() as s:
        fid = new_id("fnd")
        s.add(
            Finding(
                id=fid,
                execution_id=ctl["execution_id"],
                run_id=confirmed["run"]["id"],
                title="needs review",
                category="benign",
                verdict="INCONCLUSIVE",
                severity=None,
                failed_check_ids=[],
                repro_steps=[],
                created_at="now",
            )
        )
    resp = client.post(f"{API}/findings/{fid}/regression-test", headers=auth_headers)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "finding_not_confirmed"


def test_tampered_regression_rejected(
    app: FastAPI, client: TestClient, auth_headers: dict[str, str], confirmed: dict[str, Any]
) -> None:
    rgt = client.post(
        f"{API}/findings/{confirmed['finding']['id']}/regression-test", headers=auth_headers
    ).json()["id"]
    with app.state.db.session() as s:
        row = s.get_one(RegressionTest, rgt)
        body = copy.deepcopy(row.body)
        body["scenario"]["user_message"] = "something else"
        row.body = body
    resp = _post(
        client,
        auth_headers,
        "/retests",
        {"regression_test_id": rgt, "target_id": confirmed["target"]["id"]},
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "regression_invalid"


def test_revoked_target_cannot_be_retested(
    client: TestClient, auth_headers: dict[str, str], confirmed: dict[str, Any]
) -> None:
    rgt = client.post(
        f"{API}/findings/{confirmed['finding']['id']}/regression-test", headers=auth_headers
    ).json()["id"]
    revoked = client.patch(
        f"{API}/targets/{confirmed['target']['id']}",
        headers=auth_headers,
        json={"authorization_status": "revoked"},
    )
    assert revoked.json()["authorization_status"] == "revoked"
    resp = _post(
        client,
        auth_headers,
        "/retests",
        {"regression_test_id": rgt, "target_id": confirmed["target"]["id"]},
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "target_not_authorized"
    run = _post(
        client,
        auth_headers,
        "/runs",
        {
            "target_id": confirmed["target"]["id"],
            "contract_id": confirmed["contract"]["id"],
            "suite": "slice",
        },
    )
    assert run.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"config": {"mode": "llm"}},
        {"config": {"mode": "hardened", "image": "alpine:latest"}},
        {"authorization_status": "authorized"},
        {"name": "renamed"},
    ],
)
def test_patch_validation(
    client: TestClient,
    auth_headers: dict[str, str],
    confirmed: dict[str, Any],
    body: dict[str, Any],
) -> None:
    resp = client.patch(
        f"{API}/targets/{confirmed['target']['id']}", headers=auth_headers, json=body
    )
    assert resp.status_code == 422


def test_verify_document_detects_drift() -> None:
    from blackbox_jury.regression.export import build_document

    doc = build_document(
        regression_test_id="rgt_X",
        title="t",
        finding_id="f",
        run_id="r",
        execution_id="e",
        severity="high",
        failed_checks=["ARG_OWNERSHIP"],
        scenario_body={"bogus": True},
        base_seed=base_seed(),
        contract_name="c",
        contract_version=1,
        contract_sha256="0" * 64,
        target_snapshot={},
    )
    with pytest.raises(RegressionDocumentError):  # scenario body is not a valid Scenario
        verify_document(doc, base_seed())
    changed_seed = base_seed() | {"seed_version": 2}
    from blackbox_jury.scenarios.loader import load_library

    good = build_document(
        regression_test_id="rgt_X",
        title="t",
        finding_id="f",
        run_id="r",
        execution_id="e",
        severity="high",
        failed_checks=["ARG_OWNERSHIP"],
        scenario_body=load_library()[0].model_dump(mode="json"),
        base_seed=base_seed(),
        contract_name="c",
        contract_version=1,
        contract_sha256="0" * 64,
        target_snapshot={},
    )
    verify_document(good, base_seed())
    with pytest.raises(RegressionDocumentError, match="seed changed"):
        verify_document(good, changed_seed)
    with pytest.raises(RegressionDocumentError):
        verify_document({**good, "expect": {"checks": {}}}, base_seed())

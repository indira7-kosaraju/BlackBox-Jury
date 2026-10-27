"""End to end through the API with the REAL Docker runner and background worker.

Requires Docker and `make images`; fails (does not skip) otherwise.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import docker
import pytest
from fastapi.testclient import TestClient

from blackbox_jury.api.app import create_app
from blackbox_jury.core.config import Settings
from blackbox_jury.db.seed import seed
from blackbox_jury.sandbox.docker_runner import LABEL

pytestmark = pytest.mark.docker
API = "/api/v1"


@pytest.fixture
def live(settings: Settings, auth_headers: dict[str, str]) -> Iterator[TestClient]:
    app = create_app(settings)  # real DockerSandboxRunner, threaded worker
    with TestClient(app) as client:
        ready = client.get(f"{API}/ready", headers=auth_headers).json()
        assert ready["status"] == "ready", f"not ready: {ready} (start Docker, run `make images`)"
        seed(app.state.db, settings)
        yield client


def _wait(
    client: TestClient, headers: dict[str, str], run_id: str, timeout: float = 180
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run: dict[str, Any] = client.get(f"{API}/runs/{run_id}", headers=headers).json()
        if run["status"] in ("completed", "failed", "cancelled", "timed_out"):
            return run
        time.sleep(0.5)
    raise AssertionError("run did not finish in time")


def _ids(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    target = client.get(f"{API}/targets", headers=headers).json()["items"][0]["id"]
    contract = client.get(f"{API}/contracts?name=refund_support_v1", headers=headers).json()[
        "items"
    ][0]["id"]
    return target, contract


def test_demo_suite_vulnerable_then_hardened(
    live: TestClient, auth_headers: dict[str, str]
) -> None:
    target, contract = _ids(live, auth_headers)
    resp = live.post(
        f"{API}/runs",
        headers={**auth_headers, "Idempotency-Key": uuid.uuid4().hex},
        json={"target_id": target, "contract_id": contract, "suite": "demo"},
    )
    assert resp.status_code == 202, resp.text
    run = _wait(live, auth_headers, resp.json()["id"])
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
    report = live.get(f"{API}/runs/{run['id']}/report", headers=auth_headers).json()
    assert {f["severity"] for f in report["findings"]} <= {"critical", "high", "medium"}
    detail = live.get(
        f"{API}/executions/{report['findings'][0]['execution']['execution_id']}",
        headers=auth_headers,
    ).json()
    assert detail["sandbox_meta"]["limits"]["network"] == "internal"
    assert detail["sandbox_meta"]["agent_image_id"].startswith("sha256:")

    fixed = live.post(
        f"{API}/targets",
        headers=auth_headers,
        json={
            "name": "refundbot-hardened",
            "adapter_type": "sandboxed_container",
            "config": {"image": "bbj-refundbot:0.1.0", "mode": "hardened"},
            "authorization": {"confirm_authorized": True, "statement": "bundled mock"},
        },
    ).json()["id"]
    resp = live.post(
        f"{API}/runs",
        headers={**auth_headers, "Idempotency-Key": uuid.uuid4().hex},
        json={"target_id": fixed, "contract_id": contract, "suite": "demo"},
    )
    run = _wait(live, auth_headers, resp.json()["id"])
    assert run["totals"]["NOT_REPRODUCED"] == 10 and run["totals"]["CONFIRMED"] == 0

    leftovers = docker.from_env().containers.list(all=True, filters={"label": LABEL})
    assert leftovers == []


def test_cancel_running_run_via_api(live: TestClient, auth_headers: dict[str, str]) -> None:
    target, contract = _ids(live, auth_headers)
    run_id = live.post(
        f"{API}/runs",
        headers={**auth_headers, "Idempotency-Key": uuid.uuid4().hex},
        json={"target_id": target, "contract_id": contract, "suite": "demo"},
    ).json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:  # wait until at least one execution finished
        if live.get(f"{API}/runs/{run_id}", headers=auth_headers).json()["totals"]["executed"] >= 1:
            break
        time.sleep(0.2)
    assert live.post(f"{API}/runs/{run_id}/cancel", headers=auth_headers).status_code == 202
    run = _wait(live, auth_headers, run_id)
    assert run["status"] == "cancelled"
    assert run["totals"]["INCONCLUSIVE"] >= 1 and run["totals"]["pending"] == 0
    exes = live.get(f"{API}/runs/{run_id}/executions?limit=100", headers=auth_headers).json()[
        "items"
    ]
    cancelled = [e for e in exes if e["status"] == "cancelled"]
    finished = [e for e in exes if e["status"] == "verified"]
    assert cancelled and all(
        e["verdict"] == "INCONCLUSIVE" and e["inconclusive_reason"] == "cancelled"
        for e in cancelled
    )
    assert finished and all(e["verdict"] in ("CONFIRMED", "NOT_REPRODUCED") for e in finished)
    assert docker.from_env().containers.list(all=True, filters={"label": LABEL}) == []

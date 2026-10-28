"""Record the web UI's demo fixture from a REAL sandboxed run.

Runs the demo suite against the vulnerable RefundBot through the API (real
Docker runner, throwaway SQLite DB), exports a regression test for every
CONFIRMED finding, switches the target to hardened mode, retests each one,
then records every read-only API response the UI uses into
``frontend/src/demo/fixture.json``. The UI labels all of it "DEMO DATA".

Usage: PYTHONPATH=src uv run python scripts/export_ui_demo_fixture.py
Requires Docker and `make images`. Synthetic data only; responses are the
API's own redacted output, and the throwaway token never appears in them.
"""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from blackbox_jury.api.app import create_app
from blackbox_jury.core.config import Settings
from blackbox_jury.db.seed import seed

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend/src/demo/fixture.json"
API = "/api/v1"
TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


class Recorder:
    def __init__(self, client: TestClient, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}
        self.responses: dict[str, Any] = {}
        self.files: dict[str, dict[str, str]] = {}

    def get(self, path: str, key: str | None = None) -> Any:
        r = self.client.get(f"{API}{path}", headers=self.headers)
        if r.status_code not in (200, 503):
            raise RuntimeError(f"GET {path} -> {r.status_code}")
        body = r.json()
        self.responses[key or path.split("?")[0]] = body
        return body

    def file(self, path: str, key: str) -> None:
        r = self.client.get(f"{API}{path}", headers=self.headers)
        r.raise_for_status()
        self.files[key] = {"type": r.headers["content-type"].split(";")[0], "text": r.text}

    def post(self, path: str, body: Any = None, idempotent: bool = False) -> Any:
        headers = dict(self.headers)
        if idempotent:
            headers["Idempotency-Key"] = uuid.uuid4().hex
        r = self.client.post(f"{API}{path}", headers=headers, json=body)
        r.raise_for_status()
        return r.json()

    def wait(self, run_id: str) -> None:
        while (
            self.client.get(f"{API}/runs/{run_id}", headers=self.headers).json()["status"]
            not in TERMINAL
        ):
            time.sleep(0.5)


def main() -> int:
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(  # type: ignore[call-arg]
            api_token=token,
            env="dev",
            database_url=f"sqlite:///{tmp}/bbj.db",  # type: ignore[arg-type]
            seed_path=ROOT / "seeds/synthetic_store.json",
            contracts_dir=ROOT / "contracts",
        )
        app = create_app(settings)
        with TestClient(app) as client:
            rec = Recorder(client, token)
            if rec.get("/ready")["status"] != "ready":
                print(f"sandbox not ready: {rec.responses['/ready']}", file=sys.stderr)
                return 1
            seed(app.state.db, settings)
            target = rec.get("/targets")["items"][0]["id"]
            contract = rec.get("/contracts")["items"][0]["id"]

            run = rec.post(
                "/runs",
                {"target_id": target, "contract_id": contract, "suite": "demo"},
                idempotent=True,
            )
            rec.wait(run["id"])
            findings = rec.get("/findings?limit=200")["items"]
            confirmed = [f for f in findings if f["verdict"] == "CONFIRMED"]
            tests = [rec.post(f"/findings/{f['id']}/regression-test") for f in confirmed]
            client.patch(
                f"{API}/targets/{target}",
                headers=rec.headers,
                json={"config": {"mode": "hardened"}},
            ).raise_for_status()
            for t in tests:
                retest = rec.post(
                    "/retests",
                    {"regression_test_id": t["id"], "target_id": target},
                    idempotent=True,
                )
                rec.wait(retest["id"])

            # Record everything the UI reads.
            for path in ("/ready", "/settings", "/targets", "/contracts", "/regression-tests"):
                rec.get(path)
            rec.get("/scenarios?include=body")
            rec.get("/findings?limit=200")
            rec.get("/audit-events?limit=200")
            for c in rec.responses["/contracts"]["items"]:
                rec.get(f"/contracts/{c['id']}")
            for f in rec.responses["/findings"]["items"]:
                rec.get(f"/findings/{f['id']}")
            for t in rec.responses["/regression-tests"]["items"]:
                rec.file(
                    f"/regression-tests/{t['id']}?download=true", f"/regression-tests/{t['id']}"
                )
            runs = rec.get("/runs?limit=100")["items"]
            for r in runs:
                rec.get(f"/runs/{r['id']}")
                rec.file(
                    f"/runs/{r['id']}/report?format=json", f"/runs/{r['id']}/report?format=json"
                )
                rec.file(
                    f"/runs/{r['id']}/report?format=html", f"/runs/{r['id']}/report?format=html"
                )
                for e in rec.get(f"/runs/{r['id']}/executions?limit=100")["items"]:
                    detail = rec.get(f"/executions/{e['id']}")
                    for a in detail["artifacts"]:
                        rec.get(f"/artifacts/{a['id']}")

    if token in json.dumps([rec.responses, rec.files]):
        print("refusing to write: token found in recorded output", file=sys.stderr)
        return 1
    fixture = {
        "format": "bbj-ui-demo/1",
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": "Recorded from a real Docker-sandboxed run against the synthetic RefundBot.",
        "responses": rec.responses,
        "files": rec.files,
    }
    OUT.write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rec.responses)} responses, {len(rec.files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

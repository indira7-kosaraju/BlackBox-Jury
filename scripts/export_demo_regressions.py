"""Regenerate regressions/*.json from a REAL sandboxed demo run.

Runs the demo suite against the vulnerable RefundBot through the API (real
Docker runner, throwaway SQLite DB), exports every CONFIRMED finding as a
``bbj-regression/1`` document, and writes ``regressions/<scenario_key>.json``.

Usage: PYTHONPATH=src uv run python scripts/export_demo_regressions.py
Requires Docker and `make images`. Synthetic data only.
"""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from blackbox_jury.api.app import create_app
from blackbox_jury.core.config import Settings
from blackbox_jury.db.seed import seed

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "regressions"
API = "/api/v1"


def main() -> int:
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(  # type: ignore[call-arg]
            api_token=token,
            env="test",
            database_url=f"sqlite:///{tmp}/bbj.db",  # type: ignore[arg-type]
            seed_path=ROOT / "seeds/synthetic_store.json",
            contracts_dir=ROOT / "contracts",
        )
        headers = {"Authorization": f"Bearer {token}"}
        app = create_app(settings)
        with TestClient(app) as client:
            ready = client.get(f"{API}/ready", headers=headers).json()
            if ready["status"] != "ready":
                print(f"sandbox not ready: {ready}", file=sys.stderr)
                return 1
            seed(app.state.db, settings)
            target = client.get(f"{API}/targets", headers=headers).json()["items"][0]["id"]
            contract = client.get(
                f"{API}/contracts?name=refund_support_v1", headers=headers
            ).json()["items"][0]["id"]
            run = client.post(
                f"{API}/runs",
                headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                json={"target_id": target, "contract_id": contract, "suite": "demo"},
            ).json()
            while client.get(f"{API}/runs/{run['id']}", headers=headers).json()["status"] not in (
                "completed",
                "failed",
                "cancelled",
                "timed_out",
            ):
                time.sleep(0.5)
            report = client.get(f"{API}/runs/{run['id']}/report", headers=headers).json()
            OUT.mkdir(exist_ok=True)
            for old in OUT.glob("*.json"):
                old.unlink()
            for finding in report["findings"]:
                if finding["verdict"] != "CONFIRMED":
                    continue
                body = client.post(
                    f"{API}/findings/{finding['id']}/regression-test", headers=headers
                ).json()["body"]
                key = finding["execution"]["scenario"]["key"]
                (OUT / f"{key}.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
                print(f"exported {key}: expects PASS for {sorted(body['expect']['checks'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

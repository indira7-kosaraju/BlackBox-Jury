"""Measure PRD §17 success metrics against the REAL Docker sandbox (QA, prompt 14).

Everything runs through the API (throwaway SQLite DB, real DockerSandboxRunner,
background worker). Writes docs/qa/qa_metrics.json. Synthetic data only.

Usage: PYTHONPATH=src uv run python scripts/qa_metrics.py   (needs Docker + `make images`)
"""

from __future__ import annotations

import json
import platform
import secrets
import sys
import tempfile
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from blackbox_jury.api.app import create_app
from blackbox_jury.core.config import Settings
from blackbox_jury.db.seed import seed
from blackbox_jury.scenarios.loader import load_library

ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1"
TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


def main() -> int:
    token = secrets.token_urlsafe(32)
    headers = {"Authorization": f"Bearer {token}"}
    library = {s.key: s for s in load_library()}
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(  # type: ignore[call-arg]
            api_token=token,  # type: ignore[arg-type]
            env="test",
            database_url=f"sqlite:///{tmp}/bbj.db",
            seed_path=ROOT / "seeds/synthetic_store.json",
            contracts_dir=ROOT / "contracts",
        )
        app = create_app(settings)
        with TestClient(app) as c:
            ready = c.get(f"{API}/ready", headers=headers).json()
            if ready["status"] != "ready":
                print(f"not ready: {ready}", file=sys.stderr)
                return 1

            def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
                resp = c.post(
                    f"{API}{path}",
                    json=body,
                    headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                )
                if resp.status_code not in (200, 201, 202):
                    raise RuntimeError(f"{path} failed: {resp.status_code} {resp.text[:300]}")
                out: dict[str, Any] = resp.json()
                return out

            def wait(run_id: str) -> dict[str, Any]:
                while True:
                    run: dict[str, Any] = c.get(f"{API}/runs/{run_id}", headers=headers).json()
                    if run["status"] in TERMINAL:
                        return run
                    time.sleep(0.5)

            def report(run_id: str) -> dict[str, Any]:
                out: dict[str, Any] = c.get(f"{API}/runs/{run_id}/report", headers=headers).json()
                return out

            loop_start = time.monotonic()
            seed(app.state.db, settings)
            target = c.get(f"{API}/targets", headers=headers).json()["items"][0]["id"]
            contract = c.get(f"{API}/contracts?name=refund_support_v1", headers=headers).json()[
                "items"
            ][0]["id"]

            vulnerable_runs = []
            for _ in range(3):
                t0 = time.monotonic()
                run = wait(
                    post("/runs", {"target_id": target, "contract_id": contract, "suite": "demo"})[
                        "id"
                    ]
                )
                vulnerable_runs.append((run, report(run["id"]), time.monotonic() - t0))

            first_report = vulnerable_runs[0][1]
            pi = next(
                f
                for f in first_report["findings"]
                if f["execution"]["scenario"]["key"] == "pi_order_note_refund"
            )
            rgt = c.post(f"{API}/findings/{pi['id']}/regression-test", headers=headers).json()["id"]
            c.patch(
                f"{API}/targets/{target}", headers=headers, json={"config": {"mode": "hardened"}}
            )
            retest = wait(post("/retests", {"regression_test_id": rgt, "target_id": target})["id"])
            retest_cmp = report(retest["id"])["retest_comparison"][0]
            loop_seconds = time.monotonic() - loop_start

            t0 = time.monotonic()
            hardened = wait(
                post("/runs", {"target_id": target, "contract_id": contract, "suite": "demo"})["id"]
            )
            hardened_report = report(hardened["id"])
            hardened_seconds = time.monotonic() - t0
            # Every check result of every hardened execution (not just findings).
            h_checks = [
                chk
                for e in hardened_report["executions"]
                for chk in c.get(f"{API}/executions/{e['execution_id']}", headers=headers).json()[
                    "checks"
                ]
            ]

    def per_scenario(rep: dict[str, Any]) -> dict[str, str]:
        return {e["scenario"]: e["verdict"] for e in rep["executions"]}

    def failed_checks(rep: dict[str, Any]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for f in rep["findings"]:
            out[f["execution"]["scenario"]["key"]] = set(f["failed_checks"])
        return out

    attacks = [k for k, s in library.items() if not s.is_control]
    v_verdicts = [per_scenario(r) for _, r, _ in vulnerable_runs]
    v_failed = failed_checks(first_report)
    targets_total = sum(len(library[k].targets) for k in attacks)
    targets_hit = sum(len(set(library[k].targets) & v_failed.get(k, set())) for k in attacks)

    h_security = [c for c in h_checks if c["check_id"] != "TASK_COMPLETION"]
    h_verdicts = per_scenario(hardened_report)

    def ratio(flags: Iterable[bool]) -> str:
        values = list(flags)
        return f"{sum(values)}/{len(values)}"

    metrics = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "synthetic_data": True,
            "target": "RefundBot (scripted)",
        },
        "counts": {
            "scenarios_in_library": len(library),
            "generated_by_llm": 0,
            "executed_per_run": first_report["totals"]["executed"],
            "runs_executed": len(vulnerable_runs) + 2,
        },
        "M1_loop_seconds_run_report_export_fix_retest": round(loop_seconds, 1),
        "M1_includes_runs": "3 vulnerable demo runs + 1 retest (a single run is ~seconds below)",
        "demo_run_seconds": [round(s, 1) for _, _, s in vulnerable_runs]
        + [round(hardened_seconds, 1)],
        "M2_vulnerable_attacks_confirmed": ratio(v_verdicts[0][k] == "CONFIRMED" for k in attacks),
        "M2_targeted_checks_detected": f"{targets_hit}/{targets_total}",
        "M2_hardened_attacks_confirmed": ratio(h_verdicts[k] == "CONFIRMED" for k in attacks),
        "M3_benign_completed_vulnerable": ratio(
            c["task_completed"] is True for c in first_report["benign_controls"]
        ),
        "M3_benign_completed_hardened": ratio(
            c["task_completed"] is True for c in hardened_report["benign_controls"]
        ),
        "M4_identical_verdicts_across_3_runs": all(v == v_verdicts[0] for v in v_verdicts),
        "false_positive_fails_on_hardened": ratio(c["outcome"] == "FAIL" for c in h_security),
        "errors_on_hardened": ratio(c["outcome"] == "ERROR" for c in h_checks),
        "inconclusive_total": sum(r["totals"]["INCONCLUSIVE"] for _, r, _ in vulnerable_runs)
        + hardened_report["totals"]["INCONCLUSIVE"],
        "retest": {
            "original": retest_cmp["original_verdict"],
            "retest": retest_cmp["retest_verdict"],
            "status": retest_cmp["regression_status"],
        },
        "M6_report_shows_counts_and_disclaimer": bool(
            first_report["totals"]["executed"] and first_report["disclaimer"]
        ),
    }
    out = ROOT / "docs" / "qa" / "qa_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

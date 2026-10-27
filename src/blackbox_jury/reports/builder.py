"""JSON run report (PRD FR26-FR28, DESIGN.md §5).

Every statement carries provenance: ``VERIFIED`` (deterministic check over
gateway evidence), ``AGENT_CLAIM`` (the target's own untrusted response) or
``MODEL_ANALYSIS`` (jury; Phase 7). Totals count executed scenarios only. The
report never says the target is secure.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from blackbox_jury.db import repo
from blackbox_jury.db.models import Execution, Finding, RegressionTest, Run, ScenarioRow
from blackbox_jury.orchestrator.state_machine import RUN_TERMINAL
from blackbox_jury.verifier.severity import SEVERITY_RANK

DISCLAIMER = (
    "Results reflect only the scenarios executed in this run. "
    "They do not establish that the target is secure."
)
LIMITATIONS = [
    "Synthetic data and a local mock environment only.",
    "Detection is limited to the deterministic checks enabled by the contract; "
    "see docs/LIMITATIONS.md.",
    "NOT_REPRODUCED means the behaviour was not observed in this run, not that it cannot occur.",
    "The target's final response is an untrusted claim; outcomes are judged from gateway evidence.",
]


_PK = {
    "customers": "customer_id",
    "orders": "order_id",
    "refunds": "refund_id",
    "approvals": "approval_id",
    "outbox": "message_id",
}


def state_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Row-level diff of two redacted snapshots: ``+`` added, ``-`` removed, ``~`` changed."""
    rows: list[dict[str, Any]] = []
    for table, pk in _PK.items():
        old = {r.get(pk): r for r in before.get(table, []) if isinstance(r, dict)}
        new = {r.get(pk): r for r in after.get(table, []) if isinstance(r, dict)}
        for key in sorted(set(old) | set(new), key=str):
            if key not in old:
                rows.append(
                    {"table": table, "key": key, "change": "+", "before": None, "after": new[key]}
                )
            elif key not in new:
                rows.append(
                    {"table": table, "key": key, "change": "-", "before": old[key], "after": None}
                )
            elif old[key] != new[key]:
                changed = {
                    k: new[key].get(k) for k in new[key] if old[key].get(k) != new[key].get(k)
                }
                rows.append(
                    {
                        "table": table,
                        "key": key,
                        "change": "~",
                        "before": {k: old[key].get(k) for k in changed},
                        "after": changed,
                    }
                )
    return rows


def _states(s: Session, execution_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    found: dict[str, dict[str, Any]] = {}
    for art in repo.artifacts(s, execution_id):
        if art.kind in ("state_before", "state_after") and not art.truncated:
            try:
                value = json.loads(art.content_redacted)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                found[art.kind] = value
    return found.get("state_before"), found.get("state_after")


def _claim(s: Session, execution_id: str) -> dict[str, Any] | None:
    for art in repo.artifacts(s, execution_id):
        if art.kind == "agent_final_response":
            return {
                "artifact_id": art.id,
                "provenance": "AGENT_CLAIM",
                "text": art.content_redacted,
            }
    return None


def _diff_or_none(s: Session, execution_id: str) -> list[dict[str, Any]] | None:
    before, after = _states(s, execution_id)
    return state_diff(before, after) if before is not None and after is not None else None


def execution_view(s: Session, exe: Execution, scenario: ScenarioRow) -> dict[str, Any]:
    checks = repo.check_results(s, exe.id)
    calls = repo.tool_calls(s, exe.id)
    return {
        "execution_id": exe.id,
        "scenario": {
            "key": scenario.key,
            "version": scenario.version,
            "category": scenario.category,
            "title": scenario.body.get("title"),
        },
        "status": exe.status,
        "verdict": exe.verdict,
        "inconclusive_reason": exe.inconclusive_reason,
        "severity": exe.severity,
        "task_completed": None if exe.task_completed is None else bool(exe.task_completed),
        "started_at": exe.started_at,
        "finished_at": exe.finished_at,
        "agent_claim": _claim(s, exe.id),
        "verified_outcome": {
            "provenance": "VERIFIED",
            "tool_calls": [
                {
                    "id": c.id,
                    "seq": c.seq,
                    "tool": c.tool,
                    "args": c.args_redacted,
                    "outcome": c.outcome,
                    "side_effect": bool(c.side_effect),
                }
                for c in calls
            ],
        },
        "checks": [
            {
                "check_id": c.check_id,
                "outcome": c.outcome,
                "provenance": "VERIFIED",
                "rule_ref": c.rule_ref,
                "evidence_refs": c.evidence_refs,
                "message": c.message,
            }
            for c in checks
        ],
        "state_diff": _diff_or_none(s, exe.id),
        "evidence_problems": exe.evidence_problems or [],
        "jury": [],  # Phase 7: MODEL_ANALYSIS, advisory only
    }


def build_report(s: Session, run: Run) -> dict[str, Any]:
    contract = repo.get_contract(s, run.project_id, run.contract_id)
    executions = repo.executions_for_run(s, run.id)
    scenarios = {e.id: s.get_one(ScenarioRow, e.scenario_id) for e in executions}
    totals = repo.totals(s, run.id)
    findings = []
    for f in repo.findings_for_run(s, run.id):
        exe = next(e for e in executions if e.id == f.execution_id)
        findings.append(
            {
                "id": f.id,
                "title": f.title,
                "category": f.category,
                "verdict": f.verdict,
                "severity": f.severity,
                "failed_checks": f.failed_check_ids,
                "reproduction_steps": f.repro_steps,
                "execution": execution_view(s, exe, scenarios[exe.id]),
            }
        )
    rank: dict[str, int] = {str(k): v for k, v in SEVERITY_RANK.items()}
    findings.sort(key=lambda x: (-rank.get(str(x["severity"]), 0), str(x["verdict"]), str(x["id"])))
    controls = [
        {
            "scenario": scenarios[e.id].key,
            "verdict": e.verdict,
            "task_completed": None if e.task_completed is None else bool(e.task_completed),
            "note": "Over-blocking / usability" if e.task_completed == 0 else None,
        }
        for e in executions
        if scenarios[e.id].category == "benign"
    ]
    notes: list[str] = ["Jury not run (no model provider configured)."]
    inconclusive = [e for e in executions if e.verdict == "INCONCLUSIVE"]
    if inconclusive:
        reasons = sorted({e.inconclusive_reason or "unknown" for e in inconclusive})
        notes.append(f"{len(inconclusive)} execution(s) inconclusive: {', '.join(reasons)}.")
    if totals["pending"]:
        notes.append(f"{totals['pending']} scenario(s) not executed.")
    return {
        "retest_comparison": _retest_comparison(s, run, executions)
        if run.kind == "retest"
        else None,
        "report_version": 1,
        "partial": run.status not in RUN_TERMINAL,
        "disclaimer": DISCLAIMER,
        "synthetic": True,
        "run": {
            "id": run.id,
            "kind": run.kind,
            "status": run.status,
            "status_reason": run.status_reason,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        },
        "target": run.target_config_snapshot,
        "contract": {
            "name": contract.name,
            "version": contract.version,
            "sha256": contract.body_sha256,
        },
        "totals": totals,
        "findings": findings,
        "executions": [
            {
                "execution_id": e.id,
                "scenario": scenarios[e.id].key,
                "category": scenarios[e.id].category,
                "status": e.status,
                "verdict": e.verdict,
                "inconclusive_reason": e.inconclusive_reason,
                "severity": e.severity,
            }
            for e in executions
        ],
        "benign_controls": controls,
        "limitations": LIMITATIONS + notes,
    }


def _retest_comparison(
    s: Session, run: Run, executions: Sequence[Execution]
) -> list[dict[str, Any]]:
    """Original CONFIRMED finding vs this retest (PRD FR30). Never says "fixed"/"secure"."""
    if run.regression_test_id is None:
        return []
    rgt = s.get_one(RegressionTest, run.regression_test_id)
    original = s.get_one(Finding, rgt.finding_id)
    expected: dict[str, str] = rgt.body["expect"]["checks"]
    out = []
    for exe in executions:
        actual = {c.check_id: c.outcome for c in repo.check_results(s, exe.id)}
        met = exe.verdict == "NOT_REPRODUCED" and all(
            actual.get(k) == v for k, v in expected.items()
        )
        status = (
            "not_reproduced"
            if met
            else "still_reproduces"
            if exe.verdict == "CONFIRMED"
            else "inconclusive"
        )
        out.append(
            {
                "regression_test_id": rgt.id,
                "original_finding_id": original.id,
                "original_run_id": original.run_id,
                "original_verdict": original.verdict,
                "original_severity": original.severity,
                "retest_execution_id": exe.id,
                "retest_verdict": exe.verdict,
                "expected_checks": expected,
                "actual_checks": {k: actual.get(k, "NOT_RUN") for k in expected},
                "regression_status": status,
            }
        )
    return out

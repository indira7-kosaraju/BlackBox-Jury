"""Read-only listings used by the web UI (API.md §8): findings, regression tests,
recent audit activity, and non-secret settings. Nothing here changes state.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request

from blackbox_jury.api.deps import DB, RATE_LIMITS, DefaultLimited
from blackbox_jury.db import repo
from blackbox_jury.sandbox.runner import SandboxLimits

router = APIRouter()

APP_VERSION = "0.1.0"


@router.get("/findings")
def list_findings(
    principal: DefaultLimited,
    db: DB,
    limit: int = Query(default=50, ge=1, le=200),
    verdict: Literal["CONFIRMED", "INCONCLUSIVE"] | None = None,
    severity: Literal["critical", "high", "medium", "low"] | None = None,
) -> dict[str, Any]:
    with db.session() as s:
        items = []
        for f, run, scenario in repo.list_findings(
            s, principal.project_id, limit, verdict, severity
        ):
            rgt = repo.regression_for_finding(s, f.id)
            items.append(
                {
                    "id": f.id,
                    "run_id": f.run_id,
                    "execution_id": f.execution_id,
                    "title": f.title,
                    "category": f.category,
                    "verdict": f.verdict,
                    "severity": f.severity,
                    "failed_checks": f.failed_check_ids,
                    "scenario_key": scenario.key,
                    "target": run.target_config_snapshot.get("name"),
                    "target_mode": run.target_config_snapshot.get("mode"),
                    "regression_test_id": rgt.id if rgt else None,
                    "created_at": f.created_at,
                }
            )
        return {"items": items}


@router.get("/regression-tests")
def list_regression_tests(principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        items = []
        for rgt in repo.list_regression_tests(s, principal.project_id):
            body = rgt.body
            history = []
            for run in repo.retest_runs(s, rgt.id):
                exes = repo.executions_for_run(s, run.id)
                verdict = exes[0].verdict if exes else None
                history.append(
                    {
                        "run_id": run.id,
                        "status": run.status,
                        "verdict": verdict,
                        "target_mode": run.target_config_snapshot.get("mode"),
                        "config_version": run.target_config_snapshot.get("config_version"),
                        "finished_at": run.finished_at,
                    }
                )
            items.append(
                {
                    "id": rgt.id,
                    "finding_id": rgt.finding_id,
                    "title": body.get("title"),
                    "scenario_key": body.get("scenario", {}).get("key"),
                    "original_verdict": body.get("created_from", {}).get("original_verdict"),
                    "original_severity": body.get("created_from", {}).get("original_severity"),
                    "original_run_id": body.get("created_from", {}).get("run_id"),
                    "expected_checks": body.get("expect", {}).get("checks", {}),
                    "body_sha256": rgt.body_sha256,
                    "created_at": rgt.created_at,
                    "retests": history,
                }
            )
        return {"items": items}


@router.get("/audit-events")
def list_audit_events(
    request: Request,
    principal: DefaultLimited,
    db: DB,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    redactor = request.app.state.service.redactor
    with db.session() as s:
        return {
            "items": [
                {
                    "id": e.id,
                    "action": e.action,
                    "actor": e.actor,
                    "entity_type": e.entity_type,
                    "entity_id": e.entity_id,
                    "details": redactor.redact(e.details),
                    "created_at": e.created_at,
                }
                for e in repo.list_audit_events(s, principal.project_id, limit)
            ]
        }


@router.get("/settings")
def get_settings(request: Request, _: DefaultLimited) -> dict[str, Any]:
    """Non-secret effective configuration. Never includes tokens or API keys."""
    settings = request.app.state.settings
    limits = SandboxLimits(exec_timeout_s=settings.exec_timeout_s)
    jury_on = settings.llm_provider != "none"
    return {
        "app": {"name": "Blackbox Jury", "version": APP_VERSION, "env": settings.env},
        "sandbox": {
            "adapter_types": ["sandboxed_container"],
            "gateway_image": settings.gateway_image,
            "allowed_target_images": list(settings.allowed_target_images),
            "network": "per-execution internal network (no egress)",
            "filesystem": "read-only root, noexec tmpfs",
            "user": "10001:10001 (non-root), all capabilities dropped, no-new-privileges",
            "mem_bytes": limits.mem_bytes,
            "cpus": limits.nano_cpus / 1_000_000_000,
            "pids_limit": limits.pids_limit,
            "exec_timeout_s": settings.exec_timeout_s,
            "run_timeout_s": settings.run_timeout_s,
        },
        "jury": {
            "enabled": jury_on,
            "provider": settings.llm_provider,
            "model": settings.llm_model if jury_on else None,
            "credentials_configured": settings.anthropic_api_key is not None if jury_on else None,
        },
        "limits": {"rate_limits_per_minute": RATE_LIMITS, "max_scenarios_per_run": 25},
        "retention": {
            "implemented": False,
            "note": "No automatic pruning yet (bbj prune is planned).",
        },
    }

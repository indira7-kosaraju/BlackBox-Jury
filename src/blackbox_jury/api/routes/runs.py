"""Runs, executions, evidence, findings and reports (API.md §6-7)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from blackbox_jury.api.deps import DB, DefaultLimited, Principal, RunCreateLimited
from blackbox_jury.api.errors import ApiError
from blackbox_jury.api.schemas import IdempotencyKey, RunCreate
from blackbox_jury.core.logging import request_id_var
from blackbox_jury.db import repo
from blackbox_jury.db.models import Contract, Run, ScenarioRow, Target
from blackbox_jury.db.session import Database
from blackbox_jury.evidence.collector import canonical_json, sha256_hex
from blackbox_jury.reports.builder import build_report, execution_view
from blackbox_jury.scenarios.loader import SUITES, ScenarioError, validate_against

router = APIRouter()
_create_lock = threading.Lock()  # single process: serialise the active-run check + insert


def run_out(s: Any, run: Run) -> dict[str, Any]:
    totals = repo.totals(s, run.id)
    return {
        "id": run.id,
        "status": run.status,
        "status_reason": run.status_reason,
        "kind": run.kind,
        "target_id": run.target_id,
        "contract_id": run.contract_id,
        "target_config_snapshot": run.target_config_snapshot,
        "scenario_count": totals["selected"],
        "cancel_requested": bool(run.cancel_requested),
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "totals": totals,
        "jury": "not_run",
        "links": {"self": f"/api/v1/runs/{run.id}", "report": f"/api/v1/runs/{run.id}/report"},
    }


Resolver = Callable[[Session], tuple[Target, Contract, list[ScenarioRow], str, str | None]]


def start_run(
    *,
    request: Request,
    principal: Principal,
    db: Database,
    idempotency_key: str,
    request_sha: str,
    resolve: Resolver,
) -> JSONResponse:
    """Shared, guarded run creation for standard runs and retests.

    Order: idempotency -> target authorized + image allowlisted -> scenarios valid
    against contract/seed -> one active run -> Docker ready -> insert + audit -> enqueue.
    """
    state = request.app.state
    with _create_lock:
        with db.session() as s:
            existing = repo.find_run_by_key(s, principal.project_id, idempotency_key)
            if existing is not None:
                if existing.request_sha256 != request_sha:
                    raise ApiError(
                        409,
                        "idempotency_key_reuse",
                        "Idempotency-Key was used with a different request.",
                    )
                return JSONResponse(status_code=200, content=run_out(s, existing))
            target, contract_row, scenario_rows, kind, regression_test_id = resolve(s)
            if target.authorization_status != "authorized":
                raise ApiError(
                    422, "target_not_authorized", "Target is not authorized for testing."
                )
            if target.config.get("image") not in state.settings.allowed_target_images:
                raise ApiError(
                    422, "image_not_allowed", "Target image is no longer on the allowlist."
                )
            if not 1 <= len(scenario_rows) <= 25:
                raise ApiError(422, "invalid_request", "A run needs between 1 and 25 scenarios.")
            contract = repo.contract_model(contract_row)
            for row in scenario_rows:
                try:
                    validate_against(repo.scenario_model(row), contract, state.base_seed)
                except ScenarioError as exc:
                    raise ApiError(422, "scenario_invalid", str(exc)[:300]) from None
            if repo.active_run(s, principal.project_id) is not None:
                raise ApiError(409, "run_already_active", "Another run is still active.")
        readiness = state.runner.readiness()
        if readiness != {"docker": "ok", "images": "ok"}:
            raise ApiError(
                503,
                "sandbox_unavailable",
                "The Docker sandbox is not ready; no tests were executed.",
                {"checks": readiness},
            )
        with db.session() as s:
            run = repo.create_run(
                s,
                project=principal.project_id,
                target=target,
                contract=contract_row,
                scenario_rows=scenario_rows,
                idempotency_key=idempotency_key,
                request_sha256=request_sha,
                actor=principal.actor,
                kind=kind,
                regression_test_id=regression_test_id,
            )
            repo.audit(
                s,
                project=principal.project_id,
                actor=principal.actor,
                action="retest.create" if kind == "retest" else "run.create",
                request_id=request_id_var.get() or "",
                entity_type="run",
                entity_id=run.id,
                details={
                    "target": target.name,
                    "mode": target.config.get("mode"),
                    "contract": f"{contract_row.name}@v{contract_row.version}",
                    "scenarios": len(scenario_rows),
                    "regression_test_id": regression_test_id,
                },
            )
            content = run_out(s, run)
    state.worker.submit(run.id)
    return JSONResponse(status_code=202, content=content)


@router.post("/runs", status_code=202)
def create_run(
    body: RunCreate,
    request: Request,
    principal: RunCreateLimited,
    db: DB,
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
) -> JSONResponse:
    def resolve(s: Session) -> tuple[Target, Contract, list[ScenarioRow], str, str | None]:
        target = repo.get_target(s, principal.project_id, body.target_id)
        contract_row = repo.get_contract(s, principal.project_id, body.contract_id)
        if body.suite == "demo":
            latest: dict[str, ScenarioRow] = {}
            for r in repo.list_scenarios(s, principal.project_id):
                if r.source == "curated" and (
                    r.key not in latest or r.version > latest[r.key].version
                ):
                    latest[r.key] = r
            rows = [latest[k] for k in sorted(latest)]
        elif body.suite is not None:
            rows = repo.latest_scenarios_by_keys(s, principal.project_id, SUITES[body.suite])
        else:
            rows = repo.scenarios_by_ids(s, principal.project_id, list(body.scenario_ids or ()))
        return target, contract_row, rows, "standard", None

    return start_run(
        request=request,
        principal=principal,
        db=db,
        idempotency_key=idempotency_key,
        request_sha=sha256_hex(canonical_json(body.model_dump(mode="json"))),
        resolve=resolve,
    )


@router.get("/runs")
def list_runs(
    principal: DefaultLimited,
    db: DB,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    with db.session() as s:
        try:
            rows, next_cursor = repo.list_runs(s, principal.project_id, limit, cursor)
        except ValueError:
            raise ApiError(400, "invalid_request", "Invalid cursor.") from None
        return {"items": [run_out(s, r) for r in rows], "next_cursor": next_cursor}


@router.get("/runs/{run_id}")
def get_run(run_id: str, principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        return run_out(s, repo.get_run(s, principal.project_id, run_id))


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str, request: Request, principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        repo.request_cancel(s, principal.project_id, run_id)
        repo.audit(
            s,
            project=principal.project_id,
            actor=principal.actor,
            action="run.cancel",
            request_id=request_id_var.get() or "",
            entity_type="run",
            entity_id=run_id,
        )
    request.app.state.service.signal_cancel(run_id)
    return {"id": run_id, "cancel_requested": True}


@router.get("/runs/{run_id}/executions")
def list_executions(
    run_id: str,
    principal: DefaultLimited,
    db: DB,
    verdict: Literal["CONFIRMED", "NOT_REPRODUCED", "INCONCLUSIVE"] | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    with db.session() as s:
        repo.get_run(s, principal.project_id, run_id)
        rows = [
            e for e in repo.executions_for_run(s, run_id) if verdict is None or e.verdict == verdict
        ]
        if cursor:
            try:
                after = repo.decode_cursor(cursor)
            except ValueError:
                raise ApiError(400, "invalid_request", "Invalid cursor.") from None
            rows = [e for e in rows if e.id > after]
        page, more = rows[:limit], len(rows) > limit
        items = []
        for e in page:
            sc = s.get_one(ScenarioRow, e.scenario_id)
            meta = e.sandbox_meta or {}
            items.append(
                {
                    "id": e.id,
                    "scenario_key": sc.key,
                    "category": sc.category,
                    "status": e.status,
                    "verdict": e.verdict,
                    "inconclusive_reason": e.inconclusive_reason,
                    "severity": e.severity,
                    "task_completed": None if e.task_completed is None else bool(e.task_completed),
                    "duration_ms": meta.get("duration_ms"),
                }
            )
        return {"items": items, "next_cursor": repo.encode_cursor(page[-1].id) if more else None}


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str, principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        exe, _run = repo.get_execution(s, principal.project_id, execution_id)
        view = execution_view(s, exe, s.get_one(ScenarioRow, exe.scenario_id))
        meta = exe.sandbox_meta or {}
        view["artifacts"] = [
            {"id": a.id, "kind": a.kind, "sha256": a.sha256, "truncated": bool(a.truncated)}
            for a in repo.artifacts(s, exe.id)
        ]
        view["sandbox_meta"] = {
            k: meta.get(k)
            for k in (
                "duration_ms",
                "agent_exit_code",
                "cleanup_ok",
                "outcome",
                "limits",
                "agent_image",
                "agent_image_id",
            )
        }
        return view


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        a = repo.get_artifact(s, principal.project_id, artifact_id)
        return {
            "id": a.id,
            "kind": a.kind,
            "content_redacted": a.content_redacted,
            "sha256": a.sha256,
            "truncated": bool(a.truncated),
        }


@router.get("/findings/{finding_id}")
def get_finding(finding_id: str, principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        f = repo.get_finding(s, principal.project_id, finding_id)
        return {
            "id": f.id,
            "run_id": f.run_id,
            "execution_id": f.execution_id,
            "title": f.title,
            "category": f.category,
            "verdict": f.verdict,
            "severity": f.severity,
            "failed_checks": f.failed_check_ids,
            "reproduction_steps": f.repro_steps,
            "links": {"execution": f"/api/v1/executions/{f.execution_id}"},
        }


@router.get("/runs/{run_id}/report")
def get_report(
    run_id: str,
    principal: DefaultLimited,
    db: DB,
    format: Literal["json"] = Query(default="json"),  # HTML report arrives in Phase 9
) -> dict[str, Any]:
    with db.session() as s:
        return build_report(s, repo.get_run(s, principal.project_id, run_id))

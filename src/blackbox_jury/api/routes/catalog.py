"""Targets, contracts and scenarios (API.md §3-5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from blackbox_jury.api.deps import DB, DefaultLimited
from blackbox_jury.api.errors import ApiError
from blackbox_jury.api.schemas import TargetCreate
from blackbox_jury.contracts.loader import ContractError, check_principal_exists, parse_contract
from blackbox_jury.core.logging import request_id_var
from blackbox_jury.db import repo
from blackbox_jury.db.models import Contract, ScenarioRow, Target

router = APIRouter()


def target_out(t: Target) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "adapter_type": t.adapter_type,
        "config": t.config,
        "config_version": t.config_version,
        "is_local": bool(t.is_local),
        "authorization_status": t.authorization_status,
        "authorized_by": t.authorized_by,
        "authorized_at": t.authorized_at,
    }


def contract_out(c: Contract, include_body: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": c.id,
        "name": c.name,
        "version": c.version,
        "body_sha256": c.body_sha256,
    }
    if include_body:
        out["body"] = c.body
    return out


def scenario_out(r: ScenarioRow, include_body: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id,
        "key": r.key,
        "version": r.version,
        "category": r.category,
        "source": r.source,
        "title": r.body.get("title"),
        "body_sha256": r.body_sha256,
    }
    if include_body:
        out["body"] = r.body
    return out


# -- targets ---------------------------------------------------------------------------------


@router.post("/targets", status_code=201)
def create_target(
    body: TargetCreate, request: Request, principal: DefaultLimited, db: DB
) -> dict[str, Any]:
    settings = request.app.state.settings
    if not body.authorization.confirm_authorized:
        raise ApiError(
            422,
            "authorization_required",
            "You must confirm you are authorized to test this target.",
        )
    if body.config.image not in settings.allowed_target_images:
        raise ApiError(422, "image_not_allowed", "Target image is not on the allowlist.")
    if body.config.mode == "llm":
        raise ApiError(
            422, "provider_not_configured", "LLM mode requires a configured model provider."
        )
    with db.session() as s:
        target = repo.create_target(
            s,
            project=principal.project_id,
            name=body.name,
            image=body.config.image,
            mode=body.config.mode,
            actor=principal.actor,
        )
        rid = request_id_var.get() or ""
        details = {"name": body.name, "image": body.config.image, "mode": body.config.mode}
        repo.audit(
            s,
            project=principal.project_id,
            actor=principal.actor,
            action="target.create",
            request_id=rid,
            entity_type="target",
            entity_id=target.id,
            details=details,
        )
        repo.audit(
            s,
            project=principal.project_id,
            actor=principal.actor,
            action="target.authorize",
            request_id=rid,
            entity_type="target",
            entity_id=target.id,
            details={"statement": body.authorization.statement[:500]},
        )
        return target_out(target)


@router.get("/targets")
def list_targets(principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        return {"items": [target_out(t) for t in repo.list_targets(s, principal.project_id)]}


@router.get("/targets/{target_id}")
def get_target(target_id: str, principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        return target_out(repo.get_target(s, principal.project_id, target_id))


# -- contracts --------------------------------------------------------------------------------


def _parse(request: Request, payload: dict[str, Any]) -> Any:
    try:
        contract = parse_contract(payload)
        customers = [c.get("customer_id") for c in request.app.state.base_seed.get("customers", [])]
        check_principal_exists(contract, customers)
    except ContractError as exc:
        raise ApiError(
            422, "validation_error", "Contract validation failed.", {"fields": exc.errors}
        ) from None
    return contract


@router.post("/contracts/validate")
def validate_contract(
    payload: dict[str, Any], request: Request, _: DefaultLimited
) -> dict[str, Any]:
    _parse(request, payload)
    return {"valid": True, "warnings": []}


@router.post("/contracts", status_code=201)
def create_contract(
    payload: dict[str, Any], request: Request, principal: DefaultLimited, db: DB
) -> JSONResponse:
    contract = _parse(request, payload)
    with db.session() as s:
        row, created = repo.upsert_contract(s, principal.project_id, contract)
        if created:
            repo.audit(
                s,
                project=principal.project_id,
                actor=principal.actor,
                action="contract.create",
                request_id=request_id_var.get() or "",
                entity_type="contract",
                entity_id=row.id,
                details={"name": row.name, "version": row.version},
            )
        return JSONResponse(
            status_code=201 if created else 200, content=contract_out(row, include_body=False)
        )


@router.get("/contracts")
def list_contracts(
    principal: DefaultLimited, db: DB, name: str | None = Query(default=None, max_length=64)
) -> dict[str, Any]:
    with db.session() as s:
        if name:
            row = repo.find_contract(s, principal.project_id, name)
            return {"items": [contract_out(row, include_body=False)] if row else []}
        rows = s.scalars(
            select(Contract)
            .where(Contract.project_id == principal.project_id)
            .order_by(Contract.name)
        ).all()
        return {"items": [contract_out(r, include_body=False) for r in rows]}


@router.get("/contracts/{contract_id}")
def get_contract(contract_id: str, principal: DefaultLimited, db: DB) -> dict[str, Any]:
    with db.session() as s:
        return contract_out(repo.get_contract(s, principal.project_id, contract_id))


# -- scenarios ---------------------------------------------------------------------------------


@router.get("/scenarios")
def list_scenarios(
    principal: DefaultLimited,
    db: DB,
    category: str | None = Query(default=None, max_length=32),
    include: str | None = Query(default=None, pattern=r"^body$"),
) -> dict[str, Any]:
    with db.session() as s:
        rows = repo.list_scenarios(s, principal.project_id, category)
        return {"items": [scenario_out(r, include == "body") for r in rows]}

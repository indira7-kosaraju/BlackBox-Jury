"""Fix -> export -> retest loop (API.md §3 PATCH, §7)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.orm import Session

from blackbox_jury.api.deps import DB, DefaultLimited, RunCreateLimited
from blackbox_jury.api.errors import ApiError
from blackbox_jury.api.routes.catalog import target_out
from blackbox_jury.api.routes.runs import start_run
from blackbox_jury.api.schemas import EntityId, IdempotencyKey
from blackbox_jury.core.ids import new_id
from blackbox_jury.core.logging import request_id_var
from blackbox_jury.db import repo
from blackbox_jury.db.models import Contract, Execution, Run, ScenarioRow, Target
from blackbox_jury.evidence.collector import canonical_json, sha256_hex
from blackbox_jury.regression.export import RegressionDocumentError, build_document, verify_document

router = APIRouter()


class TargetConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["vulnerable", "hardened"]


class TargetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    config: TargetConfigPatch | None = None
    authorization_status: Literal["revoked"] | None = None

    @model_validator(mode="after")
    def _something(self) -> TargetPatch:
        if self.config is None and self.authorization_status is None:
            raise ValueError("nothing to update")
        return self


class RetestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    regression_test_id: EntityId
    target_id: EntityId


def regression_out(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "finding_id": row.finding_id,
        "body_sha256": row.body_sha256,
        "body": row.body,
    }


@router.patch("/targets/{target_id}")
def patch_target(
    target_id: str, body: TargetPatch, principal: DefaultLimited, db: DB
) -> dict[str, Any]:
    with db.session() as s:
        target, before = repo.update_target(
            s,
            principal.project_id,
            target_id,
            mode=body.config.mode if body.config else None,
            revoke=body.authorization_status == "revoked",
        )
        after = {
            "mode": target.config.get("mode"),
            "authorization_status": target.authorization_status,
            "config_version": target.config_version,
        }
        repo.audit(
            s,
            project=principal.project_id,
            actor=principal.actor,
            action="target.update",
            request_id=request_id_var.get() or "",
            entity_type="target",
            entity_id=target.id,
            details={"before": before, "after": after},
        )
        return target_out(target)


@router.post("/findings/{finding_id}/regression-test", status_code=201)
def export_regression(
    finding_id: str, request: Request, principal: DefaultLimited, db: DB
) -> JSONResponse:
    with db.session() as s:
        finding = repo.get_finding(s, principal.project_id, finding_id)
        existing = repo.regression_for_finding(s, finding.id)
        if existing is not None:
            return JSONResponse(status_code=200, content=regression_out(existing))
        if finding.verdict != "CONFIRMED" or finding.severity is None:
            raise ApiError(422, "finding_not_confirmed", "Only CONFIRMED findings can be exported.")
        exe = s.get_one(Execution, finding.execution_id)
        run = s.get_one(Run, finding.run_id)
        scenario = s.get_one(ScenarioRow, exe.scenario_id)
        contract = s.get_one(Contract, run.contract_id)
        rgt_id = new_id("rgt")
        body = build_document(
            regression_test_id=rgt_id,
            title=finding.title,
            finding_id=finding.id,
            run_id=run.id,
            execution_id=exe.id,
            severity=finding.severity,
            failed_checks=list(finding.failed_check_ids),
            scenario_body=scenario.body,
            base_seed=request.app.state.base_seed,
            contract_name=contract.name,
            contract_version=contract.version,
            contract_sha256=contract.body_sha256,
            target_snapshot=run.target_config_snapshot,
        )
        row = repo.create_regression_test(
            s,
            project=principal.project_id,
            regression_test_id=rgt_id,
            finding_id=finding.id,
            body=body,
        )
        repo.audit(
            s,
            project=principal.project_id,
            actor=principal.actor,
            action="regression.export",
            request_id=request_id_var.get() or "",
            entity_type="regression_test",
            entity_id=row.id,
            details={"finding_id": finding.id},
        )
        return JSONResponse(status_code=201, content=regression_out(row))


@router.get("/regression-tests/{regression_test_id}")
def get_regression(
    regression_test_id: str,
    principal: DefaultLimited,
    db: DB,
    download: bool = Query(default=False),
) -> JSONResponse:
    with db.session() as s:
        row = repo.get_regression_test(s, principal.project_id, regression_test_id)
        if download:
            return JSONResponse(
                content=row.body,
                headers={
                    "Content-Disposition": f'attachment; filename="{row.id}.json"',
                },
            )
        return JSONResponse(content=regression_out(row))


@router.post("/retests", status_code=202)
def create_retest(
    body: RetestCreate,
    request: Request,
    principal: RunCreateLimited,
    db: DB,
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
) -> JSONResponse:
    def resolve(s: Session) -> tuple[Target, Contract, list[ScenarioRow], str, str | None]:
        row = repo.get_regression_test(s, principal.project_id, body.regression_test_id)
        try:
            doc, _scenario = verify_document(row.body, request.app.state.base_seed)
        except RegressionDocumentError as exc:
            raise ApiError(422, "regression_invalid", str(exc)) from None
        if sha256_hex(canonical_json(row.body)) != row.body_sha256:
            raise ApiError(422, "regression_invalid", "stored regression test was altered")
        contract = repo.find_contract_version(
            s, principal.project_id, doc.contract.name, doc.contract.version
        )
        if contract is None or contract.body_sha256 != doc.contract.sha256:
            raise ApiError(
                422, "regression_invalid", "pinned contract version is missing or changed"
            )
        exe = s.get_one(Execution, doc.created_from.execution_id)
        scenario_row = s.get_one(ScenarioRow, exe.scenario_id)
        if scenario_row.body_sha256 != doc.scenario_sha256:
            raise ApiError(422, "regression_invalid", "pinned scenario version changed")
        target = repo.get_target(s, principal.project_id, body.target_id)
        return target, contract, [scenario_row], "retest", row.id

    return start_run(
        request=request,
        principal=principal,
        db=db,
        idempotency_key=idempotency_key,
        request_sha=sha256_hex(canonical_json(body.model_dump(mode="json"))),
        resolve=resolve,
    )

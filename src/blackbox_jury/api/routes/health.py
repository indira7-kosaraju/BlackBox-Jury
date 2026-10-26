"""Liveness and readiness endpoints (API.md §2)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from blackbox_jury.api.deps import AuthPrincipal

CheckStatus = Literal["ok", "unreachable", "missing", "not_configured", "not_checked"]

public_router = APIRouter()
router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckStatus]


@public_router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness only. Unauthenticated."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadyResponse, responses={503: {"model": ReadyResponse}})
def ready(_: AuthPrincipal) -> JSONResponse:
    """Readiness. Reports ready only when every dependency check is ``ok``.

    Phase 1: the database (phase 6) and Docker/image checks (phase 5) are not
    implemented yet, so this honestly reports ``not_ready``.
    """
    checks: dict[str, CheckStatus] = {
        "database": "not_configured",
        "docker": "not_checked",
        "images": "not_checked",
    }
    is_ready = all(v == "ok" for v in checks.values())
    body = ReadyResponse(status="ready" if is_ready else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if is_ready else 503, content=body.model_dump())

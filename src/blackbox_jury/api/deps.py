"""Shared request dependencies: server-side auth, project scoping, rate limits."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from blackbox_jury.api.errors import ApiError
from blackbox_jury.core.config import Settings
from blackbox_jury.core.logging import request_id_var
from blackbox_jury.core.ratelimit import RateLimiter
from blackbox_jury.db import repo
from blackbox_jury.db.session import Database

logger = logging.getLogger(__name__)

DEFAULT_PROJECT = "default"

# API.md §1 rate limits (per token, per minute).
# "default" covers status polling by the CLI (~2 requests/s during a run).
RATE_LIMITS = {"run_create": 10, "generation": 5, "default": 600}

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. MVP: one token -> one actor -> the default project."""

    actor: str
    project: str  # project name
    project_id: str


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _audit_denied(request: Request) -> None:
    db: Database | None = getattr(request.app.state, "db", None)
    limiter: RateLimiter = request.app.state.limiter
    client = request.client.host if request.client else "unknown"
    if db is None or limiter.hit(f"auth_denied_audit:{client}", 1) is not None:
        return  # at most one audit row per client per minute
    try:
        with db.session() as s:
            repo.audit(
                s,
                project=repo.project_id(s, DEFAULT_PROJECT),
                actor="anonymous",
                action="auth.denied",
                request_id=request_id_var.get() or "",
                details={"path": request.url.path[:200], "client": client},
            )
    except Exception:
        logger.exception("could not write auth.denied audit", extra={"component": "api"})


def require_auth(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Validate the bearer token in constant time; raise 401 otherwise."""
    expected = settings.api_token.get_secret_value().encode()
    supplied = credentials.credentials.encode() if credentials else b""
    if not credentials or not hmac.compare_digest(supplied, expected):
        logger.warning(
            "auth.denied",
            extra={
                "component": "api",
                "path": request.url.path,
                "client": request.client.host if request.client else None,
            },
        )
        _audit_denied(request)
        raise ApiError(
            401,
            "unauthorized",
            "Missing or invalid API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    project_id = getattr(request.app.state, "project_id", "")
    return Principal(actor=settings.api_actor, project=DEFAULT_PROJECT, project_id=project_id)


AuthPrincipal = Annotated[Principal, Depends(require_auth)]


def rate_limit(group: str) -> Callable[[Request, Principal], Principal]:
    def _dep(request: Request, principal: AuthPrincipal) -> Principal:
        limiter: RateLimiter = request.app.state.limiter
        retry = limiter.hit(f"{group}:{principal.actor}", RATE_LIMITS[group])
        if retry is not None:
            raise ApiError(
                429,
                "rate_limited",
                "Too many requests.",
                headers={"Retry-After": str(int(retry) + 1)},
            )
        return principal

    return _dep


DefaultLimited = Annotated[Principal, Depends(rate_limit("default"))]
RunCreateLimited = Annotated[Principal, Depends(rate_limit("run_create"))]
DB = Annotated[Database, Depends(get_db)]

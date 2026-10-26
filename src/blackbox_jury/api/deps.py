"""Shared request dependencies: server-side authentication and settings access."""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from blackbox_jury.api.errors import ApiError
from blackbox_jury.core.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_PROJECT = "default"

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. MVP: one token → one actor → the default project."""

    actor: str
    project: str


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def require_auth(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Validate the bearer token in constant time; raise 401 otherwise."""
    expected = settings.api_token.get_secret_value().encode()
    supplied = credentials.credentials.encode() if credentials else b""
    if not credentials or not hmac.compare_digest(supplied, expected):
        # TODO(phase 6): persist as audit_events row `auth.denied` (DATABASE.md §3.14).
        logger.warning(
            "auth.denied",
            extra={
                "component": "api",
                "path": request.url.path,
                "client": request.client.host if request.client else None,
            },
        )
        raise ApiError(
            401,
            "unauthorized",
            "Missing or invalid API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Principal(actor=settings.api_actor, project=DEFAULT_PROJECT)


AuthPrincipal = Annotated[Principal, Depends(require_auth)]

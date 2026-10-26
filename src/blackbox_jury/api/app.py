"""FastAPI application factory.

Run with: ``uvicorn --factory blackbox_jury.api.app:create_app``. A factory
(rather than a module-level app) keeps settings validation out of import time.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from blackbox_jury.api.errors import error_response, install_error_handlers
from blackbox_jury.api.routes import health
from blackbox_jury.core.config import Settings, get_settings
from blackbox_jury.core.ids import new_id
from blackbox_jury.core.logging import configure_logging, request_id_var
from blackbox_jury.core.redaction import Redactor

API_PREFIX = "/api/v1"

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, Redactor(settings.secret_values()))

    docs_enabled = settings.env == "dev"
    app = FastAPI(
        title="Blackbox Jury",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = new_id("req")
        token = request_id_var.set(request_id)
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Caught here (inside the request context) so the 500 body and log
                # line carry the request id. Details go to the redacted log only.
                logger.exception("unhandled exception", extra={"component": "api"})
                response = error_response(500, "internal_error", "An internal error occurred.")
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    install_error_handlers(app)
    app.include_router(health.public_router)
    app.include_router(health.router, prefix=API_PREFIX)
    return app

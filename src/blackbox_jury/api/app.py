"""FastAPI application factory.

Run with: ``uvicorn --factory blackbox_jury.api.app:create_app``. A factory
(rather than a module-level app) keeps settings validation out of import time.

Startup (lifespan): apply migrations, ensure the default project, recover
interrupted runs (and sweep orphaned sandbox resources), start the worker.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from blackbox_jury.api.errors import ApiError, error_response, install_error_handlers
from blackbox_jury.api.routes import catalog, health, regression, runs
from blackbox_jury.core.config import Settings, get_settings
from blackbox_jury.core.ids import new_id
from blackbox_jury.core.logging import configure_logging, request_id_var
from blackbox_jury.core.ratelimit import RateLimiter
from blackbox_jury.core.redaction import Redactor
from blackbox_jury.db import repo
from blackbox_jury.db.session import Database, upgrade
from blackbox_jury.jury.provider import LLMProvider, make_provider
from blackbox_jury.orchestrator.run_service import RunService, Worker
from blackbox_jury.sandbox.docker_runner import DockerSandboxRunner
from blackbox_jury.sandbox.runner import SandboxRunner
from blackbox_jury.scenarios.loader import load_base_seed

API_PREFIX = "/api/v1"
MAX_BODY_BYTES = 256 * 1024

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    runner: SandboxRunner | None = None,
    inline_worker: bool = False,
    provider: LLMProvider | None = None,
) -> FastAPI:
    """``runner`` / ``inline_worker`` / ``provider`` are injection points for tests only."""
    settings = settings or get_settings()
    redactor = Redactor(settings.secret_values())
    configure_logging(settings.log_level, redactor)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        upgrade(settings.database_url)
        db = Database(settings.database_url)
        with db.session() as s:
            app.state.project_id = repo.ensure_project(s, "default").id
        app.state.db = db
        app.state.base_seed = load_base_seed(settings.seed_path)
        llm = provider or make_provider(
            settings.llm_provider,
            settings.llm_model,
            settings.llm_timeout_s,
            settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None,
        )
        service = RunService(db, app.state.runner, settings, redactor, app.state.base_seed, llm)
        worker = Worker(service, inline=inline_worker)
        app.state.service, app.state.worker = service, worker
        for run_id in service.recover():
            worker.submit(run_id)
        worker.start()
        try:
            yield
        finally:
            worker.stop()
            db.engine.dispose()

    docs_enabled = settings.env == "dev"
    app = FastAPI(
        title="Blackbox Jury",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.limiter = RateLimiter()
    # Lazy: no Docker call happens until an execution or readiness check needs it.
    app.state.runner = runner or DockerSandboxRunner(
        gateway_image=settings.gateway_image,
        allowed_target_images=settings.allowed_target_images,
        docker_timeout_s=settings.docker_timeout_s,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = new_id("req")
        token = request_id_var.set(request_id)
        try:
            response = _body_guard(request)
            if response is None:
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

    async def _not_found(_: Request, exc: Exception) -> Response:
        return error_response(404, "not_found", "Resource not found.")

    async def _conflict(_: Request, exc: Exception) -> Response:
        assert isinstance(exc, repo.ConflictError)  # noqa: S101
        return error_response(409, exc.code, str(exc))

    app.add_exception_handler(repo.NotFoundError, _not_found)
    app.add_exception_handler(repo.ConflictError, _conflict)
    app.include_router(health.public_router)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(catalog.router, prefix=API_PREFIX)
    app.include_router(runs.router, prefix=API_PREFIX)
    app.include_router(regression.router, prefix=API_PREFIX)
    return app


def _body_guard(request: Request) -> Response | None:
    """Reject oversized bodies before reading them (API.md §1: max 256 KB)."""
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    length = request.headers.get("content-length")
    if length is None:
        if request.headers.get("transfer-encoding"):
            return error_response(411, "length_required", "Content-Length is required.")
        return None
    try:
        size = int(length)
    except ValueError:
        return error_response(400, "invalid_request", "Invalid Content-Length.")
    if size > MAX_BODY_BYTES:
        return error_response(413, "payload_too_large", "Request body too large.")
    return None


__all__ = ["API_PREFIX", "ApiError", "create_app"]

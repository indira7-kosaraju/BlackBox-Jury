"""Serve the built web UI (frontend/dist) at /ui.

The UI shell is public (the user signs in with the API token inside the app);
every data call still goes through the authenticated /api/v1 routes. Files are
only served from inside the dist directory, with a strict same-origin CSP.
Unknown paths under /ui fall back to index.html (client-side routing).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from blackbox_jury.api.errors import error_response

router = APIRouter(include_in_schema=False)

UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def _dist(request: Request) -> Path | None:
    dist = Path(request.app.state.settings.ui_dist_dir).resolve()
    return dist if (dist / "index.html").is_file() else None


def _file(path: Path, *, cache: bool) -> Response:
    response = FileResponse(path)
    response.headers["Content-Security-Policy"] = UI_CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if cache:  # Vite emits content-hashed asset names
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@router.get("/")
def root(request: Request) -> Response:
    if _dist(request) is None:
        return error_response(404, "not_found", "Web UI not built. Run `make ui`, or use the CLI.")
    return RedirectResponse("/ui/")


@router.get("/ui")
@router.get("/ui/{path:path}")
def serve_ui(request: Request, path: str = "") -> Response:
    dist = _dist(request)
    if dist is None:
        return error_response(404, "not_found", "Web UI not built. Run `make ui`, or use the CLI.")
    if path:
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file():
            return _file(candidate, cache=candidate.parent.name == "assets")
        if path.startswith("assets/"):
            return error_response(404, "not_found", "Resource not found.")
    response = _file(dist / "index.html", cache=False)
    response.headers["Cache-Control"] = "no-store"
    return response

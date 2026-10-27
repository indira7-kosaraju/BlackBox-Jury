"""Guards added by the Phase 10 security audit (docs/SECURITY_AUDIT.md)."""

from __future__ import annotations

import re
import stat
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from blackbox_jury.db.session import Database, upgrade

PUBLIC = {("GET", "/health")}


def test_every_registered_route_requires_auth(app: FastAPI, client: TestClient) -> None:
    # Enumerate via the OpenAPI schema: it lists every route actually served
    # (FastAPI includes routers lazily, so app.routes does not).
    paths = app.openapi()["paths"]
    checked = 0
    for raw_path, ops in paths.items():
        for method in ops:
            if (method.upper(), raw_path) in PUBLIC:
                continue
            path = re.sub(r"\{[^}]+\}", "run_01JX00000000000000000000AA", raw_path)
            code = client.request(method.upper(), path).status_code
            assert code == 401, f"{method.upper()} {raw_path} returned {code} without a token"
            checked += 1
    assert checked >= 20


def test_database_files_are_owner_only(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "bbj.db"
    url = f"sqlite:///{db_path}"
    upgrade(url)
    Database(url).ping()
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_no_cors_or_wildcard_origins(app: FastAPI, client: TestClient) -> None:
    resp = client.get("/health", headers={"Origin": "https://evil.example.test"})
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}

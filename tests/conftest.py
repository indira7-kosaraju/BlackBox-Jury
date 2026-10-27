from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blackbox_jury.api.app import create_app
from blackbox_jury.core.config import Settings
from tests.fakes.fake_runner import FakeSandboxRunner

TEST_TOKEN = "t3st-" + "k" * 40


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep tests independent of the developer's shell env and local .env file."""
    for key in list(os.environ):
        if key.startswith("BBJ_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        api_token=TEST_TOKEN,  # type: ignore[arg-type]
        env="test",
        database_url=f"sqlite:///{tmp_path / 'bbj.db'}",
        seed_path=ROOT / "seeds" / "synthetic_store.json",
        contracts_dir=ROOT / "contracts",
    )


@pytest.fixture
def fake_runner() -> FakeSandboxRunner:
    return FakeSandboxRunner()


@pytest.fixture
def app(settings: Settings, fake_runner: FakeSandboxRunner) -> FastAPI:
    return create_app(settings, runner=fake_runner, inline_worker=True)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}

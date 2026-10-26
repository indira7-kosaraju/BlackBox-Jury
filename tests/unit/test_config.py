from __future__ import annotations

import pytest
from pydantic import ValidationError

from blackbox_jury.core.config import Settings

STRONG = "s" * 40


def test_token_is_required() -> None:
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_short_token_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(api_token="short")  # type: ignore[arg-type]


@pytest.mark.parametrize("token", ["change-me-" + "x" * 30, "PLACEHOLDER" + "x" * 30])
def test_placeholder_token_rejected(token: str) -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(api_token=token)  # type: ignore[arg-type]


def test_loads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BBJ_API_TOKEN", STRONG)
    monkeypatch.setenv("BBJ_ENV", "prod")
    s = Settings()  # type: ignore[call-arg]
    assert s.env == "prod"
    assert s.host == "127.0.0.1"  # safe default bind


def test_secret_not_in_repr() -> None:
    s = Settings(api_token=STRONG)  # type: ignore[arg-type]
    assert STRONG not in repr(s)
    assert STRONG not in str(s.model_dump())


def test_invalid_env_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(api_token=STRONG, env="staging")  # type: ignore[arg-type]

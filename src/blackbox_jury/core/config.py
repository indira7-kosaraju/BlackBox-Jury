"""Typed application settings loaded from environment variables (prefix ``BBJ_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_TOKEN_LENGTH = 32
_PLACEHOLDER_MARKERS = ("change-me", "changeme", "replace", "placeholder", "example")
# Local image reference: name:tag, no registry host, no digest-only refs.
_LOCAL_IMAGE = r"^[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9._-]{1,64}$"


class Settings(BaseSettings):
    """Runtime configuration. Secrets are ``SecretStr`` and never logged."""

    model_config = SettingsConfigDict(
        env_prefix="BBJ_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    env: Literal["dev", "test", "prod"] = "dev"
    api_token: SecretStr
    api_actor: str = Field(default="local-admin", min_length=1, max_length=64)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Sandbox (ARCHITECTURE.md §6-7). Images must already exist locally; they are
    # never pulled at run time. Only allowlisted target images can be executed.
    gateway_image: str = Field(default="bbj-gateway:0.1.0", pattern=_LOCAL_IMAGE)
    allowed_target_images: tuple[Annotated[str, Field(pattern=_LOCAL_IMAGE)], ...] = Field(
        default=("bbj-refundbot:0.1.0",), min_length=1
    )
    exec_timeout_s: float = Field(default=30.0, gt=0, le=300)
    docker_timeout_s: float = Field(default=20.0, gt=0, le=120)
    run_timeout_s: float = Field(default=600.0, gt=0, le=3600)

    # Jury (Phase 7, decision D5): advisory LLM review, off by default. Only redacted,
    # synthetic evidence is ever sent. With "anthropic", BBJ_ANTHROPIC_API_KEY is used if
    # set; otherwise the SDK resolves credentials itself (ANTHROPIC_API_KEY, `ant` profile).
    llm_provider: Literal["none", "anthropic"] = "none"
    llm_model: str = Field(default="claude-opus-5-5", pattern=r"^[a-z0-9.-]{3,64}$")
    llm_timeout_s: float = Field(default=120.0, gt=0, le=600)
    anthropic_api_key: SecretStr | None = None

    # Storage and demo inputs (paths relative to the working directory).
    database_url: str = Field(default="sqlite:///./data/bbj.db", pattern=r"^sqlite:///")
    seed_path: Path = Path("seeds/synthetic_store.json")
    contracts_dir: Path = Path("contracts")

    @field_validator("api_token")
    @classmethod
    def _token_is_strong(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < MIN_TOKEN_LENGTH:
            raise ValueError(f"BBJ_API_TOKEN must be at least {MIN_TOKEN_LENGTH} characters")
        if any(marker in raw.lower() for marker in _PLACEHOLDER_MARKERS):
            raise ValueError("BBJ_API_TOKEN looks like a placeholder; generate a real token")
        return value

    def secret_values(self) -> list[str]:
        """Raw secret values the redactor must mask wherever they appear."""
        values = [self.api_token.get_secret_value()]
        if self.anthropic_api_key is not None:
            values.append(self.anthropic_api_key.get_secret_value())
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process. Raises ``ValidationError`` if config is invalid."""
    return Settings()  # values come from the environment

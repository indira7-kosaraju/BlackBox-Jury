"""Typed application settings loaded from environment variables (prefix ``BBJ_``)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_TOKEN_LENGTH = 32
_PLACEHOLDER_MARKERS = ("change-me", "changeme", "replace", "placeholder", "example")


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
        return [self.api_token.get_secret_value()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process. Raises ``ValidationError`` if config is invalid."""
    return Settings()  # values come from the environment

"""API request models (strict: unknown fields rejected). Responses are plain dicts
built in the route modules so nothing internal leaks by accident.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

_EXTERNAL_KEYS = {
    "url",
    "endpoint",
    "host",
    "hostname",
    "address",
    "base_url",
    "command",
    "entrypoint",
}

IdempotencyKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,128}$")]
EntityId = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,4}_[0-9A-Z]{26}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetConfigIn(_Strict):
    image: str = Field(min_length=1, max_length=130)
    mode: Literal["vulnerable", "hardened", "llm"]

    @model_validator(mode="before")
    @classmethod
    def _no_external(cls, data: Any) -> Any:
        if isinstance(data, dict) and _EXTERNAL_KEYS & {str(k).lower() for k in data}:
            raise ValueError("external targets are not supported in the MVP")
        return data


class AuthorizationIn(_Strict):
    confirm_authorized: bool
    statement: str = Field(default="", max_length=500)


class TargetCreate(_Strict):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,63}$")]
    adapter_type: Literal["sandboxed_container"]
    config: TargetConfigIn
    authorization: AuthorizationIn

    @model_validator(mode="before")
    @classmethod
    def _no_external(cls, data: Any) -> Any:
        if isinstance(data, dict) and _EXTERNAL_KEYS & {str(k).lower() for k in data}:
            raise ValueError("external targets are not supported in the MVP")
        return data


class RunCreate(_Strict):
    target_id: EntityId
    contract_id: EntityId
    scenario_ids: tuple[EntityId, ...] | None = Field(default=None, min_length=1, max_length=25)
    suite: Literal["demo", "slice"] | None = None

    @model_validator(mode="after")
    def _one_selector(self) -> RunCreate:
        if (self.scenario_ids is None) == (self.suite is None):
            raise ValueError("provide exactly one of scenario_ids or suite")
        if self.scenario_ids is not None and len(set(self.scenario_ids)) != len(self.scenario_ids):
            raise ValueError("scenario_ids must be unique")
        return self

"""LLM provider abstraction for the jury (ARCHITECTURE.md §9).

``NullProvider`` is the default: the jury is skipped and the deterministic
pipeline is unaffected. ``AnthropicProvider`` calls Claude with a JSON-schema
structured output and then re-validates the result with our own strict
Pydantic model. Every call returns a ``ModelResult`` with a status
(ok / malformed / timeout / rate_limited / error / skipped); callers never see
an exception from the provider, and malformed output never reaches a verdict.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from blackbox_jury.evidence.collector import sha256_hex

CallStatus = Literal["ok", "malformed", "timeout", "rate_limited", "error", "skipped"]


@dataclass(frozen=True)
class ModelResult:
    status: CallStatus
    provider: str
    model: str
    prompt_id: str
    prompt_version: str
    request_sha256: str
    output: BaseModel | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    retries: int = 0
    detail: str | None = None  # short, non-sensitive reason for non-ok statuses


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
        system: str,
        data: str,
        schema: type[BaseModel],
    ) -> ModelResult: ...


def json_schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic -> JSON schema for structured outputs (closed objects, all required)."""
    out = schema.model_json_schema()
    out.pop("title", None)
    for prop in out.get("properties", {}).values():
        prop.pop("title", None)
    out["additionalProperties"] = False
    out["required"] = list(out.get("properties", {}))
    return out


class NullProvider:
    name = "none"
    model = "none"

    def complete_json(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
        system: str,
        data: str,
        schema: type[BaseModel],
    ) -> ModelResult:
        return ModelResult(
            "skipped",
            self.name,
            self.model,
            prompt_id,
            prompt_version,
            sha256_hex(system + data),
            detail="no provider configured",
        )


class AnthropicProvider:
    """Claude via the official SDK (optional extra ``llm``), imported lazily."""

    name = "anthropic"
    REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self, model: str, timeout_s: float, api_key: str | None = None, client: Any = None
    ) -> None:
        self.model = model
        if client is None:
            import anthropic  # optional dependency

            kwargs: dict[str, Any] = {"timeout": timeout_s, "max_retries": 2}
            if api_key:
                kwargs["api_key"] = api_key
            client = anthropic.Anthropic(**kwargs)
        self._client = client

    def complete_json(
        self,
        *,
        prompt_id: str,
        prompt_version: str,
        system: str,
        data: str,
        schema: type[BaseModel],
    ) -> ModelResult:
        import anthropic

        started = time.monotonic()
        request_sha = sha256_hex(system + data)

        def result(
            status: CallStatus,
            *,
            output: BaseModel | None = None,
            detail: str | None = None,
            usage: Any = None,
        ) -> ModelResult:
            return ModelResult(
                status=status,
                provider=self.name,
                model=self.model,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                request_sha256=request_sha,
                output=output,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                detail=detail,
            )

        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=8000,
                system=system,
                messages=[{"role": "user", "content": data}],
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": json_schema_for(schema)},
                },
                # Server-side refusal fallback (routes a declined request by category).
                betas=[self.REFUSAL_FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APITimeoutError:
            return result("timeout", detail="request timed out")
        except anthropic.RateLimitError:
            return result("rate_limited", detail="rate limited")
        except anthropic.APIStatusError as exc:
            return result("error", detail=f"http {exc.status_code}")
        except anthropic.APIConnectionError:
            return result("error", detail="connection error")

        usage = getattr(response, "usage", None)
        if response.stop_reason == "refusal":
            return result("error", detail="model refused", usage=usage)
        if response.stop_reason == "max_tokens":
            return result("malformed", detail="output truncated", usage=usage)
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            return result("malformed", detail="no text output", usage=usage)
        try:
            parsed = schema.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError, RecursionError):
            return result("malformed", detail="schema validation failed", usage=usage)
        return result("ok", output=parsed, usage=usage)


def make_provider(provider: str, model: str, timeout_s: float, api_key: str | None) -> LLMProvider:
    if provider == "anthropic":
        return AnthropicProvider(model, timeout_s, api_key or None)
    return NullProvider()

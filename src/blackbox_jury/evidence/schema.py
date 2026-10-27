"""Control-plane view of the evidence collected for one execution.

The collector (Phase 5) builds an ``EvidenceBundle`` from the gateway's
evidence lines and the agent's output. Tool calls and state come from the
gateway; ``final_response`` is the agent's own **untrusted claim** and is only
used to detect false success claims and disclosures.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ToolOutcome = Literal["ok", "error", "unknown_tool", "invalid_args"]
State = dict[str, list[dict[str, Any]]]
ArtifactKind = Literal["state_before", "state_after", "agent_final_response"]


class ToolCallEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=1)
    tool: str = Field(max_length=64)
    args: dict[str, Any] | None
    response: dict[str, Any]
    outcome: ToolOutcome
    side_effect: bool


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str = Field(min_length=1, max_length=64)
    tool_calls: tuple[ToolCallEvent, ...] = ()
    state_before: State | None = None
    state_after: State | None = None
    final_response: str | None = None
    # Evidence ids for non-tool-call artifacts, cited by check results.
    artifact_ids: dict[ArtifactKind, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ordered_unique(self) -> EvidenceBundle:
        seqs = [c.seq for c in self.tool_calls]
        if seqs != sorted(set(seqs)):
            raise ValueError("tool_calls must be strictly ordered by unique seq")
        if len({c.id for c in self.tool_calls}) != len(self.tool_calls):
            raise ValueError("tool call ids must be unique")
        return self

    @property
    def is_complete(self) -> bool:
        """All evidence the verifier needs is present."""
        return (
            self.state_before is not None
            and self.state_after is not None
            and self.final_response is not None
        )

    def artifact_ref(self, kind: ArtifactKind) -> str:
        return self.artifact_ids.get(kind, f"{self.execution_id}:{kind}")

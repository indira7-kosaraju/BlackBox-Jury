"""Evidence collector: validates raw sandbox output and builds evidence.

Everything coming out of the sandbox is untrusted. The collector:

- accepts gateway lines only if each is a JSON object of a known event type,
  carries this execution's nonce, protocol v1, and a contiguous ``seq``;
- requires the full lifecycle: ``state_snapshot(before)``, ``gateway_ready``,
  ``state_snapshot(after)``, ``gateway_stopped``; tool ``call_seq`` contiguous;
- treats *any* unexpected, malformed, foreign-nonce, oversized or truncated
  input as untrustworthy evidence → ``complete=False`` (verdict INCONCLUSIVE
  ``missing_evidence``), never as a pass;
- takes the agent's final response only from the last stdout line
  ``{"final_response": str}`` (an untrusted claim), size-capped.

Two views are produced:

- ``bundle``: the unredacted in-memory ``EvidenceBundle`` for the deterministic
  verifier (redaction could mask the very value a check must detect). It is
  never persisted or logged.
- ``tool_calls`` / ``artifacts``: redacted, hashed copies for storage/display.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from blackbox_jury.core.ids import new_id
from blackbox_jury.core.redaction import Redactor
from blackbox_jury.evidence.schema import ArtifactKind, EvidenceBundle, State, ToolCallEvent
from blackbox_jury.sandbox.runner import RawExecutionOutput

MAX_LINE_BYTES = 512 * 1024
MAX_LINES = 400
MAX_FINAL_RESPONSE_CHARS = 16 * 1024
MAX_ARTIFACT_CHARS = 256 * 1024
STDOUT_TAIL_CHARS = 4096

StoredArtifactKind = Literal[
    "task_input",
    "state_before",
    "state_after",
    "state_diff",
    "agent_final_response",
    "agent_stdout_tail",
]


# -- strict event models (gateway evidence protocol v1) -------------------------------------


class _Event(BaseModel):
    # strict: no coercion (e.g. "no" must not become False for side_effect).
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    bbj: str
    v: Literal[1]
    seq: int = Field(ge=1)
    ts: str = Field(max_length=40)


class _Snapshot(_Event):
    type: Literal["state_snapshot"]
    phase: Literal["before", "after"]
    state: dict[str, list[dict[str, Any]]]


class _Ready(_Event):
    type: Literal["gateway_ready"]
    port: int


class _ToolCall(_Event):
    type: Literal["tool_call"]
    call_seq: int = Field(ge=1)
    tool: str = Field(max_length=64)
    args: dict[str, Any] | None
    response: dict[str, Any]
    outcome: Literal["ok", "error", "unknown_tool", "invalid_args"]
    side_effect: bool


class _Rejected(_Event):
    type: Literal["http_request_rejected"]
    method: str = Field(max_length=16)
    path: str = Field(max_length=256)


class _GatewayError(_Event):
    type: Literal["gateway_error"]
    error: str = Field(max_length=1000)


class _Stopped(_Event):
    type: Literal["gateway_stopped"]
    reason: str = Field(max_length=64)
    tool_calls: int = Field(ge=0)


_MODELS: dict[str, type[_Event]] = {
    "state_snapshot": _Snapshot,
    "gateway_ready": _Ready,
    "tool_call": _ToolCall,
    "http_request_rejected": _Rejected,
    "gateway_error": _GatewayError,
    "gateway_stopped": _Stopped,
}


# -- outputs -------------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredArtifact:
    id: str
    kind: StoredArtifactKind
    content_redacted: str
    sha256: str
    truncated: bool
    redactions_applied: int


@dataclass(frozen=True)
class CollectedEvidence:
    bundle: EvidenceBundle  # unredacted; verifier input only
    complete: bool
    problems: tuple[str, ...]
    tool_calls: tuple[ToolCallEvent, ...]  # redacted copies for storage
    artifacts: tuple[StoredArtifact, ...]
    rejected_requests: int = 0
    gateway_errors: tuple[str, ...] = field(default=())


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_gateway(raw: RawExecutionOutput, problems: list[str]) -> list[_Event]:
    events: list[_Event] = []
    lines = [ln for ln in raw.gateway_stdout.split(b"\n") if ln.strip()]
    if len(lines) > MAX_LINES:
        problems.append(f"too many gateway lines ({len(lines)} > {MAX_LINES})")
        lines = lines[:MAX_LINES]
    for n, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_BYTES:
            problems.append(f"gateway line {n} exceeds {MAX_LINE_BYTES} bytes")
            continue
        try:
            obj = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            problems.append(f"gateway line {n} is not JSON")
            continue
        if not isinstance(obj, dict):
            problems.append(f"gateway line {n} is not an object")
            continue
        if obj.get("bbj") != raw.nonce or not raw.nonce:
            problems.append(f"gateway line {n} has an unexpected nonce")
            continue
        model = _MODELS.get(str(obj.get("type")))
        if model is None:
            problems.append(f"gateway line {n} has unknown type")
            continue
        try:
            events.append(model.model_validate(obj))
        except ValidationError:
            problems.append(f"gateway line {n} ({obj.get('type')}) failed schema validation")
    seqs = [e.seq for e in events]
    if seqs != list(range(1, len(seqs) + 1)):
        problems.append("gateway event seq is not contiguous from 1")
    return events


def _final_response(raw: RawExecutionOutput, problems: list[str]) -> str | None:
    if raw.agent_stdout_truncated:
        problems.append("agent stdout truncated")
        return None
    lines = [ln for ln in raw.agent_stdout.split(b"\n") if ln.strip()]
    if not lines:
        problems.append("agent produced no final response")
        return None
    try:
        obj = json.loads(lines[-1])
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        problems.append("agent final line is not JSON")
        return None
    if (
        not isinstance(obj, dict)
        or set(obj) != {"final_response"}
        or not isinstance(obj["final_response"], str)
    ):
        problems.append("agent final line does not match the output contract")
        return None
    text: str = obj["final_response"]
    if len(text) > MAX_FINAL_RESPONSE_CHARS:
        problems.append("agent final response too long")
        return None
    return text


def collect(
    raw: RawExecutionOutput,
    redactor: Redactor,
    task_input: dict[str, Any] | None = None,
) -> CollectedEvidence:
    problems: list[str] = []
    if raw.gateway_stdout_truncated:
        problems.append("gateway output truncated at size cap")
    events = _parse_gateway(raw, problems)

    snapshots: dict[str, State] = {}
    calls: list[_ToolCall] = []
    ready = stopped = 0
    rejected = 0
    gateway_errors: list[str] = []
    for e in events:
        if isinstance(e, _Snapshot):
            if e.phase in snapshots:
                problems.append(f"duplicate state_snapshot({e.phase})")
            snapshots[e.phase] = e.state
        elif isinstance(e, _ToolCall):
            calls.append(e)
        elif isinstance(e, _Ready):
            ready += 1
        elif isinstance(e, _Stopped):
            stopped += 1
            if e.tool_calls != len(calls):
                problems.append("gateway_stopped tool_calls count mismatch")
        elif isinstance(e, _Rejected):
            rejected += 1
        elif isinstance(e, _GatewayError):
            gateway_errors.append(e.error)
    if "before" not in snapshots:
        problems.append("missing state_snapshot(before)")
    if "after" not in snapshots:
        problems.append("missing state_snapshot(after)")
    if ready != 1:
        problems.append("expected exactly one gateway_ready")
    if stopped != 1:
        problems.append("expected exactly one gateway_stopped")
    if gateway_errors:
        problems.append("gateway reported an error")
    if [c.call_seq for c in calls] != list(range(1, len(calls) + 1)):
        problems.append("tool call_seq is not contiguous from 1")
        # Keep the first occurrence of each call_seq, in order, so the bundle can be
        # built; the evidence is already marked incomplete.
        unique: dict[int, _ToolCall] = {}
        for c in calls:
            unique.setdefault(c.call_seq, c)
        calls = [unique[k] for k in sorted(unique)]

    final = _final_response(raw, problems) if raw.outcome == "completed" else None
    if raw.outcome == "completed" and raw.agent_exit_code != 0:
        problems.append(f"agent exited with code {raw.agent_exit_code}")
    if not raw.cleanup_ok:
        problems.append("sandbox cleanup failed")

    ids = {c.call_seq: new_id("tce") for c in calls}
    raw_calls = tuple(
        ToolCallEvent(
            id=ids[c.call_seq],
            seq=c.call_seq,
            tool=c.tool,
            args=c.args,
            response=c.response,
            outcome=c.outcome,
            side_effect=c.side_effect,
        )
        for c in calls
    )
    stored_calls = tuple(
        tc.model_copy(
            update={"args": redactor.redact(tc.args), "response": redactor.redact(tc.response)}
        )
        for tc in raw_calls
    )

    artifacts: list[StoredArtifact] = []
    artifact_ids: dict[ArtifactKind, str] = {}

    def add(kind: StoredArtifactKind, value: Any) -> str:
        text = value if isinstance(value, str) else canonical_json(value)
        redacted, count = redactor.redact_text_with_count(text)
        truncated = len(redacted) > MAX_ARTIFACT_CHARS
        if truncated:
            redacted = redacted[:MAX_ARTIFACT_CHARS]
        art = StoredArtifact(new_id("art"), kind, redacted, sha256_hex(redacted), truncated, count)
        artifacts.append(art)
        return art.id

    if task_input is not None:
        add("task_input", task_input)
    if "before" in snapshots:
        artifact_ids["state_before"] = add("state_before", snapshots["before"])
    if "after" in snapshots:
        artifact_ids["state_after"] = add("state_after", snapshots["after"])
    if final is not None:
        artifact_ids["agent_final_response"] = add("agent_final_response", final)
    tail = raw.agent_stdout[-STDOUT_TAIL_CHARS:].decode("utf-8", errors="replace")
    if tail:
        add("agent_stdout_tail", tail)

    bundle = EvidenceBundle(
        execution_id=raw.execution_id,
        tool_calls=raw_calls,
        state_before=snapshots.get("before"),
        state_after=snapshots.get("after"),
        final_response=final,
        artifact_ids=artifact_ids,
    )
    complete = raw.outcome == "completed" and not problems and bundle.is_complete
    return CollectedEvidence(
        bundle=bundle,
        complete=complete,
        problems=tuple(problems),
        tool_calls=stored_calls,
        artifacts=tuple(artifacts),
        rejected_requests=rejected,
        gateway_errors=tuple(gateway_errors),
    )

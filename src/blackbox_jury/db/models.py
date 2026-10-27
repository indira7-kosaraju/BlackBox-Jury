"""SQLAlchemy models (DATABASE.md §3). Integrity CHECKs mirror the PRD rules so
the database itself refuses, e.g., a cancelled execution marked NOT_REPRODUCED.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


RUN_STATUSES = (
    "queued",
    "running",
    "verifying",
    "reviewing",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
)
EXECUTION_STATUSES = (
    "pending",
    "running",
    "collected",
    "verified",
    "reviewed",
    "error",
    "cancelled",
    "timed_out",
)
VERDICTS = ("CONFIRMED", "NOT_REPRODUCED", "INCONCLUSIVE")
REASONS = (
    "timeout",
    "cancelled",
    "sandbox_error",
    "missing_evidence",
    "verifier_error",
    "jury_only_flag",
)
SEVERITIES = ("critical", "high", "medium", "low")
CATEGORIES = (
    "prompt_injection",
    "unauthorized_tool_call",
    "sensitive_data_exposure",
    "wrong_arguments",
    "duplicate_operation",
    "false_success_claim",
    "benign",
)
ARTIFACT_KINDS = (
    "task_input",
    "state_before",
    "state_after",
    "state_diff",
    "agent_final_response",
    "agent_stdout_tail",
    "gateway_log_tail",
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}  # noqa: RUF012  # SQLAlchemy API


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[str] = mapped_column(String(32))


class Target(Base):
    __tablename__ = "targets"
    __table_args__ = (
        UniqueConstraint("project_id", "name"),
        CheckConstraint(_in("adapter_type", ("sandboxed_container",)), name="adapter_type"),
        CheckConstraint("is_local = 1", name="local_only"),
        CheckConstraint(_in("authorization_status", ("authorized", "revoked")), name="auth_status"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(64))
    adapter_type: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict[str, Any]]
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    is_local: Mapped[int] = mapped_column(Integer, default=1)
    authorization_status: Mapped[str] = mapped_column(String(16))
    authorized_by: Mapped[str] = mapped_column(String(64))
    authorized_at: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (UniqueConstraint("project_id", "name", "version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]]
    body_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        CheckConstraint(
            _in("purpose", ("jury_review", "jury_counter", "scenario_generation")), name="purpose"
        ),
        CheckConstraint(
            _in("status", ("ok", "malformed", "timeout", "rate_limited", "error", "skipped")),
            name="status",
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(32))
    prompt_id: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    request_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[int | None]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    retries: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32))


class ScenarioRow(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("project_id", "key", "version"),
        CheckConstraint(_in("category", CATEGORIES), name="category"),
        CheckConstraint(_in("source", ("curated", "generated")), name="source"),
        Index("ix_scenarios_project_category", "project_id", "category"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16))
    body: Mapped[dict[str, Any]]
    body_sha256: Mapped[str] = mapped_column(String(64))
    generated_by_model_call_id: Mapped[str | None] = mapped_column(ForeignKey("model_calls.id"))
    created_at: Mapped[str] = mapped_column(String(32))


class RegressionTest(Base):
    __tablename__ = "regression_tests"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id", use_alter=True), unique=True)
    body: Mapped[dict[str, Any]]
    body_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint(_in("kind", ("standard", "retest")), name="kind"),
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        CheckConstraint("(kind = 'retest') = (regression_test_id IS NOT NULL)", name="retest_link"),
        Index("ix_runs_project_created", "project_id", "created_at"),
        Index("ix_runs_status", "status"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id"))
    target_config_snapshot: Mapped[dict[str, Any]]
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"))
    kind: Mapped[str] = mapped_column(String(16))
    regression_test_id: Mapped[str | None] = mapped_column(ForeignKey("regression_tests.id"))
    status: Mapped[str] = mapped_column(String(16))
    status_reason: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    requested_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[str | None] = mapped_column(String(32))
    finished_at: Mapped[str | None] = mapped_column(String(32))


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("run_id", "scenario_id"),
        CheckConstraint(_in("status", EXECUTION_STATUSES), name="status"),
        CheckConstraint(f"verdict IS NULL OR {_in('verdict', VERDICTS)}", name="verdict"),
        CheckConstraint(
            f"inconclusive_reason IS NULL OR {_in('inconclusive_reason', REASONS)}", name="reason"
        ),
        CheckConstraint(f"severity IS NULL OR {_in('severity', SEVERITIES)}", name="severity"),
        # PRD FR11/FR22: non-completed executions can never pass.
        CheckConstraint(
            "status NOT IN ('error', 'cancelled', 'timed_out') "
            "OR verdict IS NULL OR verdict = 'INCONCLUSIVE'",
            name="no_pass_when_not_completed",
        ),
        CheckConstraint(
            "verdict <> 'INCONCLUSIVE' OR inconclusive_reason IS NOT NULL", name="reason_required"
        ),
        CheckConstraint(
            "verdict <> 'CONFIRMED' OR severity IS NOT NULL", name="confirmed_has_severity"
        ),
        CheckConstraint(
            "verdict = 'CONFIRMED' OR severity IS NULL", name="severity_only_confirmed"
        ),
        CheckConstraint(
            "verdict IS NOT NULL OR status IN ('pending', 'running', 'collected')",
            name="terminal_has_verdict",
        ),
        Index("ix_executions_run_verdict", "run_id", "verdict"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"))
    status: Mapped[str] = mapped_column(String(16))
    verdict: Mapped[str | None] = mapped_column(String(16))
    inconclusive_reason: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str | None] = mapped_column(String(16))
    task_completed: Mapped[int | None]
    execution_nonce_sha256: Mapped[str | None] = mapped_column(String(64))
    sandbox_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evidence_problems: Mapped[list[Any] | None] = mapped_column(JSON)
    started_at: Mapped[str | None] = mapped_column(String(32))
    finished_at: Mapped[str | None] = mapped_column(String(32))


class ToolCallEventRow(Base):
    __tablename__ = "tool_call_events"
    __table_args__ = (
        UniqueConstraint("execution_id", "seq"),
        CheckConstraint(
            _in("outcome", ("ok", "error", "unknown_tool", "invalid_args")), name="outcome"
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"))
    seq: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(64))
    args_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    response_redacted: Mapped[dict[str, Any]]
    outcome: Mapped[str] = mapped_column(String(16))
    side_effect: Mapped[int] = mapped_column(Integer)
    gateway_ts: Mapped[str | None] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))


class EvidenceArtifact(Base):
    __tablename__ = "evidence_artifacts"
    __table_args__ = (
        CheckConstraint(_in("kind", ARTIFACT_KINDS), name="kind"),
        Index("ix_artifacts_execution_kind", "execution_id", "kind"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"))
    kind: Mapped[str] = mapped_column(String(32))
    content_redacted: Mapped[str] = mapped_column(Text)
    truncated: Mapped[int] = mapped_column(Integer, default=0)
    redactions_applied: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32))


class CheckResultRow(Base):
    __tablename__ = "check_results"
    __table_args__ = (
        UniqueConstraint("execution_id", "check_id"),
        CheckConstraint(_in("outcome", ("PASS", "FAIL", "ERROR")), name="outcome"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"))
    check_id: Mapped[str] = mapped_column(String(32))
    rule_ref: Mapped[str] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(8))
    evidence_refs: Mapped[list[Any]]
    message: Mapped[str] = mapped_column(Text)
    side_effect: Mapped[int] = mapped_column(Integer, default=0)
    verifier_version: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(32))


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(_in("verdict", ("CONFIRMED", "INCONCLUSIVE")), name="verdict"),
        CheckConstraint(f"severity IS NULL OR {_in('severity', SEVERITIES)}", name="severity"),
        CheckConstraint(
            "verdict <> 'CONFIRMED' OR severity IS NOT NULL", name="confirmed_has_severity"
        ),
        Index("ix_findings_run_severity", "run_id", "severity"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32))
    verdict: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str | None] = mapped_column(String(16))
    failed_check_ids: Mapped[list[Any]]
    repro_steps: Mapped[list[Any]]
    created_at: Mapped[str] = mapped_column(String(32))


class JuryReview(Base):
    __tablename__ = "jury_reviews"
    __table_args__ = (
        CheckConstraint(_in("role", ("reviewer", "counter_reviewer")), name="role"),
        CheckConstraint(_in("status", ("ok", "malformed", "error", "skipped")), name="status"),
        CheckConstraint(
            "(role = 'counter_reviewer') = (responds_to_id IS NOT NULL)", name="counter_link"
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"))
    role: Mapped[str] = mapped_column(String(20))
    responds_to_id: Mapped[str | None] = mapped_column(ForeignKey("jury_reviews.id"))
    model_call_id: Mapped[str] = mapped_column(ForeignKey("model_calls.id"))
    status: Mapped[str] = mapped_column(String(16))
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(32))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_project_created", "project_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(40))
    request_id: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict[str, Any]]
    created_at: Mapped[str] = mapped_column(String(32))

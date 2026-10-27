"""Database layer: migrations, integrity CHECKs, state machine, repository rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from blackbox_jury.core.ids import new_id
from blackbox_jury.db import repo
from blackbox_jury.db.models import Execution, Finding
from blackbox_jury.db.session import Database, upgrade
from blackbox_jury.orchestrator.state_machine import (
    EXECUTION_TERMINAL,
    RUN_TERMINAL,
    IllegalTransitionError,
    check_execution,
    check_run,
)
from blackbox_jury.scenarios.loader import load_library
from tests.unit.verifier.conftest import demo_contract


@pytest.fixture
def db(tmp_path: Path) -> Database:
    url = f"sqlite:///{tmp_path / 't.db'}"
    upgrade(url)
    return Database(url)


@pytest.fixture
def world(db: Database) -> dict[str, Any]:
    """Project + target + contract + one scenario + a queued run with one execution."""
    with db.session() as s:
        project = repo.ensure_project(s, "default").id
        target = repo.create_target(
            s,
            project=project,
            name="refundbot",
            image="bbj-refundbot:0.1.0",
            mode="vulnerable",
            actor="test",
        )
        contract, _ = repo.upsert_contract(s, project, demo_contract())
        scenario, _ = repo.upsert_scenario(s, project, load_library()[0])
        run = repo.create_run(
            s,
            project=project,
            target=target,
            contract=contract,
            scenario_rows=[scenario],
            idempotency_key="key-00000001",
            request_sha256="0" * 64,
            actor="test",
        )
        exe = repo.executions_for_run(s, run.id)[0]
        return {
            "project": project,
            "target": target.id,
            "contract": contract.id,
            "scenario": scenario.id,
            "run": run.id,
            "execution": exe.id,
        }


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'twice.db'}"
    upgrade(url)
    upgrade(url)  # second run must be a no-op (regression: lost alembic_version row)
    with Database(url).engine.connect() as c:
        assert c.exec_driver_sql("select version_num from alembic_version").scalar() == "0001"
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


# -- DB-level integrity backstops (PRD FR11/FR22) --------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "cancelled", "verdict": "NOT_REPRODUCED"},
        {"status": "timed_out", "verdict": "CONFIRMED", "severity": "high"},
        {"status": "error", "verdict": "NOT_REPRODUCED"},
        {"status": "verified", "verdict": "INCONCLUSIVE"},  # reason required
        {"status": "verified", "verdict": "CONFIRMED"},  # severity required
        {"status": "verified", "verdict": "NOT_REPRODUCED", "severity": "low"},
        {"status": "verified", "verdict": None},  # terminal states need a verdict
        {"status": "verified", "verdict": "PASSED"},
        {"status": "done"},
    ],
)
def test_execution_checks_reject_impossible_rows(
    db: Database, world: dict[str, Any], fields: dict[str, Any]
) -> None:
    with pytest.raises(IntegrityError), db.session() as s:
        s.add(
            Execution(
                id=new_id("exe"), run_id=world["run"], scenario_id=world["scenario"], **fields
            )
        )
        s.flush()


def test_finding_must_have_severity_when_confirmed(db: Database, world: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), db.session() as s:
        s.add(
            Finding(
                id=new_id("fnd"),
                execution_id=world["execution"],
                run_id=world["run"],
                title="t",
                category="prompt_injection",
                verdict="CONFIRMED",
                severity=None,
                failed_check_ids=[],
                repro_steps=[],
                created_at="now",
            )
        )
        s.flush()


def test_foreign_keys_enforced(db: Database) -> None:
    with pytest.raises(IntegrityError), db.session() as s:
        s.add(
            Execution(
                id=new_id("exe"), run_id="run_DOESNOTEXIST", scenario_id="scn_X", status="pending"
            )
        )
        s.flush()


def test_failed_write_rolls_back_whole_transaction(db: Database, world: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), db.session() as s:
        repo.transition_execution(s, world["execution"], "pending", "running")
        s.add(
            Execution(
                id=new_id("exe"), run_id=world["run"], scenario_id=world["scenario"], status="bogus"
            )
        )
        s.flush()
    with db.session() as s:
        assert s.get_one(Execution, world["execution"]).status == "pending"


# -- state machine ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal", sorted(RUN_TERMINAL))
@pytest.mark.parametrize("new", ["queued", "running", "completed", "failed"])
def test_terminal_runs_never_move(terminal: str, new: str) -> None:
    with pytest.raises(IllegalTransitionError):
        check_run(terminal, new)


@pytest.mark.parametrize("terminal", sorted(EXECUTION_TERMINAL - {"verified"}))
@pytest.mark.parametrize("new", ["pending", "running", "verified", "reviewed"])
def test_terminal_executions_never_move(terminal: str, new: str) -> None:
    with pytest.raises(IllegalTransitionError):
        check_execution(terminal, new)


@pytest.mark.parametrize(
    ("old", "new"), [("pending", "verified"), ("running", "verified"), ("collected", "cancelled")]
)
def test_execution_cannot_skip_collection(old: str, new: str) -> None:
    with pytest.raises(IllegalTransitionError):
        check_execution(old, new)


def test_optimistic_transition_detects_stale_state(db: Database, world: dict[str, Any]) -> None:
    with db.session() as s:
        repo.transition_execution(s, world["execution"], "pending", "running")
    with pytest.raises(repo.StaleStateError), db.session() as s:
        repo.transition_execution(s, world["execution"], "pending", "running")


# -- repository rules -------------------------------------------------------------------------


def test_contract_and_scenario_upserts_are_content_addressed(
    db: Database, world: dict[str, Any]
) -> None:
    contract = demo_contract()
    scenario = load_library()[0]
    with db.session() as s:
        assert repo.upsert_contract(s, world["project"], contract)[1] is False
        assert repo.upsert_scenario(s, world["project"], scenario)[1] is False
    changed = contract.model_copy(update={"task": "something else"})
    with pytest.raises(repo.ConflictError), db.session() as s:
        repo.upsert_contract(s, world["project"], changed)
    changed_scenario = scenario.model_copy(update={"user_message": "different"})
    with pytest.raises(repo.ConflictError), db.session() as s:
        repo.upsert_scenario(s, world["project"], changed_scenario)


def test_project_scoping(db: Database, world: dict[str, Any]) -> None:
    with db.session() as s:
        other = repo.ensure_project(s, "other").id
        with pytest.raises(repo.NotFoundError):
            repo.get_run(s, other, world["run"])
        with pytest.raises(repo.NotFoundError):
            repo.get_target(s, other, world["target"])
        with pytest.raises(repo.NotFoundError):
            repo.get_execution(s, other, world["execution"])


def test_totals_count_only_executed(db: Database, world: dict[str, Any]) -> None:
    with db.session() as s:
        assert repo.totals(s, world["run"]) == {
            "selected": 1,
            "executed": 0,
            "CONFIRMED": 0,
            "NOT_REPRODUCED": 0,
            "INCONCLUSIVE": 0,
            "not_run": 0,
            "pending": 1,
        }


def test_cursor_roundtrip_and_rejects_garbage() -> None:
    rid = "run_01JX00000000000000000000AA"
    assert repo.decode_cursor(repo.encode_cursor(rid)) == rid
    with pytest.raises(ValueError):
        repo.decode_cursor(repo.encode_cursor("anything' OR 1=1"))
    with pytest.raises(ValueError):
        repo.decode_cursor("@@@not-base64@@@")


def test_cancel_only_active_runs(db: Database, world: dict[str, Any]) -> None:
    with db.session() as s:
        repo.request_cancel(s, world["project"], world["run"])
        repo.transition_run(s, world["run"], "queued", "cancelled")
    with pytest.raises(repo.ConflictError), db.session() as s:
        repo.request_cancel(s, world["project"], world["run"])

"""Idempotent demo seeding (DATABASE.md §7): project, contracts, curated
scenarios and the bundled RefundBot target (authorized by actor ``seed``)."""

from __future__ import annotations

from typing import Any

from blackbox_jury.contracts.loader import load_contract_file
from blackbox_jury.core.config import Settings
from blackbox_jury.db import repo
from blackbox_jury.db.session import Database
from blackbox_jury.scenarios.loader import load_base_seed, load_library, validate_against

DEMO_TARGET = "refundbot"
DEMO_IMAGE = "bbj-refundbot:0.1.0"


def seed(db: Database, settings: Settings) -> dict[str, Any]:
    base_seed = load_base_seed(settings.seed_path)
    contracts = [load_contract_file(p) for p in sorted(settings.contracts_dir.glob("*.yaml"))]
    library = load_library()
    summary: dict[str, Any] = {
        "contracts_created": 0,
        "scenarios_created": 0,
        "target_created": False,
    }
    with db.session() as s:
        project = repo.ensure_project(s, "default").id
        for contract in contracts:
            _, created = repo.upsert_contract(s, project, contract)
            summary["contracts_created"] += int(created)
        for scenario in library:
            matching = [c for c in contracts if c.principal == scenario.principal]
            if not matching:
                raise ValueError(f"no contract for scenario principal {scenario.principal}")
            validate_against(scenario, matching[0], base_seed)
            _, created = repo.upsert_scenario(s, project, scenario)
            summary["scenarios_created"] += int(created)
        if (
            DEMO_IMAGE in settings.allowed_target_images
            and repo.find_target(s, project, DEMO_TARGET) is None
        ):
            target = repo.create_target(
                s,
                project=project,
                name=DEMO_TARGET,
                image=DEMO_IMAGE,
                mode="vulnerable",
                actor="seed",
            )
            repo.audit(
                s,
                project=project,
                actor="seed",
                action="target.create",
                request_id="seed",
                entity_type="target",
                entity_id=target.id,
                details={"name": DEMO_TARGET, "image": DEMO_IMAGE, "mode": "vulnerable"},
            )
            summary["target_created"] = True
    return summary

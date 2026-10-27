"""Load, cross-validate and materialise scenarios.

- ``load_library`` reads the curated YAML files (``yaml.safe_load`` only).
- ``validate_against`` checks a scenario against the contract and base seed
  (principal, referenced orders, payload sizes the sandbox will accept).
- ``build_seed`` / ``build_task_input`` produce the exact payloads the sandbox
  runner passes to the gateway and the agent. The task input never contains
  the execution nonce (ARCHITECTURE.md §6).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from blackbox_jury.contracts.schema import TaskContract
from blackbox_jury.scenarios.schema import Scenario

LIBRARY_DIR = Path(__file__).resolve().parent / "library"
MAX_SCENARIO_BYTES = 32 * 1024
MAX_BASE_SEED_BYTES = 64 * 1024
# Limits enforced by the sandbox images (bbj_gateway.server, bbj_refundbot.main).
MAX_SEED_PAYLOAD_BYTES = 64 * 1024
MAX_TASK_PAYLOAD_BYTES = 16 * 1024
MIN_CANARY_LENGTH = 8

AgentMode = Literal["vulnerable", "hardened"]

SUITES: dict[str, tuple[str, ...]] = {
    # Vertical-slice suite: one prompt-injection scenario end to end.
    "slice": ("pi_order_note_refund",),
}


class ScenarioError(ValueError):
    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def parse_scenario(data: object) -> Scenario:
    try:
        return Scenario.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"loc": [str(p) for p in e["loc"]], "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        raise ScenarioError("scenario validation failed", errors) from None


def load_scenario_text(text: str) -> Scenario:
    if len(text.encode()) > MAX_SCENARIO_BYTES:
        raise ScenarioError(f"scenario exceeds {MAX_SCENARIO_BYTES} bytes")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"scenario is not valid YAML: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise ScenarioError("scenario must be a mapping")
    return parse_scenario(data)


def load_scenario_file(path: Path) -> Scenario:
    if path.stat().st_size > MAX_SCENARIO_BYTES:
        raise ScenarioError(f"{path.name}: scenario exceeds {MAX_SCENARIO_BYTES} bytes")
    scenario = load_scenario_text(path.read_text(encoding="utf-8"))
    if scenario.key != path.stem:
        raise ScenarioError(f"{path.name}: key {scenario.key!r} must match file name")
    return scenario


def load_library(directory: Path = LIBRARY_DIR) -> tuple[Scenario, ...]:
    scenarios = [load_scenario_file(p) for p in sorted(directory.glob("*.yaml"))]
    keys = [s.key for s in scenarios]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise ScenarioError(f"duplicate scenario keys: {duplicates}")
    return tuple(scenarios)


def suite(name: str, library: tuple[Scenario, ...]) -> tuple[Scenario, ...]:
    """``demo`` = the whole curated library; other suites are fixed key lists."""
    if name == "demo":
        return library
    if name not in SUITES:
        raise ScenarioError(f"unknown suite {name!r}")
    by_key = {s.key: s for s in library}
    missing = [k for k in SUITES[name] if k not in by_key]
    if missing:
        raise ScenarioError(f"suite {name!r} references unknown scenarios: {missing}")
    return tuple(by_key[k] for k in SUITES[name])


def load_base_seed(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_BASE_SEED_BYTES:
        raise ScenarioError("base seed too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ScenarioError("base seed must be a JSON object")
    return data


# -- materialisation ---------------------------------------------------------------


def build_seed(scenario: Scenario, base_seed: dict[str, Any]) -> dict[str, Any]:
    """Base seed + whitelisted overrides. ``base_seed`` is not modified."""
    seed = copy.deepcopy(base_seed)
    seed.pop("_notice", None)
    overrides = scenario.seed
    for order in seed.get("orders", []):
        if order.get("order_id") in overrides.order_notes:
            order["note"] = overrides.order_notes[order["order_id"]]
    seed["faults"] = [
        {"tool": f.tool, "match": f.match.model_dump(), "kind": f.kind, "times": f.times}
        for f in overrides.faults
    ]
    seed["approvals"] = [*seed.get("approvals", []), *(a.model_dump() for a in overrides.approvals)]
    return seed


def canaries_for(base_seed: dict[str, Any]) -> tuple[str, ...]:
    """Synthetic secrets the agent is given and must never disclose."""
    context = base_seed.get("agent_context", {})
    if not isinstance(context, dict):
        return ()
    return tuple(v for v in context.values() if isinstance(v, str) and len(v) >= MIN_CANARY_LENGTH)


def build_task_input(
    scenario: Scenario, mode: AgentMode, base_seed: dict[str, Any]
) -> dict[str, Any]:
    context = base_seed.get("agent_context", {})
    return {
        "principal": scenario.principal,
        "user_message": scenario.user_message,
        "mode": mode,
        "system_context": {k: v for k, v in context.items() if isinstance(v, str)}
        if isinstance(context, dict)
        else {},
    }


# -- cross validation ----------------------------------------------------------------


def validate_against(scenario: Scenario, contract: TaskContract, base_seed: dict[str, Any]) -> None:
    """Raise ``ScenarioError`` if the scenario cannot run against this contract/seed."""
    problems: list[str] = []
    if scenario.principal != contract.principal:
        problems.append(
            f"principal {scenario.principal} != contract principal {contract.principal}"
        )
    customers = {c.get("customer_id") for c in base_seed.get("customers", [])}
    if scenario.principal not in customers:
        problems.append(f"principal {scenario.principal} not in seed")
    orders = {o.get("order_id") for o in base_seed.get("orders", [])}
    referenced = (
        set(scenario.seed.order_notes)
        | {f.match.order_id for f in scenario.seed.faults}
        | {a.order_id for a in scenario.seed.approvals}
    )
    unknown = sorted(referenced - orders)
    if unknown:
        problems.append(f"overrides reference unknown orders: {unknown}")
    existing_approvals = {a.get("approval_id") for a in base_seed.get("approvals", [])}
    clashes = sorted({a.approval_id for a in scenario.seed.approvals} & existing_approvals)
    if clashes:
        problems.append(f"approval ids already in seed: {clashes}")
    disabled = sorted(set(scenario.targets) - contract.enabled_checks() - {"TASK_COMPLETION"})
    if disabled:
        problems.append(f"targets not enabled by contract: {disabled}")
    # Limits apply to the decoded JSON (the images cap base64 input accordingly).
    if len(json.dumps(build_seed(scenario, base_seed)).encode()) > MAX_SEED_PAYLOAD_BYTES:
        problems.append("materialised seed exceeds sandbox limit")
    task = build_task_input(scenario, "vulnerable", base_seed)
    if len(json.dumps(task).encode()) > MAX_TASK_PAYLOAD_BYTES:
        problems.append("materialised task input exceeds sandbox limit")
    if problems:
        raise ScenarioError(f"{scenario.key}: " + "; ".join(problems))

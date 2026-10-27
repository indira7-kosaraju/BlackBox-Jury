"""Load and validate contract files. YAML is parsed with ``safe_load`` only."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from blackbox_jury.contracts.schema import TaskContract

MAX_CONTRACT_BYTES = 64 * 1024


class ContractError(ValueError):
    """A contract could not be loaded or is invalid. ``errors`` lists field problems."""

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def parse_contract(data: object) -> TaskContract:
    try:
        return TaskContract.model_validate(data)
    except ValidationError as exc:
        # Locations and messages only; never echo submitted values.
        errors = [
            {"loc": [str(p) for p in e["loc"]], "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        raise ContractError("contract validation failed", errors) from None


def load_contract_text(text: str) -> TaskContract:
    if len(text.encode()) > MAX_CONTRACT_BYTES:
        raise ContractError(f"contract exceeds {MAX_CONTRACT_BYTES} bytes")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractError(f"contract is not valid YAML: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise ContractError("contract must be a mapping")
    return parse_contract(data)


def load_contract_file(path: Path) -> TaskContract:
    if path.stat().st_size > MAX_CONTRACT_BYTES:
        raise ContractError(f"contract exceeds {MAX_CONTRACT_BYTES} bytes")
    return load_contract_text(path.read_text(encoding="utf-8"))


def check_principal_exists(contract: TaskContract, customer_ids: Iterable[str]) -> None:
    """Reference check against the synthetic seed (API.md ``/contracts/validate``)."""
    if contract.principal not in set(customer_ids):
        raise ContractError(
            "principal not found in synthetic seed",
            [{"loc": ["principal"], "msg": "unknown customer", "type": "reference_error"}],
        )

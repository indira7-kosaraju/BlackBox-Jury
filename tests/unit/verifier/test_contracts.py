"""Contract schema and loader: valid and invalid contracts (PRD FR4-FR5)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml
from bbj_gateway.events import EventSink
from bbj_gateway.store import Store
from bbj_gateway.tools import Gateway

from blackbox_jury.contracts.loader import (
    MAX_CONTRACT_BYTES,
    ContractError,
    check_principal_exists,
    load_contract_text,
    parse_contract,
)
from blackbox_jury.contracts.schema import KNOWN_TOOLS, TaskContract
from tests.unit.sandbox_images.conftest import base_seed
from tests.unit.verifier.conftest import CONTRACT_PATH, demo_contract


def _valid() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CONTRACT_PATH.read_text())
    return data


def test_demo_contract_loads(contract: TaskContract) -> None:
    assert contract.name == "refund_support_v1" and contract.version == 1
    assert contract.principal == "C-1001"
    assert set(contract.allowed_tools) == {"get_order", "list_orders", "issue_refund"}
    assert contract.enabled_checks() == {
        "TOOL_ALLOWLIST",
        "ARG_OWNERSHIP",
        "REFUND_LIMIT",
        "REFUND_APPROVAL",
        "PII_CROSS_PRINCIPAL",
        "CANARY_SECRET_EXPOSURE",
        "DUPLICATE_SIDE_EFFECT",
        "FALSE_SUCCESS_CLAIM",
    }
    assert contract.approval() is not None and contract.approval().threshold_cents == 10000  # type: ignore[union-attr]


def test_rule_refs_are_stable(contract: TaskContract) -> None:
    assert contract.rule_ref("TOOL_ALLOWLIST") == "contract:refund_support_v1@v1#allowed_tools"
    assert (
        contract.rule_ref("ARG_OWNERSHIP")
        == "contract:refund_support_v1@v1#argument_constraints[0]"
    )
    assert contract.rule_ref("REFUND_APPROVAL") == "contract:refund_support_v1@v1#invariants[0]"
    assert (
        contract.rule_ref("FALSE_SUCCESS_CLAIM") == "contract:refund_support_v1@v1#reliability[1]"
    )


def test_contract_is_immutable(contract: TaskContract) -> None:
    with pytest.raises(Exception):  # noqa: B017  # pydantic frozen-instance error
        contract.principal = "C-2002"  # type: ignore[misc]


def test_known_tools_match_gateway_catalogue() -> None:
    gw = Gateway(Store(base_seed()), EventSink("0" * 32, lambda _: None))
    assert set(gw.tool_names) == KNOWN_TOOLS


def _mutate(path: str, value: Any) -> dict[str, Any]:
    data = copy.deepcopy(_valid())
    keys = path.split(".")
    target = data
    for k in keys[:-1]:
        target = target[int(k)] if k.isdigit() else target[k]
    last = keys[-1]
    if value is _DELETE:
        del target[last]
    else:
        target[int(last) if last.isdigit() else last] = value
    return data


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "fragment"),
    [
        ("unexpected_field", True, "extra"),
        ("principal", "customer-1", "pattern"),
        ("principal", _DELETE, "missing"),
        ("version", 0, "greater than or equal"),
        ("name", "Bad Name!", "pattern"),
        ("task", "", "at least 1"),
        ("allowed_tools", [], "at least 1"),
        (
            "allowed_tools",
            ["get_order", "list_orders", "issue_refund", "delete_everything"],
            "literal",
        ),
        ("allowed_tools", ["get_order", "get_order", "list_orders", "issue_refund"], "duplicates"),
        (
            "allowed_tools",
            ["get_order", "list_orders", "issue_refund", "send_email"],
            "both allowed and forbidden",
        ),
        ("forbidden_tools", ["get_customer", "send_email"], "unclassified"),
        ("argument_constraints.0.tools", ["send_email"], "unscoped"),
        ("argument_constraints.0.tools", [], "at least 1"),
        ("argument_constraints.0.check", "MAKE_IT_PASS", "tag"),
        ("invariants.0.threshold_cents", 0, "greater than"),
        ("invariants.0.threshold_cents", -5, "greater than"),
        (
            "data_rules",
            [{"check": "PII_CROSS_PRINCIPAL"}, {"check": "PII_CROSS_PRINCIPAL"}],
            "duplicate checks",
        ),
        ("data_rules.0.override_verdict", "NOT_REPRODUCED", "extra"),
        ("reliability.0.check", "LLM_SAYS_OK", "literal"),
    ],
)
def test_invalid_contracts_rejected(path: str, value: Any, fragment: str) -> None:
    with pytest.raises(ContractError) as exc:
        parse_contract(_mutate(path, value))
    combined = " ".join(e["msg"] + " " + e["type"] for e in exc.value.errors).lower()
    assert fragment.lower() in combined


def test_validation_errors_do_not_echo_values() -> None:
    secret_like = "sk-ant-should-not-be-echoed-0123456789"
    with pytest.raises(ContractError) as exc:
        parse_contract(_mutate("principal", secret_like))
    assert secret_like not in str(exc.value.errors)


@pytest.mark.parametrize(
    "text",
    [
        "just a string",
        "- a\n- list",
        "name: [unclosed",
        "!!python/object/apply:os.system ['echo pwned']",
        "a: !!python/name:os.system",
    ],
)
def test_bad_yaml_rejected_safely(text: str) -> None:
    with pytest.raises(ContractError):
        load_contract_text(text)


def test_oversized_contract_rejected() -> None:
    with pytest.raises(ContractError, match="exceeds"):
        load_contract_text("task: " + "x" * (MAX_CONTRACT_BYTES + 1))


def test_principal_must_exist_in_seed(contract: TaskContract) -> None:
    ids = [c["customer_id"] for c in base_seed()["customers"]]
    check_principal_exists(contract, ids)
    with pytest.raises(ContractError):
        check_principal_exists(contract, ["C-2002"])


def test_minimal_contract_enables_only_allowlist() -> None:
    c = parse_contract(
        {
            "name": "minimal_contract",
            "version": 1,
            "task": "t",
            "principal": "C-1001",
            "allowed_tools": sorted(KNOWN_TOOLS),
        }
    )
    assert c.enabled_checks() == {"TOOL_ALLOWLIST"}
    assert demo_contract() != c

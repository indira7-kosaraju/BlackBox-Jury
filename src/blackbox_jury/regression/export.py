"""Portable regression tests (PRD FR29-FR30, format ``bbj-regression/1``).

A regression test pins exactly what reproduced a CONFIRMED finding: the scenario
body, the contract version, and the base-seed hash. Its expectation is that every
check that FAILED now PASSES. Hashes make tampering and drift detectable:
``verify_document`` recomputes them before any retest runs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from blackbox_jury.contracts.schema import CheckId
from blackbox_jury.evidence.collector import canonical_json, sha256_hex
from blackbox_jury.scenarios.schema import Scenario

FORMAT = "bbj-regression/1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreatedFrom(_Strict):
    finding_id: str
    run_id: str
    execution_id: str
    original_verdict: Literal["CONFIRMED"]
    original_severity: Literal["critical", "high", "medium", "low"]


class ContractRef(_Strict):
    name: str
    version: int
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Expectation(_Strict):
    # Every check that failed in the original execution must now PASS.
    checks: dict[CheckId, Literal["PASS"]] = Field(min_length=1)
    verdict: Literal["NOT_REPRODUCED"] = "NOT_REPRODUCED"


class RegressionDocument(_Strict):
    format: Literal["bbj-regression/1"]
    regression_test_id: str
    title: str = Field(max_length=200)
    created_from: CreatedFrom
    scenario: dict[str, Any]
    scenario_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract: ContractRef
    target_hint: dict[str, Any]
    expect: Expectation


class RegressionDocumentError(ValueError):
    pass


def build_document(
    *,
    regression_test_id: str,
    title: str,
    finding_id: str,
    run_id: str,
    execution_id: str,
    severity: str,
    failed_checks: list[str],
    scenario_body: dict[str, Any],
    base_seed: dict[str, Any],
    contract_name: str,
    contract_version: int,
    contract_sha256: str,
    target_snapshot: dict[str, Any],
) -> dict[str, Any]:
    doc = RegressionDocument.model_validate(
        {
            "format": FORMAT,
            "regression_test_id": regression_test_id,
            "title": title[:200],
            "created_from": {
                "finding_id": finding_id,
                "run_id": run_id,
                "execution_id": execution_id,
                "original_verdict": "CONFIRMED",
                "original_severity": severity,
            },
            "scenario": scenario_body,
            "scenario_sha256": sha256_hex(canonical_json(scenario_body)),
            "seed_sha256": sha256_hex(canonical_json(base_seed)),
            "contract": {
                "name": contract_name,
                "version": contract_version,
                "sha256": contract_sha256,
            },
            "target_hint": {
                "name": target_snapshot.get("name"),
                "image": target_snapshot.get("image"),
            },
            "expect": {"checks": dict.fromkeys(failed_checks, "PASS")},
        }
    )
    return doc.model_dump(mode="json")


def verify_document(data: object, base_seed: dict[str, Any]) -> tuple[RegressionDocument, Scenario]:
    """Validate format and hashes; raise ``RegressionDocumentError`` on any mismatch."""
    try:
        doc = RegressionDocument.model_validate(data)
        scenario = Scenario.model_validate(doc.scenario)
    except ValidationError as exc:
        raise RegressionDocumentError(
            f"invalid regression document ({exc.error_count()} errors)"
        ) from None
    if sha256_hex(canonical_json(doc.scenario)) != doc.scenario_sha256:
        raise RegressionDocumentError("scenario hash mismatch (document altered)")
    if sha256_hex(canonical_json(base_seed)) != doc.seed_sha256:
        raise RegressionDocumentError(
            "base seed changed since export; the retest would not be reproducible"
        )
    return doc, scenario

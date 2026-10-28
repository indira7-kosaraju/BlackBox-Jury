"""Committed regression tests stay valid (no Docker): hashes still match the
current seed, scenario library and pinned contract. A failure here means the
seed/scenario/contract drifted and regressions must be regenerated
(scripts/export_demo_regressions.py) or the change versioned."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blackbox_jury.regression.export import verify_document
from blackbox_jury.regression.runner import pinned_contract
from blackbox_jury.scenarios.loader import load_library
from tests.unit.sandbox_images.conftest import base_seed

ROOT = Path(__file__).resolve().parents[2]
# The committed suite is named by scenario key; ad-hoc exports (rgt_*.json) are not part of it.
FILES = sorted(p for p in (ROOT / "regressions").glob("*.json") if not p.name.startswith("rgt_"))
CONFIRMED_DEMO = {s.key for s in load_library() if not s.is_control}


def test_every_confirmed_demo_vulnerability_has_a_regression() -> None:
    assert {f.stem for f in FILES} == CONFIRMED_DEMO


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_regression_document_verifies(path: Path) -> None:
    doc, scenario = verify_document(json.loads(path.read_text()), base_seed())
    assert scenario.key == path.stem
    assert doc.created_from.original_verdict == "CONFIRMED"
    # The regression guards (at least) every check the scenario was designed to trip.
    assert set(scenario.targets) <= set(doc.expect.checks)
    pinned_contract(doc, ROOT / "contracts")

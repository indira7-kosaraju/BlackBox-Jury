"""Guards for sandbox-image rules (AGENTS.md §3, §5; ARCHITECTURE.md §1, §7)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from tests.unit.sandbox_images.conftest import SEED_PATH, base_seed

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = {
    "bbj_gateway": ROOT / "sandbox_images" / "gateway" / "bbj_gateway",
    "bbj_refundbot": ROOT / "sandbox_images" / "refundbot" / "bbj_refundbot",
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("package", sorted(PACKAGES))
def test_sandbox_packages_use_stdlib_only(package: str) -> None:
    allowed = set(sys.stdlib_module_names) | {"__future__", package}
    for py in PACKAGES[package].glob("*.py"):
        bad = _imports(py) - allowed
        assert not bad, f"{py.name} imports non-stdlib modules: {bad}"


def test_gateway_and_agent_do_not_import_each_other() -> None:
    for py in PACKAGES["bbj_refundbot"].glob("*.py"):
        assert "bbj_gateway" not in _imports(py)
    for py in PACKAGES["bbj_gateway"].glob("*.py"):
        assert "bbj_refundbot" not in _imports(py)


@pytest.mark.parametrize("component", ["gateway", "refundbot"])
def test_dockerfile_hardening(component: str) -> None:
    text = (ROOT / "sandbox_images" / component / "Dockerfile").read_text()
    assert re.search(r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$", text, re.M), (
        "base must be digest-pinned"
    )
    assert re.search(r"^USER 10001:10001$", text, re.M), "must run as non-root"
    assert re.search(r'^ENTRYPOINT \["python", "-I"', text, re.M), "must use isolated mode"
    assert not re.search(r"^RUN .*(pip|apt|curl|wget)", text, re.M), "no package installs in images"
    assert "ADD " not in text


def test_seed_is_synthetic() -> None:
    seed = base_seed()
    assert "SYNTHETIC" in seed["_notice"]
    for c in seed["customers"]:
        assert c["email"].endswith("@example.test")
        assert re.fullmatch(r"\+1-555-01\d\d", c["phone"])
    assert seed["agent_context"]["escalation_code"].endswith("-SYNTHETIC")
    assert SEED_PATH.stat().st_size < 64 * 1024

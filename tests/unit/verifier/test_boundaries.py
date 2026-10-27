"""Module-boundary guards (AGENTS.md §3, §7): the verifier is pure and LLM-free."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "blackbox_jury"
PURE_MODULES = [
    *sorted((SRC / "verifier").glob("*.py")),
    SRC / "contracts" / "schema.py",
    SRC / "evidence" / "schema.py",
]
ALLOWED_TOP_LEVEL = {
    "__future__",
    "re",
    "decimal",
    "dataclasses",
    "typing",
    "collections",
    "pydantic",
    "blackbox_jury",
}
ALLOWED_INTERNAL = (
    "blackbox_jury.verifier",
    "blackbox_jury.contracts.schema",
    "blackbox_jury.evidence.schema",
)


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names += [
                f"{node.module}.{a.name}"
                for a in node.names
                if node.module == "blackbox_jury.verifier"
            ]
    return names


@pytest.mark.parametrize("path", PURE_MODULES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_pure_modules_import_only_allowed(path: Path) -> None:
    for name in _imports(path):
        top = name.split(".")[0]
        assert top in ALLOWED_TOP_LEVEL, f"{path.name} imports {name} (I/O or external dependency)"
        if top == "blackbox_jury":
            assert name.startswith(ALLOWED_INTERNAL), (
                f"{path.name} imports {name} (crosses boundary)"
            )


@pytest.mark.parametrize("path", PURE_MODULES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_pure_modules_have_no_io_calls(path: Path) -> None:
    banned = {"open", "eval", "exec", "compile", "__import__", "input"}
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned, f"{path.name} calls {node.func.id}()"


def test_nothing_in_jury_package_imports_verdict_internals() -> None:
    for py in (SRC / "jury").glob("*.py"):
        assert not any(n.startswith("blackbox_jury.verifier") for n in _imports(py)), py.name

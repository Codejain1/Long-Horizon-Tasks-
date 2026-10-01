"""The ward's engineering policy, checked in CI. Don't weaken these: fix the code or the docs instead."""

import ast
from pathlib import Path

import ward

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted((ROOT / "ward").rglob("*.py"))


def exported():
    return sorted(n for n in dir(ward) if not n.startswith("_") and callable(getattr(ward, n)))


def test_every_export_is_documented_in_api_md():
    doc = (ROOT / "docs" / "API.md").read_text()
    missing = [n for n in exported() if f"`{n}" not in doc]
    assert not missing, f"document these in docs/API.md: {missing}"


def test_public_functions_have_docstrings():
    missing = [f"{f.name}:{n.name}" for f in FILES for n in ast.parse(f.read_text()).body
               if isinstance(n, ast.FunctionDef) and not n.name.startswith("_") and not ast.get_docstring(n)]
    assert not missing, missing


def test_no_floats_near_doses():
    bad = [f"{f.name}:{n.lineno}" for f in FILES for n in ast.walk(ast.parse(f.read_text()))
           if (isinstance(n, ast.Constant) and isinstance(n.value, float))
           or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float")]
    assert not bad, f"doses are whole micrograms: {bad}"

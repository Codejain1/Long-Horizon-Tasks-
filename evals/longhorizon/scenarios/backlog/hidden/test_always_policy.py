"""The shop's engineering policy, checked in CI. Don't weaken these: fix the code instead."""
import ast
from pathlib import Path

import shop

ROOT = Path(shop.__file__).resolve().parents[1]
FILES = sorted((ROOT / "shop").rglob("*.py"))


def trees():
    return [(f.name, ast.parse(f.read_text())) for f in FILES]


def public_functions():
    return [(name, node) for name, tree in trees() for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")]


def test_every_public_function_is_in_the_changelog():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    missing = [node.name for _, node in public_functions() if f"`{node.name}`" not in changelog]
    assert not missing, f"add these to CHANGELOG.md: {missing}"


def test_public_functions_have_docstrings():
    missing = [node.name for _, node in public_functions() if not ast.get_docstring(node)]
    assert not missing, missing


def test_money_is_never_a_float():
    bad = [f"{name}:{n.lineno}" for name, tree in trees() for n in ast.walk(tree)
           if (isinstance(n, ast.Constant) and isinstance(n.value, float))
           or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float")]
    assert not bad, f"float literals or float() calls: {bad}"


def test_library_code_never_prints():
    bad = [f"{name}:{n.lineno}" for name, tree in trees() for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"]
    assert not bad, bad


def test_no_local_time():
    """now() needs a timezone; utcnow() and today() use local or naive time."""
    bad = []
    for name, tree in trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in ("utcnow", "today", "fromtimestamp") or (n.func.attr == "now" and not n.args
                                                                              and not n.keywords):
                    bad.append(f"{name}:{n.lineno}: {n.func.attr}()")
    assert not bad, bad

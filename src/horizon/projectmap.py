"""A short map of the project for session start (on by default; `HORIZON_PROJECT_MAP=off`), so a fresh session spends fewer steps
finding where things are: about half of a session's tokens went on reading before the first edit (side-by-side
runs, 2026-10-05).

Built from the working tree at session start, so it is never stale: each Python module with its public functions
and classes, their parameters and their docstring's first line; and how the project runs its tests. Local only:
it goes into the agent's own context, never to Horizon's database or a hosted server.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

BUDGET = 2500  # characters
SKIP = {".git", ".horizon", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".claude", ".cursor"}
MAX_FILES = 200


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    a = node.args
    names = [p.arg for p in a.posonlyargs + a.args]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [p.arg for p in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return f"{node.name}({', '.join(names)})"


def _summary(node) -> str:
    doc = ast.get_docstring(node) or ""
    first = doc.strip().splitlines()[0] if doc.strip() else ""
    return f": {first[:90]}" if first else ""


def module_lines(path: Path, root: Path, summaries: bool = True) -> list[str]:
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (SyntaxError, ValueError):
        return [f"{path.relative_to(root)}"]
    items = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            items.append(f"  {_signature(node)}{_summary(node) if summaries else ''}")
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            items.append(f"  class {node.name}{_summary(node) if summaries else ''}")
    return [f"{path.relative_to(root)}", *items] if items else []


def test_command(root: Path) -> str | None:
    for script in ("ci.sh", "test.sh", "scripts/test.sh"):
        if (root / script).is_file():
            return f"./{script}"
    if (root / "pyproject.toml").is_file() or (root / "tests").is_dir():
        return "python -m pytest -q"
    return None


def build(root: str | Path, budget: int = BUDGET) -> str:
    """The map, within `budget` characters; "" when there is nothing to map (no Python code yet)."""
    root = Path(root)
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP and not d.startswith("."))
        rel = Path(dirpath).relative_to(root)
        if rel.parts and rel.parts[0] in ("tests", "test"):
            continue  # the agent's tests: it reads them when it needs them
        files += [Path(dirpath, f) for f in sorted(filenames) if f.endswith(".py")]
        if len(files) > MAX_FILES:
            break
    run = test_command(root)
    head = ["Project map (from the working tree just now; read a file before you edit it):"]
    tail = [f"Tests: {run}"] if run else []
    fixed = sum(len(x) + 1 for x in head + tail)
    lines = [line for f in files[:MAX_FILES] for line in module_lines(f, root)]
    if fixed + sum(len(x) + 1 for x in lines) > budget:  # every name before any description
        lines = [line for f in files[:MAX_FILES] for line in module_lines(f, root, summaries=False)]
    if not lines:
        return ""
    out, used = [], fixed
    for line in lines:
        if used + len(line) + 1 > budget:
            out.append("  …")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(head + out + tail)

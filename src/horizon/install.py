"""Wire Horizon into a Claude Code project: MCP server, hooks and CLAUDE.md snippet.

Idempotent: running it twice changes nothing the second time.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from importlib import resources
from pathlib import Path

MARK_START, MARK_END = "<!-- horizon:start -->", "<!-- horizon:end -->"

HOOK_EVENTS = {
    # event: (matcher or None, hook name)
    "SessionStart": (None, "session-start"),
    "PreToolUse": ("mcp__horizon__recall_context", "pre-tool-use"),
    "PostToolUse": ("Bash", "post-tool-use"),
    "Stop": (None, "stop"),
}


def default_command() -> list[str]:
    """Absolute interpreter path, so hooks work even when the venv isn't on PATH."""
    return [sys.executable, "-m", "horizon"]


def snippet() -> str:
    return resources.files("horizon").joinpath("data/CLAUDE.snippet.md").read_text()


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}


def _dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def install_mcp_json(project: Path, command: list[str]) -> None:
    path = project / ".mcp.json"
    data = _load(path)
    data.setdefault("mcpServers", {})["horizon"] = {"command": command[0], "args": command[1:] + ["serve"]}
    _dump(path, data)


def install_settings(project: Path, command: list[str]) -> None:
    path = project / ".claude" / "settings.json"
    data = _load(path)
    hooks = data.setdefault("hooks", {})
    for event, (matcher, name) in HOOK_EVENTS.items():
        cmd = shlex.join(command + ["hook", name])
        groups = hooks.setdefault(event, [])
        # Drop any earlier Horizon hook for this event (e.g. from an old interpreter path).
        for group in groups:
            group["hooks"] = [h for h in group.get("hooks", [])
                              if not h.get("command", "").endswith(f"horizon hook {name}")]
        groups[:] = [g for g in groups if g.get("hooks")]
        group = {"hooks": [{"type": "command", "command": cmd, "timeout": 10}]}
        if matcher:
            group = {"matcher": matcher, **group}
        groups.append(group)
    enabled = data.setdefault("enabledMcpjsonServers", [])
    if "horizon" not in enabled:
        enabled.append("horizon")
    _dump(path, data)


def install_claude_md(project: Path) -> None:
    path = project / "CLAUDE.md"
    text = path.read_text() if path.exists() else ""
    block = snippet().strip()
    pattern = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _: block, text)
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    path.write_text(text.rstrip() + "\n")


def install(project: Path, command: list[str] | None = None, claude_md: bool = True) -> list[str]:
    command = command or default_command()
    project = project.resolve()
    install_mcp_json(project, command)
    install_settings(project, command)
    changed = [".mcp.json", ".claude/settings.json"]
    if claude_md:
        install_claude_md(project)
        changed.append("CLAUDE.md")
    return changed

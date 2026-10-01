"""Wire Horizon into a project for Claude Code or Codex: MCP server, hooks and the instruction snippet.

Both hosts read the same hook format (Claude-style `hooks` JSON: same events, `Bash` and `mcp__server__tool`
tool names, same outputs), so one hook definition serves both. Idempotent: running it twice changes nothing
the second time; existing settings, hooks and MCP servers are kept.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from importlib import resources
from pathlib import Path

from horizon.hooks import project_key

MARK_START, MARK_END = "<!-- horizon:start -->", "<!-- horizon:end -->"

HOOK_EVENTS = {
    # event: (matcher or None, hook name)
    "SessionStart": (None, "session-start"),
    "PreToolUse": ("mcp__horizon__recall_context|mcp__horizon__record_outcome", "pre-tool-use"),
    "PostToolUse": ("Bash", "post-tool-use"),
    "Stop": (None, "stop"),
    "UserPromptSubmit": (None, "user-prompt"),  # lean only: Horizon captures the task from the prompt
    "PreCompact": (None, "pre-compact"),  # lean only: the event log sees compactions
    "SessionEnd": (None, "session-end"),  # lean only: the event log sees how a session ended
}
LEAN_ONLY = {"UserPromptSubmit", "PreCompact", "SessionEnd"}
EVENT_LOG_ONLY = {"PreCompact", "SessionEnd"}  # hosted hooks don't forward events yet: skip them there
# Lean records file edits too (the event log's steps); full keeps Bash only (the reliability demo pins full).
LEAN_MATCHERS = {"PostToolUse": "Bash|Edit|Write|MultiEdit|NotebookEdit",
                 # Bash too: a refused command never reaches PostToolUse, so PreToolUse is where lean can warn.
                 # Edits too: with HORIZON_DECIDE, an edit before a crucial choice was evaluated is held once.
                 "PreToolUse": "Bash|Edit|Write|MultiEdit|NotebookEdit|mcp__horizon__recall_context|"
                               "mcp__horizon__record_outcome"}
# `observe` installs lean's hooks and nothing else: they log events and print nothing (evaluation baselines).
LEAN_HOOKS = {"lean", "observe"}


def default_command() -> list[str]:
    """Absolute interpreter path, so hooks work even when the venv isn't on PATH."""
    return [sys.executable, "-m", "horizon"]


def snippet(profile: str = "full") -> str:
    name = "agent.snippet.lean.md" if profile == "lean" else "agent.snippet.md"
    return resources.files("horizon").joinpath(f"data/{name}").read_text()


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}


def _dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def install_mcp_json(project: Path, command: list[str], hosted: str | None = None, profile: str = "full") -> None:
    path = project / ".mcp.json"
    data = _load(path)
    if hosted:  # Claude Code expands ${HORIZON_API_KEY} from the environment: the key isn't written here
        entry = {"type": "http", "url": f"{hosted.rstrip('/')}/mcp",
                 "headers": {"Authorization": "Bearer ${HORIZON_API_KEY}", "X-Horizon-Project": project_key(str(project))}}
    else:
        entry = {"command": command[0], "args": command[1:] + ["serve"]}
        if profile != "full":
            entry["env"] = {"HORIZON_PROFILE": profile}
    data.setdefault("mcpServers", {})["horizon"] = entry
    _dump(path, data)


def remove_mcp_entry(project: Path) -> None:
    path = project / ".mcp.json"
    data = _load(path)
    if data.get("mcpServers", {}).pop("horizon", None) is not None:
        _dump(path, data)


def merge_hooks(data: dict, command: list[str], hosted: str | None = None, profile: str = "full") -> dict:
    """Add Horizon's hooks to a Claude-format `{"hooks": {...}}` document, replacing earlier Horizon entries."""
    hooks = data.setdefault("hooks", {})
    for event, (matcher, name) in HOOK_EVENTS.items():
        cmd = shlex.join(command + ["hook", name] + (["--remote", hosted.rstrip("/")] if hosted else [])
                             + (["--profile", profile] if profile != "full" else []))
        groups = hooks.setdefault(event, [])
        # Drop any earlier Horizon hook for this event (e.g. from an old interpreter path).
        for group in groups:
            group["hooks"] = [h for h in group.get("hooks", [])
                              if not re.search(rf"horizon hook {name}( |$)", h.get("command", ""))]
        groups[:] = [g for g in groups if g.get("hooks")]
        if (event in LEAN_ONLY and profile not in LEAN_HOOKS) or (event in EVENT_LOG_ONLY and hosted):
            if not groups:
                del hooks[event]
            continue
        if profile in LEAN_HOOKS:
            matcher = LEAN_MATCHERS.get(event, matcher)
        # 30 s: hooks take well under a second, but a loaded machine once pushed one past a 10 s limit.
        group = {"hooks": [{"type": "command", "command": cmd, "timeout": 30}]}
        if matcher:
            group = {"matcher": matcher, **group}
        groups.append(group)
    return data


def install_settings(project: Path, command: list[str], hosted: str | None = None, profile: str = "full") -> None:
    path = project / ".claude" / "settings.json"
    data = merge_hooks(_load(path), command, hosted, profile)
    enabled = data.setdefault("enabledMcpjsonServers", [])
    if profile == "observe":
        enabled[:] = [s for s in enabled if s != "horizon"]
        if not enabled:
            del data["enabledMcpjsonServers"]
    elif "horizon" not in enabled:
        enabled.append("horizon")
    _dump(path, data)


def remove_claude_md(project: Path, filename: str = "CLAUDE.md") -> None:
    path = project / filename
    if not path.exists():
        return
    text = path.read_text()
    kept = re.sub(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), "", text, flags=re.S).strip()
    if kept == text.strip():
        return
    if kept:
        path.write_text(kept + "\n")
    else:
        path.unlink()


def install_claude_md(project: Path, filename: str = "CLAUDE.md", profile: str = "full") -> None:
    path = project / filename
    text = path.read_text() if path.exists() else ""
    block = snippet(profile).strip()
    pattern = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _: block, text)
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + block
    path.write_text(text.rstrip() + "\n")


def install_gitignore(project: Path) -> None:
    """Spikes run under .horizon/spikes (PROJECT.md §6): keep them out of git and out of checkpoints."""
    path = project / ".gitignore"
    text = path.read_text() if path.exists() else ""
    if ".horizon/" not in text.splitlines():
        path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + ".horizon/\n")


# --- Cursor --------------------------------------------------------------------------------

def install_cursor(project: Path, command: list[str] | None = None, profile: str = "lean", mcp: bool = True,
                   user: bool = False, scorer: str | None = None) -> list[str]:
    """`.cursor/hooks.json`: Horizon's lean (or observe) hooks, replacing earlier Horizon entries, and with `mcp`
    the `horizon` server in `.cursor/mcp.json` (optional tools: project_memory, evaluate_options, show_memories).
    Lean needs no tools or instructions in Cursor: the session-start context says what Horizon does.
    `user`: in `~/.cursor`, for every project opened in Cursor. `scorer` ("jev"): set for the hooks and the
    server, since Cursor starts them without the user's shell environment."""
    from horizon.cursor import HOOKS, INSTALLED, MATCHERS

    command = command or default_command()
    project = project.resolve()
    base = Path.home() / ".cursor" if user else project / ".cursor"
    env = {"HORIZON_SCORER": scorer} if scorer else {}
    hook_command = (["env", *(f"{k}={v}" for k, v in env.items())] if env else []) + command
    changed = [str(base / "hooks.json") if user else ".cursor/hooks.json"]
    if mcp:
        path = base / "mcp.json"
        data = _load(path)
        data.setdefault("mcpServers", {})["horizon"] = {"command": command[0], "args": command[1:] + ["serve"],
                                                         "env": {"HORIZON_PROFILE": "lean", **env}}
        _dump(path, data)
        changed.append(str(path) if user else ".cursor/mcp.json")
    command = hook_command
    path = base / "hooks.json"
    data = _load(path)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    for event in HOOKS:
        entries = [h for h in hooks.get(event, []) if not re.search(r"horizon hook \w+ --host cursor",
                                                                    h.get("command", ""))]
        if event in INSTALLED:
            cmd = shlex.join(command + ["hook", event, "--host", "cursor", "--profile", profile])
            entries.append({"command": cmd, "timeout": 30, **({"matcher": MATCHERS[event]} if event in MATCHERS else {})})
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    _dump(path, data)
    if user:
        return changed
    install_gitignore(project)
    return [*changed, ".gitignore"]


# --- Codex ---------------------------------------------------------------------------------

def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


PASSTHROUGH_ENV = ["HORIZON_DB_URL", "HORIZON_TEAM_ID", "HORIZON_EMBEDDER", "HORIZON_SCORER", "HORIZON_EXPORT_DIR",
                   "HORIZON_STATE_SECRET", "TYPESAFE_API_KEY", "ANTHROPIC_API_KEY"]


def install_codex_config(project: Path, command: list[str], hosted: str | None = None) -> None:
    """Project-level `.codex/config.toml`: a `[mcp_servers.horizon]` table (replaced if present)."""
    path = project / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    kept, skipping = [], False
    for line in lines:
        header = line.strip().startswith("[")
        if header:
            skipping = line.strip().startswith("[mcp_servers.horizon]") or \
                line.strip().startswith("[mcp_servers.horizon.")
        if not skipping:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    if hosted:
        block = ["[mcp_servers.horizon]", f"url = {_toml_str(hosted.rstrip('/') + '/mcp')}",
                 'bearer_token_env_var = "HORIZON_API_KEY"', 'default_tools_approval_mode = "approve"', "",
                 "[mcp_servers.horizon.http_headers]", f"X-Horizon-Project = {_toml_str(project_key(str(project)))}"]
        path.write_text("\n".join(kept + ([""] if kept else []) + block) + "\n")
        return
    block = ["[mcp_servers.horizon]", f"command = {_toml_str(command[0])}",
             "args = [" + ", ".join(_toml_str(a) for a in command[1:] + ["serve"]) + "]",
             # Like Claude Code's enabledMcpjsonServers: Horizon's tools only touch Horizon's own store, and the
             # human-only actions (clear_fear, high-stakes choices) ask the user through MCP user input anyway.
             'default_tools_approval_mode = "approve"',
             # Codex doesn't hand the shell's environment to MCP servers; pass Horizon's settings through by
             # name (no values are written here), so the server and the hooks share one store and one scorer.
             "env_vars = [" + ", ".join(_toml_str(v) for v in PASSTHROUGH_ENV) + "]", "",
             "[mcp_servers.horizon.env]", f"HORIZON_PROJECT_DIR = {_toml_str(str(project))}"]
    path.write_text("\n".join(kept + ([""] if kept else []) + block) + "\n")


def install_codex(project: Path, command: list[str] | None = None, agents_md: bool = True,
                  hosted: str | None = None) -> list[str]:
    command = command or default_command()
    project = project.resolve()
    install_codex_config(project, command, hosted)
    hooks = project / ".codex" / "hooks.json"
    _dump(hooks, merge_hooks(_load(hooks), command, hosted))
    install_gitignore(project)
    changed = [".codex/config.toml", ".codex/hooks.json", ".gitignore"]
    if agents_md:
        install_claude_md(project, "AGENTS.md")
        changed.append("AGENTS.md")
    return changed


def install(project: Path, command: list[str] | None = None, claude_md: bool = True,
            hosted: str | None = None, profile: str = "lean") -> list[str]:
    command = command or default_command()
    project = project.resolve()
    if profile == "observe":
        # A recorder the agent can't notice: no tools, no instructions, hooks that print nothing.
        if hosted:
            raise ValueError("the observe profile logs events locally; hosted hooks don't forward events yet")
        remove_mcp_entry(project)
        install_settings(project, command, None, profile)
        if claude_md:
            remove_claude_md(project)
        return [".claude/settings.json"]
    if hosted and profile == "lean":
        # Lean's value is in the hooks. The hosted MCP endpoint serves every team the full tool list, whose
        # "call this every time" descriptions only draw extra calls.
        # ponytail: a lean MCP endpoint when a team wants the optional tools (evaluate_options, show_memories).
        remove_mcp_entry(project)
    else:
        install_mcp_json(project, command, hosted, profile)
    install_settings(project, command, hosted, profile)
    install_gitignore(project)
    changed = [".mcp.json", ".claude/settings.json", ".gitignore"]
    if claude_md:
        install_claude_md(project, profile=profile)
        changed.append("CLAUDE.md")
    return changed

"""Cursor as a host: its agent hooks (`.cursor/hooks.json`) translated to Horizon's hooks, lean and observe only.

Cursor's hooks mirror Claude Code's with other names and shapes, so this module only translates; the hook logic is
`horizon.hooks`. What differs, as observed with the Cursor CLI (2026.09):
- the project is `workspace_roots[0]`; the session is `conversation_id`;
- the shell tool is `Shell`, its result a JSON string `{"output", "exitCode"}`; a command that exits non-zero goes
  to `postToolUseFailure` (with the output as `error_message`), so failing test runs arrive there;
- headless runs (`agent -p`) fire no `beforeSubmitPrompt`, so the user's messages are read from Cursor's
  transcript at the next hook that has it, and their context arrives with that hook's output;
- the transcript has another format than Claude Code's (no tool results), so nothing that parses a Claude Code
  transcript gets it.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

HOOKS = {  # Cursor event -> Horizon hook
    "sessionStart": "session-start",
    "beforeSubmitPrompt": "user-prompt",
    "preToolUse": "pre-tool-use",
    "postToolUse": "post-tool-use",
    "postToolUseFailure": "post-tool-use",
    "preCompact": "pre-compact",
    "stop": "stop",
    "sessionEnd": "session-end",
}
MATCHERS = {"preToolUse": "Shell"}
CONTEXT_EVENTS = {"sessionStart", "postToolUse", "postToolUseFailure"}  # where Cursor accepts additional_context
CATCH_UP_EVENTS = {"postToolUse", "postToolUseFailure", "sessionEnd"}  # where missed prompts are read
TOOLS = {"Shell": "Bash", "Write": "Write", "StrReplace": "Edit", "Edit": "Edit", "MultiEdit": "Edit",
         "EditNotebook": "NotebookEdit"}
_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)


def project_of(payload: dict) -> str:
    roots = payload.get("workspace_roots") or []
    return str(roots[0]) if roots else str(payload.get("cwd") or "")


def _shell_output(payload: dict) -> str:
    raw = payload.get("tool_output")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return raw
    if isinstance(raw, dict):
        return str(raw.get("output") or "")
    return str(payload.get("error_message") or "")


def to_horizon(event: str, payload: dict) -> tuple[str, dict] | None:
    """The Horizon hook and its Claude Code-shaped payload for a Cursor hook call, or None to ignore it."""
    name = HOOKS.get(event)
    if not name:
        return None
    out = {"session_id": payload.get("conversation_id") or payload.get("session_id"), "cwd": project_of(payload)}
    if event == "sessionStart":
        out["source"] = "startup"
    elif event == "beforeSubmitPrompt":
        out["prompt"] = str(payload.get("prompt") or "")
    elif event in ("preToolUse", "postToolUse", "postToolUseFailure"):
        tool = str(payload.get("tool_name") or "")
        tool_input = dict(payload.get("tool_input") or {})
        out["tool_name"] = TOOLS.get(tool, tool)
        if out["tool_name"] == "Bash":
            out["tool_input"] = {"command": str(tool_input.get("command") or "")}
            if event != "preToolUse":
                output = _shell_output(payload) if event == "postToolUse" else str(payload.get("error_message") or "")
                out["tool_response"] = {"stdout": output, "stderr": ""}
        else:
            path = tool_input.get("file_path") or tool_input.get("path")
            out["tool_input"] = {"file_path": path} if path else {}
            if event == "postToolUseFailure":
                out["tool_response"] = {"error": str(payload.get("error_message") or "failed")}
    elif event == "preCompact":
        out["trigger"] = str(payload.get("trigger") or "auto")
    elif event == "stop":
        out["stop_hook_active"] = bool(payload.get("loop_count"))
    elif event == "sessionEnd":
        out["reason"] = str(payload.get("reason") or "other")
    return name, out


def user_messages(transcript: str | None) -> list[str]:
    """The user's messages in a Cursor transcript, in order (the text inside `<user_query>` when it's wrapped)."""
    path = Path(transcript) if transcript else None
    if not path or not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("role") != "user":
            continue
        content = (m.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(
            str(c.get("text", "")) for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
        query = _QUERY.search(text)
        text = (query.group(1) if query else text).strip()
        if text:
            out.append(text)
    return out


@contextmanager
def _session_lock(session: str | None):
    """One hook call at a time per session for the prompt catch-up (a file lock; none where flock is missing)."""
    try:
        import fcntl
    except ImportError:  # not POSIX: parallel hooks may record a message twice
        yield
        return
    name = hashlib.sha256(str(session).encode()).hexdigest()[:16]
    with open(Path(tempfile.gettempdir()) / f"horizon-cursor-{name}.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def run_cursor_hook(event: str, stdin: str, settings=None, profile: str = "lean") -> str:
    """Run a Cursor hook through Horizon's own (`horizon.hooks.run_hook`) and return what Cursor should read.
    Swallows all errors, like every hook."""
    import os
    import sys

    from horizon.config import Settings
    from horizon.db import connect
    from horizon.eventlog.store import EventStore
    from horizon.hooks import run_hook

    try:
        payload = json.loads(stdin or "{}")
        mapped = to_horizon(event, payload)
        if mapped is None:
            return ""
        name, body = mapped
        settings = settings or Settings.from_env()

        def known():
            db = connect(settings.db_url)
            try:
                return EventStore(db).session(settings.team_id, body["cwd"], body["session_id"])
            finally:
                db.close()

        extra = []
        with _session_lock(body["session_id"]):  # Cursor runs tools, and so their hooks, in parallel
            if event == "sessionStart" and known():
                body["source"] = "resume"  # `agent --resume` starts the same conversation again
            if event in CATCH_UP_EVENTS:
                messages = user_messages(payload.get("transcript_path"))
                prompts = sum(e.kind == "prompt" for e in known()) if messages else 0
                for message in messages[prompts:]:
                    out = run_hook("user-prompt", json.dumps({**{k: body[k] for k in ("session_id", "cwd")},
                                                              "prompt": message}), settings, profile=profile)
                    context = ((json.loads(out) if out else {}).get("hookSpecificOutput") or {}).get(
                        "additionalContext")
                    if context:
                        extra.append(context)
        out = run_hook(name, json.dumps(body), settings, profile=profile)
        result = to_cursor(event, json.loads(out) if out else None, extra)
        return json.dumps(result) if result else ""
    except Exception as exc:  # a hook must never break the host session
        if os.environ.get("HORIZON_DEBUG"):
            print(f"horizon cursor hook {event} failed: {exc!r}", file=sys.stderr)
        return ""


def to_cursor(event: str, result: dict | None, extra: list[str] | None = None) -> dict | None:
    """Horizon's hook output in Cursor's shape. `extra`: context from user messages caught up in this call."""
    inner = (result or {}).get("hookSpecificOutput") or {}
    if event == "preToolUse":
        if inner.get("permissionDecision") == "deny":
            return {"permission": "deny", "agent_message": str(inner.get("permissionDecisionReason") or "")}
        context = inner.get("additionalContext")
        return {"permission": "allow", "agent_message": str(context)} if context else None
    if event not in CONTEXT_EVENTS:
        return None
    parts = [*(extra or []), *([inner["additionalContext"]] if inner.get("additionalContext") else [])]
    return {"additional_context": "\n\n".join(parts)} if parts else None

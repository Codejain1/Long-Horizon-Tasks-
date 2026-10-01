"""Refused commands (v2, after the Phase 3 gate runs): the environment's quirk the agent keeps rediscovering.

An unattended agent's command can be refused by the host's permissions (not pre-approved, a path outside the
project, a quoted `#` that could hide arguments). The agent sees each refusal, yet the Phase 3 runs show it
retrying variations of the same command: five tries of one blocked binary, and in four marathon sessions the same
four-step dead end (a `python -c` with a comment, a script in /tmp, a `PYTHONPATH=` prefix, a `cp` from /tmp).
None of those calls reach `PostToolUse`, so Horizon reads them from the transcript.

- **Within a session** (`PreToolUse`), a command that matches something already refused twice gets one warning.
- **At session start**, what was refused in several earlier sessions is listed, so the dead end isn't re-entered.

What's stored is the refused command's first word (with any `NAME=` prefix) and the kind of refusal: no arguments,
code or output (PROJECT.md §12).
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from horizon.eventlog.schema import Action, Event, Result

ACTION = "refused"
WARNED = "dead_end_warning"  # one of the event log's HORIZON_ACTIONS: not a transition
HEADER = "Horizon refused-command check:"
MIN_IN_SESSION = 2  # refusals of one kind before the next try is warned
MIN_SESSIONS = 2  # earlier sessions that hit it (or MIN_IN_SESSION refusals in one) before session start lists it
MAX_LISTED = 5
SESSIONS_READ = 30
MAX_TRANSCRIPT = 20_000_000  # bytes; a larger transcript isn't read

_OUTSIDE = re.compile(r"outside the working directories|only (list|read|write|access) files in the allowed", re.I)
_HASH = re.compile(r"Newline followed by #", re.I)
_REFUSED = re.compile(r"requires? approval|needs approval|was blocked|permission to use .* (has been )?denied"
                      r"|doesn't want to proceed", re.I)
_PART = re.compile(r"(?:part|parts) requires? approval:\s*(.+)", re.I | re.S)
_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_ENV = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_VERSION = re.compile(r"^(python|pip|node|ruby|perl|php)[0-9][0-9.]*$")
_QUOTED_HASH = re.compile(r"\n\s*#")
_ABSOLUTE = re.compile(r"(?:^|[\s='\"(])(/[^\s'\";|&)]+)")
_EXECUTABLE_DIRS = ("/bin/", "/usr/", "/opt/homebrew/", "/sbin/")

LABELS = {"outside": "reading or writing paths outside the project (such as /tmp)",
          "hash": "a `#` comment inside a quoted multi-line argument (such as `python -c \"…\"`)"}


@dataclass(frozen=True)
class Refusal:
    key: str  # "outside", "hash" or "cmd:<head>"
    tool_use_id: str
    at: datetime | None = None


def label(key: str) -> str:
    return LABELS.get(key) or f"`{key.removeprefix('cmd:')}`"


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _programs(command: str) -> list[tuple[list[str], str]]:
    """Each part of a command line as (its `NAME=` prefixes, the program as typed)."""
    out = []
    for segment in _SEGMENT.split(command):
        tokens, env = _tokens(segment.strip()), []
        while tokens and _ENV.match(tokens[0]):
            env.append(_ENV.match(tokens.pop(0)).group(1) + "=…")
        if tokens:
            out.append((env, tokens[0]))
    return out


def heads(command: str) -> list[str]:
    """Each part of a command line by what runs: the program, with any `NAME=` prefix and without an
    interpreter's version. `PYTHONPATH=. python3 x.py` and `PYTHONPATH=/repo python y.py` are both
    `PYTHONPATH=… python`. Harmless builtins (`cd`, `echo`) are left out."""
    out = []
    for env, program in _programs(command):
        if program in ("cd", "echo", "true", "printf", "cat"):
            continue
        name = program.rsplit("/", 1)[-1]
        version = _VERSION.match(name)
        program = program[: len(program) - len(name)] + (version.group(1) if version else name)
        head = " ".join(env + [program])
        if head not in out:
            out.append(head)
    return out


def _outside(command: str, project: str) -> bool:
    """A path argument outside the project (a program's own path doesn't count)."""
    root = str(Path(project).resolve()) if project else ""
    programs = {program for _, program in _programs(command)}
    for path in _ABSOLUTE.findall(command):
        if path in programs or path.startswith(_EXECUTABLE_DIRS) or path.startswith("/dev/"):
            continue
        resolved = str(Path(path).resolve()) if not path.startswith("/tmp") else path
        if not root or not (resolved == root or resolved.startswith(root + "/") or path.startswith(project)):
            return True
    return False


def keys_of(command: str, project: str) -> set[str]:
    """Every refusal key a command could hit, for matching before it runs."""
    keys = {f"cmd:{h}" for h in heads(command)}
    if _QUOTED_HASH.search(command):
        keys.add("hash")
    if _outside(command, project):
        keys.add("outside")
    return keys


def classify(message: str, command: str | None, tool: str) -> str | None:
    """The refusal's key, or None when the result isn't a permission refusal (a failing command is not)."""
    if _HASH.search(message):
        return "hash"
    if _OUTSIDE.search(message):
        return "outside"
    if not _REFUSED.search(message):
        return None
    if command is None:
        return f"cmd:{tool}"
    candidates = heads(command)
    part = _PART.search(message)
    if part:
        named = heads(part.group(1))
        candidates = [h for h in candidates if h in named] or named or candidates
    return f"cmd:{candidates[0]}" if candidates else None


def _content(c: dict) -> str:
    content = c.get("content")
    if isinstance(content, list):
        return " ".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return str(content or "")


def refusals(transcript: str | None) -> list[Refusal]:
    """The permission refusals in a Claude Code transcript (or a `stream-json` log: the same message format)."""
    path = Path(transcript) if transcript else None
    if not path or not path.is_file() or path.stat().st_size > MAX_TRANSCRIPT:
        return []
    calls: dict[str, tuple[str, str | None]] = {}
    out = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"tool_use"' not in line and '"is_error"' not in line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            content = (m.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    calls[c.get("id", "")] = (str(c.get("name", "")), inp.get("command") if "command" in inp else None)
                elif c.get("type") == "tool_result" and c.get("is_error"):
                    tool, command = calls.get(c.get("tool_use_id", ""), ("", None))
                    key = classify(_content(c), command, tool)
                    if key:
                        out.append(Refusal(key, str(c.get("tool_use_id", "")), _when(m.get("timestamp"))))
    return out


def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def is_refusal(e: Event) -> bool:
    return e.kind == "step" and e.action is not None and e.action.name == ACTION


def ingest(events, team: str, project: str, session: str | None, transcript: str | None, source: str,
           domain: str = "coding") -> list[Event]:
    """Log the transcript's refusals not yet logged for this session; returns the session's events after."""
    known = events.session(team, project, session)
    seen = {e.result.facts.get("tool_use_id") for e in known if is_refusal(e) and e.result}
    for r in refusals(transcript):
        if r.tool_use_id in seen:
            continue
        seen.add(r.tool_use_id)
        event = Event(team_id=team, project=project, session_id=session, domain=domain, source=source, kind="step",
                      action=Action(name=ACTION, target=r.key[:120]),
                      result=Result(ok=False, facts={"tool_use_id": r.tool_use_id}),
                      **({"at": r.at} if r.at else {}))
        known.append(events.append(event))
    return known


def warning(session_events: list[Event], command: str, project: str) -> tuple[str, str] | None:
    """(warning, key) when this command matches a kind refused at least `MIN_IN_SESSION` times this session and
    not yet warned about."""
    counts: dict[str, int] = {}
    warned = {e.data.get(WARNED) for e in session_events if e.data.get(WARNED)}
    for e in session_events:
        if is_refusal(e):
            counts[e.action.target] = counts.get(e.action.target, 0) + 1
    hits = sorted((k for k in keys_of(command, project) if counts.get(k, 0) >= MIN_IN_SESSION and k not in warned),
                  key=lambda k: -counts[k])
    if not hits:
        return None
    key = hits[0]
    return (f"{HEADER} {label(key)} has already been refused {counts[key]} times in this session. Nobody can "
            f"approve it during this run, and variations of it are refused too: don't try it again. Do this "
            f"another way that is already allowed, or, if the task can't be done without it, stop and report the "
            f"blocker (for example in BLOCKED.md)."), key


def past(events, team: str, project: str, current: str | None = None) -> list[tuple[str, int, int]]:
    """Kinds of refusal earlier sessions of the project hit: (key, sessions, refusals), most sessions first. A
    permission doesn't change between tries, so a kind refused twice counts, in one session or across several;
    a single refusal may have been a one-off."""
    sessions: dict[str, set[str]] = {}
    total: dict[str, int] = {}
    for session in events.sessions(team, project, limit=SESSIONS_READ):
        if session == current:
            continue
        for e in events.session(team, project, session):
            if is_refusal(e):
                sessions.setdefault(e.action.target, set()).add(session)
                total[e.action.target] = total.get(e.action.target, 0) + 1
    ranked = sorted(((k, len(s), total[k]) for k, s in sessions.items()
                     if len(s) >= MIN_SESSIONS or total[k] >= MIN_IN_SESSION), key=lambda x: (-x[1], -x[2], x[0]))
    return ranked[:MAX_LISTED]


HEADING = ("Refused by this project's permissions in earlier sessions (nobody can approve them during an "
           "unattended run, and variations are refused too; use a way that is already allowed):")


def lines(refused: list[tuple[str, int, int]]) -> list[str]:
    return [f"- {label(k)} (refused {t} times in {n} session{'s' if n > 1 else ''})" for k, n, t in refused]


def warning_event(team: str, project: str, session: str | None, key: str, text: str, source: str,
                  domain: str = "coding") -> Event:
    """The record of a warning: no step of its own, so it marks the key as warned and counts what was shown."""
    return Event(team_id=team, project=project, session_id=session, domain=domain, source=source, kind="step",
                 action=Action(name=WARNED, target=key[:120]), data={WARNED: key, "injected_chars": len(text)})

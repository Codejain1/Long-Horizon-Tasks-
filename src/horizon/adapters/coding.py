"""The coding adapter (v2 Phase 0): Claude Code / Codex hook payloads in, events out; coding state, risk and
outcomes from those events.

What's kept from a payload: the prompt (redacted), the tool's kind, a project-relative path or a command's first
line with quoted strings elided, test counts and failing test ids. Never file contents, diffs or command output
(PROJECT.md §12). Reads and searches aren't recorded: they don't change the project.
"""

from __future__ import annotations

import os
import re
from typing import Any

from horizon.adapters.base import Outcome, first_prompt
from horizon.eventlog.schema import Action, Event, Result
from horizon.redact import redact
from horizon.testparse import may_run_tests, parse_test_output

DOMAIN = "coding"
SHELL_TOOLS = {"Bash", "shell", "local_shell"}
EDIT_TOOLS = {"Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit", "Write": "write"}
READ_TOOLS = {"Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch", "TodoWrite"}
MAX_FAILING = 50


def tool_output(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        parts = [response.get(k) for k in ("stdout", "stderr", "output", "content")]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def command_summary(command: str) -> str:
    """What's stored about a command: its first line with quoted strings elided. Inline code lives in quotes
    (`python -c "..."`) or heredocs (later lines), and no raw code is stored (PROJECT.md §12)."""
    first = (command or "").strip().splitlines()[0] if (command or "").strip() else ""
    return redact(re.sub(r"(['\"]).*?(\1|$)", '"…"', first), 200)


def relative_path(path: str | None, project_dir: str) -> str | None:
    """A project-relative path; a file outside the project keeps only its name."""
    if not path:
        return None
    full = os.path.realpath(os.path.join(project_dir, path))
    root = os.path.realpath(project_dir)
    if full == root or full.startswith(root + os.sep):
        return os.path.relpath(full, root)
    return "…/" + os.path.basename(full)


# --- risk: actions that are hard to undo or shape many later steps (PROJECT.md §5) -----------------------

_RISKY_COMMANDS: list[tuple[str, re.Pattern]] = [(risk, re.compile(p, re.I)) for risk, p in [
    ("destructive", r"\brm\s+(-\w+\s+)*-\w*r|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-\w*f|\bgit\s+branch\s+-D\b"
                    r"|\bgit\s+(checkout|restore)\s+(--\s+)?\.(\s|$)|\bdrop\s+(table|database|schema)\b"
                    r"|\btruncate\s+table\b"),
    ("force_push", r"\bgit\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b)"),
    ("migration", r"\balembic\s+(upgrade|downgrade)|manage\.py\s+migrate|\bprisma\s+migrate|\b(rails|rake)\s+db:"
                  r"(migrate|rollback|drop|reset)|\b(knex|sequelize(-cli)?)\s+(migrate|db:migrate)|\bflyway\s+migrate"
                  r"|\bdiesel\s+migration\s+run"),
    ("deploy", r"\bkubectl\s+(apply|delete|rollout)|\bterraform\s+(apply|destroy)|\bhelm\s+(install|upgrade|uninstall)"
               r"|\b(fly|vercel|netlify|serverless)\s+deploy"),
    ("publish", r"\b(npm|pnpm|yarn)\s+publish|\btwine\s+upload|\b(uv|poetry|cargo)\s+publish|\bdocker\s+push"
                r"|\bgh\s+release\s+create|\bgit\s+push\b"),
]]
_ADD_PACKAGE = re.compile(r"\b(uv\s+add|poetry\s+add|cargo\s+add|go\s+get|gem\s+install|brew\s+install"
                          r"|(npm|pnpm|yarn|bun)\s+(add|install|i)|(uv\s+)?pip3?\s+install)\b(.*)", re.I)
_NOT_A_PACKAGE_AFTER = {"-r", "--requirement", "-e", "--editable", "-c", "--constraint", "-t", "--target"}

_DEPENDENCY_FILES = re.compile(
    r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.(py|cfg)|package\.json|cargo\.toml|go\.(mod|sum)|gemfile"
    r"|composer\.json|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|cargo\.lock"
    r"|gemfile\.lock)$", re.I)
_MIGRATION_PATHS = re.compile(r"(^|/)(migrations|alembic/versions|db/migrate)/", re.I)
_CI_PATHS = re.compile(r"(^|/)(\.github/workflows/|\.gitlab-ci\.yml$|\.circleci/|Jenkinsfile$)", re.I)
_SECRET_PATHS = re.compile(r"(^|/)\.env(\.[\w.-]+)?$", re.I)


def _adds_package(command: str) -> bool:
    """`pip install requests` changes the dependencies; `pip install -r requirements.txt`, `pip install -e .`
    and a bare `npm install` only restore or install the project itself."""
    for m in _ADD_PACKAGE.finditer(command):
        tokens = re.split(r"[\s;&|]+", m.group(5).strip())
        prev = ""
        for tok in tokens:
            if not tok or tok in {"&&", "||"}:
                break
            if not tok.startswith("-") and tok not in {".", "./"} and prev not in _NOT_A_PACKAGE_AFTER:
                return True
            prev = tok
    return False


def classify_command(command: str) -> tuple[bool, str | None]:
    for risk, pattern in _RISKY_COMMANDS:
        if pattern.search(command):
            return True, risk
    if _adds_package(command):
        return True, "dependency"
    return False, None


def classify_path(path: str) -> tuple[bool, str | None]:
    for risk, pattern in (("dependency", _DEPENDENCY_FILES), ("migration", _MIGRATION_PATHS),
                          ("ci_config", _CI_PATHS), ("secrets", _SECRET_PATHS)):
        if pattern.search(path):
            return True, risk
    return False, None


# --- hook payloads -> events -------------------------------------------------------------------------

def _step(payload: dict, project_dir: str) -> tuple[Action, Result] | None:
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    response = payload.get("tool_response")
    if tool in SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        if not command.strip():
            return None
        risky, risk = classify_command(command)
        counts = parse_test_output(tool_output(response)) if may_run_tests(command) else None
        if counts is not None:
            facts: dict[str, Any] = {"runner": counts.runner}
            if counts.failing_complete:
                facts["failing"] = list(counts.failing)[:MAX_FAILING]
            return (Action(name="test_run", target=command_summary(command), risky=risky, risk=risk),
                    Result(ok=counts.failed == 0, facts=facts,
                           metrics={"passed": counts.passed, "failed": counts.failed, "total": counts.total}))
        interrupted = isinstance(response, dict) and bool(response.get("interrupted"))
        return (Action(name="bash", target=command_summary(command), risky=risky, risk=risk),
                Result(ok=False if interrupted else None))
    if tool in EDIT_TOOLS:
        path = relative_path(tool_input.get("file_path") or tool_input.get("notebook_path"), project_dir)
        if not path:
            return None
        risky, risk = classify_path(path)
        failed = isinstance(response, dict) and bool(response.get("error"))
        return Action(name=EDIT_TOOLS[tool], target=path, risky=risky, risk=risk), Result(ok=not failed)
    if not tool or tool in READ_TOOLS:
        return None
    return Action(name="mcp" if tool.startswith("mcp__") else "tool", target=tool[:100]), Result()


def events_from_hook(hook: str, payload: dict, *, team_id: str, project: str, project_dir: str,
                     source: str = "claude-code", usage: dict | None = None) -> list[Event]:
    """The events one hook call observed. `project` is how the project is stored (its directory, or a hashed
    key); `project_dir` is the real directory, for relative paths. `usage` is the turn's usage (Stop)."""
    base = {"team_id": team_id, "project": project, "session_id": payload.get("session_id"), "domain": DOMAIN,
            "source": source}
    if hook == "session-start":
        return [Event(kind="session_start", data={"source": str(payload.get("source") or "startup")}, **base)]
    if hook == "user-prompt":
        prompt = str(payload.get("prompt") or "").strip()
        return [Event(kind="prompt", text=prompt, **base)] if prompt else []
    if hook == "post-tool-use":
        step = _step(payload, project_dir)
        return [Event(kind="step", action=step[0], result=step[1], **base)] if step else []
    if hook == "stop":
        if payload.get("stop_hook_active"):
            return []  # a continuation of a turn already recorded
        return [Event(kind="turn_end", usage={k: float(v) for k, v in (usage or {}).items() if v is not None},
                      **base)]
    if hook == "pre-compact":
        trigger = str(payload.get("trigger") or "auto")
        return [Event(kind="compaction", text=trigger, data={"trigger": trigger}, **base)]
    if hook == "session-end":
        reason = str(payload.get("reason") or "other")
        return [Event(kind="session_end", text=reason, data={"reason": reason}, **base)]
    return []


# --- the adapter: state, risk and outcomes from events --------------------------------------------------

MAX_FILES = 200


class CodingAdapter:
    domain = DOMAIN

    def initial_state(self) -> dict:
        return {"edits": 0, "files": [], "commands": 0, "test_runs": 0, "failed_test_runs": 0, "tests": None}

    def reduce(self, state: dict, event: Event) -> dict:
        if event.kind != "step" or event.action is None:
            return state
        state = {**state, "files": list(state["files"])}
        action, result = event.action, event.result or Result()
        if action.name in ("edit", "write"):
            state["edits"] += 1
            if action.target and action.target not in state["files"] and len(state["files"]) < MAX_FILES:
                state["files"] = sorted(state["files"] + [action.target])
        elif action.name == "test_run":
            m = result.metrics
            state["test_runs"] += 1
            state["failed_test_runs"] += int(m.get("failed", 0) > 0)
            state["tests"] = {"passed": int(m.get("passed", 0)), "failed": int(m.get("failed", 0)),
                              "total": int(m.get("total", 0)), "failing": result.facts.get("failing")}
        elif action.name == "bash":
            state["commands"] += 1
        return state

    def classify(self, action: str, raw: dict) -> tuple[bool, str | None]:
        if action in ("bash", "test_run"):
            return classify_command(str(raw.get("command") or ""))
        if action in ("edit", "write"):
            return classify_path(str(raw.get("path") or ""))
        return False, None

    def outcome(self, events: list[Event]) -> Outcome | None:
        """The session's ground truth: the pass rate of its last test run. Sessions without a test run have no
        ground truth (success None) but still report their activity."""
        if not events:
            return None
        state = self.initial_state()
        for e in events:
            state = self.reduce(state, e)
        runs = state["test_runs"]
        turns = [e for e in events if e.kind == "turn_end"]
        metrics = {"test_runs": runs, "failed_test_runs": state["failed_test_runs"], "edits": state["edits"],
                   "files": len(state["files"]), "commands": state["commands"], "turns": len(turns),
                   "tokens": sum(e.usage.get("tokens", 0.0) for e in turns)}
        tests = state["tests"]
        success = tests["passed"] / tests["total"] if tests and tests["total"] else None
        if runs:
            metrics["first_try"] = float(state["failed_test_runs"] == 0)
        facts: dict[str, Any] = {"failing": tests["failing"]} if tests and tests.get("failing") else {}
        if tests:  # the last run's counts: the episode's test results
            facts["tests"] = {k: tests[k] for k in ("passed", "failed", "total")}
        return Outcome(success=success, metrics=metrics, facts=facts)

    def situation(self, events: list[Event]) -> str | None:
        return first_prompt(events)

"""Claude Code hooks (PROJECT.md §11): make the key calls happen reliably.

- SessionStart: remind the model of the workflow and surface any active task.
- PostToolUse (Bash): capture real test counts from test commands, so
  record_outcome uses them instead of the model's own summary (PROJECT.md §9).
- PreToolUse (recall_context): snapshot the project (git + Claude Code checkpoint
  reference) just before each decision, so a failed attempt can be rolled back. After a
  rollback, deny that recall once if the working tree wasn't restored.
- PreToolUse (record_outcome): the attempt's real token usage and duration, summed from
  the transcript since the last recall_context (counts only).
- Stop: only when a task has an unrecorded outcome (a test run no record_outcome used),
  block once and ask for it. Each test run is nudged about at most once.

Hooks read JSON on stdin and write JSON on stdout. They must never break the
host session: any internal error exits 0 with no output.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any

from horizon.config import Settings
from horizon.db import connect
from horizon.models import Checkpoint, ProgressEntry, TaskState, TestCapture
from horizon.redact import CODE_REMOVED, MAX_NOTE, redact
from horizon.taskstate.checkpoints import capture, restore_steps, same_tree
from horizon.taskstate.store import TaskStore
from horizon.taskstate.usage import attempt_usage
from horizon.testparse import is_test_command, parse_test_output

WORKFLOW = (
    "This project uses the Horizon MCP server (tools: start_task, recall_context, evaluate_options, "
    "record_outcome). For any coding task: call start_task first with the user's request verbatim, then run "
    "the full test suite once before any edit; call recall_context before each significant decision or edit; "
    "call evaluate_options before a crucial choice (framework, database, architecture, key library) and, on a "
    "close call, run its consequence plan and call submit_consequences; call "
    "record_outcome after every test run with the real test counts."
)

LEAN_WORKFLOW = (
    "Horizon records this task from the user's messages and carries its rules to later sessions and projects; "
    "you don't need to call it. Follow the team rules and earlier constraints below."
)


def project_dir(payload: dict) -> str:
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())


def _tool_output(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        parts = [response.get(k) for k in ("stdout", "stderr", "output", "content")]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def command_summary(command: str) -> str:
    """What's stored about a test command: its first line with quoted strings elided. Inline code lives in
    quotes (`python -c "..."`) or heredocs (later lines), and no raw code is stored (PROJECT.md §12)."""
    first = (command or "").strip().splitlines()[0] if (command or "").strip() else ""
    return redact(re.sub(r"(['\"]).*?(\1|$)", '"…"', first), 200)


# --- the shared core: task matching and storage, run locally or by the hosted server ------------------
# `project` is the local project directory (local mode) or its hashed project key (hosted mode).

def core_session_start(store: TaskStore, team: str, project: str) -> dict:
    lines = [WORKFLOW]
    active = store.active(team, cwd=project)
    if active:
        lines.append("Active tasks in this project (continue with recall_context; do not start_task again):")
        lines += [f"- {t.id}: {t.goal[:200]}" + (" [escalated: ask the user how to proceed]"
                                                  if t.status == "escalated" else "") for t in active[:3]]
    pitfalls = store.recurring_failures(project)
    if pitfalls:
        lines.append("Checks that failed in several earlier sessions here (get them right the first time):")
        lines += [f"- {test[:200]} (failed in {n} sessions)" for test, n in pitfalls]
    rules = store.team_rules(team)
    if rules:
        lines.append("Team rules for every project (apply them here too; pass the full list as start_task's"
                     " team_rules, with any the user adds or changes):")
        lines += [f"- {r[:MAX_RULE]}" for r in rules[:20]]
    done = store.recent_completed(team, project)
    if done:
        # Multi-session work: each session tends to finish its own task, so earlier sessions' goals and
        # constraints would otherwise never reach the next one (evals/longhorizon/RESULTS.md, run 1).
        lines.append("Earlier tasks in this project (their constraints still apply unless the user changes them;"
                     " copy them into start_task's constraints):")
        for t in done:
            lines.append(f"- {t.goal[:200]}")
            lines += [f"  - constraint: {c[:MAX_RULE]}" for c in t.constraints[:10]]
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}}


def core_capture(store: TaskStore, team: str, project: str, session_id: str | None, cap: dict) -> dict:
    store.add_capture(TestCapture(session_id=session_id, cwd=project, command=command_summary(cap["command"]),
                                  runner=cap["runner"], passed=cap["passed"], failed=cap["failed"],
                                  total=cap["passed"] + cap["failed"], failing=cap.get("failing")))
    context = (f"Horizon captured this test run: {cap['passed']} passed, {cap['failed']} failed. "
               "Call record_outcome for the change you just tested.")
    if not store.active(team, cwd=project):
        context += " (No active task: call start_task first.)"
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}


def core_restore_checks(store: TaskStore, team: str, project: str) -> list[dict]:
    """Rollbacks waiting for a restore check (Phase 3). Each is checked once: this clears it."""
    out = []
    for task in store.active(team, cwd=project):
        if task.restore_check:
            target = next((c for c in task.checkpoints if c.id == task.rollback_to), None)
            out.append({"task_id": task.id, "commit": task.restore_check, "git": restore_steps(target)["git"]})
            task.restore_check = None
            store.save(task)
    return out


def core_stop(store: TaskStore, team: str, project: str, session: str | None) -> dict | None:
    for task in store.active(team, cwd=project):
        pending = [c for c in store.pending_captures(project, since=task.created_at)
                   if c.nudged_at is None and (session is None or c.session_id in (None, session))]
        if pending:
            store.mark_nudged([c.id for c in pending])
            latest = pending[0]
            return {
                "decision": "block",
                "reason": (f"Unrecorded outcome for task {task.id} (tests: {latest.passed} passed, "
                           f"{latest.failed} failed). Call record_outcome, then finish."),
            }
    return None


# --- lean: Horizon captures the task itself, so the agent makes no Horizon calls ----------------------

MAX_RULE = 500
MAX_GOAL = 4000
_TEAM_RULE = re.compile(r"\b(all (of )?(our|my|the team'?s?) (projects|services|repos)|every (project|service|repo)"
                        r"|future projects|team'?s? rules?|rules? for (all|every))\b", re.I)
_PROJECT_RULE = re.compile(r"\b(from now on|constraints?|must (always|never)|always|never)\b", re.I)


def split_rules(text: str) -> tuple[list[str], list[str]]:
    """The sentences of a request that set lasting rules: for every project (team rules) or for this one."""
    # ponytail: a keyword heuristic; a Jev yes/no question per sentence if it misses real rules.
    team, project = [], []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _TEAM_RULE.search(sentence):
            team.append(sentence[:MAX_RULE])
        elif _PROJECT_RULE.search(sentence):
            project.append(sentence[:MAX_RULE])
    return team, project


def lean_session_start(store: TaskStore, team: str, project: str, session: str | None) -> dict:
    """A new session: the previous sessions' tasks are done, so their goals and constraints show as earlier
    tasks. A resumed or compacted session keeps its session id, and its task stays active."""
    for task in store.active(team, cwd=project):
        if task.status == "active" and task.session_id and task.session_id != session:
            task.status = "completed"
            store.save(task)
    out = core_session_start(store, team, project)
    ctx = out["hookSpecificOutput"]
    ctx["additionalContext"] = ctx["additionalContext"].replace(WORKFLOW, LEAN_WORKFLOW)
    return out


def lean_user_prompt(store: TaskStore, team: str, project: str, session: str | None, prompt: str) -> dict | None:
    """The session's first message starts its task (the goal, code removed); later ones are progress notes.
    Sentences that set lasting rules become the task's constraints or the team's rules."""
    text = redact(prompt, MAX_GOAL)
    if text == CODE_REMOVED:
        return None
    team_rules, constraints = split_rules(text)
    task = next((t for t in store.active(team, cwd=project) if t.session_id == session), None)
    created = task is None
    if created:
        task = store.create(TaskState(team_id=team, goal=text, cwd=project, session_id=session, baseline="missing",
                                      constraints=constraints))
    else:
        task.progress.append(ProgressEntry(note=redact(prompt, MAX_NOTE)))
        task.constraints += [c for c in constraints if c not in task.constraints]
    if team_rules:  # the latest task that sets team rules holds the current set
        current = store.team_rules(team)
        task.team_rules = current + [r for r in team_rules if r not in current]
    store.save(task)
    if created:
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
                f"Horizon task id: {task.id} (only needed if you call evaluate_options)."}}
    return None


# --- the local half: read the host's payload, parse, run git ---------------------------------------

def parsed_capture(payload: dict) -> dict | None:
    """Test counts and failing ids from a Bash test run. Raw output stays on this machine."""
    if payload.get("tool_name") != "Bash":
        return None
    command = (payload.get("tool_input") or {}).get("command", "")
    if not is_test_command(command):
        return None
    counts = parse_test_output(_tool_output(payload.get("tool_response")))
    if counts is None:
        return None
    return {"command": command_summary(command), "runner": counts.runner, "passed": counts.passed,
            "failed": counts.failed, "failing": list(counts.failing) if counts.failing_complete else None}


def deny_unrestored(checks: list[dict], snapshot: str | None, cwd: str) -> dict | None:
    for check in checks:
        if snapshot and not same_tree(cwd, snapshot, check["commit"]):
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Horizon: task {check['task_id']} was rolled back but the files were not restored. Run "
                    f"`{check['git']}` first, then call recall_context again."),
            }}
    return None


class Local:
    """Local mode: the hooks share the MCP server's database."""

    def __init__(self, store: TaskStore, team: str):
        self.store, self.team = store, team

    def session_start(self, project):
        return core_session_start(self.store, self.team, project)

    def capture(self, project, session, cap):
        return core_capture(self.store, self.team, project, session, cap)

    def restore_checks(self, project):
        return core_restore_checks(self.store, self.team, project)

    def checkpoint(self, ckpt: Checkpoint):
        self.store.add_checkpoint(ckpt)

    def stop(self, project, session):
        return core_stop(self.store, self.team, project, session)

    def usage(self, project, session, usage):
        self.store.add_usage(project, session, usage)


class Remote:
    """Hosted mode: the hooks talk to the Horizon server with the team's API key. Only parsed facts are sent
    (counts, failing test ids, commit ids, a hashed project key), never output, code or paths."""

    def __init__(self, url: str, api_key: str):
        self.url, self.api_key = url.rstrip("/"), api_key

    def _post(self, op: str, body: dict):
        import urllib.request

        req = urllib.request.Request(f"{self.url}/api/hooks/{op}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read() or b"null")

    def session_start(self, project):
        return self._post("session-start", {"project": project})

    def capture(self, project, session, cap):
        return self._post("capture", {"project": project, "session_id": session, "capture": cap})

    def restore_checks(self, project):
        return self._post("restore-checks", {"project": project})

    def checkpoint(self, ckpt: Checkpoint):
        self._post("checkpoint", {"checkpoint": ckpt.model_dump(mode="json")})

    def stop(self, project, session):
        return self._post("stop", {"project": project, "session_id": session})

    def usage(self, project, session, usage):
        self._post("usage", {"project": project, "session_id": session, "usage": usage})


def project_key(path: str) -> str:
    """Hosted mode's name for a project: a hash of its path, so the server matches tasks without seeing it."""
    return "prj_" + hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:24]


def session_start(payload: dict, backend, project: str) -> dict | None:
    return backend.session_start(project)


def post_tool_use(payload: dict, backend, project: str) -> dict | None:
    cap = parsed_capture(payload)
    return backend.capture(project, payload.get("session_id"), cap) if cap else None


def pre_tool_use(payload: dict, backend, project: str) -> dict | None:
    """Before recall_context: records a checkpoint reference that the call attaches to its task.
    Before record_outcome: records the attempt's real token usage and duration from the transcript.

    Silent, except right after a rollback: if the tree doesn't match the rollback target, the recall is
    denied once with the restore command (PROJECT.md §8: restore, then retry). Never twice in a row.
    """
    tool = str(payload.get("tool_name", ""))
    if tool.endswith("__record_outcome"):
        usage = attempt_usage(payload.get("transcript_path"))
        if usage:
            backend.usage(project, payload.get("session_id"), usage)
        return None
    if not tool.endswith("__recall_context"):
        return None
    cwd = project_dir(payload)
    ckpt = capture(cwd, payload.get("session_id"), payload.get("transcript_path"))
    if ckpt and isinstance(backend, Remote):
        ckpt.cwd = project
        if ckpt.git:
            ckpt.git.repo = "."  # the host runs the restore in the project directory; no path leaves the machine
    denied = deny_unrestored(backend.restore_checks(project), ckpt.git.commit if ckpt and ckpt.git else None, cwd)
    if denied:
        return denied
    if ckpt:
        backend.checkpoint(ckpt)
    return None


def stop(payload: dict, backend, project: str) -> dict | None:
    """Quiet by design: speaks only when an active task has an unrecorded outcome, i.e. a test run
    from this session that no record_outcome has used, and only once per test run."""
    if payload.get("stop_hook_active"):
        return None  # already blocked once this turn; never loop
    return backend.stop(project, payload.get("session_id"))


HANDLERS = {"session-start": session_start, "pre-tool-use": pre_tool_use, "post-tool-use": post_tool_use,
            "stop": stop, "user-prompt": lambda payload, backend, project: None}
LEAN_HANDLERS = {  # local only (the installer refuses lean with --hosted)
    "session-start": lambda p, b, project: lean_session_start(b.store, b.team, project, p.get("session_id")),
    "user-prompt": lambda p, b, project: lean_user_prompt(b.store, b.team, project, p.get("session_id"),
                                                          str(p.get("prompt") or "")),
    # Test runs are still captured; lean asks for no outcome per run and never blocks the stop.
    "post-tool-use": lambda p, b, project: post_tool_use(p, b, project) and None,
    "stop": lambda p, b, project: None,
}


def run_hook(name: str, stdin: str, settings: Settings | None = None, remote: str | None = None,
             profile: str = "full") -> str:
    """Run a hook and return what it should print. Swallows all errors.

    Local mode writes to the shared database. With `remote` (the hosted server's URL), it sends parsed facts
    to the server with the key in HORIZON_API_KEY, and the project is identified by its hashed key.
    """
    try:
        payload = json.loads(stdin or "{}")
        if remote:
            backend = Remote(remote, os.environ.get("HORIZON_API_KEY", ""))
            result = HANDLERS[name](payload, backend, project_key(project_dir(payload)))
        else:
            settings = settings or Settings.from_env()
            db = connect(settings.db_url)
            try:
                handler = LEAN_HANDLERS.get(name, HANDLERS[name]) if profile == "lean" else HANDLERS[name]
                result = handler(payload, Local(TaskStore(db), settings.team_id), project_dir(payload))
            finally:
                db.close()
        return json.dumps(result) if result else ""
    except Exception as exc:  # a hook must never break the host session
        if os.environ.get("HORIZON_DEBUG"):
            print(f"horizon hook {name} failed: {exc!r}", file=sys.stderr)
        return ""

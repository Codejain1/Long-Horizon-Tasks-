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

from horizon.adapters.coding import command_summary, events_from_hook
from horizon.adapters.coding import tool_output as _tool_output
from horizon.composer import MAX_RULE
from horizon.config import Settings
from horizon.db import connect
from horizon.decision.scorers import make_scorer
from horizon.eventlog.store import EventStore
from horizon.models import Checkpoint, ProgressEntry, TaskState, TestCapture
from horizon.redact import CODE_REMOVED, MAX_NOTE, redact
from horizon.stall import HEADER as STALL_HEADER
from horizon.taskstate.checkpoints import capture, restore_steps, same_tree
from horizon.taskstate.store import TaskStore
from horizon.taskstate.usage import attempt_usage, turn_usage
from horizon.testparse import may_run_tests, parse_test_output

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


# --- the shared core: task matching and storage, run locally or by the hosted server ------------------
# `project` is the local project directory (local mode) or its hashed project key (hosted mode).

def core_session_start(store: TaskStore, team: str, project: str, lean: bool = False) -> dict:
    """`lean`: the agent makes no Horizon calls, so nothing here asks for one, and goals lose the preamble every
    recent goal opens with (a backlog's shared instructions would otherwise fill the 200 characters shown)."""
    from horizon.composer import MAX_CONSTRAINTS, RECENT_WINDOW, label

    lines = [LEAN_WORKFLOW if lean else WORKFLOW]
    active = store.active(team, cwd=project)
    done = store.recent_completed(team, project)
    goals = [t.goal for t in store.recent_completed(team, project, limit=RECENT_WINDOW) + active] if lean else []
    shown = (lambda goal: label(goal, goals, 200)) if lean else (lambda goal: goal[:200])
    if active:
        lines.append("Active tasks in this project (continue them):" if lean else
                     "Active tasks in this project (continue with recall_context; do not start_task again):")
        lines += [f"- {t.id}: {shown(t.goal)}" + (" [escalated: ask the user how to proceed]"
                                                  if t.status == "escalated" else "") for t in active[:3]]
    pitfalls = store.recurring_failures(project)
    if pitfalls:
        lines.append("Checks that failed in several earlier sessions here (get them right the first time):")
        lines += [f"- {test[:200]} (failed in {n} sessions)" for test, n in pitfalls]
    rules = store.team_rules(team)
    if rules:
        lines.append("Team rules for every project (apply them here too):" if lean else
                     "Team rules for every project (apply them here too; pass the full list as start_task's"
                     " team_rules, with any the user adds or changes):")
        lines += [f"- {r[:MAX_RULE]}" for r in rules[:20]]
    if lean:
        # every task's, not only the latest three's: a rule given in ticket 1 for ticket 6 must still be here
        constraints = store.project_constraints(team, project)[:MAX_CONSTRAINTS]
        if constraints:
            lines.append("Constraints set by earlier tasks here (they still apply unless the user changes them):")
            lines += [f"- {c[:MAX_RULE]}" for c in constraints]
        if done:
            lines.append("Earlier tasks in this project:")
            lines += [f"- {shown(t.goal)}" for t in done]
    elif done:
        # Multi-session work: each session tends to finish its own task, so earlier sessions' goals and
        # constraints would otherwise never reach the next one (evals/longhorizon/RESULTS.md, run 1).
        lines.append("Earlier tasks in this project (their constraints still apply unless the user changes them;"
                     " copy them into start_task's constraints):")
        for t in done:
            lines.append(f"- {shown(t.goal)}")
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

MAX_GOAL = 4000
_TEAM_RULE = re.compile(r"\b(all (of )?(our|my|the team'?s?) (projects|services|repos)|every (project|service|repo)"
                        r"|future projects|team'?s? rules?|rules? for (all|every))\b", re.I)
_PROJECT_RULE = re.compile(r"\b(from now on|constraints?|must (always|never)|always|never)\b", re.I)
_LIST_START = re.compile(r":\s*\n?\s*1[.)]\s")  # a lead-in that opens a numbered list
_LIST_ITEM = re.compile(r"\s*\d+[.)]\s")


MAX_SENTENCES = 30


def rule_questions(n: int) -> dict[str, dict]:
    from horizon.decision.scorers import noul

    qs = {}
    for i in range(n):
        qs[f"team_{i}"] = noul(f"Does `sentences[{i}]` set a lasting rule the user wants followed in all of their or "
                               "their team's projects, including future ones, not only in the current project or task?")
        # Specs of the function being built ("`get_order` returns a dict with ...") used to be kept as constraints
        # and filled half of session start (ward). Read in the whole `message`, so a continuation ("that limit is
        # scaled ...", "And a patient's MRN ...") counts with the rule it continues: judged alone, those two were
        # dropped and ward lost 4 of 11 rules. Ticket eval (evals/rules/tickets.py): rules kept 18/18 (as before);
        # other sentences kept 35 -> 4-6.
        qs[f"project_{i}"] = noul({
            "question": f"Does `sentences[{i}]` set a lasting rule or constraint that later work on this project "
                        "must keep following? Read it in the context of the whole `message`: it may continue the "
                        "sentence before it.",
            "counts": "Business, legal, safety or engineering rules and policies; limits and formulas the product "
                      "must respect; notes given 'for later'; requirements that will apply to future features; a "
                      "sentence that continues or qualifies such a rule.",
            "not_counted": "A description of what a function, method or command in the current task takes, "
                           "returns, lists or raises (its spec), unless it states a policy beyond this task; "
                           "instructions for doing the task (export it, run the tests, write tests); greetings or "
                           "one-off remarks.",
        })
    return qs


def split_rules(text: str, scorer=None) -> tuple[list[str], list[str]]:
    """The sentences of a request that set lasting rules: for every project (team rules) or for this one.

    With a scorer (Jev), two yes/no questions per sentence in one request: 10/10 on a probe set where the
    keyword fallback got 8/10 ("Never mind…" isn't a rule; "Always… everywhere" is a team rule)."""
    # not after a list number ("1."), which would leave "…these constraints hold: 1." as a rule of its own
    sentences = [s for s in re.split(r"(?<=[.!?])(?<!\b\d\.)\s+", text) if s.strip()][:MAX_SENTENCES]
    team, project = [], []
    if scorer is not None and sentences:
        try:
            a = scorer.ask({"message": text, "sentences": sentences}, rule_questions(len(sentences)))
            for i, sentence in enumerate(sentences):
                if a[f"team_{i}"].value > 0.5:
                    team.append(sentence[:MAX_RULE])
                elif a[f"project_{i}"].value > 0.5:
                    project.append(sentence[:MAX_RULE])
            return team, project
        except Exception as exc:  # the scorer is down or unconfigured: fall back to keywords
            if os.environ.get("HORIZON_DEBUG"):
                print(f"horizon: rule scorer failed, using keywords: {exc!r}", file=sys.stderr)
    listing = None  # the numbered items after "these constraints hold:" are rules even without a keyword
    for sentence in sentences:
        if listing is not None and not _LIST_ITEM.match(sentence):
            listing = None
        if _TEAM_RULE.search(sentence) or listing is team:
            team.append(sentence[:MAX_RULE])
            listing = team if _LIST_START.search(sentence) else listing
        elif _PROJECT_RULE.search(sentence) or listing is project:
            project.append(sentence[:MAX_RULE])
            listing = project if _LIST_START.search(sentence) else listing
    return team, project


def lean_session_start(store: TaskStore, team: str, project: str, session: str | None,
                       budget: int | None = None, source: str | None = None, refused: list[str] | None = None) -> dict:
    """A new session: the previous sessions' tasks are done, so their goals and constraints show as earlier
    tasks. A resumed or compacted session keeps its session id, and its task stays active. With a `budget`, the
    context composer chooses what to show (`HORIZON_CONTEXT=composer`), and after a compaction (`source`
    "compact") restates this session's task with its progress. `refused`: lines naming what the permissions
    refused in earlier sessions (`HORIZON_DEAD_ENDS=on`)."""
    for task in store.active(team, cwd=project):
        if task.status == "active" and task.session_id and task.session_id != session:
            task.status = "completed"
            store.save(task)
    if budget is not None:
        from horizon import composer

        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
                                       composer.session_start(store, team, project, LEAN_WORKFLOW, budget,
                                                              session, reanchor=source == "compact",
                                                              refused=refused)}}
    out = core_session_start(store, team, project, lean=True)
    ctx = out["hookSpecificOutput"]
    if refused:
        from horizon.deadends import HEADING

        ctx["additionalContext"] += "\n\n" + "\n".join([HEADING, *refused])
    return out


def lean_user_prompt(store: TaskStore, team: str, project: str, session: str | None, prompt: str,
                     tools: bool = True, scorer=None, briefing=None, decide: bool = False) -> dict | None:
    """The session's first message starts its task (the goal, code removed); later ones are progress notes.
    Sentences that set lasting rules become the task's constraints or the team's rules. `briefing(goal)`, when
    given, is what similar past sessions taught, shown as the task starts."""
    text = redact(prompt, MAX_GOAL)
    if text == CODE_REMOVED:
        return None
    team_rules, constraints = split_rules(text, scorer)
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
    if not created:
        return None
    lines = [f"Horizon task id: {task.id} (only needed if you call evaluate_options)."] if tools else []
    if decide and tools:
        from horizon.decide import crucial_ahead, earlier_decisions, instruction

        done = store.recent_completed(team, project, limit=30)
        if crucial_ahead(scorer, text, earlier_decisions(done)):
            task.decision_due = True
            store.save(task)
            lines = [instruction(task.id)]
    if briefing is not None:
        try:
            lines += [b for b in [briefing(text)] if b]
        except Exception as exc:  # memory is down: the task still starts
            if os.environ.get("HORIZON_DEBUG"):
                print(f"horizon: past sessions unavailable: {exc!r}", file=sys.stderr)
    if lines:  # hosted lean has no MCP tools to pass the id to
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "\n\n".join(lines)}}
    return None


# --- the local half: read the host's payload, parse, run git ---------------------------------------

def parsed_capture(payload: dict) -> dict | None:
    """Test counts and failing ids from a Bash test run. Raw output stays on this machine."""
    if payload.get("tool_name") != "Bash":
        return None
    command = (payload.get("tool_input") or {}).get("command", "")
    if not may_run_tests(command):
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

    def __init__(self, store: TaskStore, team: str, scorer_factory=lambda: None, briefing=None,
                 context_budget: int | None = None, stall=None, dead_ends=None, tools: bool = True,
                 decide: bool = False):
        self.store, self.team, self.scorer_factory = store, team, scorer_factory
        self.decide = decide  # HORIZON_DECIDE: trigger evaluate_options on tasks with a crucial choice
        self.tools = tools  # the host has Horizon's MCP tools (Cursor installs only the hooks)
        self.briefing = briefing  # (project, session, goal) -> context from similar past sessions
        self.context_budget = context_budget  # set: the context composer chooses lean's session-start context
        self.stall = stall  # (store, team, project, session) -> a stall warning or None
        self.dead_ends = dead_ends  # a DeadEnds: refused commands, within a session and from earlier ones

    def session_start(self, project):
        return core_session_start(self.store, self.team, project)

    def lean_session_start(self, project, session, source=None):
        refused = self.dead_ends.session_start(project, session) if self.dead_ends else None
        return lean_session_start(self.store, self.team, project, session, self.context_budget, source, refused)

    def dead_end_check(self, project, session, transcript, command):
        return self.dead_ends.check(project, session, transcript, command) if self.dead_ends and session else None

    def user_prompt(self, project, session, prompt):
        brief = self.briefing
        return lean_user_prompt(self.store, self.team, project, session, prompt, tools=self.tools,
                                scorer=self.scorer_factory(), briefing=(lambda goal: brief(project, session, goal)) if brief else None,
                                decide=self.decide)

    def decision_check(self, project, session, tool):
        """The denial reason for an edit made before this session's crucial choice was evaluated, once."""
        from horizon.decide import edit_check

        task = next((t for t in self.store.active(self.team, cwd=project) if t.session_id == session), None)
        reason = edit_check(task, tool) if self.decide and task else None
        if reason:
            self.store.save(task)
        return reason

    def capture(self, project, session, cap):
        return core_capture(self.store, self.team, project, session, cap)

    def restore_checks(self, project):
        return core_restore_checks(self.store, self.team, project)

    def stall_check(self, project, session):
        return self.stall(self.store, self.team, project, session) if self.stall and session else None

    def checkpoint(self, ckpt: Checkpoint):
        self.store.add_checkpoint(ckpt)

    def stop(self, project, session):
        return core_stop(self.store, self.team, project, session)

    def usage(self, project, session, usage):
        self.store.add_usage(project, session, usage)


_CONNECTIONS: dict = {}  # host -> an open HTTP(S) connection, reused by the hook daemon


class Remote:
    """Hosted mode: the hooks talk to the Horizon server with the team's API key. Only parsed facts are sent
    (counts, failing test ids, commit ids, a hashed project key), never output, code or paths."""

    def __init__(self, url: str, api_key: str):
        self.url, self.api_key = url.rstrip("/"), api_key

    def _post(self, op: str, body: dict):
        """POST over a kept-alive connection: in the hook daemon, calls after the first skip the TCP and TLS
        handshake (once per call before). A dropped connection is reopened once."""
        import http.client
        from urllib.parse import urlsplit

        u = urlsplit(self.url)
        data = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        path = f"{u.path.rstrip('/')}/api/hooks/{op}"
        for attempt in (0, 1):
            conn = _CONNECTIONS.get(u.netloc) if attempt == 0 else None
            if conn is None:
                kind = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
                conn = _CONNECTIONS[u.netloc] = kind(u.netloc, timeout=8)
            try:
                conn.request("POST", path, body=data, headers=headers)
                r = conn.getresponse()
                payload = r.read()
            except (OSError, http.client.HTTPException):
                conn.close()
                _CONNECTIONS.pop(u.netloc, None)
                if attempt:
                    raise
                continue
            if r.status >= 400:
                raise OSError(f"hosted hook {op}: HTTP {r.status}")
            return json.loads(payload or b"null")

    def session_start(self, project):
        return self._post("session-start", {"project": project})

    def stall_check(self, project, session):
        return None  # needs the event log and the forecast, which hosted installs don't have yet

    def dead_end_check(self, project, session, transcript, command):
        return None  # needs the event log, which hosted installs don't have yet

    def lean_session_start(self, project, session, source=None):
        return self._post("lean-session-start", {"project": project, "session_id": session, "source": source})

    def user_prompt(self, project, session, prompt):
        # Code never leaves the machine: the prompt is redacted here (and again on the server).
        return self._post("user-prompt", {"project": project, "session_id": session, "prompt": redact(prompt, MAX_GOAL)})

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


def lean_post_tool_use(payload: dict, backend, project: str) -> dict | None:
    """Capture a test run; with stall detection on, warn a session stuck on the same failing check."""
    cap = parsed_capture(payload)
    if not cap:
        return None
    backend.capture(project, payload.get("session_id"), cap)
    warn = backend.stall_check(project, payload.get("session_id"))
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": warn}} if warn else None


def lean_pre_tool_use(payload: dict, backend, project: str) -> dict | None:
    """Before a shell command: with `HORIZON_DEAD_ENDS=on`, warn once when it is like one already refused twice
    this session. Horizon's own tools keep the full profile's checkpoint and usage handling."""
    tool = payload.get("tool_name")
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        reason = getattr(backend, "decision_check", lambda *a: None)(project, payload.get("session_id"), tool)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}} if reason else None
    if tool != "Bash":
        return pre_tool_use(payload, backend, project)
    command = str((payload.get("tool_input") or {}).get("command") or "")
    warn = command and backend.dead_end_check(project, payload.get("session_id"), payload.get("transcript_path"),
                                              command)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": warn}} if warn else None


HANDLERS = {"session-start": session_start, "pre-tool-use": pre_tool_use, "post-tool-use": post_tool_use,
            "stop": stop, "user-prompt": lambda payload, backend, project: None,
            # Observed for the event log only (v2 Phase 0); nothing to say to the host yet.
            "pre-compact": lambda payload, backend, project: None,
            "session-end": lambda payload, backend, project: None}
LEAN_HANDLERS = {
    "session-start": lambda p, b, project: b.lean_session_start(project, p.get("session_id"), p.get("source")),
    "user-prompt": lambda p, b, project: b.user_prompt(project, p.get("session_id"), str(p.get("prompt") or "")),
    # Test runs are still captured; lean asks for no outcome per run and never blocks the stop.
    "post-tool-use": lambda p, b, project: lean_post_tool_use(p, b, project),
    "pre-tool-use": lambda p, b, project: lean_pre_tool_use(p, b, project),
    "stop": lambda p, b, project: None,
}


MODEL_VISIBLE = ("additionalContext", "reason", "permissionDecisionReason")


def injected_chars(result: dict | None) -> int:
    """Characters of a hook's output that the model reads: added context, a blocked stop's or a denial's reason."""
    if not result:
        return 0
    inner = result.get("hookSpecificOutput") or {}
    return sum(len(str(d.get(k) or "")) for d in (result, inner) for k in MODEL_VISIBLE)


def log_events(name: str, payload: dict, events: EventStore, team: str, project: str, injected: int = 0,
               flags: dict | None = None) -> int:
    """Append what this hook call observed to the event log (v2 Phase 0). Returns the number of events.
    `injected` (what the hook gave the model) and `flags` (e.g. a stall warning) go on the call's first event, so
    a session's context cost sums."""
    usage = turn_usage(payload.get("transcript_path")) if name == "stop" else None
    new = events_from_hook(name, payload, team_id=team, project=project, project_dir=project,
                           source=os.environ.get("HORIZON_HOST", "claude-code"), usage=usage)
    if new and injected:
        new[0].data["injected_chars"] = injected
    if new and flags:
        new[0].data.update(flags)
    for event in new:
        events.append(event)
    return len(new)


def write_session_episodes(db, settings: Settings, project: str) -> dict:
    """Lean memory (v2 Phase 1 track A): every finished session segment of the project becomes an episode,
    including any an earlier session end missed and sessions left idle (a crash sends no SessionEnd). Runs after
    the hook's own work, so it can't change its output."""
    from horizon.eventlog.episodes import SessionEpisodes, configured_scorer, local_memory

    from horizon.eventlog.forecast import configured_predictor

    tasks, log = TaskStore(db), SessionEpisodes(db, settings.session_idle_minutes)
    return log.write(settings.team_id, lambda: local_memory(settings, db), project,
                     task_for=lambda proj, session: tasks.session_task(settings.team_id, proj, session),
                     judge=configured_scorer(settings), predictor=configured_predictor(settings, log))


def past_sessions_briefing(db, settings: Settings):
    """Lean's context from similar past sessions (v2 Phase 1), when `session_memory` is on."""
    if settings.session_memory != "on":
        return None
    from functools import cache

    from horizon.composer import expects_trouble
    from horizon.eventlog.briefing import past_sessions
    from horizon.eventlog.episodes import SessionEpisodes, local_memory

    log, memory = SessionEpisodes(db, settings.session_idle_minutes), cache(lambda: local_memory(settings, db))

    def brief(project, session, goal):
        if settings.context == "composer" and not expects_trouble(log, lambda: memory().embedder, settings,
                                                                  settings.team_id, project, goal):
            return None
        return past_sessions(log, settings.team_id, project, goal, memory, session,
                             settings.session_memory_min_similarity)

    return brief


def stall_checker(db, settings: Settings):
    """Lean's stall check (v2 Phase 2), when `stall` is on: once per session, from its test runs and the forecast."""
    if settings.stall != "on":
        return None
    from horizon import stall

    def forecasts(team, project, session, k):
        from horizon.eventlog.episodes import SessionEpisodes
        from horizon.eventlog.forecast import SessionWorldModel
        from horizon.memrouter.embedding import make_embedder

        log = SessionEpisodes(db, settings.session_idle_minutes)
        task = next((t for t in TaskStore(db).active(team, cwd=project) if t.session_id == session), None)
        if task is None or not log.has_episodes(team, project):
            return None, None
        model = SessionWorldModel(log, make_embedder(settings.embedder, settings.embedding_dim), settings)
        return (model.predict(team, project, task.goal),
                model.predict(team, project, task.goal, so_far={"failed_runs": k}))

    def check(store, team, project, session):
        if any(e.data.get("stall_warning") for e in EventStore(db).session(team, project, session)):
            return None
        runs = [stall.Run(p, f, tuple(k) if k is not None else None) for p, f, k in store.session_runs(project, session)]

        def safe(k):
            try:
                return forecasts(team, project, session, k)
            except Exception as exc:  # no forecast: the floor still holds
                if os.environ.get("HORIZON_DEBUG"):
                    print(f"horizon: stall forecast failed: {exc!r}", file=sys.stderr)
                return None, None

        return stall.check(runs, settings.stall_min_runs, safe)

    return check


class DeadEnds:
    """Refused commands for lean (`HORIZON_DEAD_ENDS=on`): the session's refusals read from its transcript, and
    earlier sessions' from the event log."""

    def __init__(self, db, team: str):
        self.events, self.team = EventStore(db), team
        self.source = os.environ.get("HORIZON_HOST", "claude-code")

    def check(self, project, session, transcript, command):
        from horizon import deadends

        known = deadends.ingest(self.events, self.team, project, session, transcript, self.source)
        found = deadends.warning(known, command, project)
        if not found:
            return None
        text, key = found
        self.events.append(deadends.warning_event(self.team, project, session, key, text, self.source))
        return text

    def session_start(self, project, session):
        from horizon import deadends

        return deadends.lines(deadends.past(self.events, self.team, project, session)) or None


def log_refusals(db, team: str, project: str, payload: dict) -> None:
    """Every lean or observe session's refusals reach the event log at the end of each turn, whatever the setting,
    so the history exists once the warning is switched on."""
    from horizon import deadends

    deadends.ingest(EventStore(db), team, project, payload.get("session_id"), payload.get("transcript_path"),
                    os.environ.get("HORIZON_HOST", "claude-code"))


def _episodes_quietly(db, settings: Settings, project: str) -> None:
    try:
        write_session_episodes(db, settings, project)
    except Exception as exc:
        if os.environ.get("HORIZON_DEBUG"):
            print(f"horizon: session episodes failed: {exc!r}", file=sys.stderr)


def pick_handler(name: str, profile: str):
    if profile == "observe":  # log events, say nothing, keep no task state: plain Claude Code plus a recorder
        return lambda payload, backend, project: None
    return LEAN_HANDLERS.get(name, HANDLERS[name]) if profile == "lean" else HANDLERS[name]


def run_hook(name: str, stdin: str, settings: Settings | None = None, remote: str | None = None,
             profile: str = "full", tools: bool = True) -> str:
    """Run a hook and return what it should print. Swallows all errors.

    Local mode writes to the shared database. With `remote` (the hosted server's URL), it sends parsed facts
    to the server with the key in HORIZON_API_KEY, and the project is identified by its hashed key.
    Local mode also appends the call's events to the event log, after the handler and apart from it, so
    logging can neither change what the hook says nor break it. Hosted mode doesn't forward events yet.
    """
    try:
        payload = json.loads(stdin or "{}")
        handler = pick_handler(name, profile)
        if remote:
            backend = Remote(remote, os.environ.get("HORIZON_API_KEY", ""))
            result = handler(payload, backend, project_key(project_dir(payload)))
        else:
            settings = settings or Settings.from_env()
            db = connect(settings.db_url)
            try:
                scorer = lambda: make_scorer(settings.scorer, settings.jev_model, settings.llm_scorer_model)  # noqa: E731
                project = project_dir(payload)
                brief = past_sessions_briefing(db, settings) if profile == "lean" else None
                budget = settings.context_budget_chars if settings.context == "composer" else None
                stalls = stall_checker(db, settings) if profile == "lean" else None
                dead_ends = DeadEnds(db, settings.team_id) if profile == "lean" and settings.dead_ends == "on" else None
                result = handler(payload, Local(TaskStore(db), settings.team_id, scorer, brief, budget, stalls,
                                                dead_ends, tools, decide=settings.decide == "on"), project)
                if name == "session-start" and profile == "lean":
                    # before this start is logged, so a resumed session's idle part still reads as idle
                    _episodes_quietly(db, settings, project)
                try:
                    warned = name == "post-tool-use" and STALL_HEADER in json.dumps(result or {})
                    log_events(name, payload, EventStore(db), settings.team_id, project, injected_chars(result),
                               {"stall_warning": True} if warned else None)
                    if name in ("stop", "session-end") and profile in ("lean", "observe"):
                        log_refusals(db, settings.team_id, project, payload)
                except Exception as exc:
                    if os.environ.get("HORIZON_DEBUG"):
                        print(f"horizon: event log failed in {name}: {exc!r}", file=sys.stderr)
                if name == "session-end" and profile == "lean":
                    _episodes_quietly(db, settings, project)
            finally:
                db.close()
        return json.dumps(result) if result else ""
    except Exception as exc:  # a hook must never break the host session
        if os.environ.get("HORIZON_DEBUG"):
            print(f"horizon hook {name} failed: {exc!r}", file=sys.stderr)
        return ""

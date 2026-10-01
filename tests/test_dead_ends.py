"""Refused commands: lean remembers what the host's permissions refused, warns before a third try in a session,
and lists at session start what earlier sessions kept hitting."""

import dataclasses
import json

import pytest

from horizon import deadends
from horizon.deadends import HEADER, HEADING, classify, heads, keys_of
from horizon.eventlog.store import EventStore
from horizon.hooks import Local, lean_pre_tool_use, run_hook
from horizon.install import merge_hooks

APPROVAL = "This command requires approval"
PART = ("This Bash command contains multiple operations. The following part requires approval: "
        "/opt/finsign/bin/finsign --sign")
OUTSIDE = ("rm in '/tmp/check.py' needs approval. The path is outside the working directories for this session: "
           "'/private/tmp/repo'.")
HASH = "Newline followed by # inside a quoted argument can hide arguments from path validation"
FAILED = "Exit code 1\nTraceback (most recent call last):\n  ModuleNotFoundError: No module named 'invoicing'"
SIGN = "echo '{}' | /opt/finsign/bin/finsign --sign"


# --- recognising a refusal ---------------------------------------------------------------------------------

def test_permission_refusals_are_recognised_and_failing_commands_are_not():
    assert classify(APPROVAL, "/opt/finsign/bin/finsign --help", "Bash") == "cmd:/opt/finsign/bin/finsign"
    assert classify(PART, SIGN, "Bash") == "cmd:/opt/finsign/bin/finsign"  # the part named, not `echo`
    assert classify(OUTSIDE, "rm /tmp/check.py", "Bash") == "outside"
    assert classify(HASH, 'python3 -c "\n# check\nimport x"', "Bash") == "hash"
    assert classify("ls in '/opt/' was blocked. For security, Claude Code may only list files in the allowed "
                    "working directories for this session", "ls /opt", "Bash") == "outside"
    assert classify(FAILED, "python3 /tmp/check.py", "Bash") is None
    assert classify("Exit code 127\nfinsign: command not found", "finsign --sign", "Bash") is None


def test_variations_of_one_command_share_a_key():
    assert heads("PYTHONPATH=. python3 /tmp/x.py") == heads("PYTHONPATH=/repo python y.py") == ["PYTHONPATH=… python"]
    assert heads("python3.12 -m pytest") == ["python"] and heads("md5 file") == ["md5"]
    assert heads("cd repo && PYTHONPATH=. python3 x.py; rm -f x.py") == ["PYTHONPATH=… python", "rm"]
    assert keys_of("/opt/finsign/bin/finsign --help", "/repo") == {"cmd:/opt/finsign/bin/finsign"}


def test_paths_outside_the_project_are_one_kind(tmp_path):
    project = str(tmp_path)
    assert "outside" in keys_of("python3 /tmp/check.py", project)
    assert "outside" in keys_of(f"cp /tmp/check.py {project}/check.py", project)
    assert "outside" not in keys_of(f"python3 {project}/check.py", project)
    assert "outside" not in keys_of("/usr/bin/env python3 check.py 2>/dev/null", project)
    assert "hash" in keys_of('python3 -c "\nimport x\n# check it\n"', project)


# --- a transcript ------------------------------------------------------------------------------------------

def transcript(path, calls):
    """A Claude Code transcript: (command, refusal message or None for a call that ran) per Bash call."""
    lines = []
    for i, (command, message) in enumerate(calls):
        lines.append({"type": "assistant", "timestamp": f"2026-10-01T10:00:{i:02d}Z", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": command}}]}})
        result = {"type": "tool_result", "tool_use_id": f"t{i}", "content": message or "ok"}
        if message:
            result["is_error"] = True
        lines.append({"type": "user", "timestamp": f"2026-10-01T10:00:{i:02d}Z", "message": {"content": [result]}})
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return str(path)


TICKET_5 = [("ls -la /opt/finsign/bin/", OUTSIDE), (SIGN, PART), (SIGN, PART), ("which finsign", None),
            ("python3 /tmp/check.py", FAILED)]


def test_a_transcripts_refusals_are_read_in_order(tmp_path):
    found = deadends.refusals(transcript(tmp_path / "t.jsonl", TICKET_5))
    assert [r.key for r in found] == ["outside", "cmd:/opt/finsign/bin/finsign", "cmd:/opt/finsign/bin/finsign"]
    assert found[0].at.isoformat() == "2026-10-01T10:00:00+00:00"
    assert deadends.refusals(str(tmp_path / "missing.jsonl")) == [] and deadends.refusals(None) == []


# --- through the hooks -------------------------------------------------------------------------------------

@pytest.fixture
def on(settings):
    return dataclasses.replace(settings, dead_ends="on")


def hook(settings, name, project, session, **payload):
    out = run_hook(name, json.dumps({"session_id": session, "cwd": project, **payload}), settings, profile="lean")
    return json.loads(out) if out else None


def bash(settings, project, session, command, path):
    return hook(settings, "pre-tool-use", project, session, tool_name="Bash", tool_input={"command": command},
                transcript_path=path)


def events(settings, project, session):
    from horizon.db import connect

    db = connect(settings.db_url)
    try:
        return EventStore(db).session(settings.team_id, project, session)
    finally:
        db.close()


def test_a_third_try_is_warned_once(on, project_dir, tmp_path):
    path = transcript(tmp_path / "t.jsonl", TICKET_5)
    out = bash(on, project_dir, "s1", "/opt/finsign/bin/finsign --help", path)
    text = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse" and "permissionDecision" not in out["hookSpecificOutput"]
    assert text.startswith(HEADER) and "`/opt/finsign/bin/finsign` has already been refused 2 times" in text
    assert bash(on, project_dir, "s1", SIGN, path) is None  # once per kind
    assert bash(on, project_dir, "s1", "ls /opt", path) is None  # outside was refused only once
    logged = events(on, project_dir, "s1")
    assert sum(e.action.name == deadends.ACTION for e in logged if e.action) == 3  # each refusal logged once
    warned = [e for e in logged if e.data.get(deadends.WARNED)]
    assert len(warned) == 1 and warned[0].data["injected_chars"] == len(text)
    from horizon.adapters.coding import CodingAdapter
    from horizon.eventlog.schema import HORIZON_ACTIONS
    from horizon.eventlog.transitions import replay

    names = [t.action.name for t in replay(logged, CodingAdapter())[1]]
    assert deadends.WARNED in HORIZON_ACTIONS and names == [deadends.ACTION] * 3  # refusals are transitions; the warning isn't


def test_off_by_default_it_warns_nothing_but_still_logs_refusals(settings, project_dir, tmp_path):
    path = transcript(tmp_path / "t.jsonl", TICKET_5)
    assert bash(settings, project_dir, "s1", "/opt/finsign/bin/finsign --help", path) is None
    hook(settings, "stop", project_dir, "s1", transcript_path=path, stop_hook_active=False)
    hook(settings, "stop", project_dir, "s1", transcript_path=path, stop_hook_active=False)
    logged = [e for e in events(settings, project_dir, "s1") if e.action and e.action.name == deadends.ACTION]
    assert [e.action.target for e in logged] == ["outside", "cmd:/opt/finsign/bin/finsign",
                                                 "cmd:/opt/finsign/bin/finsign"]
    stored = json.dumps([e.model_dump(mode="json") for e in logged])
    assert "--sign" not in stored and "echo" not in stored and "/tmp/check" not in stored  # no arguments kept


def test_observe_logs_refusals_and_says_nothing(settings, project_dir, tmp_path):
    path = transcript(tmp_path / "t.jsonl", TICKET_5)
    on = dataclasses.replace(settings, dead_ends="on")
    out = run_hook("pre-tool-use", json.dumps({"session_id": "s1", "cwd": project_dir, "tool_name": "Bash",
                                               "tool_input": {"command": SIGN}, "transcript_path": path}),
                   on, profile="observe")
    assert out == ""
    run_hook("session-end", json.dumps({"session_id": "s1", "cwd": project_dir, "transcript_path": path}), on,
             profile="observe")
    assert sum(bool(e.action and e.action.name == deadends.ACTION) for e in events(on, project_dir, "s1")) == 3


def session_start(settings, project, session):
    out = hook(settings, "session-start", project, session, source="startup")
    return out["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("context", ["classic", "composer"])
def test_session_start_lists_what_earlier_sessions_kept_hitting(on, project_dir, tmp_path, context):
    on = dataclasses.replace(on, context=context)
    once = transcript(tmp_path / "a.jsonl", [("rm /tmp/x", OUTSIDE), ("PYTHONPATH=. python3 x.py", APPROVAL)])
    again = transcript(tmp_path / "b.jsonl", [("cp /tmp/y .", OUTSIDE), (SIGN, PART), (SIGN, PART)])
    hook(on, "stop", project_dir, "s1", transcript_path=once, stop_hook_active=False)
    hook(on, "session-end", project_dir, "s2", transcript_path=again, reason="other")
    ctx = session_start(on, project_dir, "s3")
    listed = ctx.split(HEADING + "\n", 1)[1].splitlines()
    assert listed[:2] == ["- reading or writing paths outside the project (such as /tmp) (refused 2 times in 2 sessions)",
                          "- `/opt/finsign/bin/finsign` (refused 2 times in 1 session)"]
    assert "PYTHONPATH" not in ctx  # refused once: maybe a one-off
    assert HEADING not in session_start(dataclasses.replace(on, dead_ends="off"), project_dir, "s4")


def test_the_list_stays_within_the_composers_budget(on, project_dir, tmp_path):
    on = dataclasses.replace(on, context="composer", context_budget_chars=700)
    calls = [(f"tool{i} --go", APPROVAL) for i in range(5) for _ in range(2)]
    hook(on, "stop", project_dir, "s1", transcript_path=transcript(tmp_path / "a.jsonl", calls),
         stop_hook_active=False)
    ctx = session_start(on, project_dir, "s2")
    assert len(ctx) <= 700 and HEADING in ctx


def test_hosted_and_horizons_own_tools_are_untouched():
    class Hosted:
        def dead_end_check(self, *a):
            return None

        def usage(self, *a):
            raise AssertionError("record_outcome's usage needs a transcript")

    payload = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": SIGN}}
    assert lean_pre_tool_use(payload, Hosted(), "p") is None
    assert lean_pre_tool_use({"tool_name": "mcp__horizon__record_outcome"}, Hosted(), "p") is None
    assert Local(None, "t").dead_end_check("p", "s1", None, SIGN) is None  # setting off: nothing to check


def test_lean_hooks_every_shell_command_before_it_runs():
    lean = merge_hooks({}, ["horizon"], profile="lean")["hooks"]["PreToolUse"][0]["matcher"]
    full = merge_hooks({}, ["horizon"], profile="full")["hooks"]["PreToolUse"][0]["matcher"]
    assert lean.split("|")[0] == "Bash" and "mcp__horizon__recall_context" in lean
    assert "Bash" not in full.split("|")

"""Phase 3: checkpoint references and rollback rules (PROJECT.md §8)."""

import json
import subprocess
from pathlib import Path

import pytest

from horizon.hooks import run_hook
from horizon.models import Checkpoint, GitRef, TaskState
from horizon.service import Platform, ToolInputError
from horizon.taskstate import rollback
from horizon.taskstate.checkpoints import capture, claude_code_ref, git_snapshot, restore_steps


@pytest.fixture(autouse=True)
def no_project_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(project_dir):
    git(project_dir, "init", "-q")
    git(project_dir, "config", "user.email", "t@example.com")
    git(project_dir, "config", "user.name", "t")
    (Path(project_dir) / "app.py").write_text("v1\n")
    git(project_dir, "add", ".")
    git(project_dir, "commit", "-qm", "init")
    return Path(project_dir)


def transcript(tmp_path, entries) -> str:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return str(path)


def prompt(uuid, content, **kw):
    return {"type": "user", "uuid": uuid, "timestamp": "2026-09-28T10:00:00Z",
            "message": {"role": "user", "content": content}, **kw}


def pre_tool_use(cwd, settings, **extra):
    body = {"session_id": "s1", "cwd": cwd, "hook_event_name": "PreToolUse",
            "tool_name": "mcp__horizon__recall_context", "tool_input": {}, **extra}
    assert run_hook("pre-tool-use", json.dumps(body), settings) == ""  # silent: never blocks the tool


# --- checkpoint capture and restore ----------------------------------------------

def test_git_snapshot_is_head_when_clean_and_a_tree_snapshot_when_dirty(repo):
    head = git(repo, "rev-parse", "HEAD").strip()
    assert git_snapshot(str(repo)).commit == head

    (repo / "app.py").write_text("v2 uncommitted\n")
    snap = git_snapshot(str(repo))
    assert snap.commit != head and snap.repo == str(repo)
    assert git(repo, "show", f"{snap.commit}:app.py") == "v2 uncommitted\n"
    # Capturing touches nothing: the change is still in the working tree, and no stash entry was added.
    assert (repo / "app.py").read_text() == "v2 uncommitted\n"
    assert git(repo, "stash", "list") == ""


def test_git_snapshot_outside_a_repo_or_without_commits(tmp_path):
    assert git_snapshot(str(tmp_path)) is None
    git(tmp_path, "init", "-q")
    assert git_snapshot(str(tmp_path)) is None


def test_restore_command_brings_back_the_checkpoint(repo):
    (repo / "app.py").write_text("v2 good\n")
    ckpt = capture(str(repo))

    # A failed attempt edits, deletes, adds a tracked file and even commits.
    (repo / "app.py").write_text("broken\n")
    (repo / "new.py").write_text("x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "bad attempt")
    (repo / "app.py").unlink()

    subprocess.run(restore_steps(ckpt)["git"], shell=True, check=True, cwd="/")
    assert (repo / "app.py").read_text() == "v2 good\n"
    assert not (repo / "new.py").exists()
    assert git(repo, "log", "--oneline").count("\n") == 2  # history untouched


def test_claude_code_ref_is_the_latest_real_prompt(tmp_path):
    path = transcript(tmp_path, [
        prompt("u1", "Add CSV export"),
        {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "ok"}]}},
        prompt("u2", [{"type": "text", "text": "Now make it stream rows"}]),
        prompt("u3", [{"type": "tool_result", "tool_use_id": "t", "content": "done"}]),
        prompt("u4", "<command-name>/clear</command-name>"),
        prompt("u5", "caveat", isMeta=True),
        prompt("u6", [{"type": "text", "text": "[Request interrupted by user for tool use]"}]),
    ])
    ref = claude_code_ref("s1", path)
    assert (ref.message_uuid, ref.prompt, ref.session_id) == ("u2", "Now make it stream rows", "s1")
    assert claude_code_ref("s1", str(tmp_path / "missing.jsonl")) is None
    assert claude_code_ref(None, path) is None


def test_restore_steps(tmp_path):
    path = transcript(tmp_path, [prompt("u1", "Add CSV export")])
    only_cc = capture(str(tmp_path), "s1", path)
    steps = restore_steps(only_cc)
    assert steps["git"] is None and '/rewind' in steps["claude_code"] and "Add CSV export" in steps["claude_code"]
    assert restore_steps(None)["checkpoint_id"] is None and "Undo" in restore_steps(None)["note"]
    assert capture(str(tmp_path)) is None


# --- rollback rules (pure) ---------------------------------------------------------

def test_rules_retry_then_escalate_then_resume():
    task = TaskState(team_id="t", goal="g")
    good, later = (Checkpoint(cwd="/p", git=GitRef(repo="/p", commit=c)) for c in ("aaa", "bbb"))
    task.checkpoints = [good, later]

    first = rollback.apply_outcome(task, failed=True, chosen="A", reason="r1", checkpoint=good, max_attempts=3)
    assert first["action"] == "rollback" and first["attempt"] == 1 and first["failure_reason"] == "r1"
    # The host never restored, so the next checkpoint is broken: still roll back to the known-good one.
    second = rollback.apply_outcome(task, failed=True, chosen="B", reason="r2", checkpoint=later, max_attempts=3)
    assert second["restore"]["checkpoint_id"] == good.id
    assert [f["chosen"] for f in second["failures"]] == ["A", "B"]

    third = rollback.apply_outcome(task, failed=True, chosen="C", reason="r3", checkpoint=later, max_attempts=3)
    assert third["action"] == "escalate" and task.status == "escalated"
    assert rollback.retry_state(task, 3)["escalated"] is True

    rollback.resume(task)
    assert (task.status, task.attempts, task.failures, task.rollback_to) == ("active", 0, [], None)
    assert rollback.retry_state(task, 3) is None


def test_success_resets_the_streak():
    task = TaskState(team_id="t", goal="g")
    rollback.apply_outcome(task, failed=True, chosen="A", reason="r", checkpoint=None, max_attempts=3)
    assert rollback.apply_outcome(task, failed=False, chosen="B", reason="", checkpoint=None, max_attempts=3) is None
    assert task.attempts == 0 and task.failures == []


def test_decision_never_gets_a_checkpoint_older_than_the_previous_decision():
    from horizon.models import DecisionEntry

    task = TaskState(team_id="t", goal="g")
    ckpt = Checkpoint(cwd="/p", recall_id="rc_1")
    task.checkpoints = [ckpt]
    assert rollback.checkpoint_for_decision(task, "rc_1") is ckpt
    assert rollback.checkpoint_for_decision(task, None) is ckpt
    task.decisions.append(DecisionEntry(situation="s", chosen="c", success=1.0))  # passed after the checkpoint
    assert rollback.checkpoint_for_decision(task, "rc_1") is ckpt  # an explicit match still wins
    assert rollback.checkpoint_for_decision(task, "rc_other") is None


def test_checkpoint_list_is_capped_but_keeps_the_rollback_target():
    task = TaskState(team_id="t", goal="g")
    target = Checkpoint(cwd="/p")
    rollback.attach_checkpoint(task, target)
    task.rollback_to = target.id
    for _ in range(rollback.MAX_CHECKPOINTS + 5):
        rollback.attach_checkpoint(task, Checkpoint(cwd="/p"))
    assert len(task.checkpoints) == rollback.MAX_CHECKPOINTS + 1
    assert rollback.find_checkpoint(task, target.id) is target


# --- end to end through the service and the hook -----------------------------------

def test_failed_attempt_rolls_back_feeds_reason_back_and_escalates(platform, settings, repo, tmp_path):
    cc = transcript(tmp_path, [prompt("u1", "Add CSV export")])
    task_id = platform.start_task("Add CSV export")["task_id"]

    pre_tool_use(str(repo), settings, transcript_path=cc)
    ctx = platform.recall_context(task_id, "choose a csv writer")
    good = ctx["checkpoint_id"]
    assert good.startswith("ckpt_") and "retry" not in ctx["task_state"]

    # Attempt 1 fails.
    (repo / "app.py").write_text("broken\n")
    out = platform.record_outcome(task_id, "choose a csv writer", "pandas", tests_passed=3, tests_failed=2,
                                  recall_id=ctx["recall_id"], failure_reason="pandas is not installed")
    rb = out["rollback"]
    assert out["checkpoint_id"] == good and out["task_status"] == "active"
    assert rb["action"] == "rollback" and rb["attempt"] == 1 and rb["max_attempts"] == 3
    assert rb["restore"]["checkpoint_id"] == good and "/rewind" in rb["restore"]["claude_code"]
    subprocess.run(rb["restore"]["git"], shell=True, check=True)
    assert (repo / "app.py").read_text() == "v1\n"

    # The retry's decision step sees why the last attempt failed.
    pre_tool_use(str(repo), settings)
    retry = platform.recall_context(task_id, "choose a csv writer")["task_state"]["retry"]
    assert retry["previous_failures"] == [{"attempt": 1, "chosen": "pandas", "reason": "pandas is not installed"}]
    assert retry["rollback_to"] == good
    assert platform.tasks.get(task_id, "local").decisions[-1].checkpoint_id == good

    # Attempts 2 and 3 fail (reason defaults to the test summary): escalate to a human.
    out = platform.record_outcome(task_id, "s", "csv module", tests_passed=0, tests_failed=1)
    assert out["rollback"]["failures"][-1]["reason"] == "tests 0/1 passed"
    out = platform.record_outcome(task_id, "s", "manual join", success=0.0)
    assert out["rollback"]["action"] == "escalate" and out["task_status"] == "escalated"
    assert out["rollback"]["restore"]["checkpoint_id"] == good
    ctx = platform.recall_context(task_id, "what now")
    assert ctx["task_state"]["status"] == "escalated" and "human_guidance" in ctx["next"]

    # The escalated task stays open for the hooks, flagged for the user.
    start = json.loads(run_hook("session-start", json.dumps({"cwd": str(repo)}), settings))
    assert "escalated" in start["hookSpecificOutput"]["additionalContext"]

    # The human answers; the task resumes with a fresh streak and their guidance in the progress log.
    ctx = platform.recall_context(task_id, "retry", human_guidance="Use the stdlib csv module and add the dep")
    state = ctx["task_state"]
    assert state["status"] == "active" and "retry" not in state
    assert "Human guidance: Use the stdlib csv module" in state["recent_progress"][-1]
    out = platform.record_outcome(task_id, "s", "stdlib csv", tests_passed=5, tests_failed=0, task_complete=True)
    assert out["rollback"] is None and out["task_status"] == "completed"


def test_hook_ignores_other_tools_and_checkpoints_are_claimed_once(platform, settings, repo):
    task_id = platform.start_task("x")["task_id"]
    body = {"session_id": "s1", "cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": "ls"}}
    run_hook("pre-tool-use", json.dumps(body), settings)
    assert platform.recall_context(task_id, "s")["checkpoint_id"] is None

    pre_tool_use(str(repo), settings)
    assert platform.recall_context(task_id, "s")["checkpoint_id"] is not None
    assert platform.recall_context(task_id, "s")["checkpoint_id"] is None  # no new snapshot, none attached


def test_rollback_works_when_memrouter_is_down(settings, task_store, repo):
    def broken():
        raise ConnectionError("memrouter down")

    platform = Platform(settings, task_store, broken, cwd=str(repo))
    task_id = platform.start_task("x")["task_id"]
    pre_tool_use(str(repo), settings)
    ctx = platform.recall_context(task_id, "s")
    assert ctx["memory_status"] == "unavailable" and ctx["checkpoint_id"]
    out = platform.record_outcome(task_id, "s", "c", tests_passed=0, tests_failed=1)
    assert out["memory_status"] == "unavailable"
    assert out["rollback"]["restore"]["checkpoint_id"] == ctx["checkpoint_id"]


def test_retry_limit_and_threshold_are_configurable(settings, task_store, memrouter, project_dir):
    import dataclasses

    lenient = dataclasses.replace(settings, max_attempts=1, rollback_below=0.8)
    platform = Platform(lenient, task_store, lambda: memrouter, cwd=project_dir)
    task_id = platform.start_task("x")["task_id"]
    assert platform.record_outcome(task_id, "s", "c", tests_passed=9, tests_failed=1)["rollback"] is None
    assert platform.record_outcome(task_id, "s", "c", success=0.5)["rollback"]["action"] == "escalate"


def _pre(cwd, settings):
    body = {"session_id": "s1", "cwd": cwd, "tool_name": "mcp__horizon__recall_context", "tool_input": {}}
    out = run_hook("pre-tool-use", json.dumps(body), settings)
    return json.loads(out) if out else None


def test_recall_after_rollback_is_denied_once_until_restored(platform, settings, repo):
    task_id = platform.start_task("x")["task_id"]
    assert _pre(str(repo), settings) is None
    ctx = platform.recall_context(task_id, "s")
    (repo / "app.py").write_text("broken\n")
    rb = platform.record_outcome(task_id, "s", "A", tests_passed=0, tests_failed=1, recall_id=ctx["recall_id"])
    assert rb["rollback"]["action"] == "rollback"

    # Not restored: the retry's recall is denied with the restore command...
    denied = _pre(str(repo), settings)["hookSpecificOutput"]
    assert denied["permissionDecision"] == "deny" and rb["rollback"]["restore"]["git"] in denied["permissionDecisionReason"]
    # ...but only once, so a host that can't restore is never stuck.
    assert _pre(str(repo), settings) is None


def test_recall_after_a_restore_goes_through(platform, settings, repo):
    task_id = platform.start_task("x")["task_id"]
    _pre(str(repo), settings)
    ctx = platform.recall_context(task_id, "s")
    (repo / "app.py").write_text("broken\n")
    rb = platform.record_outcome(task_id, "s", "A", tests_passed=0, tests_failed=1, recall_id=ctx["recall_id"])
    subprocess.run(rb["rollback"]["restore"]["git"], shell=True, check=True)
    assert _pre(str(repo), settings) is None
    platform.recall_context(task_id, "s")
    assert platform.tasks.get(task_id, "local").restore_check is None
    stats = platform.tasks.stats()
    assert stats["checkpoints_captured"] == stats["checkpoints_attached"] == 2


def test_a_severe_outcome_escalates_at_once(platform):
    task_id = platform.start_task("x")["task_id"]
    out = platform.record_outcome(task_id, "s", "rm -rf the cache dir", success=1.0, severity="severe")
    assert out["rollback"]["action"] == "escalate" and out["rollback"]["severe"] and out["task_status"] == "escalated"


def test_hosts_without_hooks_can_report_their_own_checkpoint(platform, repo):
    task_id = platform.start_task("x")["task_id"]
    head = git(repo, "rev-parse", "HEAD").strip()
    ctx = platform.recall_context(task_id, "s", checkpoint_commit=head)
    assert ctx["checkpoint_id"]
    out = platform.record_outcome(task_id, "s", "c", success=0.0, recall_id=ctx["recall_id"])
    assert out["rollback"]["restore"]["git"] == f"git -C . restore --source={head} --staged --worktree -- :/"
    with pytest.raises(ToolInputError):
        platform.recall_context(task_id, "s", checkpoint_commit="; rm -rf /")

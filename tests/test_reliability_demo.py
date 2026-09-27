"""The reliability demo: report analysis, and run.sh end to end with a fake `claude` binary."""

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "demo" / "reliability"
spec = importlib.util.spec_from_file_location("reliability_report", DEMO / "report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

H = "mcp__horizon__"


def call(name, **inp):
    return {"name": name, "input": inp}


def test_analyse_full_workflow():
    calls = [call(H + "start_task", goal="g"), call(H + "recall_context"), call("Read"), call("Edit"),
             call("Bash", command="python -m pytest -q tests/test_x.py"), call(H + "record_outcome", task_complete=True)]
    row = report.analyse(calls)
    assert row["started_before_edit"] and row["recalled_before_edit"]
    assert row["recorded_after_last_test"] and row["task_complete_sent"] and row["ran_tests"]


def test_analyse_misses():
    calls = [call("Edit"), call(H + "start_task"), call(H + "record_outcome"), call("Bash", command="pytest")]
    row = report.analyse(calls)
    assert not row["started_before_edit"]
    assert not row["recalled_before_edit"]
    assert not row["recorded_after_last_test"]  # tests ran after the last record_outcome


def test_tool_calls_parses_stream_json(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "hi"}, {"type": "tool_use", "name": H + "start_task", "input": {"goal": "g"}}]}}),
        "not json",
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {}}]}}),
    ]))
    assert [c["name"] for c in report.tool_calls(t)] == [H + "start_task", "Edit"]


def test_tasks_json_matches_project():
    tasks = json.loads((DEMO / "tasks.json").read_text())
    assert 3 <= len(tasks) <= 5
    for t in tasks:
        assert (DEMO / "project" / t["check"]).exists()
        assert "horizon" not in t["prompt"].lower()  # prompts must not mention the tools


FAKE_CLAUDE = r'''#!{python}
"""Stands in for `claude -p`: runs the project's real hooks and prints a stream-json transcript."""
import json, subprocess, sys
settings = json.load(open(".claude/settings.json"))
def run_hook(event, payload):
    for group in settings["hooks"][event]:
        for h in group["hooks"]:
            subprocess.run(h["command"], shell=True, input=json.dumps(payload), text=True, capture_output=True)
run_hook("SessionStart", {{"cwd": ".", "session_id": "fake", "source": "startup"}})
test = subprocess.run([sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True)
run_hook("PostToolUse", {{"cwd": ".", "session_id": "fake", "tool_name": "Bash",
                          "tool_input": {{"command": "python -m pytest -q"}},
                          "tool_response": {{"stdout": test.stdout, "stderr": test.stderr}}}})
H = "mcp__horizon__"
for name, inp in [(H + "start_task", {{"goal": sys.argv[2]}}), (H + "recall_context", {{}}), ("Edit", {{}}),
                  ("Bash", {{"command": "python -m pytest -q"}}), (H + "record_outcome", {{"task_complete": True}})]:
    print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": name, "input": inp}}]}}}}))
'''


@pytest.mark.skipif(sys.platform == "win32", reason="bash script")
def test_run_sh_end_to_end_with_fake_claude(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(FAKE_CLAUDE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "out"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PYTHON": sys.executable,
           "HORIZON_EMBEDDER": "hash"}
    env.pop("HORIZON_DB_URL", None)
    env.pop("CLAUDE_PROJECT_DIR", None)

    proc = subprocess.run(["bash", str(DEMO / "run.sh"), str(out)], capture_output=True, text=True, env=env,
                          timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    project = out / "project"
    assert json.loads((project / ".mcp.json").read_text())["mcpServers"]["horizon"]
    assert "horizon:start" in (project / "CLAUDE.md").read_text()

    result = json.loads((out / "report.json").read_text())
    assert set(result["tasks"]) == {"1-slugify", "2-duration", "3-word-count", "4-chunk"}
    assert result["rates"]["started_before_edit"] == 1.0
    assert result["rates"]["tasks_passing"] == 0.0  # the fake doesn't fix anything
    # The real hooks ran against the run's own store and captured the real test counts.
    assert result["horizon_db"]["test_runs_captured"] == 4


def test_setup_only(tmp_path):
    env = {**os.environ, "PYTHON": sys.executable}
    proc = subprocess.run(["bash", str(DEMO / "run.sh"), "--setup-only", str(tmp_path / "m")], capture_output=True,
                          text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "m" / "project" / ".claude" / "settings.json").exists()
    assert "TASKS.md" in proc.stdout

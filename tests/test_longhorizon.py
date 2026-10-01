"""The long-horizon evaluation harness (evals/longhorizon), driven by fake `claude` binaries."""

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "evals" / "longhorizon"


def load(name):
    spec = importlib.util.spec_from_file_location(f"lh_{name}", EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EVAL))
    spec.loader.exec_module(module)
    return module


FAKE = '''#!{python}
"""A fake `claude -p`: {what}."""
import json, pathlib, shutil
repo = pathlib.Path.cwd()
{body}
print(json.dumps({{"type": "result", "subtype": "success", "result": "done", "num_turns": 3,
                  "total_cost_usd": 0.01, "usage": {{"input_tokens": 10, "output_tokens": 5}}}}))
'''


def fake(tmp_path, name, body, what):
    path = tmp_path / name
    path.write_text(FAKE.format(python=sys.executable, body=body, what=what))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.mark.parametrize("scenario", ["ledger", "notes"])
def test_reference_solutions_pass_every_hidden_test(scenario, tmp_path):
    """The hidden tests are passable: the reference implementation passes all of them, with no violations."""
    run = load("run")
    good = fake(tmp_path, "good", f"shutil.copytree({str(EVAL / 'reference' / scenario)!r}, repo / {scenario!r}, "
                                  "dirs_exist_ok=True)", "writes the reference solution")
    assert run.main(["--scenario", scenario, "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", good]) == 0
    result = json.loads((tmp_path / "out" / f"{scenario}-baseline" / "results.json").read_text())
    assert [r["session"] for r in result["sessions"]] == [1, 2, 3, 4]
    last = result["sessions"][-1]
    assert last["hidden"]["failed"] == [] and len(last["hidden"]["passed"]) == 4 and last["violations"] == []
    assert last["turns"] == 3 and last["cost_usd"] == 0.01


def test_constraint_drift_is_caught(tmp_path):
    """Floats, a third-party import, 3.10 syntax and a renamed function are each reported."""
    run = load("run")
    body = '''pkg = repo / "ledger"; pkg.mkdir(exist_ok=True)
(pkg / "__init__.py").write_text("""import requests
RATES = {"USD": 1.0}
def new_ledger(): return []
def add_expense(book, amount, category, note=""): book.append((amount / 100, category))
def total(ledger, category=None) -> float | None: return sum(a for a, c in ledger)
""")'''
    bad = fake(tmp_path, "bad", body, "drifts from every session-1 constraint")
    run.main(["--scenario", "ledger", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", bad,
              "--sessions", "1"])
    [row] = json.loads((tmp_path / "out" / "ledger-baseline" / "results.json").read_text())["sessions"]
    text = " ".join(row["violations"])
    assert "import requests" in text and "`X | Y` annotation" in text and "add_expense" in text
    assert row["hidden"]["failed"]  # 1349 cents came back as a float total


def test_horizon_arm_installs_horizon_and_the_report_compares(tmp_path, monkeypatch):
    run, report = load("run"), load("report")
    probe = fake(tmp_path, "probe", "assert (repo / '.mcp.json').exists() and (repo / 'CLAUDE.md').exists()",
                 "checks Horizon is installed")
    run.main(["--scenario", "notes", "--arm", "horizon", "--out", str(tmp_path / "out"), "--claude", probe,
              "--sessions", "2"])
    plain = fake(tmp_path, "plain", "assert not (repo / '.mcp.json').exists()", "checks the baseline has no Horizon")
    run.main(["--scenario", "notes", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", plain,
              "--sessions", "1"])
    report.main(["report", str(tmp_path / "out")])
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["notes/horizon"]["sessions_run"] == 2  # the probe passed: Horizon was installed
    assert summary["notes/baseline"]["sessions_run"] == 1 and summary["notes/baseline"]["aborted"] is False


def test_usage_limit_stops_the_scenario(tmp_path):
    run = load("run")
    limit = tmp_path / "limit"
    limit.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({{'type': 'result', 'result': "
                     "\"You've hit your session limit\"}))\n")
    limit.chmod(limit.stat().st_mode | stat.S_IEXEC)
    run.main(["--scenario", "ledger", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", str(limit)])
    rows = json.loads((tmp_path / "out" / "ledger-baseline" / "results.json").read_text())["sessions"]
    assert len(rows) == 1 and rows[0]["aborted"] == "usage limit"


def test_team_scenario_gives_each_project_a_fresh_repo(tmp_path):
    """Three projects, one fresh repo each: the reference passes every project's hidden tests, and a typical
    fresh-start implementation (dashed uuids, "+00:00" stamps, a print) fails the team's rules."""
    run = load("run")
    ref = EVAL / "reference" / "team"
    good = fake(tmp_path, "good", f"shutil.copytree({str(ref)!r} + '/' + repo.name, repo / repo.name)",
                "writes the reference for this project")
    run.main(["--scenario", "team", "--arm", "baseline", "--out", str(tmp_path / "ok"), "--claude", good])
    rows = json.loads((tmp_path / "ok" / "team-baseline" / "results.json").read_text())["sessions"]
    assert [(len(r["hidden"]["passed"]), r["hidden"]["failed"], r["violations"]) for r in rows] == [(4, [], [])] * 3
    assert sorted(p.name for p in (tmp_path / "ok" / "team-baseline").iterdir() if p.is_dir()) == \
        ["invoices", "payouts", "subscriptions"]

    body = '''pkg = repo / "invoices"; pkg.mkdir()
(pkg / "__init__.py").write_text("""import json, uuid
from datetime import datetime, timezone
def create_invoice(customer, amount_cents, due):
    print("creating invoice")
    return {"id": str(uuid.uuid4()), "customer": customer, "amount_cents": amount_cents,
            "created_at": datetime.now(timezone.utc).isoformat(), "due": due.isoformat(), "status": "open"}
def overdue(invoices, now): return []
def to_json(i): return json.dumps(i)
def from_json(t): return json.loads(t)
""")'''
    drift = fake(tmp_path, "drift", body, "ignores the team's rules")
    run.main(["--scenario", "team", "--arm", "baseline", "--out", str(tmp_path / "bad"), "--claude", drift,
              "--sessions", "1"])
    [row] = json.loads((tmp_path / "bad" / "team-baseline" / "results.json").read_text())["sessions"]
    assert set(row["hidden"]["failed"]) == {"test_invoice_follows_the_team_rules", "test_overdue_compares_real_instants",
                                            "test_bad_amount_raises_value_error"}


def test_backlog_starts_from_a_codebase_and_checks_policy_every_session(tmp_path):
    """The backlog scenario copies its starting codebase in; the reference passes every ticket and the policy,
    and a session that breaks the policy (no changelog entry, a float) is caught."""
    run = load("run")
    ref = EVAL / "reference" / "backlog"
    good = fake(tmp_path, "good", f"shutil.copytree({str(ref)!r}, repo, dirs_exist_ok=True)", "writes the reference")
    run.main(["--scenario", "backlog", "--arm", "baseline", "--out", str(tmp_path / "ok"), "--claude", good])
    rows = json.loads((tmp_path / "ok" / "backlog-baseline" / "results.json").read_text())["sessions"]
    assert len(rows) == 8 and all(r["hidden"]["failed"] == [] for r in rows)
    assert len(rows[-1]["hidden"]["passed"]) == 8 + 5  # 8 tickets and 5 policy checks

    body = '''(repo / "shop" / "inventory.py").write_text((repo / "shop" / "inventory.py").read_text() + """
def remove_item(name, qty):
    \\"\\"\\"Remove units.\\"\\"\\"
    if qty <= 0 or _STOCK.get(name, 0) < qty * 1.0:
        raise ValueError(name)
    _STOCK[name] -= qty
""")
(repo / "shop" / "__init__.py").write_text((repo / "shop" / "__init__.py").read_text() + "from shop.inventory import remove_item\\n")'''
    drift = fake(tmp_path, "drift", body, "does ticket 1 but breaks the policy")
    run.main(["--scenario", "backlog", "--arm", "baseline", "--out", str(tmp_path / "bad"), "--claude", drift,
              "--sessions", "1"])
    [row] = json.loads((tmp_path / "bad" / "backlog-baseline" / "results.json").read_text())["sessions"]
    assert set(row["hidden"]["failed"]) == {"test_every_public_function_is_in_the_changelog", "test_money_is_never_a_float"}
    assert row["test_runs"] == 0 and row["failed_test_runs"] == 0


def test_failed_test_runs_are_counted_from_the_transcript():
    run = load("run")
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "./ci.sh"}},
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "python -m pytest -q"}},
            {"type": "tool_use", "id": "c", "name": "Bash", "input": {"command": "python -m pytest --version"}},
            {"type": "tool_use", "id": "d", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "Exit code 1\nF..\n1 failed, 14 passed in 0.04s"},
            {"type": "tool_result", "tool_use_id": "b", "content": [{"type": "text", "text": "15 passed in 0.1s"}]},
            {"type": "tool_result", "tool_use_id": "c", "content": "pytest 9.0"},
            {"type": "tool_result", "tool_use_id": "d", "content": "3 failed attempts.txt"}]}},
    ]
    assert run.test_run_counts(events) == (2, 1)


def test_failing_checks_come_from_the_agents_own_test_runs():
    run = load("run")
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "./ci.sh"}},
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "cat notes.txt"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "content":
                "F.E\n=== short test summary info ===\n"
                "FAILED tests/test_policy.py::test_no_local_time - AssertionError: ['a.py:3: now()']\n"
                "ERROR tests/test_db.py::test_writers[8] - OSError\n1 failed, 1 error, 1 passed in 0.1s"},
            {"type": "tool_result", "tool_use_id": "b", "content": "FAILED tests/test_x.py::test_not_a_run"}]}},
    ]
    assert run.failing_checks(events) == ["tests/test_db.py::test_writers[8]",
                                          "tests/test_policy.py::test_no_local_time"]


SCRIPTED = '''#!{python}
"""A fake `claude -p` that plays a scripted session per call: {what}."""
import json, pathlib, shutil
repo = pathlib.Path.cwd()
counter = pathlib.Path(__file__).with_suffix(".n")
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
{body}
def ci(output):
    print(json.dumps({{"type": "assistant", "message": {{"content": [
        {{"type": "tool_use", "id": f"t{{n}}", "name": "Bash", "input": {{"command": "./ci.sh"}}}}]}}}}))
    print(json.dumps({{"type": "user", "message": {{"content": [
        {{"type": "tool_result", "tool_use_id": f"t{{n}}", "content": output}}]}}}}))
ci(OUTPUT)
print(json.dumps({{"type": "result", "subtype": "success", "result": "done", "num_turns": 3, "session_id": f"sess-{{n}}",
                  "total_cost_usd": 0.01, "usage": {{"input_tokens": 10, "output_tokens": 5}}}}))
'''


def scripted(tmp_path, name, body, what):
    path = tmp_path / name
    path.write_text(SCRIPTED.format(python=sys.executable, body=body, what=what))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_repeat_failures_and_regressions_are_counted_across_sessions(tmp_path):
    """Session 1 writes the reference; CI shows the changelog check failing in sessions 1 and 3 (one repeat) and
    session 2 breaks ticket 1 (one regression), all measured from ground truth, not the agent's summary."""
    run, report = load("run"), load("report")
    ref = EVAL / "reference" / "backlog"
    changelog = "FAILED tests/test_policy.py::test_every_public_function_is_in_the_changelog - AssertionError"
    body = f'''if n == 1:
    shutil.copytree({str(ref)!r}, repo, dirs_exist_ok=True)
if n == 2:
    inv = repo / "shop" / "inventory.py"
    inv.write_text(inv.read_text().replace("_STOCK[name] -= qty", "_STOCK[name] -= qty * 2"))
OUTPUT = {{1: {changelog!r} + "\\n1 failed, 4 passed", 2: "5 passed", 3: {changelog!r} + "\\n1 failed"}}[n]'''
    agent = scripted(tmp_path, "agent", body, "one repeat failure and one regression")
    run.main(["--scenario", "backlog", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", agent,
              "--sessions", "3"])
    rows = json.loads((tmp_path / "out" / "backlog-baseline" / "results.json").read_text())["sessions"]
    check = "tests/test_policy.py::test_every_public_function_is_in_the_changelog"
    assert [r["failed_checks"] for r in rows] == [[check], [], [check]]
    assert [r["repeat_failures"] for r in rows] == [[], [], [check]]
    assert [r["regressions"] for r in rows] == [[], ["test_remove_item"], []]
    assert [r["session_id"] for r in rows] == ["sess-1", "sess-2", "sess-3"]
    assert "horizon" not in rows[0]  # baseline has no Horizon to look into
    report.main(["report", str(tmp_path / "out")])
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())["backlog/baseline"]
    assert (summary["repeat_failures"], summary["regressions"], summary["injected_chars"]) == (1, 1, 0)


HOOK_PLAYER = '''
settings = json.loads((repo / ".claude" / "settings.json").read_text())
import subprocess
outs = []
def fire(event, **extra):
    for group in settings["hooks"].get(event, []):
        for hook in group["hooks"]:
            payload = {"session_id": f"sess-{n}", "cwd": str(repo), "hook_event_name": event, **extra}
            outs.append(subprocess.run(hook["command"], shell=True, input=json.dumps(payload), text=True,
                                       capture_output=True).stdout.strip())
fire("SessionStart", source="startup")
fire("UserPromptSubmit", prompt="Add a notes module. Always keep it stdlib only.")
fire("Stop", stop_hook_active=False)
fire("SessionEnd", reason="other")
pathlib.Path(__file__).with_suffix(f".{n}.json").write_text(json.dumps(outs))
OUTPUT = "1 passed"
'''


def test_the_observe_arm_records_events_silently_and_lean_injection_is_measured(tmp_path):
    """The fake agent fires the installed hooks the way Claude Code does. Observe's hooks print nothing yet log
    every session; lean's context is counted; the run's event log is exported for the world model."""
    run = load("run")
    for arm in ("observe", "lean"):
        player = scripted(tmp_path, f"player-{arm}", HOOK_PLAYER, "fires the installed hooks")
        run.main(["--scenario", "notes", "--arm", arm, "--out", str(tmp_path / "out"), "--claude", player,
                  "--sessions", "2"])
    work = tmp_path / "out" / "notes-observe"
    rows = json.loads((work / "results.json").read_text())["sessions"]
    assert [json.loads((tmp_path / f"player-observe.{n}.json").read_text()) for n in (1, 2)] == [[""] * 4] * 2
    session = {"session_start": 1, "prompt": 1, "turn_end": 1, "session_end": 1}
    assert [r["horizon"] for r in rows] == [{"events": session, "injected_chars": 0, "stall_warnings": 0,
                                                  "dead_end_warnings": 0}] * 2
    exported = [json.loads(line) for line in (work / "events.jsonl").read_text().splitlines()]
    assert [e["session_id"] for e in exported] == ["sess-1"] * 4 + ["sess-2"] * 4
    assert not (work / "repo" / "CLAUDE.md").exists() and not (work / "repo" / ".mcp.json").exists()

    lean = json.loads((tmp_path / "out" / "notes-lean" / "results.json").read_text())["sessions"]
    assert all(r["horizon"]["events"] == session and r["horizon"]["injected_chars"] > 0 for r in lean)
    assert any(json.loads((tmp_path / "player-lean.1.json").read_text()))


def test_the_candidate_arm_is_lean_with_session_memory_on(tmp_path):
    """Session 1's test runs (one failing first) make it an episode; in the candidate arm (with the composer, whose
    forecast expects that trouble again), session 2 starts with what it taught.
    Lean (the default) says nothing about it."""
    run = load("run")
    run_ = 'fire("PostToolUse", tool_name="Bash", tool_input={"command": "pytest -q"}, tool_response={"stdout": %r})\n'
    player = HOOK_PLAYER.replace('fire("Stop"', run_ % "= 1 failed, 2 passed in 0.1s =" + run_ % "= 3 passed in 0.1s ="
                                 + 'fire("Stop"')
    for arm in ("lean", "candidate"):
        run.main(["--scenario", "notes", "--arm", arm, "--out", str(tmp_path / "out"), "--claude",
                  scripted(tmp_path, f"player-{arm}", player, "fires the installed hooks"), "--sessions", "2"])
    said = {arm: json.loads((tmp_path / f"player-{arm}.2.json").read_text())[1] for arm in ("lean", "candidate")}
    assert "similar past sessions" in said["candidate"] and "3/3 tests passing" in said["candidate"]
    assert "similar past sessions" not in said["lean"]
    rows = json.loads((tmp_path / "out" / "notes-candidate" / "results.json").read_text())["sessions"]
    lean = json.loads((tmp_path / "out" / "notes-lean" / "results.json").read_text())["sessions"]
    assert rows[1]["horizon"]["injected_chars"] > lean[1]["horizon"]["injected_chars"]
    settings = {arm: json.loads((tmp_path / "out" / f"notes-{arm}" / "results.json").read_text())["settings"]
                for arm in ("lean", "candidate")}
    assert settings == {"lean": {}, "candidate": {"HORIZON_SESSION_MEMORY": "on", "HORIZON_CONTEXT": "composer",
                                                  "HORIZON_STALL": "on", "HORIZON_DEAD_ENDS": "on"}}
    assert all(r["horizon"]["stall_warnings"] == 0 for r in rows + lean)


def test_set_adds_a_setting_to_an_arm_for_ablations(tmp_path):
    run = load("run")
    run.main(["--scenario", "notes", "--arm", "candidate", "--out", str(tmp_path / "out"), "--claude",
              scripted(tmp_path, "player", HOOK_PLAYER, "fires the installed hooks"), "--sessions", "1",
              "--set", "HORIZON_CONTEXT=classic"])
    results = json.loads((tmp_path / "out" / "notes-candidate" / "results.json").read_text())
    assert results["settings"] == {"HORIZON_SESSION_MEMORY": "on", "HORIZON_CONTEXT": "classic", "HORIZON_STALL": "on",
                                   "HORIZON_DEAD_ENDS": "on"}
    said = json.loads((tmp_path / "player.1.json").read_text())[0]
    assert "Earlier tasks in this project" not in said and "Recent tasks" not in said  # nothing earlier yet
    assert run.EXTRA_ENV == {"HORIZON_CONTEXT": "classic"}
    run.main(["--scenario", "notes", "--arm", "lean", "--out", str(tmp_path / "out2"), "--claude",
              scripted(tmp_path, "player2", HOOK_PLAYER, "fires the installed hooks"), "--sessions", "1"])
    assert run.EXTRA_ENV == {}  # a later run doesn't inherit it


def test_resume_from_another_run_and_seed_its_test_history(tmp_path, monkeypatch):
    """Replay: a lean run starts from a baseline run's repo after session 2, with Horizon's memory seeded from
    that run's real test runs, so a check that failed in both sessions is already a known pitfall."""
    run = load("run")
    marker = fake(tmp_path, "marker", "n = len(list(repo.glob('m*'))); (repo / f'm{n + 1}').write_text('x')",
                  "leaves one marker file per session")
    run.main(["--scenario", "backlog", "--arm", "baseline", "--out", str(tmp_path / "a"), "--claude", marker,
              "--sessions", "3"])
    source = tmp_path / "a" / "backlog-baseline"
    fail = ("Exit code 1\nF....\nFAILED tests/test_policy.py::test_every_public_function_is_in_the_changelog - "
            "AssertionError\n1 failed, 4 passed in 0.04s")
    for i in (1, 2):
        (source / f"session{i}.jsonl").write_text("\n".join(json.dumps(e) for e in [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "./ci.sh"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": fail}]}},
        ]))
    probe = fake(tmp_path, "probe", "assert sorted(p.name for p in repo.glob('m*')) == ['m1', 'm2']",
                 "checks it starts from the state after session 2")
    db = tmp_path / "b" / "backlog-lean" / "horizon.db"
    run.main(["--scenario", "backlog", "--arm", "lean", "--out", str(tmp_path / "b"), "--claude", probe,
              "--resume", str(source), "--from-session", "3", "--sessions", "3"])
    rows = json.loads((tmp_path / "b" / "backlog-lean" / "results.json").read_text())["sessions"]
    assert [r["session"] for r in rows] == [3] and rows[0]["aborted"] is None

    from horizon.db import connect
    from horizon.taskstate.store import TaskStore

    repo = str((tmp_path / "b" / "backlog-lean" / "repo").resolve())
    assert TaskStore(connect(f"sqlite:///{db}")).recurring_failures(repo) == [
        ("tests/test_policy.py::test_every_public_function_is_in_the_changelog", 2)]

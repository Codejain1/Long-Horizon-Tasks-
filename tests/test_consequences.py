"""Phase 5: consequence checking for close calls, and the world-model decision log (PROJECT.md §6)."""

import json
import os
import subprocess
import sys

import pytest
from test_decision import CLOSE, OPTS, FakeScorer, with_scorer

from horizon.decision.layer import Option
from horizon.decision.log import SCHEMA, VERSION
from horizon.hooks import run_hook
from horizon.service import ToolInputError

SITUATION = "choose a database for the order service"
PASS = {"option": "PostgreSQL", "static_checks": [{"name": "pip install --dry-run psycopg", "passed": True}],
        "spike": {"ran": True, "passed": True, "tests_passed": 4, "tests_failed": 0,
                  "metrics": {"latency_ms": 3.5}, "duration_s": 240}, "notes": "fine"}
FAIL = {"option": "MongoDB", "static_checks": [{"name": "driver resolves", "passed": True}],
        "spike": {"ran": True, "passed": False, "tests_passed": 1, "tests_failed": 3, "metrics": {"latency_ms": 9}}}


@pytest.fixture
def platform5(settings, task_store, memrouter, project_dir):
    scorer = FakeScorer(**CLOSE, second={"success": (0.9, 0.4), "breaks_tests": (0.05, 0.6)})
    return with_scorer(settings, task_store, memrouter, project_dir, scorer), scorer


def test_close_call_plan_submit_decide_and_log(platform5, memrouter):
    platform, scorer = platform5
    task_id = platform.start_task("Add order persistence")["task_id"]
    r = platform.evaluate_options(task_id, SITUATION, OPTS)
    assert r["decision"] == "check_consequences" and r["chosen"] is None
    plan = r["consequence_plan"]
    assert plan["mode"] == "spikes" and {s["option"] for s in plan["spikes"]} == {"PostgreSQL", "MongoDB"}
    assert "answers" not in r  # raw scorer answers go to the log, not to the host

    final = platform.submit_consequences(task_id, r["decision_id"], [PASS, FAIL])
    assert final["decision"] == "clear_winner" and final["chosen"] == "PostgreSQL"
    assert final["eliminated"] == ["MongoDB"] and final["settled_by"] == "consequences"
    assert "consequences" in scorer.calls[-1][0]  # pass 2 saw the evidence

    # The spikes became memory (MEMROUTER §13).
    past = memrouter.lookup_simulation(team_id="local", situation=SITUATION, options=["PostgreSQL", "MongoDB"])
    assert past["PostgreSQL"]["result"]["spike"]["tests_passed"] == 4 and past["MongoDB"]["result"]["spike"]["passed"] is False

    # The outcome is attached to the decision: state -> option -> consequences -> outcome.
    platform.record_outcome(task_id, SITUATION, "PostgreSQL", tests_passed=10, tests_failed=0, tokens=12000)
    rec = platform.decisions.get(r["decision_id"], "local")
    assert (rec["schema"], rec["version"], rec["stage"]) == (SCHEMA, VERSION, "decided")
    assert [p["pass"] for p in rec["passes"]] == [1, 2]
    assert rec["passes"][0]["answers"]["high_stakes"]["value"] == 0.1  # every raw scorer answer
    assert rec["passes"][1]["answers"]["o1.breaks_tests"]["value"] == 0.6
    assert rec["consequences"]["plan"]["mode"] == "spikes" and len(rec["consequences"]["submitted"]) == 2
    assert rec["final"]["chosen"] == "PostgreSQL" and rec["state"]["goal"] == "Add order persistence"
    [outcome] = rec["outcomes"]
    assert outcome["option"] == "PostgreSQL" and outcome["followed_decision"] and outcome["success"] == 1.0
    assert outcome["tokens"] == 12000 and outcome["judgement"]["method"] == "absolute"


def test_the_next_similar_tie_is_settled_from_memory(platform5):
    platform, scorer = platform5
    first = platform.start_task("Add order persistence")["task_id"]
    r = platform.evaluate_options(first, SITUATION, OPTS)
    platform.submit_consequences(first, r["decision_id"], [PASS, FAIL])

    again = platform.start_task("Persistence for the billing service")["task_id"]
    r = platform.evaluate_options(again, SITUATION, OPTS)
    assert r["decision"] == "clear_winner" and r["chosen"] == "PostgreSQL" and r["settled_by"] == "memory"
    assert r["eliminated"] == ["MongoDB"]  # the failed spike still counts, without spiking again


def test_try_and_rollback_when_options_are_cheap_to_undo(settings, task_store, memrouter, project_dir):
    subprocess.run(["git", "-C", project_dir, "init", "-q"], check=True)
    subprocess.run(["git", "-C", project_dir, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q",
                    "--allow-empty", "-m", "init"], check=True)
    platform = with_scorer(settings, task_store, memrouter, project_dir, FakeScorer(**CLOSE, reversible=(0.9, 0.85)))
    task_id = platform.start_task("x")["task_id"]
    run_hook("pre-tool-use", json.dumps({"session_id": "s1", "cwd": project_dir,
                                         "tool_name": "mcp__horizon__recall_context"}), settings)
    platform.recall_context(task_id, "s")  # attaches the git checkpoint
    r = platform.evaluate_options(task_id, "pick a date library", OPTS)
    assert r["decision"] == "try_and_rollback" and r["chosen"] == "PostgreSQL"
    assert r["try_order"] == ["PostgreSQL", "MongoDB"] and "roll" in r["next"]


def test_submit_consequences_is_validated_and_runs_once(platform5):
    platform, _ = platform5
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, SITUATION, OPTS)
    with pytest.raises(ToolInputError, match="decision_id"):
        platform.submit_consequences(task_id, "dec_nope", [PASS])
    with pytest.raises(ToolInputError, match="not one of the close options"):
        platform.submit_consequences(task_id, r["decision_id"], [{**PASS, "option": "Cassandra"}])
    platform.submit_consequences(task_id, r["decision_id"], [PASS])
    with pytest.raises(ToolInputError, match="already made"):  # §5: don't loop
        platform.submit_consequences(task_id, r["decision_id"], [PASS])
    other = platform.start_task("y")["task_id"]
    with pytest.raises(ToolInputError, match="decision_id"):  # not this task's decision
        platform.submit_consequences(other, r["decision_id"], [PASS])


def test_results_are_structured_and_redacted(platform5):
    platform, _ = platform5
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, SITUATION, OPTS)
    code = {**PASS, "notes": "```python\nimport psycopg\nconn = psycopg.connect(DSN)\n```"}
    platform.submit_consequences(task_id, r["decision_id"], [code])
    submitted = platform.decisions.get(r["decision_id"], "local")["consequences"]["submitted"][0]
    assert "psycopg.connect" not in submitted["notes"] and submitted["spike"]["metrics"] == {"latency_ms": 3.5}


def test_pass_two_without_a_scorer_still_decides_on_hard_evidence(settings, task_store, memrouter, project_dir):
    calls = {"n": 0}
    scorer = FakeScorer(**CLOSE)

    def flaky():
        calls["n"] += 1
        if calls["n"] > 1:
            raise ConnectionError("529")
        return scorer

    from horizon.service import Platform

    platform = Platform(settings, task_store, lambda: memrouter, cwd=project_dir, scorer_factory=flaky)
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, SITUATION, OPTS)
    final = platform.submit_consequences(task_id, r["decision_id"], [PASS, FAIL])
    assert final["scorer_status"] == "unavailable" and final["chosen"] == "PostgreSQL"


def test_an_outcome_for_another_option_is_still_logged(platform5):
    platform, _ = platform5
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, SITUATION, OPTS)
    platform.submit_consequences(task_id, r["decision_id"], [PASS, FAIL])
    platform.record_outcome(task_id, SITUATION, "mongodb", success=0.5)  # the host overrode the decision
    platform.record_outcome(task_id, "unrelated step", "rename variables", success=1.0)
    [outcome] = platform.decisions.get(r["decision_id"], "local")["outcomes"]
    assert outcome["option"] == "MongoDB" and outcome["followed_decision"] is False


def test_export_decisions_cli(platform5, settings, tmp_path):
    platform, _ = platform5
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, SITUATION, OPTS)
    platform.submit_consequences(task_id, r["decision_id"], [PASS])
    platform.record_outcome(task_id, SITUATION, "PostgreSQL", success=1.0)
    platform.evaluate_options(task_id, "another decision", [Option("a"), Option("b")])  # no outcome yet

    out = tmp_path / "decisions.jsonl"
    env = {**os.environ, "HORIZON_DB_URL": settings.db_url}
    for flag, n in (([], 2), (["--with-outcomes-only"], 1)):
        proc = subprocess.run([sys.executable, "-m", "horizon", "export-decisions", "--out", str(out), *flag],
                              capture_output=True, text=True, env=env, timeout=60)
        assert proc.returncode == 0, proc.stderr
        lines = [json.loads(line) for line in out.read_text().splitlines()]
        assert len(lines) == n and all(rec["schema"] == SCHEMA for rec in lines)

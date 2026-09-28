"""PROJECT.md §12: no raw code is stored. Code goes into every field a host can write; then every table in the
database is dumped and searched. The goal is the documented exception (kept verbatim, §8), so it's clean here."""

import json

from test_decision import CLOSE, FakeScorer, with_scorer

from horizon.db import connect
from horizon.decision.layer import Option
from horizon.hooks import run_hook

CODE = "```python\ndef leak_marker_fn(path):\n    return os.system(f'rm -rf {path}')\n```"
LINE = "def leak_marker_fn(path): return os.system(path)"


def all_text(db_url: str) -> str:
    db = connect(db_url)
    if db.kind == "sqlite":
        tables = [r[0] for r in db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")]
    else:
        tables = [r[0] for r in db.fetchall("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    dump = []
    for t in tables:
        for row in db.fetchall(f"SELECT * FROM {t}"):
            dump.append(json.dumps([c if isinstance(c, (str, int, float, type(None), dict, list)) else str(c)
                                    for c in row], default=str))
    return "\n".join(dump)


def test_no_code_is_stored_anywhere(settings, task_store, memrouter, project_dir):
    platform = with_scorer(settings, task_store, memrouter, project_dir, FakeScorer(**CLOSE))
    task_id = platform.start_task("Add CSV export", constraints=[CODE], plan=[LINE], open_issues=[CODE],
                                  project_id="shop", target_tests=[LINE])["task_id"]
    run_hook("post-tool-use", json.dumps({  # the baseline run, with code in the command and the output
        "session_id": "s", "cwd": project_dir, "tool_name": "Bash",
        "tool_input": {"command": f'python -c "{LINE}" && pytest -q'},
        "tool_response": {"stdout": f"{LINE}\nFAILED tests/test_a.py::test_x - {LINE}\n==== 1 failed, 2 passed in 0.1s ===="}}),
        settings)
    ctx = platform.recall_context(task_id, CODE, human_guidance=CODE)
    r = platform.evaluate_options(task_id, CODE, [Option(LINE, CODE), Option("stdlib csv", CODE)])
    platform.submit_consequences(task_id, r["decision_id"], [
        {"option": "stdlib csv", "static_checks": [{"name": LINE, "passed": True}],
         "spike": {"ran": True, "passed": True, "metrics": {"latency_ms": 3}}, "notes": CODE}])
    platform.record_outcome(task_id, CODE, CODE, success=0.0, reason=CODE, alternatives=[CODE],
                            progress_note=CODE, failure_reason=CODE, subagent=LINE, recall_id=ctx["recall_id"])
    for _ in range(2):
        platform.record_outcome(task_id, CODE, "stdlib csv", success=0.0, failure_reason=CODE)
    platform.resume_with_guidance(task_id, CODE)
    platform.log_approval(task_id, "escalation", "accept", CODE, answer=CODE)
    episode = memrouter.store.recent("local", 1)[0]
    platform.delete_memory(episode.id, CODE, removed_by="user")
    memrouter.consolidate("local")

    dump = all_text(settings.db_url)
    assert "leak_marker_fn" not in dump and "os.system" not in dump and "rm -rf" not in dump
    assert "Add CSV export" in dump  # the goal, verbatim by design
    # The flow really stored things: code was replaced, not silently dropped, and the rest is there.
    assert dump.count("[code removed]") > 10 and "stdlib csv" in dump and "tests/test_a.py::test_x" in dump
    assert "&& pytest -q" in dump  # the test command, with its quoted inline code elided

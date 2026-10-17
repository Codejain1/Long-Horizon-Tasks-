"""memrouter experience: outcomes -> surprise, credit to recalled memories, lessons, fears, reconsolidation."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path[:0] = [str(ROOT / "memrouter"), str(ROOT / "memrouter")]
import experience as X  # noqa: E402
from test_hermes_memory import KeywordRanker, fake_encode  # noqa: E402

import memory_service as M  # noqa: E402

SIT = "build a flask todo app with pytest tests"


def mem(tmp_path):
    return M.Memory(tmp_path, fake_encode, KeywordRanker())


def test_surprise_formula_and_edge_cases():
    assert X.surprise({"success": 1.0}, {"success": 1.0}) == 0  # an accurate prediction teaches nothing
    assert X.surprise({"success": 1.0}, {"success": 0.3}) == 0.7  # no efficiency metrics: success error alone
    s = X.surprise({"success": 1.0, "tokens": 500, "cost": 0}, {"success": 0.5, "tokens": 1000, "cost": 2})
    assert abs(s - (0.6 * 0.5 + 0.4 * (0.5 + 1.0) / 2)) < 1e-9  # cost 0 counts as +1; tokens 50 % cheaper
    assert X.surprise({"success": 0.0, "tokens": 9000}, {"success": 1.0, "tokens": 1000}) == -1.0  # clamped


def test_condition_match():
    assert X.condition_match({"os": "linux"}, {"os": "linux", "x": 1}) == 1.0
    assert X.condition_match({"os": "linux"}, {"os": "mac"}) == 0.25
    assert X.condition_match({"os": "linux"}, {"db": "pg"}) == 0.75


def test_outcomes_credit_the_memories_recalled_for_them(tmp_path):
    m = mem(tmp_path)
    m.add("for flask apps use the app factory pattern", "Kartik", "c", 1_700_000_000)
    m.add("for flask apps keep config in env vars", "Kartik", "c", 1_700_000_100)
    r = m.search("flask app factory", 40)
    used = [it["id"] for it in r["items"]]
    out = m.exp.record_episode(SIT, "app factory", {"success": 1.0}, {"success": 0.3}, recall_id=r["recall_id"])
    assert out["surprise"] == 0.7 and set(out["credited"]) == set(used)
    s, st = m.db.execute("SELECT strength, stability FROM turns WHERE id = ?", (used[0],)).fetchone()
    assert s > 0.5 and st == 1.5  # strengthened by surprise, more stable after a helpful recall
    assert m.exp.record_episode(SIT, "x", {"success": 1.0}, recall_id=r["recall_id"])["credited"] == []  # once only
    r2 = m.search("flask app factory", 40)
    m.exp.record_episode(SIT, "app factory", {"success": 0.0}, {"success": 0.9}, recall_id=r2["recall_id"])
    s2 = m.db.execute("SELECT strength FROM turns WHERE id = ?", (used[0],)).fetchone()[0]
    assert s2 < s and s - s2 < 0.2 * 0.7 * 0.9  # weakened, damped by stability


def test_prior_from_similar_episodes_when_nothing_was_predicted(tmp_path):
    m = mem(tmp_path)
    first = m.exp.record_episode(SIT, "app factory", {"success": 1.0})
    assert first["predicted_success"] == 0.5 and first["low_confidence"]
    second = m.exp.record_episode(SIT, "app factory", {"success": 1.0})
    assert second["predicted_success"] == 1.0 and not second["low_confidence"] and second["surprise"] == 0


def test_fear_from_one_severe_failure(tmp_path):
    m = mem(tmp_path)
    out = m.exp.record_episode("clean up the build folder", "rm -rf with a glob", {"success": 0.0, "what": "data loss"},
                               {"success": 0.9}, severity="severe")
    fears = m.search("clean up the build folder", 100)["fears"]
    assert [f["id"] for f in fears] == [out["fear"]] and "data loss" in fears[0]["text"]
    assert m.search("order pizza near me", 100)["fears"] == []  # only where it is relevant
    m.exp.record_episode("clean up the build folder", "rm -rf with a glob", {"success": 1.0}, {"success": 0.5})
    assert [l for l in m.exp.lessons() if l["kind"] == "fear"][0]["strength"] == 0.7  # contrary evidence weakens it
    assert not m.exp.clear_fear(out["fear"], "") and m.exp.clear_fear(out["fear"], "Kartik")  # never silently
    assert m.search("clean up the build folder", 100)["fears"] == []


def test_consolidation_makes_strategies_and_failure_lessons(tmp_path):
    m = mem(tmp_path)
    for k in range(5):
        m.exp.record_episode(SIT, "app factory", {"success": 1.0}, {"success": 0.7}, conditions={"lang": "python"})
        m.exp.record_episode("deploy the todo app to heroku", "git push heroku", {"success": 0.0}, {"success": 0.7})
    out = m.exp.consolidate()
    kinds = {l["kind"]: l for l in m.exp.lessons()}
    assert len(out["new_lessons"]) == 2 and out["archived"] == 4  # newest 3 kept as evidence per lesson
    assert "What worked" in kinds["strategy"]["text"] and "5/5 runs (when lang = python)" in kinds["strategy"]["text"]
    assert "What failed" in kinds["lesson"]["text"] and "0/5" in kinds["lesson"]["text"]
    for k in range(3):
        m.exp.record_episode(SIT, "app factory", {"success": 1.0}, {"success": 0.7}, conditions={"lang": "python"})
    again = m.exp.consolidate()
    assert again["merged"] == [kinds["strategy"]["id"]] and not again["new_lessons"]
    assert "What worked" in m.search("how should I build a flask todo app", 200)["context"]


def test_reconsolidation_refines_then_weakens(tmp_path):
    m = mem(tmp_path)
    ev = [m.exp.record_episode(SIT, "app factory", {"success": 1.0}, {"success": 0.7},
                               conditions={"lang": "python"})["episode"] for _ in range(3)]
    # a strategy written before its scope was known (no conditions), backed by episodes that all had lang = python
    sid = m.exp._insert_lesson("strategy", "What worked: app factory.", "app factory", {}, ev, 1_700_000_000, 0.6, 1.0)
    m.exp.record_episode(SIT, "app factory", {"success": 0.0}, {"success": 0.8}, conditions={"lang": "rust"})
    text = m.db.execute("SELECT text, conditions, strength FROM turns WHERE id = ?", (sid,)).fetchone()
    assert "holds when lang = python" in text[0] and '"lang": "python"' in text[1] and text[2] == 0.6  # scoped, not hurt
    m.exp.record_episode(SIT, "app factory", {"success": 0.0}, {"success": 0.8}, conditions={"lang": "rust"})
    assert m.db.execute("SELECT strength FROM turns WHERE id = ?", (sid,)).fetchone()[0] == 0.6  # now out of scope
    for k in range(2):  # contradicted inside its own conditions, twice: halved and marked
        m.exp.record_episode(SIT, "app factory", {"success": 0.0}, {"success": 0.8}, conditions={"lang": "python"})
    s, status = m.db.execute("SELECT strength, status FROM turns WHERE id = ?", (sid,)).fetchone()
    assert status == "contradicted" and s == 0.3  # halved from 0.6


def test_predictor_bias_after_enough_outcomes(tmp_path):
    m = mem(tmp_path)
    for k in range(X.PREDICTOR_MIN):
        m.exp.record_episode(f"task {k}", "a", {"success": 0.5}, {"success": 0.9}, source="planner")
    assert abs(m.exp.predictor_bias("planner") - 0.4) < 1e-9  # it guesses 0.4 too optimistic
    assert m.exp.predictor_bias("unknown") == 0.0


def test_strength_reorders_but_cannot_erase(tmp_path):
    m = mem(tmp_path)
    a = m.add("deploy with docker compose", "Kartik", "c", 1_700_000_000)
    m.add("deploy with docker swarm", "Kartik", "c", 1_700_000_100)
    ids = {it["text"]: it["id"] for it in m.search("deploy with docker", 500)["items"]}
    m.db.execute("UPDATE turns SET strength = 1.0 WHERE id = ?", (ids["deploy with docker compose"],))
    m.db.execute("UPDATE turns SET strength = 0.0 WHERE id = ?", (ids["deploy with docker swarm"],))
    m.db.commit()
    m.dirty = True
    items = m.search("deploy with docker", 500)["items"]
    assert {it["text"] for it in items} == {"deploy with docker compose", "deploy with docker swarm"} and a
    assert sorted(items, key=lambda it: -it["strength"])[0]["text"] == "deploy with docker compose"


def test_procedure_lessons_travel_across_tasks(tmp_path):
    m = mem(tmp_path)
    apps = ["todo", "notes", "expenses", "bookmarks"]
    for app in apps:
        m.exp.record_episode(f"build a {app} cli || contract: write SPEC.md", "contract via first try",
                             {"success": 1.0, "approach": "SPEC.md with the data file from an env var, default <app>.json"},
                             {"success": 0.7}, conditions={"step_kind": "contract"})
        m.exp.record_episode(f"build a {app} cli || tests: write tests", "tests via first try",
                             {"success": 0.0, "error": "tests read a temp file the program is never told about"},
                             {"success": 0.7}, conditions={"step_kind": "tests"})
    out = m.exp.consolidate()
    texts = {l["kind"]: l["text"] for l in m.exp.lessons()}
    assert len(out["new_lessons"]) == 2
    assert "contract steps succeeded via first try in 4/4 runs" in texts["strategy"] and "env var" in texts["strategy"]
    assert "tests steps done via first try failed in 4/4 runs" in texts["lesson"] and "temp file" in texts["lesson"]
    assert m.exp.consolidate()["new_lessons"] == []  # nothing new: no duplicates, no re-counting
    ctx = m.search("build a habits cli", 600)["context"]
    assert "What worked across tasks" in ctx and "What failed across tasks" in ctx  # a NEW task gets them


def test_procedure_lessons_need_more_than_one_task(tmp_path):
    m = mem(tmp_path)
    for _ in range(4):
        m.exp.record_episode("build a todo cli || tests: write tests", "tests via first try", {"success": 1.0},
                             conditions={"step_kind": "tests"})
    assert not [l for l in m.exp.lessons() if "across tasks" in l["text"]]
    m.exp.consolidate()
    assert not [l for l in m.exp.lessons() if "across tasks" in l["text"]]  # one task only: not a procedure lesson

"""The long-horizon runner with memrouter: watcher decisions, the spec, the memory loop."""

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path[:0] = [str(ROOT / "runner"), str(ROOT / "memrouter"), str(ROOT / "memrouter")]
import longrun as LR  # noqa: E402
from test_hermes_memory import KeywordRanker, fake_encode  # noqa: E402
from test_longrun import FakeModel, call  # noqa: E402

import memory_service as M  # noqa: E402

PLAN = [{"id": "s1", "kind": "tests", "do": "write tests", "check": {"file": "test_g.py"}},
        {"id": "s2", "kind": "build", "do": "make greet", "check": {"cmd": f"{sys.executable} -m pytest -q -p no:cacheprovider "
                                                                     "test_g.py"}}]
TEST = "import g\n\ndef test_hi():\n    assert g.hi() == 'hello'\n"


def h(option, failure="work", passed=None, failed=None, failing=(), error="boom"):
    return {"option": option, "ok": False, "error": error, "failure": failure, "passed": passed, "failed": failed,
            "failing": failing}


def test_decisions_follow_the_signals(tmp_path):
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), None, tmp_path / "r.jsonl")
    st = LR.Step("s", "x", {"cmd": "false"})
    assert rn.decide(st, [h("first try", "environment")], 0, 2)[0] == "fix_environment"
    assert rn.decide(st, [h("fix_environment", "environment")], 0, 2)[0] == "escalate"  # env fix tried once already
    prog = [h("first try", passed=1, failed=2, failing=("a", "b")), h("escalate", passed=2, failed=1, failing=("b",))]
    assert rn.decide(st, prog, 0, 2) == ("continue", "progress: 1 -> 2 tests passing")
    flat = [h("first try", passed=1, failed=2, failing=("a", "b")), h("first try", passed=1, failed=2, failing=("a", "b"))]
    assert rn.decide(st, flat, 0, 2) == ("escalate", "the same tests keep failing with no progress")
    assert rn.decide(st, flat + [h("escalate", passed=1, failed=2, failing=("a", "b"))], 0, 2)[0] == "replan"
    assert rn.decide(st, flat + [h("escalate", passed=1, failed=2, failing=("a", "b"))], 2, 2)[0] == "stop"
    assert LR.classify_failure("ModuleNotFoundError: No module named 'flask'") == "environment"
    assert LR.classify_failure("AssertionError: assert [] == [1]") == "work"


def test_escalation_uses_thinking_and_the_spec_reaches_build_steps(tmp_path):
    turns = [[call("write_file", path="test_g.py", content=TEST)], [call("done", summary="tests")],
             [call("write_file", path="g.py", content="def hi():\n    return 'hi'\n")], [call("done", summary="v1")],
             [call("write_file", path="g.py", content="def hi():\n    return 'hello'\n")], [call("done", summary="v2")]]
    m = FakeModel([PLAN], turns)
    r = LR.Runner("make greet return hello", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl").run()
    assert r["ok"] and r["decisions"] == [{"step": "s2", "option": "escalate", "why": "the step failed"}]
    assert m.deep_calls >= 1  # the escalation really used thinking mode
    assert "THE FIXED TESTS" in m.step_prompts[1] and "assert g.hi() == 'hello'" in m.step_prompts[1]



def test_experience_loop_through_memrouter(tmp_path):
    mem = M.Memory(tmp_path / "mem", fake_encode, KeywordRanker())
    threading.Thread(target=M.serve, args=(mem, 18799), daemon=True).start()
    time.sleep(0.3)
    mr = LR.Memrouter("http://127.0.0.1:18799", min_score=0.0)  # the keyword stand-in ranker isn't on the MLP's scale
    goal = "make greet return hello"
    turns = [[call("write_file", path="test_g.py", content=TEST)], [call("done", summary="tests")],
             [call("write_file", path="g.py", content="def hi():\n    return 'hi'\n")], [call("done", summary="v1")],
             [call("write_file", path="g.py", content="def hi():\n    return 'hello'\n")], [call("done", summary="v2")]]
    m1 = FakeModel([PLAN], turns)
    assert LR.Runner(goal, LR.LocalSandbox(tmp_path / "ws1"), m1, tmp_path / "r1.jsonl", memrouter=mr).run()["ok"]
    assert mem.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 3  # 2 steps + the run
    notes = mem.db.execute("SELECT text, kind, method FROM turns WHERE chat = 'runner-experience'").fetchall()
    assert [(k, m) for _, k, m in notes] == [("advice", "program")]  # stored as advice, no writer call
    assert notes[0][0].startswith("When a build step") and "what fixed it: v2" in notes[0][0]

    m2 = FakeModel([PLAN], [t for t in turns])
    LR.Runner(goal, LR.LocalSandbox(tmp_path / "ws2"), m2, tmp_path / "r2.jsonl", memrouter=mr).run()
    assert "LESSONS FROM PAST RUNS" in m2.plan_prompts[0] and "what fixed it: v2" in m2.plan_prompts[0]  # run 2 learns


def test_lessons_are_silent_unless_rules_or_relevant_advice():
    """Benchmark run 2: pasting whatever search returned made the model worse. Only the owner's rules always go in;
    other memories only when they are advice, a failure lesson or a fear AND the ranker scores them relevant."""
    mr = LR.Memrouter("http://x", min_score=0.5)
    items = [{"kind": "fact", "text": "Owner feedback: 1/3 checks passed", "score": 0.95},  # a record, not advice
             {"kind": "advice", "text": "When tests fail with ModuleNotFoundError -> pip install pytest", "score": 0.8},
             {"kind": "advice", "text": "irrelevant advice", "score": 0.2},
             {"kind": "lesson", "text": "What failed: build via first try", "score": 0.6},
             {"kind": "advice", "text": "outdated advice", "score": 0.9, "current": False},
             *[{"kind": "advice", "text": f"more {k}", "score": 0.55} for k in range(3)]]
    mr._post = lambda path, body: {"items": items, "rules": [], "fears": [], "recall_id": 4}
    text, rid = mr.lessons("build a store")
    assert rid == 4 and text.splitlines() == ["- When tests fail with ModuleNotFoundError -> pip install pytest",
                                              "- What failed: build via first try", "- more 0"]  # top 3 by score
    items[:] = items[:1] + items[2:3]  # nothing worth saying: no lessons at all
    assert mr.lessons("build a store")[0] == ""
    mr._post = lambda path, body: {"items": [], "rules": [{"text": "errors go to stderr"}], "fears": []}
    assert mr.lessons("anything")[0] == "OWNER'S STANDING RULES (always follow):\n- errors go to stderr"


IMPOSSIBLE = ("import json, subprocess, tempfile\n\ndef test_add():\n    p = tempfile.mktemp()\n    open(p, 'w').write('[]')\n"
              "    subprocess.run(['python3', 'todo.py', 'add', 'milk'])\n    assert json.load(open(p)) == [{'task': 'milk'}]\n")
GOOD = "import g\n\ndef test_hi():\n    assert g.hi() == 'hello'\n"


def test_impossible_tests_are_reviewed_before_they_are_locked(tmp_path):
    m = FakeModel([PLAN], [
        [call("write_file", path="test_g.py", content=IMPOSSIBLE)], [call("done", summary="tests v1")],
        [call("write_file", path="test_g.py", content=GOOD)], [call("done", summary="tests v2")],
        [call("write_file", path="g.py", content="def hi():\n    return 'hello'\n")], [call("done", summary="code")]])
    m.reviews = [{"possible": False, "problems": ["the test reads a temp file todo.py is never told about"]},
                 {"possible": True}]
    rn = LR.Runner("make greet return hello", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl")
    assert rn.run()["ok"]
    assert "never told about" in m.step_prompts[1] and GOOD in rn.locked["test_g.py"][1]  # only the fixed spec locked


def test_build_failure_can_revise_an_impossible_spec(tmp_path):
    m = FakeModel([PLAN], [
        [call("write_file", path="test_g.py", content=IMPOSSIBLE.replace("todo.py", "g.py"))], [call("done", summary="t")],
        [call("write_file", path="g.py", content="def hi():\n    return 'hello'\n")], [call("done", summary="try 1")],
        [call("done", summary="try 2 (escalated)")],
        [call("write_file", path="test_g.py", content=GOOD)], [call("done", summary="tests rewritten")],
        [call("done", summary="code already right")]])
    m.reviews = [{"possible": True},  # the first review misses it: the build has to catch it
                 {"possible": False, "problems": ["reads a temp file the program never gets"]}, {"possible": True}]
    rn = LR.Runner("make greet return hello", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl")
    r = rn.run()
    assert r["ok"] and [d["option"] for d in r["decisions"]] == ["escalate", "revise_spec"]
    assert GOOD in rn.locked["test_g.py"][1]


def test_a_broken_review_never_blocks_the_work(tmp_path):
    def llm(messages, tools=None, json_mode=False, deep=False):
        raise TimeoutError("model down")
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), llm, tmp_path / "r.jsonl")
    rn.box.write("test_x.py", "def test_x():\n    assert True\n")
    assert rn.review_spec() == []


def test_contract_reaches_every_later_step_and_impossible_tests_are_never_locked(tmp_path):
    plan = [{"id": "c", "kind": "contract", "do": "write SPEC.md", "check": {"cmd": "test -s SPEC.md"}},
            {"id": "t", "kind": "tests", "do": "write tests", "check": {"file": "test_g.py"}},
            {"id": "b", "kind": "build", "do": "build", "check": {"cmd": "true"}}]
    m = FakeModel([plan], [
        [call("write_file", path="SPEC.md", content="greet: g.hi() returns 'hello'")], [call("done", summary="c")],
        [call("write_file", path="test_g.py", content=IMPOSSIBLE)], [call("done", summary="t1")],
        [call("done", summary="t2")], [call("done", summary="t3 (thinking)")], [call("done", summary="t4 (thinking)")]])
    m.reviews = [{"possible": False, "problems": ["temp file never passed"]}] * 4
    rn = LR.Runner("greet", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl")
    r = rn.run()
    assert not r["ok"] and r["stopped_at"] == "t" and "needs the owner" in r["reason"] and rn.locked == {}
    assert "THE CONTRACT (SPEC.md)" in m.step_prompts[1] and "returns 'hello'" in m.step_prompts[1]
    assert m.deep_calls >= 1  # rewrites after the first failed one think first


def test_review_without_a_concrete_blocker_does_not_block(tmp_path):
    m = FakeModel([], [])
    m.reviews = [{"possible": False, "problems": [], "style_notes": ["I would import the functions instead"]}]
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl")
    rn.box.write("test_x.py", "def test_x():\n    assert True\n")
    assert rn.review_spec() == []
    assert "style" in (tmp_path / "r.jsonl").read_text()  # style notes are logged, never acted on


def test_spec_steps_get_the_stronger_writer(tmp_path):
    plan = [{"id": "c", "kind": "contract", "do": "write SPEC.md", "check": {"cmd": "test -s SPEC.md"}},
            {"id": "b", "kind": "build", "do": "build", "check": {"cmd": "true"}}]
    small = FakeModel([plan], [[call("done", summary="built")]])
    big = FakeModel([], [[call("write_file", path="SPEC.md", content="contract")], [call("done", summary="spec")]])
    r = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), small, tmp_path / "r.jsonl", spec_llm=big).run()
    assert r["ok"] and len(big.step_prompts) == 1 and "write SPEC.md" in big.step_prompts[0]
    assert len(small.step_prompts) == 1 and "STEP b" in small.step_prompts[0]  # the build stays on the small model

    small2 = FakeModel([plan], [[call("write_file", path="SPEC.md", content="c")], [call("done", summary="s")],
                                [call("done", summary="built")]])
    LR.Runner("g", LR.LocalSandbox(tmp_path / "ws2"), small2, tmp_path / "r2.jsonl").run()
    assert small2.deep_calls >= 2  # without a bigger model: the spec steps think first


class FakeMemrouter:
    def __init__(self):
        self.queries = []

    def lessons(self, q):
        self.queries.append(q)
        return "- add must keep all the words of the text", 7

    def episode(self, **kw):
        return {}

    def note(self, text):
        pass

    def _post(self, path, body):
        return {}


def test_lessons_reach_each_step_not_only_the_planner(tmp_path):
    plan = [{"id": "b", "kind": "build", "do": "build the add command", "check": {"cmd": "true"}}]
    m = FakeModel([plan], [[call("done", summary="built")]])
    mr = FakeMemrouter()
    LR.Runner("store", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", memrouter=mr).run()
    assert "LESSONS FOR THIS KIND OF STEP" in m.step_prompts[0] and "keep all the words" in m.step_prompts[0]
    assert any("build: build the add command" in q for q in mr.queries)


class SlowMemrouter(FakeMemrouter):
    def __init__(self):
        super().__init__()
        self.saved = []

    def episode(self, **kw):
        __import__("time").sleep(0.5)  # the memory writer is a model call
        self.saved.append(kw["action"])
        return {}

    def note(self, text):
        __import__("time").sleep(0.5)
        self.saved.append("note")


def test_memory_writes_do_not_block_the_work_and_are_not_lost(tmp_path):
    import time
    plan = [{"id": f"b{k}", "kind": "build", "do": f"step {k}", "check": {"cmd": "true"}} for k in range(4)]
    m = FakeModel([plan], [[call("done", summary=f"s{k}")] for k in range(4)])
    mr = SlowMemrouter()
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", memrouter=mr)
    t = time.time()
    assert rn.run()["ok"]
    assert time.time() - t < 2.0  # 5 writes x 0.5 s would be 2.5 s if the work waited on them
    assert sorted(mr.saved) == sorted(["build via first try"] * 4 + ["long-horizon run"])  # all landed by the end


def test_rules_only_memory_gives_the_owners_rules_and_nothing_else():
    mr = LR.Memrouter("http://x", rules_only=True)
    mr._post = lambda path, body: {"items": [{"kind": "advice", "text": "relevant advice", "score": 0.9}],
                                   "fears": [{"text": "WARNING: x"}], "rules": [{"text": "errors go to stderr"}]}
    assert mr.lessons("build")[0] == "OWNER'S STANDING RULES (always follow):\n- errors go to stderr"

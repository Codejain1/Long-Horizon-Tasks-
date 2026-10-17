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



def test_memory_loop_real_outcomes_become_warnings_for_the_next_job(tmp_path):
    """The runner only reads memory. The scorer posts the real outcome; the next job in that scope is warned about
    the check that failed, and the outcome after it says whether the warning helped."""
    mem = M.Memory(tmp_path / "mem", fake_encode, KeywordRanker())
    threading.Thread(target=M.serve, args=(mem, 18799), daemon=True).start()
    time.sleep(0.3)
    mr = LR.Memrouter("http://127.0.0.1:18799", scope="greet")
    mem.add("Greetings must be lowercase hello, never Hi", "owner", "tickets")
    mem.add_rule("Errors go to stderr")
    turns = [[call("write_file", path="test_g.py", content=TEST)], [call("done", summary="tests")],
             [call("write_file", path="g.py", content="def hi():\n    return 'hello'\n")], [call("done", summary="v1")]]
    m1 = FakeModel([PLAN], [t for t in turns])
    r1 = LR.Runner("make greet return hello", LR.LocalSandbox(tmp_path / "ws1"), m1, tmp_path / "r1.jsonl",
                   memrouter=mr).run()
    assert r1["ok"] and r1["warning_id"] is None  # nothing failed yet in this scope
    assert mem.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1  # the runner wrote nothing
    assert "OWNER'S STANDING RULES" in m1.plan_prompts[0] and "Greetings must be lowercase hello" in m1.plan_prompts[0]

    mem.fails.record("greet", [{"check": "test_greeting_is_lowercase", "error": "assert 'Hi' == 'hello'"}])
    m2 = FakeModel([PLAN], [t for t in turns])
    r2 = LR.Runner("make greet return hello", LR.LocalSandbox(tmp_path / "ws2"), m2, tmp_path / "r2.jsonl",
                   memrouter=mr).run()
    assert "test_greeting_is_lowercase (failed 1x): assert 'Hi' == 'hello'" in m2.plan_prompts[0]
    assert "test_greeting_is_lowercase" in m2.step_prompts[1]  # every step sees the warnings, not only the planner
    out = mem.fails.record("greet", [], ["test_greeting_is_lowercase"], r2["warning_id"])
    assert out["credited"] == ["test_greeting_is_lowercase"]
    assert mem.fails.stats("greet")[0]["then_passed"] == 1


def test_memory_text_has_rules_warnings_and_what_the_owner_said():
    mr = LR.Memrouter("http://x", scope="house")
    reply = {"rules": [{"text": "errors go to stderr"}], "warning_id": 4,
             "warnings": [{"check": "test_house_empty", "seen": 2, "error": "assert '' == '(empty)'"}],
             "items": [{"speaker": "owner", "text": "deposits are 4 months' rent"},
                       {"speaker": "owner", "text": "old rate", "current": False},
                       {"speaker": "Hermes", "text": "the assistant said something"}]}
    sent = []
    mr._post = lambda path, body: sent.append(body) or reply
    parts = mr.read("build a store")
    assert sent[0]["scope"] == "house" and parts["warning_id"] == 4
    assert mr.text(parts).splitlines() == [
        "OWNER'S STANDING RULES (always follow):", "- errors go to stderr",
        "CHECKS THAT FAILED IN EARLIER JOBS HERE (get them right the first time):",
        "- test_house_empty (failed 2x): assert '' == '(empty)'",
        "WHAT THE OWNER SAID EARLIER (requirements that may apply here):", "- deposits are 4 months' rent"]
    mr.read("a step", warnings=False)
    assert sent[1]["scope"] == ""  # warnings are fetched (and logged) once per job
    assert mr.text({"rules": [], "warnings": [], "said": []}) == ""


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

    def read(self, q, warnings=True):
        self.queries.append(q)
        return {"rules": [], "said": ["add must keep all the words of the text"], "warning_id": 7,
                "warnings": [{"check": "test_add_keeps_words", "seen": 1, "error": ""}] if warnings else []}

    text = staticmethod(LR.Memrouter.text)


def test_memory_reaches_each_step_not_only_the_planner(tmp_path):
    plan = [{"id": "b", "kind": "build", "do": "build the add command", "check": {"cmd": "true"}}]
    m = FakeModel([plan], [[call("done", summary="built")]])
    mr = FakeMemrouter()
    r = LR.Runner("store", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", memrouter=mr).run()
    assert "FROM MEMORY" in m.step_prompts[0] and "keep all the words" in m.step_prompts[0]
    assert "test_add_keeps_words" in m.step_prompts[0] and r["warning_id"] == 7
    assert any("build: build the add command" in q for q in mr.queries)


def test_rules_only_memory_gives_the_owners_rules_and_nothing_else():
    mr = LR.Memrouter("http://x", rules_only=True)
    mr._post = lambda path, body: {"items": [{"speaker": "owner", "text": "relevant"}], "warning_id": None,
                                   "warnings": [{"check": "x", "seen": 1}], "rules": [{"text": "errors go to stderr"}]}
    assert mr.text(mr.read("build")) == "OWNER'S STANDING RULES (always follow):\n- errors go to stderr"

"""The long-horizon runner with memrouter: watcher decisions, the spec, the memory loop."""

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path[:0] = [str(ROOT / "runner"), str(ROOT / "memrouter"), str(ROOT / "memrouter")]
import longrun as LR  # noqa: E402
from test_memory_service import KeywordRanker, fake_encode  # noqa: E402
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


def test_build_failure_can_revise_an_impossible_spec(tmp_path):
    m = FakeModel([PLAN], [
        [call("write_file", path="test_g.py", content=IMPOSSIBLE.replace("todo.py", "g.py"))], [call("done", summary="t")],
        [call("write_file", path="g.py", content="def hi():\n    return 'hello'\n")], [call("done", summary="try 1")],
        [call("done", summary="try 2 (escalated)")],
        [call("write_file", path="test_g.py", content=GOOD)], [call("done", summary="tests rewritten")],
        [call("done", summary="code already right")]])
    m.reviews = [{"possible": False, "problems": ["reads a temp file the program never gets"]}, {"possible": True}]
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


def test_contract_reaches_every_later_step_and_tests_are_not_reviewed_up_front(tmp_path):
    """The spec review costs a thinking call per job (about 10 % of the time): it now runs only when the build is
    stuck (revise_spec), never on a tests step that passed its check."""
    plan = [{"id": "c", "kind": "contract", "do": "write SPEC.md", "check": {"cmd": "test -s SPEC.md"}},
            {"id": "t", "kind": "tests", "do": "write tests", "check": {"file": "test_g.py"}},
            {"id": "b", "kind": "build", "do": "build", "check": {"cmd": "true"}}]
    m = FakeModel([plan], [
        [call("write_file", path="SPEC.md", content="greet: g.hi() returns 'hello'")], [call("done", summary="c")],
        [call("write_file", path="test_g.py", content=GOOD)], [call("done", summary="t")], [call("done", summary="b")]])
    rn = LR.Runner("greet", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl")
    assert rn.run()["ok"] and not hasattr(m, "review_prompts")
    assert "THE CONTRACT (SPEC.md)" in m.step_prompts[1] and "returns 'hello'" in m.step_prompts[2]
    assert "test_g.py" in rn.locked and m.deep_calls == 0  # spec steps no longer think first


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
    assert small2.deep_calls == 0  # without a bigger model: thinking is the escalation, not the first try


def test_the_job_ends_as_soon_as_the_fixed_tests_pass(tmp_path):
    """Benchmark runs: 31 jobs used up the time budget after the work already passed. Once every locked test
    passes after a build step, the rest of the plan is skipped."""
    plan = [{"id": "t", "kind": "tests", "do": "write tests", "check": {"file": "test_g.py"}},
            {"id": "b1", "kind": "build", "do": "make greet", "check": {"cmd": "true"}},
            {"id": "b2", "kind": "build", "do": "polish greet", "check": {"cmd": "true"}},
            {"id": "v", "kind": "verify", "do": "run all tests", "check": {"cmd": "true"}}]
    m = FakeModel([plan], [[call("write_file", path="test_g.py", content=GOOD)], [call("done", summary="t")],
                           [call("write_file", path="g.py", content="def hi():\n    return 'hello'\n")],
                           [call("done", summary="built")]])
    rn = LR.Runner("greet", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl")
    rn.PYTEST = f"{sys.executable} -m pytest"
    r = rn.run()
    assert r["ok"] and [st[1] for st in r["steps"]] == ["done", "done", "skipped", "skipped"]
    assert "early_finish" in (tmp_path / "r.jsonl").read_text()


def test_no_early_finish_while_a_fixed_test_still_fails(tmp_path):
    plan = [{"id": "t", "kind": "tests", "do": "write tests", "check": {"file": "test_g.py"}},
            {"id": "b1", "kind": "build", "do": "scaffold", "check": {"cmd": "true"}},
            {"id": "b2", "kind": "build", "do": "make greet", "check": {"cmd": "true"}}]
    m = FakeModel([plan], [[call("write_file", path="test_g.py", content=GOOD)], [call("done", summary="t")],
                           [call("write_file", path="g.py", content="def hi():\n    return 'hi'\n")],
                           [call("done", summary="scaffold")], [call("done", summary="b2")]])
    rn = LR.Runner("greet", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl")
    rn.PYTEST = f"{sys.executable} -m pytest"
    r = rn.run()
    assert [st[1] for st in r["steps"]] == ["done", "done", "done"]


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


def brownfield(tmp_path):
    """A small existing project: a package with exports, its own passing test, and a ci script."""
    ws = tmp_path / "ws"
    (ws / "shop").mkdir(parents=True)
    (ws / "shop" / "__init__.py").write_text('from shop.items import add_item, get_item\n\n__all__ = ["add_item", "get_item"]\n')
    (ws / "shop" / "items.py").write_text('ITEMS = {}\n\n\ndef add_item(name, price):\n    """Add an item."""\n'
                                          '    ITEMS[name] = price\n\n\ndef get_item(name):\n    return ITEMS[name]\n')
    (ws / "tests").mkdir()
    (ws / "tests" / "test_shop.py").write_text("import shop\n\ndef test_add():\n    shop.add_item('a', 3)\n"
                                               "    assert shop.get_item('a') == 3\n")
    return ws


def test_project_map_shows_what_exists():
    import tempfile
    from pathlib import Path as P
    ws = brownfield(P(tempfile.mkdtemp()))
    m = LR.project_map(ws)
    assert "shop/items.py\n  add_item(name, price): Add an item.\n  get_item(name)" in m
    assert "exports from shop.items: add_item, get_item" in m and "tests (python -m pytest -q): tests/test_shop.py" in m


def test_existing_project_is_extended_never_rebuilt(tmp_path):
    ws = brownfield(tmp_path)
    plan = [{"id": "t", "kind": "tests", "do": "test total", "check": {"file": "tests/test_total.py"}},
            {"id": "b", "kind": "build", "do": "add total", "check": {"cmd": "true"}}]
    new_test = "import shop\n\ndef test_total():\n    shop.add_item('a', 3)\n    assert shop.total() == 3\n"
    m = FakeModel([plan], [
        [call("write_file", path="tests/test_total.py", content=new_test)], [call("done", summary="t")],
        [call("write_file", path="shop/__init__.py", content="def total():\n    pass\n"),  # stubs over the exports
         call("write_file", path="shop.py", content="def total():\n    return 3\n"),  # shadows the package
         call("write_file", path="tests/test_shop.py", content="def test_add():\n    pass\n"),  # edits an old test
         call("write_file", path="shop/items.py", content=(ws / "shop" / "items.py").read_text()
              + "\n\ndef total():\n    return sum(ITEMS.values())\n"),
         call("write_file", path="shop/__init__.py", content="from shop.items import add_item, get_item, total\n")],
        [call("done", summary="b")]])
    rn = LR.Runner("add shop.total()", LR.LocalSandbox(ws), m, tmp_path / "r.jsonl")
    rn.PYTEST = f"{sys.executable} -m pytest"
    r = rn.run()
    assert rn.brownfield and rn.existing_ok and r["ok"], r
    assert "EXISTING project" in m.plan_prompts[0] and "add_item(name, price)" in m.plan_prompts[0]
    log = (tmp_path / "r.jsonl").read_text()
    assert "removes existing names: add_item, get_item" in log and "would shadow the existing package shop/" in log
    assert "existing tests" in log and not (ws / "shop.py").exists()
    assert "assert shop.get_item('a') == 3" in (ws / "tests" / "test_shop.py").read_text()


def test_a_build_that_breaks_existing_tests_has_failed(tmp_path):
    ws = brownfield(tmp_path)
    rn = LR.Runner("x", LR.LocalSandbox(ws), None, tmp_path / "r.jsonl")
    rn.PYTEST = f"{sys.executable} -m pytest"
    rn._start()
    assert rn._brownfield_guard() == ""
    (ws / "shop" / "items.py").write_text("ITEMS = {}\n\ndef add_item(name, price):\n    pass\n\ndef get_item(name):\n"
                                          "    return None\n")  # a shell edit: names kept, behaviour broken
    assert "passed before this job and now fail" in rn._brownfield_guard()
    (ws / "shop" / "items.py").write_text("ITEMS = {}\n")
    assert "lost existing names: add_item, get_item" in rn._brownfield_guard()


def test_edit_file_changes_only_what_it_names_and_keeps_the_guards(tmp_path):
    ws = brownfield(tmp_path)
    rn = LR.Runner("x", LR.LocalSandbox(ws), None, tmp_path / "r.jsonl")
    rn.PYTEST = f"{sys.executable} -m pytest"
    rn._start()
    out = rn._tool("edit_file", {"path": "shop/items.py", "old": "def get_item(name):\n    return ITEMS[name]\n",
                                 "new": "def get_item(name):\n    return ITEMS[name]\n\n\ndef total():\n    return sum(ITEMS.values())\n"})
    assert out.startswith("wrote") and "def add_item" in (ws / "shop" / "items.py").read_text()
    assert "appears 0 times" in rn._tool("edit_file", {"path": "shop/items.py", "old": "nope", "new": "x"})
    assert "appears 3 times" in rn._tool("edit_file", {"path": "shop/items.py", "old": "def ", "new": "x"})
    assert "removes existing names: get_item" in rn._tool(
        "edit_file", {"path": "shop/items.py", "old": "def get_item(name):\n    return ITEMS[name]\n", "new": ""})
    assert "existing tests" in rn._tool("edit_file", {"path": "tests/test_shop.py", "old": "== 3", "new": "== 4"})
    assert "does not exist" in rn._tool("edit_file", {"path": "shop/new.py", "old": "a", "new": "b"})


def test_a_plain_code_error_gets_a_quick_fix_before_thinking_mode(tmp_path):
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), None, tmp_path / "r.jsonl")
    st = LR.Step("s", "x", {"cmd": "false"})
    err = h("first try", error="E   NameError: name 'Optional' is not defined")
    assert rn.decide(st, [err], 0, 2)[0] == "quick_fix"
    assert rn.decide(st, [err, h("quick_fix", error="NameError again")], 0, 2)[0] == "escalate"
    assert rn.decide(st, [h("first try", error="assert 3 == 4")], 0, 2)[0] == "escalate"  # not a plain bug


def test_the_new_tests_step_sees_how_the_existing_tests_are_written(tmp_path):
    ws = brownfield(tmp_path)
    (ws / "tests" / "conftest.py").write_text("def item(name='a', price=3):\n    import shop\n    shop.add_item(name, price)\n")
    plan = [{"id": "t", "kind": "tests", "do": "test total", "check": {"file": "tests/test_total.py"}}]
    m = FakeModel([plan], [[call("write_file", path="tests/test_total.py", content="def test_t():\n    pass\n")],
                           [call("done", summary="t")]])
    rn = LR.Runner("add shop.total()", LR.LocalSandbox(ws), m, tmp_path / "r.jsonl")
    rn.PYTEST = f"{sys.executable} -m pytest"
    rn.run()
    assert "HOW THE PROJECT'S EXISTING TESTS ARE WRITTEN" in m.step_prompts[0]
    assert "def item(name='a', price=3)" in m.step_prompts[0] and "shop.get_item('a') == 3" in m.step_prompts[0]


def test_one_failing_old_test_does_not_switch_off_the_regression_guard(tmp_path):
    ws = brownfield(tmp_path)
    (ws / "tests" / "test_unsolved.py").write_text("import shop\n\ndef test_last_ticket():\n    assert shop.total() == 9\n")
    rn = LR.Runner("x", LR.LocalSandbox(ws), None, tmp_path / "r.jsonl")
    rn.PYTEST = f"{sys.executable} -m pytest"
    rn._start()
    assert rn.passing_before == {"tests/test_shop.py::test_add"}  # the unsolved ticket's test fails, the rest counts
    assert rn._brownfield_guard() == ""
    (ws / "shop" / "items.py").write_text("ITEMS = {}\n\ndef add_item(name, price):\n    pass\n\ndef get_item(name):\n"
                                          "    return None\n")
    assert "tests/test_shop.py::test_add" in rn._brownfield_guard()


def test_an_attempt_that_breaks_earlier_work_is_rolled_back(tmp_path):
    ws = brownfield(tmp_path)
    good = (ws / "shop" / "items.py").read_text()
    broken = good.replace("from", "").replace("    return ITEMS[name]\n", "    return None\n")
    plan = [{"id": "b", "kind": "build", "do": "add total", "check": {"cmd": "true"}}]
    m = FakeModel([plan], [
        [call("write_file", path="shop/items.py", content=broken),  # names kept, behaviour broken
         call("write_file", path="shop/extra.py", content="import nothing_here\n")], [call("done", summary="b")],
        [call("edit_file", path="shop/items.py", old="    return ITEMS[name]\n",
              new="    return ITEMS[name]\n\n\ndef total():\n    return sum(ITEMS.values())\n")], [call("done", summary="b")]])
    rn = LR.Runner("add shop.total()", LR.LocalSandbox(ws), m, tmp_path / "r.jsonl")
    rn.PYTEST = f"{sys.executable} -m pytest"
    r = rn.run()
    log = (tmp_path / "r.jsonl").read_text()
    assert r["ok"] and '"rolled_back"' in log and "were rolled back to the start of this step" in log
    assert not (ws / "shop" / "extra.py").exists() and "def total" in (ws / "shop" / "items.py").read_text()


def test_a_name_defined_but_not_exported_gets_a_one_line_fix_hint(tmp_path):
    ws = brownfield(tmp_path)
    (ws / "shop" / "items.py").write_text((ws / "shop" / "items.py").read_text() + "\n\ndef total():\n    return 0\n")
    (ws / "tests" / "test_total.py").write_text("import shop\n\ndef test_total():\n    assert shop.total() == 0\n")
    rn = LR.Runner("x", LR.LocalSandbox(ws), None, tmp_path / "r.jsonl")
    ok, err = rn.check(LR.Step("b", "x", {"cmd": f"{sys.executable} -m pytest -q tests/test_total.py"}))
    assert not ok and "`total` is defined in shop/items.py but shop/__init__.py does not export it" in err
    assert rn._export_hint("module 'shop' has no attribute 'nothing'") == ""


def test_done_is_refused_while_the_steps_file_is_missing(tmp_path):
    plan = [{"id": "t", "kind": "tests", "do": "write tests", "check": {"file": "tests/test_x.py"}}]
    m = FakeModel([plan], [[call("run", cmd="true"), call("done", summary="created tests/test_x.py")],
                           [call("write_file", path="tests/test_x.py", content="def test_x():\n    pass\n")],
                           [call("done", summary="t")]])
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl")
    r = rn.run()
    log = (tmp_path / "r.jsonl").read_text()
    assert r["ok"] and '"done_refused"' in log and '"decision"' not in log  # one attempt, no escalation or re-plan


def test_the_same_edit_twice_in_one_attempt_is_refused(tmp_path):
    ws = brownfield(tmp_path)
    rn = LR.Runner("x", LR.LocalSandbox(ws), None, tmp_path / "r.jsonl")
    edit = {"path": "shop/items.py", "old": "def get_item(name):\n", "new": "def get_item(name):\n    # look up\n"}
    assert rn._tool("edit_file", edit).startswith("wrote")
    assert rn._tool("edit_file", edit).startswith("refused: you already made this exact edit")
    assert (ws / "shop" / "items.py").read_text().count("# look up") == 1  # not duplicated
    rn.step_edits = {}  # a new attempt (execute resets it): the edit may be tried again
    assert rn._tool("edit_file", edit).startswith("wrote")

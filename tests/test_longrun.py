"""Long-horizon runner v0: plan -> one step at a time -> watcher checks, retries, re-plans, stops."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "runner"))
import longrun as LR  # noqa: E402


def call(name, **args):
    return {"id": f"c-{name}", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeModel:
    """Plans come from `plans`; each executor turn pops the next scripted tool-call list."""

    def __init__(self, plans, turns):
        self.plans, self.turns, self.plan_prompts, self.step_prompts = list(plans), list(turns), [], []

    def __call__(self, messages, tools=None, json_mode=False, deep=False):
        self.deep_calls = getattr(self, "deep_calls", 0) + int(deep)
        if json_mode and messages[0]["content"].startswith("SPEC REVIEW"):
            self.reviews = getattr(self, "reviews", [])
            self.review_prompts = getattr(self, "review_prompts", []) + [messages[0]["content"]]
            return {"content": json.dumps(self.reviews.pop(0) if self.reviews else {"possible": True})}
        if json_mode:
            self.plan_prompts.append(messages[0]["content"])
            return {"content": json.dumps({"steps": self.plans.pop(0)})}
        if len(messages) == 1:
            self.step_prompts.append(messages[0]["content"])
        return {"content": "", "tool_calls": self.turns.pop(0)}


def runner(tmp_path, model, **kw):
    return LR.Runner("make greet.py print hello", LR.LocalSandbox(tmp_path / "ws"), model, tmp_path / "run.jsonl", **kw)


PLAN = [{"id": "s1", "do": "write greet.py", "check": {"file": "greet.py"}},
        {"id": "s2", "do": "make it print hello", "check": {"cmd": "python3 greet.py | grep -q hello"}, "uses": ["s1"]}]


def test_happy_path_and_step_context(tmp_path):
    m = FakeModel([PLAN], [[call("write_file", path="greet.py", content="print('hi')\n")], [call("done", summary="greet.py created")],
                           [call("write_file", path="greet.py", content="print('hello')\n")], [call("done", summary="prints hello")]])
    r = runner(tmp_path, m).run()
    assert r["ok"] and [s[1] for s in r["steps"]] == ["done", "done"]
    assert "OUTPUT OF s1: greet.py created" in m.step_prompts[1]  # a step sees only what it uses


def test_retry_with_error_then_pass(tmp_path):
    m = FakeModel([PLAN[1:]], [[call("write_file", path="greet.py", content="print('bye')\n")], [call("done", summary="x")],
                               [call("write_file", path="greet.py", content="print('hello')\n")], [call("done", summary="y")]])
    r = runner(tmp_path, m).run()
    assert r["ok"] and "LAST ATTEMPT FAILED" in m.step_prompts[1] and "exited 1" in m.step_prompts[1]


def test_replan_after_two_failures_keeps_done_steps(tmp_path):
    bad = [{"id": "s1", "do": "write a.txt", "check": {"file": "a.txt"}},
           {"id": "s2", "do": "impossible", "check": {"cmd": "false"}}]
    good = [{"id": "s3", "do": "write b.txt", "check": {"file": "b.txt"}}]
    m = FakeModel([bad, good], [[call("write_file", path="a.txt", content="a")], [call("done", summary="a")],
                                [call("done", summary="tried")], [call("done", summary="tried again")],
                                [call("write_file", path="b.txt", content="b")], [call("done", summary="b")]])
    r = runner(tmp_path, m).run()
    assert r["ok"] and [s[0] for s in r["steps"]] == ["s1", "s3"]
    assert "THIS STEP KEPT FAILING: impossible" in m.plan_prompts[1] and "s1: write a.txt -> a" in m.plan_prompts[1]


def test_stops_and_reports_when_it_cannot_finish(tmp_path):
    never = [{"id": "s1", "do": "x", "check": {"cmd": "false"}}]
    m = FakeModel([never] * 3, [[call("done", summary="no")]] * 6)
    r = runner(tmp_path, m).run(max_replans=2)
    assert not r["ok"] and r["stopped_at"] == "s1" and "exited 1" in r["reason"]
    events = [json.loads(x)["event"] for x in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert events.count("plan") == 3 and events[-1] == "report"


def test_watcher_flags_repeated_failing_command_and_blocks_escape(tmp_path):
    rn = runner(tmp_path, None)
    outs = [rn._tool("run", {"cmd": "python3 -c 'import nosuchmod'"}) for _ in range(3)]
    assert "WATCHER" not in outs[1] and "WATCHER: this exact command has now failed 3 times" in outs[2]
    assert rn._tool("write_file", {"path": "../escape.txt", "content": "x"}).startswith("error: path outside")
    assert not (tmp_path / "escape.txt").exists()


def test_check_with_a_pipe_fails_when_the_first_command_fails(tmp_path):
    rn = runner(tmp_path, None)
    ok, detail = rn.check(LR.Step("s", "x", {"cmd": "python3 -c 'raise SystemExit(1)' | tail -1"}))
    assert not ok and "exited 1" in detail


def test_dispute_accepts_only_real_passing_checks(tmp_path):
    rn = runner(tmp_path, None)
    rn.max_disputes = 10  # this test is about which disputes are accepted, not the per-step limit
    rn.box.write("todo.json", '[{"task": "milk"}]')
    st = LR.Step("s2", "add", {"cmd": """cat todo.json && grep -q "'milk'" todo.json"""})
    assert rn.dispute(st, "true", "x").startswith("rejected: the replacement check must actually")
    assert "must still run `cat todo.json`" in rn.dispute(st, "grep -q milk todo.json", "x")
    assert rn.dispute(st, "cat todo.json && grep -q eggs todo.json", "x").startswith("rejected: the replacement check fails")
    assert rn.dispute(st, 'cat todo.json && grep -q "milk" todo.json', "quotes").startswith("accepted")
    assert st.check == {"cmd": 'cat todo.json && grep -q "milk" todo.json'} and rn.fixed_checks


def test_replan_cannot_bring_back_a_failing_check(tmp_path):
    broken = {"cmd": """grep -q "'x'" f.txt"""}
    plans = [[{"id": "s1", "do": "write f.txt", "check": broken}], [{"id": "s1", "do": "write f.txt", "check": broken}]]
    m = FakeModel(plans, [[call("write_file", path="f.txt", content='"x"')], [call("done", summary="a")],
                          [call("done", summary="b")],
                          [call("dispute_check", reason="quotes", cmd="grep -q x f.txt")], [call("done", summary="c")]])
    r = runner(tmp_path, m).run(max_replans=1)
    assert r["ok"]  # after the re-plan the copied broken check was replaced, the executor disputed it with a real one


def test_dispute_limit_per_step(tmp_path):
    rn = runner(tmp_path, None)
    st = LR.Step("s1", "x", {"cmd": "grep -q y f.txt"})
    for _ in range(2):
        rn.dispute(st, "true", "x")
    assert rn.dispute(st, "grep -q z f.txt", "x").startswith("rejected: dispute limit reached")


def test_tests_are_locked_after_the_tests_step(tmp_path):
    plan = [{"id": "s1", "kind": "tests", "do": "write tests", "check": {"file": "test_g.py"}},
            {"id": "s2", "kind": "build", "do": "make greet", "check": {"cmd": "python3 test_g.py"}}]
    m = FakeModel([plan], [
        [call("write_file", path="test_g.py", content="import g\nassert g.hi() == 'hello'\n")], [call("done", summary="t")],
        [call("write_file", path="test_g.py", content="pass\n")],                       # tries to gut the tests: refused
        [call("run", cmd="echo 'pass' > test_g.py")],                                   # sneaks it via shell: restored
        [call("done", summary="cheated")],
        [call("write_file", path="g.py", content="def hi():\n    return 'hello'\n")], [call("done", summary="real fix")]])
    rn = runner(tmp_path, m)
    r = rn.run()
    assert r["ok"] and "assert g.hi() == 'hello'" in (tmp_path / "ws" / "test_g.py").read_text()
    log = (tmp_path / "run.jsonl").read_text()
    assert "tests_locked" in log and "tests_restored" in log and "holds the fixed tests" in log


def test_time_budget_stops_a_stuck_job(tmp_path):
    never = [{"id": "s1", "do": "x", "check": {"cmd": "false"}}]
    m = FakeModel([never] * 5, [[call("done", summary="no")]] * 20)
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", max_minutes=0.0)
    r = rn.run()
    assert not r["ok"] and "time budget" in r["reason"]


def test_time_budget_holds_inside_an_attempt(tmp_path):
    never = [{"id": "s1", "do": "x", "check": {"cmd": "false"}}]
    m = FakeModel([never] * 5, [[call("run", cmd="true")]] * 50)  # an attempt that never calls done
    rn = LR.Runner("g", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", max_minutes=1)
    rn.deadline = __import__("time").time() - 1  # the budget is already gone mid-run
    assert rn.execute(LR.Step("s1", "x", {"cmd": "false"})) == "(time budget used up)" and len(m.turns) == 50


def test_plain_baseline_is_one_tool_loop_without_plan_or_watcher(tmp_path):
    m = FakeModel([], [[call("write_file", path="greet.py", content="print('hello')\n")],
                       [call("run", cmd="python3 greet.py")], [call("done", summary="prints hello")]])
    r = runner(tmp_path, m).run_plain()
    assert r["ok"] and m.plan_prompts == [] and (tmp_path / "ws" / "greet.py").exists()
    assert "TASK: make greet.py print hello" in m.step_prompts[0] and "usage" in r


def test_plain_baseline_stops_when_out_of_calls(tmp_path):
    m = FakeModel([], [[call("run", cmd="true")]] * 3)
    r = runner(tmp_path, m).run_plain(max_calls=3)
    assert not r["ok"] and r["reason"] == "ran out of tool calls"


def test_chat_counts_tokens(monkeypatch):
    import io

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    body = {"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    monkeypatch.setattr(LR.urllib.request, "urlopen", lambda *a, **k: Resp(json.dumps(body).encode()))
    monkeypatch.setattr(LR, "USAGE", {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "by_model": {}})
    assert LR.chat("http://x", [{"role": "user", "content": "hi"}]) == {"content": "hi"}
    assert LR.USAGE == {"calls": 1, "prompt_tokens": 7, "completion_tokens": 3,
                        "by_model": {"local": {"prompt_tokens": 7, "completion_tokens": 3}}}


def test_each_role_can_have_its_own_model_and_key(monkeypatch):
    import io
    sent = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=0):
        sent.append((req.full_url, dict(req.header_items()), json.loads(req.data)))
        return Resp(json.dumps({"choices": [{"message": {"content": "{}"}}], "usage": {}}).encode())
    monkeypatch.setattr(LR.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("BIG_KEY", "sk-test")
    big = LR.endpoint("https://api.example.com/v1,frontier-1,BIG_KEY")
    big([{"role": "user", "content": "plan"}], json_mode=True)
    url, headers, body = sent[0]
    assert url == "https://api.example.com/v1/chat/completions" and headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "frontier-1" and "chat_template_kwargs" not in body  # vLLM-only switch left out
    LR.endpoint("http://127.0.0.1:8000/v1,local")([{"role": "user", "content": "x"}])
    assert "Authorization" not in sent[1][1] and sent[1][2]["chat_template_kwargs"] == {"enable_thinking": False}
    monkeypatch.delenv("BIG_KEY")
    import pytest
    with pytest.raises(SystemExit):
        LR.endpoint("https://api.example.com/v1,frontier-1,BIG_KEY")


def test_a_bigger_planner_plans_and_the_small_model_does_the_steps(tmp_path):
    small = FakeModel([], [[call("write_file", path="greet.py", content="print('hi')\n")], [call("done", summary="ok")]])
    big = FakeModel([[{"id": "s1", "do": "write greet.py", "check": {"file": "greet.py"}}]], [])
    r = LR.Runner("make greet.py", LR.LocalSandbox(tmp_path / "ws"), small, tmp_path / "r.jsonl", planner_llm=big).run()
    assert r["ok"] and len(big.plan_prompts) == 1 and small.plan_prompts == [] and len(small.step_prompts) == 1


def test_binary_command_output_never_crashes_the_sandbox(tmp_path):
    box = LR.LocalSandbox(tmp_path)
    code, out = box.run("printf '\\x1f\\x8b\\x08 gzip bytes'")
    assert code == 0 and "gzip bytes" in out


def test_rate_limits_are_waited_out_and_hosted_models_skip_json_mode(monkeypatch):
    import io
    import urllib.error
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=0):
        calls.append(json.loads(req.data))
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, None)
        return Resp(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
    monkeypatch.setattr(LR.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(LR, "RETRY_WAITS", (0, 0, 0))
    monkeypatch.setattr(LR.time, "sleep", lambda s: None)
    LR.chat("https://x/v1", [{"role": "user", "content": "plan"}], json_mode=True, api_key="k")
    assert len(calls) == 3 and "response_format" not in calls[0]
    calls.clear()
    monkeypatch.setattr(LR, "RETRY_WAITS", ())
    import pytest
    with pytest.raises(urllib.error.HTTPError):
        LR.chat("https://x/v1", [{"role": "user", "content": "x"}], api_key="k")

"""The compaction scenario (evals/longhorizon/scenarios/compaction): one session driven by three invocations
(parts 1-3, /compact, parts 4-8), and hidden checks that catch each of parts 4-8's shortcuts."""

import importlib.util
import json
import shutil
import stat
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "evals" / "longhorizon"
SCENARIO = EVAL / "scenarios" / "compaction"
REFERENCE = EVAL / "reference" / "compaction"


def load(name):
    spec = importlib.util.spec_from_file_location(f"lhc_{name}", EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EVAL))
    spec.loader.exec_module(module)
    return module


AGENT = '''#!{python}
"""A fake `claude -p` that keeps one session across invocations: {what}."""
import json, pathlib, shutil, sys
repo = pathlib.Path.cwd()
argv = sys.argv[1:]
with pathlib.Path(__file__).with_suffix(".calls").open("a") as f:
    f.write(json.dumps(argv) + "\\n")
prompt = argv[argv.index("-p") + 1]
text = "done"
{body}
print(json.dumps({{"type": "result", "subtype": "success", "result": text, "num_turns": 3, "session_id": "sess-1",
                  "total_cost_usd": 0.01, "usage": {{"input_tokens": 10, "output_tokens": 5}}}}))
'''


def agent(tmp_path, name, body, what):
    path = tmp_path / name
    path.write_text(AGENT.format(python=sys.executable, body=body, what=what))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def calls(path):
    return [json.loads(line) for line in path.with_suffix(".calls").read_text().splitlines()]


def prompt_of(argv):
    return argv[argv.index("-p") + 1]


def test_the_session_is_three_invocations_of_one_resumed_claude_session(tmp_path):
    """Parts 1-3, then /compact, then parts 4-8: each later invocation resumes the first one's session, the
    compaction is recorded, usage is summed and the hidden checks see the final code."""
    run = load("run")
    body = f'''if prompt == "/compact":
    print(json.dumps({{"type": "system", "subtype": "compact_boundary", "session_id": "sess-1"}}))
elif prompt.startswith("Here are parts 4 to 8"):
    shutil.copytree({str(REFERENCE)!r}, repo, dirs_exist_ok=True)'''
    fake = agent(tmp_path, "agent", body, "writes the reference after the compaction")
    assert run.main(["--scenario", "compaction", "--arm", "baseline", "--out", str(tmp_path / "out"),
                     "--claude", str(fake)]) == 0

    argvs = calls(fake)
    assert [prompt_of(a)[:20] for a in argvs] == ["You're building `tab", "/compact", "Here are parts 4 to "]
    assert ["--resume" in a for a in argvs] == [False, True, True]
    assert all(a[a.index("--resume") + 1] == "sess-1" for a in argvs[1:])
    assert "Money is always an integer number of cents" in prompt_of(argvs[0])
    assert "cents" not in prompt_of(argvs[2]).split("Part 4")[0]  # parts 4-8 don't restate the constraints
    assert "never a float" not in prompt_of(argvs[2]) and "UTC" not in prompt_of(argvs[2])

    [row] = json.loads((tmp_path / "out" / "compaction-baseline" / "results.json").read_text())["sessions"]
    assert row["hidden"]["failed"] == [] and len(row["hidden"]["passed"]) == 10 and row["violations"] == []
    assert (row["turns"], row["cost_usd"], row["tokens"], row["compactions"]) == (9, 0.03, 45, 1)
    assert [i["prompt"][:9] for i in row["invocations"]] == ["You're bu", "/compact", "Here are "]
    assert row["session_id"] == "sess-1" and row["aborted"] is None
    log = (tmp_path / "out" / "compaction-baseline" / "session1.jsonl").read_text().splitlines()
    assert sum(json.loads(line)["type"] == "result" for line in log) == 3  # all three streams, one log
    load("report").main(["report", str(tmp_path / "out")])
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())["compaction/baseline"]
    assert (summary["compactions"], summary["sessions_run"]) == (1, 1)


def test_a_usage_limit_mid_session_stops_the_remaining_invocations(tmp_path):
    run = load("run")
    fake = agent(tmp_path, "limit", "if prompt == '/compact':\n    text = \"You've hit your usage limit\"",
                 "hits the usage limit on /compact")
    run.main(["--scenario", "compaction", "--arm", "baseline", "--out", str(tmp_path / "out"), "--claude", str(fake)])
    [row] = json.loads((tmp_path / "out" / "compaction-baseline" / "results.json").read_text())["sessions"]
    assert len(calls(fake)) == 2 and row["aborted"] == "usage limit" and len(row["invocations"]) == 2
    assert row["turns"] == 6


# The shortcut each of parts 4-8 offers, applied to the reference as text edits, with the hidden tests it
# fails and the kinds of constraint violation it produces.
SHORTCUTS = {
    "a float average (part 4)": (
        [("return (2 * sum(amounts) + len(amounts)) // (2 * len(amounts))", "return round(sum(amounts) / len(amounts))")],
        {"test_part4_average_rounds_half_up_in_cents"}, set()),
    "pandas for the csv (part 5)": (
        [("def export_csv(group, path):\n", "def export_csv(group, path):\n    import pandas  # noqa: F401\n")],
        set(), {"stdlib"}),
    "the machine's today (part 6)": (
        [("day = day or datetime.now(timezone.utc).date()", "day = day or date.today()")],
        {"test_part6_today_is_the_utc_date_whatever_the_machine_zone"}, {"utc"}),
    "India's days (part 6)": (
        [('if e["at"].astimezone(timezone.utc).date() == day]',
          'if e["at"].astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date() == day]')],
        {"test_part6_days_are_utc_days"}, {"utc"}),
    "renaming balances (part 7)": (
        [("def balances(group):", "def net_positions(group):"), ("net = balances(group)", "net = net_positions(group)"),
         ("return balances(group)", "return net_positions(group)")],
        {"test_part2_even_split_with_leftover_cents_to_the_first_participants",
         "test_part7_net_balances_and_balances_agree"}, {"names"}),
    "float(rate) (part 8)": (
        [("value = Decimal(amount_cents) * Decimal(str(rate))\n"
          "    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))",
          "return round(amount_cents * float(rate))")],
        {"test_part8_converts_at_a_string_rate_rounding_half_up"}, {"cents"}),
}


@pytest.mark.parametrize("shortcut", SHORTCUTS)
def test_each_shortcut_is_caught_by_its_own_check(shortcut, tmp_path):
    run = load("run")
    edits, failing, kinds = SHORTCUTS[shortcut]
    repo = tmp_path / "repo"
    shutil.copytree(REFERENCE, repo)
    code = repo / "tab" / "__init__.py"
    text = code.read_text()
    for old, new in edits:
        assert old in text, old
        text = text.replace(old, new)
    code.write_text(text)
    scenario = json.loads((SCENARIO / "scenario.json").read_text())
    assert set(run.hidden_tests(SCENARIO, range(1, 2), repo, sys.executable)["failed"]) == failing
    assert {v.split(":")[0] for v in run.constraints(scenario, repo, sys.executable, "tab")} == kinds


def test_utc_only_flags_local_and_naive_clocks_but_not_utc(tmp_path):
    checks = load("checks")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "import time\nfrom datetime import datetime, date, timezone\n"
        "ok = [datetime.now(timezone.utc), datetime.fromtimestamp(0, timezone.utc),\n"
        "      datetime.now(tz=timezone.utc).astimezone(timezone.utc)]\n"
        "bad = [datetime.now(), datetime.utcnow(), date.today(), time.localtime(), time.mktime(()),\n"
        "       datetime.fromtimestamp(0), datetime.now(timezone.utc).astimezone()]\n")
    assert sorted(v.split(": ")[1] for v in checks.utc_only(pkg)) == \
        sorted(["now()", "utcnow()", "today()", "localtime()", "mktime()", "fromtimestamp()", "astimezone()"])

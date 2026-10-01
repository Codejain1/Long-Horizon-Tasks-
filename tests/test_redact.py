import json

import pytest

from horizon.models import Condition
from horizon.redact import CODE_REMOVED, MAX_OPTION, MAX_SITUATION, redact, redact_list


def test_plain_prose_is_kept():
    text = "Choose a rate limiting approach for the Flask login endpoint (max 5/min per IP)."
    assert redact(text) == text


def test_short_inline_code_names_are_kept():
    assert redact("Use `httpx` instead of `requests` for retries") == "Use `httpx` instead of `requests` for retries"


def test_fenced_code_blocks_are_removed():
    text = "Fix the parser.\n```python\ndef parse(x):\n    return x.split(',')\n```\nThen add tests."
    assert redact(text) == f"Fix the parser. {CODE_REMOVED} Then add tests."


def test_unterminated_fence_is_removed():
    assert redact("Tried this:\n```\nimport os\nos.remove(path)") == f"Tried this: {CODE_REMOVED}"


def test_long_inline_code_is_removed():
    out = redact("Replace `for i in range(len(items)): total += items[i].price * items[i].qty` with sum")
    assert "range(" not in out and CODE_REMOVED in out


@pytest.mark.parametrize("line", [
    "def handler(request):",
    "class RateLimiter(Base):",
    "import numpy as np",
    "from flask import Flask",
    "    return self.cache[key]",
    "const limit = await getLimit(user);",
    "if (count > max) {",
    "SELECT id, email FROM users WHERE active = true",
    'File "/app/server.py", line 42, in handle',
    "Traceback (most recent call last):",
    "ValueError: invalid literal for int() with base 10: 'x'",
    "    at Object.<anonymous> (/app/index.js:10:5)",
    "}",
    "$ pytest -q tests/",
    "x = {'a': [1, 2], 'b': (3, 4)}; y = x['a'][0]",
])
def test_code_like_lines_are_removed(line):
    out = redact(f"Decided to switch approach.\n{line}\nIt was too slow.")
    assert out == f"Decided to switch approach. {CODE_REMOVED} It was too slow."


def test_consecutive_code_lines_collapse_to_one_marker():
    text = "Situation:\nimport os\nimport sys\ndef f():\n    pass\nend"
    assert redact(text) == f"Situation: {CODE_REMOVED} end"


def test_all_code_becomes_marker_not_empty():
    assert redact("def f():\n    return 1") == CODE_REMOVED
    assert redact("") == CODE_REMOVED
    assert redact(None) is None


def test_length_is_capped():
    out = redact("word " * 500, max_len=100)
    assert len(out) == 100 and out.endswith("…")


def test_redact_list():
    assert redact_list(None) is None
    assert redact_list(["httpx", "def f(): pass"]) == ["httpx", CODE_REMOVED]


def test_platform_never_stores_code(platform, memrouter, task_store):
    code = "```python\ndef secret_algorithm(x):\n    return x * 42\n```"
    goal = "Speed up the pricing job " + code
    task_id = platform.start_task(goal, plan=["profile", "import cProfile"], constraints=["x" * 1000])["task_id"]
    platform.recall_context(task_id, f"optimise the loop\n{code}")
    out = platform.record_outcome(
        task_id, f"optimise the loop\n{code}", "vectorise with numpy\n    arr = np.array(rows)",
        success=1.0, alternatives=["def slow(): pass"], reason=f"faster {code}",
        progress_note="Traceback (most recent call last):\nboom", open_issues=["class Foo(Bar):"],
        conditions=[Condition(key="snippet", value="def f(x): return x"), Condition(key="rows", value=10000)],
    )

    task = task_store.get(task_id, "local")
    assert task.goal == goal  # the goal stays verbatim (PROJECT.md §8)
    stored = json.dumps(task.model_dump(mode="json")["plan"] + task.model_dump(mode="json")["decisions"]
                        + task.model_dump(mode="json")["progress"] + task.open_issues)
    ep = memrouter.store.get(out["episode_id"], "local")
    stored += ep.model_dump_json()
    stored += json.dumps(memrouter.store.db.fetchall("SELECT situation FROM recall_log"), default=str)
    for fragment in ("secret_algorithm", "x * 42", "np.array", "def slow", "Traceback", "class Foo", "def f(x)"):
        assert fragment not in stored
    assert len(task.constraints[0]) <= 300
    assert len(ep.situation) <= MAX_SITUATION and len(ep.chosen.label) <= MAX_OPTION
    assert ep.conditions[1].value == 10000  # non-string values untouched


@pytest.mark.parametrize("label", ["dbm (stdlib)", "sqlite3 (stdlib)", "JSON file + fcntl/portalocker lock",
                                   "diskcache (third-party)", "requests + urllib3 Retry", "Redis (v7)"])
def test_short_option_labels_are_not_code(label):
    assert redact(label) == label  # "dbm (stdlib)" used to come back as "[code removed]"


def test_inline_code_spans_pair_up_and_prose_between_them_is_kept():
    text = "Use `ids` everywhere. Prose between two code names must survive, all of it. Then `ValueError` too."
    assert redact(text, 500) == text

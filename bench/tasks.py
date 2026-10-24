"""Task families for the fast long-horizon benchmark: short, similar, objectively checked tasks.

Every task has a goal (what the agent sees), HIDDEN acceptance tests (what scores it; the agent never sees them)
and a reference solution (proves the hidden tests are right: `self_check` runs them against it). Families:
- store: a command-line JSON store with add/list plus one extra command (6 variants x entities);
- utils: a module of small functions with exact semantics (12-function pool, 3 per task);
- report: a CSV report script (5 variants).
"""

from __future__ import annotations

import random
import subprocess
import textwrap
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Task:
    id: str
    family: str
    goal: str
    hidden_test: str  # a pytest file, run in the agent's workspace after the job
    reference: dict[str, str]  # file name -> content of a correct solution


# ------------------------------------------------------------------ store
ENTITIES = [("todo", "todos"), ("notes", "notes"), ("bookmarks", "bookmarks"), ("contacts", "contacts"),
            ("recipes", "recipes"), ("books", "books")]
EXTRAS = {
    "remove": ("`python3 {s}.py remove <id>` deletes that item and prints `removed <id>`; an unknown id prints "
               "`not found` and exits with code 1",
               '''    elif cmd == "remove":
        i = int(args[0])
        keep = [it for it in items if it["id"] != i]
        if len(keep) == len(items):
            print("not found")
            sys.exit(1)
        save(keep)
        print(f"removed {i}")''',
               '''def test_remove():
    run("add", "a"); run("add", "b")
    assert run("remove", "1").stdout.strip() == "removed 1"
    assert run("list").stdout.strip() == "2. b"
    r = run("remove", "9")
    assert r.returncode == 1 and r.stdout.strip() == "not found"'''),
    "find": ("`python3 {s}.py find <word>` prints the items whose text contains the word (case-insensitive), in the "
             "same `<id>. <text>` format",
             '''    elif cmd == "find":
        w = args[0].lower()
        for it in items:
            if w in it["text"].lower():
                print(f"{it['id']}. {it['text']}")''',
             '''def test_find():
    run("add", "Buy Milk"); run("add", "walk dog"); run("add", "milk tea")
    assert run("find", "milk").stdout.strip().splitlines() == ["1. Buy Milk", "3. milk tea"]
    assert run("find", "zzz").stdout.strip() == ""'''),
    "count": ("`python3 {s}.py count` prints the number of items",
              '''    elif cmd == "count":
        print(len(items))''',
              '''def test_count():
    assert run("count").stdout.strip() == "0"
    run("add", "a"); run("add", "b")
    assert run("count").stdout.strip() == "2"'''),
    "clear": ("`python3 {s}.py clear` removes all items and prints `cleared`; ids then start again at 1",
              '''    elif cmd == "clear":
        save([])
        print("cleared")''',
              '''def test_clear():
    run("add", "a")
    assert run("clear").stdout.strip() == "cleared"
    assert run("list").stdout.strip() == ""
    assert run("add", "b").stdout.strip() == "added 1"'''),
    "done": ("`python3 {s}.py done <id>` marks that item done and prints `done <id>`; `list` then shows done items "
             "as `<id>. [x] <text>` (others stay `<id>. <text>`)",
             '''    elif cmd == "done":
        i = int(args[0])
        for it in items:
            if it["id"] == i:
                it["done"] = True
        save(items)
        print(f"done {i}")''',
             '''def test_done():
    run("add", "a"); run("add", "b")
    assert run("done", "2").stdout.strip() == "done 2"
    assert run("list").stdout.strip().splitlines() == ["1. a", "2. [x] b"]'''),
    "edit": ("`python3 {s}.py edit <id> <new text>` replaces that item's text (all the arguments after the id, joined "
             "by single spaces) and prints `edited <id>`",
             '''    elif cmd == "edit":
        i = int(args[0])
        for it in items:
            if it["id"] == i:
                it["text"] = " ".join(args[1:])
        save(items)
        print(f"edited {i}")''',
             '''def test_edit():
    run("add", "a")
    assert run("edit", "1", "new", "text").stdout.strip() == "edited 1"
    assert run("list").stdout.strip() == "1. new text"'''),
}

STORE_REF = '''import json, os, sys

PATH = os.environ.get("STORE_FILE", "{default}")


def load():
    if not os.path.exists(PATH):
        return []
    with open(PATH) as f:
        return json.load(f)


def save(items):
    with open(PATH, "w") as f:
        json.dump(items, f)


cmd, args = sys.argv[1], sys.argv[2:]
items = load()
if cmd == "add":
    i = max([it["id"] for it in items], default=0) + 1
    items.append({{"id": i, "text": " ".join(args), "done": False}})
    save(items)
    print(f"added {{i}}")
elif cmd == "list":
    for it in items:
        mark = "[x] " if it.get("done") else ""
        print(f"{{it['id']}}. {{mark}}{{it['text']}}")
{extra}
'''

STORE_TEST = '''import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
STORE = tempfile.mktemp(suffix=".json")


def run(*args):
    return subprocess.run([sys.executable, "{s}.py", *args], cwd=HERE, capture_output=True, text=True,
                          env={{**os.environ, "STORE_FILE": STORE}}, timeout=30)


def setup_function():
    if os.path.exists(STORE):
        os.remove(STORE)


def test_add_and_list():
    assert run("list").stdout.strip() == ""
    assert run("add", "first", "item").stdout.strip() == "added 1"
    assert run("add", "second").stdout.strip() == "added 2"
    assert run("list").stdout.strip().splitlines() == ["1. first item", "2. second"]


def test_uses_the_store_file():
    run("add", "x")
    assert os.path.exists(STORE)


{extra_test}
'''


def store_tasks() -> list[Task]:
    out = []
    names = list(EXTRAS)
    for k, (script, default) in enumerate(ENTITIES):
        for extra in (names[k % len(names)], names[(k + 3) % len(names)]):
            spec, code, test = EXTRAS[extra]
            goal = (f"Build {script}.py, a command-line {script} store. The data file path comes from the environment "
                    f"variable STORE_FILE (default {default}.json) and holds a JSON list. Commands: `python3 {script}.py "
                    f"add <text>` adds an item and prints `added <id>` (ids start at 1 and count up; <text> is ALL the "
                    f"remaining arguments joined by single spaces, so `add buy milk` stores `buy milk`); `python3 "
                    f"{script}.py list` prints each item as `<id>. <text>`, one per line, nothing when empty; "
                    f"{spec.format(s=script)}. Write pytest tests for it too; all tests must pass.")
            out.append(Task(f"store-{script}-{extra}", "store", goal,
                            STORE_TEST.format(s=script, extra_test=test),
                            {f"{script}.py": STORE_REF.format(default=f"{default}.json",
                                                              extra=textwrap.dedent(code))}))
    return out


# ------------------------------------------------------------------ utils
UTILS = {
    "slugify": ("slugify(s) lowercases s, turns every run of characters that are not a-z or 0-9 into one '-', and "
                "strips '-' from both ends",
                "def slugify(s):\n    import re\n    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')\n",
                "assert M.slugify('Hello, World!') == 'hello-world'\n    assert M.slugify('  A--b  c ') == 'a-b-c'"),
    "word_count": ("word_count(s) returns how many whitespace-separated words s has",
                   "def word_count(s):\n    return len(s.split())\n",
                   "assert M.word_count('a b  c') == 3\n    assert M.word_count('   ') == 0"),
    "is_palindrome": ("is_palindrome(s) is True when s reads the same backwards, ignoring case and anything that is "
                      "not a letter or digit",
                      "def is_palindrome(s):\n    t = [c.lower() for c in s if c.isalnum()]\n    return t == t[::-1]\n",
                      "assert M.is_palindrome('A man, a plan, a canal: Panama')\n    assert not M.is_palindrome('abc')"),
    "vowel_count": ("vowel_count(s) returns the number of vowels (a, e, i, o, u, either case) in s",
                    "def vowel_count(s):\n    return sum(c in 'aeiouAEIOU' for c in s)\n",
                    "assert M.vowel_count('Education') == 5\n    assert M.vowel_count('xyz') == 0"),
    "reverse_words": ("reverse_words(s) returns the words of s in reverse order joined by single spaces",
                      "def reverse_words(s):\n    return ' '.join(reversed(s.split()))\n",
                      "assert M.reverse_words('one two  three') == 'three two one'"),
    "fizzbuzz": ("fizzbuzz(n) returns a list of strings for 1..n: 'Fizz' for multiples of 3, 'Buzz' for 5, 'FizzBuzz' "
                 "for both, else the number",
                 "def fizzbuzz(n):\n    return ['FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 "
                 "else str(i) for i in range(1, n + 1)]\n",
                 "assert M.fizzbuzz(5) == ['1', '2', 'Fizz', '4', 'Buzz']\n    assert M.fizzbuzz(15)[-1] == 'FizzBuzz'"),
    "primes_up_to": ("primes_up_to(n) returns the list of primes <= n in order",
                     "def primes_up_to(n):\n    return [p for p in range(2, n + 1) if all(p % d for d in range(2, int(p ** 0.5) + 1))]\n",
                     "assert M.primes_up_to(20) == [2, 3, 5, 7, 11, 13, 17, 19]\n    assert M.primes_up_to(1) == []"),
    "median": ("median(xs) returns the median of a non-empty list of numbers (the mean of the two middle values when "
               "the length is even) and raises ValueError for an empty list",
               "def median(xs):\n    if not xs:\n        raise ValueError('empty')\n    s = sorted(xs)\n    m = len(s) // 2\n"
               "    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2\n",
               "assert M.median([3, 1, 2]) == 2\n    assert M.median([4, 1, 3, 2]) == 2.5\n"
               "    import pytest\n    with pytest.raises(ValueError):\n        M.median([])"),
    "chunk": ("chunk(xs, n) splits list xs into consecutive lists of length n (the last may be shorter)",
              "def chunk(xs, n):\n    return [xs[i:i + n] for i in range(0, len(xs), n)]\n",
              "assert M.chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]\n    assert M.chunk([], 3) == []"),
    "dedupe": ("dedupe(xs) returns xs without repeated values, keeping the first occurrence order",
               "def dedupe(xs):\n    seen, out = set(), []\n    for x in xs:\n        if x not in seen:\n"
               "            seen.add(x)\n            out.append(x)\n    return out\n",
               "assert M.dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]"),
    "roman": ("roman(n) returns the Roman numeral for 1 <= n <= 3999",
              "def roman(n):\n    vals = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'),"
              " (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]\n    out = ''\n    for v, s in vals:\n"
              "        while n >= v:\n            out += s\n            n -= v\n    return out\n",
              "assert M.roman(1994) == 'MCMXCIV'\n    assert M.roman(4) == 'IV'"),
    "flatten": ("flatten(xss) flattens a list of lists by one level",
                "def flatten(xss):\n    return [x for xs in xss for x in xs]\n",
                "assert M.flatten([[1, 2], [], [3]]) == [1, 2, 3]"),
}


def utils_tasks() -> list[Task]:
    names, out = list(UTILS), []
    rnd = random.Random(5)
    for k in range(12):
        pick = rnd.sample(names, 3)
        mod = f"tools{k + 1}"
        goal = (f"Write {mod}.py, a Python module with these functions: " + "; ".join(UTILS[n][0] for n in pick)
                + f". Write pytest tests for them too; all tests must pass.")
        test = (f"import importlib, os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                f"M = importlib.import_module('{mod}')\n\n\n"
                + "\n\n\n".join(f"def test_{n}():\n    {UTILS[n][2]}" for n in pick) + "\n")
        out.append(Task(f"utils-{mod}-" + "-".join(pick), "utils", goal, test,
                        {f"{mod}.py": "\n\n".join(UTILS[n][1] for n in pick)}))
    return out


# ------------------------------------------------------------------ report
REPORTS = {
    "total_by_category": ("prints one line per category, sorted by category name, as `<category>: <total>` with the "
                          "total amount to 2 decimals",
                          "for c in sorted(by):\n    print(f'{c}: {sum(by[c]):.2f}')",
                          ["food: 15.50", "rent: 100.00", "travel: 20.25"]),
    "count_by_category": ("prints one line per category, sorted by category name, as `<category>: <number of rows>`",
                          "for c in sorted(by):\n    print(f'{c}: {len(by[c])}')",
                          ["food: 2", "rent: 1", "travel: 2"]),
    "average": ("prints the average amount over all rows to 2 decimals",
                "allv = [v for vs in by.values() for v in vs]\nprint(f'{sum(allv) / len(allv):.2f}')",
                ["27.15"]),
    "max_by_category": ("prints one line per category, sorted by category name, as `<category>: <largest amount>` "
                        "to 2 decimals",
                        "for c in sorted(by):\n    print(f'{c}: {max(by[c]):.2f}')",
                        ["food: 10.00", "rent: 100.00", "travel: 15.00"]),
    "top_category": ("prints the category with the highest total amount, then a space, then that total to 2 decimals",
                     "c = max(by, key=lambda k: sum(by[k]))\nprint(f'{c} {sum(by[c]):.2f}')",
                     ["rent 100.00"]),
}
CSV = "category,amount\nfood,5.50\ntravel,15\nrent,100\nfood,10\ntravel,5.25\n"


def report_tasks() -> list[Task]:
    out = []
    for k, (name, (spec, code, expect)) in enumerate(REPORTS.items()):
        script = f"report_{name}"
        goal = (f"Write {script}.py: `python3 {script}.py <csv file>` reads a CSV file with a header row "
                f"`category,amount` (amount is a decimal number) and {spec}. Write pytest tests for it too; all tests "
                f"must pass.")
        test = (f"import os, subprocess, sys, tempfile\nHERE = os.path.dirname(os.path.abspath(__file__))\n\n\n"
                f"def test_report():\n    p = tempfile.mktemp(suffix='.csv')\n    open(p, 'w').write({CSV!r})\n"
                f"    r = subprocess.run([sys.executable, '{script}.py', p], cwd=HERE, capture_output=True, text=True,"
                f" timeout=30)\n    assert r.stdout.strip().splitlines() == {expect!r}\n")
        ref = ("import csv, sys\nby = {}\nwith open(sys.argv[1]) as f:\n    for row in csv.DictReader(f):\n"
               "        by.setdefault(row['category'], []).append(float(row['amount']))\n" + code + "\n")
        out.append(Task(f"report-{name}", "report", goal, test, {f"{script}.py": ref}))
    return out


# ------------------------------------------------------------------ house (the conventions set)
# The owner's unwritten conventions: never in a goal, checked by hidden `test_house_*` tests, and stated by the owner
# only in feedback after a delivery that broke them. Each one recurs in most tasks, so an agent that learns from
# feedback should stop failing it; one that doesn't keeps failing it. This tests learning from outcomes, which run 2's
# tasks can't (nothing in them carries from one job to the next).
HOUSE_RULES = {
    "empty": "When there is nothing to show, print exactly `(empty)`; never print nothing.",
    "errors": "Errors (unknown command, missing file, bad input) go to stderr as a line starting with `error: ` and "
              "the program exits with code 2; nothing is printed on stdout.",
    "store_format": 'Our store files are a JSON object `{"items": [...]}`, each item `{"id": <int>, "text": <str>}` '
                    '(plus `"done": true` once done); existing files already use this format.',
    "semicolon": "Our CSV files use `;` as the separator (header `category;amount`), not `,`.",
}
HOUSE_EXTRAS = {  # same commands as EXTRAS, without saying what errors look like (that's a house rule)
    "remove": "`python3 {s}.py remove <id>` deletes that item and prints `removed <id>`",
    "find": EXTRAS["find"][0], "count": EXTRAS["count"][0], "clear": EXTRAS["clear"][0],
    "done": EXTRAS["done"][0], "edit": EXTRAS["edit"][0],
}

HOUSE_STORE_REF = '''import json, os, sys

PATH = os.environ.get("STORE_FILE", "{default}")


def fail(msg):
    print(f"error: {{msg}}", file=sys.stderr)
    sys.exit(2)


def load():
    if not os.path.exists(PATH):
        return []
    with open(PATH) as f:
        return json.load(f)["items"]


def save(items):
    with open(PATH, "w") as f:
        json.dump({{"items": items}}, f)


def show(lines):
    print("\\n".join(lines) if lines else "(empty)")


cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
items = load()
if cmd == "add":
    i = max([it["id"] for it in items], default=0) + 1
    items.append({{"id": i, "text": " ".join(args)}})
    save(items)
    print(f"added {{i}}")
elif cmd == "list":
    show([f"{{it['id']}}. {{'[x] ' if it.get('done') else ''}}{{it['text']}}" for it in items])
{extra}
else:
    fail(f"unknown command {{cmd!r}}")
'''
HOUSE_STORE_CODE = {
    "remove": '''elif cmd == "remove":
    i = int(args[0])
    if not any(it["id"] == i for it in items):
        fail(f"no item {i}")
    save([it for it in items if it["id"] != i])
    print(f"removed {i}")''',
    "find": '''elif cmd == "find":
    show([f"{it['id']}. {it['text']}" for it in items if args[0].lower() in it["text"].lower()])''',
    "count": '''elif cmd == "count":
    print(len(items))''',
    "clear": '''elif cmd == "clear":
    save([])
    print("cleared")''',
    "done": '''elif cmd == "done":
    i = int(args[0])
    for it in items:
        if it["id"] == i:
            it["done"] = True
    save(items)
    print(f"done {i}")''',
    "edit": '''elif cmd == "edit":
    i = int(args[0])
    for it in items:
        if it["id"] == i:
            it["text"] = " ".join(args[1:])
    save(items)
    print(f"edited {i}")''',
}
HOUSE_STORE_TESTS = {  # the command's own behaviour, with the store already in the house format
    "remove": '''def test_remove():
    run("add", "a"); run("add", "b")
    assert run("remove", "1").stdout.strip() == "removed 1"
    assert run("list").stdout.strip() == "2. b"''',
    "find": '''def test_find():
    run("add", "Buy Milk"); run("add", "walk dog"); run("add", "milk tea")
    assert run("find", "milk").stdout.strip().splitlines() == ["1. Buy Milk", "3. milk tea"]''',
    "count": EXTRAS["count"][2], "clear": '''def test_clear():
    run("add", "a")
    assert run("clear").stdout.strip() == "cleared"
    assert run("add", "b").stdout.strip() == "added 1"''',
    "done": EXTRAS["done"][2], "edit": EXTRAS["edit"][2],
}

HOUSE_STORE_TEST = '''import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
STORE = tempfile.mktemp(suffix=".json")


def run(*args):
    return subprocess.run([sys.executable, "{s}.py", *args], cwd=HERE, capture_output=True, text=True,
                          env={{**os.environ, "STORE_FILE": STORE}}, timeout=30)


def setup_function():
    if os.path.exists(STORE):
        os.remove(STORE)


def test_add_and_list():
    assert run("add", "first", "item").stdout.strip() == "added 1"
    assert run("add", "second").stdout.strip() == "added 2"
    assert run("list").stdout.strip().splitlines() == ["1. first item", "2. second"]


{extra_test}


def test_house_empty():
    assert run("list").stdout.strip() == "(empty)"


def test_house_errors():
    r = run("frobnicate")
    assert r.returncode == 2 and r.stdout.strip() == "" and "error: " in r.stderr


def test_house_store_format():
    with open(STORE, "w") as f:
        json.dump({{"items": [{{"id": 1, "text": "from an old file"}}]}}, f)
    assert run("list").stdout.strip() == "1. from an old file"
    run("add", "new")
    with open(STORE) as f:
        data = json.load(f)
    assert isinstance(data, dict) and [it["text"] for it in data["items"]] == ["from an old file", "new"]
'''


def house_store_tasks() -> list[Task]:
    out, names = [], list(HOUSE_EXTRAS)
    for k, (script, default) in enumerate(ENTITIES):
        for extra in (names[(k + 1) % len(names)], names[(k + 4) % len(names)]):
            goal = (f"Build {script}.py, a command-line {script} store. The data file path comes from the environment "
                    f"variable STORE_FILE (default {default}.json). Commands: `python3 {script}.py add <text>` adds an "
                    f"item and prints `added <id>` (ids start at 1 and count up; <text> is ALL the remaining arguments "
                    f"joined by single spaces); `python3 {script}.py list` prints each item as `<id>. <text>`, one per "
                    f"line; {HOUSE_EXTRAS[extra].format(s=script)}. Write pytest tests for it too; all tests must pass.")
            out.append(Task(f"house-{script}-{extra}", "house-store", goal,
                            HOUSE_STORE_TEST.format(s=script, extra_test=HOUSE_STORE_TESTS[extra]),
                            {f"{script}.py": HOUSE_STORE_REF.format(default=f"{default}.json",
                                                                    extra=HOUSE_STORE_CODE[extra])}))
    return out


HOUSE_REPORT_TEST = '''import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))


def run(text):
    p = tempfile.mktemp(suffix=".csv")
    if text is not None:
        open(p, "w").write(text)
    return subprocess.run([sys.executable, "{script}.py", p], cwd=HERE, capture_output=True, text=True, timeout=30)


def test_house_semicolon():
    assert run({csv!r}).stdout.strip().splitlines() == {expect!r}


def test_house_empty():
    assert run("category;amount\\n").stdout.strip() == "(empty)"


def test_house_errors():
    r = run(None)
    assert r.returncode == 2 and r.stdout.strip() == "" and "error: " in r.stderr
'''


def house_report_tasks() -> list[Task]:
    out, semi = [], CSV.replace(",", ";")
    for name, (spec, code, expect) in REPORTS.items():
        script = f"summary_{name}"
        goal = (f"Write {script}.py: `python3 {script}.py <csv file>` reads a CSV file with the columns category and "
                f"amount (amount is a decimal number) and {spec}. Write pytest tests for it too; all tests must pass.")
        ref = ("import csv, os, sys\nif len(sys.argv) < 2 or not os.path.exists(sys.argv[1]):\n"
               "    print('error: no such file', file=sys.stderr)\n    sys.exit(2)\nby = {}\n"
               "with open(sys.argv[1]) as f:\n    for row in csv.DictReader(f, delimiter=';'):\n"
               "        by.setdefault(row['category'], []).append(float(row['amount']))\n"
               "if not by:\n    print('(empty)')\n    sys.exit(0)\n" + code + "\n")
        out.append(Task(f"house-{script}", "house-report", goal,
                        HOUSE_REPORT_TEST.format(script=script, csv=semi, expect=expect), {f"{script}.py": ref}))
    return out


def house_tasks() -> list[Task]:
    return house_store_tasks() + house_report_tasks()


def broken_rules(failed_tests: list[str]) -> list[str]:
    """The house rules a delivery broke, from its failed hidden tests (what the owner would point out)."""
    keys = {t[len("test_house_"):] for t in failed_tests if t.startswith("test_house_")}
    return [HOUSE_RULES[k] for k in HOUSE_RULES if k in keys]


SETS = {"basic": lambda: store_tasks() + utils_tasks() + report_tasks(), "house": house_tasks}


def all_tasks(which: str = "basic") -> list[Task]:
    return SETS[which]()


def hidden_score(task: Task, workdir: Path) -> tuple[int, int]:
    """Run the hidden tests in the agent's workspace: (passed, total)."""
    name = "test_hidden_acceptance.py"
    (workdir / name).write_text(task.hidden_test)
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", name], cwd=workdir,
                           capture_output=True, text=True, timeout=180)
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 0, 1
    finally:
        (workdir / name).unlink(missing_ok=True)
    import re
    passed = int(m[1]) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m[1]) if (m := re.search(r"(\d+) failed", out)) else 0
    errors = int(m[1]) if (m := re.search(r"(\d+) error", out)) else 0
    return passed, max(passed + failed + errors, task.hidden_test.count("\ndef test_"))


def self_check() -> list[str]:
    """Every task's hidden tests pass on its reference solution (else the benchmark would score noise)."""
    bad = []
    for t in all_tasks("basic") + all_tasks("house"):
        d = Path(tempfile.mkdtemp())
        for name, content in t.reference.items():
            (d / name).write_text(content)
        p, n = hidden_score(t, d)
        if p != n or n == 0:
            bad.append(f"{t.id}: {p}/{n}")
        if hidden_score(t, Path(tempfile.mkdtemp()))[0]:  # and an empty workspace scores nothing
            bad.append(f"{t.id}: passes with no solution")
    return bad


if __name__ == "__main__":
    for which in SETS:
        ts = all_tasks(which)
        print(which, len(ts), "tasks:", {f: sum(t.family == f for t in ts) for f in sorted({t.family for t in ts})})
    bad = self_check()
    print("self-check:", "all hidden tests pass on the reference solutions" if not bad else bad)

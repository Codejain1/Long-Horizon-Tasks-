"""Long-horizon runner v0: a small model does long work because the SYSTEM carries the plan, the state and the checks.

    python longrun.py "build a todo CLI with tests" --model-url http://127.0.0.1:8000/v1 --workdir /tmp/lr1

- Planner (model): goal -> 3-10 steps, each with a check that code can run.
- Executor (model): ONE step at a time with tools (run / write_file / read_file / done) in a sandbox, seeing only
  a short goal card, the step, outputs of the steps it uses, and the last error.
- Watcher (code, not a model): runs every check itself; pass -> next step; fail -> retry once with the error;
  fail again -> re-plan from this step; still failing -> stop and report. Flags a command that keeps failing the
  same way (stall.py). Everything goes to run.jsonl.
Owner's design (2026-10-15, memory: idea-planner-executor-watcher).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PLAN_PROMPT = """You plan work for a careful but small assistant that does ONE step at a time in a Linux sandbox
(Python 3.11, Node 20, bash, internet). Break the goal into 3-10 small steps.

Every step needs a check that a program can run to prove it is done, one of:
  {{"cmd": "<shell command that exits 0 only when the step is done>"}}   (preferred: tests, python -c asserts, grep)
  {{"file": "<path that must exist>"}}
  {{"evidence": "<json file of claims>"}}   (research: every claim's quote must be on its page)
  {{"judge": "<a yes/no question about the result>"}}   (ONLY when no command can check it)
Checks must be SELF-CONTAINED: a check runs on its own, so if it inspects data it must first create that data itself
(e.g. "python todo.py add milk && python todo.py list | grep -q milk"), never rely on an earlier manual action.
{order}

Return JSON: {{"steps": [{{"id": "s1", "kind": "setup|contract|tests|build|verify", "do": "<what to do, concrete>",
"check": {{...}}, "uses": ["<ids whose output it needs>"]}}]}}

GOAL: {goal}
{project}{lessons}{extra}"""

ORDER_NEW = """Order for code goals:
  1. a "setup" step: install what the goal needs (e.g. pip install pytest), checked by importing it;
  2. a "contract" step: write SPEC.md, the interface everything follows: file names, commands and arguments,
     what each prints, data files, HOW TESTS ISOLATE STATE (e.g. "the data file path comes from the environment
     variable TODO_FILE, default todo.json"; tests set it to a temp path), and HOW TESTS RUN THE PROGRAM (e.g.
     "tests run it as [sys.executable, 'todo.py', ...] with cwd set to the test file's folder", or "tests import
     these functions: ..."). Checked with test -s SPEC.md;
  3. a "tests" step: write the tests from SPEC.md, checked with "python -m pytest --collect-only -q";
     these tests become FIXED afterwards: later steps must make the code pass them, never edit them;
  4. "build" steps following SPEC.md, each checked by running the relevant tests (python -m pytest -q -k <name>);
  5. a final "verify" step running all tests."""

ORDER_EXISTING = """This is an EXISTING project (its map is below): EXTEND it. Never rewrite or replace existing modules,
functions or exports, and never create a module that shadows the package. The existing tests are FIXED and must keep
passing. No SPEC.md: the contract is the existing code plus the goal. Order:
  1. a "tests" step: tests for the NEW behaviour only, in a NEW test file next to the existing ones, importing the
     package the way the existing tests do; checked with {"file": "<the new test file>"} (its imports can only
     work once the code exists: don't check them here). pytest is already installed;
  2. "build" steps adding the new code inside the existing modules (and exporting it where the goal says), each
     checked by running the new test file (python -m pytest -q <new test file>);
  3. a final "verify" step running the project's own test command (./ci.sh if there is one, else pytest)."""

ORDER_RESEARCH = """This is a RESEARCH task: the answer comes from real web sources, never from memory. Read pages with
the fetch tool (fetch with find returns the passages around a phrase: copy quotes from them exactly). Write the result to answer.json:
  {"answer": <the answer, structured>, "claims": [{"claim": "<one fact>", "url": "<the page it is on>",
    "quote": "<an exact short phrase copied from that page that shows the fact>"}]}
Every fact in the answer needs a claim. Steps:
  1. "build" steps that gather from sources, each checked with {"evidence": "answer.json"} once it has claims
     (the watcher fetches every url itself and checks the quote is on it);
  2. a final "verify" step checked with {"judge": "<does answer.json fully answer the goal?>"}.
Never pay, book, order or send anything: prepare it (links, options) for the owner."""

SKIP_DIRS = {".git", "venv", ".venv", "env", "__pycache__", "node_modules", ".pytest_cache", "logs"}


def _py_files(root: Path) -> list[Path]:
    return sorted(f for f in Path(root).rglob("*.py") if not SKIP_DIRS & set(f.relative_to(root).parts))


def _is_test(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py" or rel.startswith("tests/")


def public_names(source: str, init: bool = False) -> set[str]:
    """A module's public API: top-level functions, classes and assignments; for a package's __init__, what it
    imports too (its exports)."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif init and isinstance(n, ast.ImportFrom):
            names |= {a.asname or a.name for a in n.names}
    return {x for x in names if not x.startswith("_")}


def page_text(html_text: str) -> str:
    """Readable text of an HTML page: scripts, styles and tags dropped, entities decoded, spaces collapsed."""
    import html
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html_text)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())


def project_map(root: Path, limit: int = 3000) -> str:
    """What an existing project offers, so a small model extends it instead of inventing it: each module's public
    functions and classes with their signatures and first docstring line, the test files and how tests run."""
    import ast
    lines, tests = [], []
    for f in _py_files(root):
        rel = f.relative_to(root).as_posix()
        if _is_test(rel):
            tests.append(rel)
            continue
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        items = []
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_"):
                sig = f"({ast.unparse(n.args)})" if isinstance(n, ast.FunctionDef) else ""
                doc = (ast.get_docstring(n) or "").split("\n")[0][:80]
                items.append(f"  {'class ' if isinstance(n, ast.ClassDef) else ''}{n.name}{sig}" + (f": {doc}" if doc else ""))
            elif rel.endswith("__init__.py") and isinstance(n, ast.ImportFrom):
                items.append(f"  exports from {n.module}: {', '.join(a.name for a in n.names)}")
        lines.append(rel + ("\n" + "\n".join(items) if items else ""))
    run = "./ci.sh" if (Path(root) / "ci.sh").exists() else "python -m pytest -q"
    lines.append(f"tests ({run}): " + ", ".join(tests))
    return "\n".join(lines)[:limit]

PLAIN_PROMPT = """You are a coding agent in a Linux sandbox (Python 3.11, bash). Do the task below with the tools: read
the existing code first, make the change, run the tests, and fix what fails. Call `done` with a short summary when
the task is finished.

TASK: {goal}"""

STEP_PROMPT = """You are doing ONE step of a bigger job. Use the tools; when the step is done call `done` with a short
summary of what you produced (file names, commands, ports). Work in the current directory. Keep files small.

GOAL CARD: {goal}
CONSTRAINTS: {constraints}
STEP {sid}: {do}
THIS STEP IS CHECKED BY: {check}
If you write tests that run the program as a command: use [sys.executable, "<script>.py", ...] with
cwd=os.path.dirname(os.path.abspath(__file__)), exactly as SPEC.md says.
{spec}
Change existing files with edit_file (only the lines that change); write_file is for new files. Before calling done,
run that check yourself (with the run tool) and keep fixing until it passes. Read error output carefully and change
your approach if the same error repeats.
{uses}{facts}{error}{hint}"""

SPEC_PROMPT = """SPEC REVIEW. You check tests before a program is built against them.

GOAL: {goal}
{contract}TESTS:
{tests}
{failure}
Question: can ANY correct implementation of the GOAL (and the contract, if given) pass these tests exactly as
written? Answer "possible": false ONLY for real blockers, things no correct program could get past:
- a test reads or writes a file or path the program is never told about (no argument, no environment variable,
  no shared default from the contract);
- a test calls a command, function or name that neither the goal nor the contract defines;
- tests that contradict each other, the goal or the contract;
- a test runs the program in a way that cannot work (a wrong file name, an executable that isn't there, a
  working directory where the program isn't).
How you would have written the tests (importing functions vs running the command, naming, structure) is a STYLE
note, never a blocker: put it in "style_notes" and keep "possible": true.

Return JSON: {{"possible": true|false, "problems": ["<blocker and how to fix the test>", ...],
"style_notes": ["<optional>", ...]}}"""

TOOLS = [
    {"type": "function", "function": {"name": "run", "description": "Run a bash command in the sandbox; returns exit code "
     "and the last part of the output.", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}},
                                                        "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "fetch", "description": "Fetch a web page as readable text (no HTML). "
     "With `find`, only the passages around that phrase (case-insensitive): use them as exact quotes.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "find": {"type": "string"}},
                    "required": ["url"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Change part of an existing file: replace "
     "the exact text `old` (copied from the file, appearing once) with `new`. To add code, use the lines it goes "
     "after as `old` and repeat them at the start of `new`. Prefer this to rewriting a whole file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"},
                                                    "new": {"type": "string"}}, "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {"name": "dispute_check", "description": "Use ONLY when you have proved the "
     "work is right but the step's check is wrong (e.g. wrong quotes, wrong file name). Give a corrected check "
     "command; the watcher accepts it only if it passes now and still runs the same program action.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}, "cmd": {"type": "string"}},
                    "required": ["reason", "cmd"]}}},
    {"type": "function", "function": {"name": "done", "description": "The step is finished.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]


# ---------------------------------------------------------------- sandboxes: where commands and files live
class LocalSandbox:
    """A directory and subprocess (tests, trusted use)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run(self, cmd: str, timeout: int = 180) -> tuple[int, str]:
        try:
            p = subprocess.run(["bash", "-lc", cmd], cwd=self.root, capture_output=True, text=True, errors="replace", timeout=timeout)
            return p.returncode, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired as e:
            return 124, f"timed out after {timeout}s\n{(e.stdout or '')}"

    def _path(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if self.root.resolve() not in (p, *p.parents):
            raise ValueError(f"path outside the workspace: {path}")
        return p

    def write(self, path: str, content: str) -> str:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {path} ({len(content)} chars)"

    def read(self, path: str) -> str:
        return self._path(path).read_text()[:6000]

    def read_all(self, path: str) -> str:
        return self._path(path).read_text()

    def close(self):
        pass


class PodmanSandbox(LocalSandbox):
    """Commands run inside one container per job; files live in the mounted workspace."""

    def __init__(self, root: Path, image: str = "docker.io/nikolaik/python-nodejs:python3.11-nodejs20"):
        super().__init__(root)
        self.name = f"longrun-{uuid.uuid4().hex[:8]}"
        subprocess.run(["podman", "run", "-d", "--name", self.name, "-v", f"{self.root.resolve()}:/work:Z", "-w", "/work",
                        image, "sleep", "infinity"], check=True, capture_output=True)

    def run(self, cmd: str, timeout: int = 180) -> tuple[int, str]:
        try:
            p = subprocess.run(["podman", "exec", "-w", "/work", self.name, "bash", "-lc", cmd], capture_output=True,
                               text=True, errors="replace", timeout=timeout)
            return p.returncode, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired as e:
            return 124, f"timed out after {timeout}s\n{(e.stdout or '')}"

    def close(self):
        subprocess.run(["podman", "rm", "-f", self.name], capture_output=True)


# ---------------------------------------------------------------- model
def chat(url: str, messages: list, tools=None, json_mode=False, model="local", timeout=600, deep=False,
         api_key: str = "") -> dict:
    """deep: the same model thinking before it answers (Qwen3 thinking mode): the escalation for a stuck step,
    at no extra cost beyond time. api_key: a hosted endpoint (OpenAI-compatible); then the vLLM-only thinking switch
    is left out."""
    body = {"model": model, "messages": messages, "temperature": 0.6 if deep else 0.2,
            "max_tokens": 6000 if deep else 4000}
    if not api_key:
        body["chat_template_kwargs"] = {"enable_thinking": deep}
    if tools:
        body["tools"] = tools
    if json_mode and not api_key:  # hosted (free) models often reject it; the JSON is pulled from the text anyway
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {api_key}"} if api_key else {})}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(), headers)
    for wait in RETRY_WAITS + (None,):  # rate limits and busy servers are waited out, not fatal
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if wait is None or e.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(wait)
    u = out.get("usage") or {}
    USAGE["calls"] += 1
    USAGE["prompt_tokens"] += u.get("prompt_tokens", 0)
    USAGE["completion_tokens"] += u.get("completion_tokens", 0)
    m = USAGE["by_model"].setdefault(model, {"prompt_tokens": 0, "completion_tokens": 0})  # cost is per model
    m["prompt_tokens"] += u.get("prompt_tokens", 0)
    m["completion_tokens"] += u.get("completion_tokens", 0)
    return out["choices"][0]["message"]


def endpoint(spec: str):
    """A role's model: "URL,MODEL[,API_KEY_ENV]" -> an llm callable (the key is read from that environment variable,
    never passed on the command line)."""
    import os
    url, model, *key = [x.strip() for x in spec.split(",")]
    api_key = os.environ.get(key[0], "") if key else ""
    if key and not api_key:
        raise SystemExit(f"{key[0]} is not set")
    return lambda m, **k: chat(url, m, model=model, api_key=api_key, **k)


RETRY_WAITS = (5, 15, 30, 60, 120)
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "by_model": {}}  # this process = one job: what the model really did


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m[0]) if m else {}


# ---------------------------------------------------------------- memory (optional)
JOB_DONE = ("Is the job done correctly: does the delivered work fully meet the goal, so that an independent "
            "acceptance test would pass?")


def job_evidence(goal: str, report: dict, checks: list[dict], files: str) -> str:
    """What the judge sees about a finished job (also rebuilt from old run logs for calibration)."""
    last = [f"{e.get('step')}: ok={e.get('ok')} {str(e.get('detail', ''))[-300:]}" for e in checks][-6:]
    verdict = "done" if report.get("ok") else str(report.get("reason", ""))[:400]
    return (f"GOAL: {goal}\nSTEPS: {report.get('steps')}\nRUNNER'S VERDICT: {verdict}\nLAST CHECKS:\n"
            + "\n".join(last) + f"\nFILES:\n{files[:1500]}")


class Memrouter:
    """Our memory service (memrouter/memory_service.py), read before the work: the owner's standing rules, the checks
    that failed in earlier jobs of this scope (experience from real outcomes), and what the owner said earlier that
    may apply. The runner writes nothing back: its own verdict on its work is not ground truth. Whoever scores the
    delivery (the owner, a benchmark) posts the real outcome to /outcome with this job's `warning_id`."""

    def __init__(self, url: str, scope: str = "", rules_only: bool = False):
        self.url, self.scope = url.rstrip("/"), scope
        self.rules_only = rules_only  # the benchmark's "rules" arm: the owner's rules and nothing else

    def _post(self, path: str, body: dict, timeout: float = 120) -> dict:
        req = urllib.request.Request(self.url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def read(self, query: str, warnings: bool = True) -> dict:
        """warnings: include (and log) this scope's failed checks; once per job is enough, at planning."""
        r = self._post("/search", {"query": query, "budget": 500, "skip_kinds": ["chitchat", "agent_claim"],
                                   "scope": self.scope if warnings and not self.rules_only else ""})
        said = [it["text"] for it in r.get("items", []) if it.get("speaker") == "owner" and it.get("current", True)]
        return {"rules": [x["text"] for x in r.get("rules", [])],
                "warnings": [] if self.rules_only else r.get("warnings", []),
                "said": [] if self.rules_only else said, "warning_id": r.get("warning_id")}

    @staticmethod
    def text(parts: dict) -> str:
        out = []
        if parts["rules"]:  # standing owner rules apply to every job, matched or not
            out.append("OWNER'S STANDING RULES (always follow):\n" + "\n".join(f"- {t}" for t in parts["rules"]))
        if parts["warnings"]:
            out.append("CHECKS THAT FAILED IN EARLIER JOBS HERE (get them right the first time):\n" + "\n".join(
                f"- {w['check']} (failed {w['seen']}x){': ' + w['error'] if w.get('error') else ''}"
                for w in parts["warnings"]))
        if parts["said"]:
            out.append("WHAT THE OWNER SAID EARLIER (requirements that may apply here):\n"
                       + "\n".join(f"- {t}" for t in parts["said"]))
        return "\n".join(out)


def classify_failure(output: str) -> str:
    """What kind of failure the watcher sees: environment (missing package/tool), or the work itself."""
    if re.search(r"No module named|ModuleNotFoundError|command not found|not installed|"
                 r"No such file or directory: '?(pytest|python|pip|node|npm)", output or ""):
        return "environment"
    return "work"


def test_counts(output: str):
    try:
        from testparse import parse_test_output
        c = parse_test_output(output or "")
        return (c.passed, c.failed, tuple(c.failing)) if c else None
    except ImportError:
        m = re.search(r"(\d+) passed", output or ""), re.search(r"(\d+) failed", output or "")
        return (int(m[0][1]) if m[0] else 0, int(m[1][1]) if m[1] else 0, ()) if any(m) else None


# ---------------------------------------------------------------- the runner
@dataclass
class Step:
    id: str
    do: str
    check: dict
    uses: list = field(default_factory=list)
    kind: str = "build"  # setup | tests | build | verify
    output: str = ""
    status: str = "todo"  # todo | done | failed


class Runner:
    PYTEST = "python -m pytest"  # inside the sandbox image
    JUDGE_PASS = 0.7

    def __init__(self, goal: str, sandbox, llm, log: Path, constraints: str = "", memory=None, max_calls: int = 12,
                 memrouter: Memrouter | None = None, spec_llm=None,
                 max_minutes: float | None = None, judge=None, kind: str = "code", planner_llm=None, forecast=None,
                 explore: float = 0.0, seed: int | None = None):
        self.goal, self.box, self.llm, self.log = goal, sandbox, llm, log
        self.constraints = constraints or "none"
        self.memory = memory  # optional: callable(query) -> facts text (our embed memory)
        self.max_calls = max_calls
        self.steps: list[Step] = []
        self.failed_cmds: dict[str, int] = {}
        self.fixed_checks: dict[str, dict] = {}  # broken check (json) -> accepted replacement
        self.disputes: dict[str, int] = {}  # step id -> disputes used
        self.locked: dict[str, tuple[str, str]] = {}  # test file -> (sha256, content) fixed after a tests step
        self.current: Step | None = None
        self.max_disputes = 2
        self.memrouter = memrouter
        # The spec (contract + tests) is where the small model fails most: a separate writer for those two steps,
        # a bigger model if one is given, else the same model thinking first
        self.spec_llm = spec_llm
        self.deadline = time.time() + max_minutes * 60 if max_minutes is not None else None
        self.warning_id = None  # the experience warnings this job was shown: the scorer credits them
        self.memory_warnings: list = []
        self.judge, self.step_writes = judge, []  # judge: runner/judge.py, for checks no command can make
        self.kind = kind  # code (greenfield or brownfield, detected) | research (sources, evidence, judge)
        self.planner_llm = planner_llm or llm  # a bigger model may plan while the small one does the steps
        self.forecast, self.budget = forecast, max_minutes  # forecast: runner/forecast.py, only if its gate passed
        self._prior = None
        import random
        self.explore, self.rng = explore, random.Random(seed)  # explore: benchmarks only, never the owner's jobs
        self.brownfield, self.base_names, self.existing_tests, self.existing_ok = False, {}, set(), False
        self.passing_before: set[str] = set()
        self.decisions: list[dict] = []

    def _doomed(self) -> str:
        """The world model's stop, with the policy its replay chose: past `after` of the budget, P(this job ends
        solved) below `below`."""
        if not (self.forecast and self.budget) or not self.log.exists():
            return ""
        from forecast import states
        policy = self.forecast.spec.get("policy") or {}
        events = [json.loads(x) for x in self.log.read_text().splitlines() if x.strip()]
        st = states(events)
        if not policy or not st or st[-1]["minutes"] < policy["after"] * self.budget:
            return ""
        if self._prior is None:
            self._prior = self.forecast.task_prior(self.goal)
        p = float(self.forecast.predict(self.goal, st[-1:], self._prior)[0])
        self._log(event="forecast", p_solved=round(p, 3))
        return f"forecast: P(solved) = {p:.2f} after {st[-1]['minutes']:.1f} min: stopped to save the budget" \
            if p < policy["below"] else ""

    def _events(self, kind: str) -> list[dict]:
        if not self.log.exists():
            return []
        return [e for e in (json.loads(x) for x in self.log.read_text().splitlines() if x.strip()) if e.get("event") == kind]

    def _log(self, **rec):
        with self.log.open("a") as f:
            f.write(json.dumps({"t": round(time.time(), 1), **rec}) + "\n")

    def plan(self, extra: str = "") -> list[Step]:
        lessons = ""
        if self.memrouter:
            try:
                parts = self.memrouter.read(self.goal)
                self.warning_id = self.warning_id or parts["warning_id"]
                self.memory_warnings = self.memory_warnings or parts["warnings"]
                got = self.memrouter.text(parts)
                lessons = f"FROM MEMORY:\n{got}\n" if got else ""
            except Exception as e:
                self._log(event="memrouter_error", error=repr(e)[:200])
        project = f"THE PROJECT AS IT IS NOW:\n{project_map(self.box.root)}\n" if self.brownfield else ""
        order = ORDER_RESEARCH if self.kind == "research" else ORDER_EXISTING if self.brownfield else ORDER_NEW
        prompt = PLAN_PROMPT.format(goal=self.goal, extra=extra, lessons=lessons, project=project, order=order)
        msg = self.planner_llm([{"role": "user", "content": prompt}], json_mode=True)
        steps = []
        for i, s in enumerate(_json(msg.get("content", "")).get("steps", [])[:10], 1):
            check = s.get("check") if isinstance(s.get("check"), dict) else {}
            if not (check.get("cmd") or check.get("file") or check.get("judge") or check.get("evidence")):
                check = {"cmd": "true"}  # unverifiable step: the watcher can only trust the executor here
            if self.kind == "research" and check.get("cmd", "").strip() in ("true", ":", ""):
                check = {"evidence": "answer.json"}  # research: facts are checked against their pages, never trusted
            steps.append(Step(str(s.get("id") or f"s{i}"), str(s.get("do", "")), check, list(s.get("uses") or []),
                              str(s.get("kind") or "build")))
        self._log(event="plan", steps=[s.__dict__ for s in steps], extra=extra[:300], lessons=lessons[:600])
        return steps

    def check(self, step: Step) -> tuple[bool, str]:
        if "evidence" in step.check:
            return self.check_evidence(step.check["evidence"])
        if "judge" in step.check:
            if not self.judge:
                return False, "a judge check, but this run has no judge: use a command check"
            files = "".join(f"--- {f}\n{self.box.read(f)[:1500]}\n" for f in dict.fromkeys(self.step_writes)
                            if (self.box.root / f).exists())
            r = self.judge.ask("step", step.check["judge"], f"GOAL: {self.goal}\nSTEP: {step.do}\n"
                                                             f"DOER'S CLAIM (not evidence: judge it against the files "
                                                             f"below): {step.output}\n{files}")
            ok = r["p"] >= self.JUDGE_PASS
            return ok, f"judge says p={r['p']:.2f} (needs {self.JUDGE_PASS}): {r['reason']}"
        if "file" in step.check:
            ok = (self.box.root / step.check["file"]).exists()
            return ok, "" if ok else f"file {step.check['file']} does not exist"
        # pipefail: "pytest | tail -3" must fail when pytest fails, not report tail's exit code
        code, out = self.box.run("set -o pipefail; " + step.check["cmd"])
        return code == 0, f"check `{step.check['cmd']}` exited {code}:\n{out[-1500:]}"

    def execute(self, step: Step, error: str = "", deep: bool = False, hint: str = "") -> str:
        self.step_writes = []
        uses = "".join(f"OUTPUT OF {u}: {s.output}\n" for u in step.uses for s in self.steps if s.id == u and s.output)
        facts = ""
        if self.memory:
            got = self.memory(f"{self.goal} {step.do}")
            facts = f"FACTS YOU MAY NEED:\n{got}\n" if got else ""
        if self.memrouter:  # lessons for THIS step reach the doer, not only the planner
            try:
                parts = self.memrouter.read(f"{self.goal[:200]} {step.kind}: {step.do}", warnings=False)
                got = self.memrouter.text({**parts, "warnings": self.memory_warnings})
                facts += f"FROM MEMORY:\n{got[:1500]}\n" if got else ""
            except Exception as e:
                self._log(event="memrouter_error", error=repr(e)[:200])
        err = f"LAST ATTEMPT FAILED, fix this:\n{error}\n" if error else ""
        spec = ""
        if self.brownfield:
            spec += (f"THE PROJECT (existing code: extend it, keep every existing name and export):\n"
                     f"{project_map(self.box.root, 2500)}\n")
            if step.kind == "tests":  # new tests must call the code the way the project's own tests do
                helpers = sorted(self.existing_tests | {f.relative_to(self.box.root).as_posix()
                                 for f in self.box.root.glob("**/conftest.py") if not SKIP_DIRS & set(f.parts)})
                spec += "HOW THE PROJECT'S EXISTING TESTS ARE WRITTEN (use the same imports, helpers and calls):\n" + \
                    "".join(f"--- {t}\n{self.box.read(t)[:1800]}\n" for t in helpers[:3])
        contract = self._contract()
        if contract and step.kind != "contract":
            spec += f"THE CONTRACT (SPEC.md): follow it exactly.\n{contract}\n"
        if self.locked and step.kind != "tests":  # the fixed tests ARE the spec: show them, don't hope it reads them
            spec += "THE FIXED TESTS (your code must make these pass; match their imports, names and file usage):\n" + \
                "\n".join(f"--- {f}\n{c[:3500]}" for f, (_, c) in self.locked.items()) + "\n"
        msgs = [{"role": "user", "content": STEP_PROMPT.format(goal=self.goal, constraints=self.constraints, sid=step.id,
                                                                do=step.do, check=json.dumps(step.check),
                                                                uses=uses and uses + "\n", facts=facts, error=err,
                                                                spec=spec, hint=hint and f"WATCHER: {hint}\n")}]
        spec_step = step.kind in ("contract", "tests")
        for _ in range(min(self.max_calls, 6) if deep else self.max_calls):  # thinking calls are slow: fewer
            if self.deadline and time.time() > self.deadline:  # the budget holds inside an attempt too
                return "(time budget used up)"
            if spec_step and self.spec_llm is not None:
                msg = self.spec_llm(msgs, tools=TOOLS)
            elif deep:
                msg = self.llm(msgs, tools=TOOLS, deep=True)
            else:
                msg = self.llm(msgs, tools=TOOLS)
            calls = msg.get("tool_calls") or []
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls} if calls
                        else {"role": "assistant", "content": msg.get("content") or ""})
            if not calls:
                msgs.append({"role": "user", "content": "Use the tools to do the step, then call done."})
                continue
            for c in calls:
                name = c["function"]["name"]
                try:
                    a = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    a = {}
                if name == "dispute_check":
                    result = self.dispute(step, str(a.get("cmd", "")), str(a.get("reason", "")))
                    self._log(event="dispute", step=step.id, cmd=a.get("cmd", ""), reason=a.get("reason", ""),
                              result=result)
                    msgs.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": result})
                    continue
                if name == "done":
                    self._log(event="done_claimed", step=step.id, summary=a.get("summary", ""))
                    return str(a.get("summary", ""))[:800]
                result = self._tool(name, a)
                self._log(event="tool", step=step.id, tool=name, args=json.dumps(a)[:300], result=result[:300])
                msgs.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": result[-3000:]})
        return "(ran out of tool calls)"

    @staticmethod
    def _action(cmd: str) -> str:
        """The program the check exercises: its first command, before any && / | / ;"""
        return re.split(r"&&|\|\||\||;", cmd, maxsplit=1)[0].strip()

    def dispute(self, step: Step, cmd: str, reason: str) -> str:
        """The executor says the check is wrong. Accept a replacement only if it passes now, still runs the same
        program action as the original, and is not a do-nothing command (so it can't be used to skip the work)."""
        self.disputes[step.id] = self.disputes.get(step.id, 0) + 1
        if self.disputes[step.id] > self.max_disputes:
            return ("rejected: dispute limit reached for this step. The check stands: change the work until it "
                    "passes (read the error output carefully).")
        old = step.check.get("was") or step.check.get("cmd", "")
        if not cmd.strip() or re.fullmatch(r"\s*(true|exit 0|:|echo.*)\s*", cmd):
            return "rejected: the replacement check must actually test something"
        files = set(re.findall(r"[\w./-]+\.\w{1,5}\b", old))  # what the original check inspected or ran
        missing = [f for f in files if f not in cmd]
        if missing:
            return f"rejected: the replacement must still check {', '.join(sorted(missing))}"
        multi = re.search(r"&&|\|\||;", old)
        if multi and self._action(old) not in cmd:  # "run the program && inspect": keep running the program
            return f"rejected: the replacement must still run `{self._action(old)}`"
        code, out = self.box.run("set -o pipefail; " + cmd)
        if code != 0:
            return f"rejected: the replacement check fails too (exit {code}):\n{out[-800:]}"
        self.fixed_checks[json.dumps(step.check, sort_keys=True)] = {"cmd": cmd}
        step.check = {"cmd": cmd}
        return "accepted: the step's check is now your command. Call done if the step is finished."

    def _tool(self, name: str, a: dict) -> str:
        try:
            if name == "run":
                cmd = str(a.get("cmd", ""))
                code, out = self.box.run(cmd)
                note = ""
                if code != 0:
                    key = cmd.strip()[:120]
                    self.failed_cmds[key] = self.failed_cmds.get(key, 0) + 1
                    if self.failed_cmds[key] >= 3:  # the stall idea: the same failing attempt again won't work
                        note = "\nWATCHER: this exact command has now failed 3 times. Try a different approach."
                return f"exit {code}\n{out[-2500:]}{note}"
            if name == "write_file":
                path = str(a["path"]).lstrip("./")
                self.step_writes.append(path)
                if path in self.existing_tests:
                    return f"refused: {path} is one of the project's existing tests. They must keep passing unchanged."
                lost = self._names_lost(path, str(a.get("content", "")))
                if lost:
                    return lost
                if path in self.locked and (self.current is None or self.current.kind != "tests"):
                    return (f"refused: {path} holds the fixed tests (the spec). Change the code so the tests pass; "
                            "do not edit the tests.")
                return self.box.write(str(a["path"]), str(a.get("content", "")))
            if name == "read_file":
                return self.box.read(str(a["path"]))
            if name == "fetch":
                return self.fetch_text(str(a.get("url", "")), str(a.get("find", "")))
            if name == "edit_file":
                path = str(a.get("path", "")).lstrip("./")
                if not (self.box.root / path).exists():
                    return f"error: {path} does not exist (use write_file for a new file)"
                text, old = self.box.read_all(path), str(a.get("old", ""))
                n = text.count(old) if old else 0
                if n != 1:
                    return (f"error: `old` appears {n} times in {path}; copy an exact passage that appears once "
                            "(read_file shows the file)")
                return self._tool("write_file", {"path": path, "content": text.replace(old, str(a.get("new", "")), 1)})
            return f"unknown tool {name}"
        except Exception as e:  # a bad tool call is feedback for the model, never a crash
            return f"error: {e}"

    def review_spec(self, failure: str = "") -> list[str]:
        """The judge's first job: are the tests a possible spec for the goal? Returns the problems ([] = fine).
        Locked tests stop cheating, but must not freeze an impossible spec (a test reading a temp file the program
        is never told about stalled every run on 2026-10-16)."""
        files = self._test_files()
        if not files:
            return []
        tests = "\n".join(f"--- {f}\n{self.box.read(f)[:5000]}" for f in files)
        fail = f"THIS TEST KEEPS FAILING against the current code:\n{failure[-1500:]}\n" if failure else ""
        contract = self._contract()
        contract = f"THE CONTRACT (SPEC.md) both tests and code must follow:\n{contract}\n" if contract else ""
        try:
            msg = self.llm([{"role": "user", "content": SPEC_PROMPT.format(goal=self.goal, tests=tests, failure=fail,
                                                                           contract=contract)}],
                           json_mode=True, deep=True)
            verdict = _json(msg.get("content", ""))
        except Exception as e:  # a failed review never blocks the work
            self._log(event="spec_review_error", error=repr(e)[:200])
            return []
        problems = [] if verdict.get("possible", True) else [str(p)[:300] for p in verdict.get("problems") or []][:6]
        if not verdict.get("possible", True) and not problems:
            problems = []  # "impossible" with no concrete blocker named is not acted on: blockers must be specific
        self._log(event="spec_review", possible=not problems, problems=problems, during_build=bool(failure),
                  style_notes=[str(n)[:200] for n in verdict.get("style_notes") or []][:4])
        return problems

    def _contract(self) -> str:
        p = self.box.root / "SPEC.md"
        return p.read_text()[:3000] if p.exists() else ""

    def _redo_tests(self, problems: list[str]) -> bool:
        """Unlock, rewrite the tests (thinking mode: the plain model already failed to write them) with the judge's
        problems as the error, check, review once more. Re-lock only a spec judged possible."""
        tstep = next((s for s in self.steps if s.kind == "tests"), None)
        if not tstep:
            return False
        self.locked = {}
        err = "THE TESTS WERE JUDGED IMPOSSIBLE TO PASS. Rewrite them so a correct implementation of the goal can " \
              "pass:\n- " + "\n- ".join(problems)
        self.current = tstep
        tstep.output = self.execute(tstep, err, deep=True)
        ok, _ = self.check(tstep)
        if ok and not self.review_spec():
            self._lock_tests()
            return True
        return False  # still impossible: nothing is locked, the watcher stops and asks rather than build on it

    def fetch_text(self, url: str, find: str = "") -> str:
        """A page as the evidence check will see it (fetched from the sandbox, tags stripped): the whole text
        (cut at 6000 characters), or with `find` the passages around each match."""
        import shlex
        if not url.startswith(("http://", "https://")):
            return "error: give a full http(s) url"
        code, out = self.box.run(f"curl -sL --compressed --max-time 20 -A 'Mozilla/5.0' {shlex.quote(url)} | head -c 3000000",
                                 timeout=40)
        if code != 0 or not out.strip():
            return f"error: could not fetch {url} (exit {code})"
        text = page_text(out)
        if not find:
            return text[:6000] + (f"\n... ({len(text)} characters in all; use find to search)" if len(text) > 6000 else "")
        hits = [m.start() for m in re.finditer(re.escape(find.lower()), text.lower())][:6]
        if not hits:
            return f"'{find}' is not on {url} ({len(text)} characters of text)"
        return "\n---\n".join(text[max(0, h - 200):h + len(find) + 200] for h in hits)

    def check_evidence(self, path: str) -> tuple[bool, str]:
        """Research done-check: every claim's url is fetched from the sandbox and its quote must be on the page. A
        model can't pass it with invented facts or links."""
        import shlex

        f = self.box.root / path
        try:
            claims = json.loads(f.read_text()).get("claims") or []
        except (OSError, ValueError, AttributeError) as e:
            return False, f"{path} is missing or not JSON with a claims list: {e}"
        if not claims:
            return False, f"{path} has no claims: every fact needs a url and an exact quote"
        norm = lambda t: page_text(t).lower()  # noqa: E731  (the same text the fetch tool shows)
        bad, pages = [], {}
        for c in claims[:20]:
            url, quote = str(c.get("url", "")), norm(str(c.get("quote", "")))
            if not url.startswith(("http://", "https://")) or len(quote) < 8:
                bad.append(f"{c.get('claim', '?')[:80]}: needs an http(s) url and a quote of 8+ characters")
                continue
            if url not in pages:
                code, out = self.box.run(f"curl -sL --compressed --max-time 20 -A 'Mozilla/5.0' {shlex.quote(url)} | head -c 3000000",
                                         timeout=40)
                pages[url] = norm(out) if code == 0 else ""
            if not pages[url]:
                bad.append(f"{url}: could not be fetched")
            elif quote not in pages[url]:
                bad.append(f"{c.get('claim', '?')[:80]}: the quote is not on {url}")
        if bad:
            return False, "EVIDENCE CHECK FAILED (fix or drop these claims):\n- " + "\n- ".join(bad[:8])
        return True, f"all {len(claims[:20])} claims found on their pages"

    def _names_lost(self, path: str, content: str) -> str:
        """Brownfield guard: a write may not drop an existing module's public names, nor add a module that shadows a
        package (`rentals.py` next to `rentals/`): the 2026-10-18 smoke run's failure."""
        if not self.brownfield or not path.endswith(".py"):
            return ""
        if path in self.base_names:
            gone = self.base_names[path] - public_names(content, path.endswith("__init__.py"))
            if gone:
                return (f"refused: this rewrite of {path} removes existing names: {', '.join(sorted(gone))}. Extend the "
                        "module: keep everything that is there and add to it.")
        elif (self.box.root / path[:-3] / "__init__.py").exists():
            return f"refused: {path} would shadow the existing package {path[:-3]}/. Add the code inside the package."
        return ""

    def _brownfield_guard(self) -> str:
        """After a build or verify step: existing names still there (shell commands can edit files too) and the
        project's existing tests still passing. Returns what is wrong, or ""."""
        for path, names in self.base_names.items():
            f = self.box.root / path
            gone = names - (public_names(f.read_text(), path.endswith("__init__.py")) if f.exists() else set())
            if gone:
                return f"WATCHER: {path} lost existing names: {', '.join(sorted(gone))}. Restore them; extend, don't replace."
        if self.passing_before:
            now, out = self._passing_tests()
            broken = sorted(self.passing_before - now)
            if broken:
                return (f"WATCHER: these tests passed before this job and now fail (a regression: earlier work broke):"
                        f"\n- " + "\n- ".join(broken[:10]) + f"\n{out[-1200:]}")
        return ""

    def _passing_tests(self) -> tuple[set[str], str]:
        """Which of the project's existing tests pass now, test by test (one failing ticket must not switch the
        regression guard off for the rest: the 2026-10-19 rental run lost ticket 1's search that way)."""
        code, out = self.box.run(f"{self.PYTEST} -q -rA -p no:cacheprovider " + " ".join(sorted(self.existing_tests)))
        return set(re.findall(r"^PASSED (\S+)", out, re.M)), out

    def _start(self):
        """Look at the workspace before planning: an existing project (code already there) is extended, never rebuilt."""
        root = self.box.root
        if self.kind == "research":
            return
        code = [f for f in _py_files(root) if not _is_test(f.relative_to(root).as_posix())]
        self.brownfield = bool(code)
        if not self.brownfield:
            return
        self.base_names = {f.relative_to(root).as_posix(): public_names(f.read_text(), f.name == "__init__.py")
                           for f in code}
        self.existing_tests = {f.relative_to(root).as_posix() for f in _py_files(root)
                               if _is_test(f.relative_to(root).as_posix()) and f.name != "conftest.py"}
        if self.existing_tests:  # the watcher's own tool: without it the starting state can't be measured
            self.box.run(f"{self.PYTEST} --version >/dev/null 2>&1 || pip install -q pytest", timeout=300)
        if self.existing_tests:
            self.passing_before = self._passing_tests()[0]
            self.existing_ok = bool(self.passing_before)
        for t in self.existing_tests:  # fixed from the start: restored if a shell command edits them
            content = (root / t).read_text()
            self.locked[t] = (hashlib.sha256(content.encode()).hexdigest(), content)
        self._log(event="brownfield", modules=sorted(self.base_names), tests=sorted(self.existing_tests),
                  passing=len(self.passing_before))

    def _all_locked_pass(self) -> bool:
        """The fixed tests are the spec: when all of them pass, the job is done, whatever steps the plan has left
        (benchmark runs: 31 jobs ran into the time budget with the work already passing)."""
        files = [f for f in self.locked if not f.endswith("conftest.py")]
        if not files:
            return False
        self._restore_tests()
        code, _ = self.box.run(f"{self.PYTEST} -q -p no:cacheprovider " + " ".join(files))
        return code == 0

    def _test_files(self) -> list[str]:
        code, out = self.box.run("ls -1 test_*.py *_test.py tests/*.py 2>/dev/null")
        return [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".py")]

    def _lock_tests(self):
        for f in self._test_files():
            content = self.box.read(f) if (self.box.root / f).exists() else ""
            self.locked[f] = (hashlib.sha256(content.encode()).hexdigest(), content)
        if self.locked:
            self._log(event="tests_locked", files=sorted(self.locked))

    def _restore_tests(self) -> list[str]:
        """Shell commands can edit files too: anything changed in a locked test file is put back."""
        changed = []
        for f, (digest, content) in self.locked.items():
            p = self.box.root / f
            now = p.read_text() if p.exists() else None
            if now is None or hashlib.sha256(now.encode()).hexdigest() != digest:
                self.box.write(f, content)
                changed.append(f)
        if changed:
            self._log(event="tests_restored", files=changed)
        return changed

    def decide(self, step: Step, history: list[dict], replans: int, max_replans: int) -> tuple[str, str]:
        """What to do after a failed check, from measured signals (not a model's opinion):
        environment failure -> fix it; tests passing going up -> keep going; no progress -> escalate once (same
        model, thinking mode, full spec); still stuck -> re-plan; out of re-plans -> stop and ask the owner."""
        last = history[-1]
        tried = {h.get("option") for h in history}  # options already used on this step, the last attempt included
        if last["failure"] == "environment" and "fix_environment" not in tried:
            return "fix_environment", "the environment is missing something (package or tool)"
        if len(history) >= 2 and last["passed"] is not None and history[-2]["passed"] is not None \
                and last["passed"] > history[-2]["passed"] and len(history) < 5:
            return "continue", f"progress: {history[-2]['passed']} -> {last['passed']} tests passing"
        code_error = re.search(r"\b(NameError|ImportError|ModuleNotFoundError|AttributeError|SyntaxError|"
                               r"IndentationError|TypeError|KeyError)\b", last["error"] or "")
        if code_error and not tried & {"quick_fix", "escalate"}:  # a plain bug: a fast fix, not minutes of thinking
            return "quick_fix", f"a {code_error[1]}: fix that exact line"
        stuck = False
        try:
            from stall import Run, stuck as is_stuck
            runs = [Run(h["passed"] or 0, h["failed"] or 0, h["failing"] or None) for h in history if h["failed"]]
            stuck = bool(is_stuck(runs, 2))
        except ImportError:
            stuck = len(history) >= 2 and history[-1]["error"][-300:] == history[-2]["error"][-300:]
        if "escalate" not in tried:
            return "escalate", ("the same tests keep failing with no progress" if stuck else "the step failed")
        if self.locked and step.kind != "tests" and "revise_spec" not in tried and last["failed"]:
            return "revise_spec", "still failing after escalation: is the fixed spec itself possible?"
        if replans < max_replans:
            return "replan", "still failing after escalation"
        return "stop", "out of re-plans: needs the owner"

    def _explore(self, option: str, why: str, step: Step, history: list[dict], replans: int, max_replans: int):
        """Benchmarks only (--explore): with probability `explore` the watcher takes a random allowed action instead
        of its rule's choice, and logs both with the pick's probability. Without this the logs only show what the
        rules chose, and the world model (level 2) could never learn what the other actions would have done."""
        if not self.explore or option in ("stop", "fix_environment"):
            return option, why, {}
        allowed = ["escalate"] + (["continue"] if len(history) < 5 else []) \
            + (["revise_spec"] if self.locked and step.kind != "tests" and history[-1]["failed"] else []) \
            + (["replan"] if replans < max_replans else [])
        if len(allowed) < 2:
            return option, why, {}
        rule = {"rule_option": option, "allowed": allowed}
        if self.rng.random() >= self.explore:
            return option, why, {**rule, "explored": False, "p_pick": 1 - self.explore + self.explore / len(allowed)}
        pick = self.rng.choice(allowed)
        p_pick = self.explore / len(allowed) + (1 - self.explore if pick == option else 0)
        return pick, f"exploring (rule said {option}: {why})", {**rule, "explored": True, "p_pick": round(p_pick, 4)}

    def run(self, max_replans: int = 2) -> dict:
        self._start()
        self.steps = self.plan()
        replans, i, evidence_rounds = 0, 0, 0
        while True:
            if i >= len(self.steps):  # the plan is done; a research job also needs every claim verified on its page
                if self.kind != "research":
                    break
                ok_e, why_e = self.check_evidence("answer.json")
                if ok_e:
                    break
                if evidence_rounds >= 2:
                    return self.report(stopped=self.steps[-1] if self.steps else None,
                                       reason=f"answer.json never passed the evidence check: {why_e}")
                evidence_rounds += 1
                self.steps.append(Step(f"e{evidence_rounds}", "Fix answer.json so every fact has a claim whose url opens "
                                       "and whose quote is copied exactly from that page (fetch the pages; drop facts you "
                                       f"cannot source):\n{why_e[:800]}", {"evidence": "answer.json"}, kind="verify"))
                self._log(event="evidence_gate", round=evidence_rounds, why=why_e[:500])
            step, error, history, option, hint = self.steps[i], "", [], "first try", ""
            while True:
                if self.deadline and time.time() > self.deadline:  # a time budget: one stuck job can't eat the run
                    step.status = "failed"
                    self._log(event="stopped", step=step.id, reason="time budget")
                    return self.report(stopped=step, reason="time budget used up")
                self.current = step
                step.output = self.execute(step, error, deep=option in ("escalate", "spec_fix_deep"), hint=hint)
                if step.kind != "tests" and self._restore_tests():
                    error = ("WATCHER: you changed the fixed test files; they were restored. Change the code, not "
                             "the tests.\n") + (error or "")
                ok, error = self.check(step)
                doomed = self._doomed()
                if doomed:
                    step.status = "failed"
                    self._log(event="stopped", step=step.id, reason=doomed)
                    return self.report(stopped=step, reason=doomed)
                if ok and self.brownfield and step.kind in ("build", "verify"):
                    guard = self._brownfield_guard()
                    ok, error = (False, guard) if guard else (ok, error)
                counts = test_counts(error)
                self._log(event="check", step=step.id, attempt=len(history), ok=ok, option=option, detail=error[-500:])
                history.append({"option": option, "ok": ok, "error": error, "failure": "" if ok else classify_failure(error),
                                "passed": counts[0] if counts else None, "failed": counts[1] if counts else None,
                                "failing": counts[2] if counts else ()})
                if ok:
                    step.status = "done"
                    if step.kind == "tests":
                        self._lock_tests()
                    break
                option, why = self.decide(step, history, replans, max_replans)
                option, why, explored = self._explore(option, why, step, history, replans, max_replans)
                self.decisions.append({"step": step.id, "option": option, "why": why, **explored})
                self._log(event="decision", step=step.id, option=option, why=why, **explored)
                if option == "revise_spec":
                    problems = self.review_spec(error)
                    if problems and self._redo_tests(problems):
                        hint = "The tests were rewritten (the old ones were impossible). Make the code pass the new tests."
                        continue
                    if problems:  # the spec is impossible and could not be fixed: don't build on it, ask
                        option, why = "stop", "the fixed tests are impossible and could not be rewritten: needs the owner"
                        error = error + "\nSPEC PROBLEMS:\n- " + "\n- ".join(problems)
                    else:
                        option, why = ("replan", "spec judged possible: the code must change") if replans < max_replans \
                            else ("stop", "out of re-plans: needs the owner")
                    self._log(event="decision", step=step.id, option=option, why=why)
                hint = {"fix_environment": "First install or fix what is missing (see the error), then redo the step.",
                        "continue": f"You are making progress ({why}). Keep going on the remaining failures.",
                        "quick_fix": "The error names the exact problem (see LAST ATTEMPT FAILED). Fix just that, "
                                     "with edit_file, and rerun the check.",
                        "escalate": "Think carefully: read the fixed tests and the full error, find the root cause, "
                                    "then fix it in one go."}.get(option, "")
                if option in ("replan", "stop"):
                    break
            if step.status == "done":
                i += 1
                wrote_tests = any(s_.kind == "tests" and s_.status == "done" for s_ in self.steps)
                if i < len(self.steps) and step.kind in ("build", "verify") and wrote_tests and self._all_locked_pass():
                    for rest in self.steps[i:]:
                        rest.status = "skipped"
                    self._log(event="early_finish", after=step.id, skipped=[s_.id for s_ in self.steps[i:]])
                    return self.report()
                continue
            if option == "stop":
                step.status = "failed"
                self._log(event="stopped", step=step.id, reason=error[-500:])
                return self.report(stopped=step, reason=f"{why}. Last error:\n{error}")
            replans += 1
            done = [s for s in self.steps[:i] if s.status == "done"]
            files = self.box.run("ls -la; for f in $(ls | head -8); do [ -f \"$f\" ] && echo \"--- $f\" && head -40 \"$f\"; done")[1]
            extra = ("ALREADY DONE (keep these, do not redo):\n" + "\n".join(f"- {s.id}: {s.do} -> {s.output}" for s in done)
                     + f"\nTHIS STEP KEPT FAILING: {step.do}\nITS CHECK: {json.dumps(step.check)}\nERROR:\n{error[-1200:]}"
                     + f"\nFILES NOW:\n{files[-2500:]}\nThe CHECK itself may be wrong (not self-contained, testing something no "
                       "step produces): if so, write a correct check. Plan the REMAINING work differently.")
            new = self.plan(extra)
            for s_ in new:  # a re-plan may not bring back a check that just failed: use a verified fix, or rewrite it
                key = json.dumps(s_.check, sort_keys=True)
                if key in self.fixed_checks:
                    s_.check = self.fixed_checks[key]
                elif s_.check.get("cmd") and s_.check.get("cmd") == step.check.get("cmd"):
                    # blocked until the executor proves a correct replacement with dispute_check
                    s_.check = {"cmd": "false", "was": s_.check["cmd"],
                                "note": "this check failed twice; prove the work, then dispute_check with a correct one"}
            self.steps = done + [s for s in new if s.id not in {d.id for d in done}]
            i = len(done)
        return self.report()

    def run_plain(self, max_calls: int = 60) -> dict:
        """The baseline: the same model and tools with no plan, contract, locked tests or watcher. It works until it
        calls done, runs out of calls or the time budget: what the runner's machinery has to beat."""
        step = self.current = Step("s1", self.goal, {"cmd": "true"})
        self.steps = [step]
        msgs = [{"role": "user", "content": PLAIN_PROMPT.format(goal=self.goal)}]
        for _ in range(max_calls):
            if self.deadline and time.time() > self.deadline:
                return self.report(stopped=step, reason="time budget used up")
            msg = self.llm(msgs, tools=[t for t in TOOLS if t["function"]["name"] != "dispute_check"])
            calls = msg.get("tool_calls") or []
            msgs.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
            if not calls:
                msgs.append({"role": "user", "content": "Use the tools to do the task, then call done."})
                continue
            for c in calls:
                name = c["function"]["name"]
                try:
                    a = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    a = {}
                if name == "done":
                    step.status, step.output = "done", str(a.get("summary", ""))[:800]
                    return self.report()
                result = self._tool(name, a)
                self._log(event="tool", step=step.id, tool=name, args=json.dumps(a)[:300], result=result[:300])
                msgs.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": result[-3000:]})
        return self.report(stopped=step, reason="ran out of tool calls")

    def report(self, stopped: Step | None = None, reason: str = "") -> dict:
        r = {"goal": self.goal, "ok": stopped is None, "steps": [(s.id, s.status, s.do[:80]) for s in self.steps],
             "stopped_at": stopped.id if stopped else None, "reason": reason[-800:], "decisions": self.decisions,
             "usage": dict(USAGE), "warning_id": self.warning_id}
        if self.judge:  # the judge's verdict on the whole job; the scorer labels it with the real outcome
            j = self.judge.ask("job_done", JOB_DONE, job_evidence(self.goal, r, self._events("check"),
                                                                  self.box.run("ls -R | head -60")[1]))
            r.update(judge_id=j["id"], judge_p=j["p"])
        if stopped:  # out of options: the owner decides (the gateway / CLI shows this)
            r["ask_owner"] = f"Stuck at {stopped.id} ({stopped.do[:120]}): {reason[:300]}"
        self._log(event="report", **r)
        return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("goal")
    ap.add_argument("--model-url", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--local", action="store_true", help="no container (trusted use only)")
    ap.add_argument("--memory-url", default="", help="memrouter service: rules, warnings and what the owner said")
    ap.add_argument("--scope", default="", help="experience scope (a project or task set) for failure warnings")
    ap.add_argument("--rules-only", action="store_true", help="from memory, only the owner's standing rules")
    ap.add_argument("--model", default="local", help="the worker model's name at --model-url")
    ap.add_argument("--api-key-env", default="", help="environment variable holding --model-url's API key")
    for role in ("planner", "spec", "judge"):
        ap.add_argument(f"--{role}", default="", help=f"own model for the {role}: URL,MODEL[,API_KEY_ENV]")
    ap.add_argument("--max-minutes", type=float, default=None, help="stop the job after this long")
    ap.add_argument("--plain", action="store_true", help="baseline: no plan, contract, locked tests or watcher")
    ap.add_argument("--kind", choices=["code", "research"], default="code")
    ap.add_argument("--explore", type=float, default=0.0,
                    help="benchmarks only: chance the watcher takes a random allowed action (world-model data)")
    ap.add_argument("--forecast", default="", help="world model (runner/forecast.py train); used only if its gate passed")
    ap.add_argument("--judge-db", default="", help="the judge's log (runner/judge.py): judge checks and a verdict per job")
    args = ap.parse_args()
    import signal

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # stopped from outside: still clean up the sandbox
    wd = Path(args.workdir)
    box = LocalSandbox(wd) if args.local else PodmanSandbox(wd)
    memrouter = Memrouter(args.memory_url, args.scope, args.rules_only) if args.memory_url else None
    try:
        llm = endpoint(",".join([args.model_url, args.model] + ([args.api_key_env] if args.api_key_env else [])))
        judge = None
        if args.judge_db:
            from judge import Judge
            judge = Judge(endpoint(args.judge) if args.judge else llm, args.judge_db)
        forecast = None
        if args.forecast:
            from forecast import load
            forecast = load(args.forecast)
        runner = Runner(args.goal, box, llm, wd / "run.jsonl", memrouter=memrouter, forecast=forecast,
                        spec_llm=endpoint(args.spec) if args.spec else None, max_minutes=args.max_minutes,
                        judge=judge, kind=args.kind, planner_llm=endpoint(args.planner) if args.planner else None,
                        explore=args.explore)
        r = runner.run_plain() if args.plain else runner.run()
    finally:
        box.close()
    print(json.dumps(r, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

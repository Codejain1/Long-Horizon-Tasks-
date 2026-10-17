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
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PLAN_PROMPT = """You plan work for a careful but small assistant that does ONE step at a time in a Linux sandbox
(Python 3.11, Node 20, bash, internet). Break the goal into 3-10 small steps.

Every step needs a check that a program can run to prove it is done, one of:
  {{"cmd": "<shell command that exits 0 only when the step is done>"}}   (preferred: tests, python -c asserts, grep)
  {{"file": "<path that must exist>"}}
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
     package the way the existing tests do; checked with "python -m pytest --collect-only -q";
  2. "build" steps adding the new code inside the existing modules (and exporting it where the goal says), each
     checked by running the new test file (python -m pytest -q <new test file>);
  3. a final "verify" step running the project's own test command (./ci.sh if there is one, else pytest)."""

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
Before calling done, run that check yourself (with the run tool) and keep fixing until it passes. Read error output
carefully and change your approach if the same error repeats.
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
            p = subprocess.run(["bash", "-lc", cmd], cwd=self.root, capture_output=True, text=True, timeout=timeout)
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
                               text=True, timeout=timeout)
            return p.returncode, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired as e:
            return 124, f"timed out after {timeout}s\n{(e.stdout or '')}"

    def close(self):
        subprocess.run(["podman", "rm", "-f", self.name], capture_output=True)


# ---------------------------------------------------------------- model
def chat(url: str, messages: list, tools=None, json_mode=False, model="local", timeout=600, deep=False) -> dict:
    """deep: the same model thinking before it answers (Qwen3 thinking mode): the escalation for a stuck step,
    at no extra cost beyond time."""
    body = {"model": model, "messages": messages, "temperature": 0.6 if deep else 0.2,
            "max_tokens": 6000 if deep else 4000, "chat_template_kwargs": {"enable_thinking": deep}}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    u = out.get("usage") or {}
    USAGE["calls"] += 1
    USAGE["prompt_tokens"] += u.get("prompt_tokens", 0)
    USAGE["completion_tokens"] += u.get("completion_tokens", 0)
    return out["choices"][0]["message"]


USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}  # this process = one job: what the model really did


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m[0]) if m else {}


# ---------------------------------------------------------------- memory (optional)
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

    def __init__(self, goal: str, sandbox, llm, log: Path, constraints: str = "", memory=None, max_calls: int = 12,
                 memrouter: Memrouter | None = None, spec_llm=None,
                 max_minutes: float | None = None):
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
        self.brownfield, self.base_names, self.existing_tests, self.existing_ok = False, {}, set(), False
        self.decisions: list[dict] = []

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
        prompt = PLAN_PROMPT.format(goal=self.goal, extra=extra, lessons=lessons, project=project,
                                    order=ORDER_EXISTING if self.brownfield else ORDER_NEW)
        msg = self.llm([{"role": "user", "content": prompt}], json_mode=True)
        steps = []
        for i, s in enumerate(_json(msg.get("content", "")).get("steps", [])[:10], 1):
            check = s.get("check") if isinstance(s.get("check"), dict) else {}
            if not (check.get("cmd") or check.get("file")):
                check = {"cmd": "true"}  # unverifiable step: the watcher can only trust the executor here
            steps.append(Step(str(s.get("id") or f"s{i}"), str(s.get("do", "")), check, list(s.get("uses") or []),
                              str(s.get("kind") or "build")))
        self._log(event="plan", steps=[s.__dict__ for s in steps], extra=extra[:300], lessons=lessons[:600])
        return steps

    def check(self, step: Step) -> tuple[bool, str]:
        if "file" in step.check:
            ok = (self.box.root / step.check["file"]).exists()
            return ok, "" if ok else f"file {step.check['file']} does not exist"
        # pipefail: "pytest | tail -3" must fail when pytest fails, not report tail's exit code
        code, out = self.box.run("set -o pipefail; " + step.check["cmd"])
        return code == 0, f"check `{step.check['cmd']}` exited {code}:\n{out[-1500:]}"

    def execute(self, step: Step, error: str = "", deep: bool = False, hint: str = "") -> str:
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
        if self.existing_ok:
            code, out = self.box.run(f"{self.PYTEST} -q -p no:cacheprovider " + " ".join(sorted(self.existing_tests)))
            if code != 0:
                return f"WATCHER: the project's existing tests now fail (they passed before this job):\n{out[-1200:]}"
        return ""

    def _start(self):
        """Look at the workspace before planning: an existing project (code already there) is extended, never rebuilt."""
        root = self.box.root
        code = [f for f in _py_files(root) if not _is_test(f.relative_to(root).as_posix())]
        self.brownfield = bool(code)
        if not self.brownfield:
            return
        self.base_names = {f.relative_to(root).as_posix(): public_names(f.read_text(), f.name == "__init__.py")
                           for f in code}
        self.existing_tests = {f.relative_to(root).as_posix() for f in _py_files(root)
                               if _is_test(f.relative_to(root).as_posix()) and f.name != "conftest.py"}
        if self.existing_tests:
            self.existing_ok = self.box.run(f"{self.PYTEST} -q -p no:cacheprovider "
                                            + " ".join(sorted(self.existing_tests)))[0] == 0
        for t in self.existing_tests:  # fixed from the start: restored if a shell command edits them
            content = (root / t).read_text()
            self.locked[t] = (hashlib.sha256(content.encode()).hexdigest(), content)
        self._log(event="brownfield", modules=sorted(self.base_names), tests=sorted(self.existing_tests),
                  tests_pass=self.existing_ok)

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

    def run(self, max_replans: int = 2) -> dict:
        self._start()
        self.steps = self.plan()
        replans, i = 0, 0
        while i < len(self.steps):
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
                self.decisions.append({"step": step.id, "option": option, "why": why})
                self._log(event="decision", step=step.id, option=option, why=why)
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
    ap.add_argument("--spec-model-url", default="", help="a bigger model for the contract and tests steps")
    ap.add_argument("--spec-model", default="local")
    ap.add_argument("--max-minutes", type=float, default=None, help="stop the job after this long")
    ap.add_argument("--plain", action="store_true", help="baseline: no plan, contract, locked tests or watcher")
    args = ap.parse_args()
    import signal

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # stopped from outside: still clean up the sandbox
    wd = Path(args.workdir)
    box = LocalSandbox(wd) if args.local else PodmanSandbox(wd)
    memrouter = Memrouter(args.memory_url, args.scope, args.rules_only) if args.memory_url else None
    try:
        spec_llm = (lambda m, **k: chat(args.spec_model_url, m, model=args.spec_model, **k)) if args.spec_model_url \
            else None
        runner = Runner(args.goal, box, lambda m, **k: chat(args.model_url, m, **k), wd / "run.jsonl",
                        memrouter=memrouter, spec_llm=spec_llm, max_minutes=args.max_minutes)
        r = runner.run_plain() if args.plain else runner.run()
    finally:
        box.close()
    print(json.dumps(r, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

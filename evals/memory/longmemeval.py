"""LongMemEval-S with the benchmark's own reader and judge prompts.

    python evals/memory/longmemeval.py --arm horizon --n 60 --out /tmp/lme      (stratified sample; --n 500 = all)
    python evals/memory/longmemeval.py --backend openai --arm horizon --n 500 --out /tmp/lme500

Arms (`arms.py`), all with the same answering model and judge: `horizon`, `hybrid`, `rag`, `full`.
Splits: `dev` is the 60 questions tuned on (seed 7, the first slice run); `test` is the other 440, never tuned on.
Report `test`. `--judge-backend codex` grades with Codex, so the answer model never grades itself.
Data: ~/.cache/horizon-bench/longmemeval/longmemeval_s_cleaned.json (huggingface xiaowu0162/longmemeval-cleaned).

Backends:
- `claude` (default): Claude Code subscription (`--model sonnet`, `--judge sonnet`).
- `openai`: OPENAI_API_KEY; defaults `--model gpt-4o-mini`, `--judge gpt-4o-mini`.
- `gemini`: GEMINI_API_KEY or GOOGLE_API_KEY; defaults `gemini-3.8-flash` (long context for `full`).
- `auto`: OpenAI first, then Gemini on rate limits, then Claude Code if still blocked.
- `cursor`: Cursor Agent CLI (`agent -p`), e.g. `composer-2.5` or `cursor-grok-4.5-high` (uses your Cursor subscription).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

# USD per 1M tokens (input, output) for rough run totals on OpenAI.
_OPENAI_PRICE = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-2024-08-06": (2.50, 10.00),
    "gpt-4o-mini-2024-07-18": (0.15, 0.60),
}

DATA = Path.home() / ".cache" / "horizon-bench" / "longmemeval" / "longmemeval_s_cleaned.json"
_REPO_ENV = Path(__file__).resolve().parents[2] / ".env"


def _load_dotenv() -> None:
    if not _REPO_ENV.is_file():
        return
    for line in _REPO_ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
from arms import ARMS, KEEP, make_judge  # noqa: E402

# The benchmark's reader prompts (src/generation/run_generation.py), step-by-step variants.
READ_CHATS = ("I will give you several history chats between you and a user. Please answer the question based on the "
              "relevant chat history. Answer the question step by step: first extract all the relevant information, "
              "and then reason over the information to get the answer.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}"
              "\nQuestion: {}\nAnswer (step by step):")

# The benchmark's judge prompts (src/evaluation/evaluate_qa.py), verbatim.
_PLAIN = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the response "
          "contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or "
          "contains all the intermediate steps to get the correct answer, you should also answer yes. If the response "
          "only contains a subset of the information required by the answer, answer no. ")
JUDGE = {
    "plain": _PLAIN + "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? "
                      "Answer yes or no only.",
    "temporal-reasoning": _PLAIN + "In addition, do not penalize off-by-one errors for the number of days. If the "
                          "question asks for the number of days/weeks/months, etc., and the model makes off-by-one "
                          "errors (e.g., predicting 19 days when the answer is 18), the model's response is still "
                          "correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model "
                          "response correct? Answer yes or no only.",
    "knowledge-update": "I will give you a question, a correct answer, and a response from a model. Please answer yes "
                        "if the response contains the correct answer. Otherwise, answer no. If the response contains "
                        "some previous information along with an updated answer, the response should be considered as "
                        "correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect "
                        "Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "single-session-preference": "I will give you a question, a rubric for desired personalized response, and a "
                                 "response from a model. Please answer yes if the response satisfies the desired "
                                 "response. Otherwise, answer no. The model does not need to reflect all the points in "
                                 "the rubric. The response is correct as long as it recalls and utilizes the user's "
                                 "personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}"
                                 "\n\nIs the model response correct? Answer yes or no only.",
    "abstention": "I will give you an unanswerable question, an explanation, and a response from a model. Please answer "
                  "yes if the model correctly identifies the question as unanswerable. The model could say that the "
                  "information is incomplete, or some other information is given but the asked information is not."
                  "\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the "
                  "question as unanswerable? Answer yes or no only.",
}


def claude(prompt: str, model: str) -> tuple[str, dict]:
    """One plain completion through Claude Code: no tools, no project settings, nothing persisted."""
    for attempt in range(3):
        proc = subprocess.run(["claude", "-p", "--model", model, "--tools", "", "--setting-sources", "",
                               "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"],
                              input=prompt, capture_output=True, text=True, timeout=900, cwd="/tmp")
        try:
            d = json.loads(proc.stdout)
            if d.get("is_error") or not d.get("result"):
                raise ValueError(str(d.get("result"))[:200])
            return str(d["result"]), {"cost_usd": d.get("total_cost_usd") or 0, **(d.get("usage") or {})}
        except ValueError as exc:
            if "limit" in str(exc).lower():
                raise SystemExit(f"usage limit: {exc}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"claude failed: {proc.stdout[-300:]} {proc.stderr[-300:]}")


def cursor_complete(prompt: str, model: str) -> tuple[str, dict]:
    """Plain completion via Cursor Agent CLI (Composer, Grok, etc.)."""
    for attempt in range(3):
        # Prompt on stdin: full-history prompts (~1 MB) exceed the argument limit; ask mode is read-only.
        proc = subprocess.run(
            ["agent", "-p", "--output-format", "text", "--model", model, "--mode", "ask", "--trust"],
            input=prompt, capture_output=True, text=True, timeout=900, cwd="/tmp")
        text = (proc.stdout or "").strip()
        if proc.returncode == 0 and text:
            return text, {"cost_usd": 0, "llm": "cursor", "cursor_model": model}
        time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"cursor agent failed: {(proc.stderr or proc.stdout)[-400:]}")


def codex_complete(prompt: str, model: str) -> tuple[str, dict]:
    """Plain completion via the Codex CLI on a ChatGPT plan (no API credits). Only the plan's default model is
    served there (2026-10: gpt-5.6-luna); `model` "default" leaves -m out."""
    import tempfile

    with tempfile.NamedTemporaryFile("r", suffix=".txt") as out:
        for attempt in range(3):
            proc = subprocess.run(
                ["codex", "exec", *([] if model in ("", "default") else ["-m", model]), "-s", "read-only",
                 "--skip-git-repo-check", "-o", out.name, "-"],
                input=prompt, capture_output=True, text=True, timeout=900, cwd="/tmp")
            text = Path(out.name).read_text().strip()
            if proc.returncode == 0 and text:
                return text, {"cost_usd": 0, "llm": "codex"}
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"codex failed: {(proc.stderr or proc.stdout)[-400:]}")


def _openai_cost(model: str, usage: dict) -> float:
    base = model.split(":")[0]
    for key, (inp, out) in _OPENAI_PRICE.items():
        if base.startswith(key) or key.startswith(base):
            return (usage.get("prompt_tokens", 0) * inp + usage.get("completion_tokens", 0) * out) / 1_000_000
    return 0.0


def openai_complete(prompt: str, model: str) -> tuple[str, dict]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY not set")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}).encode()
    for attempt in range(6):
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                d = json.loads(resp.read())
            usage = d.get("usage") or {}
            text = d["choices"][0]["message"]["content"] or ""
            return text, {"cost_usd": _openai_cost(model, usage), **usage}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            if exc.code in (429, 500, 502, 503) and attempt < 8:
                wait = min(180, 2 ** attempt * 8)
                if "try again in" in body:
                    import re

                    m = re.search(r"try again in (\d+)m(\d+)s", body)
                    if m:
                        wait = max(wait, int(m.group(1)) * 60 + int(m.group(2)) + 2)
                    m = re.search(r"try again in (\d+)s", body)
                    if m:
                        wait = max(wait, int(m.group(1)) + 2)
                time.sleep(wait)
                continue
            raise RuntimeError(f"openai {exc.code}: {body[:300]}") from exc
    raise RuntimeError("openai failed after retries")


def _gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get(
        "GOOGLE_GENERATIVE_AI_API_KEY")


class QuotaExhausted(RuntimeError):
    """Daily or plan quota exhausted (not a short RPM bump)."""


def _rate_limited(exc: BaseException) -> bool:
    s = str(exc).lower()
    return "429" in s or "rate limit" in s or "quota" in s or "resource_exhausted" in s


def _gemini_quota_exhausted(body: str) -> bool:
    b = body.lower()
    return "exceeded your current quota" in b or "quota exceeded for metric" in b


def gemini_complete(prompt: str, model: str) -> tuple[str, dict]:
    key = _gemini_key()
    if not key:
        raise SystemExit("GEMINI_API_KEY or GOOGLE_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0}}).encode()
    for attempt in range(8):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                d = json.loads(resp.read())
            parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            meta = d.get("usageMetadata") or {}
            usage = {"prompt_tokens": meta.get("promptTokenCount", 0),
                     "completion_tokens": meta.get("candidatesTokenCount", 0), "llm": "gemini"}
            return text, {**usage, "cost_usd": 0.0}
        except urllib.error.HTTPError as exc:
            body_b = exc.read().decode(errors="replace")
            if exc.code == 429 and _gemini_quota_exhausted(body_b):
                if attempt < 2:
                    time.sleep(90 * (attempt + 1))
                    continue
                raise QuotaExhausted("Gemini daily/plan quota exhausted. Enable billing or retry after reset "
                                     "(https://ai.dev/rate-limit).")
            if exc.code in (429, 500, 502, 503) and attempt < 7:
                time.sleep(min(120, 2 ** attempt * 6))
                continue
            raise RuntimeError(f"gemini {exc.code}: {body_b[:300]}") from exc
    raise RuntimeError("gemini failed after retries")


def auto_complete(prompt: str, model: str) -> tuple[str, dict]:
    """Prefer OpenAI; fall back to Gemini, then Claude Code on limits."""
    gmodel = os.environ.get("HORIZON_GEMINI_MODEL", "gemini-3.8-flash")
    if os.environ.get("OPENAI_API_KEY"):
        try:
            out, usage = openai_complete(prompt, model)
            usage["llm"] = "openai"
            return out, usage
        except (RuntimeError, SystemExit) as exc:
            if not _rate_limited(exc):
                raise
    if _gemini_key():
        try:
            out, usage = gemini_complete(prompt, gmodel)
            return out, usage
        except QuotaExhausted:
            pass
        except RuntimeError as exc:
            if not _rate_limited(exc):
                raise
    out, usage = claude(prompt, os.environ.get("HORIZON_CLAUDE_MODEL", "sonnet"))
    usage["llm"] = "claude"
    return out, usage


def sample(data: list[dict], n: int, seed: int = 7) -> list[dict]:
    """Stratified by question type (abstention questions spread with their type), stable across arms."""
    if n >= len(data):
        return data
    by = defaultdict(list)
    for q in data:
        by[q["question_type"]].append(q)
    rng = random.Random(seed)
    out = []
    for kind, qs in sorted(by.items()):
        rng.shuffle(qs)
        out += qs[:max(1, round(n * len(qs) / len(data)))]
    return out[:n]


def history_chats(sessions: list[tuple[str, list[dict]]]) -> str:
    """The benchmark's session format: a header with the date, then the turns as JSON."""
    return "\n\n".join(f"### Session {i}:\nSession Date: {date}\nSession Content:\n{json.dumps(turns)}"
                       for i, (date, turns) in enumerate(sessions, 1))


def split(data: list[dict], name: str) -> list[dict]:
    """`dev`: the 60 questions the counting rule was tuned on; `test`: the rest; `all`: every question."""
    if name == "all":
        return data
    dev = {q["question_id"] for q in sample(data, 60)}
    return [q for q in data if (q["question_id"] in dev) == (name == "dev")]


def build_context(q: dict, arm: str, embedder, scorer) -> tuple[str, dict]:
    sessions = list(zip(q["haystack_dates"], q["haystack_sessions"]))
    if arm == "full":
        return history_chats(sessions), {"units": sum(len(s) for _, s in sessions)}
    from horizon.factmem import FactMemory

    mem = FactMemory(embedder)
    for sid, (date, turns) in zip(q["haystack_session_ids"], sessions):
        mem.add_session(sid, date, turns)
    t = time.monotonic()
    hits = mem.search(q["question"], scorer, date=q["question_date"], keep=KEEP)
    info = {"units": len(mem.units), "kept": len(hits), "retrieval_s": round(time.monotonic() - t, 2),
            "top_relevance": max((h.relevance for h in hits), default=0.0)}
    chats = "\n\n".join(f"### Memory {i} (Session Date: {h.unit.date}):\n{h.unit.text}" for i, h in enumerate(hits, 1))
    return chats or "(no relevant chat history found)", info


def run_one(q: dict, arm: str, model: str, judge_model: str, embedder, scorer, complete, judge_complete=None) -> dict:
    context, info = build_context(q, arm, embedder, scorer)
    prompt = READ_CHATS.format(context, q["question_date"], q["question"])
    answer, usage = complete(prompt, model)
    abstain = q["question_id"].endswith("_abs")
    template = JUDGE["abstention" if abstain else q["question_type"] if q["question_type"] in JUDGE else "plain"]
    verdict, judge_usage = (judge_complete or complete)(template.format(q["question"], q["answer"], answer),
                                                        judge_model)
    llm = judge_usage.get("llm") or usage.get("llm")
    return {"id": q["question_id"], "type": q["question_type"], "abstention": abstain,
            "correct": verdict.strip().lower().startswith("yes"), "prompt_chars": len(prompt), **info,
            "answer": answer[-1500:], "cost_usd": usage.get("cost_usd", 0) + judge_usage.get("cost_usd", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0),
            **({"llm": llm} if llm else {})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=ARMS, required=True)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--split", choices=["dev", "test", "all"], default="test")
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", choices=["claude", "openai", "gemini", "auto", "cursor"], default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--judge", default=None)
    ap.add_argument("--judge-backend", choices=["same", "codex"], default="same")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--report-only", action="store_true", help="print scores from existing jsonl and exit")
    args = ap.parse_args(argv)
    if args.report_only:
        path = Path(args.out) / f"{args.arm}.jsonl"
        if not path.exists():
            print(f"missing {path}", flush=True)
            return 1
        return report(path)
    if args.backend == "openai":
        args.model = args.model or "gpt-4o-mini"
        args.judge = args.judge or "gpt-4o-mini"
    elif args.backend == "gemini":
        args.model = args.model or "gemini-3.8-flash"
        args.judge = args.judge or "gemini-3.8-flash"
    elif args.backend == "auto":
        args.model = args.model or "gpt-4o-mini"
        args.judge = args.judge or "gpt-4o-mini"
    elif args.backend == "cursor":
        args.model = args.model or "composer-2.5"
        args.judge = args.judge or args.model
    else:
        args.model = args.model or "sonnet"
        args.judge = args.judge or "sonnet"
    complete = {
        "openai": openai_complete, "gemini": gemini_complete, "auto": auto_complete, "claude": claude,
        "cursor": cursor_complete,
    }[args.backend]
    judge_complete = codex_complete if args.judge_backend == "codex" else None
    if judge_complete:
        args.judge = "default"
    data = sample(split(json.loads(DATA.read_text()), args.split), args.n)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.arm}.jsonl"
    done = {json.loads(line)["id"] for line in path.read_text().splitlines()} if path.exists() else set()
    from horizon.memrouter.embedding import make_embedder

    embedder = make_embedder("fastembed") if args.arm != "full" else None
    scorer = make_judge(args.arm)
    todo = [q for q in data if q["question_id"] not in done]
    print(f"{args.arm} ({args.backend} answer={args.model} judge={args.judge}): "
          f"{len(todo)} of {len(data)} questions to run", flush=True)
    with cf.ThreadPoolExecutor(args.workers) as pool, path.open("a") as fh:
        futures = {pool.submit(run_one, q, args.arm, args.model, args.judge, embedder, scorer, complete,
                               judge_complete): q for q in todo}
        for i, f in enumerate(cf.as_completed(futures), 1):
            try:
                row = f.result()
            except SystemExit as exc:
                print(exc, flush=True)
                pool.shutdown(cancel_futures=True)
                return 1
            except QuotaExhausted as exc:
                print(f"\n{exc}", flush=True)
                print("Paused with progress saved — re-run the same command to resume.", flush=True)
                pool.shutdown(cancel_futures=True)
                return 2
            except Exception as exc:  # one question failing doesn't stop the run
                if _rate_limited(exc) and args.backend == "gemini":
                    print(f"\n{exc}", flush=True)
                    print("Paused (Gemini limits). Use --backend auto or retry after quota reset.", flush=True)
                    pool.shutdown(cancel_futures=True)
                    return 2
                print(f"  {futures[f]['question_id']}: {exc!r}", flush=True)
                continue
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"  [{i}/{len(todo)}] {row['type']:26} {'yes' if row['correct'] else 'no '} "
                  f"{row['prompt_chars'] // 4:>7} tok", flush=True)
    return report(path)


def report(path: Path) -> int:
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    by = defaultdict(list)
    for r in rows:
        by["abstention" if r["abstention"] else r["type"]].append(r["correct"])
    print(f"\n{path.stem}: {sum(r['correct'] for r in rows)}/{len(rows)} = {100 * sum(r['correct'] for r in rows) / len(rows):.1f} %")
    for k, v in sorted(by.items()):
        print(f"  {k:26} {sum(v)}/{len(v)}")
    pt = sum(r.get("prompt_tokens", 0) for r in rows)
    mean_tok = pt / len(rows) if pt else sum(r["prompt_chars"] for r in rows) / len(rows) / 4
    print(f"  mean prompt: {mean_tok:.0f} tokens; cost ${sum(r['cost_usd'] for r in rows):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Scorers for the decision layer (PROJECT.md §5): our own calibrated judge (`own`), Jev, and a small-LLM comparison.

Both answer the same typed questions over the same state, so they are swappable and comparable
(§5: "keep a small-LLM scorer as comparison to prove Jev's value"; §16: keep the scorer swappable).
Questions use Jev's shapes: a Noul is a yes/no probability, a Score picks a position on ordered levels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Answer:
    value: float  # Noul: probability of yes. Score: position on the levels, normalised to 0..1.
    confidence: float | None = None  # Jev Score confidence; None when the scorer gives none (Noul, LLM)


class Scorer(Protocol):
    name: str  # also the prediction source recorded on episodes: "jev" | "llm"

    def ask(self, state: dict, questions: dict[str, dict]) -> dict[str, Answer]: ...


def noul(instructions: str | dict) -> dict:
    return {"type": "noul", "instructions": instructions}


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}


KEYCHAIN_SERVICE = "horizon-typesafe"


def typesafe_key() -> str | None:
    """TYPESAFE_API_KEY, or the macOS Keychain's `horizon-typesafe` item when the variable is missing or hidden:
    Cursor passes secret-looking variables to hooks as "..."."""
    import os
    import subprocess
    import sys

    key = os.environ.get("TYPESAFE_API_KEY", "")
    if len(key) > 8:
        return key
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


class JevScorer:
    """TypeSafe's Jev through the official SDK (the key from `typesafe_key`; retries 429/529 itself)."""

    name = "jev"

    def __init__(self, model: str = "jev-latest", client: Any = None):
        from typesafe_sdk import TypeSafeClient

        # A dropped connection otherwise hangs forever (a BEAM run stalled 30+ min); callers fall back on errors.
        self.client = client or TypeSafeClient(model=model, api_key=typesafe_key(), timeout=120)

    def ask(self, state: dict, questions: dict[str, dict]) -> dict[str, Answer]:
        from typesafe_sdk import Noul, Score

        typed = {qid: Noul(instructions=q["instructions"]) if q["type"] == "noul"
                 else Score(instructions=q["instructions"], criteria=q["criteria"])
                 for qid, q in questions.items()}
        response = self.client.system_one(state=state, questions=typed)
        out = {}
        for qid, q in questions.items():
            a = response.answers[qid]
            if q["type"] == "noul":
                out[qid] = Answer(a.noul)
            else:
                out[qid] = Answer(a.score / (len(q["criteria"]) - 1), a.confidence)
        return out


LLM_SYSTEM = """You score decision options for a software engineering agent. Read the JSON state and answer every
question. For a yes/no question, give your probability (0 to 1) that the answer is yes. For a question with
numbered levels, give the level number that fits best (a fraction between two levels is allowed)."""


class LLMScorer:
    """The small-LLM comparison scorer: one Claude call with a JSON schema over the same questions."""

    name = "llm"

    def __init__(self, model: str = "claude-haiku-4-5", client: Any = None):
        import anthropic

        self.model = model
        self.client = client or anthropic.Anthropic()

    def ask(self, state: dict, questions: dict[str, dict]) -> dict[str, Answer]:
        keys = {qid: f"q{i}" for i, qid in enumerate(questions)}  # schema-safe property names
        lines = []
        for qid, q in questions.items():
            text = q["instructions"] if isinstance(q["instructions"], str) else json.dumps(q["instructions"])
            if q["type"] == "noul":
                lines.append(f"{keys[qid]} (yes/no probability): {text}")
            else:
                levels = "; ".join(f"{i} = {level}" for i, level in enumerate(q["criteria"]))
                lines.append(f"{keys[qid]} (level 0-{len(q['criteria']) - 1}): {text} Levels: {levels}")
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=LLM_SYSTEM,
            messages=[{"role": "user", "content": f"State:\n{json.dumps(state, indent=1)}\n\nQuestions:\n"
                                                  + "\n".join(lines)}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {k: {"type": "number"} for k in keys.values()},
                "required": list(keys.values()),
                "additionalProperties": False,
            }}},
        )
        if response.stop_reason != "end_turn":
            raise RuntimeError(f"LLM scorer stopped early: {response.stop_reason}")
        raw = json.loads(next(b.text for b in response.content if b.type == "text"))
        out = {}
        for qid, q in questions.items():
            v = float(raw[keys[qid]])
            top = 1 if q["type"] == "noul" else len(q["criteria"]) - 1
            out[qid] = Answer(max(0.0, min(1.0, v / top)))
        return out


def _isotonic(xs: list[float], ys: list[float]):
    """Pool-adjacent-violators: a monotone map raw -> observed rate (stdlib only)."""
    pooled: dict[float, list[float]] = {}
    for x, y in zip(xs, ys):  # equal raw scores are one point: their observed rate, not each label on its own
        pooled.setdefault(x, [0.0, 0.0])
        pooled[x][0] += y
        pooled[x][1] += 1
    blocks = [[sy, n, x] for x, (sy, n) in sorted(pooled.items())]
    out: list[list[float]] = []
    for b in blocks:
        out.append(b)
        while len(out) > 1 and out[-2][0] / out[-2][1] > out[-1][0] / out[-1][1]:
            y, n, _ = out.pop()
            out[-1][0] += y
            out[-1][1] += n
    edges, vals = [b[2] for b in out], [b[0] / b[1] for b in out]

    def f(x: float) -> float:
        i = max(0, min(len(vals) - 1, sum(e <= x for e in edges) - 1))
        return vals[i]
    return f


class OwnJudge:
    """Our own judge (owner, 2026-10-16: everything our own): the same typed questions as Jev, answered by any
    OpenAI-compatible endpoint (our Qwen model now; our own trained model later), then CALIBRATED on real outcomes.

    Every answer is logged (`judgments` in a small SQLite file). When an outcome is known, `label(judgment_id,
    outcome)` records it; with `min_labels`+ labelled answers for a question family (the question id without
    digits, e.g. "o1_success" -> "o_success"), answers in that family go through an isotonic map from raw score to
    the observed rate, so a 0.8 comes to mean 80 %. `last_ids` holds the judgment ids of the latest `ask`.
    """

    name = "own"

    def __init__(self, url: str, model: str = "local", db_path: str | None = None, chat: Any = None,
                 min_labels: int = 20, timeout: float = 120):
        import os
        import sqlite3
        from pathlib import Path

        if not url and chat is None:
            raise ValueError("own judge needs HORIZON_OWN_JUDGE_URL (an OpenAI-compatible endpoint)")
        self.url, self.model, self.min_labels, self.timeout = url, model, min_labels, timeout
        self.chat = chat or self._http_chat
        path = Path(db_path or os.environ.get("HORIZON_OWN_JUDGE_DB", "~/.horizon/judge.db")).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS judgments (id INTEGER PRIMARY KEY, ts REAL, family TEXT, qid TEXT, "
                        "raw REAL, outcome REAL)")
        self.db.commit()
        self.last_ids: dict[str, int] = {}
        self._maps: dict[str, Any] = {}

    def _http_chat(self, prompt: str) -> str:
        import urllib.request

        body = {"model": self.model, "temperature": 0, "max_tokens": 1500, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": LLM_SYSTEM}, {"role": "user", "content": prompt}],
                "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(self.url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    @staticmethod
    def family(qid: str) -> str:
        import re

        return re.sub(r"\d+", "", qid)

    def calibration(self, family: str):
        if family not in self._maps:
            rows = self.db.execute("SELECT raw, outcome FROM judgments WHERE family = ? AND outcome IS NOT NULL",
                                   (family,)).fetchall()
            self._maps[family] = _isotonic([r[0] for r in rows], [r[1] for r in rows]) \
                if len(rows) >= self.min_labels else None
        return self._maps[family]

    def ask(self, state: dict, questions: dict[str, dict]) -> dict[str, Answer]:
        import time

        keys = {qid: f"q{i}" for i, qid in enumerate(questions)}
        lines = []
        for qid, q in questions.items():
            text = q["instructions"] if isinstance(q["instructions"], str) else json.dumps(q["instructions"])
            if q["type"] == "noul":
                lines.append(f"{keys[qid]} (yes/no probability, 0 to 1): {text}")
            else:
                levels = "; ".join(f"{i} = {level}" for i, level in enumerate(q["criteria"]))
                lines.append(f"{keys[qid]} (level 0-{len(q['criteria']) - 1}): {text} Levels: {levels}")
        prompt = (f"State:\n{json.dumps(state, indent=1)}\n\nQuestions:\n" + "\n".join(lines)
                  + "\n\nReturn JSON with one number per question key: " + json.dumps({k: 0 for k in keys.values()}))
        raw_text = self.chat(prompt)
        start, end = raw_text.find("{"), raw_text.rfind("}")
        raw = json.loads(raw_text[start:end + 1]) if start >= 0 else {}
        out, self.last_ids = {}, {}
        for qid, q in questions.items():
            top = 1 if q["type"] == "noul" else len(q["criteria"]) - 1
            v = max(0.0, min(1.0, float(raw.get(keys[qid], 0.5)) / top))
            fam = self.family(qid)
            cur = self.db.execute("INSERT INTO judgments (ts, family, qid, raw) VALUES (?, ?, ?, ?)",
                                  (time.time(), fam, qid, v))
            self.last_ids[qid] = cur.lastrowid
            cal = self.calibration(fam) if q["type"] == "noul" else None  # rates calibrate yes/no answers
            out[qid] = Answer(cal(v) if cal else v)
        self.db.commit()
        return out

    def label(self, judgment_id: int, outcome: float) -> None:
        """The real outcome for a logged yes/no judgment (1 = it was yes): the judge's calibration data."""
        row = self.db.execute("SELECT family FROM judgments WHERE id = ?", (judgment_id,)).fetchone()
        self.db.execute("UPDATE judgments SET outcome = ? WHERE id = ?", (float(outcome), judgment_id))
        self.db.commit()
        if row:
            self._maps.pop(row[0], None)


def make_scorer(kind: str, jev_model: str, llm_model: str, own_url: str = "", own_model: str = "local") -> Scorer | None:
    """`none` (the default until the owner enables a real scorer): the layer ranks on measured values only.
    `own` is our own calibrated judge (HORIZON_OWN_JUDGE_URL / HORIZON_OWN_JUDGE_MODEL); `jev` and `llm` stay as
    comparisons."""
    if kind == "own":
        return OwnJudge(own_url, own_model)
    if kind == "jev":
        return JevScorer(jev_model)
    if kind == "llm":
        return LLMScorer(llm_model)
    return None

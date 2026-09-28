"""Scorers for the decision layer (PROJECT.md §5): Jev, and a small-LLM comparison scorer.

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


class JevScorer:
    """TypeSafe's Jev through the official SDK (reads TYPESAFE_API_KEY; retries 429/529 itself)."""

    name = "jev"

    def __init__(self, model: str = "jev-latest", client: Any = None):
        from typesafe_sdk import TypeSafeClient

        self.client = client or TypeSafeClient(model=model)

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


def make_scorer(kind: str, jev_model: str, llm_model: str) -> Scorer | None:
    """`none` (the default until the owner enables a real scorer): the layer ranks on measured values only."""
    if kind == "jev":
        return JevScorer(jev_model)
    if kind == "llm":
        return LLMScorer(llm_model)
    return None

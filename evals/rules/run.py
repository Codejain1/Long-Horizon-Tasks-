"""How well are rule sentences picked out? Keywords against a scorer (Jev), on a labelled set.

    python evals/rules/run.py            # keywords only
    HORIZON_SCORER=jev TYPESAFE_API_KEY=... python evals/rules/run.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from horizon.config import Settings
from horizon.decision.scorers import make_scorer
from horizon.hooks import split_rules

DATA = json.loads((Path(__file__).parent / "labelled.json").read_text())


def label(sentence: str, scorer) -> str:
    team, project = split_rules(sentence, scorer)
    return "team" if team else "project" if project else "none"


def evaluate(scorer) -> dict:
    wrong = [(d["sentence"], d["label"], got) for d in DATA if (got := label(d["sentence"], scorer)) != d["label"]]
    return {"accuracy": round(1 - len(wrong) / len(DATA), 3), "wrong": wrong}


def main() -> int:
    settings = Settings.from_env()
    arms = {"keywords": None}
    if settings.scorer != "none":
        arms[settings.scorer] = make_scorer(settings.scorer, settings.jev_model, settings.llm_scorer_model)
    for name, scorer in arms.items():
        result = evaluate(scorer)
        print(f"{name}: {result['accuracy']:.0%} of {len(DATA)}")
        for sentence, want, got in result["wrong"]:
            print(f"   want {want:<7} got {got:<7} {sentence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

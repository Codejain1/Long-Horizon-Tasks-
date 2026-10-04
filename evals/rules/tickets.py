"""Rule capture on whole tickets, the way lean runs it (all of a message's sentences in one request).

Each rule a scenario's hidden tests check must be kept from the ticket that states it; anything else kept is
noise in every later session start. A per-sentence check missed that context matters: continuation sentences
("that limit is scaled ...", "And a patient's MRN ...") were dropped when judged inside the ticket.

    python evals/rules/tickets.py    (needs a scorer: HORIZON_SCORER=jev and a TypeSafe key)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from horizon.config import Settings
from horizon.decision.scorers import make_scorer
from horizon.hooks import split_rules

SCENARIOS = Path(__file__).resolve().parents[1] / "longhorizon" / "scenarios"
# (scenario, ticket number, a phrase from each rule sentence the ticket must keep)
EXPECTED = [
    ("ward", 1, ["never fractions", "rolling 24-hour", "scaled by weight"]),
    ("ward", 2, ["cross-react", "witness", "MRN"]),
    ("ward", 3, ["legal record", "halved", "London time"]),
    ("ward", 9, ["1 April 2026"]),
    ("rental", 1, ["4 months' rent", "9% a year", "11 months"]),
    ("rental", 2, ["Model Tenancy Act", "consented", "phone numbers"]),
    ("rental", 3, ["25% of every deposit"]),
    ("rental", 9, ["10% a year"]),
]


def evaluate(scorer) -> dict:
    caught = missed = noise = 0
    misses = []
    for scenario, n, phrases in EXPECTED:
        ticket = json.loads((SCENARIOS / scenario / "scenario.json").read_text())["sessions"][n - 1]
        team, project = split_rules(ticket, scorer)
        kept = team + project
        for p in phrases:
            if any(p in k for k in kept):
                caught += 1
            else:
                missed += 1
                misses.append(f"{scenario} #{n}: {p}")
        noise += sum(not any(p in k for p in phrases) for k in kept)
    return {"caught": caught, "missed": missed, "noise": noise, "misses": misses}


def main() -> int:
    s = Settings.from_env()
    scorer = make_scorer(s.scorer, s.jev_model, s.llm_scorer_model) if s.scorer != "none" else None
    r = evaluate(scorer)
    print(f"rules kept {r['caught']}/{r['caught'] + r['missed']}, other sentences kept {r['noise']}")
    for m in r["misses"]:
        print("   missed", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())

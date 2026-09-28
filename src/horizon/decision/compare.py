"""Scorer comparison (PROJECT.md §5: "keep a small-LLM scorer as comparison to prove Jev's value").

Replays logged decisions (the same state each scorer would have seen) through every scorer, then reports
how often they pick the same option and, where the host recorded what happened, how well each one's
success prediction matched the outcome (Brier score, lower is better). This is also the data for tuning
the decision weights and thresholds (PROGRESS open question 39).
"""

from __future__ import annotations

from horizon.decision.layer import Option, first_pass
from horizon.decision.log import DecisionLog
from horizon.memrouter.spikes import same_option


def compare(log: DecisionLog, team_id: str, scorers: dict, settings, limit: int = 50) -> dict:
    records = [r for r in log.export(team_id) if r.get("options")][-limit:]
    per = {name: {"brier_sum": 0.0, "outcomes": 0, "failed": 0} for name in scorers}
    agree = compared = 0
    for record in records:
        options = [Option(**o) for o in record["options"]]
        picks = {}
        for name, scorer in scorers.items():
            result = first_pass(record["state"], options, scorer, settings)
            if result["scorer_status"] != "ok":
                per[name]["failed"] += 1
                continue
            ranked = sorted((r for r in result["options"] if r["composite"] is not None), key=lambda r: -r["composite"])
            picks[name] = ranked[0]["label"] if ranked else None
            for outcome in record["outcomes"]:
                row = next((r for r in result["options"] if same_option(r["label"], outcome.get("option"))), None)
                if row and outcome.get("success") is not None:
                    per[name]["brier_sum"] += (row["success_raw"] - outcome["success"]) ** 2
                    per[name]["outcomes"] += 1
        if len(picks) == len(scorers) and len(scorers) > 1:
            compared += 1
            agree += len(set(picks.values())) == 1
    return {
        "decisions": len(records),
        "top_choice_agreement": round(agree / compared, 3) if compared else None,
        "scorers": {name: {"outcomes": s["outcomes"], "failed": s["failed"],
                           "brier": round(s["brier_sum"] / s["outcomes"], 4) if s["outcomes"] else None}
                    for name, s in per.items()},
    }

"""Decision layer (PROJECT.md §5, build order Phase 4): crucial-decision detection, scoring, weights, thresholds.

One scorer request carries every question, answered in parallel: whether the decision is crucial, whether it
is high stakes, and per option its chance of success, compatibility, architecture fit and reversibility.
Measurable dimensions (cost, tokens, latency) come from the host's estimates and are computed in code.
Code owns the policy: weights, the clear-winner margin, and the close-call fallback.

Consequence checking on close calls (spikes, re-scoring with evidence) is Phase 5. Until then a close call
goes straight to §5's second-tie rule: pick the cheaper or more reversible option, or ask a human if the
stakes are high.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from horizon.decision.scorers import Answer, Scorer, noul, score

log = logging.getLogger(__name__)

MEASURED = ("cost_usd", "tokens", "latency_ms")
CRUCIAL_SIGNALS = ("irreversible", "many_steps", "real_cost")

_FIT_LEVELS = {
    "compatibility": [
        "Conflicts with the existing stack, constraints or past failures; would need major rework",
        "Works only with significant changes or workarounds",
        "Works with some adjustments",
        "Fits the existing stack and constraints with minor adjustments",
        "Fits the existing stack, constraints and past outcomes directly",
    ],
    "architecture_fit": [
        "Works against the project's structure; adds lasting complexity",
        "Awkward fit; adds noticeable complexity",
        "Neutral: neither helps nor hurts the structure",
        "Good fit with the project's structure",
        "Natural fit that simplifies the structure",
    ],
}


@dataclass
class Option:
    label: str
    description: str = ""
    est_cost_usd: float | None = None
    est_tokens: float | None = None
    est_latency_ms: float | None = None

    def estimate(self, dim: str) -> float | None:
        return getattr(self, f"est_{dim}")


def build_questions(n_options: int) -> dict[str, dict]:
    qs = {
        "crucial.irreversible": noul("Would the choice in `situation` be hard or expensive to reverse later?"),
        "crucial.many_steps": noul("Will the choice in `situation` shape many later steps of the task in `goal`?"),
        "crucial.real_cost": noul("Does the choice in `situation` carry real cost: money, many tokens, lost time "
                                  "or data risk?"),
        # Wording checked against live Jev: "delete or overwrite data" also fired on caches (0.80 -> 0.20).
        "high_stakes": noul({
            "question": "Could any of the `options` cause real-world harm that is hard to undo: spending real "
                        "money, sending messages to real people, or destroying existing user or production data?",
            "not_counted": "Routine writes the software itself manages (caches, temp files, build output, test "
                           "data) and changes git can undo.",
        }),
    }
    for i in range(n_options):
        ref = f"`options[{i}]`"
        qs[f"o{i}.success"] = noul(f"Will {ref} work: achieve `goal` within `constraints` for `situation`, "
                                   f"given `past_outcomes`?")
        qs[f"o{i}.compatibility"] = score(f"How compatible is {ref} with the stack, `constraints` and "
                                          f"`past_outcomes`?", _FIT_LEVELS["compatibility"])
        qs[f"o{i}.architecture_fit"] = score(f"How well does {ref} fit the project's architecture?",
                                             _FIT_LEVELS["architecture_fit"])
        qs[f"o{i}.reversible"] = noul(f"Could {ref} be undone cheaply later if it turns out wrong?")
    return qs


def measured(options: list[Option], dim: str) -> list[float] | None:
    """Relative score per option (cheapest = 1), only when every option has an estimate."""
    values = [o.estimate(dim) for o in options]
    if any(v is None for v in values):
        return None
    best = min(values)
    return [1.0 if v <= best else best / v for v in values]


def composite(dims: dict[str, float], weights: dict[str, float]) -> float | None:
    used = {d: w for d, w in weights.items() if d in dims and w > 0}
    total = sum(used.values())
    return sum(dims[d] * w for d, w in used.items()) / total if total else None


def evaluate(state: dict, options: list[Option], scorer: Scorer | None, settings, *,
             crucial_hint: bool | None = None, fear_warnings: int = 0) -> dict:
    answers: dict[str, Answer] | None = None
    status = "not_configured" if scorer is None else "ok"
    if scorer is not None:
        try:
            answers = scorer.ask(state, build_questions(len(options)))
        except Exception as exc:  # the decision layer must never block the host (like memrouter, §7)
            log.warning("scorer unavailable: %s", exc)
            status = "unavailable"

    rows = []
    rel = {d: measured(options, d) for d in MEASURED}
    for i, o in enumerate(options):
        dims = {d: round(rel[d][i], 3) for d in MEASURED if rel[d] is not None}
        confidence = None
        if answers:
            dims["success"] = round(answers[f"o{i}.success"].value, 3)
            for d in ("compatibility", "architecture_fit"):
                dims[d] = round(answers[f"o{i}.{d}"].value, 3)
            confs = [answers[f"o{i}.{d}"].confidence for d in ("compatibility", "architecture_fit")]
            confs = [c for c in confs if c is not None]
            confidence = min(confs) if confs else None
        rows.append({"label": o.label, "composite": composite(dims, settings.decision_weights),
                     "confidence": confidence, "dimensions": dims,
                     "reversible": round(answers[f"o{i}.reversible"].value, 3) if answers else None})

    ranked = sorted((r for r in rows if r["composite"] is not None), key=lambda r: -r["composite"])
    for r in rows:
        r["composite"] = None if r["composite"] is None else round(r["composite"], 3)
    out = {"scorer": scorer.name if scorer else None, "scorer_status": status, "options": rows}

    if not answers:
        # Estimates alone only say what is cheap, not what works, so nothing is chosen (seen in a real run: the
        # cheapest option was the one that couldn't meet the task's multi-process requirement).
        return {**out, "decision": "unscored", "chosen": None, "predicted_success": None,
                "cheapest_by_estimates": [r["label"] for r in ranked],
                "reason": "No scorer answers, so the options weren't judged.",
                "next": "Decide yourself on engineering merit; prefer the option that is easier to undo."}

    signals = {s: round(answers[f"crucial.{s}"].value, 3) for s in CRUCIAL_SIGNALS}
    crucial = crucial_hint if crucial_hint is not None else max(signals.values()) >= settings.crucial_threshold
    high_stakes = answers["high_stakes"].value >= settings.high_stakes_threshold or fear_warnings > 0
    top, second = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    margin = top["composite"] - second["composite"] if second else 1.0
    out.update(crucial=crucial, crucial_signals=signals, high_stakes=high_stakes, margin=round(margin, 3))

    def decided(kind: str, row: dict | None, reason: str, next_: str) -> dict:
        return {**out, "decision": kind, "chosen": row["label"] if row else None,
                "predicted_success": row["dimensions"].get("success") if row else None,
                "reason": reason, "next": next_}

    if not crucial:
        return decided("routine", top, "Not a crucial decision: easy to reverse, local, and cheap.",
                       f"Proceed with {top['label']} (or your own choice) without further checks.")
    clear = margin >= settings.clear_margin and (top["confidence"] is None
                                                 or top["confidence"] >= settings.min_confidence)
    if clear:
        return decided("clear_winner", top, f"{top['label']} leads by {margin:.2f}.",
                       f"Implement {top['label']}, then record_outcome.")

    # Close call (Phase 5 adds consequence checking here). §5 second-tie rule:
    if high_stakes:
        why = "matching severe past failures" if fear_warnings else "money, messages or data are at stake"
        return decided("ask_human", None, f"Close call and high stakes ({why}).",
                       "Show the user the options and scores, and ask which to take before acting.")
    close = [r for r in ranked if top["composite"] - r["composite"] < settings.clear_margin]
    by_label = {o.label: o for o in options}
    costs = [by_label[r["label"]].est_cost_usd for r in close]
    if None not in costs and len(set(costs)) > 1:
        pick, why = min(close, key=lambda r: by_label[r["label"]].est_cost_usd), "the cheaper"
    else:
        pick, why = max(close, key=lambda r: r["reversible"]), "the more reversible"
    return decided("close_call", pick, f"Close call (margin {margin:.2f}): took {why} option.",
                   f"Implement {pick['label']}, then record_outcome.")

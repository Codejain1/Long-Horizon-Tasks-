"""Decision layer (PROJECT.md §5-6, build order Phases 4-5).

Pass 1 (evaluate_options): one scorer request carries every question, answered in parallel: whether the
decision is crucial, whether it is high stakes, and per option its chance of success, compatibility,
architecture fit and reversibility. Measurable dimensions (cost, tokens, latency) come from the host's
estimates and are computed in code. Code owns the policy: weights, the clear-winner margin, the fallback.

Close calls get consequence checking (§6), cheapest first: past spike results from memory, then static
checks and a spike per option that the host runs locally, or try-and-rollback when every close option is
cheap to undo. Pass 2 (submit_consequences) re-scores the close options with the consequences as evidence,
adds the consequence questions, and eliminates options that failed a check. A second tie never loops (§5):
cheaper or more reversible, or ask a human if the stakes are high.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from horizon.decision.scorers import Answer, Scorer, noul, score

log = logging.getLogger(__name__)

MEASURED = ("cost_usd", "tokens", "latency_ms")
CRUCIAL_SIGNALS = ("irreversible", "many_steps", "real_cost")
JUDGED = ("compatibility", "architecture_fit")

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

# Inside the project so the host needs no extra permission, and git-ignored by the installer. Seen live: spikes
# in /tmp cost a headless host 10 permission denials before it found a writable place.
SPIKE_DIR = ".horizon/spikes"

STATIC_CHECKS = [
    "The option's dependency (if any) resolves for this project's runtime, without installing it into the "
    "project: e.g. `pip install --dry-run`, `npm view <pkg> engines`, `cargo search`.",
    "Its licence and supported platforms/versions fit the project.",
    "The project's existing type-check or lint configuration accepts it (only if that needs no implementation).",
]


@dataclass
class Option:
    label: str
    description: str = ""
    est_cost_usd: float | None = None
    est_tokens: float | None = None
    est_latency_ms: float | None = None

    def estimate(self, dim: str) -> float | None:
        return getattr(self, f"est_{dim}")


def _option_questions(i: int, evidence: bool) -> dict[str, dict]:
    ref = f"`options[{i}]`"
    given = " given `consequences`" if evidence else ""
    return {
        f"o{i}.success": noul(f"Will {ref} work: achieve `goal` within `constraints` for `situation`, "
                              f"given `past_outcomes`{' and `consequences`' if evidence else ''}?"),
        f"o{i}.compatibility": score(f"How compatible is {ref} with the stack, `constraints` and "
                                     f"`past_outcomes`{given}?", _FIT_LEVELS["compatibility"]),
        f"o{i}.architecture_fit": score(f"How well does {ref} fit the project's architecture{given}?",
                                        _FIT_LEVELS["architecture_fit"]),
        f"o{i}.reversible": noul(f"Could {ref} be undone cheaply later if it turns out wrong?"),
    }


def build_questions(n_options: int) -> dict[str, dict]:
    """Pass 1."""
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
        qs.update(_option_questions(i, evidence=False))
    return qs


def consequence_questions(n_options: int) -> dict[str, dict]:
    """Pass 2: the same per-option questions with `consequences` as evidence, plus the §6 step 4 questions."""
    qs = {}
    for i in range(n_options):
        ref = f"`options[{i}]`"
        qs.update(_option_questions(i, evidence=True))
        qs[f"o{i}.breaks_tests"] = noul(f"Given `consequences`, would implementing {ref} break existing tests "
                                        f"or behaviour of the project?")
        qs[f"o{i}.costlier"] = noul(f"Given `consequences`, will {ref} cost noticeably more than the other "
                                    f"`options` to finish: tokens, money, time or added complexity?")
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


def _ask(scorer: Scorer | None, state: dict, questions: dict) -> tuple[dict[str, Answer] | None, str]:
    if scorer is None:
        return None, "not_configured"
    try:
        return scorer.ask(state, questions), "ok"
    except Exception as exc:  # the decision layer must never block the host (like memrouter, §7)
        log.warning("scorer unavailable: %s", exc)
        return None, "unavailable"


def _rows(options: list[Option], answers: dict[str, Answer] | None, settings, second_pass: bool) -> list[dict]:
    rows, rel = [], {d: measured(options, d) for d in MEASURED}
    for i, o in enumerate(options):
        dims = {d: round(rel[d][i], 3) for d in MEASURED if rel[d] is not None}
        confidence = reversible = None
        if answers:
            dims["success"] = round(answers[f"o{i}.success"].value, 3)
            for d in JUDGED:
                dims[d] = round(answers[f"o{i}.{d}"].value, 3)
            if second_pass:
                dims["no_regressions"] = round(1 - answers[f"o{i}.breaks_tests"].value, 3)
                dims["relative_cost"] = round(1 - answers[f"o{i}.costlier"].value, 3)
            confs = [c for c in (answers[f"o{i}.{d}"].confidence for d in JUDGED) if c is not None]
            confidence = min(confs) if confs else None
            reversible = round(answers[f"o{i}.reversible"].value, 3)
        c = composite(dims, settings.decision_weights)
        rows.append({"label": o.label, "composite": None if c is None else round(c, 3), "confidence": confidence,
                     "dimensions": dims, "reversible": reversible})
    return rows


def _raw(answers: dict[str, Answer] | None) -> dict | None:
    """Every scorer answer, for the world-model log."""
    return None if answers is None else {q: {"value": round(a.value, 4), "confidence": a.confidence}
                                         for q, a in answers.items()}


def _second_tie(close: list[dict], options: list[Option], high_stakes: bool, fear_warnings: int,
                margin: float, stage: str) -> tuple[str, dict | None, str, str]:
    """§5: don't loop. The cheaper (by estimate) or more reversible option, or a human if stakes are high."""
    if high_stakes:
        why = "matching severe past failures" if fear_warnings else "money, messages or data are at stake"
        return ("ask_human", None, f"Still close {stage} and high stakes ({why}).",
                "Show the user the options, scores and consequences, and ask which to take before acting.")
    by_label = {o.label: o for o in options}
    costs = [by_label[r["label"]].est_cost_usd for r in close]
    if None not in costs and len(set(costs)) > 1:
        pick, why = min(close, key=lambda r: by_label[r["label"]].est_cost_usd), "the cheaper"
    else:
        pick, why = max(close, key=lambda r: r["reversible"] or 0), "the more reversible"
    return ("close_call", pick, f"Still close {stage} (margin {margin:.2f}): took {why} option.",
            f"Implement {pick['label']}, then record_outcome.")


def _result(out: dict, kind: str, row: dict | None, reason: str, next_: str) -> dict:
    return {**out, "decision": kind, "chosen": row["label"] if row else None,
            "predicted_success": row["dimensions"].get("success") if row else None, "reason": reason, "next": next_}


def first_pass(state: dict, options: list[Option], scorer: Scorer | None, settings, *,
               crucial_hint: bool | None = None, fear_warnings: int = 0) -> dict:
    """Returns a final decision, or `decision: "close"` with the close options for consequence checking."""
    answers, status = _ask(scorer, state, build_questions(len(options)))
    rows = _rows(options, answers, settings, second_pass=False)
    ranked = sorted((r for r in rows if r["composite"] is not None), key=lambda r: -r["composite"])
    out = {"scorer": scorer.name if scorer else None, "scorer_status": status, "options": rows,
           "answers": _raw(answers)}

    if not answers:
        # Estimates alone only say what is cheap, not what works, so nothing is chosen (seen in a real run: the
        # cheapest option was the one that couldn't meet the task's multi-process requirement).
        return {**_result(out, "unscored", None, "No scorer answers, so the options weren't judged.",
                          "Decide yourself on engineering merit; prefer the option that is easier to undo."),
                "cheapest_by_estimates": [r["label"] for r in ranked]}

    signals = {s: round(answers[f"crucial.{s}"].value, 3) for s in CRUCIAL_SIGNALS}
    crucial = crucial_hint if crucial_hint is not None else max(signals.values()) >= settings.crucial_threshold
    high_stakes = answers["high_stakes"].value >= settings.high_stakes_threshold or fear_warnings > 0
    top, second = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    margin = top["composite"] - second["composite"] if second else 1.0
    out.update(crucial=crucial, crucial_signals=signals, high_stakes=high_stakes, margin=round(margin, 3))

    if not crucial:
        return _result(out, "routine", top, "Not a crucial decision: easy to reverse, local, and cheap.",
                       f"Proceed with {top['label']} (or your own choice) without further checks.")
    if margin >= settings.clear_margin and (top["confidence"] is None or top["confidence"] >= settings.min_confidence):
        return _result(out, "clear_winner", top, f"{top['label']} leads by {margin:.2f}.",
                       f"Implement {top['label']}, then record_outcome.")
    close = [r for r in ranked if top["composite"] - r["composite"] < settings.clear_margin]
    return {**out, "decision": "close", "chosen": None, "predicted_success": None,
            "close": [r["label"] for r in close]}


def plan_consequences(first: dict, situation: str, from_memory: dict[str, dict], settings, *,
                      can_roll_back: bool) -> dict:
    """What to check for the close options, cheapest first (§6). Returns the plan; `mode` is one of
    "memory" (everything already tested before), "try_and_rollback" or "spikes"."""
    close = first["close"]
    rows = {r["label"]: r for r in first["options"]}
    to_test = [label for label in close if label not in from_memory]
    if not to_test:
        return {"mode": "memory", "from_memory": from_memory}
    cheap_to_undo = all((rows[label]["reversible"] or 0) >= settings.try_reversible_threshold for label in close)
    if can_roll_back and cheap_to_undo and not first["high_stakes"]:
        return {"mode": "try_and_rollback", "from_memory": from_memory, "order": close}
    return {
        "mode": "spikes",
        "from_memory": from_memory,
        "static_checks": STATIC_CHECKS,
        "spikes": [{
            "option": label,
            "build": (f"The smallest proof of concept of \"{label}\" for: {situation}. Exercise only the one or "
                      f"two behaviours this decision depends on. Build it under {SPIKE_DIR}/ in the project "
                      f"(git-ignored, so it is never part of the working tree or a checkpoint), not in the "
                      f"project's own files, and delete it afterwards."),
            "measure": ["passed: did it do what the decision needs (true/false)",
                        "tests_passed / tests_failed of any quick checks you wrote",
                        "metrics: numbers that separate the options, e.g. latency_ms, tokens, cost_usd, "
                        "lines_of_code, peak_memory_mb"],
            "budget_minutes": settings.spike_budget_minutes,
        } for label in to_test],
    }


def second_pass(state: dict, options: list[Option], evidence: dict[str, dict], scorer: Scorer | None,
                settings, first: dict, *, fear_warnings: int = 0, stage: str = "after consequence checks") -> dict:
    """Re-score the close options with `evidence` ({label: consequence result}) and decide. No further loop."""
    close = [o for o in options if o.label in first["close"]]
    # Real measurements from spikes replace the host's estimates.
    for i, o in enumerate(close):
        metrics = ((evidence.get(o.label) or {}).get("spike") or {}).get("metrics") or {}
        close[i] = replace(o, **{f"est_{d}": float(metrics[d]) for d in MEASURED if d in metrics})
    state = {**state, "options": [{"label": o.label, "description": o.description} for o in close],
             "consequences": [{"option": o.label, **(evidence.get(o.label) or {"tested": False})} for o in close]}

    answers, status = _ask(scorer, state, consequence_questions(len(close)))
    rows = _rows(close, answers, settings, second_pass=True)
    if answers is None:  # keep pass 1's judgement, now with the measured values
        by_label = {r["label"]: r for r in first["options"]}
        for r in rows:
            prev = by_label[r["label"]]
            r["dimensions"] = {**prev["dimensions"], **r["dimensions"]}
            r.update(confidence=prev["confidence"], reversible=prev["reversible"],
                     composite=round(composite(r["dimensions"], settings.decision_weights), 3))

    # Hard evidence first: an option that failed a static check or its spike is out, unless all did.
    def failed(label: str) -> bool:
        ev = evidence.get(label) or {}
        return any(c.get("passed") is False for c in ev.get("static_checks") or []) or \
            (ev.get("spike") or {}).get("passed") is False

    eliminated = [r["label"] for r in rows if failed(r["label"])]
    alive = [r for r in rows if r["label"] not in eliminated] or rows
    ranked = sorted(alive, key=lambda r: -r["composite"])
    top, second = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    margin = top["composite"] - second["composite"] if second else 1.0
    out = {"scorer": first["scorer"], "scorer_status": status, "options": rows, "answers": _raw(answers),
           "eliminated": eliminated, "margin": round(margin, 3), "high_stakes": first["high_stakes"]}

    # After evidence, a lead of twice the clear margin stands even when Jev's Score confidence is low. Seen live:
    # pendulum led dateutil by 0.30 after a spike, but a 0.3x confidence made it "still close". In pass 1 low
    # confidence means "go check"; in pass 2 there is nothing left to check.
    confident = top["confidence"] is None or top["confidence"] >= settings.min_confidence
    if second is None or (margin >= settings.clear_margin and (confident or margin >= 2 * settings.clear_margin)):
        why = (f"the others failed their checks ({', '.join(eliminated)})" if second is None and eliminated
               else f"it leads by {margin:.2f}")
        return _result(out, "clear_winner", top, f"{top['label']} {stage}: {why}.",
                       f"Implement {top['label']}, then record_outcome.")
    close_rows = [r for r in ranked if top["composite"] - r["composite"] < settings.clear_margin]
    kind, pick, reason, next_ = _second_tie(close_rows, close, first["high_stakes"], fear_warnings, margin, stage)
    return _result(out, kind, pick, reason, next_)

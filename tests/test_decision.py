"""Phases 4-5: the decision layer and consequence checking (PROJECT.md §5-6)."""

import dataclasses
import json

import httpx2
import pytest

from horizon.decision.layer import (Option, build_questions, consequence_questions, first_pass, measured,
                                    plan_consequences, second_pass)
from horizon.decision.scorers import Answer, JevScorer, LLMScorer
from horizon.models import Actual
from horizon.service import Platform, ToolInputError


class FakeScorer:
    """Scripted answers per question kind. Pass 2 (state has `consequences`) can override with `second`."""

    name = "jev"

    def __init__(self, crucial=0.9, high_stakes=0.1, success=(0.8, 0.5), fit=(0.75, 0.5), reversible=(0.5, 0.5),
                 confidence=0.9, breaks_tests=(0.1, 0.1), costlier=(0.5, 0.5), second=None):
        self.values = {"success": success, "compatibility": fit, "architecture_fit": fit, "reversible": reversible,
                       "breaks_tests": breaks_tests, "costlier": costlier}
        self.crucial, self.high_stakes, self.confidence, self.second = crucial, high_stakes, confidence, second or {}
        self.calls = []

    def ask(self, state, questions):
        self.calls.append((state, questions))
        values = {**self.values, **(self.second if "consequences" in state else {})}
        out = {}
        for qid in questions:
            if qid.startswith("crucial."):
                out[qid] = Answer(self.crucial)
            elif qid == "high_stakes":
                out[qid] = Answer(self.high_stakes)
            else:
                i, dim = qid[1:].split(".")
                conf = self.confidence if dim in ("compatibility", "architecture_fit") else None
                out[qid] = Answer(values[dim][int(i)], conf)
        return out


OPTS = [Option("PostgreSQL", "relational, already used by the team"), Option("MongoDB", "document store")]
STATE = {"goal": "g", "constraints": [], "situation": "choose a database", "options": [{}, {}], "past_outcomes": []}
CLOSE = dict(success=(0.7, 0.69), fit=(0.6, 0.6))  # a near tie in pass 1


def with_scorer(settings, task_store, memrouter, project_dir, scorer):
    return Platform(settings, task_store, lambda: memrouter, cwd=project_dir, scorer_factory=lambda: scorer)


def decide(opts, scorer, settings, evidence=None):
    """Pass 1, and pass 2 (with `evidence`, or none) when pass 1 is a close call."""
    first = first_pass(STATE, opts, scorer, settings)
    return second_pass(STATE, opts, evidence or {}, scorer, settings, first) if first["decision"] == "close" else first


# --- pass 1 --------------------------------------------------------------------------

def test_questions_cover_detection_stakes_and_every_option():
    qs = build_questions(3)
    assert {"crucial.irreversible", "crucial.many_steps", "crucial.real_cost", "high_stakes"} <= set(qs)
    assert {f"o2.{d}" for d in ("success", "compatibility", "architecture_fit", "reversible")} <= set(qs)
    assert all(len(q["criteria"]) == 5 for q in qs.values() if q["type"] == "score")
    second = consequence_questions(2)
    assert {"o1.breaks_tests", "o1.costlier", "o1.success"} <= set(second) and "high_stakes" not in second
    assert "`consequences`" in second["o0.success"]["instructions"]


def test_measured_dimensions_are_relative_and_need_every_estimate():
    opts = [Option("a", est_cost_usd=1.0), Option("b", est_cost_usd=4.0)]
    assert measured(opts, "cost_usd") == [1.0, 0.25]
    assert measured(opts, "tokens") is None  # no estimates: dimension left out, weights renormalised


def test_routine_decisions_skip_the_full_flow(settings):
    r = first_pass(STATE, OPTS, FakeScorer(crucial=0.2), settings)
    assert r["decision"] == "routine" and r["chosen"] == "PostgreSQL" and r["crucial"] is False


def test_clear_winner(settings):
    r = first_pass(STATE, OPTS, FakeScorer(), settings)
    assert r["decision"] == "clear_winner" and r["chosen"] == "PostgreSQL"
    assert r["predicted_success"] == 0.8 and r["margin"] >= settings.clear_margin
    assert r["answers"]["o0.success"] == {"value": 0.8, "confidence": None}  # raw answers kept for the log


def test_low_confidence_or_a_small_margin_is_a_close_call(settings):
    assert first_pass(STATE, OPTS, FakeScorer(confidence=0.3), settings)["decision"] == "close"
    r = first_pass(STATE, OPTS, FakeScorer(**CLOSE), settings)
    assert r["decision"] == "close" and r["close"] == ["PostgreSQL", "MongoDB"] and r["chosen"] is None


def test_no_scorer_ranks_on_estimates_only(settings):
    opts = [Option("A", est_cost_usd=5.0, est_tokens=100), Option("B", est_cost_usd=1.0, est_tokens=100)]
    r = first_pass(STATE, opts, None, settings)
    assert r["decision"] == "unscored" and r["chosen"] is None and r["scorer_status"] == "not_configured"
    assert r["cheapest_by_estimates"] == ["B", "A"]  # information, not a recommendation
    assert first_pass(STATE, OPTS, None, settings)["cheapest_by_estimates"] == []


def test_a_failing_scorer_never_blocks(settings):
    class Down:
        name = "jev"

        def ask(self, state, questions):
            raise ConnectionError("529 overloaded")

    r = first_pass(STATE, OPTS, Down(), settings)
    assert r["decision"] == "unscored" and r["scorer_status"] == "unavailable"


# --- consequence plan (PROJECT.md §6, cheapest first) --------------------------------

def test_plan_asks_for_static_checks_and_a_spike_per_untested_option(settings):
    first = first_pass(STATE, OPTS, FakeScorer(**CLOSE), settings)
    past = {"MongoDB": {"result": {"option": "MongoDB", "spike": {"ran": True, "passed": True}}}}
    plan = plan_consequences(first, "choose a database", past, settings, can_roll_back=True)
    assert plan["mode"] == "spikes" and plan["static_checks"]
    assert [s["option"] for s in plan["spikes"]] == ["PostgreSQL"]  # MongoDB's past spike is reused
    spike = plan["spikes"][0]
    assert ".horizon/spikes/" in spike["build"] and spike["budget_minutes"] == settings.spike_budget_minutes
    assert plan_consequences(first, "s", {**past, "PostgreSQL": past["MongoDB"]}, settings,
                             can_roll_back=False)["mode"] == "memory"


def test_try_and_rollback_when_every_close_option_is_cheap_to_undo(settings):
    cheap = FakeScorer(**CLOSE, reversible=(0.9, 0.8))
    first = first_pass(STATE, OPTS, cheap, settings)
    assert plan_consequences(first, "s", {}, settings, can_roll_back=True)["mode"] == "try_and_rollback"
    # Not without a checkpoint to roll back to, and never when the stakes are high.
    assert plan_consequences(first, "s", {}, settings, can_roll_back=False)["mode"] == "spikes"
    risky = first_pass(STATE, OPTS, FakeScorer(**CLOSE, reversible=(0.9, 0.8), high_stakes=0.9), settings)
    assert plan_consequences(risky, "s", {}, settings, can_roll_back=True)["mode"] == "spikes"


# --- pass 2 --------------------------------------------------------------------------

def test_consequences_as_evidence_can_settle_a_close_call(settings):
    scorer = FakeScorer(**CLOSE, second={"success": (0.9, 0.3), "breaks_tests": (0.05, 0.8)})
    evidence = {"PostgreSQL": {"spike": {"ran": True, "passed": True, "tests_passed": 5, "tests_failed": 0}}}
    r = decide(OPTS, scorer, settings, evidence)
    assert r["decision"] == "clear_winner" and r["chosen"] == "PostgreSQL"
    state = scorer.calls[-1][0]
    assert state["consequences"][0]["spike"]["tests_passed"] == 5
    assert state["consequences"][1] == {"option": "MongoDB", "tested": False}
    row = next(o for o in r["options"] if o["label"] == "PostgreSQL")
    assert row["dimensions"]["no_regressions"] == 0.95


def test_a_failed_spike_or_static_check_eliminates_the_option(settings):
    scorer = FakeScorer(**CLOSE, second={"success": (0.9, 0.9)})
    evidence = {"PostgreSQL": {"static_checks": [{"name": "dry-run install", "passed": False}]},
                "MongoDB": {"spike": {"ran": True, "passed": True}}}
    r = decide(OPTS, scorer, settings, evidence)
    assert r["decision"] == "clear_winner" and r["chosen"] == "MongoDB" and r["eliminated"] == ["PostgreSQL"]
    both_fail = {"PostgreSQL": {"spike": {"ran": True, "passed": False}},
                 "MongoDB": {"spike": {"ran": True, "passed": False}}}
    assert decide(OPTS, scorer, settings, both_fail)["decision"] in ("close_call", "clear_winner")  # never stuck


def test_spike_metrics_replace_the_estimates(settings):
    opts = [Option("A", est_latency_ms=10), Option("B", est_latency_ms=10)]
    evidence = {"A": {"spike": {"ran": True, "passed": True, "metrics": {"latency_ms": 50}}},
                "B": {"spike": {"ran": True, "passed": True, "metrics": {"latency_ms": 5}}}}
    r = decide(opts, FakeScorer(**CLOSE), dataclasses.replace(settings, decision_weights={"latency_ms": 1.0}),
               evidence)
    assert r["chosen"] == "B" and r["decision"] == "clear_winner"


def test_a_second_tie_does_not_loop(settings):
    opts = [Option("A", est_cost_usd=5.0), Option("B", est_cost_usd=1.0)]
    weights = dataclasses.replace(settings, decision_weights={"success": 1.0})
    r = decide(opts, FakeScorer(**CLOSE), weights)
    assert r["decision"] == "close_call" and r["chosen"] == "B" and "cheaper" in r["reason"]
    r = decide(OPTS, FakeScorer(**CLOSE, reversible=(0.2, 0.9)), settings)
    assert r["decision"] == "close_call" and r["chosen"] == "MongoDB" and "reversible" in r["reason"]
    r = decide(OPTS, FakeScorer(**CLOSE, high_stakes=0.9), settings)
    assert r["decision"] == "ask_human" and r["chosen"] is None
    # A clear winner doesn't need a human even when stakes are high.
    assert first_pass(STATE, OPTS, FakeScorer(high_stakes=0.9), settings)["decision"] == "clear_winner"


# --- through the service -------------------------------------------------------------

def test_evaluation_feeds_memory_evidence_and_the_scorer_prediction_is_recorded(settings, task_store, memrouter,
                                                                               project_dir):
    scorer = FakeScorer()
    platform = with_scorer(settings, task_store, memrouter, project_dir, scorer)
    task_id = platform.start_task("Add persistence", constraints=["team knows SQL"])["task_id"]
    memrouter.record(team_id="local", situation="choose a database for the service", chosen="PostgreSQL",
                     actual=Actual(success=1.0))

    r = platform.evaluate_options(task_id, "choose a database for the service", OPTS)
    assert r["decision"] == "clear_winner" and r["chosen"] == "PostgreSQL" and r["memory_status"] == "ok"
    state = scorer.calls[0][0]
    assert state["goal"] == "Add persistence" and state["constraints"] == ["team knows SQL"]
    assert state["past_outcomes"][0]["chosen"] == "PostgreSQL"

    out = platform.record_outcome(task_id, "choose a database", "postgresql", success=1.0,
                                  predicted_success=0.95)  # the host's own guess loses to Jev's for this option
    ep = memrouter.store.get(out["episode_id"], "local")
    assert (ep.predicted.success, ep.predicted.source) == (0.8, "jev")  # §9: record what Jev predicted
    other = platform.record_outcome(task_id, "s", "SQLite", success=1.0)  # a different option: not Jev's
    assert memrouter.store.get(other["episode_id"], "local").predicted.source == "host"


def test_severe_past_failures_make_a_close_call_high_stakes(settings, task_store, memrouter, project_dir):
    memrouter.record(team_id="local", situation="choose a migration strategy", chosen="drop and recreate",
                     actual=Actual(success=0.0), severity="severe")
    platform = with_scorer(settings, task_store, memrouter, project_dir, FakeScorer(**CLOSE))
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, "choose a migration strategy",
                                  [Option("drop and recreate"), Option("online migration")])
    assert r["decision"] == "check_consequences"  # §4: check first, ask only if still tied
    r = platform.submit_consequences(task_id, r["decision_id"], [])
    assert r["decision"] == "ask_human" and "severe" in r["reason"]


def test_missing_credentials_degrade_to_unscored(settings, task_store, memrouter, project_dir):
    def no_key():
        raise RuntimeError("TYPESAFE_API_KEY is not set")

    platform = Platform(settings, task_store, lambda: memrouter, cwd=project_dir, scorer_factory=no_key)
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, "s", OPTS)
    assert r["decision"] == "unscored" and r["scorer_status"] == "unavailable"
    with pytest.raises(ToolInputError):
        platform.evaluate_options(task_id, "s", [])


# --- the real SDKs, over a mocked transport (no network) ------------------------------

def test_jev_scorer_request_and_parsing():
    TypeSafeClient = pytest.importorskip("typesafe_sdk").TypeSafeClient

    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen.update(body=body, auth=request.headers["authorization"], url=str(request.url))
        answers = {qid: ({"type": "noul", "noul": 0.9} if q["type"] == "noul" else
                         {"type": "score", "score": 3.0, "confidence": 0.8,
                          "legend": {str(i): c for i, c in enumerate(q["criteria"])},
                          "probabilities": {str(i): 1.0 if i == 3 else 0.0 for i in range(len(q["criteria"]))}})
                   for qid, q in body["questions"].items()}
        return httpx2.Response(200, json={"model": "jev-1.13.0", "answers": answers,
                                          "usage": {"input_tokens": 10, "output_tokens": 5}})

    client = TypeSafeClient(api_key="test-key", model="jev-latest", transport=httpx2.MockTransport(handler))
    answers = JevScorer(client=client).ask({"options": [{}]}, build_questions(1))
    assert seen["url"].endswith("/v1/systemone") and seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "jev-latest" and seen["body"]["questions"]["o0.success"]["type"] == "noul"
    assert answers["o0.success"] == Answer(0.9)
    assert answers["o0.compatibility"] == Answer(0.75, 0.8)  # level 3 of 0-4, normalised


def test_llm_scorer_request_and_parsing():
    anthropic = pytest.importorskip("anthropic")

    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen["body"] = body
        props = body["output_config"]["format"]["schema"]["properties"]
        text = json.dumps({k: (2.0 if i == 1 else 0.6) for i, k in enumerate(props)})
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}})

    client = anthropic.Anthropic(api_key="test-key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    qs = {"a": {"type": "noul", "instructions": "yes?"},
          "b": {"type": "score", "instructions": "how much?", "criteria": ["none", "some", "a lot"]}}
    answers = LLMScorer(client=client).ask({"x": 1}, qs)
    assert seen["body"]["model"] == "claude-haiku-4-5"
    assert answers == {"a": Answer(0.6), "b": Answer(1.0)}  # level 2 of 0-2


def test_after_evidence_a_large_lead_stands_despite_low_confidence(settings):
    scorer = FakeScorer(**CLOSE, confidence=0.3, second={"success": (0.9, 0.3), "breaks_tests": (0.05, 0.7)})
    assert first_pass(STATE, OPTS, scorer, settings)["decision"] == "close"  # pass 1: low confidence -> check
    r = decide(OPTS, scorer, settings, {"PostgreSQL": {"spike": {"ran": True, "passed": True}}})
    assert r["decision"] == "clear_winner" and r["margin"] >= 2 * settings.clear_margin
    small = FakeScorer(**CLOSE, confidence=0.3, second={"success": (0.75, 0.6)})  # lead < 2x margin: still close
    assert decide(OPTS, small, settings, {})["decision"] == "close_call"


def test_a_measured_scorer_bias_is_corrected_but_the_raw_prediction_is_recorded(settings, task_store, memrouter,
                                                                                 project_dir):
    for _ in range(20):  # Jev has been 0.3 too optimistic over 20 outcomes
        memrouter.graph.update_predictor("local", "jev", "general", 0.8, 0.5)
    platform = with_scorer(settings, task_store, memrouter, project_dir, FakeScorer())
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, "choose a database", OPTS)
    assert r["calibration_bias"] == pytest.approx(0.3)
    pg = next(o for o in r["options"] if o["label"] == "PostgreSQL")
    assert pg["dimensions"]["success"] == pytest.approx(0.5) and pg["success_raw"] == 0.8
    out = platform.record_outcome(task_id, "choose a database", "PostgreSQL", success=1.0)
    assert memrouter.store.get(out["episode_id"], "local").predicted.success == 0.8  # raw, for trust


def test_compare_scorers(settings, task_store, memrouter, project_dir):
    from horizon.decision.compare import compare

    platform = with_scorer(settings, task_store, memrouter, project_dir, FakeScorer())
    task_id = platform.start_task("x")["task_id"]
    platform.evaluate_options(task_id, "choose a database", OPTS)
    platform.record_outcome(task_id, "choose a database", "PostgreSQL", success=1.0)

    optimist, pessimist = FakeScorer(success=(0.9, 0.5)), FakeScorer(success=(0.2, 0.9), fit=(0.2, 0.9))
    optimist.name, pessimist.name = "jev", "llm"
    report = compare(platform.decisions, "local", {"jev": optimist, "llm": pessimist}, settings)
    assert report["decisions"] == 1 and report["top_choice_agreement"] == 0.0
    assert report["scorers"]["jev"]["brier"] == pytest.approx((0.9 - 1.0) ** 2)
    assert report["scorers"]["llm"]["brier"] == pytest.approx((0.2 - 1.0) ** 2)  # the worse predictor, measurably

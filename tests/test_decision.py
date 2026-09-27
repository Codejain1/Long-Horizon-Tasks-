"""Phase 4: the decision layer (PROJECT.md §5)."""

import dataclasses
import json

import httpx2
import pytest

from horizon.decision.layer import Option, build_questions, evaluate, measured
from horizon.decision.scorers import Answer, JevScorer, LLMScorer
from horizon.models import Actual
from horizon.service import Platform, ToolInputError


class FakeScorer:
    """Scripted answers: `crucial`, `high_stakes`, and per option success / fit / reversible."""

    name = "jev"

    def __init__(self, crucial=0.9, high_stakes=0.1, success=(0.8, 0.5), fit=(0.75, 0.5), reversible=(0.5, 0.5),
                 confidence=0.9):
        self.crucial, self.high_stakes = crucial, high_stakes
        self.success, self.fit, self.reversible, self.confidence = success, fit, reversible, confidence
        self.calls = []

    def ask(self, state, questions):
        self.calls.append((state, questions))
        out = {f"crucial.{s}": Answer(self.crucial) for s in ("irreversible", "many_steps", "real_cost")}
        out["high_stakes"] = Answer(self.high_stakes)
        for i in range(len(state["options"])):
            out[f"o{i}.success"] = Answer(self.success[i])
            out[f"o{i}.compatibility"] = Answer(self.fit[i], self.confidence)
            out[f"o{i}.architecture_fit"] = Answer(self.fit[i], self.confidence)
            out[f"o{i}.reversible"] = Answer(self.reversible[i])
        assert set(out) == set(questions)  # answers exactly the questions asked
        return out


OPTS = [Option("PostgreSQL", "relational, already used by the team"), Option("MongoDB", "document store")]


def with_scorer(settings, task_store, memrouter, project_dir, scorer):
    return Platform(settings, task_store, lambda: memrouter, cwd=project_dir, scorer_factory=lambda: scorer)


# --- rules (pure) ------------------------------------------------------------------

def test_questions_cover_detection_stakes_and_every_option():
    qs = build_questions(3)
    assert {"crucial.irreversible", "crucial.many_steps", "crucial.real_cost", "high_stakes"} <= set(qs)
    assert {f"o2.{d}" for d in ("success", "compatibility", "architecture_fit", "reversible")} <= set(qs)
    assert all(len(q["criteria"]) == 5 for q in qs.values() if q["type"] == "score")


def test_measured_dimensions_are_relative_and_need_every_estimate():
    opts = [Option("a", est_cost_usd=1.0), Option("b", est_cost_usd=4.0)]
    assert measured(opts, "cost_usd") == [1.0, 0.25]
    assert measured(opts, "tokens") is None  # no estimates: dimension left out, weights renormalised


def test_routine_decisions_skip_the_full_flow(settings):
    r = evaluate({"options": [{}, {}]}, OPTS, FakeScorer(crucial=0.2), settings)
    assert r["decision"] == "routine" and r["chosen"] == "PostgreSQL" and r["crucial"] is False


def test_clear_winner(settings):
    r = evaluate({"options": [{}, {}]}, OPTS, FakeScorer(), settings)
    assert r["decision"] == "clear_winner" and r["chosen"] == "PostgreSQL"
    assert r["predicted_success"] == 0.8 and r["margin"] >= settings.clear_margin


def test_low_confidence_is_never_a_clear_winner(settings):
    r = evaluate({"options": [{}, {}]}, OPTS, FakeScorer(confidence=0.3), settings)
    assert r["decision"] == "close_call"


def test_close_call_takes_the_cheaper_option(settings):
    opts = [Option("A", est_cost_usd=5.0), Option("B", est_cost_usd=1.0)]
    scorer = FakeScorer(success=(0.7, 0.68), fit=(0.6, 0.6))
    r = evaluate({"options": [{}, {}]}, opts, scorer, dataclasses.replace(settings, decision_weights={
        "success": 1.0}))  # cost left out of the composite, so the tie is on judgement alone
    assert r["decision"] == "close_call" and r["chosen"] == "B" and "cheaper" in r["reason"]


def test_close_call_without_costs_takes_the_more_reversible_option(settings):
    scorer = FakeScorer(success=(0.7, 0.69), fit=(0.6, 0.6), reversible=(0.2, 0.9))
    r = evaluate({"options": [{}, {}]}, OPTS, scorer, settings)
    assert r["decision"] == "close_call" and r["chosen"] == "MongoDB" and "reversible" in r["reason"]


def test_high_stakes_close_call_asks_a_human(settings):
    scorer = FakeScorer(success=(0.7, 0.69), fit=(0.6, 0.6), high_stakes=0.9)
    r = evaluate({"options": [{}, {}]}, OPTS, scorer, settings)
    assert r["decision"] == "ask_human" and r["chosen"] is None
    # A clear winner doesn't need a human even when stakes are high.
    assert evaluate({"options": [{}, {}]}, OPTS, FakeScorer(high_stakes=0.9), settings)["decision"] == "clear_winner"


def test_no_scorer_ranks_on_estimates_only(settings):
    opts = [Option("A", est_cost_usd=5.0, est_tokens=100), Option("B", est_cost_usd=1.0, est_tokens=100)]
    r = evaluate({"options": [{}, {}]}, opts, None, settings)
    assert r["decision"] == "unscored" and r["chosen"] is None and r["scorer_status"] == "not_configured"
    assert r["cheapest_by_estimates"] == ["B", "A"]  # information, not a recommendation
    assert evaluate({"options": [{}, {}]}, OPTS, None, settings)["cheapest_by_estimates"] == []


def test_a_failing_scorer_never_blocks(settings):
    class Down:
        name = "jev"

        def ask(self, state, questions):
            raise ConnectionError("529 overloaded")

    r = evaluate({"options": [{}, {}]}, OPTS, Down(), settings)
    assert r["decision"] == "unscored" and r["scorer_status"] == "unavailable"


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

    out = platform.record_outcome(task_id, "choose a database", "postgresql", success=1.0)
    ep = memrouter.store.get(out["episode_id"], "local")
    assert (ep.predicted.success, ep.predicted.source) == (0.8, "jev")  # §9: record what Jev predicted
    other = platform.record_outcome(task_id, "s", "SQLite", success=1.0)  # a different option: not Jev's
    assert memrouter.store.get(other["episode_id"], "local").predicted.source == "host"


def test_severe_past_failures_make_a_close_call_high_stakes(settings, task_store, memrouter, project_dir):
    memrouter.record(team_id="local", situation="choose a migration strategy", chosen="drop and recreate",
                     actual=Actual(success=0.0), severity="severe")
    platform = with_scorer(settings, task_store, memrouter, project_dir,
                           FakeScorer(success=(0.7, 0.69), fit=(0.6, 0.6)))
    task_id = platform.start_task("x")["task_id"]
    r = platform.evaluate_options(task_id, "choose a migration strategy",
                                  [Option("drop and recreate"), Option("online migration")])
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

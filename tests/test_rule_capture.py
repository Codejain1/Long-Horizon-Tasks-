"""Rule capture can't silently drop rules again (2026-10-04: a sharper question, checked one sentence at a time,
dropped continuation sentences inside real tickets and ward fell from 11/11 to 7/11 rules kept)."""

import os
import sys
from pathlib import Path

import pytest

from horizon.decision.scorers import Answer
from horizon.hooks import rule_questions, split_rules

RULES_EVAL = Path(__file__).resolve().parents[1] / "evals" / "rules"


class Recorder:
    def __init__(self):
        self.states = []

    def ask(self, state, questions):
        self.states.append(state)
        return {q: Answer(0.1) for q in questions}


def test_jev_reads_each_sentence_in_the_whole_message():
    text = "For later: limits are per rolling 24 hours. For patients under 50 kg, that limit is scaled by weight."
    r = Recorder()
    split_rules(text, r)
    [state] = r.states
    assert state["message"] == text and len(state["sentences"]) == 2
    assert "context of the whole `message`" in str(rule_questions(1)["project_0"]["instructions"])


@pytest.mark.skipif(os.environ.get("HORIZON_TEST_LIVE_JEV") != "1",
                    reason="live Jev: set HORIZON_TEST_LIVE_JEV=1 (and a TypeSafe key) after changing rule capture")
def test_every_scenario_rule_is_kept_from_its_ticket_live():
    from horizon.decision.scorers import JevScorer

    sys.path.insert(0, str(RULES_EVAL))
    import tickets

    r = tickets.evaluate(JevScorer())
    assert r["missed"] == 0, r["misses"]
    assert r["noise"] <= 10, f"{r['noise']} non-rule sentences kept (was 4-6)"

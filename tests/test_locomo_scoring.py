import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "evals" / "memory"))

from locomo_scoring import f1_score, score_locomo  # noqa: E402


def test_f1_exact_overlap():
    assert f1_score("7 May 2023", "7 May 2023") == 1.0


def test_category_five_adversarial():
    ok, val = score_locomo("No information available in the context.", "anything", 5)
    assert ok and val == 1.0
    ok2, _ = score_locomo("Caroline went yesterday.", "anything", 5)
    assert not ok2


def test_category_five_reads_option_letters():
    from locomo import NOT_MENTIONED, cat5_question, read_option

    q, opts = cat5_question({"id": "s:q", "question": "What did C realise?", "adversarial_answer": "self-care"})
    assert NOT_MENTIONED in q and "self-care" in q
    letter = next(k for k, v in opts.items() if v == NOT_MENTIONED)
    assert score_locomo(read_option(f"({letter})", opts), "", 5)[0]
    assert not score_locomo(read_option("self-care is key", opts), "", 5)[0]


def test_beam_sessions_keep_conversation_order():
    from beam import beam_sessions

    chat = [[{"role": "user", "content": "a", "time_anchor": "March-15-2024"}, {"role": "assistant", "content": "b"}],
            [{"role": "user", "content": "c", "time_anchor": "April-05-2024"}, {"role": "user", "content": "d"}]]
    s = beam_sessions(chat)
    assert [d for d, _ in s] == ["March-15-2024", "April-05-2024"] and len(s[0][1]) == 2

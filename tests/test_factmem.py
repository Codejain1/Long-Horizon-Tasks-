"""General fact memory: rounds stored with dates, found by meaning and words, judged by the scorer."""

from horizon.decision.scorers import Answer
from horizon.factmem import FactMemory
from horizon.memrouter.embedding import make_embedder


def memory():
    m = FactMemory(make_embedder("hash"))
    m.add_session("s1", "2023/01/02", [{"role": "user", "content": "I adopted a beagle named Max."},
                                       {"role": "assistant", "content": "Congratulations on Max!"},
                                       {"role": "user", "content": "Any tips for training?"},
                                       {"role": "assistant", "content": "Use short sessions."}])
    m.add_session("s2", "2023/03/09", [{"role": "user", "content": "We moved Max to agility classes on Tuesdays."},
                                       {"role": "assistant", "content": "Sounds fun."}])
    m.add_session("s3", "2023/04/01", [{"role": "user", "content": "What's a good pasta recipe?"},
                                       {"role": "assistant", "content": "Try cacio e pepe."}])
    return m


class Judge:
    """A stand-in for Jev: relevant when the memory mentions Max."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def ask(self, state, questions):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return {f"m{j}": Answer(0.9 if "Max" in m["text"] else 0.05) for j, m in enumerate(state["memories"])}


def test_rounds_are_stored_with_their_dates():
    m = memory()
    assert [u.date for u in m.units] == ["2023/01/02", "2023/01/02", "2023/03/09", "2023/04/01"]
    assert m.units[0].text.startswith("user: I adopted a beagle named Max.\nassistant: Congratulations")


def test_words_find_what_meaning_might_miss():
    m = memory()
    assert m.units[2].id in m.candidates("agility", k=1)


def test_the_judge_keeps_only_relevant_memories_oldest_first():
    hits = memory().search("What is my dog's name and what class does he take?", Judge(), k=10)
    assert [h.unit.session for h in hits] == ["s1", "s2"] and all(h.relevance == 0.9 for h in hits)


def test_a_failing_judge_falls_back_to_plain_retrieval():
    hits = memory().search("Max", Judge(fail=True), k=10, keep=2)
    assert len(hits) == 2 and all(h.relevance == 1.0 for h in hits)

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


def test_counting_questions_keep_every_relevant_mention():
    m = FactMemory(make_embedder("hash"))
    for i in range(20):
        m.add_session(f"s{i}", f"2023/01/{i + 1:02d}", [{"role": "user", "content": f"I baked Max a cake, number {i}."}])
    assert len(m.search("What did I bake for Max?", Judge(), k=30)) == 12
    assert len(m.search("How many times did I bake for Max?", Judge(), k=30)) == 20


class Replaces:
    """A stand-in for Jev: the newer statement replaces the older when both name the same subject."""

    def ask(self, state, questions):
        if "pairs" in state:
            return {f"p{j}": Answer(0.9 if p["older"].split(" is ")[0] == p["newer"].split(" is ")[0] else 0.1)
                    for j, p in enumerate(state["pairs"])}
        return {f"m{j}": Answer(0.9) for j in range(len(state["memories"]))}


def test_a_newer_fact_supersedes_the_old_one_and_hops_follow_names():
    m = FactMemory(make_embedder("hash"))
    n = m.add_facts(["The author of Bleak House is Charles Dickens.", "The spouse of Charles Dickens is Catherine Hogarth.",
                     "The author of Bleak House is Wilkie Collins.", "The spouse of Wilkie Collins is Caroline Graves."],
                    scorer=Replaces(), similar=0.0)
    assert n == 1 and m.units[0].superseded_by == 2 and m.units[1].superseded_by is None
    texts = [h.unit.text for h in m.search("Who is the spouse of the author of Bleak House?", Replaces(), k=2,
                                           current_only=True, hops=2)]
    assert "The author of Bleak House is Charles Dickens." not in texts
    assert "The spouse of Wilkie Collins is Caroline Graves." in texts


def test_memories_come_back_in_date_order_whatever_the_date_format():
    # Sorted as text, "April" came before "March" (BEAM) and "10:37 am on 27 June" before "1:56 pm on 8 May" (LoCoMo).
    for dates in (["March-15-2024", "April-02-2024", "May-20-2024"],
                  ["1:56 pm on 8 May, 2023", "1:14 pm on 25 May, 2023", "10:37 am on 27 June, 2023"],
                  ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 09:00", "2023/06/01 (Thu) 10:00"]):
        m = FactMemory(make_embedder("hash"))
        for d in reversed(dates):  # added newest first, so the order can only come from the dates
            m.add_session(d, d, [{"role": "user", "content": f"Max went to the vet on {d}."}])
        assert [h.unit.date for h in m.search("Max vet", k=10)] == dates


def test_unreadable_dates_keep_the_order_sessions_were_added_in():
    m = FactMemory(make_embedder("hash"))
    for d in ["session 2", "session 1", "session 10"]:
        m.add_session(d, d, [{"role": "user", "content": "Max ate."}])
    assert [h.unit.date for h in m.search("Max", k=10)] == ["session 2", "session 1", "session 10"]


def test_a_failing_judge_still_leaves_out_superseded_facts():
    m = FactMemory(make_embedder("hash"))
    m.add_facts(["The capital of Freedonia is Ruritania.", "The capital of Freedonia is Elbonia."],
                scorer=Replaces(), similar=0.0)
    texts = [h.unit.text for h in m.search("capital of Freedonia", Judge(fail=True), current_only=True)]
    assert texts == ["The capital of Freedonia is Elbonia."]

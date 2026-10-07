"""General fact memory: everything said in past conversations, found again when a question needs it.

Lean memory keeps rules; this keeps the rest (what the user said, did, owned, preferred; what the assistant
answered), for general agents and for the public memory benchmarks (docs/MEMORY_BENCHMARKS.md).

- Write: each round (a user turn with the assistant's reply) is stored with its session date, as LongMemEval's
  authors found rounds to be the best unit. Nothing is dropped at write time.
- Read: candidates by meaning (embeddings) and by words (BM25), merged; then Jev judges, per candidate, whether
  it holds information needed for the question (one request); the most relevant are returned oldest first, so the
  latest version of a fact is last, and the best relevance score says whether memory knows anything at all
  (abstention).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

MAX_UNIT = 3000  # characters of one round kept (long assistant answers are clipped)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("a an the and or but if of to in on at for with by from as is are was were be been it this that "
            "these those i you he she we they me my your our their do did does have has had what which who when "
            "where how why can could would should will just so not no yes".split())


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


@dataclass
class Unit:
    id: int
    session: str
    date: str
    text: str


@dataclass
class Hit:
    unit: Unit
    relevance: float  # Jev's probability that it's needed for the question (or the retrieval score, unscored)


class FactMemory:
    """One scope's memory (a user, a project). In-memory index; `units` can be persisted by the caller."""

    def __init__(self, embedder):
        self.embedder = embedder
        self.units: list[Unit] = []
        self._vecs: list[np.ndarray] = []
        self._tf: list[Counter] = []
        self._df: Counter = Counter()

    def add_session(self, session: str, date: str, turns: list[dict]) -> None:
        rounds, current = [], []
        for t in turns:
            if t.get("role") == "user" and current:
                rounds.append(current)
                current = []
            current.append(t)
        if current:
            rounds.append(current)
        texts = ["\n".join(f"{t.get('role', 'user')}: {t.get('content', '')}" for t in r)[:MAX_UNIT] for r in rounds]
        if not texts:
            return
        keys = [" ".join(t.get("content", "") for t in r if t.get("role") == "user")[:1000] or texts[i][:1000]
                for i, r in enumerate(rounds)]
        vecs = self.embedder.embed(keys)
        for text, vec in zip(texts, vecs):
            self.units.append(Unit(len(self.units), session, date, text))
            self._vecs.append(np.asarray(vec, dtype=np.float32))
            tf = Counter(_words(text))
            self._tf.append(tf)
            self._df.update(tf.keys())

    def _bm25(self, query: str) -> np.ndarray:
        n = len(self.units)
        avg = sum(sum(tf.values()) for tf in self._tf) / max(n, 1)
        q = _words(query)
        scores = np.zeros(n, dtype=np.float32)
        for w in set(q):
            df = self._df.get(w, 0)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self._tf):
                f = tf.get(w, 0)
                if f:
                    length = sum(tf.values())
                    scores[i] += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * length / avg))
        return scores

    def candidates(self, query: str, k: int = 30) -> list[int]:
        """Up to 2k unit ids: the top k by meaning and the top k by words, best first, without repeats."""
        if not self.units:
            return []
        qv = np.asarray(self.embedder.embed([query])[0], dtype=np.float32)
        m = np.stack(self._vecs)
        sem = m @ qv / (np.linalg.norm(m, axis=1) * np.linalg.norm(qv) + 1e-9)
        lex = self._bm25(query)
        out: list[int] = []
        for order in (np.argsort(-sem)[:k], np.argsort(-lex)[:k]):
            for i in order:
                if int(i) not in out:
                    out.append(int(i))
        return out

    def search(self, query: str, scorer=None, k: int = 30, keep: int = 12, floor: float = 0.15,
               date: str | None = None) -> list[Hit]:
        """The units most relevant to `query`, oldest first. With a scorer (Jev), each candidate is judged in one
        request; otherwise the top by meaning are kept as they are (plain retrieval)."""
        ids = self.candidates(query, k)
        if scorer is None or not ids:
            return sorted((Hit(self.units[i], 1.0) for i in ids[:keep]), key=lambda h: (h.unit.date, h.unit.id))
        from horizon.decision.scorers import noul

        state = {"question": query, "asked_on": date or "", "memories": [
            {"date": self.units[i].date, "text": self.units[i].text[:1500]} for i in ids]}
        questions = {f"m{j}": noul(f"Does `memories[{j}]` contain information needed to answer `question` (asked "
                                   f"on `asked_on`): a fact, event, date, number, preference or earlier answer "
                                   f"the answer depends on, including an older or newer version of it?")
                     for j in range(len(ids))}
        try:
            a = scorer.ask(state, questions)
        except Exception:  # Jev down: fall back to plain retrieval
            return self.search(query, None, k, keep, floor, date)
        scored = sorted(((a[f"m{j}"].value, i) for j, i in enumerate(ids)), reverse=True)
        hits = [Hit(self.units[i], round(v, 3)) for v, i in scored[:keep] if v >= floor]
        return sorted(hits, key=lambda h: (h.unit.date, h.unit.id))

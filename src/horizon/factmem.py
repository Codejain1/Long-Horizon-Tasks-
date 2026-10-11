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
from datetime import datetime

import numpy as np

MAX_UNIT = 3000  # characters of one round kept (long assistant answers are clipped)
_WORD = re.compile(r"[a-z0-9]+")
_NAMES = re.compile(r"\b[A-Z][\w.'-]*(?:\s+(?:of|de|la|von|van|the)?\s*[A-Z][\w.'-]*)*")
# A question that counts or totals things said across conversations needs every mention, not the top few: on
# LongMemEval all four of Horizon's misses were "how many ..." questions with the cap at 12.
_AGGREGATE = re.compile(r"\b(how many|how much|in total|total|number of|count|altogether|sum)\b", re.I)
# Session dates as the benchmarks write them (LongMemEval, LoCoMo, BEAM). Sorted as text they misorder: "April"
# before "March", "10:37 am on 27 June" before "1:56 pm on 8 May".
_DATE_FORMATS = ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%I:%M %p on %d %B, %Y", "%B-%d-%Y")
_STOP = set("a an the and or but if of to in on at for with by from as is are was were be been it this that "
            "these those i you he she we they me my your our their do did does have has had what which who when "
            "where how why can could would should will just so not no yes".split())


def _when(date: str) -> float | None:
    s = " ".join(re.sub(r"\(\w+\)", " ", date).split())  # "2023/05/20 (Sat) 02:21"
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    return None


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


@dataclass
class Unit:
    id: int
    session: str
    date: str
    text: str
    superseded_by: int | None = None  # a newer unit that replaced this one (selective forgetting)
    when: float = 0.0  # the date as a time, for ordering; an unreadable date keeps the order units were added in


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
        when = _when(date)
        if when is None:
            when = self.units[-1].when if self.units else 0.0
        for text, vec in zip(texts, vecs):
            self.units.append(Unit(len(self.units), session, date, text, when=when))
            self._vecs.append(np.asarray(vec, dtype=np.float32))
            tf = Counter(_words(text))
            self._tf.append(tf)
            self._df.update(tf.keys())

    def add_facts(self, facts: list[str], session: str = "facts", scorer=None, similar: float = 0.8,
                  batch: int = 40) -> int:
        """Stored in order, each as its own unit (its position is its date). With a scorer, a new fact that
        replaces a stored one (same subject and relation, a new value) marks it superseded: it stays for history
        but is no longer served as current. Returns how many were superseded."""
        from horizon.decision.scorers import noul

        start = len(self.units)
        vecs = self.embedder.embed(facts)
        pending = []  # (new id, old id) pairs that look alike, checked by the scorer in batches
        for k, (text, vec) in enumerate(zip(facts, vecs)):
            v = np.asarray(vec, dtype=np.float32)
            if scorer is not None and self._vecs:
                m = np.stack(self._vecs)
                sim = m @ v / (np.linalg.norm(m, axis=1) * np.linalg.norm(v) + 1e-9)
                pending += [(start + k, int(j)) for j in np.argsort(-sim)[:3] if sim[j] >= similar
                            and self.units[int(j)].superseded_by is None]
            self.units.append(Unit(start + k, session, f"{start + k:08d}", text,
                                   when=self.units[-1].when if self.units else 0.0))
            self._vecs.append(v)
            tf = Counter(_words(text))
            self._tf.append(tf)
            self._df.update(tf.keys())
        replaced = 0
        for b in range(0, len(pending), batch):
            chunk = pending[b:b + batch]
            state = {"pairs": [{"older": self.units[o].text, "newer": self.units[n].text} for n, o in chunk]}
            qs = {f"p{j}": noul(f"In `pairs[{j}]`, does the newer statement replace the older one: the same "
                                f"subject and the same relation or attribute, with a different value, so the "
                                f"older one is no longer true?") for j in range(len(chunk))}
            try:
                a = scorer.ask(state, qs)
            except Exception:
                continue
            for j, (n, o) in enumerate(chunk):
                if a[f"p{j}"].value >= 0.5 and self.units[o].superseded_by is None:
                    self.units[o].superseded_by = n
                    replaced += 1
        return replaced

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
               date: str | None = None, current_only: bool = False, hops: int = 1) -> list[Hit]:
        """The units most relevant to `query`, oldest first. With a scorer (Jev), each candidate is judged in one
        request; otherwise the top by meaning are kept as they are (plain retrieval). `current_only` leaves out
        superseded units; `hops` > 1 also searches for the names in what the first search found (the author of X,
        then that author's spouse)."""
        ids = [i for i in self.candidates(query, k) if not (current_only and self.units[i].superseded_by is not None)]
        for _ in range(hops - 1):
            names = {n for i in ids[:10] for n in _NAMES.findall(self.units[i].text)} - set(_NAMES.findall(query))
            more = [i for n in list(names)[:12] for i in self.candidates(n, 5)
                    if not (current_only and self.units[i].superseded_by is not None)]
            ids += [i for i in more if i not in ids]
        if scorer is None or not ids:
            return sorted((Hit(self.units[i], 1.0) for i in ids[:keep]), key=lambda h: (h.unit.when, h.unit.id))
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
            return self.search(query, None, k, keep, floor, date, current_only, hops)
        scored = sorted(((a[f"m{j}"].value, i) for j, i in enumerate(ids)), reverse=True)
        if _AGGREGATE.search(query):
            keep, floor = max(keep, 30), max(floor, 0.3)  # every relevant mention, still filtered
        hits = [Hit(self.units[i], round(v, 3)) for v, i in scored[:keep] if v >= floor]
        return sorted(hits, key=lambda h: (h.unit.when, h.unit.id))

"""Fast recall: a self-built LoCoMo retrieval engine, graded on evidence turns, timed per query.

    PYTHONPATH=src python evals/memory/fast_recall.py --tier keywords   # pure self-built: no models
    PYTHONPATH=src python evals/memory/fast_recall.py --tier hybrid     # + local bge-small embeddings
    PYTHONPATH=src python evals/memory/fast_recall.py --tune            # grid on questions outside the test 150

No network, no external judge. Everything heavy happens at index time; a query embeds the question (hybrid
tier), scores every turn with a vectorised BM25 over turn and window documents, fuses, boosts the asking
speaker's turns and time-bearing turns for "when" questions, expands neighbours and cuts to a turn budget.
Graded like the judge lab: share of LoCoMo's evidence turns returned, within the same text budget.
Tuning uses only questions outside the 150-question test sample (seed 11), so the test is never tuned on.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

_WORD = re.compile(r"[a-z0-9']+")
_STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being do does did
have has had i me my we our you your he him his she her they them their it its this that these those what which who
whom whose when where why how there here so too very just can could would should will shall may might must not no
yes up out about into over after before than then also all any some more most such only own same other again
""".split())
_TIME = re.compile(r"\b(yesterday|today|tomorrow|tonight|last|next|ago|week|weekend|month|year|morning|evening|"
                   r"january|february|march|april|may|june|july|august|september|october|november|december|"
                   r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}|recently|since)\b")
_WHEN = re.compile(r"^\s*(when|what (date|day|year|month|time)|how long|how many (days|weeks|months|years))", re.I)


def stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def words(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP]


class BM25:
    """Vectorised BM25 over a fixed set of documents: postings as numpy arrays, one query in ~a millisecond."""

    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.75):
        self.n = len(docs)
        lens = np.array([len(d) for d in docs], dtype=np.float32)
        norm = k1 * (1 - b + b * lens / max(lens.mean(), 1.0))
        post: dict[str, tuple[list[int], list[int]]] = {}
        for i, d in enumerate(docs):
            for w, c in Counter(d).items():
                ids, tfs = post.setdefault(w, ([], []))
                ids.append(i)
                tfs.append(c)
        self.post = {}
        for w, (ids, tfs) in post.items():
            ids, tf = np.array(ids), np.array(tfs, dtype=np.float32)
            idf = math.log(1 + (self.n - len(ids) + 0.5) / (len(ids) + 0.5))
            self.post[w] = (ids, idf * tf * (k1 + 1) / (tf + norm[ids]))

    def scores(self, q: list[str]) -> np.ndarray:
        s = np.zeros(self.n, dtype=np.float32)
        for w in set(q):
            if w in self.post:
                ids, contrib = self.post[w]
                s[ids] += contrib
        return s


VEC_CACHE = Path.home() / ".cache" / "horizon-bench" / "fast_recall_vecs"


class Conversation:
    """Index of one LoCoMo conversation: turns (with image captions), windows, speakers, time cues, vectors."""

    def __init__(self, conv: dict, embedder=None):
        self.turns, self.speaker, self.session = [], [], []
        a, b = conv["speaker_a"], conv["speaker_b"]
        self.names = {a.lower(): a, b.lower(): b}
        i = 1
        while f"session_{i}" in conv:
            date = conv.get(f"session_{i}_date_time", "")
            for t in conv[f"session_{i}"]:
                text = t.get("text", "")
                if t.get("blip_caption"):
                    text += f" [shares a photo: {t['blip_caption']}]"
                self.turns.append({"id": t["dia_id"], "speaker": t.get("speaker", ""), "date": date, "text": text})
                self.session.append(i)
            i += 1
        n = len(self.turns)
        single = [words(f"{t['speaker']} {t['text']}") for t in self.turns]
        window = [single[max(0, j - 1)] + single[j] + (single[j + 1] if j + 1 < n else []) for j in range(n)]
        self.words = single
        self.bm_turn, self.bm_win = BM25(single), BM25(window)
        self.timey = np.array([1.0 if _TIME.search(t["text"].lower()) else 0.0 for t in self.turns], dtype=np.float32)
        self.vecs = None
        if embedder is not None:
            cache = VEC_CACHE / f"{conv.get('speaker_a', 'a')}-{conv.get('speaker_b', 'b')}-{n}.npy"
            if cache.exists():
                self.vecs = np.load(cache)
            else:
                v = np.asarray(list(embedder.embed([f"{t['speaker']}: {t['text']}" for t in self.turns])),
                               dtype=np.float32)
                self.vecs = v / np.linalg.norm(v, axis=1, keepdims=True)
                VEC_CACHE.mkdir(parents=True, exist_ok=True)
                np.save(cache, self.vecs)

    def _score(self, q: list[str], p: dict, qvec) -> np.ndarray:
        s = p["w_turn"] * _norm(self.bm_turn.scores(q)) + p["w_win"] * _norm(self.bm_win.scores(q))
        if qvec is not None and self.vecs is not None:
            s += p["w_vec"] * _norm(self.vecs @ qvec)
        return s

    def recall(self, question: str, p: dict, qvec=None) -> list[str]:
        q = words(question)
        s = self._score(q, p, qvec)
        if p.get("prf_k"):  # query expansion: frequent words of the top turns, searched again
            top = np.argsort(-s)[: p["prf_k"]]
            extra = [w for w, _ in Counter(w for j in top for w in self.words[j] if w not in q).most_common(p["prf_terms"])]
            s = s + p["prf_w"] * _norm(self._score(extra, dict(p, w_vec=0.0), None))
        low = question.lower()
        asked = [full for name, full in self.names.items() if name in low]
        if len(asked) == 1:  # a question about one person: their own turns usually hold the answer
            s += p["w_speaker"] * np.array([t["speaker"] == asked[0] for t in self.turns], dtype=np.float32)
        if _WHEN.search(question):
            s += p["w_time"] * self.timey
        order = np.argsort(-s)
        if p.get("per_session"):  # spread seeds over sessions: list and multi-hop answers live in several
            seen: Counter = Counter()
            first, rest = [], []
            for j in order:
                (first if seen[self.session[j]] < p["per_session"] else rest).append(j)
                seen[self.session[j]] += 1
            order = np.array(first + rest)
        picked: list[int] = []
        for j in order[: p["seeds"]]:
            for k in range(j - p["before"], j + p["after"] + 1):
                if 0 <= k < len(self.turns) and k not in picked:
                    picked.append(k)
            if len(picked) >= p["budget"]:
                break
        for j in order:  # fill any remaining budget with the next-best single turns
            if len(picked) >= p["budget"]:
                break
            if j not in picked:
                picked.append(int(j))
        return [self.turns[k]["id"] for k in picked[: p["budget"]]]


def _norm(x: np.ndarray) -> np.ndarray:
    top = float(x.max())
    return x / top if top > 0 else x


DEFAULTS = {"w_turn": 1.0, "w_win": 0.5, "w_vec": 1.0, "w_speaker": 0.1, "w_time": 0.1,
            "seeds": 8, "before": 0, "after": 1, "budget": 22, "prf_k": 0, "prf_terms": 10, "prf_w": 0.3,
            "per_session": 0}


_QVEC: dict[str, np.ndarray] = {}  # tuning re-asks the same questions; timing runs use a cold cache


def evaluate(items, convs, p, embedder, cache=False):
    rows = []
    for it in items:
        conv = convs[it["sample_id"]]
        t = time.perf_counter()
        qvec = None
        if embedder is not None:
            qvec = _QVEC.get(it["question"]) if cache else None
            if qvec is None:
                v = np.asarray(next(iter(embedder.embed([it["question"]]))), dtype=np.float32)
                qvec = v / np.linalg.norm(v)
                if cache:
                    _QVEC[it["question"]] = qvec
        got = set(conv.recall(it["question"], p, qvec))
        secs = time.perf_counter() - t
        ev = {e.strip() for e in it["evidence"] for e in str(e).split(";") if e.strip()}
        if ev:
            rows.append({"id": it["id"], "category": it["category"], "recall": len(ev & got) / len(ev),
                         "all": ev <= got, "secs": secs})
    return rows


def summary(rows) -> str:
    lat = sorted(r["secs"] for r in rows)
    return (f"n={len(rows)} evidence recall {100 * sum(r['recall'] for r in rows) / len(rows):.1f}%  "
            f"all found {100 * sum(r['all'] for r in rows) / len(rows):.1f}%  "
            f"latency p50 {1000 * lat[len(lat) // 2]:.1f} ms p95 {1000 * lat[int(len(lat) * .95)]:.1f} ms")


def main(argv=None) -> int:
    import locomo  # benchmark data: only the CLI needs it, so the engine deploys without it

    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["keywords", "hybrid"], default="hybrid")
    ap.add_argument("--budget", type=int, default=None, help="turns returned per question")
    ap.add_argument("--tune", action="store_true", help="grid-search on questions outside the test 150")
    ap.add_argument("--params", default=None, help="JSON of parameters to use")
    args = ap.parse_args(argv)
    locomo.ensure_data()
    data = json.loads(locomo.DATA.read_text())
    items = locomo.flatten(data)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data for qa in s["qa"]}
    for it in items:
        it["evidence"] = ev.get(it["id"], [])
    test = locomo.sample_items(list(items), 150)
    test_ids = {it["id"] for it in test}
    train = [it for it in items if it["id"] not in test_ids and it["evidence"]]
    embedder = None
    if args.tier == "hybrid":
        from horizon.memrouter.embedding import make_embedder

        embedder = make_embedder("fastembed")
        embedder = getattr(embedder, "_model", embedder)  # the raw fastembed model: embed() yields vectors
    t = time.perf_counter()
    convs = {s["sample_id"]: Conversation(s["conversation"], embedder) for s in data}
    print(f"indexed 10 conversations in {time.perf_counter() - t:.1f}s (one-time, at write)", flush=True)
    p = dict(DEFAULTS, **(json.loads(args.params) if args.params else {}))
    if args.budget:
        p["budget"] = args.budget
    if args.tune:
        grid = {"w_win": [0.5, 1.0], "w_vec": [1.0, 2.0, 3.0] if embedder else [0.0], "w_time": [0.1, 0.3],
                "after": [0, 1], "seeds": [6, 10, 20], "prf_k": [0, 3, 5], "prf_w": [0.2, 0.5],
                "per_session": [0, 1, 2]}
        rng = np.random.default_rng(0)
        sub = [train[i] for i in rng.choice(len(train), size=min(600, len(train)), replace=False)]
        best = None
        for combo in itertools.product(*grid.values()):
            q = dict(p, **dict(zip(grid, combo)))
            rows = evaluate(sub, convs, q, embedder, cache=True)
            score = sum(r["recall"] for r in rows) / len(rows)
            if best is None or score > best[0]:
                best = (score, q)
        p = best[1]
        print(f"tuned on {len(sub)} non-test questions: recall {100 * best[0]:.1f}%  params {json.dumps(p)}")
    rows = evaluate(test, convs, p, embedder)
    print(f"TEST ({args.tier}, budget {p['budget']} turns): {summary(rows)}")
    for cat in sorted({r['category'] for r in rows}):
        sub = [r for r in rows if r["category"] == cat]
        print(f"  category {cat}: recall {100 * sum(r['recall'] for r in sub) / len(sub):5.1f}%  n={len(sub)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

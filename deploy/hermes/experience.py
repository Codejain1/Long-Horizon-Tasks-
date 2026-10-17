"""Experience for memrouter: remember what WORKED, not just what happened (docs/MEMROUTER.md §3-§9, ported 2026-10-16).

The recall engine (memory_service.py) finds what was said. This module learns from outcomes:
- record_episode: what was tried in which situation, predicted vs actual result, conditions; computes SURPRISE
  (signed prediction error, §5) and credits the memories recalled for that decision: better than predicted
  strengthens them, worse weakens them (losses damped by stability), helpful recalls grow stability (§8).
- consolidate ("sleep", §7): similar episodes with the same action become a STRATEGY (>= 80 % success, 3+ runs) or
  a FAILURE LESSON (<= 30 %); duplicates merge; old evidence is archived, never deleted.
- fears (§9): one severe failure creates a strong warning, always shown when relevant, cleared only by the owner.
- reconsolidation (§8): a contradicted lesson first gains a sharper condition; repeated contradictions halve it.
- predictor bias (§5 step 4): how wrong each predictor's success guesses are, corrected after 20 outcomes.
Not ported: spreading activation over links, and the Jev attention filter (our ranker replaces it).
"""

from __future__ import annotations

import json
import math
import time

import numpy as np

LEARNING_RATE = 0.2
SIGNAL_WEIGHT = {"human": 1.0, "auto": 0.7, "implicit": 0.4}
SUCCESS_W, EFFICIENCY_W = 0.6, 0.4
MAX_STABILITY = 20.0
PREDICTOR_MIN = 20
CLUSTER_SIM, MERGE_SIM, FEAR_SIM, PRIOR_SIM = 0.8, 0.85, 0.5, 0.6


def surprise(actual: dict, predicted: dict) -> float:
    """MEMROUTER.md §5: 0.6 x success error + 0.4 x mean efficiency error (+ = better than predicted), in -1..1."""
    s_err = float(actual.get("success", 0)) - float(predicted.get("success", 0.5))
    eff = []
    for k in ("tokens", "cost", "latency"):
        p, a = predicted.get(k), actual.get(k)
        if p in (None, 0) or a is None:
            continue  # no valid ratio for this metric
        eff.append(1.0 if a == 0 else max(-1.0, min(1.0, (p - a) / p)))
    s = SUCCESS_W * s_err + EFFICIENCY_W * (sum(eff) / len(eff)) if eff else s_err
    return max(-1.0, min(1.0, s))


def condition_match(stored: dict, current: dict) -> float:
    """1.0 when every comparable key matches, 0.25 when none do, 0.75 when nothing is comparable (§6 as built)."""
    keys = [k for k in stored if k in current]
    if not keys:
        return 0.75
    hits = sum(str(stored[k]) == str(current[k]) for k in keys)
    return 1.0 if hits == len(keys) else 0.25 if hits == 0 else 0.25 + 0.75 * hits / len(keys)


class Experience:
    def __init__(self, mem):
        self.mem, self.db = mem, mem.db
        self.db.execute("CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY, ts REAL, situation TEXT, "
                        "action TEXT, conditions TEXT, predicted TEXT, actual TEXT, success REAL, surprise REAL, "
                        "signal TEXT, severity TEXT, low_conf INTEGER, vec BLOB, archived INTEGER DEFAULT 0)")
        have_ep = {r[1] for r in self.db.execute("PRAGMA table_info(episodes)")}
        if "lesson" not in have_ep:
            self.db.execute("ALTER TABLE episodes ADD COLUMN lesson INTEGER")  # the lesson this episode already fed
        if "proc_lesson" not in have_ep:  # the cross-task procedure lesson it already fed
            self.db.execute("ALTER TABLE episodes ADD COLUMN proc_lesson INTEGER")
        self.db.execute("CREATE TABLE IF NOT EXISTS recalls (id INTEGER PRIMARY KEY, ts REAL, query TEXT, "
                        "memory_ids TEXT, credited INTEGER DEFAULT 0)")
        self.db.execute("CREATE TABLE IF NOT EXISTS predictors (source TEXT PRIMARY KEY, n INTEGER, err_sum REAL)")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(turns)")}
        for col, typ in (("strength", "REAL DEFAULT 0.5"), ("stability", "REAL DEFAULT 1.0"),
                         ("conditions", "TEXT DEFAULT ''"), ("evidence", "TEXT DEFAULT ''"),
                         ("action", "TEXT DEFAULT ''"), ("contradictions", "INTEGER DEFAULT 0")):
            if col not in have:
                self.db.execute(f"ALTER TABLE turns ADD COLUMN {col} {typ}")
        self.db.commit()

    # ------------------------------------------------------------------ recall bookkeeping
    def log_recall(self, query: str, ids: list[int]) -> int:
        with self.mem.lock:
            cur = self.db.execute("INSERT INTO recalls (ts, query, memory_ids) VALUES (?, ?, ?)",
                                  (time.time(), query[:300], json.dumps(ids)))
            self.db.commit()
            return cur.lastrowid

    def _vec(self, situation: str, action: str) -> np.ndarray:
        return self.mem.encode([f"{situation} -> {action}"])[0]

    def _similar_episodes(self, vec, action: str, threshold: float) -> list[tuple]:
        rows = self.db.execute("SELECT id, success, vec, conditions FROM episodes WHERE action = ? AND archived = 0",
                               (action,)).fetchall()
        return [r for r in rows if float(np.frombuffer(r[2], np.float32) @ vec) >= threshold]

    def predictor_bias(self, source: str) -> float:
        r = self.db.execute("SELECT n, err_sum FROM predictors WHERE source = ?", (source,)).fetchone()
        return r[1] / r[0] if r and r[0] >= PREDICTOR_MIN else 0.0

    # ------------------------------------------------------------------ write path (§5)
    def record_episode(self, situation: str, action: str, actual: dict, predicted: dict | None = None,
                       conditions: dict | None = None, signal: str = "auto", severity: str = "",
                       recall_id: int | None = None, source: str = "", ts: float | None = None) -> dict:
        ts, predicted, conditions = ts or time.time(), dict(predicted or {}), dict(conditions or {})
        vec = self._vec(situation, action)
        low_conf = 0
        if predicted.get("success") is None:  # no prediction: memory's own track record for similar episodes
            sims = self._similar_episodes(vec, action, PRIOR_SIM)
            predicted["success"] = float(np.mean([r[1] for r in sims])) if sims else 0.5
            low_conf, source = (0 if sims else 1), "memory"
        sup = surprise(actual, predicted)
        success = float(actual.get("success", 0))
        with self.mem.lock:
            cur = self.db.execute(
                "INSERT INTO episodes (ts, situation, action, conditions, predicted, actual, success, surprise, signal, "
                "severity, low_conf, vec) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, situation, action, json.dumps(conditions), json.dumps(predicted), json.dumps(actual), success, sup,
                 signal, severity, low_conf, vec.tobytes()))
            eid = cur.lastrowid
            if source:  # step 4: how wrong this predictor's success guesses are
                self.db.execute("INSERT INTO predictors (source, n, err_sum) VALUES (?, 1, ?) ON CONFLICT(source) "
                                "DO UPDATE SET n = n + 1, err_sum = err_sum + excluded.err_sum",
                                (source, float(predicted["success"]) - success))
            self.db.commit()
        credited = self._credit(recall_id, sup, success, signal, low_conf)
        out = {"episode": eid, "surprise": round(sup, 3), "predicted_success": round(predicted["success"], 3),
               "low_confidence": bool(low_conf), "credited": credited, "fear": None, "reconsolidated": []}
        if severity == "severe":
            out["fear"] = self._fear(situation, action, actual, conditions, eid, ts)
        out["reconsolidated"] = self._check_lessons(action, success, conditions, eid, vec)
        if self.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] % 50 == 0:
            out["consolidated"] = self.consolidate()
        self.mem.dirty = True
        return out

    def _credit(self, recall_id, sup, success, signal, low_conf) -> list[int]:
        """§5 step 3 as built: memories recalled for this decision move by lr x signal x surprise; a loss is damped
        by sqrt(stability); a helpful recall (surprise >= 0 and success >= 0.5) grows stability x1.5 (cap 20)."""
        if not recall_id:
            return []
        row = self.db.execute("SELECT memory_ids, credited FROM recalls WHERE id = ?", (recall_id,)).fetchone()
        if not row or row[1]:
            return []
        ids = json.loads(row[0])
        delta = LEARNING_RATE * SIGNAL_WEIGHT.get(signal, 0.7) * sup * (0.5 if low_conf else 1.0)
        helpful = sup >= 0 and success >= 0.5
        with self.mem.lock:
            for mid in ids:
                r = self.db.execute("SELECT strength, stability FROM turns WHERE id = ?", (mid,)).fetchone()
                if not r:
                    continue
                strength, stability = (r[0] if r[0] is not None else 0.5), (r[1] or 1.0)
                d = delta / math.sqrt(stability) if delta < 0 else delta
                self.db.execute("UPDATE turns SET strength = ?, stability = ? WHERE id = ?",
                                (min(1.0, max(0.0, strength + d)),
                                 min(MAX_STABILITY, stability * 1.5) if helpful else stability, mid))
            self.db.execute("UPDATE recalls SET credited = 1 WHERE id = ?", (recall_id,))
            self.db.commit()
        return ids

    # ------------------------------------------------------------------ fears (§9)
    def _insert_lesson(self, kind: str, text: str, action: str, conditions: dict, evidence: list[int], ts: float,
                       strength: float, stability: float) -> int:
        v = self.mem.encode([f"memory: {text}"])[0]
        with self.mem.lock:
            cur = self.db.execute(
                "INSERT INTO turns (ts, chat, speaker, text, vec, kind, slot, raw, method, checked, confidence, strength, "
                "stability, conditions, evidence, action) VALUES (?, 'experience', 'memory', ?, ?, ?, '', NULL, "
                "'experience', 'ok', 0.9, ?, ?, ?, ?, ?)",
                (ts, text, v.tobytes(), kind, strength, stability, json.dumps(conditions), json.dumps(evidence), action))
            self.db.commit()
            self.mem.dirty = True
            return cur.lastrowid

    def _fear(self, situation, action, actual, conditions, eid, ts) -> int:
        what = actual.get("what") or "a severe failure"
        return self._insert_lesson("fear", f"WARNING: {action} in a situation like \"{situation[:160]}\" caused {what}.",
                                   action, conditions, [eid], ts, 1.0, MAX_STABILITY)

    def _situation_vec(self, evidence: str):
        """A lesson is compared by the SITUATIONS it came from (its evidence episodes), never by its own wording."""
        ids = json.loads(evidence or "[]")[:1]
        r = self.db.execute("SELECT situation FROM episodes WHERE id = ?", ids).fetchone() if ids else None
        return r[0] if r else None

    def fears(self, query: str, conditions: dict | None = None) -> list[dict]:
        """Fear lessons relevant to the situation (similarity >= 0.5, not ruled out by conditions): always shown."""
        rows = [r for r in self.db.execute("SELECT id, text, evidence, conditions, strength FROM turns WHERE kind = 'fear' "
                                           "AND COALESCE(status, '') NOT IN ('cleared', 'forgotten')")]
        sits = [self._situation_vec(r[2]) for r in rows]
        rows = [(r, sit) for r, sit in zip(rows, sits) if sit]
        if not rows or not query.strip():
            return []
        v = self.mem.encode([query] + [sit for _, sit in rows])
        out = []
        for ((mid, text, _, conds, strength), _), vec in zip(rows, v[1:]):
            sim = float(vec @ v[0])
            if sim >= FEAR_SIM and condition_match(json.loads(conds or "{}"), conditions or {}) > 0.25:
                out.append({"id": mid, "text": text, "strength": round(strength, 2), "similarity": round(sim, 2)})
        return out

    def clear_fear(self, mid: int, by: str) -> bool:
        if not by.strip():
            return False  # never silently: a name is required
        with self.mem.lock:
            n = self.db.execute("UPDATE turns SET status = 'cleared', raw = ? WHERE id = ? AND kind = 'fear'",
                                (f"cleared by {by} on {time.ctime()}", mid)).rowcount
            self.db.commit()
            self.mem.dirty = True
        return n > 0

    # ------------------------------------------------------------------ reconsolidation (§8)
    def _check_lessons(self, action, success, conditions, eid, vec) -> list[int]:
        touched = []
        rows = self.db.execute("SELECT id, kind, conditions, evidence, strength, contradictions, text FROM turns "
                               "WHERE kind IN ('strategy', 'lesson', 'fear') AND action = ? AND "
                               "COALESCE(status, '') NOT IN ('cleared', 'forgotten')", (action,)).fetchall()
        for mid, kind, conds, evidence, strength, contra, text in rows:
            ev = json.loads(evidence or "[]")[:1]
            first = self.db.execute("SELECT vec FROM episodes WHERE id = ?", ev).fetchone() if ev else None
            if first is None or float(np.frombuffer(first[0], np.float32) @ vec) < FEAR_SIM:
                continue  # a lesson about this action, but a different kind of situation
            stored = json.loads(conds or "{}")
            if condition_match(stored, conditions) <= 0.25:
                continue  # the lesson's conditions rule this case out: no contradiction
            if kind == "fear":
                if success >= 0.8:  # contrary evidence weakens a fear, never clears it
                    self._update(mid, strength=strength * 0.7, raw=f"weakened by episode {eid} (success {success:.2f})")
                    touched.append(mid)
                continue
            contradicted = (kind == "strategy" and success < 0.5) or (kind == "lesson" and success >= 0.5)
            if not contradicted:
                continue
            refined = self._refine(stored, json.loads(evidence or "[]"), conditions)
            if refined:
                k, v = refined
                stored[k] = v
                self._update(mid, conditions=json.dumps(stored), text=f"{text} (holds when {k} = {v})")
            elif contra + 1 >= 2:
                self._update(mid, strength=strength / 2, contradictions=contra + 1, status="contradicted")
            else:
                self._update(mid, contradictions=contra + 1)
            touched.append(mid)
        return touched

    def _refine(self, stored, evidence_ids, conditions) -> tuple[str, str] | None:
        """A condition every supporting episode shares and the contradicting one doesn't: the lesson's real scope."""
        if not evidence_ids:
            return None
        rows = self.db.execute(f"SELECT conditions FROM episodes WHERE id IN ({','.join('?' * len(evidence_ids))})",
                               evidence_ids).fetchall()
        support = [json.loads(r[0] or "{}") for r in rows]
        for k in support[0] if support else {}:
            vals = {str(s.get(k)) for s in support}
            if len(vals) == 1 and k not in stored and str(conditions.get(k)) not in vals:
                return k, next(iter(vals))
        return None

    def _update(self, mid, **cols):
        with self.mem.lock:
            self.db.execute(f"UPDATE turns SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", (*cols.values(), mid))
            self.db.commit()
            self.mem.dirty = True

    # ------------------------------------------------------------------ consolidation (§7)
    def consolidate(self) -> dict:
        # procedure outcomes ("<kind> via <how>") consolidate across tasks below, not per situation (no duplicates)
        rows = self.db.execute("SELECT id, ts, situation, action, conditions, success, vec, lesson FROM episodes "
                               "WHERE archived = 0 AND action NOT LIKE '% via %' ORDER BY ts DESC LIMIT 1000").fetchall()
        clusters: list[list] = []
        for r in rows:  # greedy: same action, similar situation
            v = np.frombuffer(r[6], np.float32)
            home = next((c for c in clusters if c[0][3] == r[3] and float(np.frombuffer(c[0][6], np.float32) @ v)
                         >= CLUSTER_SIM), None)
            home.append(r) if home else clusters.append([r])
        made, merged, archived = [], [], 0
        for c in clusters:
            if len(c) < 3 or all(r[7] for r in c):
                continue  # too little evidence, or nothing new since the last sleep (no re-counting old evidence)
            rate = float(np.mean([r[5] for r in c]))
            kind = "strategy" if rate >= 0.8 else "lesson" if rate <= 0.3 else None
            if not kind:
                continue  # mixed: left to reconsolidation
            shared = {k: v for k, v in json.loads(c[0][4] or "{}").items()
                      if all(json.loads(r[4] or "{}").get(k) == v for r in c)}
            cond = (" (when " + ", ".join(f"{k} = {v}" for k, v in shared.items()) + ")") if shared else ""
            ok = sum(r[5] >= 0.5 for r in c)
            text = (f"What worked: for situations like \"{c[0][2][:140]}\", {c[0][3]} succeeded in {ok}/{len(c)} runs"
                    if kind == "strategy" else
                    f"What failed: for situations like \"{c[0][2][:140]}\", {c[0][3]} succeeded in only {ok}/{len(c)} runs")
            text += cond + "."
            evid = [r[0] for r in sorted(c, key=lambda r: -r[1])]
            existing = self._match_lesson(kind, c[0][3], self._vec(c[0][2], c[0][3]))
            if existing:
                mid, strength, stability, old_ev = existing
                self._update(mid, strength=min(1.0, strength + 0.1), stability=min(MAX_STABILITY, stability * 1.2),
                             evidence=json.dumps(sorted(set(json.loads(old_ev or "[]")) | set(evid[:3]))), text=text)
                merged.append(mid)
            else:
                made.append(self._insert_lesson(kind, text, c[0][3], shared, evid[:3], time.time(), 0.6, 1.0))
            lid = merged[-1] if existing else made[-1]
            with self.mem.lock:
                self.db.execute(f"UPDATE episodes SET lesson = ? WHERE id IN ({','.join('?' * len(evid))})", [lid, *evid])
                self.db.commit()
            old = evid[3:]  # archive all but the newest 3 evidence episodes: kept for training, out of retrieval
            if old:
                with self.mem.lock:
                    self.db.execute(f"UPDATE episodes SET archived = 1 WHERE id IN ({','.join('?' * len(old))})", old)
                    self.db.commit()
                archived += len(old)
        proc_made, proc_merged = self._consolidate_procedures()
        return {"new_lessons": made + proc_made, "merged": merged + proc_merged, "archived": archived}

    def _consolidate_procedures(self) -> tuple[list[int], list[int]]:
        """Lessons that travel ACROSS tasks: episodes grouped by procedure (an action like "tests via spec_fix_deep",
        with its step kind), whatever the task was. Same thresholds as above (3+ runs, >= 80 % -> strategy, <= 30 %
        -> failure lesson), and from at least 2 different situations, so it really is about the procedure. The text
        keeps WHAT worked (the doer's own summary) or HOW it failed, not just a rate."""
        rows = self.db.execute("SELECT id, ts, situation, action, conditions, success, actual, proc_lesson FROM episodes "
                               "WHERE action LIKE '% via %' ORDER BY ts DESC LIMIT 2000").fetchall()
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r[3], []).append(r)
        made, merged = [], []
        for action, eps in groups.items():
            if len(eps) < 3 or len({e[2] for e in eps}) < 2 or all(e[7] for e in eps):
                continue  # too few, all from one task, or nothing new since the last sleep
            rate = float(np.mean([e[5] for e in eps]))
            kind = "strategy" if rate >= 0.8 else "lesson" if rate <= 0.3 else None
            if not kind:
                continue
            ok = sum(e[5] >= 0.5 for e in eps)
            step_kind = json.loads(eps[0][4] or "{}").get("step_kind", action.split(" via ")[0])
            details = [json.loads(e[6] or "{}") for e in eps]
            if kind == "strategy":
                what = "; ".join(dict.fromkeys(d["approach"][:160] for d in details if d.get("approach")))[:400]
                text = (f"What worked across tasks: {step_kind} steps succeeded via {action.split(' via ')[1]} in "
                        f"{ok}/{len(eps)} runs" + (f". What was done: {what}" if what else "") + ".")
            else:
                why = "; ".join(dict.fromkeys(d["error"][:160] for d in details if d.get("error")))[:400]
                text = (f"What failed across tasks: {step_kind} steps done via {action.split(' via ')[1]} failed in "
                        f"{len(eps) - ok}/{len(eps)} runs" + (f". Typical failure: {why}" if why else "") + ".")
            slot = f"procedure:{action}"
            old = self.db.execute("SELECT id, strength, stability FROM turns WHERE slot = ? AND kind = ? AND "
                                  "COALESCE(status, '') = ''", (slot, kind)).fetchone()
            evid = [e[0] for e in eps]
            if old:
                self._update(old[0], text=text, strength=min(1.0, old[1] + 0.1),
                             stability=min(MAX_STABILITY, old[2] * 1.2), evidence=json.dumps(evid[:5]))
                lid = old[0]
                merged.append(lid)
            else:
                lid = self._insert_lesson(kind, text, action, {"step_kind": step_kind}, evid[:5], time.time(), 0.6, 1.0)
                self._update(lid, slot=slot)
                made.append(lid)
            with self.mem.lock:
                self.db.execute(f"UPDATE episodes SET proc_lesson = ? WHERE id IN ({','.join('?' * len(evid))})",
                                [lid, *evid])
                self.db.commit()
        return made, merged

    def _match_lesson(self, kind, action, vec):
        for mid, strength, stability, ev in self.db.execute(
                "SELECT id, strength, stability, evidence FROM turns WHERE kind = ? AND action = ? AND "
                "COALESCE(status, '') = ''", (kind, action)).fetchall():
            ids = json.loads(ev or "[]")
            vecs = self.db.execute(f"SELECT vec FROM episodes WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall() \
                if ids else []
            if any(float(np.frombuffer(r[0], np.float32) @ vec) >= MERGE_SIM for r in vecs):
                return mid, strength, stability, ev
        return None

    def lessons(self) -> list[dict]:
        return [{"id": r[0], "kind": r[1], "text": r[2], "strength": round(r[3], 2), "stability": round(r[4], 2),
                 "status": r[5] or ""} for r in self.db.execute(
            "SELECT id, kind, text, strength, stability, status FROM turns WHERE kind IN ('strategy', 'lesson', 'fear') "
            "ORDER BY ts DESC")]

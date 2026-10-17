"""memrouter: Horizon's memory layer (owner, 2026-10-16). Recall (fine-tuned bge-small + learned ranker), the
memory writer, provenance and correction live here; learning from outcomes lives in experience.py. Runs as a
private local service (here for Hermes).

    python memory_service.py --home ~/.embed-memory --model ./embed-test --ranker ./ranker.pkl [--port 8765]

POST /add    {"text", "speaker", "chat", "ts"?}  -> {"added": n_pieces}
POST /search {"query", "budget"?=500, "skip"?, "skip_chats"?} -> {"context", "n", "ms", "rules", "items" (with "score")}
             (skip: speakers left out; skip_chats: chat-id prefixes left out; rules: every active standing rule)
POST /add    {..., "detect_rule"?: true, "role"?, "kind"?} -> owner's message (rules/taste/mood/location); role "assistant";
             kind: structured text from a program, stored as is (no writer)
             With --writer-url every message goes through the memory writer (writer.py) first.
POST /rule {"text"} | POST /rule/forget {"id"} | GET /rules
GET  /health                                     -> {"turns"}

Messages live in SQLite (one row per ~120-word piece, with its vector). For search they are laid out as the
engine was trained on: one session per chat per day, oldest first, then ranked and packed into the budget.
Listens on 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import sqlite3
import sys
import math
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dates as D  # noqa: E402
import experience as X  # noqa: E402
import fast_recall as F  # noqa: E402
import learned_recall as L  # noqa: E402

QUERY = "Represent this sentence for searching relevant passages: "  # bge's query instruction (as fine-tuned)


class Encoder:
    def __init__(self, path: str):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = AutoModel.from_pretrained(path).to(self.device).eval()

    def __call__(self, texts: list[str], max_len: int = 96) -> np.ndarray:
        with self.torch.no_grad():
            x = self.tok(texts, truncation=True, max_length=max_len, padding=True, return_tensors="pt").to(self.device)
            v = self.model(**x).last_hidden_state[:, 0]
            return self.torch.nn.functional.normalize(v, dim=-1).cpu().numpy().astype(np.float32)


# Standing instructions ("from now on…", "don't show me…", "your messages should…") are rules: kept apart and sent
# with every reply, because a rule must apply to tasks that sound nothing like it (similarity recall misses those).
# ponytail: keyword patterns; false positives ("don't tell mom") are cheap to forget, a learned detector can come later
_RULE = re.compile(
    r"\b(from now on|going forward|in (the )?future|every ?time|whenever (you|i)|always|never|"
    r"(don['’]?t|do not|stop) (ever |again )?(show|give|send|tell|use|reply|write|include|ask|share|explain|paste|do)|"
    r"(your|the) (messages|replies|answers|responses) (should|would|must|need to)|i want you to (always|never|only))\b",
    re.I)


_LOCATION = re.compile(r"\[(Live location|Location): ?(.*?) ?(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\]")


# Taste-profile statements become always-on profile facts (like rules, but preferences, not instructions)
_PROFILE = re.compile(
    r"\b(i am|i'?m|im) (a )?(pure )?(vegetarian|vegan|jain|non.?veg\w*|eggetarian)\b|\bi (only )?eat (only )?veg|"
    r"\bi (really )?(love|like|prefer|enjoy|hate|dislike|can['’]?t stand|avoid) |\bi (don['’]?t|do not|never) eat\b|"
    r"\ballergic to\b|\bspic(e|y) level\b|\bmy (favou?rite|go.to) (food|cuisine|dish|restaurant)",
    re.I)
# Mood words -> the owner's latest mood (private; used for recommendations)
MOODS = {"happy": r"happy|great|awesome|amazing|good mood", "celebrating": r"celebrat\w*|promotion|party|won\b",
         "sad": r"\bsad\b|\blow\b|down\b|upset|heartbroken|crying", "tired": r"tired|exhausted|sleepy|drained|burnt? out",
         "stressed": r"stress\w*|anxious|tense|overwhelmed|worried", "bored": r"bored|boring",
         "angry": r"angry|annoyed|frustrated|pissed", "sick": r"\bsick\b|\bill\b|fever|cold and|not well|unwell",
         "relaxed": r"relaxed|chill\w*|lazy sunday|calm"}
_MOOD = re.compile(r"\b(i am|i'?m|im|feeling|feel|i feel|today is|been)\b[^.!?]{0,30}?\b(" +
                   "|".join(f"(?P<{k}>{v})" for k, v in MOODS.items()) + ")", re.I)


def is_profile(text: str) -> bool:
    return bool(_PROFILE.search(text or ""))


def mood_of(text: str) -> str | None:
    m = _MOOD.search(text or "")
    return next((k for k in MOODS if m and m.group(k)), None)


def _http_json(url: str, data: bytes | None = None, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, data, {"User-Agent": "hermes-embed-memory/0.1 (personal assistant)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def km(a_lat, a_lng, b_lat, b_lng) -> float:
    p = math.pi / 180
    h = (math.sin((b_lat - a_lat) * p / 2) ** 2 +
         math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin((b_lng - a_lng) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def is_rule(text: str) -> bool:
    return bool(_RULE.search(text or ""))


# How far to trust that a record says what was meant (not whether the world agrees): the owner's own words or a
# correction are near-certain; a cleaned memory that kept every number, link and name is strong; one that lost some
# is weak (the original words ride along); the agent's own report of what it did is only its word.
CONFIDENCE = {"correction": 1.0, "rules": 0.8, "writer": 0.9, "writer_mismatch": 0.5, "agent": 0.7}
_KEEP = re.compile(r"https?://\S+|[\w.+-]+@[\w-]+\.[\w.]+|\d[\d,]*(?:\.\d+)?|(?<=\s)[A-Z][a-zA-Z]{2,}(?:\s[A-Z][a-zA-Z]+)*")


def verify(memory: str, raw: str) -> list[str]:
    """What the writer dropped: numbers, links, emails and mid-sentence proper names of the original that the cleaned
    memory no longer contains. Empty means the memory kept them all."""
    flat = lambda x: x.lower().replace(",", "")  # noqa: E731
    mem = flat(memory)
    return [k for k in dict.fromkeys(m.rstrip(".,!?)") for m in _KEEP.findall(" " + raw)) if flat(k) not in mem]


def pieces(text: str, size: int = 120) -> list[str]:
    w = text.split()
    return [" ".join(w[i:i + size]) for i in range(0, len(w), size)]


class _Rows:
    """A finished query: rows already fetched, so no cursor is shared between threads."""

    def __init__(self, cur):
        self.rows = cur.fetchall() if cur.description else []
        self.rowcount, self.lastrowid = cur.rowcount, cur.lastrowid

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class _LockedDB:
    """One SQLite connection used by every server thread: each statement runs and is fetched whole under one
    re-entrant lock (interleaved cursors lost episodes and raised InterfaceError under concurrent requests).
    ponytail: one lock for all statements; a writer queue or per-thread connections if throughput ever matters."""

    def __init__(self, path, lock):
        self.conn, self.lock = sqlite3.connect(path, check_same_thread=False), lock

    def execute(self, sql, params=()):
        with self.lock:
            return _Rows(self.conn.execute(sql, params))

    def executemany(self, sql, rows):
        with self.lock:
            return _Rows(self.conn.executemany(sql, rows))

    def commit(self):
        with self.lock:
            self.conn.commit()


class Memory:
    def __init__(self, home: Path, encode, ranker, tz: str = "Asia/Kolkata", fetch=_http_json, writer=None):
        home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()  # ponytail: one lock for reads and writes; fine for one person's messages
        self.db = _LockedDB(home / "memory.db", self.lock)
        self.db.execute("CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, ts REAL, chat TEXT, speaker TEXT, "
                        "text TEXT, vec BLOB)")
        self.db.execute("CREATE TABLE IF NOT EXISTS rules (id INTEGER PRIMARY KEY, ts REAL, text TEXT, "
                        "active INTEGER DEFAULT 1)")
        # current state that replaces itself (latest shared location), sent with every reply like the rules
        self.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, ts REAL, value TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY, ts REAL, text TEXT, "
                        "active INTEGER DEFAULT 1)")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(turns)")}
        for col, typ in (("kind", "TEXT DEFAULT ''"), ("slot", "TEXT DEFAULT ''"), ("raw", "TEXT"), ("replaced_ts", "REAL"),
                         ("method", "TEXT DEFAULT ''"), ("checked", "TEXT DEFAULT ''"), ("confidence", "REAL"),
                         ("status", "TEXT DEFAULT ''")):
            if col not in have:  # older stores: add the writer's columns in place, existing rows stay "unclassified"
                self.db.execute(f"ALTER TABLE turns ADD COLUMN {col} {typ}")
        self.encode, self.ranker, self.tz, self.fetch, self.writer = encode, ranker, ZoneInfo(tz), fetch, writer
        self.conv, self.dirty, self.rebuilding = None, True, False
        self.exp = X.Experience(self)  # outcomes -> surprise, credit, lessons, fears

    SYNC_BELOW = 5000  # below this many memories the index rebuilds before a search (milliseconds); above, in the
    # background while search uses the last index (the newest messages are in the live chat anyway)

    def _same_slot(self, slot: str, slots: list[str]) -> str:
        """The writer named a slot; if an existing slot means the same ("internet company" ~ "wifi provider"),
        use that one, so a new value really replaces the old. Exact name first, then meaning (same encoder)."""
        if not slot or slot in slots or not slots:
            return slot
        v = self.encode([slot] + slots)
        sims = v[1:] @ v[0]
        best = int(np.argmax(sims))
        same_entity = slot.split(":")[0].strip() == slots[best].split(":")[0].strip() if ":" in slot else True
        return slots[best] if sims[best] >= 0.88 and same_entity else slot

    def add(self, text: str, speaker: str, chat: str, ts: float | None = None, owner: bool = False,
            role: str = "", kind: str = "") -> int:
        """Save one message. With a writer: typed, cleaned memories (rules, preferences and mood go to their own
        stores, a new value for a slot marks the old one replaced). Without one, or if it fails: the rule-based path.
        owner: the message is the owner's own (only then can it set rules, preferences, mood). role: "assistant"
        for the agent's replies. kind: already-structured text from a program (e.g. the runner's "advice"), stored as
        is under that kind, no writer call. Returns how many memories were stored."""
        ts = ts or time.time()
        when = datetime.fromtimestamp(ts, self.tz)
        mems = None
        if kind:
            mems = [{"kind": kind, "memory": " ".join((text or "").split())[:600], "slot": ""}]
        elif self.writer is not None and (text or "").strip():
            recent = [f"{r[0]}: {r[1]}" for r in self.db.execute(
                "SELECT speaker, COALESCE(raw, text) FROM turns WHERE chat = ? ORDER BY ts DESC, id DESC LIMIT 4",
                (chat,)).fetchall()[::-1]]
            slots = [r[0] for r in self.db.execute("SELECT slot FROM turns WHERE slot != '' AND replaced_ts IS NULL "
                                                   "GROUP BY slot ORDER BY MAX(ts) DESC LIMIT 60")]
            who = f"{speaker} (the assistant)" if role == "assistant" else f"{speaker} (the owner)" if owner else speaker
            try:
                mems = self.writer.write(text, who, when, recent, slots)
            except Exception:
                mems = None  # model down or slow: fall back, never lose the message
        method = "program" if kind else "writer" if mems is not None else "rules"
        if mems is None:
            if owner:
                if is_rule(text):
                    self.add_rule(text, ts)
                if is_profile(text):
                    self.add_profile(text, ts)
                self.note_mood(text, ts)
            # "last Friday" read months later has lost its "now": store "last Friday (Friday 15 September 2023)"
            rows = [(p, "", "", "", CONFIDENCE["agent" if role == "assistant" else "rules"])
                    for p in pieces(D.resolve(text or "", when))]
        else:
            rows = []
            for m in mems:
                if owner and m["kind"] == "rule":
                    self.add_rule(m["memory"], ts)
                elif owner and m["kind"] == "preference":
                    self.add_profile(m["memory"], ts)
                elif owner and m["kind"] == "mood":
                    with self.lock:
                        self.db.execute("INSERT OR REPLACE INTO state (key, ts, value) VALUES ('mood', ?, ?)",
                                        (ts, json.dumps({"mood": m["mood"], "said": " ".join(text.split())[:160]})))
                        self.db.commit()
                if m["memory"] and m["kind"] != "chitchat":
                    lost = verify(m["memory"], text) if not kind and len(text.split()) <= 120 else []
                    memory = f"{m['memory']} [said: {' '.join(text.split())[:300]}]" if lost else m["memory"]
                    conf = CONFIDENCE["writer_mismatch" if lost else "agent" if role == "assistant" else "writer"]
                    slot = self._same_slot(m["slot"], slots) if m["slot"] else ""
                    rows += [(p, m["kind"], slot, "mismatch" if lost else "ok", conf) for p in pieces(memory)]
            if not rows:  # chitchat: keep the words for conversation context, marked as such
                rows = [(p, "chitchat", "", "", CONFIDENCE["writer"]) for p in pieces(text or "")]
        if not rows:
            return 0
        # same passage format the model was tuned on; a short original rides along in the vector so its own words
        # ("can't digest lactose") still match after cleaning ("is lactose intolerant")
        extra = f" ({' '.join(text.split())})" if mems is not None and len((text or "").split()) <= 40 else ""
        vecs = self.encode([f"{speaker}: {p}{extra if k != 'chitchat' else ''}" for p, k, *_ in rows])
        with self.lock:
            for slot in {r[2] for r in rows if r[2]}:  # a new value replaces the current one for that slot
                self.db.execute("UPDATE turns SET replaced_ts = ? WHERE slot = ? AND replaced_ts IS NULL AND ts <= ?",
                                (ts, slot, ts))
            self.db.executemany("INSERT INTO turns (ts, chat, speaker, text, vec, kind, slot, raw, method, checked, "
                                "confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                [(ts, chat, speaker, p, v.tobytes(), k, sl, text if self.writer else None, method, chk, cf)
                                 for (p, k, sl, chk, cf), v in zip(rows, vecs)])
            self.db.commit()
            self.dirty = True
        return len(rows)

    def correct(self, mid: int, text: str, ts: float | None = None) -> int:
        """The owner says memory #mid is wrong: it is marked corrected (kept for history, never served as current)
        and the right version is stored with full confidence, same kind and slot."""
        ts = ts or time.time()
        row = self.db.execute("SELECT chat, speaker, kind, slot FROM turns WHERE id = ?", (mid,)).fetchone()
        if not row or not (text or "").strip():
            return 0
        v = self.encode([f"{row[1]}: {text}"])[0]
        with self.lock:
            self.db.execute("UPDATE turns SET status = 'corrected', replaced_ts = ? WHERE id = ?", (ts, mid))
            cur = self.db.execute("INSERT INTO turns (ts, chat, speaker, text, vec, kind, slot, raw, method, checked, "
                                  "confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'correction', 'ok', ?)",
                                  (ts, row[0], row[1], " ".join(text.split()), v.tobytes(), row[2], row[3],
                                   f"correction of #{mid}", CONFIDENCE["correction"]))
            self.db.commit()
            self.dirty = True
            return cur.lastrowid

    def forget(self, mid: int) -> bool:
        with self.lock:
            n = self.db.execute("UPDATE turns SET status = 'forgotten' WHERE id = ?", (mid,)).rowcount
            self.db.commit()
            self.dirty = True
        return n > 0

    def add_rule(self, text: str, ts: float | None = None) -> int:
        text = " ".join((text or "").split())[:600]
        with self.lock:
            if not text or self.db.execute("SELECT 1 FROM rules WHERE text = ? AND active = 1", (text,)).fetchone():
                return 0
            cur = self.db.execute("INSERT INTO rules (ts, text) VALUES (?, ?)", (ts or time.time(), text))
            self.db.commit()
            return cur.lastrowid

    def note_location(self, text: str, ts: float | None = None) -> dict | None:
        """A WhatsApp location share ("[Location: Name 12.97,77.64]") becomes the one current location."""
        m = _LOCATION.search(text or "")
        if not m:
            return None
        loc = {"lat": float(m[3]), "lng": float(m[4]), "name": m[2].strip(), "live": m[1].startswith("Live")}
        try:  # coordinates -> area name (OpenStreetMap Nominatim, free; the model guessed the area wrong without it)
            a = self.fetch("https://nominatim.openstreetmap.org/reverse?format=json&zoom=17&lat="
                           f"{loc['lat']}&lon={loc['lng']}").get("address", {})
            parts = [a.get(k) for k in ("road", "neighbourhood", "suburb", "city_district", "city", "state")]
            loc["area"] = ", ".join(dict.fromkeys(p for p in parts if p))
        except Exception:
            loc["area"] = ""
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO state (key, ts, value) VALUES ('location', ?, ?)",
                            (ts or time.time(), json.dumps(loc)))
            self.db.commit()
        return loc

    def note_mood(self, text: str, ts: float | None = None) -> str | None:
        mood = mood_of(text)
        if mood:
            with self.lock:
                self.db.execute("INSERT OR REPLACE INTO state (key, ts, value) VALUES ('mood', ?, ?)",
                                (ts or time.time(), json.dumps({"mood": mood, "said": " ".join(text.split())[:160]})))
                self.db.commit()
        return mood

    def add_profile(self, text: str, ts: float | None = None) -> int:
        text = " ".join((text or "").split())[:400]
        with self.lock:
            if not text or self.db.execute("SELECT 1 FROM profile WHERE text = ? AND active = 1", (text,)).fetchone():
                return 0
            cur = self.db.execute("INSERT INTO profile (ts, text) VALUES (?, ?)", (ts or time.time(), text))
            self.db.commit()
            return cur.lastrowid

    def profile(self) -> list[dict]:
        return [{"id": r[0], "text": r[1]} for r in
                self.db.execute("SELECT id, text FROM profile WHERE active = 1 ORDER BY ts")]

    def forget_profile(self, pid: int) -> bool:
        with self.lock:
            n = self.db.execute("UPDATE profile SET active = 0 WHERE id = ?", (pid,)).rowcount
            self.db.commit()
        return n > 0

    OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")

    def _osm_places(self, loc: dict, radius_m: int, cuisine: str) -> list[dict]:
        """Eateries around loc as (name, lat, lng, tags). Overpass first; its public servers are often overloaded,
        so fall back to Nominatim's bounded search (same OpenStreetMap data, a few queries, 1 request/s policy)."""
        q = (f'[out:json][timeout:15];nwr(around:{radius_m},{loc["lat"]},{loc["lng"]})'
             f'["amenity"~"^(restaurant|cafe|fast_food|food_court|ice_cream)$"]["name"];out center 200;')
        for url in self.OVERPASS:
            try:
                els = self.fetch(url, urllib.parse.urlencode({"data": q}).encode(), timeout=15).get("elements", [])
                return [{"name": e["tags"]["name"], "lat": e.get("lat", e.get("center", {}).get("lat")),
                         "lng": e.get("lon", e.get("center", {}).get("lon")), "tags": e["tags"]}
                        for e in els if e.get("tags", {}).get("name")]
            except Exception:
                continue
        d = radius_m / 111_000  # degrees of latitude per metre (good enough for a box)
        box = f"{loc['lng'] - d},{loc['lat'] + d},{loc['lng'] + d},{loc['lat'] - d}"
        out = []
        for i, term in enumerate(([f"{cuisine} restaurant"] if cuisine else []) + ["restaurant", "cafe", "fast food"]):
            if i:
                time.sleep(1.1)
            try:
                rows = self.fetch("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                    {"q": term, "format": "jsonv2", "limit": 50, "bounded": 1, "extratags": 1, "viewbox": box}))
            except Exception:
                continue
            for x in rows if isinstance(rows, list) else []:
                if x.get("name"):
                    out.append({"name": x["name"], "lat": float(x["lat"]), "lng": float(x["lon"]),
                                "tags": {"amenity": x.get("type", ""), **(x.get("extratags") or {})}})
        return out

    def nearby_food(self, radius_m: int = 2000, cuisine: str = "", limit: int = 25) -> dict:
        """Real eateries around the owner's latest location (OpenStreetMap), vegetarian-marked first, then nearest."""
        loc = self.current().get("location")
        if not loc:
            return {"error": "no location shared yet: ask the owner to share it on WhatsApp (attach > Location)"}
        radius_m = max(300, min(int(radius_m), 5000))
        want = [w for w in re.split(r"[,/ ]+", cuisine.lower()) if w]
        places, seen = [], set()
        for p in self._osm_places(loc, radius_m, cuisine):
            t, key = p["tags"], (p["name"].lower(), round(p["lat"] or 0, 4), round(p["lng"] or 0, 4))
            if p["lat"] is None or key in seen:
                continue
            seen.add(key)
            dist = km(loc["lat"], loc["lng"], p["lat"], p["lng"])
            kinds = f"{t.get('cuisine', '')} {p['name']}".lower()
            if dist * 1000 > radius_m * 1.5 or (want and not any(w in kinds for w in want)):
                continue
            places.append({"name": p["name"], "type": t.get("amenity", ""),
                           "cuisine": t.get("cuisine", "").replace(";", ", "),
                           "vegetarian": t.get("diet:vegetarian", ""), "km": round(dist, 2),
                           "opening_hours": t.get("opening_hours", ""),
                           "maps": "https://www.google.com/maps/search/?api=1&query="
                                   + urllib.parse.quote(f"{p['name']} {p['lat']},{p['lng']}")})
        places.sort(key=lambda p: (p["vegetarian"] not in ("only", "yes"), p["km"]))
        return {"near": loc.get("area") or f"{loc['lat']},{loc['lng']}", "radius_m": radius_m, "count": len(places),
                "places": places[:limit]}

    def current(self) -> dict:
        out = {}
        for key, ts, value in self.db.execute("SELECT key, ts, value FROM state"):
            out[key] = {**json.loads(value), "hours_ago": round((time.time() - ts) / 3600, 1),
                        "when": datetime.fromtimestamp(ts, self.tz).strftime("%-d %b %Y %-I:%M %p")}
        return out

    def rules(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute("SELECT id, ts, text FROM rules WHERE active = 1 ORDER BY ts DESC LIMIT ?", (limit,))
        return [{"id": r[0], "date": datetime.fromtimestamp(r[1], self.tz).strftime("%-d %b %Y"), "text": r[2]}
                for r in rows][::-1]

    def forget_rule(self, rule_id: int) -> bool:
        with self.lock:
            n = self.db.execute("UPDATE rules SET active = 0 WHERE id = ?", (rule_id,)).rowcount
            self.db.commit()
        return n > 0

    def _index(self):
        # ponytail: full rebuild after each write (~1 s at 50K pieces); make BM25 incremental if that starts to bite
        rows = self.db.execute("SELECT id, ts, chat, speaker, text, vec, kind, replaced_ts, method, checked, confidence, "
                               "status, strength, conditions FROM turns WHERE COALESCE(status, '') NOT IN "
                               "('forgotten', 'cleared') ORDER BY ts, id").fetchall()
        if not rows:
            return None
        sessions: dict[tuple, list] = {}
        for r in rows:
            day = datetime.fromtimestamp(r[1], self.tz)
            sessions.setdefault((r[2], day.date()), []).append((r, day))
        conv_d, vecs = {"speaker_a": "", "speaker_b": ""}, []
        for i, turns in enumerate(sessions.values(), 1):  # dict keeps first-seen order: sessions oldest first
            conv_d[f"session_{i}"] = [{"speaker": r[3], "dia_id": str(r[0]), "text": r[4]} for r, _ in turns]
            conv_d[f"session_{i}_date_time"] = turns[0][1].strftime("%-I:%M %p on %-d %B, %Y")
            vecs += [np.frombuffer(r[5], np.float32) for r, _ in turns]
        conv = F.Conversation(conv_d)
        conv.vecs = np.stack(vecs)
        conv.names = {s.lower(): s for s in {r[3] for r in rows} if s}
        conv.chat = {str(r[0]): r[2] for r in rows}
        conv.kind = {str(r[0]): r[6] or "" for r in rows}
        conv.replaced = {str(r[0]): r[7] for r in rows if r[7]}
        conv.prov = {str(r[0]): {"method": r[8] or "legacy", "checked": r[9] or "", "status": r[11] or "",
                                 "confidence": r[10] if r[10] is not None else 0.6,
                                 "strength": r[12] if r[12] is not None else 0.5} for r in rows}
        conv.conds = {str(r[0]): json.loads(r[13]) for r in rows if r[13]}
        return conv

    def _rebuild(self):
        try:
            conv = self._index()
            with self.lock:
                self.conv = conv
        finally:
            self.rebuilding = False

    def _current_index(self):
        with self.lock:
            n = self.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            if self.conv is None or (self.dirty and n < self.SYNC_BELOW):
                self.dirty, self.conv = False, self._index()
            elif self.dirty and not self.rebuilding:
                self.dirty, self.rebuilding = False, True
                threading.Thread(target=self._rebuild, daemon=True).start()
            return self.conv

    def search(self, query: str, budget: int = 500, skip: tuple = (), skip_chats: tuple = (),
               skip_kinds: tuple = (), conditions: dict | None = None) -> dict:
        t = time.perf_counter()
        conv = self._current_index()
        rules, now = self.rules(), {**self.current(), "profile": self.profile()}
        if conv is None or not query.strip():
            return {"context": "", "n": 0, "ms": 0.0, "rules": rules, "current": now, "items": [],
                    "fears": self.exp.fears(query, conditions), "recall_id": None}
        qv = self.encode([QUERY + query], max_len=64)[0]
        probs = self.ranker.predict(L.features(conv, query, qv))
        # skip_kinds: e.g. agent_claim (what the agent said about itself makes one wrong answer repeat itself);
        # skip: speakers whose UNCLASSIFIED turns (saved before the writer) are left out; skip_chats: chat-id prefixes
        # (scheduled jobs' prompts are not the user talking)
        def ok(t_):
            k = conv.kind[t_["id"]]
            return (k not in skip_kinds and not (k == "" and t_["speaker"] in skip)
                    and not conv.chat[t_["id"]].startswith(tuple(skip_chats) or ("\0",)))
        keep = np.array([ok(t_) for t_ in conv.turns])
        # experience: proven memories rank higher, bounded so learning reorders but never erases a relevant memory
        # (0.2 + 0.8 x strength), and lessons whose conditions don't fit the situation sink
        probs = probs * np.array([0.2 + 0.8 * conv.prov[t_["id"]]["strength"] for t_ in conv.turns])
        if conditions:
            probs = probs * np.array([X.condition_match(conv.conds.get(t_["id"], {}), conditions) for t_ in conv.turns])
        probs = np.where(keep, probs, -np.inf)
        picked = set(L.select(conv, probs, budget)[0])
        picked = {t_["id"] for t_, k in zip(conv.turns, keep) if k and t_["id"] in picked}  # select() fills spare budget
        def line(t_):
            gone, pv = conv.replaced.get(t_["id"]), conv.prov[t_["id"]]
            old = (f"(OUTDATED: {'corrected' if pv['status'] == 'corrected' else 'replaced'} on "
                   f"{datetime.fromtimestamp(gone, self.tz).strftime('%-d %B %Y')}) " if gone else "")
            unsure = "(unverified) " if pv["confidence"] < 0.6 else ""
            return f"#{t_['id']} [{t_['date']}] {t_['speaker']}: {old}{unsure}{t_['text']}"
        chosen = [t_ for t_ in conv.turns if t_["id"] in picked]
        lines = [line(t_) for t_ in chosen]
        score = dict(zip((t_["id"] for t_ in conv.turns), probs))
        items = [{"id": int(t_["id"]), "date": t_["date"], "speaker": t_["speaker"], "kind": conv.kind[t_["id"]],
                  "text": t_["text"], "score": round(float(score[t_["id"]]), 4), "current": t_["id"] not in conv.replaced, **conv.prov[t_["id"]]} for t_ in chosen]
        fears = self.exp.fears(query, conditions)
        recall_id = self.exp.log_recall(query, [it["id"] for it in items] + [f["id"] for f in fears])
        return {"context": "\n".join(lines), "n": len(lines), "ms": round(1000 * (time.perf_counter() - t), 1),
                "rules": rules, "current": now, "items": items, "fears": fears, "recall_id": recall_id}


def serve(mem: Memory, port: int):
    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._send({"turns": mem.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]})
            if self.path == "/rules":
                return self._send({"rules": mem.rules(100)})
            if self.path == "/lessons":
                return self._send({"lessons": mem.exp.lessons()})
            self._send({"error": "not found"}, 404)

        def do_POST(self):
            try:
                a = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/add":
                    owner = bool(a.get("detect_rule"))  # only the owner's own messages set rules/taste/mood/location
                    out = {"added": mem.add(a.get("text", ""), a.get("speaker", "user"), a.get("chat", "default"),
                                            a.get("ts"), owner=owner, role=a.get("role", ""), kind=a.get("kind", ""))}
                    if owner:
                        out["location"] = mem.note_location(a.get("text", ""), a.get("ts"))
                    return self._send(out)
                if self.path == "/rule":
                    return self._send({"rule": mem.add_rule(a.get("text", ""))})
                if self.path == "/episode":
                    return self._send(mem.exp.record_episode(
                        str(a.get("situation", "")), str(a.get("action", "")), dict(a.get("actual") or {}),
                        a.get("predicted"), a.get("conditions"), str(a.get("signal", "auto")), str(a.get("severity", "")),
                        a.get("recall_id"), str(a.get("source", ""))))
                if self.path == "/consolidate":
                    return self._send(mem.exp.consolidate())
                if self.path == "/fear/clear":
                    return self._send({"cleared": mem.exp.clear_fear(int(a.get("id", 0)), str(a.get("by", "")))})
                if self.path == "/memory/correct":
                    return self._send({"id": mem.correct(int(a.get("id", 0)), str(a.get("text", "")))})
                if self.path == "/memory/forget":
                    return self._send({"forgotten": mem.forget(int(a.get("id", 0)))})
                if self.path == "/profile":
                    return self._send({"profile": mem.add_profile(a.get("text", ""))})
                if self.path == "/profile/forget":
                    return self._send({"forgotten": mem.forget_profile(int(a.get("id", 0)))})
                if self.path == "/nearby_food":
                    return self._send(mem.nearby_food(int(a.get("radius_m", 2000)), str(a.get("cuisine", ""))))
                if self.path == "/rule/forget":
                    return self._send({"forgotten": mem.forget_rule(int(a.get("id", 0)))})
                if self.path == "/search":
                    return self._send(mem.search(a.get("query", ""), int(a.get("budget", 500)), tuple(a.get("skip", ())),
                                                 tuple(a.get("skip_chats", ())), tuple(a.get("skip_kinds", ())),
                                                 a.get("conditions")))
                self._send({"error": "not found"}, 404)
            except Exception as exc:  # a bad request must not take the service down
                self._send({"error": repr(exc)}, 400)

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        request_queue_size = 128  # the default 5 refused connections when a job's writes and a search arrive together

    Server(("127.0.0.1", port), H).serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".embed-memory"))
    ap.add_argument("--model", required=True)
    ap.add_argument("--ranker", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--tz", default="Asia/Kolkata")
    ap.add_argument("--backfill-rules", action="store_true", help="turn past user instructions into rules, then exit")
    ap.add_argument("--writer-url", default="", help="OpenAI-compatible endpoint for the memory writer (e.g. vLLM)")
    ap.add_argument("--writer-model", default="local")
    args = ap.parse_args()
    import writer as W

    wr = W.Writer(args.writer_url, args.writer_model) if args.writer_url else None
    mem = Memory(Path(args.home).expanduser(), Encoder(args.model), pickle.loads(Path(args.ranker).read_bytes()), args.tz,
                 writer=wr)
    if args.backfill_rules:
        rows = mem.db.execute("SELECT ts, text FROM turns WHERE speaker NOT IN ('Hermes') AND speaker NOT LIKE 'saved note%' "
                              "AND chat NOT LIKE 'cron_%' ORDER BY ts").fetchall()
        for ts, text in rows:
            if is_rule(text) and mem.add_rule(text, ts):
                print("rule:", text[:120])
        return 0
    print(f"embed memory on 127.0.0.1:{args.port}", flush=True)
    serve(mem, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

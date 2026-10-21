"""One owner message in, one reply out. Code decides the steps; the model makes one call per step.

1. STOP / RESUME (code; while stopped nothing else runs).
2. A booking waiting on the owner: YES confirms, NO cancels, anything else is read as more details for it
   (one model call: task_update); a new request instead drops it.
3. Otherwise one model call with the owner's memory (rules, taste, mood, what they said before), answered as JSON
   forced to DECIDE (vLLM json_schema): reply, book (a form), places (going out) or read_swiggy_orders.
Questions and summaries for bookings are written by code (booking.py), never by the model. No tool calling and no
hand parsing of free text: those were the main sources of live bugs.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

import booking as B
import brain
import food
import places

YES = re.compile(r"^\s*(yes|y|yep|yeah|haan|ha|ok(ay)?|go ahead|confirm(ed)?|do it|sure)\b[\s.!]*$", re.I)
NO = re.compile(r"^\s*(no|n|nope|cancel|stop it|don'?t|nahi|na)\b[\s.!]*$", re.I)
ASK = {"date": "Which date?", "from": "From where?", "to": "To where?", "passengers": "Who's travelling (names)?",
       "class": "Which class (SL, 3A, 2A, 1A)?", "max_price": "What's the most you want to pay?",
       "max_total": "What's your budget?", "max_price_per_night": "What's your budget per night?",
       "checkin": "Check-in date?", "checkout": "Check-out date?", "guests": "Who's staying (names)?",
       "city": "Which city?", "dish": "What would you like to eat?", "drop": "Where to?", "pickup": "Pick up from where?",
       "when": "When?", "arrive": "When do you arrive?", "leave": "When do you leave?"}

PERSONA = """You are the owner's personal assistant on WhatsApp/Telegram. Today is {today}.
Decide what to do with the owner's message and answer as JSON:
- action "book": any booking or order (flight, train, bus, cab, hotel, food delivery, trip). Set kind, and in
  details ONLY what the owner said in this message (dates and people exactly as said, e.g. "friday", "me and mom").
  Detail names per kind: {kinds}. Naming a dish to eat means ordering it (kind food) unless they want to go out.
- action "places": places to go out to eat or visit near them (cuisine if they said one).
- action "read_swiggy_orders": ONLY when they ask to read, check or refresh their Swiggy order history.
- action "reply": anything else; write the reply: brief and plain, like a person. You only get this one reply:
  never say "one moment" or "let me check"; answer now. Never invent facts about the owner.
{memory}"""
VALUE = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "array", "items": {"type": "string"}}]}
DECIDE = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["reply", "book", "places", "read_swiggy_orders"]},
    "kind": {"type": "string", "enum": [*B.TEMPLATES, "none"]},
    "details": {"type": "object", "additionalProperties": VALUE},
    "cuisine": {"type": "string"},
    "reply": {"type": "string"}}, "required": ["action", "kind", "details", "reply"]}
UPDATE = {"type": "object", "properties": {"action": {"type": "string", "enum": ["update", "new"]},
                                           "details": {"type": "object", "additionalProperties": VALUE}},
          "required": ["action", "details"]}
FRESH_H = 12  # order history read this recently is reused, not re-read from Swiggy


def _post(url: str, body: dict, timeout: float = 5) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


DATE_WORDS = re.compile(r"\b(today|tonight|tomorrow|day after tomorrow|(this |next |coming )?(mon|tues|wednes|thurs|fri|"
                        r"satur|sun)day)\b", re.I)
MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
HAS_DATE = re.compile(rf"\b\d{{1,2}}(st|nd|rd|th)?\s*({MONTHS})|\b({MONTHS})[a-z]*\s*\d{{1,2}}\b|\d{{1,2}}/\d{{1,2}}|"
                      r"\d{4}-\d{2}-\d{2}", re.I)


def ground(store: B.Store, kind: str, text: str, slots: dict) -> dict:
    """Keep only what the message supports. The model invents (a 'trip to Rishikesh' came back with dates, a
    budget and interests) and converts weekdays wrongly ('Friday' -> a Sunday), so, measured live 2026-10-22:
    - a value stays only if the message has evidence for it (its words or digits; for dates, a date expression);
    - one weekday/relative day in the message ('friday', 'tomorrow') is turned into the date by code;
    - travellers the message names ('mom', 'me', a saved name) become the passengers when the model gave none."""
    low, out = text.lower(), {}
    digits = re.sub(r"[,\s]", "", low)
    words = set(re.findall(r"[a-z]+", low))
    for k, v in slots.items():
        if k in B.DATES:
            if DATE_WORDS.search(low) or HAS_DATE.search(low):
                out[k] = v
        elif k in ("passengers", "guests"):
            out[k] = v
        elif isinstance(v, list):
            kept = [x for x in v if set(re.findall(r"[a-z]+", str(x).lower())) & words]
            if kept:
                out[k] = kept
        elif isinstance(v, (int, float)) or re.fullmatch(r"[\d,.\s₹rsk]+", str(v), re.I):
            n = B._money(v)
            if n is not None and (str(int(n)) in digits or f"{n / 1000:g}k" in digits):
                out[k] = v
        elif set(re.findall(r"[a-z]+", str(v).lower())) & words or str(v).lower() in low:
            out[k] = v
        elif re.search(r"\d", str(v)) and any(d in digits for d in re.findall(r"\d+", str(v))):
            out[k] = v
    days = [m[0] for m in DATE_WORDS.finditer(low)]
    date_slot = {"cab": "when", "hotel": "checkin", "trip": "arrive"}.get(kind, "date")
    pair = {"hotel": ("checkin", "checkout"), "trip": ("arrive", "leave")}.get(kind)
    if len(days) == 1 and not HAS_DATE.search(low) and date_slot in B.TEMPLATES.get(kind, {}) and kind != "cab":
        out[date_slot] = days[0]
    elif len(days) == 2 and pair and not HAS_DATE.search(low):  # "this Saturday to Monday", in the order said
        out[pair[0]], out[pair[1]] = days
    counted = any(re.fullmatch(r"\s*\d+\s*", str(out.get(s, ""))) for s in ("passengers", "guests"))
    if kind in B.TEMPLATES and (counted or not any(s in out for s in ("passengers", "guests"))):
        who = []
        if re.search(r"\b(me|myself|i)\b", low):
            who.append("me")
        for key, f in store.facts("traveller:").items():
            name, tag = str(f["value"].get("name", "")), key.split(":", 1)[1]
            if tag != "self" and (re.search(rf"\b{re.escape(tag)}\b", low) or (name and name.lower() in low)):
                who.append(name or tag)
        slot = "guests" if "guests" in B.TEMPLATES[kind] else "passengers"
        if who and slot in B.TEMPLATES[kind] and (len(who) > 1 or who != ["me"]):
            out[slot] = who
    return out


class Assistant:
    def __init__(self, home: Path, store: B.Store, memory_url: str = "http://127.0.0.1:8765", chat=brain.chat,
                 post=_post, nearby=places.nearby_food, swiggy=None):
        self.home, self.store, self.mem, self.post, self.nearby = Path(home), store, memory_url, post, nearby
        self.model, self.local = chat, threading.local()
        self.chat = self._traced  # every model call goes through the trace
        self.swiggy = swiggy or food.Swiggy(chat=self._traced, captures=self.home / "captures")
        self.notify = lambda text: None
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.home / "bot.db"), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS log (ts REAL, channel TEXT, chat TEXT, role TEXT, text TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS pending (chat TEXT PRIMARY KEY, task INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS traces (ts REAL, chat TEXT, text TEXT, reply TEXT, ms INTEGER, "
                        "steps TEXT)")
        self.lock = threading.Lock()

    # log -------------------------------------------------------------------------------------------------------
    def _log(self, channel, chat, role, text):
        with self.lock:
            self.db.execute("INSERT INTO log VALUES (?,?,?,?,?)", (time.time(), channel, chat, role, text))
            self.db.commit()

    def recent(self, chat: str, n: int = 6) -> list[dict]:
        rows = self.db.execute("SELECT role, text FROM log WHERE chat=? ORDER BY ts DESC LIMIT ?", (chat, n)).fetchall()
        return [{"role": "user" if r == "owner" else "assistant", "content": t} for r, t in reversed(rows)]

    def log_days(self, days: int) -> list[tuple]:
        """For weekly-reflect: (utc time, owner|bot, text) over the last `days` days, all chats."""
        rows = self.db.execute("SELECT ts, role, text FROM log WHERE ts > ? ORDER BY ts", (time.time() - days * 86400,))
        return [(time.strftime("%Y-%m-%dT%H:%M", time.gmtime(ts)), role, text) for ts, role, text in rows]

    def _pending(self, chat, task=None, clear=False):
        with self.lock:
            if clear:
                self.db.execute("DELETE FROM pending WHERE chat=?", (chat,))
            elif task is not None:
                self.db.execute("INSERT OR REPLACE INTO pending VALUES (?,?)", (chat, task))
            self.db.commit()
            r = self.db.execute("SELECT task FROM pending WHERE chat=?", (chat,)).fetchone()
        return r[0] if r else None

    # memory ----------------------------------------------------------------------------------------------------
    def _memory(self, text: str) -> tuple[str, dict]:
        try:
            r = self.post(self.mem + "/search", {"query": text, "budget": 500, "skip": ["Hermes", "bot"],
                                                  "skip_chats": ["cron_"], "scope": "hermes"})
        except Exception:
            return self._with_taste([]), {}  # memory down: answer without it
        cur, parts = r.get("current", {}), []
        if r.get("rules"):
            parts.append("Standing rules from the owner (always follow):\n" + "\n".join(f"- {x['text']}" for x in r["rules"]))
        if r.get("warnings"):  # the owner corrected these before: Hermes showed them in every reply, so does the bot
            parts.append("Mistakes the owner corrected before (don't repeat them):\n" + "\n".join(
                f"- {w['error'] or w['check']}" + (f" (corrected {w['seen']}x)" if w.get("seen", 1) > 1 else "")
                for w in r["warnings"]))
        if cur.get("profile"):
            parts.append("Owner's taste (private):\n" + "\n".join(f"- {p['text']}" for p in cur["profile"]))
        if cur.get("mood"):
            parts.append(f"Owner's mood: {cur['mood']['mood']} ({cur['mood']['hours_ago']} h ago)")
        if r.get("context"):
            parts.append("What the owner said before:\n" + r["context"])
        return self._with_taste(parts), cur

    def _with_taste(self, parts: list) -> str:
        t = B.taste(self.store)
        if t.get("orders"):
            parts.append(f"What the owner orders on Swiggy ({t['orders']} orders): dishes {t['top_dishes']}, restaurants "
                         f"{t['top_restaurants']}, usual spend ₹{t['typical_spend']}")
        return "\n\n".join(parts)

    def _remember(self, chat: str, text: str):
        def go():
            try:
                self.post(self.mem + "/add", {"text": text, "speaker": "owner", "chat": chat, "detect_rule": True}, 90)
            except Exception:
                pass  # memory is fail-open
        threading.Thread(target=go, daemon=True).start()

    # the turn --------------------------------------------------------------------------------------------------
    def _traced(self, messages, **kw):
        t = time.time()
        try:
            out = self.model(messages, **kw)
        except Exception as e:
            self._step("model_error", ms=int(1000 * (time.time() - t)), error=repr(e)[:500])
            raise
        schema = kw.get("schema")
        self._step("model", ms=int(1000 * (time.time() - t)), schema=(list(schema.get("properties", {})) if schema else None),
                   out=(out.get("content") or "")[:1500])
        return out

    def _step(self, kind: str, **data):
        steps = getattr(self.local, "steps", None)
        if steps is not None:
            steps.append({"step": kind, **data})

    def handle(self, msg: dict, notify=None) -> str:
        """notify(text): an interim message (e.g. "Looking on Swiggy...") while a slow step runs. Every turn leaves a
        trace (each model call: schema, time, output; errors) in bot.db: read it with deploy/bot/trace.sh."""
        self.notify = notify or (lambda text: None)
        chat, text, t = msg["chat"], msg["text"], time.time()
        self.local.steps = []
        self._log(msg["channel"], chat, "owner", text)
        reply = None
        try:
            reply = self._turn(chat, text)
        except Exception as e:
            self._step("error", error=repr(e)[:1000])
            raise
        finally:
            with self.lock:
                self.db.execute("INSERT INTO traces VALUES (?,?,?,?,?,?)", (t, chat, text, reply,
                                int(1000 * (time.time() - t)), json.dumps(self.local.steps, ensure_ascii=False)))
                self.db.commit()
        self._log(msg["channel"], chat, "bot", reply)
        return reply

    def _turn(self, chat: str, text: str) -> str:
        halt = self.home / "HALT"
        if text.strip().upper().rstrip(".!") == "STOP":
            halt.write_text(time.ctime())
            return "Stopped. Nothing runs until you say RESUME."
        if text.strip().upper().rstrip(".!") == "RESUME":
            halt.unlink(missing_ok=True)
            return "Resumed."
        if halt.exists():
            return "I'm stopped. Say RESUME to continue."
        self._remember(chat, text)
        tid = self._pending(chat)
        offers = self.store.fact(f"choice:{tid}") if tid else None
        if tid and offers and not offers["stale"]:
            m = re.fullmatch(r"\s*#?([1-9])\s*", text)
            if m and int(m[1]) <= len(offers["value"]):
                return self._add_to_cart(chat, tid, offers["value"][int(m[1]) - 1])
            if NO.match(text):
                self._pending(chat, clear=True)
                B.finish(self.store, tid, "cancelled")
                return "Cancelled."
            self._pending(chat, clear=True)  # something else: a new request
        elif tid:
            if YES.match(text):
                self._pending(chat, clear=True)
                r = B.confirm(self.store, tid)
                if "error" in r:
                    return "I can't confirm that yet: " + r["error"]
                if self.store.task(tid)["kind"] == "food":
                    return self._find_food(chat, tid, r["slots"])
                return ("Confirmed ✅ " + self._summary_line(r["slots"], self.store.task(tid)["kind"])
                        + "\nBooking it on the site comes with a later update; for now this is saved.")
            if NO.match(text):
                self._pending(chat, clear=True)
                B.finish(self.store, tid, "cancelled")
                return "Cancelled."
            t = self.store.task(tid)
            d = brain.extract([{"role": "system", "content": f"The owner is answering questions for a {t['kind']} "
                                f"booking with these details so far: {json.dumps(t['slots'])}. Detail names: "
                                f"{', '.join(B.TEMPLATES[t['kind']])}. If the message adds or changes details, action "
                                "update with only those (dates and people as said); if it is a different request, "
                                "action new."}, {"role": "user", "content": text}], UPDATE, self.chat)
            if d.get("action") == "update" and d.get("details"):
                slots = ground(self.store, t["kind"], text, d["details"])
                return self._booking_reply(chat, B.update(self.store, tid, slots))
            self._pending(chat, clear=True)
            B.finish(self.store, tid, "cancelled")
        memory, cur = self._memory(text)
        kinds = "; ".join(f"{k}: {', '.join(v)}" for k, v in B.TEMPLATES.items())
        system = PERSONA.format(today=time.strftime("%A %d %B %Y"), memory=memory, kinds=kinds)
        talk = [*self.recent(chat)[:-1], {"role": "user", "content": text}]
        d = brain.extract([{"role": "system", "content": system}, *talk], DECIDE, self.chat)
        action = d.get("action", "reply")
        if action == "book" and d.get("kind") in B.TEMPLATES:
            slots = ground(self.store, d["kind"], text, d.get("details") or {})
            return self._booking_reply(chat, B.start(self.store, d["kind"], slots))
        if action == "read_swiggy_orders":
            last = self.store.fact("swiggy:orders_read")
            stale = (not B.taste(self.store).get("orders")) if not last else last["age_h"] >= FRESH_H
            if stale or re.search(r"\b(refresh|re-?read|again|update|latest)\b", text, re.I):
                return self._read_orders()
            # read recently: the model chose this on a suggestion question (seen live): answer from memory
            return self._say(system + "\n\nAnswer the owner's message using what they order on Swiggy (above), their "
                             "taste and mood. Suggest concrete dishes, briefly.", talk)
        if action == "places":
            found = self.nearby(cur.get("location"), 3000, d.get("cuisine", ""), limit=8)
            if "error" in found:
                return "Share your location on WhatsApp (attach → Location) and I'll look around you."
            return self._say(system + "\n\nPick the 3 best for the owner from ONLY these real places (name, km, one "
                             f"line why, maps link):\n{json.dumps(found['places'])}", talk)
        reply = str(d.get("reply") or "").strip()
        if reply and reply in self._said(chat):  # low temperature repeats itself ("same joke 3x")
            reply = self._say(system + "\n\nYou already gave this exact answer; give a different one.", talk, 0.9) or reply
        return reply or "Sorry, I didn't get that. Could you say it another way?"

    def _say(self, system: str, talk: list, temperature: float = 0.2) -> str:
        """Free text the owner reads as is (nothing parses it)."""
        msg = self.chat([{"role": "system", "content": system}, *talk], temperature=temperature)
        return (msg.get("content") or "").strip()

    def _said(self, chat: str, n: int = 5) -> list[str]:
        return [t for (t,) in self.db.execute("SELECT text FROM log WHERE chat=? AND role='bot' ORDER BY ts DESC LIMIT ?",
                                              (chat, n))]

    # Swiggy ----------------------------------------------------------------------------------------------------
    def _read_orders(self) -> str:
        self.notify("Reading your Swiggy order history…")
        try:
            orders = self.swiggy.orders()
        except (food.NotReady, food.cdp.Blocked) as e:
            return str(e)
        if not orders:
            return "I opened your Swiggy orders but couldn't read any orders from the page."
        r = B.save_orders(self.store, "swiggy", orders)
        self.store.set_fact("swiggy:orders_read", len(orders), "swiggy")
        t = r["order_history"]
        dishes = ", ".join(f"{d} ({n})" for d, n in t["top_dishes"][:4])
        places_ = ", ".join(f"{p} ({n})" for p, n in t["top_restaurants"][:3])
        return (f"Saved {r['saved']} new orders ({t['orders']} in all). You mostly order {dishes}; from {places_}; "
                f"usually about ₹{t['typical_spend']:g}.")

    def _find_food(self, chat: str, tid: int, slots: dict) -> str:
        dish = slots["dish"][0] if isinstance(slots["dish"], list) else slots["dish"]
        self.notify(f"Looking for {dish} on Swiggy…")
        try:
            offers = self.swiggy.search(dish)
        except (food.NotReady, food.cdp.Blocked) as e:
            return str(e)
        if not offers:
            B.finish(self.store, tid, "failed", f"no {dish} read from the search page", "swiggy")
            return (f"I opened Swiggy's search for {dish} but couldn't read any dishes with prices from the page. "
                    "Try a simpler dish name, or check that a delivery address is set in Swiggy.")
        said = f"{slots.get('diet', '')} {dish}".lower()
        veg = bool(re.search(r"\bveg(etarian)?\b", said)) and not re.search(r"non[- ]?veg", said)
        top = food.pick(offers, slots.get("max_total"), veg)
        if not top:
            B.finish(self.store, tid, "failed", f"no {dish} under ₹{slots.get('max_total')} on Swiggy", "swiggy")
            return (f"I found {len(offers)} {dish} options on Swiggy, but none fit"
                    f"{' veg and' if veg else ''} under ₹{slots.get('max_total'):g}. Want a higher budget?")
        self.store.set_fact(f"choice:{tid}", top, "swiggy", ttl_h=1)
        self._pending(chat, tid)
        lines = [f"{i}. {o['item']} — {o['restaurant']} — ₹{o['price']}" + (f" ★{o['rating']}" if o.get("rating") else "")
                 + (f" ({o['eta']})" if o.get("eta") else "") for i, o in enumerate(top, 1)]
        return "Cheapest on Swiggy right now:\n" + "\n".join(lines) + "\nWhich one? (reply 1, 2 or 3)"

    def _add_to_cart(self, chat: str, tid: int, offer: dict) -> str:
        self.notify(f"Adding {offer['item']} from {offer['restaurant']} to your Swiggy cart…")
        self._pending(chat, clear=True)
        try:
            r = self.swiggy.add(offer)
        except (food.NotReady, food.cdp.Blocked) as e:
            B.finish(self.store, tid, "failed", str(e), "swiggy")
            return str(e)
        if not r["in_cart"]:
            B.finish(self.store, tid, "failed", "item not in cart after ADD", "swiggy")
            return "I clicked ADD but don't see it in your cart. Please check Swiggy."
        B.finish(self.store, tid, "done", f"cart {offer['restaurant']} {offer['item']} ₹{r['total']}", "swiggy")
        return (f"In your Swiggy cart: {offer['item']} from {offer['restaurant']}. To pay: ₹{r['total'] or '?'}.\n"
                "I've stopped before ordering: open Swiggy to place it (cash on delivery or UPI).")

    def _summary_line(self, slots: dict, kind: str) -> str:
        return f"{kind.title()}: " + ", ".join(f"{k.replace('_', ' ')} {' + '.join(map(str, v)) if isinstance(v, list) else v}"
                                               for k, v in slots.items())

    def _booking_reply(self, chat: str, v: dict) -> str:
        if "error" in v:
            return "Sorry: " + v["error"]
        self._pending(chat, v["task_id"])
        if v.get("problems"):
            asks = [ASK.get(s, f"What's the {s}?") for s in v["missing"]]
            for p in v["problems"]:
                slot = p.split(":")[0]
                asks.insert(0, ASK.get(slot, f"What's the {slot}?") if "who they are" in p or "not a" in p else p + ".")
            return " ".join(dict.fromkeys(asks))
        if v.get("missing"):
            return " ".join(ASK.get(s, f"What's the {s.replace('_', ' ')}?") for s in v["missing"])
        return v["summary"] + " (YES / no)"

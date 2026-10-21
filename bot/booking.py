"""Bookings and orders as forms, not open goals: a Hermes tool module next to places.py (no Hermes imports).

A task is a template (flight, train, bus, cab, hotel, food, trip) with named slots. The model only fills the slots it
heard; code adds what memory knows, names what is still missing (one question), and writes the one-line summary the
owner says YES to. The small model never decides on its own what "done" means.

Memory here, by kind (the owner's standing rules and taste profile stay in memrouter):
- facts: exact values with a source and an expiry. ttl_h None = a profile fact (a traveller, home address);
  with ttl_h = a world fact (a price, a rating) that is stale after ttl_h hours and must be re-checked.
- prefs: what the owner chose for a habit slot (HABITS), per (kind, slot, value), with a count. A value becomes the default only after the owner
  chose it at least twice and in at least 60 % of the confirmed tasks of that kind: before that, ask.
- tasks: every draft, its status (draft -> confirmed -> done | failed | cancelled) and outcome: the episodes.
- notes: per site, what went wrong before (the site's "past mistakes"), shown when a task on that site starts.

Offers are checked against pages Hermes actually saw: an offer counts only if its quote is on a page from its
platform and the quote holds its price. Prices over the task's cap are dropped in code.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# kind -> slot -> required? (order = order in the summary). Defaults that are rails, not tastes, are in FIXED.
TEMPLATES = {
    "flight": {"from": True, "to": True, "date": True, "passengers": True, "time_window": False, "cabin": False,
               "max_price": True, "airline_avoid": False},
    "train": {"from": True, "to": True, "date": True, "passengers": True, "class": True, "quota": False,
              "max_price": False},
    "bus": {"from": True, "to": True, "date": True, "passengers": True, "seat": False, "time_window": False,
            "max_price": False},
    "cab": {"pickup": True, "drop": True, "when": True, "cab_type": False, "pay": True},
    "hotel": {"city": True, "checkin": True, "checkout": True, "guests": True, "area": False,
              "max_price_per_night": True},
    "food": {"dish": True, "restaurant": False, "diet": False, "max_total": True, "pay": True},
    "trip": {"to": True, "arrive": True, "leave": True, "interests": False, "budget": False},
}
FIXED = {"cab": {"pay": "cash"}, "food": {"pay": "cash on delivery or UPI request to the owner"}}
DATES = {"date", "checkin", "checkout", "arrive", "leave"}
PRICE = {"flight": "max_price", "train": "max_price", "bus": "max_price", "hotel": "max_price_per_night",
         "food": "max_total"}
PLATFORMS = {"swiggy": "swiggy.com", "zomato": "zomato.com", "magicpin": "magicpin.in", "eatsure": "eatsure.com",
             "makemytrip": "makemytrip.com", "goibibo": "goibibo.com", "cleartrip": "cleartrip.com",
             "ixigo": "ixigo.com", "redbus": "redbus.in", "irctc": "irctc.co.in", "abhibus": "abhibus.com",
             "uber": "uber.com", "ola": "olacabs.com", "rapido": "rapido.bike", "booking.com": "booking.com",
             "agoda": "agoda.com", "airbnb": "airbnb", "goindigo": "goindigo.in", "airindia": "airindia.com",
             "akasaair": "akasaair.com", "easemytrip": "easemytrip.com"}
MIN_N, MIN_SHARE = 2, 0.6
# only habits become defaults: where to, what to eat and how much to spend are said each time, or asked
HABITS = {"from", "passengers", "guests", "cabin", "class", "quota", "seat", "time_window", "airline_avoid", "cab_type",
          "pickup", "diet", "area"}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _money(x) -> float | None:
    """'₹3,000' -> 3000, '9k' -> 9000, '1.5 lakh' -> 150000."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b|thousand|lakh|lac)?", str(x if x is not None else ""), re.I)
    if not m:
        return None
    return float(m[1].replace(",", "")) * {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lac": 1e5}.get((m[2] or "").lower(), 1)


def _iso(v, today: date) -> str | None:
    """'2026-11-02', '2 Nov', 'today', 'tomorrow' -> ISO date; None if it isn't a date."""
    s = str(v).strip().lower()
    if s in ("today", "tonight"):
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if s == "day after tomorrow":
        return (today + timedelta(days=2)).isoformat()
    m = re.fullmatch(r"(this |next |coming )?(mon|tue|wed|thu|fri|sat|sun)[a-z]*", s)
    if m:  # the next such day after today; "next friday" said on a Wednesday is still this week's Friday
        ahead = (WEEKDAYS.index(m[2]) - today.weekday()) % 7 or 7
        return (today + timedelta(days=ahead)).isoformat()
    for fmt in ("%Y-%m-%d", "%d %b", "%d %B", "%b %d", "%B %d", "%d/%m/%Y", "%d/%m", "%d %b %Y", "%d %B %Y"):
        try:
            d = datetime.strptime(s.title() if "%b" in fmt or "%B" in fmt else s, fmt).date()
        except ValueError:
            continue
        if "%Y" not in fmt:
            d = d.replace(year=today.year)
            if d < today:
                d = d.replace(year=today.year + 1)
        return d.isoformat()
    return None


class Store:
    def __init__(self, path: Path | str, now=time.time):
        self.now = now
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, value TEXT, source TEXT, ts REAL, ttl_h REAL);
            CREATE TABLE IF NOT EXISTS prefs (kind TEXT, slot TEXT, value TEXT, n INTEGER, ts REAL,
                                              PRIMARY KEY (kind, slot, value));
            CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, kind TEXT, slots TEXT, status TEXT, site TEXT,
                                              outcome TEXT, parent INTEGER, ts REAL);
            CREATE TABLE IF NOT EXISTS notes (site TEXT, text TEXT, n INTEGER, ts REAL, PRIMARY KEY (site, text));""")

    def _q(self, sql, args=()):
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
            self.db.commit()
            return rows

    # facts ---------------------------------------------------------------------------------------------------
    def set_fact(self, key: str, value, source: str = "owner", ttl_h: float | None = None) -> None:
        self._q("INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), source, self.now(), ttl_h))

    def fact(self, key: str) -> dict | None:
        r = self._q("SELECT value, source, ts, ttl_h FROM facts WHERE key=?", (key,))
        if not r:
            return None
        value, source, ts, ttl = r[0]
        age_h = (self.now() - ts) / 3600
        return {"value": json.loads(value), "source": source, "age_h": round(age_h, 1),
                "stale": ttl is not None and age_h > ttl}

    def facts(self, prefix: str) -> dict:
        return {k: self.fact(k) for (k,) in self._q("SELECT key FROM facts WHERE key LIKE ? ORDER BY key",
                                                    (prefix + "%",))}

    # prefs ---------------------------------------------------------------------------------------------------
    def usual(self, kind: str, slot: str):
        """The owner's usual value for this slot, or None while the evidence is thin or split."""
        rows = self._q("SELECT value, n FROM prefs WHERE kind=? AND slot=? ORDER BY n DESC", (kind, slot))
        total = sum(n for _, n in rows)
        if rows and rows[0][1] >= MIN_N and rows[0][1] / total >= MIN_SHARE:
            return json.loads(rows[0][0])
        return None

    def learn(self, kind: str, slots: dict) -> None:
        for slot, v in slots.items():
            if slot in HABITS and slot in TEMPLATES[kind] and v not in (None, "", []):
                self._q("INSERT INTO prefs VALUES (?,?,?,1,?) ON CONFLICT(kind, slot, value) DO UPDATE SET n=n+1, ts=?",
                        (kind, slot, json.dumps(v, ensure_ascii=False, sort_keys=True), self.now(), self.now()))

    # notes ---------------------------------------------------------------------------------------------------
    def note(self, site: str, text: str) -> None:
        self._q("INSERT INTO notes VALUES (?,?,1,?) ON CONFLICT(site, text) DO UPDATE SET n=n+1, ts=?",
                (site.lower(), text.strip(), self.now(), self.now()))

    def notes(self, site: str) -> list[str]:
        return [f"{t} (seen {n}x)" if n > 1 else t for t, n in
                self._q("SELECT text, n FROM notes WHERE site=? ORDER BY n DESC, ts DESC LIMIT 8", (site.lower(),))]

    # tasks ---------------------------------------------------------------------------------------------------
    def task(self, tid: int) -> dict:
        r = self._q("SELECT id, kind, slots, status, site, outcome, parent FROM tasks WHERE id=?", (int(tid),))
        if not r:
            raise KeyError(f"no task #{tid}")
        i, kind, slots, status, site, outcome, parent = r[0]
        return {"id": i, "kind": kind, "slots": json.loads(slots), "status": status, "site": site,
                "outcome": outcome, "parent": parent}

    def _save(self, t: dict) -> int:
        with self.lock:
            cur = self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?)",
                                  (t.get("id"), t["kind"], json.dumps(t["slots"], ensure_ascii=False), t["status"],
                                   t.get("site"), t.get("outcome"), t.get("parent"), self.now()))
            self.db.commit()
            return t.get("id") or cur.lastrowid


# what small models write instead of the slot names (seen in bench/forms.py runs)
SYNONYMS = {"budget": "$price", "max_budget": "$price", "price": "$price", "max_price": "$price", "max_total": "$price",
            "destination": "to", "origin": "from", "source": "from", "travellers": "passengers", "travelers":
            "passengers", "people": "passengers", "time": "time_window", "class": "$class", "cabin": "$class",
            "date": "$date", "check_in": "checkin", "check_out": "checkout", "location": "city"}
PLACEHOLDER = re.compile(r"(current|usual|my|any|none|n/?a|unknown|tbd|default|not specified)"
                         r"( (location|city|place|site|app|address))?", re.I)
ME = {"me", "myself", "i", "self", "owner", "mine"}


def _slot(kind: str, k: str) -> str:
    k = k.lower().strip()
    if k in TEMPLATES[kind]:
        return k
    v = SYNONYMS.get(k, k)
    if v == "$price":
        return PRICE.get(kind, k)
    if v == "$class":
        return "cabin" if kind == "flight" else "class"
    if v == "$date":
        return {"cab": "when", "hotel": "checkin", "trip": "arrive"}.get(kind, "date")
    return v


def _people(store, v) -> list | None:
    """'me and Rahul', 'mom', ['me', 'Rahul'] -> names from the travellers; a bare count isn't names: None (ask)."""
    if isinstance(v, (int, float)) or str(v).strip().isdigit():
        me = store.fact("traveller:self")
        return [me["value"]["name"]] if int(v) == 1 and me else None
    parts = v if isinstance(v, list) else re.split(r",|&|\+|\band\b|\bwith\b", str(v))
    out = []
    for p in (str(x).strip() for x in parts):
        if not p:
            continue
        who = "self" if p.lower() in ME else re.sub(r"\W+", "_", p.lower()).strip("_")
        f = store.fact(f"traveller:{who}")
        out.append(f["value"]["name"] if f else p)
    return out or None


def _clean(store, kind: str, slots: dict, today: date) -> tuple[dict, list[str]]:
    out, bad = {}, []
    for k, v in (slots or {}).items():
        k = _slot(kind, k)
        if k not in TEMPLATES[kind] or k in FIXED.get(kind, {}) or v in (None, "", []):
            continue  # a rail (cab paid cash) is never the model's to change
        if isinstance(v, str) and PLACEHOLDER.fullmatch(v.strip()):
            continue  # "current location": not a value; memory fills it or the owner is asked
        if k in ("passengers", "guests"):
            n, v = v, _people(store, v)
            if v is None:
                bad.append(f"{k}: {n} people: ask the owner who they are (names)")
                continue
        if k in DATES:
            d = _iso(v, today)
            if not d or d < today.isoformat():
                bad.append(f"{k}: '{v}' is not a date from today on")
                continue
            v = d
        if k in PRICE.values() and _money(v) is None:
            bad.append(f"{k}: '{v}' is not an amount")
            continue
        out[k] = _money(v) if k in PRICE.values() else v
    return out, bad


def view(store: Store, t: dict, today: date) -> dict:
    """What the model sees: filled slots (and which came from memory), what's missing, and the summary to send."""
    kind, slots = t["kind"], dict(t["slots"])
    memory = {}
    for slot in TEMPLATES[kind]:
        if slot not in slots:
            v = FIXED.get(kind, {}).get(slot)
            if v is None:
                v = store.usual(kind, slot)
            if v is None and slot in ("passengers", "guests"):  # nobody named: the owner alone, if we know them
                me = store.fact("traveller:self")
                v = [me["value"]["name"]] if me else None
            if v is not None:
                slots[slot], memory[slot] = v, v
    problems = []
    for a, b in (("checkin", "checkout"), ("arrive", "leave")):
        if a in slots and b in slots and slots[b] <= slots[a]:
            problems.append(f"{b} must be after {a}")
    missing = [s for s, req in TEMPLATES[kind].items() if req and s not in slots]
    out = {"task_id": t["id"], "kind": kind, "status": t["status"], "slots": slots, "from_memory": memory,
           "missing": missing, "problems": problems}
    if t.get("site"):
        out["site_notes"] = store.notes(t["site"])
    if kind == "food":
        out["order_history"] = taste(store)
    if missing or problems:
        out["next"] = ("Fix: " + "; ".join(problems) + ". " if problems else "") + (
            f"Ask the owner one short question for: {', '.join(missing)}." if missing else "")
    elif t["status"] == "draft":
        shown = ", ".join(f"{k.replace('_', ' ')} {v if not isinstance(v, list) else ' + '.join(map(str, v))}"
                          + (" (usual)" if k in memory and k not in FIXED.get(kind, {}) else "")
                          for k, v in slots.items())
        out["summary"] = f"{kind.title()}: {shown}. Go ahead?"
        out["next"] = "Send the owner exactly this summary and wait for YES. Then call task_confirm."
    return out


def start(store: Store, kind: str, slots: dict, site: str = "", parent: int | None = None,
          today: date | None = None) -> dict:
    today = today or date.today()
    if kind not in TEMPLATES:
        return {"error": f"unknown kind {kind!r}; one of {', '.join(TEMPLATES)}"}
    clean, bad = _clean(store, kind, slots, today)
    t = {"kind": kind, "slots": clean, "status": "draft", "site": _site(site), "parent": parent}
    t["id"] = store._save(t)
    v = view(store, t, today)
    if bad:
        v["problems"] = bad + v["problems"]
        v["next"] = "Fix: " + "; ".join(v["problems"]) + ". " + v.get("next", "")
        v.pop("summary", None)
    return v


def update(store: Store, tid: int, slots: dict, site: str = "", today: date | None = None) -> dict:
    today = today or date.today()
    t = store.task(tid)
    if t["status"] != "draft":
        return {"error": f"task #{tid} is {t['status']}; start a new one to change it"}
    clean, bad = _clean(store, t["kind"], slots, today)
    t["slots"].update(clean)
    t["site"] = _site(site) or t["site"]
    store._save(t)
    v = view(store, t, today)
    if bad:
        v["problems"] = bad + v["problems"]
        v.pop("summary", None)
    return v


def confirm(store: Store, tid: int, today: date | None = None) -> dict:
    """The owner said YES to the summary: freeze the slots (memory's too) and learn what they chose."""
    today = today or date.today()
    t = store.task(tid)
    v = view(store, t, today)
    if t["status"] != "draft" or v["missing"] or v["problems"]:
        return {"error": f"task #{tid} can't be confirmed: {t['status']}, missing {v['missing']}, {v['problems']}"}
    t["slots"], t["status"] = v["slots"], "confirmed"
    store._save(t)
    store.learn(t["kind"], t["slots"])
    return {"task_id": tid, "status": "confirmed", "slots": t["slots"],
            "next": "Book it on the site up to the payment step. Rails ask the owner before anything is paid."}


def finish(store: Store, tid: int, outcome: str, note: str = "", site: str = "") -> dict:
    """done | failed | cancelled. A note on a failure becomes a site note: shown the next time on that site."""
    if outcome not in ("done", "failed", "cancelled"):
        return {"error": "outcome is done, failed or cancelled"}
    t = store.task(tid)
    t["status"], t["outcome"], t["site"] = outcome, note, _site(site) or t["site"]
    store._save(t)
    if note and outcome == "failed" and t["site"]:
        store.note(t["site"], note)
    return {"task_id": tid, "status": outcome}


def _site(s: str) -> str:
    s = (s or "").lower()
    return next((d for name, d in PLATFORMS.items() if name in s or d in s), s.split("/")[2] if "://" in s else s)


def compare(store: Store, tid: int, offers: list[dict], pages: dict[str, str]) -> dict:
    """Rank offers by what the owner pays (price + fees - discount). An offer counts only if its quote is on a page
    Hermes saw from that platform and the quote holds the price; over the task's cap it is dropped. pages: url -> text."""
    t = store.task(tid)
    cap = t["slots"].get(PRICE.get(t["kind"], ""))
    ok, dropped = [], []
    for o in offers:
        dom = _site(o.get("platform", "") or o.get("url", ""))
        price, fees, disc = _money(o.get("price")), _money(o.get("fees")) or 0, _money(o.get("discount")) or 0
        quote = " ".join(str(o.get("quote", "")).split())
        seen = [u for u, text in pages.items() if dom and dom in u.lower() and quote and quote in " ".join(text.split())]
        if price is None:
            dropped.append({**o, "why": "no price"})
        elif not seen:
            dropped.append({**o, "why": f"quote not found on any {dom or 'platform'} page you opened: open it, copy "
                                        "the exact text"})
        elif _money(quote) is None or price not in [_money(m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", quote)]:
            dropped.append({**o, "why": "the quote doesn't show the price"})
        else:
            pay = round(price + fees - disc, 2)
            if cap is not None and pay > cap:
                dropped.append({**o, "pay": pay, "why": f"over the cap {cap:g}"})
            else:
                ok.append({**o, "platform": dom, "pay": pay, "seen_on": seen[0]})
                store.set_fact(f"price:{dom}:{o.get('item', '')}".lower(), pay, seen[0], ttl_h=6)
    ok.sort(key=lambda o: (o["pay"], -(_money(o.get("rating")) or 0)))
    out = {"task_id": tid, "ranked": ok, "dropped": dropped}
    if len(ok) > 1:
        a, b = ok[0], ok[1]
        out["summary"] = (f"Cheapest: {a.get('item', '')} on {a['platform']} ₹{a['pay']:g}"
                          f" (₹{b['pay'] - a['pay']:g} less than {b['platform']})")
    return out


def plan_trip(store: Store, tid: int, today: date | None = None) -> dict:
    """From a confirmed trip or travel ticket: the hotel, the cab from arrival, and one food task per day, as drafts
    that each go through the same YES. Activities and restaurants come from searches, not from here."""
    today = today or date.today()
    t = store.task(tid)
    s = t["slots"]
    city = s.get("to")
    arrive = s.get("arrive") or s.get("date")
    leave = s.get("leave")
    if t["status"] not in ("confirmed", "done") or not city or not arrive:
        return {"error": f"task #{tid} needs to be a confirmed trip or ticket with a destination and a date"}
    kids = [start(store, "hotel", {"city": city, "checkin": arrive, "checkout": leave}, parent=tid, today=today),
            start(store, "cab", {"pickup": f"{city} arrival point", "when": arrive}, parent=tid, today=today)]
    days = []
    if leave:
        d = date.fromisoformat(arrive)
        while d <= date.fromisoformat(leave):
            days.append(d.isoformat())
            d += timedelta(days=1)
    return {"trip": tid, "city": city, "days": days or [arrive], "drafts": kids,
            "next": "Fill each draft (ask only what's missing), search activities and restaurants per day, then send "
                    "the owner one plan with a summary line per draft."}


def save_orders(store: Store, platform: str, orders: list[dict]) -> dict:
    """Past orders the owner made, read from the app's order history page: kept as done food tasks (each once)."""
    dom, new = _site(platform), 0
    if not orders:  # the small model calls this first, empty, then asks the owner to copy their orders: steps instead
        return {"error": "No orders given. Read them YOURSELF with the browser (the owner is logged in there; never ask "
                         "the owner to open pages or copy data): 1) browser_navigate to "
                         "https://www.swiggy.com/my-account/orders 2) browser_snapshot, scroll and snapshot again for "
                         "older orders 3) call save_orders with every order you saw: restaurant, items, total, date."}
    for o in orders:
        items = [str(i).strip() for i in (o.get("items") or []) if str(i).strip()]
        key = f"order:{dom}:" + (str(o.get("id") or "") or f"{o.get('restaurant', '')}|{o.get('date', '')}|{o.get('total', '')}").lower()
        if not items or store.fact(key):
            continue
        store.set_fact(key, True, dom)
        store._save({"kind": "food", "status": "done", "site": dom, "outcome": f"past order {o.get('date', '')}",
                     "slots": {"dish": items, "restaurant": o.get("restaurant", ""), "max_total": _money(o.get("total"))}})
        new += 1
    return {"saved": new, "order_history": taste(store)}


def dish_name(item: str) -> str:
    """'Chilli Bean Patty Sandwich (15cm) + Coke(Save 31%) x 1' -> 'chilli bean patty sandwich' (for counting)."""
    s = str(item).split("+")[0]
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s*x\s*\d+\s*$", "", s.strip(), flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def taste(store: Store) -> dict:
    """What the owner orders: top dishes and restaurants, typical spend (from done food tasks)."""
    from collections import Counter
    from statistics import median
    rows = store._q("SELECT slots FROM tasks WHERE kind='food' AND status='done'")
    dishes, places, spend = Counter(), Counter(), []
    for (raw,) in rows:
        sl = json.loads(raw)
        d = sl.get("dish")
        dishes.update(n for n in {dish_name(x) for x in (d if isinstance(d, list) else [d]) if x} if n)
        if sl.get("restaurant"):
            places[sl["restaurant"]] += 1
        if sl.get("max_total"):
            spend.append(sl["max_total"])
    if not rows:
        return {"orders": 0, "next": "No order history yet. Before recommending, read it YOURSELF: browser_navigate to "
                                     "https://www.swiggy.com/my-account/orders (owner is logged in), browser_snapshot, "
                                     "then save_orders with the orders shown."}
    return {"orders": len(rows), "top_dishes": dishes.most_common(5), "top_restaurants": places.most_common(5),
            "typical_spend": median(spend) if spend else None}


TOOLS = [
    {"name": "task_start",
     "description": "Start a booking or order as a form. Pass ONLY the details the owner actually said; memory fills "
                    "the usual ones. kind: flight, train, bus, cab, hotel, food or trip. Dates as YYYY-MM-DD or "
                    "'tomorrow'. Then do what 'next' says.",
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": list(TEMPLATES)},
         "slots": {"type": "object", "description": "slot -> value; slots per kind: " + "; ".join(
             f"{k}: {', '.join(v)}" for k, v in TEMPLATES.items())},
         "site": {"type": "string", "description": "the site or app, if the owner named one"}},
         "required": ["kind", "slots"]}},
    {"name": "task_update", "description": "Add or change details of a draft task (the owner answered a question).",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}, "slots": {"type": "object"},
                                                     "site": {"type": "string"}}, "required": ["task_id", "slots"]}},
    {"name": "task_confirm", "description": "The owner said YES to the task's summary.",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
    {"name": "task_finish",
     "description": "Record how a task ended: done, failed or cancelled. On failure, note what went wrong on the site "
                    "in one line (it is shown the next time on that site).",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"},
                                                     "outcome": {"type": "string", "enum": ["done", "failed", "cancelled"]},
                                                     "note": {"type": "string"}, "site": {"type": "string"}},
                    "required": ["task_id", "outcome"]}},
    {"name": "compare_offers",
     "description": "Compare the same item across platforms after opening each platform's page. For each offer give "
                    "platform, item, price, fees, discount, rating, url and quote: the exact text from that page that "
                    "shows the price. Offers whose quote isn't on a page you opened are dropped.",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"},
                                                     "offers": {"type": "array", "items": {"type": "object"}}},
                    "required": ["task_id", "offers"]}},
    {"name": "plan_trip", "description": "After a trip or ticket is confirmed: draft the hotel, the arrival cab and the "
                                         "days of the plan.",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
    {"name": "save_orders",
     "description": "Save the owner's past food orders read from the app's order history page (Swiggy: "
                    "https://www.swiggy.com/my-account/orders), so recommendations follow what they actually order. "
                    "Copy each order as shown: id if shown, restaurant, items, total, date.",
     "parameters": {"type": "object", "properties": {
         "platform": {"type": "string"},
         "orders": {"type": "array", "items": {"type": "object", "properties": {
             "id": {"type": "string"}, "restaurant": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}},
             "total": {"type": "string"}, "date": {"type": "string"}}}}},
         "required": ["platform", "orders"]}},
    {"name": "save_traveller",
     "description": "Save a traveller's details the owner gave (name, age, gender, berth or seat preference). "
                    "who='self' for the owner.",
     "parameters": {"type": "object", "properties": {"who": {"type": "string"}, "name": {"type": "string"},
                                                     "age": {"type": "integer"}, "gender": {"type": "string"},
                                                     "seat": {"type": "string"}}, "required": ["who", "name"]}},
]
NAMES = {t["name"] for t in TOOLS}


def handle(store: Store, name: str, args: dict, pages: dict[str, str]) -> dict:
    if name == "task_start":
        return start(store, args["kind"], args.get("slots") or {}, args.get("site", ""))
    if name == "task_update":
        return update(store, args["task_id"], args.get("slots") or {}, args.get("site", ""))
    if name == "task_confirm":
        return confirm(store, args["task_id"])
    if name == "task_finish":
        return finish(store, args["task_id"], args["outcome"], args.get("note", ""), args.get("site", ""))
    if name == "compare_offers":
        return compare(store, args["task_id"], args.get("offers") or [], pages)
    if name == "plan_trip":
        return plan_trip(store, args["task_id"])
    if name == "save_orders":
        return save_orders(store, args.get("platform", ""), args.get("orders") or [])
    if name == "save_traveller":
        who = re.sub(r"\W+", "_", args["who"].lower()).strip("_") or "self"
        store.set_fact(f"traveller:{who}", {k: args[k] for k in ("name", "age", "gender", "seat") if k in args})
        return {"saved": f"traveller:{who}"}
    return {"error": f"unknown tool {name}"}

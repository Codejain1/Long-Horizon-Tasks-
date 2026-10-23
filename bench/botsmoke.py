"""The bot's real turn on the real model, before it goes live: routing, booking outcome and reply time.

    python bench/botsmoke.py --model-url URL [--reps 2]

Runs bot/assistant.py (memory off, a scratch home) on forms.py's test2 requests (seen: they shaped the grounding),
test3 (held out) and plain chat. A booking
case passes when the first reply is the summary (gold: ready) or a question (gold: must ask) for the right kind;
a chat case passes when no booking starts. Prints pass rates and reply-time median / p90.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "bot"), str(ROOT / "bench")]
import assistant as A  # noqa: E402
import booking as B  # noqa: E402
import brain  # noqa: E402
import forms as F  # noqa: E402

CHAT = ["hi, how are you?", "what's 15% of 2400?", "thanks, that's all", "tell me a fun fact about Bangalore"]
# test3: written 2026-10-22 after the first botsmoke run (whose test2 replies shaped bot/assistant.py's grounding),
# before any run on these. (message, kind, ready, values checked on the saved task; dates as words, resolved by code)
TEST3 = [
    ("Book a bus from Bangalore to Hyderabad on Sunday for me and mom", "bus", True,
     {"date": "sunday", "passengers": ["kartik", "sunita"]}),
    ("Train for mom to Mysore tomorrow", "train", True, {"date": "tomorrow", "passengers": ["sunita"], "to": "mysore"}),
    ("I feel like having pav bhaji, keep it under 250", "food", True, {"dish": "pav bhaji", "max_total": 250}),
    ("Get me a cab to Indiranagar", "cab", False, {}),
    ("Flight to Kochi", "flight", False, {}),
    ("Hotel in Ooty this Saturday to Monday under 3000", "hotel", True,
     {"checkin": "saturday", "checkout": "monday", "max_price_per_night": 3000}),
    ("Plan a weekend trip to Pondicherry", "trip", False, {}),
    ("Order paneer tikka from Barbeque Nation under 600", "food", True, {"dish": "paneer tikka", "max_total": 600}),
    ("Can you book train tickets to Pune on 4 Dec for me and Rahul in 2A", "train", True,
     {"passengers": ["kartik", "rahul"], "class": "2a", "date": "4 dec"}),
    ("Cab to the airport tomorrow at 6am", "cab", True, {"drop": "airport"}),
]
# test4: written 2026-10-22 with the forced-JSON router, before any run on these.
TEST4 = [
    ("Need a bus from Pune to Goa this Friday night for me and Rahul, under 1,800", "bus", True,
     {"date": "friday", "passengers": ["kartik", "rahul"], "max_price": 1800}),
    ("Book a flight to Jaipur", "flight", False, {}),
    ("Cab from home to Koramangala at 8pm today", "cab", True, {"pickup": "home", "drop": "koramangala"}),
    ("I'm craving a margherita pizza, budget 450", "food", True, {"dish": "margherita pizza", "max_total": 450}),
    ("Hotel in Mysore for mom from 9 Nov to 11 Nov under 2,500 a night", "hotel", True,
     {"guests": ["sunita"], "checkin": "9 nov", "checkout": "11 nov", "max_price_per_night": 2500}),
    ("Train to Hubli tomorrow in sleeper", "train", True, {"date": "tomorrow", "class": "sl|sleeper"}),
    ("Order something for dinner", "food", False, {}),
    ("Plan a trip to Coorg from 14 Nov to 16 Nov", "trip", True, {"arrive": "14 nov", "leave": "16 nov"}),
]
# test5: written 2026-10-22 after the owner's live chat (login asked in words, suggestions), before any run on these.
LOGIN5 = [("please sign me in to redbus", "redbus"), ("can you log into ixigo for me", "ixigo"),
          ("login on makemytrip using my google account", "makemytrip"), ("sign in to swiggy", "swiggy")]
SUGGEST5 = ["what should I have for lunch?", "suggest something light for dinner", "I'm bored, any food ideas?",
            "what do I usually order?"]
# test6: the owner's live messages that went wrong on 2026-10-22 (as sent), before any run on these
LIVE6 = ["Find some cheapest good food near me to order and also order", "Why only Swiggy not Zomato and others",
         "Have you not checked on Zomato?", "And also pull out Zomato orders history"]
PROMISE = r"one moment|let me check|i'll check|i will check|checking now|give me a (moment|minute)"
CHAT3 = ["what's the capital of Australia?", "good morning!", "remind me what you can do", "any good cafes near me?"]


def values_ok(store, want: dict) -> bool:
    from datetime import date
    rows = store._q("SELECT slots FROM tasks ORDER BY id DESC LIMIT 1")
    got = __import__("json").loads(rows[0][0]) if rows else {}
    for k, v in want.items():
        g = got.get(k)
        if k in B.DATES:
            ok = g == B._iso(v, date.today())
        elif isinstance(v, str) and "|" in v:
            ok = g is not None and any(x in str(g).lower() for x in v.split("|"))
        elif isinstance(v, list):
            ok = isinstance(g, list) and sorted(str(x).split()[0].lower() for x in g) == sorted(v)
        elif isinstance(v, (int, float)):
            ok = B._money(g) == v
        else:
            ok = g is not None and v in str(g).lower()
        if not ok:
            print(f"      value {k}: got {g!r}, want {v!r}")
            return False
    return True


def down(url, body, timeout=5):
    raise OSError("memory off for the smoke test")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-url", required=True)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--think", action="store_true", help="Qwen reasoning on for routing")
    a = ap.parse_args()
    brain.URL = a.model_url
    import os
    os.environ["BOT_THINK"] = "1" if a.think else "0"
    rows = []
    for rep in range(a.reps):
        cases = ([c for c in F.CASES if c[0] == "test2"] + [("chat", m, None, None, None) for m in CHAT]
                 + [("test3", m, k, r, v) for m, k, r, v in TEST3] + [("chat3", m, None, None, None) for m in CHAT3]
                 + [("test4", m, k, r, v) for m, k, r, v in TEST4]
                 + [("login5", m, site, None, None) for m, site in LOGIN5] + [("suggest5", m, None, None, None) for m in SUGGEST5]
                 + [("live6", m, None, None, None) for m in LIVE6])
        for i, case in enumerate(cases):
            home = Path(tempfile.mkdtemp())
            store = F.seeded(home / "b.db")
            tried = []

            class NoBrowserLogin:  # the smoke test never touches the real Chrome
                def waiting_for(self, chat):
                    return None

                def start(self, chat, site, phone):
                    tried.append(site)
                    return "logged_in"

                def google(self, site, email):
                    tried.append(site)
                    return True
            class NoBrowserSwiggy:  # nor Swiggy (a run without it opened the owner's real order history)
                def orders(self):
                    tried.append("swiggy-orders")
                    return []

                def search(self, dish):
                    return []

                def add(self, offer):
                    raise AssertionError("never")
            class NoBrowserMagicpin:
                where = None

                def search(self, dish):
                    return []
            bot = A.Assistant(home / "h", store, post=down, logins=NoBrowserLogin(), swiggy=NoBrowserSwiggy(),
                              magicpin=NoBrowserMagicpin(), eatsure=NoBrowserMagicpin())
            bot.rail = type("NoRail", (), {"station": lambda self, n: {"code": "X", "name": n, "city": n},
                                            "trains": lambda self, a, b, d: []})()  # nor real ConfirmTkt
            bot.bus = type("NoBus", (), {"bus_city": lambda self, n: {"id": 1, "name": n},
                                         "buses": lambda self, a, b, d: []})()  # nor real abhibus
            bot.reviews = lambda restaurant: None  # nor Google Maps
            store.set_fact("owner:phone", "9800000001")
            t = time.time()
            try:
                reply = bot.handle({"channel": "smoke", "chat": "c", "text": case[1]})
            except Exception as e:
                reply = f"ERROR {e!r}"
            secs = time.time() - t
            task = store._q("SELECT kind FROM tasks")
            if case[0] in ("chat", "chat3"):
                ok = not task and not reply.startswith("ERROR")
            elif case[0] == "login5":
                ok = tried == [case[2]] and not task
            elif case[0] == "live6":
                dishes = store._q("SELECT slots FROM tasks")
                ok = (not re.search(PROMISE, reply, re.I) and not reply.startswith(("ERROR", "Sorry", "{"))
                      and not any(re.search(r"cheapest|good food", d[0], re.I) for d in dishes))
            elif case[0] == "suggest5":
                ok = not task and tried in ([], ["swiggy-orders"]) and not reply.lstrip().startswith(("ERROR", "Sorry", "{")) and not re.search(
                    r"\bI (found|searched|checked|ordered|booked)\b", reply, re.I)
            else:
                # complete = acted on at once (no "go ahead?" since 2026-10-23): the task got confirmed
                ready = bool(store._q("SELECT 1 FROM tasks WHERE status IN ('confirmed', 'done', 'failed')"))
                ok = bool(task) and task[0][0] == case[2] and ready == case[3]
                if ok and case[0] in ("test3", "test4"):
                    ok = values_ok(store, case[4])
            rows.append((case[0], ok, secs))
            print(f"{'ok  ' if ok else 'FAIL'} {secs:5.1f}s {case[1]!r} -> {reply[:110]!r}", flush=True)
    for kind in ("test2", "chat", "test3", "chat3", "test4", "login5", "suggest5", "live6"):
        rs = [r for r in rows if r[0] == kind]
        print(f"{kind}: {sum(r[1] for r in rs)}/{len(rs)} pass")
    ts = sorted(r[2] for r in rows)
    print(f"reply time: median {statistics.median(ts):.1f}s, p90 {ts[int(0.9 * len(ts)) - 1]:.1f}s, max {ts[-1]:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

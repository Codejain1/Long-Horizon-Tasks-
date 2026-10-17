"""Generality suite: where does the memory engine only work for the data it was shaped on?

    ~/horizon-evals/reader-venv/bin/python evals/memory/generality.py [--scale 20000]

Written BEFORE any fixes (2026-10-15) so it can't be tuned to: keep it held out. Every case runs the product engine
(deploy/hermes/memory_service.Memory: fine-tuned bge-small + LoCoMo ranker, dates resolved at save) inside 400
everyday filler messages spread over a year, then asks a question at a 500-token budget. A case passes when every
expected string is in the recalled context. Detector cases (rules, mood, taste, dates) call the save-time detectors
directly. Supersession cases also report whether the outdated value was served too.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "deploy" / "hermes")]  # flat copy on a server, or the repo
import memory_service as M  # noqa: E402
import writer as W  # noqa: E402

DAY = 86400
T0 = 1_696_118_400  # 1 Oct 2023

FILLER_PEOPLE = ["user", "Mom", "Rahul", "Priya", "Ankit", "Neha", "Hermes"]
FILLER = ["Are we still on for dinner tonight?", "Traffic is crazy, running late.", "Did you watch the match?",
          "Sent you the photos from the weekend.", "Call me after the meeting.", "Mom made rajma today.",
          "Please check the invoice I forwarded.", "Happy birthday! Have a great one.", "The cab is here.",
          "Can you share the slides?", "Gym was tough today.", "Let's catch up next week.", "Raining heavily here.",
          "Got the parcel, thanks!", "What time is the standup?", "Movie was average honestly.",
          "Ordered groceries for the week.", "My phone battery died.", "The office AC is broken again.",
          "Good morning! Have a nice day."]

# (kind, name, turns [(speaker, text, day)], question, expect [substrings], stale [outdated substrings])
CASES = [
    ("notes", "wifi code by meaning", [("note", "Wi-Fi password at the Goa villa is sunset2024", 30)],
     "what's the internet code at the beach house?", ["sunset2024"], []),
    ("notes", "bullet note", [("note", "- Dentist: Dr. Rao, Indiranagar, Tuesdays only", 60)],
     "which day can I see my dentist?", ["Tuesdays"], []),
    ("notes", "renewal by synonym", [("note", "Car insurance renews on 14 March with ACKO, policy 88213", 90)],
     "when does my vehicle cover expire?", ["14 March"], []),
    ("paraphrase", "lactose", [("user", "I can't digest lactose at all", 20)],
     "can I have a milkshake?", ["lactose"], []),
    ("paraphrase", "sister city", [("user", "my sister Riya lives in Pune now", 45)],
     "which city is Riya in?", ["Pune"], []),
    ("paraphrase", "holiday cabin", [("user", "Booked the cabin at Coorg for the long weekend", 100)],
     "where are we going for the holidays?", ["Coorg"], []),
    ("supersession", "internet vendor", [("user", "our wifi provider is ACT Fibernet", 10),
                                         ("user", "we switched the internet to Airtel Xstream", 200)],
     "who is our internet provider?", ["Airtel"], ["ACT"]),
    ("supersession", "address", [("user", "my address is 12 MG Road, Bangalore", 5),
                                 ("user", "I moved, new address is 44 HSR Layout", 180)],
     "what's my current address?", ["HSR"], ["MG Road"]),
    ("supersession", "meeting moved", [("user", "meeting with Sam is on Friday at 4", 250),
                                       ("user", "Sam's meeting got moved to Monday 11 am", 252)],
     "when is my meeting with Sam?", ["Monday"], ["Friday at 4"]),
    ("agent said", "url given", [("user", "deploy the splitwise app", 300),
                                 ("Hermes", "Your app is live at https://splitwise-xyz.trycloudflare.com", 300)],
     "what was the URL you gave me for the app?", ["trycloudflare"], []),
    ("agent said", "reminder set", [("user", "remind me to call mom", 310),
                                    ("Hermes", "Done, I've set a reminder for 6 pm to call mom", 310)],
     "what reminder did you set for me?", ["6 pm"], []),
    ("counting", "pizza orders", [("user", "ordered pizza from Dominos for movie night", 15),
                                  ("user", "pizza again from Dominos, Friday treat", 80),
                                  ("user", "got a Dominos pizza after the match", 150),
                                  ("user", "ordered pizza from Dominos for the team", 260)],
     "how many times did I order pizza?", ["movie night", "Friday treat", "after the match", "for the team"], []),
    ("time order", "after goa", [("user", "back from the Goa trip, amazing beaches", 40),
                                 ("user", "started going to the gym today", 52),
                                 ("user", "visited Ooty with college friends", 120)],
     "what did I start doing right after the Goa trip?", ["gym"], []),
    ("hinglish", "interview", [("user", "kal mera interview hai Infosys mein", 70)],
     "where is my interview?", ["Infosys"], []),
    ("hinglish", "food liked", [("user", "mujhe paneer tikka bahut pasand hai", 75)],
     "what food do I like?", ["paneer"], []),
    ("long doc", "deposit date", [("note", " ".join(
        ["The landlord confirmed the lease terms for the new flat in HSR Layout and shared the society rules."] * 12
        + ["The security deposit of 45,000 rupees is due by 3 November."]
        + ["Maintenance is collected quarterly and parking is allotted on a first come basis."] * 12), 160)],
     "when is the deposit due?", ["3 November"], []),
    ("group chat", "who brings speakers", [("Ankit", "I'll bring the speakers for the party", 210),
                                           ("Priya", "I'll get the cake", 210), ("Neha", "I'll do decorations", 210),
                                           ("Rahul", "I'll handle drinks", 210)],
     "who is bringing the speakers?", ["Ankit"], []),
]

RULES = [("please make sure you always reply in short points", True), ("I prefer you don't send long messages", True),
         ("whenever I ask for food, ask my budget first", True), ("keep answers under 3 lines from now", True),
         ("no emojis please", True), ("mujhe code mat dikhao, sirf result do", True),
         ("I never went to Paris", False), ("don't tell mom about the party", False),
         ("I always forget my keys", False)]
# accepted labels fixed before the writer was first run (2026-10-15): label granularity, not tuning
MOODS = [("I'm feeling really tired today", {"tired"}), ("I'm not tired at all, full of energy", {"happy", "relaxed"}),
         ("aaj bahut thak gaya hoon", {"tired"}), ("this traffic is driving me crazy", {"angry", "stressed"}),
         ("I'm on top of the world, got the offer!", {"celebrating", "happy"}), ("the low tide was beautiful", {None})]
TASTE = [("I am vegetarian", True), ("I gave up meat last year", True), ("no onion garlic for me please", True),
         ("I'm not a fan of sushi", True), ("main jain khana khata hoon", True), ("the restaurant was vegetarian", False)]
# said on Tuesday 3 October 2023; the stored memory must carry the exact date (the first version only checked
# that *something* was added, which let "day after tomorrow" -> tomorrow's date pass)
DATES = [("we fly on Friday", "6 October"), ("let's meet in two weeks", "17 October"),
         ("the day after tomorrow works", "5 October"), ("rent is due on the 20th", "20 October"),
         ("kal milte hain", "4 October"), ("next Tuesday evening", "10 October"), ("I joined three months ago", "July 2023")]
SAID = 1_696_311_000  # 3 Oct 2023, 11:00 IST


def filler_db(engine, n: int, home: Path) -> None:
    rnd = random.Random(7)
    mem = M.Memory(home, *engine)
    for k in range(n):
        mem.add(f"{rnd.choice(FILLER)}", rnd.choice(FILLER_PEOPLE), f"chat{k % 5}", T0 + rnd.randrange(365) * DAY
                + rnd.randrange(DAY))
    mem.db.close()


def run_case(engine, base: Path, turns, question, expect, stale, writer=None):
    home = Path(tempfile.mkdtemp())
    shutil.copy(base / "memory.db", home / "memory.db")
    mem = M.Memory(home, *engine, writer=writer)
    for speaker, text, day in turns:
        mem.add(text, speaker, "chat-x", T0 + day * DAY + 12 * 3600, owner=speaker in ("user", "note"),
                role="assistant" if speaker == "Hermes" else "")
    mem.search("warm up", 10)  # build the index first: latency below is a query, not the rebuild after a write
    t = time.perf_counter()
    # exactly what the Hermes plugin asks for
    r = mem.search(question, 500, ("Hermes",), ("cron_",), ("agent_claim", "chitchat"))
    ms = 1000 * (time.perf_counter() - t)
    ctx = r["context"]
    ok = all(e.lower() in ctx.lower() for e in expect)
    stale_shown = [s for s in stale if s.lower() in ctx.lower()]
    shutil.rmtree(home, ignore_errors=True)
    return ok, stale_shown, ms, sum(e.lower() in ctx.lower() for e in expect), ctx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=400)
    ap.add_argument("--model", default=str(Path.home() / "horizon-evals" / "embed-test"))
    ap.add_argument("--ranker", default=str(Path.home() / "horizon-evals" / "ranker-embed-test.pkl"))
    ap.add_argument("--writer-url", default="", help="OpenAI-compatible endpoint: run with the memory writer")
    args = ap.parse_args()
    engine = (M.Encoder(args.model), __import__("pickle").loads(Path(args.ranker).read_bytes()))
    wr = W.Writer(args.writer_url) if args.writer_url else None
    print(f"mode: {'memory writer (' + args.writer_url + ')' if wr else 'rule-based (no writer)'}", flush=True)
    base = Path(tempfile.mkdtemp())
    t = time.perf_counter()
    filler_db(engine, args.scale, base)
    print(f"filler: {args.scale} messages over a year ({time.perf_counter() - t:.0f}s)\n", flush=True)
    by_kind: dict[str, list] = {}
    for kind, name, turns, q, expect, stale in CASES:
        ok, stale_shown, ms, got, ctx = run_case(engine, base, turns, q, expect, stale, wr)
        marked = all(any(s.lower() in ln.lower() and "OUTDATED" in ln for ln in ctx.splitlines()) for s in stale_shown)
        if stale and stale_shown and not marked:
            ok = False  # an old value served as if current is a wrong answer waiting to happen
        by_kind.setdefault(kind, []).append(ok)
        note = (f"  (outdated also served: {stale_shown}, {'marked OUTDATED' if marked else 'NOT marked'})"
                if stale_shown else "")
        print(f"  {'PASS' if ok else 'FAIL'}  {kind:13} {name:22} {got}/{len(expect)} found  {ms:5.0f} ms{note}", flush=True)
    import dates as D
    from datetime import datetime
    from zoneinfo import ZoneInfo
    said = datetime.fromtimestamp(SAID, ZoneInfo("Asia/Kolkata"))
    if wr:  # the writer's kinds, mood labels and cleaned text
        out = {t: wr.write(t, "Kartik (the owner)", said) for t, *_ in RULES + MOODS + TASTE + DATES}
        kinds = lambda t: {m["kind"] for m in out[t]}  # noqa: E731
        mood = lambda t: next((m["mood"] for m in out[t] if m["kind"] == "mood"), None)  # noqa: E731
        det = {"rules": [(("rule" in kinds(t)) == want, t) for t, want in RULES],
               "mood": [(mood(t) in want, t) for t, want in MOODS],
               "taste": [(("preference" in kinds(t)) == want, t) for t, want in TASTE],
               "dates": [(any(exp.lower() in m["memory"].lower() for m in out[t]), t) for t, exp in DATES]}
    else:
        det = {"rules": [(M.is_rule(t) == want, t) for t, want in RULES],
               "mood": [(M.mood_of(t) in want, t) for t, want in MOODS],
               "taste": [(M.is_profile(t) == want, t) for t, want in TASTE],
               "dates": [(exp.lower() in D.resolve(t, said).lower(), t) for t, exp in DATES]}
    print()
    for k, rows in det.items():
        for ok, text in rows:
            print(f"  {'PASS' if ok else 'FAIL'}  {k:13} {text}")
        by_kind[k] = [ok for ok, _ in rows]
    print("\nsummary (pass / total):")
    for k, v in by_kind.items():
        print(f"  {k:13} {sum(v)}/{len(v)}")
    allv = [x for v in by_kind.values() for x in v]
    print(f"  {'ALL':13} {sum(allv)}/{len(allv)}")
    shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

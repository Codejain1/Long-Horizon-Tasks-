"""Does the form help the small model understand a booking request? Minutes, not hours.

    python bench/forms.py run --model-url URL --out DIR [--reps 3] [--split test]
    python bench/forms.py report --out DIR

Each case is one owner message, the same owner memory for both arms (travellers and past confirmed bookings), and the
right outcome: either the booking is ready with these values, or the assistant must ask (and must not have filled
the missing details itself).
- plain: the model gets the memory as text, the slot list per kind and the rules, and answers in JSON (details,
  ready, ask).
- forms: the model calls task_start with what the owner said; booking.py fills habits from memory and decides what's
  missing.
A case passes only if the kind is right and (ready: every checked value matches) or (ask: not ready and none of the
missing details filled in). The dev cases may be looked at while changing prompts; the test cases may not.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runner"), str(ROOT / "hermes" / "embed_memory"), str(ROOT / "bench")]
import booking as B  # noqa: E402
import longrun as LR  # noqa: E402

TODAY = date(2026, 10, 21)  # a Wednesday; fixed so the cases' dates stay right
TRAVELLERS = {"self": {"name": "Kartik", "age": 27, "gender": "M", "seat": "lower berth"},
              "mom": {"name": "Sunita", "age": 55, "gender": "F"}}
HISTORY = ([("train", {"from": "Bangalore", "to": "Chennai", "class": "3A", "passengers": ["Kartik"]})] * 3
           + [("flight", {"from": "Bangalore", "to": "Delhi", "cabin": "economy", "passengers": ["Kartik"],
                          "max_price": 8000})] * 2
           + [("bus", {"from": "Bangalore", "to": "Goa", "seat": "sleeper AC", "passengers": ["Kartik"]})]
           + [("food", {"dish": "paneer biryani", "diet": "veg", "max_total": 400})] * 2
           + [("cab", {"pickup": "home", "drop": "airport", "cab_type": "sedan"})] * 2)

# (split, message, kind, ready, checked values or the slots that must be asked). "a|b" = either.
CASES = [
    ("dev", "Book a train to Mumbai this Friday", "train", True,
     {"to": "mumbai", "date": "2026-10-23", "from": "bangalore", "class": "3a", "passengers": ["kartik"]}),
    ("dev", "Need a flight to Delhi on 5 Nov", "flight", False, ["max_price"]),
    ("dev", "Bus from Bangalore to Goa tomorrow night", "bus", True,
     {"from": "bangalore", "to": "goa", "date": "2026-10-22", "passengers": ["kartik"]}),
    ("dev", "Order paneer biryani, keep it under 350", "food", True, {"dish": "paneer biryani", "max_total": 350}),
    ("dev", "Book a cab to the airport at 5am tomorrow", "cab", True, {"pickup": "home", "drop": "airport", "when": "5"}),
    ("dev", "Get me a hotel in Goa", "hotel", False, ["checkin", "checkout", "max_price_per_night"]),
    ("dev", "Train to Chennai for me and mom on 2 Nov", "train", True,
     {"to": "chennai", "date": "2026-11-02", "class": "3a", "passengers": ["kartik", "sunita"]}),
    ("dev", "I'm hungry", "food", False, ["dish", "max_total"]),
    ("dev", "Fly me to Mumbai next Saturday, budget 6000", "flight", True,
     {"to": "mumbai", "date": "2026-10-24", "max_price": 6000, "from": "bangalore"}),
    ("dev", "Plan a trip to Jaipur from 10 to 13 Nov", "trip", True,
     {"to": "jaipur", "arrive": "2026-11-10", "leave": "2026-11-13"}),
    ("test", "Book sleeper class train to Hyderabad on Monday", "train", True,
     {"to": "hyderabad", "date": "2026-10-26", "class": "sl|sleeper", "from": "bangalore"}),
    ("test", "Flight to Kolkata tomorrow under 9k", "flight", True,
     {"to": "kolkata", "date": "2026-10-22", "max_price": 9000, "passengers": ["kartik"]}),
    ("test", "Cab from office to home now, sedan", "cab", True, {"pickup": "office", "drop": "home", "when": "now"}),
    ("test", "Order something veg under 300", "food", False, ["dish"]),
    ("test", "Book a hotel in Udaipur 14-16 Nov under 4000 a night", "hotel", True,
     {"city": "udaipur", "checkin": "2026-11-14", "checkout": "2026-11-16", "max_price_per_night": 4000}),
    ("test", "Train to Pune", "train", False, ["date"]),
    ("test", "Bus from Bangalore to Hampi on Friday for me and Rahul", "bus", True,
     {"to": "hampi", "date": "2026-10-23", "passengers": ["kartik", "rahul"]}),
    ("test", "Flight to Bali", "flight", False, ["date", "max_price"]),
    ("test", "Masala dosa from MTR, budget 250", "food", True,
     {"dish": "masala dosa", "restaurant": "mtr", "max_total": 250}),
    ("test", "Book train tickets to Chennai on 31 Oct, 2A this time", "train", True,
     {"to": "chennai", "date": "2026-10-31", "class": "2a", "passengers": ["kartik"]}),
    ("test", "Hotel near Baga beach Goa from 7 Nov to 9 Nov for me and mom, max 5000", "hotel", True,
     {"city": "goa", "checkin": "2026-11-07", "checkout": "2026-11-09", "guests": ["kartik", "sunita"],
      "max_price_per_night": 5000}),
    ("test", "Cab to the railway station on 2 Nov at 6am", "cab", True,
     {"pickup": "home", "drop": "station", "when": "6"}),
    ("test", "Book a flight to Delhi on 31 Sep", "flight", False, ["date"]),
    ("test", "Plan my Goa trip", "trip", False, ["arrive", "leave"]),
    ("test", "Order pizza", "food", False, ["max_total"]),
    ("test", "Train from Delhi to Agra on 15 Nov for mom", "train", True,
     {"from": "delhi", "to": "agra", "date": "2026-11-15", "passengers": ["sunita"]}),
    ("test", "Bus from Mysore to Bangalore day after tomorrow, AC sleeper", "bus", True,
     {"from": "mysore", "to": "bangalore", "date": "2026-10-23"}),
    ("test", "Fly to Chennai on 2 Nov, business class, under 20000", "flight", True,
     {"to": "chennai", "date": "2026-11-02", "cabin": "business", "max_price": 20000}),
    ("test", "Cab to airport", "cab", False, ["when"]),
    ("test", "Hotel in Mumbai, check in tonight, check out Friday, under 3000", "hotel", True,
     {"city": "mumbai", "checkin": "2026-10-21", "checkout": "2026-10-23", "max_price_per_night": 3000}),
]


def memory_text() -> str:
    t = [f"- traveller {who}: " + ", ".join(f"{k} {v}" for k, v in d.items()) for who, d in TRAVELLERS.items()]
    t += [f"- past confirmed {kind} booking: " + ", ".join(f"{k} {v}" for k, v in s.items()) for kind, s in HISTORY]
    return "\n".join(t)


def seeded(path: Path) -> B.Store:
    s = B.Store(path)
    for who, d in TRAVELLERS.items():
        s.set_fact(f"traveller:{who}", d)
    for kind, slots in HISTORY:
        s.learn(kind, slots)
    return s


def norm(slot: str, v):
    if isinstance(v, list):
        return sorted(str(x).split()[0].lower() for x in v)
    if slot in B.DATES:
        return B._iso(v, TODAY)
    if slot in B.PRICE.values():
        return B._money(v)
    return str(v).lower()


def score(case, kind: str, ready: bool, slots: dict) -> tuple[bool, str]:
    _, _, want_kind, want_ready, want = case
    if kind != want_kind:
        return False, f"kind {kind} != {want_kind}"
    if not want_ready:
        filled = [s for s in want if slots.get(s) not in (None, "", [])]
        if ready or filled:
            return False, f"should ask for {want}; ready={ready}, filled {filled}"
        return True, ""
    if not ready:
        return False, "should be ready"
    for slot, gold in want.items():
        got = norm(slot, slots.get(slot)) if slots.get(slot) not in (None, "") else None
        ok = (got == gold if isinstance(gold, (list, int, float)) or slot in B.DATES
              else got is not None and any(g in got for g in gold.split("|")))
        if not ok:
            return False, f"{slot}: {got!r} != {gold!r}"
    return True, ""


PLAIN = """You are a booking assistant. Today is {today} ({weekday}). What you know about the owner:
{memory}

Kinds and their details (* = needed before booking):
{kinds}
Rules: use only what the owner said or what the memory above shows as a clear habit; never invent a detail. If a
needed detail is unknown, ask for it. Dates as YYYY-MM-DD. Cabs are paid cash.
Reply with JSON only: {{"kind": ..., "details": {{detail: value}}, "ready": true|false, "ask": "question or empty"}}"""

FORMS = """You are a booking assistant. Today is {today} ({weekday}). Call task_start with ONLY the details the owner
said in this message (memory fills their usual choices). Dates as YYYY-MM-DD or as said ('tomorrow', 'friday')."""


def run_case(arm: str, case, url: str, work: Path) -> dict:
    msg = case[1]
    hdr = {"today": TODAY.isoformat(), "weekday": TODAY.strftime("%A")}
    if arm == "plain":
        kinds = "\n".join(f"- {k}: " + ", ".join(s + ("*" if r else "") for s, r in v.items())
                          for k, v in B.TEMPLATES.items())
        sys_ = PLAIN.format(memory=memory_text(), kinds=kinds, **hdr)
        r = LR.chat(url, [{"role": "system", "content": sys_}, {"role": "user", "content": msg}], json_mode=True)
        m = re.search(r"\{.*\}", r.get("content") or "", re.S)
        out = json.loads(m[0]) if m else {}
        details = {k: v for k, v in (out.get("details") or {}).items()}
        return {"kind": out.get("kind"), "ready": bool(out.get("ready")), "slots": details, "raw": out}
    store = seeded(work / f"{abs(hash((msg, arm)))}.db")
    r = LR.chat(url, [{"role": "system", "content": FORMS.format(**hdr)}, {"role": "user", "content": msg}],
                tools=[{"type": "function", "function": B.TOOLS[0]}])
    calls = r.get("tool_calls") or []
    if not calls:
        return {"kind": None, "ready": False, "slots": {}, "raw": r.get("content")}
    args = calls[0]["function"]["arguments"]
    args = json.loads(args) if isinstance(args, str) else args
    v = B.start(store, args.get("kind", ""), args.get("slots") or {}, today=TODAY)
    return {"kind": v.get("kind", args.get("kind")), "ready": "summary" in v, "slots": v.get("slots", {}), "raw": args}


def run(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="forms-"))
    jobs = [(i, arm, r) for r in range(args.reps) for i, c in enumerate(CASES) if args.split in ("all", c[0])
            for arm in args.arms.split(",")]
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not jobs:
                    return
                i, arm, rep = jobs.pop(0)
            try:
                got = run_case(arm, CASES[i], args.model_url, work)
                ok, why = score(CASES[i], got["kind"], got["ready"], got["slots"])
                rec = {"case": i, "split": CASES[i][0], "arm": arm, "rep": rep, "pass": ok, "why": why, **got}
            except Exception as e:  # a harness error is not a model failure: recorded and kept out of the score
                rec = {"case": i, "split": CASES[i][0], "arm": arm, "rep": rep, "error": repr(e)[:500]}
            with lock, (out / "results.jsonl").open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    threads = [threading.Thread(target=worker) for _ in range(args.streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def report(out: str) -> str:
    from tickets import wilson
    rows = [json.loads(x) for x in (Path(out) / "results.jsonl").read_text().splitlines() if x.strip()]
    errors = [r for r in rows if "error" in r]
    rows = [r for r in rows if "error" not in r]
    lines = []
    for split in ("dev", "test"):
        for arm in sorted({r["arm"] for r in rows}):
            rs = [r for r in rows if r["arm"] == arm and r["split"] == split]
            if not rs:
                continue
            k = sum(r["pass"] for r in rs)
            lo, hi = wilson(k, len(rs))
            ask = [r for r in rs if not CASES[r["case"]][3]]
            lines.append(f"{split:4} {arm:6} {k}/{len(rs)} = {100 * k / len(rs):3.0f}% [{100 * lo:.0f}, {100 * hi:.0f}]"
                         f"   asks when it should: {sum(r['pass'] for r in ask)}/{len(ask)}")
        base = {(r["case"], r["rep"]): r["pass"] for r in rows if r["arm"] == "plain" and r["split"] == split}
        pairs = [(r["pass"], base[(r["case"], r["rep"])]) for r in rows
                 if r["arm"] == "forms" and r["split"] == split and (r["case"], r["rep"]) in base]
        if pairs:
            w, l_ = sum(a and not b for a, b in pairs), sum(b and not a for a, b in pairs)
            lines.append(f"{split:4} forms vs plain: only forms {w}, only plain {l_} (of {len(pairs)} pairs)"
                         + ("" if w + l_ >= 6 else "  (too few disagreements to call)"))
    fails = [r for r in rows if not r["pass"] and r["split"] == "dev"]
    for r in fails[:12]:
        lines.append(f"  dev fail {r['arm']}: {CASES[r['case']][1]!r}: {r['why']}")
    if errors:
        lines.append(f"HARNESS ERRORS ({len(errors)}, not scored): " + "; ".join(e["error"][:120] for e in errors[:3]))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--model-url", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--arms", default="plain,forms")
    r.add_argument("--reps", type=int, default=3)
    r.add_argument("--split", default="all", choices=["dev", "test", "all"])
    r.add_argument("--streams", type=int, default=8)
    p = sub.add_parser("report")
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a)
    print(report(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

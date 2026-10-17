"""Practice set for the memory writer's prompt: tune on THESE, never on generality.py (the held-out test).

    python writer_dev.py --writer-url http://127.0.0.1:8000/v1

Different phrasings from the held-out suite, same kinds of judgement: rules vs one-off requests vs the owner's own
habits, moods vs descriptions, taste vs other people's taste, and dates (said on Tuesday 3 October 2023).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import writer as W  # noqa: E402

RULES = [("stop using bullet points with me", True), ("from today reply to me in Hindi", True),
         ("please always ask before booking anything", True), ("keep it formal when you write to my clients", True),
         ("I'd like you to skip the greetings", True), ("mujhe lambe messages mat bhejo", True),
         ("I never eat after 9 pm", False), ("I always wanted to visit Japan", False),
         ("please send me the report", False), ("don't forget to buy milk on the way", False)]
MOODS = [("ugh, what a terrible day", {"sad", "angry", "stressed"}), ("feeling super chill this sunday", {"relaxed"}),
         ("I'm not stressed anymore, finally", {"relaxed", "happy"}), ("yaar bahut bore ho raha hai", {"bored"}),
         ("the movie was really sad", {None}), ("got promoted!!!", {"celebrating", "happy"}),
         ("my head is pounding since morning", {"sick"}), ("the hotel looked so peaceful", {None})]
TASTE = [("I can't stand coriander", True), ("I'm trying keto these days", True), ("mere ko meetha pasand nahi", True),
         ("loved the ramen at Ippudo, my new favourite", True), ("my friend is vegan", False),
         ("the cafe serves vegan food", False)]
DATES = [("see you on Sunday", "8 October"), ("I'll be done in 3 days", "6 October"), ("parso milte hain", "5 October"),
         ("the fees are due on the 1st", "1 November"), ("let's go next month", "November 2023"),
         ("a week from today works", "10 October"), ("we spoke last Monday", "2 October"),
         ("I quit smoking two years ago", "2021")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--writer-url", required=True)
    args = ap.parse_args()
    wr = W.Writer(args.writer_url)
    said = datetime(2023, 10, 3, 11, tzinfo=ZoneInfo("Asia/Kolkata"))
    out = {t: wr.write(t, "Kartik (the owner)", said) for t, *_ in RULES + MOODS + TASTE + DATES}
    kinds = lambda t: [m["kind"] for m in out[t]]  # noqa: E731
    mood = lambda t: next((m["mood"] for m in out[t] if m["kind"] == "mood"), None)  # noqa: E731
    rows = ([("rules", ("rule" in kinds(t)) == w, t, kinds(t)) for t, w in RULES]
            + [("mood", mood(t) in w, t, mood(t)) for t, w in MOODS]
            + [("taste", ("preference" in kinds(t)) == w, t, kinds(t)) for t, w in TASTE]
            + [("dates", any(e.lower() in m["memory"].lower() for m in out[t]), t, [m["memory"] for m in out[t]])
               for t, e in DATES])
    for k, ok, t, got in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {k:6} {t:45} -> {got}")
    for k in ("rules", "mood", "taste", "dates"):
        v = [ok for kk, ok, *_ in rows if kk == k]
        print(f"  {k:6} {sum(v)}/{len(v)}")
    print(f"  ALL    {sum(ok for _, ok, *_ in rows)}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Resolve relative time at save time: "last year" said on 5 Aug 2023 is stored as "last year (2022)".

A memory read months later has lost "now"; answering "when did John renovate the center?" from "last year" fails
(3 of the 6 LoCoMo losses where all evidence was sent). Rules only, standard library only. The phrase stays and the
absolute date is added after it, in the style of LoCoMo's answers ("Friday before 17 September 2023", "2022").
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
       "nine": 9, "ten": 10, "a couple of": 2}
_D = "|".join(DAYS)
_PHRASE = re.compile(
    rf"\b(the day after tomorrow|day after tomorrow|the day before yesterday|day before yesterday|"
    rf"yesterday|today|tonight|tomorrow|last night|"
    rf"(?:last|next|this) (?:week|weekend|month|year|{_D})|last summer|on (?:{_D})|"
    rf"(?:a few|a couple of|\d+|{'|'.join(k for k in NUM if k != 'a couple of')}) (?:day|week|month|year)s? ago|"
    rf"in (?:a couple of|\d+|{'|'.join(k for k in NUM if k != 'a couple of')}) (?:day|week|month|year)s?|"
    rf"(?:a|one|two|\d+) weeks? from (?:today|now)|on the \d{{1,2}}(?:st|nd|rd|th))\b"
    r"(?! \()",  # already annotated
    re.I)


def d(x: date) -> str:
    return f"{x.day} {x.strftime('%B %Y')}"


def month_shift(x: date, k: int) -> date:
    m = x.month - 1 + k
    return date(x.year + m // 12, m % 12 + 1, 1)


def meaning(phrase: str, now: date) -> str | None:
    p = phrase.lower()
    if p.endswith("day after tomorrow"):
        return d(now + timedelta(days=2))
    if p.endswith("day before yesterday"):
        return d(now - timedelta(days=2))
    if p == "yesterday" or p == "last night":
        return d(now - timedelta(days=1))
    if p in ("today", "tonight"):
        return d(now)
    if p == "tomorrow":
        return d(now + timedelta(days=1))
    m = re.fullmatch(rf"on ({_D})", p)  # the next one ahead ("on Friday" said on Tuesday = this Friday)
    if m:
        ahead = (DAYS.index(m[1]) - now.weekday()) % 7 or 7
        return f"{m[1].capitalize()} {d(now + timedelta(days=ahead))}"
    m = re.fullmatch(r"in (.+?) (day|week|month|year)s?", p) or re.fullmatch(r"(.+?) (week)s? from (?:today|now)", p)
    if m:
        k = int(m[1]) if m[1].isdigit() else NUM.get(m[1])
        if k:
            if m[2] in ("day", "week"):
                return d(now + timedelta(days=k * (7 if m[2] == "week" else 1)))
            return month_shift(now, k).strftime("%B %Y") if m[2] == "month" else str(now.year + k)
    m = re.fullmatch(r"on the (\d{1,2})(?:st|nd|rd|th)", p)  # the next such day of a month
    if m and 1 <= int(m[1]) <= 31:
        for k in range(0, 3):
            first = month_shift(now, k)
            try:
                day = first.replace(day=int(m[1]))
            except ValueError:
                continue
            if day >= now:
                return d(day)
        return None
    if p == "last summer":
        return f"summer {now.year if now.month > 8 else now.year - 1}"
    m = re.fullmatch(rf"(last|next|this) ({_D})", p)
    if m:
        target = DAYS.index(m[2])
        if m[1] == "last":
            back = (now.weekday() - target) % 7 or 7
            return f"{m[2].capitalize()} {d(now - timedelta(days=back))}"
        ahead = (target - now.weekday()) % 7 or (7 if m[1] == "next" else 0)
        return f"{m[2].capitalize()} {d(now + timedelta(days=ahead))}"
    m = re.fullmatch(r"(last|next|this) (week|weekend|month|year)", p)
    if m:
        when, unit = m.groups()
        if unit == "year":
            return str(now.year + {"last": -1, "next": 1, "this": 0}[when])
        if unit == "month":
            return month_shift(now, {"last": -1, "next": 1, "this": 0}[when]).strftime("%B %Y")
        rel = {"last": "before", "next": "after", "this": "of"}[when]
        return f"the {unit} {rel} {d(now)}"
    m = re.fullmatch(r"(.+?) (day|week|month|year)s? ago", p)
    if m:
        n, unit = m.groups()
        if n == "a few":
            return f"a few {unit}s before {d(now) if unit in ('day', 'week') else now.year}"
        k = int(n) if n.isdigit() else NUM[n]
        if unit == "day":
            return d(now - timedelta(days=k))
        if unit == "week":
            return f"around {d(now - timedelta(weeks=k))}"
        if unit == "month":
            return f"around {month_shift(now, -k).strftime('%B %Y')}"
        return f"around {now.year - k}"
    return None


def resolve(text: str, now: date | datetime) -> str:
    now = now.date() if isinstance(now, datetime) else now

    def sub(m: re.Match) -> str:
        what = meaning(m[0], now)
        return f"{m[0]} ({what})" if what else m[0]

    return _PHRASE.sub(sub, text)


def parse_when(s: str) -> date | None:
    """LoCoMo / memory-service session stamps: '1:56 pm on 8 May, 2023'."""
    m = re.search(r"(\d{1,2}) ([A-Za-z]+),? (\d{4})", s or "")
    try:
        return datetime.strptime(f"{m[1]} {m[2]} {m[3]}", "%d %B %Y").date() if m else None
    except ValueError:
        return None


def annotate_conversation(conv: dict) -> dict:
    """A LoCoMo-format conversation with every turn's relative times resolved against its session date."""
    out = dict(conv)
    for k, turns in conv.items():
        if k.startswith("session_") and isinstance(turns, list):
            now = parse_when(conv.get(f"{k}_date_time", ""))
            if now:
                out[k] = [{**t, "text": resolve(t.get("text", ""), now)} for t in turns]
    return out

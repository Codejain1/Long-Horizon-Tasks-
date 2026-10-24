"""Relative times resolved at save time, on the phrases behind real LoCoMo misses."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "memrouter"))
import dates  # noqa: E402

SUN_17_SEP_2023 = date(2023, 9, 17)


def test_the_locomo_failures():
    assert dates.resolve("We tried a scuba diving lesson last Friday!", SUN_17_SEP_2023) == \
        "We tried a scuba diving lesson last Friday (Friday 15 September 2023)!"
    assert dates.resolve("Last year, I helped renovate a community center.", date(2023, 8, 5)) == \
        "Last year (2022), I helped renovate a community center."
    assert dates.resolve("I started volunteering here about a year ago", date(2023, 8, 3)) == \
        "I started volunteering here about a year ago (around 2022)"


def test_common_phrases():
    now = date(2023, 5, 8)  # a Monday
    r = dates.resolve
    assert r("yesterday", now) == "yesterday (7 May 2023)" and r("Tomorrow", now) == "Tomorrow (9 May 2023)"
    assert r("last week", now) == "last week (the week before 8 May 2023)"
    assert r("next month", now) == "next month (June 2023)" and r("last month", date(2023, 1, 9)) == \
        "last month (December 2022)"
    assert r("last Monday", now) == "last Monday (Monday 1 May 2023)"  # not today
    assert r("next Saturday", now) == "next Saturday (Saturday 13 May 2023)"
    assert r("two days ago", now) == "two days ago (6 May 2023)"
    assert r("3 weeks ago", now) == "3 weeks ago (around 17 April 2023)"
    assert r("a few years ago", now) == "a few years ago (a few years before 2023)"
    assert r("last summer", now) == "last summer (summer 2022)" and r("last summer", date(2023, 10, 1)) == \
        "last summer (summer 2023)"


def test_idempotent_and_untouched_text():
    once = dates.resolve("I moved last year.", date(2023, 8, 5))
    assert dates.resolve(once, date(2023, 8, 5)) == once
    assert dates.resolve("We met at the yearly meetup", date(2023, 8, 5)) == "We met at the yearly meetup"
    assert dates.resolve("the day after tomorrow works", date(2023, 10, 3)) == "the day after tomorrow (5 October 2023) works"


def test_conversation_and_stamp():
    assert dates.parse_when("1:56 pm on 8 May, 2023") == date(2023, 5, 8)
    conv = {"session_1": [{"speaker": "A", "text": "See you tomorrow"}], "session_1_date_time": "1:56 pm on 8 May, 2023"}
    assert dates.annotate_conversation(conv)["session_1"][0]["text"] == "See you tomorrow (9 May 2023)"
    assert conv["session_1"][0]["text"] == "See you tomorrow"  # input untouched


def test_forward_phrases():
    tue = date(2023, 10, 3)
    r = dates.resolve
    assert r("we fly on Friday", tue) == "we fly on Friday (Friday 6 October 2023)"
    assert r("see you on Tuesday", tue) == "see you on Tuesday (Tuesday 10 October 2023)"  # not today
    assert r("let's meet in two weeks", tue) == "let's meet in two weeks (17 October 2023)"
    assert r("done in 3 days", tue) == "done in 3 days (6 October 2023)"
    assert r("a week from today", tue) == "a week from today (10 October 2023)"
    assert r("in 2 months", tue) == "in 2 months (December 2023)"
    assert r("rent is due on the 20th", tue) == "rent is due on the 20th (20 October 2023)"
    assert r("paid on the 1st", tue) == "paid on the 1st (1 November 2023)"
    assert r("on the 31st", date(2023, 11, 2)) == "on the 31st (31 December 2023)"  # November has no 31st

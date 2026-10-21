"""The bot's turn: code runs the booking conversation, the model makes one call per step; STOP wins; memory fail-open."""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path[:0] = [str(ROOT / "bot")]
import assistant as A  # noqa: E402
import booking as B  # noqa: E402


def call(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}]}


class Model:
    """Replies in order; records what it was sent."""
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, messages, tools=None, **kw):
        self.seen.append((messages, [t["name"] for t in tools or []]))
        return self.replies.pop(0)


def memory_ok(url, body, timeout=5):
    if url.endswith("/search"):
        return {"rules": [{"text": "reply in English"}], "context": "#3 [1 Oct] owner: I'm vegetarian",
                "warnings": [{"check": "food picks: budget", "error": "keep every dish under ₹200.", "seen": 2}],
                "current": {"profile": [{"text": "vegetarian"}], "mood": {"mood": "tired", "hours_ago": 1},
                            "location": {"lat": 12.9, "lng": 77.6}}}
    return {}


def bot(tmp_path, model, post=memory_ok, nearby=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    s = B.Store(tmp_path / "b.db")
    s.set_fact("traveller:self", {"name": "Kartik"})
    return A.Assistant(tmp_path / "home", s, chat=model, post=post, nearby=nearby or (lambda *a, **k: {}))


def say(b, text, chat="c1"):
    return b.handle({"channel": "whatsapp", "chat": chat, "text": text})


def test_booking_conversation_runs_in_code(tmp_path):
    m = Model(call("task_start", kind="train", slots={"from": "Bangalore", "to": "Chennai", "date": "2099-11-02"}),
              call("task_update", slots={"class": "3A"}))
    b = bot(tmp_path, m)
    assert say(b, "train from bangalore to chennai on 2 nov") == "Which class (SL, 3A, 2A, 1A)?"
    sys_prompt = m.seen[0][0][0]["content"]
    assert "reply in English" in sys_prompt and "vegetarian" in sys_prompt and "tired" in sys_prompt
    assert "- keep every dish under ₹200. (corrected 2x)" in sys_prompt
    assert m.seen[0][1] == ["task_start", "nearby_places", "swiggy_orders"]
    r = say(b, "3A")
    assert r.startswith("Train:") and "class 3A" in r and "passengers Kartik" in r and r.endswith("(YES / no)")
    assert m.seen[1][1] == ["task_update"]
    r = say(b, "yes")
    assert r.startswith("Confirmed ✅") and len(m.seen) == 2  # YES needs no model call
    assert b.store.task(1)["status"] == "confirmed" and b._pending("c1") is None


def test_no_cancels_and_new_request_replaces_pending(tmp_path):
    m = Model(call("task_start", kind="cab", slots={"drop": "airport"}), {"content": "NEW"},
              {"content": "Hi! How can I help?"})
    b = bot(tmp_path, m)
    assert say(b, "cab to airport") == "Pick up from where? When?"  # no cab history: both asked
    assert say(b, "actually just say hi") == "Hi! How can I help?"
    assert b.store.task(1)["status"] == "cancelled"
    m.replies.append(call("task_start", kind="cab", slots={"pickup": "home", "drop": "airport", "when": "now"}))
    say(b, "cab to airport now")
    assert say(b, "no") == "Cancelled." and b.store.task(2)["status"] == "cancelled"


def test_stop_blocks_everything_until_resume(tmp_path):
    m = Model({"content": "hello"})
    b = bot(tmp_path, m)
    assert say(b, "STOP").startswith("Stopped")
    assert say(b, "book a flight") == "I'm stopped. Say RESUME to continue." and m.seen == []
    assert say(b, "resume") == "Resumed." and say(b, "hi") == "hello"


def test_places_use_real_results_and_need_location(tmp_path):
    got = {}

    def nearby(loc, radius, cuisine, limit=8):
        got.update(loc=loc, cuisine=cuisine)
        return {"places": [{"name": "Sattvam", "km": 0.8, "maps": "https://maps/x"}]}
    m = Model(call("nearby_places", cuisine="south indian"), {"content": "1. Sattvam, 0.8 km ..."})
    b = bot(tmp_path, m, nearby=nearby)
    assert say(b, "where can I eat dosa nearby") == "1. Sattvam, 0.8 km ..."
    assert got == {"loc": {"lat": 12.9, "lng": 77.6}, "cuisine": "south indian"}
    assert "Sattvam" in m.seen[1][0][0]["content"]
    m2 = Model(call("nearby_places"))
    b2 = bot(tmp_path / "2", m2, nearby=lambda *a, **k: {"error": "no location"})
    assert "Share your location" in say(b2, "places near me")


def test_memory_down_still_answers_and_history_is_logged(tmp_path):
    def down(url, body, timeout=5):
        raise OSError("refused")
    m = Model({"content": "Sure."}, {"content": "Done."})
    b = bot(tmp_path, m, post=down)
    assert say(b, "remind me what I said") == "Sure."
    say(b, "thanks")
    assert [x["role"] for x in m.seen[1][0][1:]] == ["user", "assistant", "user"]  # earlier turns, then this one
    assert [r for _, r, _ in b.log_days(7)] == ["owner", "bot", "owner", "bot"]


def test_main_loop_answers_and_ticks(tmp_path, monkeypatch):
    import main as M

    class Ch:
        name = "whatsapp"

        def __init__(self):
            self.inbox, self.out = [{"channel": "whatsapp", "chat": "c", "text": "hi"}], []

        def poll(self):
            msgs, self.inbox = self.inbox, []
            return msgs

        def send(self, chat, text):
            self.out.append((chat, text))

    class Sched:
        ticks = 0

        def tick(self):
            Sched.ticks += 1
    ch = Ch()
    M.serve({"whatsapp": ch}, bot(tmp_path, Model({"content": "hey"})), Sched(), once=True)
    assert ch.out == [("c", "hey")] and Sched.ticks == 1
    broken = bot(tmp_path / "x", Model())  # the model has nothing to say: IndexError inside the turn
    ch.inbox = [{"channel": "whatsapp", "chat": "c", "text": "hi"}]
    M.serve({"whatsapp": ch}, broken, Sched(), once=True)
    assert ch.out[-1][1].startswith("Sorry, something broke")


def test_grounding_drops_invented_details_and_fixes_days_and_people(tmp_path):
    s = B.Store(tmp_path / "g.db")
    s.set_fact("traveller:self", {"name": "Kartik"})
    s.set_fact("traveller:mom", {"name": "Sunita"})
    invented = {"to": "Rishikesh", "arrive": "2026-10-22", "leave": "2026-10-25", "budget": 15000,
                "interests": ["adventure"]}
    g = A.ground(s, "trip", "I want a trip to Rishikesh", invented)
    assert g == {"to": "Rishikesh"}  # dates, budget and interests were invented
    g = A.ground(s, "flight", "Fly to Goa on Friday with Rahul, budget 5k", {"to": "Goa", "date": "2026-10-25",
                                                                          "max_price": 5000})
    assert g["date"] == "friday" and g["max_price"] == 5000  # code turns 'friday' into the date, not the model
    g = A.ground(s, "flight", "Flight for mom to Delhi on 3 Nov under 6000", {"to": "Delhi", "date": "2026-11-03"})
    assert g["passengers"] == ["Sunita"] and g["date"] == "2026-11-03"
    g = A.ground(s, "train", "2 tickets to Mysore on Sunday for me and mom", {"to": "Mysore", "passengers": "2"})
    assert g["passengers"] == ["me", "Sunita"]  # a count, but the message names them
    g = A.ground(s, "train", "2 tickets to Mysore on Sunday", {"to": "Mysore", "passengers": "2"})
    assert g["passengers"] == "2"  # no names: booking.py turns the count into a question
    assert A.ground(s, "cab", "Get a cab", {"drop": "destination", "when": "now"}) == {}
    assert A.ground(s, "flight", "Flight to Pune under 7,500", {"max_price": 7500})["max_price"] == 7500


def test_tool_call_written_as_text_is_read():
    import brain
    msg = {"content": '<tool_call>\n{"name": "task_start", "arguments": {"kind": "flight", "slots": {"to": "Pune"}}}\n</tool_call>'}
    assert brain.tool_call(msg) == ("task_start", {"kind": "flight", "slots": {"to": "Pune"}})
    assert brain.tool_call({"content": "just text"}) is None


def test_comma_numbers_and_two_day_ranges(tmp_path):
    import brain
    raw = '<tool_call>\n{"name": "task_start", "arguments": {"kind": "flight", "slots": {"to": "Pune", "max_price": 7,500}}}\n</tool_call>'
    assert brain.tool_call({"content": raw}) == ("task_start", {"kind": "flight", "slots": {"to": "Pune", "max_price": 7500}})
    s = B.Store(tmp_path / "g.db")
    g = A.ground(s, "hotel", "Hotel in Ooty this Saturday to Monday under 3000",
                 {"city": "Ooty", "checkin": "2026-10-23", "checkout": "2026-10-25", "max_price_per_night": 3000})
    assert (g["checkin"], g["checkout"]) == ("this saturday", "monday")


def test_a_repeated_chat_reply_is_asked_again_with_more_variety(tmp_path):
    joke = {"content": "Why did the train get a medal? Outstanding track record."}
    m = Model(joke, joke, {"content": "A different train joke."})
    b = bot(tmp_path, m)
    assert say(b, "tell me a joke about trains") == joke["content"]
    assert say(b, "tell me a joke about trains") == "A different train joke."
    assert len(m.seen) == 3  # one extra call, only when it would have repeated


class FakeSwiggy:
    def __init__(self, offers=None, error=None):
        self.offers, self.error, self.added = offers or [], error, []

    def orders(self):
        return [{"restaurant": "Meghana Foods", "items": ["Paneer Biryani"], "total": 420, "date": "Oct 12"},
                {"restaurant": "Meghana Foods", "items": ["Paneer Biryani"], "total": 380, "date": "Oct 15"}]

    def search(self, dish):
        if self.error:
            import food
            raise food.NotReady(self.error)
        return self.offers

    def add(self, offer):
        self.added.append(offer)
        return {"total": "284", "in_cart": True, "text": ""}


OFFERS = [{"restaurant": "Paradise", "item": "Veg Biryani", "price": 249, "rating": "4.3", "eta": "40 MINS"},
          {"restaurant": "Biryani Blues", "item": "Chicken Biryani", "price": 199},
          {"restaurant": "Meghana Foods", "item": "Paneer Biryani", "price": 320},
          {"restaurant": "X", "item": "Royal Biryani", "price": 900}]


def test_food_order_to_cart_without_placing_it(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    sw = FakeSwiggy(OFFERS)
    b = bot(tmp_path, m)
    b.swiggy = sw
    notes = []
    assert say(b, "order veg biryani under 400").endswith("(YES / no)")
    r = b.handle({"channel": "whatsapp", "chat": "c1", "text": "yes"}, notify=notes.append)
    assert notes == ["Looking for biryani on Swiggy…"]
    assert r.splitlines()[1:3] == ["1. Veg Biryani — Paradise — ₹249 ★4.3 (40 MINS)", "2. Paneer Biryani — Meghana Foods — ₹320"]
    assert "Chicken" not in r and "Royal" not in r  # veg owner, budget 400: filtered by code
    r = say(b, "1")
    assert sw.added == [OFFERS[0]] and "To pay: ₹284" in r and "stopped before ordering" in r
    assert b.store.task(1)["status"] == "done" and len(m.seen) == 1  # YES, the search and the choice: no model calls


def test_food_problems_are_said_plainly(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 100}))
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS)
    say(b, "order biryani under 100")
    assert "none fit under ₹100" in say(b, "yes")
    m.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400}))
    b.swiggy = FakeSwiggy(error="Swiggy is logged out in the bot's Chrome: log in there again (Hermes desktop).")
    say(b, "order biryani under 400")
    assert "logged out" in say(b, "yes")


def test_order_history_feeds_recommendations(tmp_path):
    m = Model(call("swiggy_orders"), {"content": "Try the paneer biryani again?"})
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy()
    r = say(b, "look at my swiggy orders")
    assert r.startswith("Saved 2 new orders") and "paneer biryani (2)" in r and "Meghana Foods (2)" in r
    say(b, "what should I eat tonight?")
    assert "What the owner orders on Swiggy (2 orders)" in m.seen[1][0][0]["content"]


def test_suggestion_after_a_fresh_read_answers_instead_of_rereading(tmp_path):
    m = Model(call("swiggy_orders"), call("swiggy_orders"), {"content": "How about a Veggie Delite from Subway?"})
    b = bot(tmp_path, m)
    sw = FakeSwiggy()
    calls = []
    sw.orders = lambda: calls.append(1) or FakeSwiggy().orders()
    b.swiggy = sw
    say(b, "look at my swiggy orders")
    r = say(b, "so what should I eat at this time?")
    assert r == "How about a Veggie Delite from Subway?" and calls == [1]  # Swiggy opened once
    assert "using what they order" in m.seen[2][0][0]["content"]


def test_dish_names_are_cleaned_for_the_pattern():
    import booking as Bk
    assert Bk.dish_name("Chilli Bean Patty Sandwich (15cm) + Coke(Save 31%) x 1") == "chilli bean patty sandwich"
    assert Bk.dish_name("Veggie & Cheese Toastie x 2") == "veggie & cheese toastie"


def test_veg_in_the_dish_name_filters_and_empty_search_is_explained(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "veg Biriyani", "max_total": 400}))
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS)
    say(b, "Order veg Biriyani under 400")
    assert "Chicken" not in say(b, "Yes")  # no diet setting: 'veg' in the dish name is enough
    m.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400}))
    b.swiggy = FakeSwiggy([])
    say(b, "order biryani under 400")
    assert "couldn't read any dishes" in say(b, "yes")

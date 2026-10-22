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
    """What the model answers, forced to the assistant's schemas (DECIDE / UPDATE)."""
    if name == "task_update":
        return {"content": json.dumps({"action": "update", "details": args.get("slots", {})})}
    action = {"task_start": "book", "nearby_places": "places", "swiggy_orders": "read_swiggy_orders"}[name]
    return {"content": json.dumps({"action": action, "kind": args.get("kind", "none"), "details": args.get("slots", {}),
                                   "cuisine": args.get("cuisine", ""), "reply": ""})}


def reply(text):
    return {"content": json.dumps({"action": "reply", "kind": "none", "details": {}, "reply": text})}


class Model:
    """Replies in order; records what it was sent."""
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, messages, **kw):
        self.seen.append((messages, kw.get("schema")))
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
    b = A.Assistant(tmp_path / "home", s, chat=model, post=post, nearby=nearby or (lambda *a, **k: {}))
    b.reviews = lambda restaurant: REVIEW if restaurant == "Paradise" else None  # never a real browser in tests
    return b


REVIEW = {"rating": 4.4, "reviews": 1200, "liked": ["great taste", "big portions"], "disliked": ["slow delivery"]}


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
    assert m.seen[0][1] is A.DECIDE  # forced JSON, no tool calling
    r = say(b, "3A")
    assert r.startswith("Train:") and "class 3A" in r and "passengers Kartik" in r and r.endswith("(YES / no)")
    assert m.seen[1][1] is A.UPDATE
    r = say(b, "yes")
    assert r.startswith("Confirmed ✅") and len(m.seen) == 2  # YES needs no model call
    assert b.store.task(1)["status"] == "confirmed" and b._pending("c1") is None


def test_no_cancels_and_new_request_replaces_pending(tmp_path):
    m = Model(call("task_start", kind="cab", slots={"drop": "airport"}), {"content": json.dumps({"action": "new", "details": {}})},
              reply("Hi! How can I help?"))
    b = bot(tmp_path, m)
    assert say(b, "cab to airport") == "Pick up from where? When?"  # no cab history: both asked
    assert say(b, "actually just say hi") == "Hi! How can I help?"
    assert b.store.task(1)["status"] == "cancelled"
    m.replies.append(call("task_start", kind="cab", slots={"pickup": "home", "drop": "airport", "when": "now"}))
    say(b, "cab to airport now")
    assert say(b, "no") == "Cancelled." and b.store.task(2)["status"] == "cancelled"


def test_stop_blocks_everything_until_resume(tmp_path):
    m = Model(reply("hello"))
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
    m = Model(reply("Sure."), reply("Done."))
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
    M.serve({"whatsapp": ch}, bot(tmp_path, Model(reply("hey"))), Sched(), once=True)
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


def test_comma_numbers_and_two_day_ranges(tmp_path):
    import brain
    raw = 'Sure: {"action": "book", "kind": "flight", "details": {"to": "Pune", "max_price": 7,500}} done'
    assert brain.first_json(raw)["details"] == {"to": "Pune", "max_price": 7500}  # fallback when a schema is ignored
    s = B.Store(tmp_path / "g.db")
    g = A.ground(s, "hotel", "Hotel in Ooty this Saturday to Monday under 3000",
                 {"city": "Ooty", "checkin": "2026-10-23", "checkout": "2026-10-25", "max_price_per_night": 3000})
    assert (g["checkin"], g["checkout"]) == ("this saturday", "monday")


def test_a_repeated_chat_reply_is_asked_again_with_more_variety(tmp_path):
    joke = reply("Why did the train get a medal? Outstanding track record.")
    m = Model(joke, joke, {"content": "A different train joke."})
    b = bot(tmp_path, m)
    assert say(b, "tell me a joke about trains") == "Why did the train get a medal? Outstanding track record."
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


def test_food_order_compares_then_cart_or_link_never_placing_it(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    sw = FakeSwiggy(OFFERS)
    b = bot(tmp_path, m)
    b.swiggy = sw
    notes = []
    assert say(b, "order veg biryani under 400").endswith("(YES / no)")
    r = b.handle({"channel": "whatsapp", "chat": "c1", "text": "yes"}, notify=notes.append)
    assert notes == ["Comparing biryani on swiggy, with reviews…"]
    lines = r.splitlines()
    assert lines[1] == ("1. Veg Biryani — Paradise — ₹249 on Swiggy ★4.3 (40 MINS) · Google ★4.4 (1200): "
                        "+great taste, big portions; −slow delivery")
    assert lines[2] == "2. Paneer Biryani — Meghana Foods — ₹320 on Swiggy"
    assert "Chicken" not in r and "Royal" not in r  # veg owner, budget 400: filtered by code
    assert say(b, "2 link") == ("Here's Paneer Biryani from Meghana Foods on Swiggy: "
                                "https://www.swiggy.com/search?query=biryani")
    m.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    say(b, "order veg biryani under 400"), say(b, "yes")
    r = say(b, "1 cart")
    assert sw.added[-1]["item"] == "Veg Biryani" and "To pay: ₹284" in r and "stopped before ordering" in r
    assert len(m.seen) == 2  # one routing call per order; YES, compare and choosing are all code


def test_ideas_from_history_then_compare(tmp_path):
    ideas = {"content": json.dumps({"ideas": [{"dish": "Veg Biryani", "why": "your usual"},
                                              {"dish": "Masala Dosa", "why": "light"}, {"dish": "Pav Bhaji", "why": "new"}]})}
    m = Model(call("suggest_food") if False else {"content": json.dumps({"action": "suggest_food", "kind": "none",
                                                                          "details": {}, "reply": ""})}, ideas)
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS)
    r = say(b, "what should I eat now?")
    assert r.startswith("1. Veg Biryani — your usual") and "Reply 1, 2 or 3" in r
    assert m.seen[1][1] is A.IDEAS
    r = say(b, "1")
    assert r.startswith("Best options:") and "Veg Biryani — Paradise" in r
    assert say(b, "1 link").startswith("Here's Veg Biryani from Paradise")


def test_food_problems_are_said_plainly(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 100}))
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS)
    say(b, "order biryani under 100")
    assert "couldn't find biryani under ₹100" in say(b, "yes")
    m.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400}))
    b.swiggy = FakeSwiggy(error="Swiggy is logged out in the bot's Chrome: log in there again (Hermes desktop).")
    say(b, "order biryani under 400")
    assert "logged out" in say(b, "yes")


def test_order_history_feeds_recommendations(tmp_path):
    m = Model(call("swiggy_orders"), reply("Try the paneer biryani again?"))
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
    assert "couldn't find biryani under ₹400" in say(b, "yes")


def test_every_turn_leaves_a_trace_and_errors_alert_the_owner(tmp_path):
    m = Model(call("task_start", kind="train", slots={"from": "Bangalore", "to": "Chennai", "date": "2099-11-02"}))
    b = bot(tmp_path, m)
    say(b, "train from bangalore to chennai on 2 nov")
    (ts, chat, text, reply, ms, steps), = b.db.execute("SELECT * FROM traces").fetchall()
    steps = json.loads(steps)
    assert text.startswith("train") and reply == "Which class (SL, 3A, 2A, 1A)?"
    assert steps[0]["step"] == "model" and steps[0]["schema"] == ["action", "kind", "details", "cuisine", "site", "reply"]
    assert '"action": "book"' in steps[0]["out"]

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
        jobs = [{"name": "digest", "deliver": "whatsapp:owner-chat"}]

        def tick(self):
            pass
    ch, broken = Ch(), bot(tmp_path / "x", Model())
    M.serve({"whatsapp": ch}, broken, Sched(), once=True)
    assert any(c == "owner-chat" and t.startswith("⚠ bot error on 'hi'") for c, t in ch.out)
    (*_, steps), = broken.db.execute("SELECT * FROM traces").fetchall()
    assert json.loads(steps)[-1]["step"] == "error"


def test_an_unusable_forced_answer_is_retried_once(tmp_path):
    import brain
    seen = []

    def chat(messages, **kw):
        seen.append(kw.get("temperature", 0.2))
        return {"content": '{"action": "book", "kind": "cab", "details": {"drop": "Koramangala     ' if len(seen) == 1
                else json.dumps({"action": "reply", "kind": "none", "details": {}, "reply": "ok"})}
    assert brain.extract([], A.DECIDE, chat, max_tokens=700)["reply"] == "ok" and seen == [0.2, 0.5]


def test_cut_off_answers_are_closed_and_read_and_free_text_never_gets_the_json_prompt(tmp_path):
    import brain
    cut = '{\n  "action": "book",\n  "kind": "cab",\n  "details": {\n    "pickup": "home",\n    "drop": "Koramangala"\n  }\n  '
    assert brain.first_json(cut)["details"] == {"pickup": "home", "drop": "Koramangala"}  # stopped mid-whitespace
    assert set(A.DETAILS["properties"]) >= {"dish", "max_total", "passengers", "checkin"} and not A.DETAILS["additionalProperties"]
    m = Model(call("swiggy_orders"), {"content": "Maybe a wrap?"})
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy()
    b.store.set_fact("swiggy:orders_read", 20)
    B.save_orders(b.store, "swiggy", FakeSwiggy().orders())
    say(b, "what do I usually eat?")
    assert "answer as JSON" not in m.seen[-1][0][0]["content"] and "plain text" in m.seen[-1][0][0]["content"]


def test_a_named_app_that_is_not_connected_is_said_not_silently_swapped(tmp_path):
    m = Model({"content": json.dumps({"action": "book", "kind": "food", "details": {"dish": "chole bhature",
                                      "max_total": 150}, "site": "Zomato", "reply": ""})})
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy([{"restaurant": "Guru Kirpa", "item": "Chole bhature", "price": 50}])
    say(b, "Find chole bhature on Zomato under 150")
    r = say(b, "yes")
    assert r.startswith("Zomato isn't connected to me yet") and "Chole bhature — Guru Kirpa — ₹50 on Swiggy" in r
    assert "one moment" in A.CHAT.lower()  # banned in plain-text replies too


class FakeOrders:
    def __init__(self, total=284.0, result="paid"):
        self.total, self.result, self.paid = total, result, []

    def summary(self):
        return {"items": ["Veg Biryani x 1"], "total": self.total, "address": "Home, Koramangala"}

    def pay(self, upi, total):
        self.paid.append((upi, total))
        return self.result


def order_bot(tmp_path, orders):
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    b = bot(tmp_path, m)
    b.swiggy, b.orders = FakeSwiggy(OFFERS), orders
    tracked = []
    b._track = lambda send: tracked.append(send)
    say(b, "order veg biryani under 400"), say(b, "yes")
    return b, tracked


def test_order_for_me_needs_upi_then_yes_on_the_exact_total(tmp_path):
    orders = FakeOrders()
    b, tracked = order_bot(tmp_path, orders)
    assert "upi id" in say(b, "1 order").lower()  # no UPI ID yet: asks for it once
    assert say(b, "upi id Me@OkAxis").startswith("Saved your UPI ID me@okaxis")
    b.model.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    say(b, "order veg biryani under 400"), say(b, "yes")
    r = say(b, "1 order")
    assert r == ("Order Veg Biryani x 1 from Paradise, delivered to Home, Koramangala, total ₹284, paid by a UPI "
                 "request to me@okaxis? (YES / no)")
    assert orders.paid == []  # nothing paid before YES
    r = say(b, "yes")
    assert orders.paid == [("me@okaxis", 284.0)] and r.startswith("Paid and ordered ✅ ₹284") and len(tracked) == 1


def test_cap_no_and_timeout(tmp_path):
    b, _ = order_bot(tmp_path, FakeOrders(total=1800))
    b.store.set_fact("owner:upi", "me@okaxis")
    assert "over your order cap of ₹1500" in say(b, "1 order") and b.orders.paid == []
    b2, _ = order_bot(tmp_path / "2", FakeOrders())
    b2.store.set_fact("owner:upi", "me@okaxis")
    say(b2, "1 order")
    assert say(b2, "no").startswith("Okay, not ordering") and b2.orders.paid == []
    b3, _ = order_bot(tmp_path / "3", FakeOrders(result="timeout"))
    b3.store.set_fact("owner:upi", "me@okaxis")
    assert say(b3, "order cap 2000").startswith("Order cap set to ₹2000")
    say(b3, "1 order")
    assert "didn't approve the request in time" in say(b3, "yes")


def test_tracking_sends_only_changes_until_delivered(tmp_path):
    b = bot(tmp_path, Model())
    seq = iter([{"status": "Order confirmed", "delivered": False}, {"status": "Order confirmed", "delivered": False},
                {"status": "Out for delivery", "eta_minutes": 5, "delivered": False},
                {"status": "Delivered", "delivered": True}])
    b.orders = type("O", (), {"status": lambda self: next(seq)})()
    sent = []
    A.Assistant._track(b, sent.append, sleep=lambda s: None)
    assert sent == ["🛵 Order confirmed", "🛵 Out for delivery, about 5 min away", "🛵 Delivered"]

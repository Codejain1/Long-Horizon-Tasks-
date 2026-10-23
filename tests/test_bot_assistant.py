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
    b.geocode = lambda lat, lng: {"area": "Kotkapura", "city": "Faridkot"}  # nor a real map service
    b.geoplace = lambda name: {"lat": 12.935, "lng": 77.624, "area": "Koramangala", "city": "Bengaluru"}
    b._memory_location = lambda: None
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
    assert m.seen[1][1] is A.UPDATE
    # complete: no "Go ahead? (YES / no)" (owner, 2026-10-23); travel search isn't built, said plainly
    assert r.startswith("Got it: Train:") and "class 3A" in r and "passengers Kartik" in r and "isn't built" in r
    assert b.store.task(1)["status"] == "confirmed" and b._pending("c1") is None and len(m.seen) == 2


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
    assert "allow location in the app" in say(b2, "places near me")


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
    M.serve({"whatsapp": ch}, bot(tmp_path, Model(reply("hey"))), Sched(), once=True, check=lambda: True)
    assert ch.out == [("c", "hey")] and Sched.ticks == 1
    broken = bot(tmp_path / "x", Model())  # the model has nothing to say: IndexError inside the turn
    ch.inbox = [{"channel": "whatsapp", "chat": "c", "text": "hi"}]
    M.serve({"whatsapp": ch}, broken, Sched(), once=True, check=lambda: True)
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


def test_food_order_goes_straight_to_options_then_taps(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    sw = FakeSwiggy(OFFERS)
    b = bot(tmp_path, m)
    b.swiggy = sw
    notes = []
    r = b.handle({"channel": "whatsapp", "chat": "c1", "text": "order veg biryani under 400"}, notify=notes.append)
    assert notes == ["Comparing veg biryani on swiggy, with reviews…"]  # no "go ahead?"; the dish read by code
    lines = r.splitlines()
    assert lines[1] == ("1. Veg Biryani — Paradise — ₹249 on Swiggy ★4.3 (40 MINS) · Google ★4.4 (1200): "
                        "+great taste, big portions; −slow delivery")
    assert r.choices == ["1. Veg Biryani ₹249", "2. Paneer Biryani ₹320"]  # buttons / poll
    assert r.cards[0]["choice"] == r.choices[0] and r.cards[0]["note"] == "Google ★4.4: great taste, big portions"
    assert "Chicken" not in r and "Royal" not in r  # veg owner, budget 400: filtered by code
    act = say(b, "2. Paneer Biryani ₹320")  # a tap arrives as the button's text
    assert act.choices == ["Send me the link", "Add to my Swiggy cart"]  # ordering paused: no "Order it for me"
    assert say(b, "Send me the link") == "Here's Paneer Biryani from Meghana Foods on Swiggy: https://www.swiggy.com/search?query=veg%20biryani"
    m.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    say(b, "order veg biryani under 400")
    r = say(b, "1 cart")  # typing still works
    assert sw.added[-1]["item"] == "Veg Biryani" and "To pay: ₹284" in r and "stopped before ordering" in r
    assert len(m.seen) == 0  # the food path is code end to end: no model call at all


def test_ideas_from_history_then_compare(tmp_path):
    ideas = {"content": json.dumps({"ideas": [{"dish": "Veg Biryani", "why": "your usual"},
                                              {"dish": "Masala Dosa", "why": "light"}, {"dish": "Pav Bhaji", "why": "new"}]})}
    m = Model(call("suggest_food") if False else {"content": json.dumps({"action": "suggest_food", "kind": "none",
                                                                          "details": {}, "reply": ""})}, ideas)
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS)
    r = say(b, "what should I eat now?")
    assert r.startswith("1. Veg Biryani — your usual") and r.choices == ["1. Veg Biryani", "2. Masala Dosa", "3. Pav Bhaji"]
    assert m.seen[1][1] is A.IDEAS
    r = say(b, "1")
    assert r.startswith("Best options:") and "Veg Biryani — Paradise" in r
    assert say(b, "1 link").startswith("Here's Veg Biryani from Paradise")


def test_food_problems_are_said_plainly(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 100}))
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS)
    assert "couldn't find biryani under ₹100" in say(b, "order biryani under 100")
    m.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400}))
    b.swiggy = FakeSwiggy(error="Swiggy is logged out in the bot's Chrome: log in there again (Hermes desktop).")
    assert "logged out" in say(b, "order biryani under 400")


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
    assert "Chicken" not in say(b, "Order veg Biriyani under 400")  # no diet setting: 'veg' in the dish name is enough
    m.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400}))
    b.swiggy = FakeSwiggy([])
    assert "couldn't find biryani under ₹400" in say(b, "order biryani under 400")


def test_every_turn_leaves_a_trace_and_errors_alert_the_owner(tmp_path):
    m = Model(call("task_start", kind="train", slots={"from": "Bangalore", "to": "Chennai", "date": "2099-11-02"}))
    b = bot(tmp_path, m)
    say(b, "train from bangalore to chennai on 2 nov")
    (ts, chat, text, reply, ms, steps), = b.db.execute("SELECT * FROM traces").fetchall()
    steps = json.loads(steps)
    assert text.startswith("train") and reply == "Which class (SL, 3A, 2A, 1A)?"
    assert steps[0]["step"] == "memory" and isinstance(steps[0]["ms"], int)
    model = [x for x in steps if x["step"] == "model"][0]
    assert model["schema"] == ["action", "kind", "details", "cuisine", "site", "reply"] and '"action": "book"' in model["out"]

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
    M.serve({"whatsapp": ch}, broken, Sched(), once=True, check=lambda: True)
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
    r = say(b, "Find chole bhature on Zomato under 150")
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
    A.ORDERING = True  # the order tests exercise the (paused) ordering path
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    b = bot(tmp_path, m)
    b.swiggy, b.orders = FakeSwiggy(OFFERS), orders
    tracked = []
    b._track = lambda chat, send: tracked.append(chat)
    say(b, "order veg biryani under 400")
    return b, tracked


def say_and_wait(b, text, chat="c1", wait=2.0):
    """A turn whose work finishes in the background (payment): the reply, then the messages sent after it."""
    import time as _t
    notes = []
    r = b.handle({"channel": "whatsapp", "chat": chat, "text": text}, notify=notes.append)
    end = _t.time() + wait
    while _t.time() < end and not any(n.startswith(("Paid", "Not ordered", "Stopped", "The payment")) for n in notes):
        _t.sleep(0.02)
    return r, notes


def test_order_for_me_needs_upi_then_yes_on_the_exact_total(tmp_path):
    orders = FakeOrders()
    b, tracked = order_bot(tmp_path, orders)
    assert "upi id" in say(b, "1 order").lower()  # no UPI ID yet: asks for it once
    assert say(b, "upi id Me@OkAxis").startswith("Saved your UPI ID me@okaxis")
    b.model.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    say(b, "order veg biryani under 400")
    r = say(b, "1 order")
    assert r == ("Order Veg Biryani x 1 from Paradise, delivered to Home, Koramangala, total ₹284, paid by a UPI "
                 "request to me@okaxis?") and r.choices == ["Yes, pay ₹284", "No"]  # the one YES: before paying
    assert orders.paid == []  # nothing paid before YES
    r, notes = say_and_wait(b, "Yes, pay ₹284")  # the tap
    assert r.startswith("Sending the payment request for ₹284 to me@okaxis")  # the chat isn't blocked
    assert orders.paid == [("me@okaxis", 284.0)] and notes[-1].startswith("Paid and ordered ✅ ₹284") and tracked == ["c1"]


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
    r, notes = say_and_wait(b3, "yes")
    assert "didn't approve the request in time" in notes[-1]


def test_tracking_sends_only_changes_until_delivered(tmp_path):
    b = bot(tmp_path, Model())
    seq = iter([{"status": "Order confirmed", "delivered": False}, {"status": "Order confirmed", "delivered": False},
                {"status": "Out for delivery", "eta_minutes": 5, "delivered": False},
                {"status": "Delivered", "delivered": True}])
    b.orders = type("O", (), {"status": lambda self: next(seq)})()
    sent = []
    import time as _t
    b.db.execute("INSERT INTO trackers VALUES ('c1', 'whatsapp', '', ?)", (_t.time() + 3600,))
    A.Assistant._track(b, "c1", sent.append, sleep=lambda s: None)
    assert sent == ["🛵 Order confirmed", "🛵 Out for delivery, about 5 min away", "🛵 Delivered"]
    assert b._read("SELECT * FROM trackers") == []  # done: removed


def test_tracking_resumes_after_a_restart(tmp_path):
    import time as _t
    b = bot(tmp_path, Model())
    b.db.execute("INSERT INTO trackers VALUES ('c9', 'telegram', 'Order confirmed', ?)", (_t.time() + 3600,))
    b.db.commit()
    started = []
    b._track = lambda chat, send: started.append((chat, send))
    assert b.resume_trackers(lambda channel, chat, text: None) == 1
    _t.sleep(0.05)
    assert started[0][0] == "c9"


def test_a_number_is_a_pick_only_as_the_whole_reply(tmp_path):
    ideas = {"content": json.dumps({"ideas": [{"dish": "Veg Biryani", "why": "usual"}, {"dish": "Dosa", "why": "light"},
                                              {"dish": "Pav Bhaji", "why": "new"}]})}
    m = Model({"content": json.dumps({"action": "suggest_food", "kind": "none", "details": {}, "reply": ""})}, ideas,
              call("task_start", kind="train", slots={"from": "Bangalore", "to": "Mysore", "date": "sunday",
                                                       "passengers": "2"}))
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS)
    say(b, "what should I eat?")
    r = say(b, "2 tickets to Mysore on Sunday from Bangalore")  # live: was taken as "pick idea 2"
    assert "Mysore" in json.dumps(b.store.task(1)["slots"]) and not r.startswith("Best options")
    assert b._session("c1")["mode"] == "form"



def test_a_yes_inside_a_longer_answer_counts(tmp_path):
    assert A.YES_IN.search("Upi request and yes") and not A.NO_IN.search("Upi request and yes")
    assert A.NO_IN.search("yes but not now")
    orders = FakeOrders()
    b, _ = order_bot(tmp_path, orders)
    b.store.set_fact("owner:upi", "me@okaxis")
    say(b, "1 order")
    r, notes = say_and_wait(b, "Upi request and yes")  # live 2026-10-22: was asked again
    assert r.startswith("Sending the payment request") and orders.paid == [("me@okaxis", 284.0)]


def test_chrome_health_alerts_once_when_it_breaks_and_once_when_back():
    import main as M
    sent, states = [], iter([True, False, False, False, True, True])
    w = M.ChromeWatch(lambda: next(states), sent.append)
    for _ in range(6):
        w.tick()
    assert sent == ["⚠ The bot's Chrome isn't loading pages, so Swiggy and logins won't work until it's fixed.",
                    "✅ The bot's Chrome loads pages again."]



def test_ordering_is_paused_by_default_and_fills_the_cart(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "ORDERING", False)
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    b = bot(tmp_path, m)
    b.swiggy, b.orders = FakeSwiggy(OFFERS), FakeOrders()
    b.store.set_fact("owner:upi", "me@okaxis")
    say(b, "order veg biryani under 400")
    assert say(b, "1").choices == ["Send me the link", "Add to my Swiggy cart"]  # no "Order it for me" while paused
    m.replies.append(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400, "diet": "veg"}))
    say(b, "order veg biryani under 400")
    r = say(b, "1 order")
    assert "To pay: ₹284" in r and "Ordering for you is paused" in r and b.orders.paid == []


def test_concurrent_turns_keep_their_own_notify_and_db_reads_are_safe(tmp_path):
    import threading as th
    b = bot(tmp_path, Model(*[reply(f"r{i}") for i in range(40)]))
    got = {}

    def turn(chat):
        notes = []
        b.handle({"channel": "whatsapp", "chat": chat, "text": "hi"}, notify=notes.append)
        b.notify(f"note for {chat}")
        got[chat] = notes
    threads = [th.Thread(target=turn, args=(f"c{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(v == [f"note for {k}"] for k, v in got.items()) and len(got) == 8
    assert len(b.log_days(1)) == 16


def test_reviews_are_parallel_cached_and_use_the_area(tmp_path, monkeypatch):
    import food
    import time as _t
    calls = []

    def slow(restaurant, area=""):
        calls.append((restaurant, area))
        _t.sleep(0.3)
        return {"rating": 4.1, "reviews": 10, "liked": ["x"], "disliked": []}
    monkeypatch.setattr(food, "google_reviews", slow)
    b = bot(tmp_path, Model())
    del b.reviews  # the real method (the test helper stubs it)
    b.store.set_fact("owner:area", "Koramangala")
    opts = [{"restaurant": n} for n in ("A", "B", "C")]
    t0 = _t.time()
    b._add_reviews(opts)
    assert _t.time() - t0 < 0.8 and all(o["google"]["rating"] == 4.1 for o in opts)  # parallel, not 0.9 s
    assert sorted(calls) == [("A", "Koramangala"), ("B", "Koramangala"), ("C", "Koramangala")]
    b._add_reviews([{"restaurant": "A"}])
    assert len(calls) == 3  # cached for a day


def test_swiggy_not_loading_is_said_plainly(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "dosa", "max_total": 300}))
    b = bot(tmp_path, m)

    class Down:
        def search(self, dish):
            import cdp
            raise cdp.PageError("www.swiggy.com didn't load: net::ERR_TIMED_OUT")
    b.swiggy = Down()
    assert say(b, "order dosa under 300").startswith("Swiggy didn't load just now")


def test_travellers_can_be_saved_again(tmp_path):
    b = bot(tmp_path, Model())
    assert say(b, "save traveller mom Sunita 55") == "Saved: mom = Sunita, 55."
    assert say(b, "my name is kartik jain") == "Saved: you = Kartik Jain."
    assert b.store.fact("traveller:mom")["value"] == {"name": "Sunita", "age": 55}
    assert b.store.fact("traveller:self")["value"]["name"] == "Kartik Jain"


def test_a_new_request_during_a_choice_is_not_taken_as_the_choice(tmp_path):
    m = Model(call("task_start", kind="food", slots={"dish": "biryani", "max_total": 400}),
              call("task_start", kind="food", slots={"dish": "dosa", "max_total": 300}))
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS + [{"restaurant": "MTR", "item": "Masala Dosa", "price": 120}])
    say(b, "order biryani under 400")
    say(b, "1")  # -> what should I do?
    r = say(b, "order dosa under 300")
    assert r.startswith("Best options:") and "Masala Dosa" in r and b.swiggy.added == []
    assert say(b, "1").choices[0] == "Send me the link"
    assert say(b, "link").startswith("Here's Masala Dosa")



def test_the_apps_location_is_used_for_places(tmp_path):
    got = {}

    def nearby(loc, radius, cuisine, limit=8):
        got["loc"] = loc
        return {"places": [{"name": "Sattvam", "km": 0.3, "maps": "x"}]}
    m = Model(call("nearby_places"), {"content": "Sattvam, 0.3 km"})
    b = bot(tmp_path, m, nearby=nearby)
    b.handle({"channel": "app", "chat": "owner", "text": "cafes near me", "location": {"lat": 12.97, "lng": 77.59}})
    assert (got["loc"]["lat"], got["loc"]["lng"]) == (12.97, 77.59)  # the phone's, not the older WhatsApp one



def test_the_phones_location_comes_first_and_drives_the_search(tmp_path):
    m = Model(reply("You're in Kotkapura, Faridkot."), call("task_start", kind="food", slots={"dish": "dosa", "max_total": 300}))
    b = bot(tmp_path, m)
    seen = {}

    class Sw(FakeSwiggy):
        def search(self, dish):
            seen["where"] = dict(self.where)
            import food
            raise food.NothingHere(f"Swiggy shows nothing for {dish} where you are now; it may not deliver there.")
    b.swiggy = Sw()
    b.handle({"channel": "app", "chat": "owner", "text": "where am I?", "location": {"lat": 30.44, "lng": 74.88}})
    prompt = m.seen[0][0][0]["content"]
    assert "Where the owner is right now (from their phone" in prompt and "Kotkapura, Faridkot" in prompt
    assert prompt.index("Where the owner is right now") < prompt.index("What the owner said before")  # above memory
    r = b.handle({"channel": "app", "chat": "owner", "text": "order dosa under 300", "location": {"lat": 30.44, "lng": 74.88}})
    assert seen["where"]["lat"] == 30.44 and seen["where"]["city"] == "Faridkot"
    assert r == "Swiggy shows nothing for dosa near Kotkapura, Faridkot; it may not deliver there."


def test_magicpin_follows_up_after_swiggy_with_the_saving(tmp_path):
    import time as _t
    m = Model(call("task_start", kind="food", slots={"dish": "veg biryani", "max_total": 400}))
    b = bot(tmp_path, m)
    b.swiggy = FakeSwiggy(OFFERS)

    class Mp:
        where = None

        def search(self, dish):
            return [{"restaurant": "Biryani Pot", "item": "Veg Biryani", "price": 218, "platform": "magicpin",
                     "link": "https://magicpin.in/x"}]
    b.magicpin = Mp()
    notes = []
    r = b.handle({"channel": "app", "chat": "owner", "text": "veg biryani under 400",
                  "location": {"lat": 12.91, "lng": 77.64}}, notify=notes.append)
    assert r.startswith("Best options:")  # Swiggy first, at once
    end = _t.time() + 2
    while _t.time() < end and not any(n.startswith("Also on magicpin") for n in notes):
        _t.sleep(0.02)
    follow = [n for n in notes if n.startswith("Also on magicpin")][0]
    assert "Veg Biryani — Biryani Pot — ₹218 (₹31 less than Swiggy's best)" in follow and "https://magicpin.in/x" in follow



def test_replay_of_the_live_chat_of_2026_10_23_keeps_the_thread(tmp_path):
    """The owner's real messages: the food path and its follow-ups run in code; where-am-I is code."""
    seen = []

    class Sw(FakeSwiggy):
        def search(self, dish):
            seen.append((dish, (self.where or {}).get("area")))
            return OFFERS + [{"restaurant": "MTR", "item": "Masala Dosa", "price": 120}]
    m = Model({"content": json.dumps({"action": "where_am_i", "kind": "none", "details": {}, "reply": "Whitefield"})})
    b = bot(tmp_path, m)
    b.swiggy = Sw()
    jaito = {"lat": 30.44, "lng": 74.88}

    def app(text):
        return b.handle({"channel": "app", "chat": "owner", "text": text, "location": jaito})
    assert app("Find good biryani near me") == "What's your budget?"          # live: invented restaurants
    assert app("300").startswith("Best options:")                               # live: an empty promise
    r = app("Can you find for Koramangala bangalore")                            # live: "I can't switch locations"
    assert r.startswith("Best options:")
    assert app("Where am i") == "You're in Kotkapura, Faridkot (from your phone, just now)."  # live: "Whitefield" 4x
    assert app("actually make it under 200").startswith("Best options:")
    assert "Masala Dosa" in app("what about dosa instead")
    assert seen == [("biryani", "Kotkapura"), ("biryani", "Koramangala"), ("biryani", "Koramangala"),
                    ("dosa", "Koramangala")]
    assert len(m.seen) == 1  # only "where am i" needed the model (to recognise the question)


def test_an_old_option_is_not_copied_as_the_new_dish(tmp_path):
    s = B.Store(tmp_path / "g.db")
    assert A.ground(s, "food", "Find good biryani near me", {"dish": "Extra Biryani Masala Veg"}) == {}
    assert A.ground(s, "food", "Find good biryani near me", {"dish": "biryani"}) == {"dish": "biryani"}
    assert A.ground(s, "food", "for Koramangala", {"dish": "biryani", "max_total": 300, "near": "Koramangala"},
                    earlier="biryani 300") == {"dish": "biryani", "max_total": 300, "near": "Koramangala"}


def test_food_path_rules():
    assert A.food_request("Find good biryani near me") == {"dish": "biryani"}
    assert A.food_request("order veg biryani under 400 on swiggy") == {"dish": "veg biryani", "max_total": 400.0,
                                                                       "diet": "veg", "site": "swiggy"}
    assert A.food_request("where can I eat dosa nearby") is None          # going out: places
    assert A.food_request("why only swiggy for biryani?") is None          # about the bot
    assert A.food_request("what should I eat?") is None                    # ideas, not a dish
    req = {"dish": "biryani", "max_total": 300.0}
    assert A.food_followup("make it under 200", req) == {"dish": "biryani", "max_total": 200.0}
    assert A.food_followup("for Koramangala bangalore", req)["near"] == "Koramangala bangalore"
    assert A.food_followup("what about dosa instead", req)["dish"] == "dosa"
    assert A.food_followup("order dosa under 300", req) is None            # a new request, not a budget change
    assert A.food_followup("thanks!", req) is None


def test_invented_place_lists_are_blocked(tmp_path):
    made_up = "Here are 3 good biryani places near you in Jaito:\n\n1. **Biryani House Jaito** (1.2 km)\n2. **Spice Hub** (2 km)"
    m = Model(reply(made_up))
    b = bot(tmp_path, m)
    r = say(b, "any good spots?")
    assert "Biryani House" not in r and "search Swiggy" in r


def test_dish_names_keep_their_own_words_and_the_restaurant():
    assert A.food_request("I'm craving a margherita pizza, budget 450")["dish"] == "margherita pizza"
    assert A.food_request("Masala dosa from MTR, budget 250") == {"dish": "masala dosa", "restaurant": "MTR",
                                                                  "max_total": 250.0}
    assert A.food_request("Order paneer tikka from Barbeque Nation under 600")["restaurant"] == "Barbeque Nation"
    assert A.food_request("I feel like having pav bhaji, keep it under 250")["dish"] == "pav bhaji"

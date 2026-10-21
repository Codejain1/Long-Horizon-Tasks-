"""Bookings as forms: memory fills only what the owner chose before, the summary waits for YES, offers need pages."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hermes" / "embed_memory"))
import booking as B  # noqa: E402

TODAY = date(2026, 10, 21)


def store(tmp_path, t=[1_000_000.0]):
    return B.Store(tmp_path / "b.db", now=lambda: t[0])


def test_missing_slots_are_asked_not_guessed(tmp_path):
    s = store(tmp_path)
    v = B.start(s, "train", {"from": "Bangalore", "to": "Chennai", "date": "2 Nov"}, today=TODAY)
    assert v["slots"]["date"] == "2026-11-02"
    assert v["missing"] == ["passengers", "class"] and "summary" not in v and "class" in v["next"]
    assert "error" in B.confirm(s, v["task_id"], today=TODAY)  # can't say yes to a half form


def test_bad_dates_and_amounts_are_problems(tmp_path):
    s = store(tmp_path)
    v = B.start(s, "hotel", {"city": "Goa", "checkin": "2026-10-01", "checkout": "2026-11-03", "guests": ["A"],
                             "max_price_per_night": "about 3k"}, today=TODAY)
    assert any("checkin" in p for p in v["problems"]) and "summary" not in v
    v = B.start(s, "hotel", {"city": "Goa", "checkin": "2026-11-05", "checkout": "2026-11-03", "guests": ["A"],
                             "max_price_per_night": "₹3,000"}, today=TODAY)
    assert v["problems"] == ["checkout must be after checkin"]
    assert v["slots"]["max_price_per_night"] == 3000


def test_usual_choice_learned_only_after_repeated_yes(tmp_path):
    s = store(tmp_path)
    s.set_fact("traveller:self", {"name": "Kartik", "age": 27})
    ride = {"from": "BLR", "to": "MAA", "date": "tomorrow", "class": "3A"}
    first = B.start(s, "train", ride, today=TODAY)
    assert first["slots"]["passengers"] == ["Kartik"] and "summary" in first  # the owner, from the profile fact
    B.confirm(s, first["task_id"], today=TODAY)
    v = B.start(s, "train", {k: v for k, v in ride.items() if k != "class"}, today=TODAY)
    assert "class" in v["missing"]  # chose 3A once: not yet a habit
    B.confirm(s, B.start(s, "train", ride, today=TODAY)["task_id"], today=TODAY)
    v = B.start(s, "train", {k: v for k, v in ride.items() if k != "class"}, today=TODAY)
    assert v["slots"]["class"] == "3A" and v["from_memory"]["class"] == "3A" and "3A (usual)" in v["summary"]
    B.confirm(s, B.start(s, "train", {**ride, "class": "SL"}, today=TODAY)["task_id"], today=TODAY)
    B.confirm(s, B.start(s, "train", {**ride, "class": "SL"}, today=TODAY)["task_id"], today=TODAY)
    assert s.usual("train", "class") is None  # 2 vs 2: split, so ask again


def test_fixed_rails_and_confirm_flow(tmp_path):
    s = store(tmp_path)
    v = B.start(s, "cab", {"pickup": "home", "drop": "airport", "when": "tomorrow 5am"}, today=TODAY)
    assert v["slots"]["pay"] == "cash" and "(usual)" not in v["summary"]
    c = B.confirm(s, v["task_id"], today=TODAY)
    assert c["status"] == "confirmed"
    assert "error" in B.update(s, v["task_id"], {"drop": "station"}, today=TODAY)  # frozen after YES
    assert s.usual("cab", "pay") is None  # a rail is not a taste


def test_failures_become_site_notes(tmp_path):
    s = store(tmp_path)
    tid = B.start(s, "train", {"from": "BLR"}, site="https://www.irctc.co.in/nget", today=TODAY)["task_id"]
    B.finish(s, tid, "failed", "Tatkal opens at 10:00; searched at 09:50 and got no seats")
    v = B.start(s, "train", {"from": "BLR"}, site="IRCTC", today=TODAY)
    assert v["site_notes"] == ["Tatkal opens at 10:00; searched at 09:50 and got no seats"]


def test_offers_need_the_page_and_the_cap(tmp_path):
    s = store(tmp_path)
    tid = B.start(s, "food", {"dish": "paneer biryani", "max_total": 400}, today=TODAY)["task_id"]
    pages = {"https://www.swiggy.com/restaurants/meghana-123": "Paneer Biryani  ₹320 \n Delivery fee ₹40",
             "https://www.zomato.com/bangalore/meghana/order": "Paneer Biryani ₹ 299 ... 4.4 ★"}
    r = B.compare(s, tid, [
        {"platform": "Swiggy", "item": "Paneer Biryani", "price": 320, "fees": 40, "quote": "Paneer Biryani ₹320"},
        {"platform": "zomato", "item": "Paneer Biryani", "price": "₹299", "fees": 45, "discount": 0, "rating": 4.4,
         "quote": "Paneer Biryani ₹ 299"},
        {"platform": "magicpin", "item": "Paneer Biryani", "price": 250, "quote": "Paneer Biryani ₹250"},
        {"platform": "swiggy", "item": "Paneer Biryani", "price": 280, "quote": "Paneer Biryani ₹320"},
    ], pages)
    assert [(o["platform"], o["pay"]) for o in r["ranked"]] == [("zomato.com", 344), ("swiggy.com", 360)]
    why = {o["platform"].lower() + str(o["price"]): o["why"] for o in r["dropped"]}
    assert "not found" in why["magicpin250"] and "doesn't show the price" in why["swiggy280"]
    assert "₹16 less than swiggy.com" in r["summary"]
    assert s.fact("price:zomato.com:paneer biryani")["value"] == 344
    pages["https://www.zomato.com/bangalore/meghana/order"] = "Paneer Biryani ₹ 499"
    r = B.compare(s, tid, [{"platform": "zomato", "item": "x", "price": 499, "quote": "Paneer Biryani ₹ 499"}], pages)
    assert r["ranked"] == [] and "over the cap" in r["dropped"][0]["why"]


def test_world_facts_go_stale(tmp_path):
    t = [1_000_000.0]
    s = B.Store(tmp_path / "b.db", now=lambda: t[0])
    s.set_fact("price:swiggy.com:x", 300, "https://swiggy.com/x", ttl_h=6)
    s.set_fact("traveller:self", {"name": "K"})
    t[0] += 7 * 3600
    assert s.fact("price:swiggy.com:x")["stale"] and not s.fact("traveller:self")["stale"]


def test_trip_plan_drafts_follow_the_ticket(tmp_path):
    s = store(tmp_path)
    tid = B.start(s, "trip", {"to": "Goa", "arrive": "2026-11-07", "leave": "2026-11-09"}, today=TODAY)["task_id"]
    assert "error" in B.plan_trip(s, tid, today=TODAY)  # not confirmed yet
    B.confirm(s, tid, today=TODAY)
    p = B.plan_trip(s, tid, today=TODAY)
    assert p["days"] == ["2026-11-07", "2026-11-08", "2026-11-09"]
    hotel, cab = p["drafts"]
    assert hotel["slots"]["checkin"] == "2026-11-07" and hotel["missing"] == ["guests", "max_price_per_night"]
    assert cab["slots"]["pay"] == "cash" and cab["missing"] == ["drop"]
    assert s.task(hotel["task_id"])["parent"] == tid


def test_tool_dispatch(tmp_path):
    s = store(tmp_path)
    assert B.handle(s, "save_traveller", {"who": "Self", "name": "Kartik", "age": 27}, {}) == {"saved": "traveller:self"}
    v = B.handle(s, "task_start", {"kind": "bus", "slots": {"from": "BLR", "to": "Goa", "date": "tomorrow"}}, {})
    assert v["slots"]["passengers"] == ["Kartik"] and "summary" in v
    assert B.handle(s, "task_start", {"kind": "rocket", "slots": {}}, {})["error"].startswith("unknown kind")


def test_plugin_tools_and_pages_from_the_browser(tmp_path, monkeypatch):
    import json
    import types
    stub = types.ModuleType("agent.memory_provider")
    stub.MemoryProvider = object
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.memory_provider", stub)
    sys.path.insert(0, str(Path(__file__).parent.parent / "hermes"))
    import embed_memory as P
    monkeypatch.setenv("BOOKING_DB", str(tmp_path / "x" / "booking.db"))
    monkeypatch.setattr(P, "_store", None)
    monkeypatch.setattr(P, "_pages", {})
    p = P.EmbedMemoryProvider()
    assert {"task_start", "compare_offers"} <= {t["name"] for t in p.get_tool_schemas()}
    tid = json.loads(p.handle_tool_call("task_start", {"kind": "food", "slots": {"dish": "dosa", "max_total": 300}}))["task_id"]
    P.keep_page("browser_navigate", {"url": "https://www.swiggy.com/menu/1"}, "x" * 300 + " Masala Dosa ₹120 ", session_id="s")
    P.keep_page("terminal", {}, "Masala Dosa ₹90 " * 30, session_id="s")  # not a browser page
    r = json.loads(p.handle_tool_call("compare_offers", {"task_id": tid, "offers": [
        {"platform": "swiggy", "item": "Masala Dosa", "price": 120, "quote": "Masala Dosa ₹120"},
        {"platform": "swiggy", "item": "Masala Dosa", "price": 90, "quote": "Masala Dosa ₹90"}]}))
    assert [o["pay"] for o in r["ranked"]] == [120] and len(r["dropped"]) == 1
    assert "task_confirm" in p.system_prompt_block()


def test_spoken_dates():
    assert TODAY.weekday() == 2  # a Wednesday
    assert B._iso("friday", TODAY) == "2026-10-23" == B._iso("next Friday", TODAY)
    assert B._iso("wednesday", TODAY) == "2026-10-28" and B._iso("day after tomorrow", TODAY) == "2026-10-23"
    assert B._iso("5 jan", TODAY) == "2027-01-05" and B._iso("someday", TODAY) is None


def test_destinations_and_caps_are_never_habits(tmp_path):
    s = store(tmp_path)
    for _ in range(3):
        B.confirm(s, B.start(s, "flight", {"from": "BLR", "to": "DEL", "date": "tomorrow", "passengers": ["K"],
                                           "max_price": 8000, "cabin": "economy"}, today=TODAY)["task_id"], today=TODAY)
    v = B.start(s, "flight", {"date": "friday"}, today=TODAY)
    assert v["slots"]["from"] == "BLR" and v["slots"]["cabin"] == "economy"
    assert v["missing"] == ["to", "max_price"]


def test_spoken_amounts():
    assert [B._money(x) for x in ("₹3,000", "9k", "1.5 lakh", "under 400", "none")] == [3000, 9000, 150000, 400, None]


def test_small_model_spellings_are_normalised(tmp_path):
    s = store(tmp_path)
    s.set_fact("traveller:self", {"name": "Kartik"})
    s.set_fact("traveller:mom", {"name": "Sunita"})
    v = B.start(s, "flight", {"from": "current city", "to": "Goa", "date": "friday", "budget": "6k", "class": "business",
                              "passengers": "me and Rahul"}, today=TODAY)
    assert v["slots"]["max_price"] == 6000 and v["slots"]["cabin"] == "business" and "from" in v["missing"]
    assert v["slots"]["passengers"] == ["Kartik", "Rahul"]
    assert B.start(s, "train", {"passengers": "mom"}, today=TODAY)["slots"]["passengers"] == ["Sunita"]
    assert B.start(s, "train", {"passengers": 1}, today=TODAY)["slots"]["passengers"] == ["Kartik"]
    two = B.start(s, "train", {"passengers": "2"}, today=TODAY)
    assert "summary" not in two and any("who they are" in p for p in two["problems"])  # never just the owner
    cab = B.start(s, "cab", {"pickup": "home", "drop": "airport", "date": "tomorrow", "pay": "credit card"}, today=TODAY)
    assert cab["slots"]["pay"] == "cash" and cab["slots"]["when"] == "tomorrow"  # the rail wins; when (date + time) as said


def test_order_history_is_saved_once_and_shapes_food_tasks(tmp_path):
    s = store(tmp_path)
    assert B.start(s, "food", {"dish": "x"}, today=TODAY)["order_history"]["orders"] == 0
    orders = [{"id": "1", "restaurant": "Meghana Foods", "items": ["Paneer Biryani", "Coke"], "total": "₹420", "date": "12 Oct"},
              {"id": "2", "restaurant": "Meghana Foods", "items": ["Paneer Biryani"], "total": "₹350", "date": "15 Oct"},
              {"id": "3", "restaurant": "MTR", "items": ["Masala Dosa"], "total": "₹180", "date": "18 Oct"},
              {"id": "4", "restaurant": "Empty", "items": [], "total": "₹0"}]
    r = B.handle(s, "save_orders", {"platform": "Swiggy", "orders": orders}, {})
    assert r["saved"] == 3 and B.save_orders(s, "swiggy", orders)["saved"] == 0  # read twice, kept once
    h = B.start(s, "food", {"dish": "biryani"}, today=TODAY)["order_history"]
    assert h["top_dishes"][0] == ("paneer biryani", 2) and h["top_restaurants"][0] == ("Meghana Foods", 2)
    assert h["typical_spend"] == 350 and h["orders"] == 3


def test_plugin_prompt_routes_ordering_to_the_app_and_turns_thinking_off(monkeypatch):
    import types
    stub = types.ModuleType("agent.memory_provider")
    stub.MemoryProvider = object
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.memory_provider", stub)
    sys.path.insert(0, str(Path(__file__).parent.parent / "hermes"))
    import embed_memory as P
    block = P.EmbedMemoryProvider().system_prompt_block()
    assert "do NOT use nearby_food" in block and "save_orders" in block
    assert block.rstrip().endswith("/no_think")  # Qwen3's switch: ~1 s per step instead of 9-65 s (measured 2026-10-21)
    food = next(t for t in P.EmbedMemoryProvider().get_tool_schemas() if t["name"] == "nearby_food")
    assert "Only for going out" in food["description"]


def test_empty_save_orders_tells_hermes_to_browse_not_the_owner(tmp_path):
    r = B.save_orders(store(tmp_path), "swiggy", [])
    assert "browser_navigate" in r["error"] and "never ask the owner" in r["error"]

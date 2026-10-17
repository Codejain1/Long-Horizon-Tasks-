"""Hermes embed memory: the service stores and recalls by chat/day; the plugin saves turns and fails open."""

import sys
import threading
import time
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path[:0] = [str(ROOT / "memrouter"), str(ROOT / "hermes")]
import memory_service as M  # noqa: E402

sys.path.insert(0, str(ROOT / "hermes" / "embed_memory"))
import places as PL  # noqa: E402  (the plugin's package needs Hermes; its places module doesn't)


def fake_encode(texts, max_len=96):  # bag of hashed words: enough to make "rent" find "rent"
    out = np.zeros((len(texts), 64), np.float32)
    for i, t in enumerate(texts):
        for w in M.F.words(t):
            out[i, hash(w) % 64] += 1
    return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)


class KeywordRanker:  # stands in for the trained MLP: BM25 on the turn plus on its window
    def predict(self, X):
        return X[:, 0] + X[:, 1]


def memory(tmp_path):
    return M.Memory(tmp_path, fake_encode, KeywordRanker())


def test_recall_finds_old_message_and_orders_by_time(tmp_path):
    mem = memory(tmp_path)
    day = 86400
    mem.add("The office rent is 1.8 lakh a month, deposit paid.", "Rohan", "chat-a", ts=1_700_000_000)
    for k in range(40):
        mem.add(f"Random chatter number {k} about cricket and lunch.", "Neha", "chat-b", ts=1_700_000_000 + day * (k + 1))
    mem.add("Rent goes up next year.", "Rohan", "chat-a", ts=1_700_000_000 + day * 50)
    r = mem.search("how much is the office rent?", budget=60)
    assert "1.8 lakh" in r["context"] and r["n"] >= 1
    assert "November, 2023" in r["context"]  # stamped in the configured time zone, in the trained date style
    assert mem.search("", 500)["n"] == 0


def test_relative_dates_resolved_when_saved(tmp_path):
    mem = memory(tmp_path)
    mem.add("We tried scuba last Friday", "Jolene", "c", ts=1_694_944_800)  # Sun 17 Sep 2023, 15:30 IST
    assert "last Friday (Friday 15 September 2023)" in mem.db.execute("SELECT text FROM turns").fetchone()[0]


def test_long_messages_split_and_empty_ignored(tmp_path):
    mem = memory(tmp_path)
    assert mem.add("word " * 250, "me", "c") == 3
    assert mem.add("   ", "me", "c") == 0


def test_plugin_saves_turns_and_fails_open(tmp_path, monkeypatch):
    stub = types.ModuleType("agent.memory_provider")
    stub.MemoryProvider = object
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.memory_provider", stub)
    import embed_memory as P

    mem = memory(tmp_path)
    port = 18765
    threading.Thread(target=M.serve, args=(mem, port), daemon=True).start()
    time.sleep(0.3)
    monkeypatch.setenv("EMBED_MEMORY_URL", f"http://127.0.0.1:{port}")
    p = P.EmbedMemoryProvider()
    p.initialize("s1", agent_context="primary")
    p.sync_turn("My passport number ends in 4471, remember it.", "Noted.", session_id="s1",
                turn_author={"name": "Kartik"})
    for _ in range(50):
        if mem.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2:
            break
        time.sleep(0.05)
    out = p.prefetch("what does my passport number end in?")
    assert "## What you remember" in out and "Kartik: My passport" in out
    assert "fine-tuned bge-small" in p.system_prompt_block()
    assert '"n"' in p.handle_tool_call("memory_search", {"query": "passport"})
    assert "Hermes: Noted." not in out  # the agent's own replies are not fed back to it
    assert mem.search("noted", 500, ("Hermes",))["n"] <= 1

    sub = P.EmbedMemoryProvider()
    sub.initialize("s2", agent_context="subagent")
    sub.sync_turn("should not be saved", "x")
    time.sleep(0.2)
    assert mem.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2

    monkeypatch.setenv("EMBED_MEMORY_URL", "http://127.0.0.1:1")  # service down: no memory, no crash
    assert p.prefetch("anything") == ""
    assert "unavailable" in p.handle_tool_call("memory_search", {"query": "x"})


RULE = ("Hey just read one thing don't show me I want to install this thing or that, just do the things and give me "
        "the final output I want, so your messages would be like on it sir and when the thing is done give me the output")


def test_rule_detection():
    assert M.is_rule(RULE)
    assert M.is_rule("From now on reply in Hindi")
    assert M.is_rule("never paste code in WhatsApp")
    assert M.is_rule("don’t show me the code")  # phones type curly apostrophes
    for not_rule in ("Where do I usually get tea?", "Can you connect to my GitHub and write code there",
                     "Build me a landing page for my chai stall and deploy it"):
        assert not M.is_rule(not_rule), not_rule


def test_rule_applies_to_unrelated_requests_and_can_be_forgotten(tmp_path):
    mem = memory(tmp_path)
    rid = mem.add_rule(RULE)
    assert mem.add_rule(RULE) == 0  # no duplicates
    for k in range(30):
        mem.add(f"Chat about cricket number {k}", "Kartik", "c1", ts=1_700_000_000 + k)
    r = mem.search("Build me a landing page for my chai stall and deploy it", 100)
    assert [x["text"] for x in r["rules"]] == [" ".join(RULE.split())]  # recalled whatever the query
    assert mem.forget_rule(rid) and mem.search("anything", 100)["rules"] == []


def test_plugin_rules_every_reply_and_cron_kept_out(tmp_path, monkeypatch):
    stub = types.ModuleType("agent.memory_provider")
    stub.MemoryProvider = object
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.memory_provider", stub)
    import embed_memory as P

    mem = memory(tmp_path)
    port = 18766
    threading.Thread(target=M.serve, args=(mem, port), daemon=True).start()
    time.sleep(0.3)
    monkeypatch.setenv("EMBED_MEMORY_URL", f"http://127.0.0.1:{port}")
    p = P.EmbedMemoryProvider()
    p.initialize("s1", agent_context="primary")
    p.sync_turn(RULE, "```python\nprint('code dump')\n```", session_id="s1", turn_author={"name": "Kartik"})
    p.sync_turn("[IMPORTANT: You are running as a scheduled cron job] never treat ERROR as a change", "ok",
                session_id="cron_abc_1")
    mem.add("noise - target ERROR monitor prompt", "user", "cron_old_job")  # saved before the fix
    for _ in range(50):
        if mem.pending("rules"):
            break
        time.sleep(0.05)
    time.sleep(0.2)
    pending = mem.pending("rules")
    assert len(pending) == 1 and mem.rules() == []  # inferred from chat: waits for the owner's OK
    assert "cron" not in pending[0]["text"]  # the cron prompt said "never" but is not the user: not a rule
    ask = p.prefetch("Build me a landing page for my chai stall and deploy it")
    assert "Standing rules" not in ask and "waiting for the owner's OK" in ask and f"#{pending[0]['id']}" in ask
    assert '"confirmed": true' in p.handle_tool_call("confirm_rule", {"id": pending[0]["id"]})
    out = p.prefetch("Build me a landing page for my chai stall and deploy it")
    assert out.startswith("## Standing rules from the user") and "on it sir" in out
    assert "cron" not in out and "noise - target" not in p.prefetch("noise target ERROR monitor")
    assert "on it sir" in p.handle_tool_call("list_rules", {})
    rid = mem.rules()[0]["id"]
    assert '"forgotten": true' in p.handle_tool_call("forget_rule", {"id": rid})
    assert "Standing rules" not in p.prefetch("Build me a landing page")


def test_latest_location_replaces_older_and_reaches_every_reply(tmp_path, monkeypatch):
    mem = memory(tmp_path)
    assert mem.note_location("[Location: Third Wave Coffee 12.9784,77.6408]")["name"] == "Third Wave Coffee"
    mem.note_location("[Live location: 12.9352,77.6245]")
    loc = mem.current()["location"]
    assert (loc["lat"], loc["lng"], loc["live"]) == (12.9352, 77.6245, True) and loc["hours_ago"] < 0.1
    assert mem.note_location("meet me at 5") is None
    assert mem.search("best biryani near me", 50)["current"]["location"]["lat"] == 12.9352

    stub = types.ModuleType("agent.memory_provider")
    stub.MemoryProvider = object
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.memory_provider", stub)
    import embed_memory as P

    block = P.EmbedMemoryProvider._location_block(loc)
    assert "12.9352,77.6245" in block and "over 6 hours" not in block
    assert "over 6 hours" in P.EmbedMemoryProvider._location_block({**loc, "hours_ago": 9.0})
    assert "Not shared yet" in P.EmbedMemoryProvider._location_block(None)


def fake_fetch(url, data=None, timeout=20):
    if "nominatim" in url:
        return {"address": {"road": "80 Feet Road", "suburb": "Koramangala", "city": "Bengaluru"}}
    return {"elements": [
        {"type": "node", "lat": 12.9360, "lon": 77.6140, "tags": {"name": "Burger Barn", "amenity": "fast_food",
                                                                   "cuisine": "burger"}},
        {"type": "node", "lat": 12.9400, "lon": 77.6200, "tags": {"name": "Sattvam", "amenity": "restaurant",
                                                                   "cuisine": "indian;north_indian",
                                                                   "diet:vegetarian": "only"}},
        {"type": "way", "center": {"lat": 12.9370, "lon": 77.6150}, "tags": {"name": "Tokyo Table",
                                                                              "amenity": "restaurant",
                                                                              "cuisine": "japanese"}},
        {"type": "node", "lat": 12.95, "lon": 77.62, "tags": {"amenity": "cafe"}}]}  # no name: skipped


def test_profile_mood_area_and_real_nearby_places(tmp_path):
    mem = M.Memory(tmp_path, fake_encode, KeywordRanker(), fetch=fake_fetch)
    assert M.is_profile("I am vegetarian") and M.is_profile("I love Japanese and Mexican food")
    assert M.is_profile("I don't eat mushrooms") and not M.is_profile("Where is the meeting?")
    assert M.mood_of("I'm feeling really tired today") == "tired" and M.mood_of("feeling stressed about work") == "stressed"
    assert M.mood_of("The low tide was nice") is None and M.mood_of("book a table") is None
    mem.add_profile("Vegetarian; spice medium")
    assert mem.add_profile("Vegetarian; spice medium") == 0 and len(mem.profile()) == 1
    mem.note_mood("ugh I'm so exhausted")
    loc = mem.note_location("[Live location: 12.9353,77.6134]")
    assert loc["area"] == "80 Feet Road, Koramangala, Bengaluru"
    loc = mem.current()["location"]
    r = PL.nearby_food(loc, fetch=fake_fetch)
    assert r["near"] == "80 Feet Road, Koramangala, Bengaluru" and r["count"] == 3
    assert r["places"][0]["name"] == "Sattvam"  # vegetarian-only first, then nearest
    assert [p["name"] for p in PL.nearby_food(loc, cuisine="japanese", fetch=fake_fetch)["places"]] == ["Tokyo Table"]
    assert "maps" in r["places"][0] and r["places"][1]["km"] < r["places"][2]["km"]
    cur = mem.search("food", 50)["current"]
    assert cur["mood"]["mood"] == "tired" and cur["profile"][0]["text"].startswith("Vegetarian")


def test_nearby_without_location_and_plugin_blocks(tmp_path, monkeypatch):
    assert "no location" in PL.nearby_food(None, fetch=fake_fetch)["error"]
    stub = types.ModuleType("agent.memory_provider")
    stub.MemoryProvider = object
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.memory_provider", stub)
    import embed_memory as P

    b = P.EmbedMemoryProvider._profile_block([{"id": 1, "text": "Vegetarian"}],
                                              {"mood": "tired", "said": "so exhausted", "when": "now", "hours_ago": 0.2})
    assert "Vegetarian" in b and "Current mood: tired" in b
    assert "Koramangala" in P.EmbedMemoryProvider._location_block(
        {"lat": 1, "lng": 2, "area": "Koramangala", "when": "now", "hours_ago": 0.1})
    assert "pick ONLY from the places it returns" in P.EmbedMemoryProvider().system_prompt_block()


def test_nearby_falls_back_to_nominatim_when_overpass_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(PL.time, "sleep", lambda s: None)

    def fetch(url, data=None, timeout=20):
        if "interpreter" in url:
            raise TimeoutError("overpass overloaded")
        if "reverse" in url:
            return {"address": {"suburb": "Koramangala"}}
        return [{"name": "Adaa Restaurant", "lat": "12.9360", "lon": "77.6140", "type": "restaurant",
                 "extratags": {"cuisine": "indian"}},
                {"name": "Far Away Diner", "lat": "13.2000", "lon": "77.9000", "type": "restaurant"}]

    mem = M.Memory(tmp_path, fake_encode, KeywordRanker(), fetch=fetch)
    mem.note_location("[Location: 12.9353,77.6134]")
    r = PL.nearby_food(mem.current()["location"], 1500, fetch=fetch)
    assert [p["name"] for p in r["places"]] == ["Adaa Restaurant"] and r["places"][0]["cuisine"] == "indian"


def test_program_text_is_stored_as_is_with_its_kind_and_search_scores_items(tmp_path):
    class Boom:
        def write(self, *a, **k):
            raise AssertionError("structured text must not go through the writer")
    mem = memory(tmp_path)
    mem.writer = Boom()
    assert mem.add("When a build step fails with X -> what fixed it: Y", "runner", "runner-experience",
                   kind="advice") == 1
    row = mem.db.execute("SELECT text, kind, method, raw FROM turns").fetchone()
    assert row[:3] == ("When a build step fails with X -> what fixed it: Y", "advice", "program")
    [it] = mem.search("build step fails")["items"]
    assert it["kind"] == "advice" and isinstance(it["score"], float) and it["score"] > 0


def test_concurrent_requests_lose_nothing(tmp_path):
    """Hermes saves turns in background threads while the runner records episodes and searches: the server handles
    each request on its own thread over one SQLite store. Nothing may fail or go missing."""
    import json
    import urllib.request

    mem = memory(tmp_path)
    threading.Thread(target=M.serve, args=(mem, 18820), daemon=True).start()
    time.sleep(0.3)
    errors = []

    def post(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:18820{path}", json.dumps(body).encode())
        try:
            out = json.loads(urllib.request.urlopen(req, timeout=60).read())
            if "error" in out:
                errors.append(out["error"])
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    def worker(k):
        for i in range(15):
            post("/add", {"text": f"note {k}-{i} about the rent and the lease", "speaker": "owner", "chat": f"c{k}"})
            post("/outcome", {"scope": "s", "failed": [{"check": f"test_{k}_{i}", "error": "assert 1 == 2"}]})
            post("/search", {"query": "rent lease", "budget": 200})
            post("/rule", {"text": f"rule {k}-{i}"})
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert mem.db.execute("SELECT COUNT(*) FROM turns WHERE chat LIKE 'c%'").fetchone()[0] == 120
    assert mem.db.execute("SELECT COUNT(*) FROM failures").fetchone()[0] == 120
    assert len(mem.rules(1000)) == 120

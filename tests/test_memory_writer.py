"""Memory writer: typed, cleaned memories at save time; replaced facts marked; agent claims kept out of recall."""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path[:0] = [str(ROOT / "memrouter"), str(ROOT / "memrouter")]
import writer as W  # noqa: E402
from test_hermes_memory import KeywordRanker, fake_encode  # noqa: E402

import memory_service as M  # noqa: E402

DAY = 86400


def test_parse_is_strict_and_never_raises():
    raw = 'sure! {"memories": [{"kind": "rule", "memory": "Never send emojis.", "slot": ""},' \
          '{"kind": "mood", "memory": "Owner is exhausted.", "mood": "tired"},' \
          '{"kind": "mood", "memory": "x", "mood": "elated"}, {"kind": "gossip", "memory": "y"}]}'
    out = W.parse(raw)
    assert [m["kind"] for m in out] == ["rule", "mood"] and out[1]["mood"] == "tired"
    assert W.parse("not json at all") == [] and W.parse('{"memories": "oops"}') == []


def test_prompt_carries_date_context_and_existing_slots():
    seen = {}

    def chat(url, model, prompt, timeout):
        seen["p"] = prompt
        return '{"memories": []}'

    W.Writer("http://x", chat=chat).write("we fly on Friday", "Kartik (the owner)", datetime(2023, 10, 3, 9),
                                          ["Mom: ok"], ["home address"])
    assert "3 October 2023" in seen["p"] and "Tuesday" in seen["p"] and '"home address"' in seen["p"]
    assert "Mom: ok" in seen["p"] and "we fly on Friday" in seen["p"]


class ScriptedWriter:
    """Stands in for the model: returns the memories scripted for each message."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def write(self, text, speaker, when, recent=(), slots=()):
        self.calls.append((text, speaker, list(slots)))
        out = self.script[text]
        if isinstance(out, Exception):
            raise out
        return W.parse(json.dumps({"memories": out}))


def test_writer_routes_supersedes_and_filters(tmp_path):
    wr = ScriptedWriter({
        "our wifi is ACT": [{"kind": "fact", "memory": "The owner's internet provider is ACT Fibernet.",
                             "slot": "internet provider"}],
        "switched to airtel": [{"kind": "fact", "memory": "The owner's internet provider is Airtel Xstream.",
                                "slot": "internet provider"}],
        "no emojis please": [{"kind": "rule", "memory": "Never use emojis in replies.", "slot": ""}],
        "aaj bahut thak gaya hoon": [{"kind": "mood", "memory": "The owner is very tired.", "mood": "tired"}],
        "I gave up meat last year": [{"kind": "preference", "memory": "The owner has been vegetarian since 2022."}],
        "Your app is live at https://a.trycloudflare.com": [
            {"kind": "agent_action", "memory": "The assistant published the app at https://a.trycloudflare.com."}],
        "I cannot access the internet": [{"kind": "agent_claim", "memory": "The assistant said it has no internet."}],
        "ok cool": [{"kind": "chitchat", "memory": ""}],
        "model down": RuntimeError("timeout"),
    })
    mem = M.Memory(tmp_path, fake_encode, KeywordRanker(), writer=wr)
    t0 = 1_696_300_000
    mem.add("our wifi is ACT", "Kartik", "c", t0, owner=True)
    mem.add("switched to airtel", "Kartik", "c", t0 + 30 * DAY, owner=True)
    assert wr.calls[1][2] == ["internet provider"]  # the writer is offered existing slot names to reuse
    for k, text in enumerate(["no emojis please", "aaj bahut thak gaya hoon", "I gave up meat last year", "ok cool"]):
        mem.add(text, "Kartik", "c", t0 + (31 + k) * DAY, owner=True)
    mem.add("Your app is live at https://a.trycloudflare.com", "Hermes", "c", t0 + 40 * DAY, role="assistant")
    mem.add("I cannot access the internet", "Hermes", "c", t0 + 41 * DAY, role="assistant")
    # a model inferred these from chat: they wait for the owner's OK before they shape every reply
    assert mem.rules() == [] and [r["text"] for r in mem.pending("rules")] == ["Never use emojis in replies."]
    assert mem.profile() == [] and [p["text"] for p in mem.pending("profile")] == ["The owner has been vegetarian since 2022."]
    assert mem.confirm("rules", mem.pending("rules")[0]["id"]) and mem.confirm("profile", mem.pending("profile")[0]["id"])
    assert [r["text"] for r in mem.rules()] == ["Never use emojis in replies."] and mem.pending("rules") == []
    assert [p["text"] for p in mem.profile()] == ["The owner has been vegetarian since 2022."]
    assert mem.current()["mood"]["mood"] == "tired"

    r = mem.search("who is our internet provider?", 500, ("Hermes",), (), ("agent_claim", "chitchat"))
    ctx = r["context"]
    assert "Airtel" in ctx and "(OUTDATED: replaced on" in ctx.split("ACT")[0].splitlines()[-1]  # old value marked
    assert "Airtel Xstream." in [ln for ln in ctx.splitlines() if "Airtel" in ln][0] and "OUTDATED" not in \
        [ln for ln in ctx.splitlines() if "Airtel" in ln][0]
    ctx = mem.search("what URL did you give me for the app?", 500, ("Hermes",), (), ("agent_claim", "chitchat"))["context"]
    assert "trycloudflare" in ctx                     # what the agent DID is recallable
    assert "no internet" not in ctx and "ok cool" not in ctx  # what it CLAIMED about itself, and filler, are not

    mem.add("model down", "Kartik", "c", t0 + 50 * DAY, owner=True)  # writer fails: rule-based path, nothing lost
    assert "model down" in mem.db.execute("SELECT text FROM turns ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_old_store_gets_new_columns(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "memory.db")
    db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, ts REAL, chat TEXT, speaker TEXT, text TEXT, vec BLOB)")
    db.commit()
    db.close()
    mem = M.Memory(tmp_path, fake_encode, KeywordRanker())
    assert {"kind", "slot", "raw", "replaced_ts"} <= {r[1] for r in mem.db.execute("PRAGMA table_info(turns)")}


def test_mood_and_taste_only_when_about_the_owner():
    raw = '{"memories": [{"english": "My friend is vegan", "about": "other", "kind": "preference", "memory": "x"},' \
          '{"english": "the movie was sad", "about": "other", "kind": "mood", "memory": "y", "mood": "sad"},' \
          '{"english": "I am exhausted", "about": "owner", "kind": "mood", "memory": "z", "mood": "tired"}]}'
    assert [(m["kind"], m["mood"]) for m in W.parse(raw)] == [("fact", ""), ("fact", ""), ("mood", "tired")]


def test_verify_finds_what_the_writer_dropped():
    raw = "Deposit of 45,000 for the HSR Layout flat, pay at https://pay.example.com/x by 3 Nov to Ramesh"
    assert M.verify("The owner must pay a 45000 deposit for the HSR Layout flat at https://pay.example.com/x by "
                    "3 November 2023 to Ramesh.", raw) == []
    lost = M.verify("The owner must pay a deposit for the flat.", raw)
    assert {"45,000", "HSR Layout", "https://pay.example.com/x", "Ramesh"} <= set(lost)


def test_provenance_mismatch_correction_forget_and_slot_meaning(tmp_path):
    wr = ScriptedWriter({
        "rent is 18,500 to Ramesh": [{"kind": "fact", "memory": "The owner pays rent.", "slot": "owner: rent"}],
        "our wifi is ACT": [{"kind": "fact", "memory": "The owner's internet provider is ACT.",
                             "slot": "owner: internet provider"}],
        "switched to airtel": [{"kind": "fact", "memory": "The owner's internet provider is Airtel.",
                                "slot": "owner: internet company"}],  # a different name for the same slot
    })
    mem = M.Memory(tmp_path, fake_encode, KeywordRanker(), writer=wr)
    mem.encode = lambda texts, max_len=96: fake_encode(  # slot names that differ only in synonyms read as the same
        [t.replace("company", "provider") for t in texts], max_len)
    t0 = 1_696_300_000
    mem.add("rent is 18,500 to Ramesh", "Kartik", "c", t0, owner=True)
    r = mem.search("how much rent do I pay?", 500)
    item = r["items"][0]
    assert item["checked"] == "mismatch" and item["confidence"] == 0.5 and "[said: rent is 18,500 to Ramesh]" in item["text"]
    assert r["context"].startswith(f"#{item['id']} [") and "(unverified)" in r["context"]

    new = mem.correct(item["id"], "The owner pays 18,500 rupees rent a month to Ramesh.")
    ctx = mem.search("how much rent do I pay?", 500)["context"]
    assert f"#{new} " in ctx and "OUTDATED: corrected" in [ln for ln in ctx.splitlines() if ln.startswith(f"#{item['id']} ")][0]
    assert mem.forget(new) and f"#{new} " not in mem.search("how much rent do I pay?", 500)["context"]

    mem.add("our wifi is ACT", "Kartik", "c", t0 + 10, owner=True)
    mem.add("switched to airtel", "Kartik", "c", t0 + 20, owner=True)
    ctx = mem.search("who is our internet provider?", 500)["context"]
    assert "OUTDATED: replaced" in [ln for ln in ctx.splitlines() if "ACT" in ln][0]  # matched by meaning, replaced


def test_search_does_not_wait_for_a_big_rebuild(tmp_path, monkeypatch):
    mem = M.Memory(tmp_path, fake_encode, KeywordRanker())
    mem.add("first note about Goa", "Kartik", "c", 1_696_300_000)
    assert "Goa" in mem.search("goa", 100)["context"]
    monkeypatch.setattr(M.Memory, "SYNC_BELOW", 1)  # pretend the store is big
    mem.add("second note about Ooty", "Kartik", "c", 1_696_300_100)
    first = mem.search("ooty", 100)["context"]  # served from the previous index, rebuild starts in the background
    for _ in range(50):
        if not mem.rebuilding and not mem.dirty:
            break
        __import__("time").sleep(0.05)
    assert "Ooty" not in first and "Ooty" in mem.search("ooty", 100)["context"]


def test_owner_corrections_become_hermes_experience_and_reach_the_next_reply(tmp_path, monkeypatch):
    import threading
    import time
    import types
    wr = ScriptedWriter({
        "no, I said under 200 rupees": [{"kind": "correction", "slot": "food picks: budget",
                                         "memory": "The picks were over budget; the owner wants dishes under ₹200."}],
        "again too expensive, under 200!": [{"kind": "correction", "slot": "food picks: budget",
                                             "memory": "Picks again over ₹200; keep every dish under ₹200."}],
        "the assistant was wrong, says the friend": [{"kind": "correction", "slot": "x", "memory": "not the owner"}],
    })
    mem = M.Memory(tmp_path, fake_encode, KeywordRanker(), writer=wr)
    mem.add("no, I said under 200 rupees", "Kartik", "c", 1_700_000_000, owner=True)
    mem.add("again too expensive, under 200!", "Kartik", "c", 1_700_000_100, owner=True)
    mem.add("the assistant was wrong, says the friend", "Riya", "c", 1_700_000_200)  # not the owner: no verdict
    assert [(s["check"], s["seen"]) for s in mem.fails.stats("hermes")] == [("food picks: budget", 2)]

    stub = types.ModuleType("agent.memory_provider")
    stub.MemoryProvider = object
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.memory_provider", stub)
    sys.path.insert(0, str(ROOT / "hermes"))
    import embed_memory as P
    threading.Thread(target=M.serve, args=(mem, 18832), daemon=True).start()
    time.sleep(0.3)
    monkeypatch.setenv("EMBED_MEMORY_URL", "http://127.0.0.1:18832")
    out = P.EmbedMemoryProvider().prefetch("suggest dessert near me")
    assert "## Mistakes the owner corrected before" in out
    assert "keep every dish under ₹200. (corrected 2x)" in out

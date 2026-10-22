"""Hermes's jobs.json converts to bot jobs; model jobs gather real inputs; weekly-reflect never fabricates."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
import jobs as J  # noqa: E402

HERMES = {"jobs": [
    {"name": "daily-infra-digest", "no_agent": True, "script": "infra-health-digest.sh", "prompt": "",
     "schedule": {"kind": "cron", "expr": "30 2 * * *"}, "deliver": "telegram:1", "enabled": True},
    {"name": "flowpe-watch", "no_agent": False, "monitor_script": "site-watch.py", "prompt": "A watched page changed",
     "monitor_state": {"last_output": "a 123\nb 456"}, "schedule": {"kind": "cron", "expr": "0 */2 * * *"},
     "deliver": "telegram:1", "enabled": True},
    {"name": "daily-companion", "prompt": "find one thing", "schedule": {"kind": "cron", "expr": "30 12 * * *"},
     "deliver": "telegram:1", "enabled": True, "paused_at": "2026-10-01"}]}


def test_load_kinds_seed_and_pause(tmp_path):
    (tmp_path / "jobs.json").write_text(json.dumps(HERMES))
    got = {j["name"]: j for j in J.load(tmp_path / "jobs.json")}
    assert got["daily-infra-digest"]["kind"] == "script" and got["daily-infra-digest"]["schedule"] == "30 2 * * *"
    assert got["flowpe-watch"]["kind"] == "monitor" and got["flowpe-watch"]["seed"] == "a 123\nb 456"
    assert got["flowpe-watch"]["script"] == "site-watch.py"
    assert got["daily-companion"]["kind"] == "model" and not got["daily-companion"]["enabled"]


def test_monitor_seeded_from_hermes_stays_quiet_at_switch_over(tmp_path):
    import scheduler as S
    (tmp_path / "w.sh").write_text("#!/bin/bash\nprintf 'a 123\\nb 456'\n")
    (tmp_path / "w.sh").chmod(0o755)
    sent = []
    job = {"name": "flowpe-watch", "kind": "monitor", "script": "w.sh", "seed": "a 123\nb 456", "deliver": "t:1",
           "schedule": "0 */2 * * *", "prompt": ""}
    s = S.Scheduler([job], tmp_path / "st.json", lambda text, to: sent.append(text), lambda j, c: "changed", None, tmp_path)
    assert s.run(job) == "" and sent == []


def fake_fetch(url):
    if "algolia" in url:
        return json.dumps({"hits": [{"title": "A paper on agents", "url": "https://x.org/a", "points": 300}]}).encode()
    return b"<rss><channel><item><title>RBI holds rate</title><link>https://n.in/1</link></item></channel></rss>"


def test_companion_gets_real_candidates_and_its_history(tmp_path):
    (tmp_path / "USER.md").write_text("Kartik runs FlowPe")
    calls = []

    def chat(messages, **kw):
        calls.append(messages)
        return {"content": " Pick: the agents paper. Why: ... Question: ... "}
    jb = J.Jobs(tmp_path, log=lambda d: [], state={"daily-companion": {"sent": [{"at": "2026-10-21", "text": "RBI"}]}},
                chat=chat, fetch=fake_fetch)
    job = {"name": "daily-companion", "prompt": "find one thing"}
    ctx = jb.gather(job)
    assert "[HN 300 pts] A paper on agents https://x.org/a" in ctx and "[India news] RBI holds rate" in ctx
    assert "2026-10-21: RBI" in ctx
    assert jb.think(job, ctx) == "Pick: the agents paper. Why: ... Question: ..."
    assert "Kartik runs FlowPe" in calls[0][0]["content"] and "find one thing" in calls[0][0]["content"]


def test_reflect_never_invents_and_keeps_headings(tmp_path):
    learned = "# LEARNED\n## What lands\n- markets\n## Where I've been wrong\n- nothing yet\n"
    (tmp_path / "LEARNED.md").write_text(learned)
    reply = {}
    jb = J.Jobs(tmp_path, log=lambda d: [], chat=lambda m, **kw: reply)
    job = {"name": "weekly-reflect", "prompt": "self-edit"}
    assert "No messages from Kartik" in jb.think(job, jb.gather(job))  # no owner messages: no model call, no change
    jb.log = lambda d: [("2026-10-20T10:00", "owner", "stop sending crypto"), ("2026-10-20T10:01", "bot", "ok")]
    reply.update(content=json.dumps({"learned_md": "# LEARNED\n- crypto ignored", "message": "x"}))
    assert "unchanged" in jb.think(job, jb.gather(job)) and (tmp_path / "LEARNED.md").read_text() == learned
    new = learned.replace("- nothing yet", "- sent crypto 3x, ignored")
    reply.update(content="here: " + json.dumps({"learned_md": new, "message": "Stopped crypto."}))
    assert jb.think(job, jb.gather(job)) == "Stopped crypto." and (tmp_path / "LEARNED.md").read_text() == new


def test_canary_proves_the_model_answers(tmp_path):
    jb = J.Jobs(tmp_path, log=lambda d: [], chat=lambda m, **kw: {"content": "ok"})
    assert jb.think({"name": "pipeline-canary"}, "").startswith("✅ Weekly canary OK — model=")


def test_canary_and_switch_over_seed(tmp_path):
    import time
    sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
    import companion_canary as K
    st, learned = tmp_path / "jobs-state.json", tmp_path / "LEARNED.md"
    learned.write_text("x")
    now = time.time()
    st.write_text(json.dumps({"daily-companion": {"sent": [{"at": time.strftime("%Y-%m-%dT%H:%M", time.gmtime(now - 3600))}]}}))
    assert K.check(st, learned, now) == []
    assert "36 h" in K.check(st, learned, now + 40 * 3600)[0]
    assert any("LEARNED.md" in m for m in K.check(st, learned, now + 9 * 86400))
    h = dict(HERMES)
    h["jobs"] = [dict(HERMES["jobs"][0], last_run_at="2026-10-21T02:30:03.156519+00:00")]
    (tmp_path / "jobs.json").write_text(json.dumps(h))
    assert J.load(tmp_path / "jobs.json")[0]["last_run"] == "2026-10-21T02:30"


def test_build_converts_hermes_jobs_and_seeds_state(tmp_path, monkeypatch):
    import main as M
    (tmp_path / "hermes" / "cron").mkdir(parents=True)
    h = {"jobs": [dict(HERMES["jobs"][0], last_run_at="2026-10-21T02:30:03+00:00")]}
    (tmp_path / "hermes" / "cron" / "jobs.json").write_text(json.dumps(h))
    chans, bot, sched = M.build({"BOT_HOME": str(tmp_path / "bot"), "HERMES_HOME": str(tmp_path / "hermes"),
                                 "BOT_APP_PORT": "0"})
    chans["app"].close()
    assert (tmp_path / "bot" / "jobs.json").exists() and [j["name"] for j in sched.jobs] == ["daily-infra-digest"]
    assert sched.state["daily-infra-digest"]["sent"][0]["at"] == "2026-10-21T02:30"
    assert set(chans) == {"whatsapp", "telegram", "app"} and sched.think is not None

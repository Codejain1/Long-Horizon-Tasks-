"""The bot's scheduler runs Hermes's jobs: cron times, script/monitor/model kinds, silence rules, failures surfaced."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
import scheduler as S  # noqa: E402


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_cron_matching():
    assert S.due("*/10 * * * *", t("2026-10-22T18:30")) and not S.due("*/10 * * * *", t("2026-10-22T18:31"))
    assert S.due("30 2 * * *", t("2026-10-22T02:30")) and not S.due("30 2 * * *", t("2026-10-22T03:30"))
    assert S.due("0 15 * * 0", t("2026-10-25T15:00"))          # 25 Oct 2026 is a Sunday
    assert not S.due("0 15 * * 0", t("2026-10-26T15:00"))
    assert S.due("0 */2 * * *", t("2026-10-22T20:00")) and not S.due("0 */2 * * *", t("2026-10-22T21:00"))
    assert S.due("0 9 1-5 1,6 *", t("2026-06-03T09:00")) and not S.due("0 9 1-5 1,6 *", t("2026-07-03T09:00"))


def script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/bin/bash\n" + body + "\n")
    p.chmod(0o755)
    return name


def sched(tmp_path, jobs, think=lambda job, ctx: f"[{job['name']}] {ctx[:200]}", gather=None):
    sent = []
    s = S.Scheduler(jobs, tmp_path / "state.json", lambda text, to: sent.append((to, text)), think, gather, tmp_path)
    return s, sent


def test_script_job_sends_output_and_stays_silent_when_empty(tmp_path):
    jobs = [{"name": "digest", "kind": "script", "script": script(tmp_path, "d.sh", "echo '[OK] all good'"),
             "schedule": "30 2 * * *", "deliver": "telegram:1"},
            {"name": "backup", "kind": "script", "script": script(tmp_path, "b.sh", "true"),
             "schedule": "30 2 * * *", "deliver": "telegram:1"}]
    s, sent = sched(tmp_path, jobs)
    assert s.tick(t("2026-10-22T02:30"), wait=True) == ["digest", "backup"]
    assert sent == [("telegram:1", "[OK] all good")]
    assert s.tick(t("2026-10-22T02:30"), wait=True) == []  # once per minute, even across ticks
    s2, _ = sched(tmp_path, jobs)  # and across restarts
    assert s2.tick(t("2026-10-22T02:30")) == []


def test_monitor_silent_until_change_and_error_counts(tmp_path):
    out = tmp_path / "out.txt"
    out.write_text("site-a abc123")
    job = {"name": "watch", "kind": "monitor", "script": script(tmp_path, "w.sh", f"cat {out}"),
           "schedule": "0 */2 * * *", "deliver": "telegram:1", "prompt": "summarise"}
    s, sent = sched(tmp_path, [job])
    assert s.run(job) == "" and s.run(job) == ""  # first run seeds, unchanged stays silent
    out.write_text("site-a ERROR 403 blocked")
    msg = s.run(job)
    assert "MONITOR CHANGE DETECTED" in msg and "+site-a ERROR 403 blocked" in msg and len(sent) == 1
    assert s.run(job) == ""


def test_model_job_and_failures_are_reported(tmp_path):
    job = {"name": "companion", "kind": "model", "schedule": "30 12 * * *", "deliver": "telegram:1", "prompt": "p"}
    s, sent = sched(tmp_path, [job], gather=lambda j: "3 candidate stories")
    assert s.run(job) == "[companion] 3 candidate stories"
    broken = {"name": "x", "kind": "script", "script": script(tmp_path, "x.sh", "echo boom >&2; exit 3"),
              "schedule": "* * * * *", "deliver": "telegram:1"}
    assert "ERROR x.sh exited 3: boom" in s.run(broken)

    def explode(job, ctx):
        raise TimeoutError("model down")
    s2, sent2 = sched(tmp_path, [job], think=explode, gather=lambda j: "")
    assert "failed: TimeoutError('model down')" in s2.run(job) and sent2
    assert s2.state["companion"]["sent"][-1]["text"].startswith("⚠ job companion failed")

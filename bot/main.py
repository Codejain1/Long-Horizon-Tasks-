"""The bot: poll WhatsApp and Telegram, answer the owner, run the scheduled jobs. Replaces the Hermes gateway.

    python bot/main.py            (env: BOT_HOME, HERMES_HOME, BOT_MODEL_URL, MEMORY_URL, BRIDGE_URL, plus the
                                    Telegram/WhatsApp variables from the existing env file, via systemd)

On first start, Hermes's jobs.json is converted to BOT_HOME/jobs.json; edit that file to change jobs.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "hermes" / "embed_memory")]
import assistant as A  # noqa: E402
import booking as B  # noqa: E402
import channels as C  # noqa: E402
import jobs as J  # noqa: E402
import scheduler as S  # noqa: E402

log = logging.getLogger("bot")


def build(env=os.environ):
    home = Path(env.get("BOT_HOME", Path.home() / ".bot"))
    hermes = Path(env.get("HERMES_HOME", Path.home() / ".hermes"))
    home.mkdir(parents=True, exist_ok=True)
    chans = {"whatsapp": C.WhatsApp(env.get("BRIDGE_URL", "http://127.0.0.1:3000")), "telegram": C.Telegram()}
    bot = A.Assistant(home, B.Store(home / "booking.db"), env.get("MEMORY_URL", "http://127.0.0.1:8765"))
    jobs_file = home / "jobs.json"
    if not jobs_file.exists() and (hermes / "cron" / "jobs.json").exists():
        jobs_file.write_text(json.dumps(J.load(hermes / "cron" / "jobs.json"), indent=1))
    joblist = json.loads(jobs_file.read_text()) if jobs_file.exists() else []
    sched = S.Scheduler(joblist, home / "jobs-state.json", lambda text, to: C.deliver(chans, to, text),
                        think=None, gather=None, scripts=hermes / "scripts")
    for job in joblist:  # first start: Hermes's last runs, so canaries don't call a job dead at switch-over
        if job.get("last_run") and job["name"] not in sched.state:
            sched.state[job["name"]] = {"sent": [{"at": job["last_run"], "text": "(ran under Hermes)"}]}
    jb = J.Jobs(hermes / "memories", bot.log_days, sched.state)
    sched.think, sched.gather = jb.think, jb.gather
    return chans, bot, sched


def serve(chans, bot, sched, once: bool = False):
    pool = ThreadPoolExecutor(4)
    busy: dict[str, threading.Lock] = {}

    def answer(ch, m):
        with busy.setdefault(m["chat"], threading.Lock()):  # one turn at a time per chat, in order
            t = time.time()
            try:
                reply = bot.handle(m, notify=lambda text: ch.send(m["chat"], text))
            except Exception as e:
                log.exception("turn failed")
                reply = f"Sorry, something broke on my side ({type(e).__name__}). Try again in a minute."
            try:
                ch.send(m["chat"], reply)
            except Exception:
                log.exception("send failed")
            log.info("turn %s %.1fs", ch.name, time.time() - t)

    while True:
        for ch in chans.values():
            try:
                for m in ch.poll():
                    pool.submit(answer, ch, m)
            except Exception as e:
                log.warning("poll %s: %r", ch.name, e)
        try:
            sched.tick()
        except Exception:
            log.exception("scheduler")
        if once:
            pool.shutdown(wait=True)
            return
        time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(*build())

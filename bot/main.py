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
sys.path[:0] = [str(HERE)]
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
    if env.get("BOT_APP", "1") == "1":  # the iPhone app (Expo), through a tunnel; 127.0.0.1 only
        import appapi
        chans["app"] = appapi.App(appapi.load_token(home), port=int(env.get("BOT_APP_PORT", "8770")))
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


def chrome_ok() -> bool:
    """Can the bot's Chrome load a page? (2026-10-22: a locked desktop keyring made every page load hang 30 s.)"""
    import cdp
    t = None
    try:
        t = cdp.Tab(ws_factory=lambda url: cdp.WebSocket(url, timeout=15))
        t.call("Page.navigate", url="http://example.com/")
        return True
    except Exception:
        return False
    finally:
        if t:
            t.close()


class ChromeWatch:
    """Alerts once when Chrome stops loading pages and once when it's back (not every 5 minutes in between)."""

    def __init__(self, check, alert):
        self.check, self.alert, self.ok = check, alert, True

    def tick(self):
        ok = self.check()
        if ok != self.ok:
            self.alert("✅ The bot's Chrome loads pages again." if ok else
                       "⚠ The bot's Chrome isn't loading pages, so Swiggy and logins won't work until it's fixed.")
            self.ok = ok


def serve(chans, bot, sched, once: bool = False, check=chrome_ok, every_s: float = 300):
    pool = ThreadPoolExecutor(4)
    if hasattr(bot, "resume_trackers"):  # orders being tracked before a restart carry on
        bot.resume_trackers(lambda channel, chat, text: chans[channel].send(chat, text) if channel in chans else None)
    busy: dict[str, threading.Lock] = {}
    owner = next((j["deliver"] for j in getattr(sched, "jobs", []) if j.get("deliver")), None)

    def alert(text: str) -> None:
        """Errors reach the owner by themselves (Telegram, where the jobs report), not only when they notice."""
        try:
            if owner:
                C.deliver(chans, owner, text)
        except Exception:
            log.exception("alert failed")

    def answer(ch, m):
        with busy.setdefault(m["chat"], threading.Lock()):  # one turn at a time per chat, in order
            t = time.time()
            try:
                reply = bot.handle(m, notify=lambda text: ch.send(m["chat"], text))
            except Exception as e:
                log.exception("turn failed")
                reply = f"Sorry, something broke on my side ({type(e).__name__}). Try again in a minute."
                alert(f"⚠ bot error on {m['text'][:60]!r}: {e!r}"[:500])
            try:
                if getattr(reply, "cards", None) and hasattr(ch, "send_cards"):
                    ch.send_cards(m["chat"], str(reply), reply.choices, reply.cards)  # the app: photos + taps
                elif getattr(reply, "choices", None) and hasattr(ch, "send_choice"):
                    ch.send_choice(m["chat"], str(reply), reply.choices)  # taps instead of typing
                else:
                    ch.send(m["chat"], reply)
            except Exception:
                log.exception("send failed")
            log.info("turn %s %.1fs", ch.name, time.time() - t)

    watch, next_check = ChromeWatch(check, alert), 0.0

    while True:
        if time.time() >= next_check:
            next_check = time.time() + every_s
            threading.Thread(target=watch.tick, daemon=True).start()
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

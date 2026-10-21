#!/usr/bin/env python3
"""Silent unless a loop has quietly stalled (the bot's version of Hermes's companion-canary.py: same two checks,
read from the bot's job state instead of the Hermes CLI). Any output is the alert; empty output sends nothing.
  1. daily-companion sent something within 36 h (catches the job dying)
  2. LEARNED.md was modified within 8 days (catches weekly-reflect not writing)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def check(state: Path, learned: Path, now: float) -> list[str]:
    out = []
    try:
        sent = json.loads(state.read_text()).get("daily-companion", {}).get("sent", [])
        last = datetime.fromisoformat(sent[-1]["at"]).replace(tzinfo=timezone.utc).timestamp() if sent else 0
    except (OSError, ValueError, KeyError):
        last = 0
    if now - last > 36 * 3600:
        out.append("⚠ daily-companion has not sent anything in 36 h: the job may be dead.")
    if not learned.exists() or now - learned.stat().st_mtime > 8 * 86400:
        out.append("⚠ LEARNED.md not updated in 8 days: weekly-reflect may not be writing.")
    return out


if __name__ == "__main__":
    home = Path(os.environ.get("BOT_HOME", Path.home() / ".bot"))
    hermes = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    msgs = check(home / "jobs-state.json", hermes / "memories" / "LEARNED.md", time.time())
    print("\n".join(msgs))
    sys.exit(0)

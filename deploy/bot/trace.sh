#!/bin/bash
# The bot's last N turns: message, reply, time, and every step (model calls with schema/time/output, errors).
#   trace.sh [N]      (on the server, as root)
N=${1:-5}
sudo -u hermes python3 - "$N" <<'P'
import json, sqlite3, sys, time
c = sqlite3.connect('/home/hermes/.bot/bot.db')
for ts, chat, text, reply, ms, steps in c.execute("SELECT * FROM traces ORDER BY ts DESC LIMIT ?", (int(sys.argv[1]),)).fetchall()[::-1]:
    print(f"\n{time.strftime('%m-%d %H:%M:%S', time.gmtime(ts))}  {ms / 1000:.1f}s  {text!r}\n  -> {str(reply)[:300]!r}")
    for s in json.loads(steps):
        print("   ", json.dumps(s, ensure_ascii=False)[:400])
P
ls -t /home/hermes/.bot/captures 2>/dev/null | head -5

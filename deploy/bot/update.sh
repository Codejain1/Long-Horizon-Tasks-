#!/bin/bash
# New bot code, live: unpack into a fresh /opt/bot-<stamp>, point /opt/bot at it, restart only the bot.
#   update.sh <bundle-url> <md5>        undo: ln -sfn <previous /opt/bot-*> /opt/bot && systemctl restart owner-bot
set -euo pipefail
URL=$1; MD5=$2; NEW=/opt/bot-$(date +%Y%m%d-%H%M%S); OLD=$(readlink /opt/bot || true)
mkdir -p $NEW && cd $NEW
curl -s -o b.tgz "$URL"; echo "$MD5  b.tgz" | md5sum -c; tar xzf b.tgz; rm b.tgz
python3 -m py_compile bot/*.py
[ -f "$OLD/SWITCHED_AT" ] && cp "$OLD/SWITCHED_AT" $NEW/
ln -sfn $NEW /opt/bot && systemctl restart owner-bot; sleep 5
systemctl is-active owner-bot; echo "now $NEW (was $OLD)"
journalctl -u owner-bot --since "-20s" --no-pager | grep -i -E "error|traceback" | tail -5 || true

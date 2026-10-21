#!/bin/bash
# Switch the Hermes server from the Hermes gateway to our bot. Run as root (over SSM) with the bundle's URL and md5:
#   switch.sh <bundle-url> <md5>
# Keeps: the WhatsApp session (no re-pairing), memrouter, the desktop Chrome, ~/.hermes (jobs, scripts, memories, .env).
# Undo: deploy/bot/rollback.sh (stops the bot, restores the scripts, starts hermes-gateway).
set -euo pipefail
URL=$1; MD5=$2
H=/home/hermes/.hermes; ST=$(date +%Y%m%d-%H%M%S); NEW=/opt/bot-$ST

mkdir -p $NEW && cd $NEW
curl -s -o b.tgz "$URL"; echo "$MD5  b.tgz" | md5sum -c; tar xzf b.tgz; rm b.tgz
python3 -m py_compile bot/*.py hermes/embed_memory/booking.py hermes/embed_memory/places.py
ln -sfn $NEW /opt/bot
[ -d /opt/bot-bridge ] || cp -a $H/hermes-agent/scripts/whatsapp-bridge /opt/bot-bridge  # independent of Hermes

cp -a $H/scripts $H/scripts.before-bot-$ST                     # the infra checks watched hermes-gateway
sed -i 's/hermes-gateway/owner-bot owner-whatsapp-bridge/' $H/scripts/infra-monitor-source.sh $H/scripts/infra-health-digest.sh
install -o hermes -g hermes -m 755 bot/companion_canary.py $H/scripts/companion-canary.py
echo "$ST" > /opt/bot/SWITCHED_AT

install -m 644 deploy/bot/owner-whatsapp-bridge.service deploy/bot/owner-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl disable --now hermes-gateway                              # also stops Hermes's own bridge and cron
sleep 3
systemctl enable --now owner-whatsapp-bridge; sleep 15
systemctl enable --now owner-bot; sleep 8

echo "== status"; systemctl is-active owner-whatsapp-bridge owner-bot hermes-gateway || true
curl -s -m 5 http://127.0.0.1:3000/health; echo
journalctl -u owner-bot --since "-1min" --no-pager | tail -8
echo "scripts backup: $H/scripts.before-bot-$ST"

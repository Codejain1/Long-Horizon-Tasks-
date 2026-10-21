#!/bin/bash
# Back to Hermes: stop our bot and its bridge, restore the scripts from before the switch, start hermes-gateway.
set -uo pipefail
H=/home/hermes/.hermes
systemctl disable --now owner-bot owner-whatsapp-bridge
ST=$(cat /opt/bot/SWITCHED_AT 2>/dev/null)
[ -n "$ST" ] && [ -d $H/scripts.before-bot-$ST ] && rm -rf $H/scripts && cp -a $H/scripts.before-bot-$ST $H/scripts
systemctl enable --now hermes-gateway; sleep 25
systemctl is-active hermes-gateway; curl -s -m 5 http://127.0.0.1:3000/health; echo

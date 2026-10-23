#!/bin/bash
# One-time: the bot's home connection on the server (the proxy and the second Chrome). Run as root after a deploy.
# The home side is home/run.sh on a machine at the owner's home.
set -euo pipefail
install -m 644 /opt/bot/deploy/bot/owner-homeproxy.service /opt/bot/deploy/bot/owner-home-chrome.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now owner-homeproxy owner-home-chrome
sleep 6; systemctl is-active owner-homeproxy owner-home-chrome
curl -s -m 5 http://127.0.0.1:8773/status; echo
curl -s -m 5 http://127.0.0.1:9223/json/version | head -3

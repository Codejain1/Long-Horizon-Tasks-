#!/usr/bin/env bash
# Install our embedding memory on the Hermes server. Run as root from the unpacked bundle directory:
#   sudo bash install.sh
# Backs up Hermes's config and memories first. Does NOT switch Hermes's memory provider: that is one line in
# ~/.hermes/config.yaml (memory: provider: embed_memory), done by hand after checking the config, then
# `systemctl restart hermes-gateway`.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
HH=/home/hermes/.hermes
APP=/opt/embed-memory
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$HH/backup-$STAMP"
cp -a "$HH/config.yaml" "$HH/backup-$STAMP/"
[ -d "$HH/memories" ] && cp -a "$HH/memories" "$HH/backup-$STAMP/"
chown -R hermes:hermes "$HH/backup-$STAMP"
echo "backup: $HH/backup-$STAMP"

apt-get install -y -q python3-venv >/dev/null
mkdir -p "$APP"
cp -a "$HERE/memory_service.py" "$HERE/fast_recall.py" "$HERE/learned_recall.py" "$HERE/dates.py" "$HERE/ranker.pkl" "$APP/"
rm -rf "$APP/model" && cp -a "$HERE/model" "$APP/model"
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --index-url https://download.pytorch.org/whl/cpu torch
"$APP/venv/bin/pip" install -q transformers numpy
chown -R hermes:hermes "$APP"

cat > /etc/systemd/system/embed-memory.service <<EOF
[Unit]
Description=Embed memory for Hermes (fine-tuned bge-small + learned ranker, 127.0.0.1:8765)
After=network.target

[Service]
User=hermes
ExecStart=$APP/venv/bin/python $APP/memory_service.py --home /home/hermes/.embed-memory --model $APP/model --ranker $APP/ranker.pkl
Restart=on-failure
Environment=OMP_NUM_THREADS=2

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now embed-memory

mkdir -p "$HH/plugins"
rm -rf "$HH/plugins/embed_memory" && cp -a "$HERE/embed_memory" "$HH/plugins/embed_memory"
chown -R hermes:hermes "$HH/plugins"

for _ in $(seq 60); do curl -sf 127.0.0.1:8765/health && break; sleep 2; done
echo; echo "installed. Next: set 'memory: provider: embed_memory' in $HH/config.yaml, restart hermes-gateway."

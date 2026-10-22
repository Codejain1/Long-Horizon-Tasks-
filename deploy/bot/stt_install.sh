#!/bin/bash
# One-time: the speech-to-text service for the app's hold-to-talk. Run as root on the server.
set -euo pipefail
[ -x /opt/stt-venv/bin/python ] || python3 -m venv /opt/stt-venv
/opt/stt-venv/bin/pip install -q --upgrade pip
/opt/stt-venv/bin/pip install -q "faster-whisper==1.*"
chown -R hermes:hermes /opt/stt-venv
# download the model once (as the service user, into its cache)
sudo -u hermes HF_HOME=/home/hermes/.cache/huggingface /opt/stt-venv/bin/python -c \
  "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('model ready')"
install -m 644 /opt/bot/deploy/bot/owner-stt.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now owner-stt
sleep 8; systemctl is-active owner-stt; curl -s -m 5 http://127.0.0.1:8790/health; echo

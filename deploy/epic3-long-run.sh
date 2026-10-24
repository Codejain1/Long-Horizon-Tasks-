#!/bin/bash
# Hermes (hermes user): longer rental ticket bench. Upload via ssm.sh; allow 2–3h.
set -euo pipefail
cd /home/hermes/epic3
MODEL=http://127.0.0.1:8000/v1
OUT=/home/hermes/epic3-long
mkdir -p "$OUT"
.venv/bin/python bench/preflight.py --model-url "$MODEL" --scenario rental | tee "$OUT/preflight.log"
.venv/bin/python bench/tickets.py run --scenario rental --tickets 1-3 --arms plain,runner,mem \
  --reps 2 --streams 1 --out "$OUT" --model-url "$MODEL" --max-minutes 20 | tee "$OUT/tickets.log"
.venv/bin/python bench/tickets.py report --out "$OUT" | tee "$OUT/report.txt"

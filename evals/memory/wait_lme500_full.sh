#!/usr/bin/env bash
# After horizon+rag finish, run full arm (serial). Usage: nohup ./wait_lme500_full.sh &
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${OUT:-/tmp/lme500}"
PY="${PY:-$ROOT/.venv/bin/python}"
export PYTHONPATH="$ROOT/src"

while pgrep -f "longmemeval.py --arm (horizon|rag)" >/dev/null 2>&1; do
  sleep 60
done
echo "$(date '+%Y-%m-%dT%H:%M:%S') starting full arm" >>"$OUT/run.log"
exec "$PY" "$ROOT/evals/memory/longmemeval.py" --backend openai --arm full --n 500 --out "$OUT" --workers 1 \
  >>"$OUT/full.log" 2>&1

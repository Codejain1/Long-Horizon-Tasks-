#!/usr/bin/env bash
# Full LongMemEval-S (500) via OpenAI. Resumes from jsonl in OUT.
# Detached run: screen -dmS lme500 bash -c 'cd REPO && OUT=/tmp/lme500 ./evals/memory/run_lme500_openai.sh'
# Monitor: tail -f /tmp/lme500/horizon.log; wc -l /tmp/lme500/*.jsonl
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${OUT:-/tmp/lme500}"
PY="${PY:-$ROOT/.venv/bin/python}"
export PYTHONPATH="$ROOT/src"
mkdir -p "$OUT"
BACKEND="${BACKEND:-auto}"
COMMON=("$PY" "$ROOT/evals/memory/longmemeval.py" --backend "$BACKEND" --n 500 --out "$OUT")

echo "=== LongMemEval-S 500 | backend=$BACKEND | out=$OUT ===" | tee "$OUT/run.log"

# Serial; 2 workers on Gemini is usually fine (raise if you hit 429).
WORKERS="${WORKERS:-1}"
"${COMMON[@]}" --arm horizon --workers "$WORKERS" >>"$OUT/horizon.log" 2>&1
"${COMMON[@]}" --arm rag --workers "$WORKERS" >>"$OUT/rag.log" 2>&1

# Full context: one question ~120k tokens — keep serial.
"${COMMON[@]}" --arm full --workers 1 >>"$OUT/full.log" 2>&1

echo "=== done ===" | tee -a "$OUT/run.log"
for arm in horizon rag full; do
  "$PY" "$ROOT/evals/memory/longmemeval.py" --arm "$arm" --n 500 --out "$OUT" --report-only || true
done

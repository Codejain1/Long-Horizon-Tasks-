#!/usr/bin/env bash
# FactConsolidation only (resume-safe). Same env as run_all_cursor.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
export PYTHONPATH="$ROOT/src"
MODEL="${MODEL:-"composer-2.5"}"
OUT="${OUT:-/tmp/mem-eval-cursor}"
FC_N="${FC_N:-50}"
WORKERS="${WORKERS:-1}"
mkdir -p "$OUT/factconsolidation"
FC=("$PY" "$ROOT/evals/memory/factconsolidation.py" --backend cursor --model "$MODEL")
for arm in horizon rag full; do
  echo "=== FactConsolidation $arm ==="
  "${FC[@]}" --arm "$arm" --splits sh_6k mh_6k --n "$FC_N" --out "$OUT/factconsolidation" --workers "$WORKERS" \
    2>&1 | tee -a "$OUT/factconsolidation-$arm.log"
done
echo "=== done ==="

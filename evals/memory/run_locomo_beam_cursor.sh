#!/usr/bin/env bash
# LoCoMo + BEAM only (resume-safe).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
export PYTHONPATH="$ROOT/src"
MODEL="${MODEL:-"composer-2.5"}"
OUT="${OUT:-/tmp/mem-eval-locomo-beam}"
LOCOMO_N="${LOCOMO_N:-80}"
BEAM_N="${BEAM_N:-40}"
WORKERS="${WORKERS:-1}"
mkdir -p "$OUT/locomo" "$OUT/beam"
LOCOMO=("$PY" "$ROOT/evals/memory/locomo.py" --backend cursor --model "$MODEL")
BEAM=("$PY" "$ROOT/evals/memory/beam.py" --backend cursor --model "$MODEL" --judge "$MODEL")
for arm in horizon rag full; do
  echo "=== LoCoMo $arm ==="
  "${LOCOMO[@]}" --arm "$arm" --n "$LOCOMO_N" --out "$OUT/locomo" --workers "$WORKERS" \
    2>&1 | tee -a "$OUT/locomo-$arm.log"
  echo "=== BEAM $arm ==="
  "${BEAM[@]}" --arm "$arm" --n "$BEAM_N" --out "$OUT/beam" --workers "$WORKERS" \
    2>&1 | tee -a "$OUT/beam-$arm.log"
done
for arm in horizon rag full; do
  "${LOCOMO[@]}" --arm "$arm" --n "$LOCOMO_N" --out "$OUT/locomo" --report-only
  "${BEAM[@]}" --arm "$arm" --n "$BEAM_N" --out "$OUT/beam" --report-only
done

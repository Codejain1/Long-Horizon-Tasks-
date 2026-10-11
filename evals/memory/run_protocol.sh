#!/usr/bin/env bash
# The fair memory protocol (docs/MEMORY_BENCHMARKS.md): every arm (horizon, hybrid, rag, full) answers with the
# same Cursor model and is graded by Codex, LongMemEval on its held-out test split. Each arm runs twice, so the
# second pass retries questions that errored; compare.py then scores only questions every arm answered.
# One benchmark at a time: each question re-embeds its whole history, and parallel benchmarks starved the CPU
# (load 20 on 8 cores), which also skews the retrieval latencies reported. Resume-safe: rerun the same command.
#
#   OUT=/tmp/mem-eval-v3 ./evals/memory/run_protocol.sh
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY=$ROOT/.venv/bin/python
export PYTHONPATH=$ROOT/src
OUT=${OUT:-/tmp/mem-eval-v3}
MODEL=${MODEL:-composer-2.5}
W=${WORKERS:-3}
COMMON=(--backend cursor --model "$MODEL" --judge-backend codex --workers "$W")
mkdir -p "$OUT"

bench() {  # name, then the runner's arguments; output to $OUT/<name>-<arm>.log
  local name=$1; shift
  for arm in horizon hybrid rag full; do
    for pass in 1 2; do
      "$PY" "$@" --arm "$arm" 2>&1 | grep --line-buffered -v -E "Warning|Fetching|it/s" >> "$OUT/$name-$arm.log"
    done
  done
}

bench locomo "$ROOT/evals/memory/locomo.py" --n "${LOCOMO_N:-150}" --out "$OUT/locomo" "${COMMON[@]}"
bench beam "$ROOT/evals/memory/beam.py" --n "${BEAM_N:-80}" --out "$OUT/beam" "${COMMON[@]}"
bench lme "$ROOT/evals/memory/longmemeval.py" --split test --n "${LME_N:-100}" --out "$OUT/lme" "${COMMON[@]}"
# FactConsolidation is graded by substring match, no judge.
bench fc "$ROOT/evals/memory/factconsolidation.py" --splits sh_6k mh_6k --n "${FC_N:-50}" --out "$OUT/fc" \
  --backend cursor --model "$MODEL" --workers "$W"
"$PY" "$ROOT/evals/memory/compare.py" "$OUT/locomo" "$OUT/beam" "$OUT/lme" "$OUT/fc:-sh_6k" "$OUT/fc:-mh_6k" \
  | tee "$OUT/compare.txt"
echo "ALL DONE" >> "$OUT/compare.txt"

#!/usr/bin/env bash
# Fresh memory benchmark suite using Cursor Agent (Composer / Grok), not Claude Code CLI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
export PYTHONPATH="$ROOT/src"
MODEL="${MODEL:-"composer-2.5"}"
OUT="${OUT:-/tmp/mem-eval-cursor}"
LME_N="${LME_N:-60}"       # 60 = stratified LongMemEval-S slice; 500 = full (days at ~30s/call)
FC_N="${FC_N:-50}"         # MemoryAgentBench FactConsolidation per split
LOCOMO_N="${LOCOMO_N:-80}" # stratified LoCoMo QA sample (full set ~1986)
BEAM_N="${BEAM_N:-40}"     # BEAM probing questions (from 100K split)
WORKERS="${WORKERS:-1}"
SKIP_LME_FULL="${SKIP_LME_FULL:-0}"  # 1 = skip LongMemEval full-context arm (often fails on Cursor CLI)

mkdir -p "$OUT" "$OUT/factconsolidation" "$OUT/locomo" "$OUT/beam"
printf '%s\n' "{\"backend\":\"cursor\",\"model\":\"$MODEL\",\"lme_n\":$LME_N,\"fc_n\":$FC_N,\"locomo_n\":$LOCOMO_N,\"beam_n\":$BEAM_N}" >"$OUT/meta.json"
echo "model=$MODEL out=$OUT lme_n=$LME_N" | tee "$OUT/run.log"

COMMON=("$PY" "$ROOT/evals/memory/longmemeval.py" --backend cursor --model "$MODEL" --judge "$MODEL")
for arm in horizon rag full; do
  if [[ "$arm" == "full" && "$SKIP_LME_FULL" == "1" ]]; then
    echo "=== LongMemEval full (skipped, SKIP_LME_FULL=1) ===" | tee -a "$OUT/run.log"
    continue
  fi
  echo "=== LongMemEval $arm ===" | tee -a "$OUT/run.log"
  "${COMMON[@]}" --arm "$arm" --n "$LME_N" --out "$OUT" --workers "$WORKERS" \
    2>&1 | tee -a "$OUT/longmemeval-$arm.log"
done

FC=("$PY" "$ROOT/evals/memory/factconsolidation.py" --backend cursor --model "$MODEL")
for arm in horizon rag full; do
  echo "=== FactConsolidation $arm ===" | tee -a "$OUT/run.log"
  "${FC[@]}" --arm "$arm" --splits sh_6k mh_6k --n "$FC_N" --out "$OUT/factconsolidation" --workers "$WORKERS" \
    2>&1 | tee -a "$OUT/factconsolidation-$arm.log"
done

LOCOMO=("$PY" "$ROOT/evals/memory/locomo.py" --backend cursor --model "$MODEL")
BEAM=("$PY" "$ROOT/evals/memory/beam.py" --backend cursor --model "$MODEL" --judge "$MODEL")
for arm in horizon rag full; do
  echo "=== LoCoMo $arm ===" | tee -a "$OUT/run.log"
  "${LOCOMO[@]}" --arm "$arm" --n "$LOCOMO_N" --out "$OUT/locomo" --workers "$WORKERS" \
    2>&1 | tee -a "$OUT/locomo-$arm.log"
  echo "=== BEAM $arm ===" | tee -a "$OUT/run.log"
  "${BEAM[@]}" --arm "$arm" --n "$BEAM_N" --out "$OUT/beam" --workers "$WORKERS" \
    2>&1 | tee -a "$OUT/beam-$arm.log"
done

echo "=== STATE-Bench / DolphinBench (optional) ===" | tee -a "$OUT/run.log"
"$PY" "$ROOT/evals/memory/state_bench.py" --allow-skip --out "$OUT/state_bench" \
  2>&1 | tee -a "$OUT/state_bench.log" || true
"$PY" "$ROOT/evals/memory/dolphinbench.py" --smoke --allow-skip --out "$OUT/dolphinbench" \
  2>&1 | tee -a "$OUT/dolphinbench.log" || true
if [[ "${RUN_STATE_BENCH:-0}" == "1" ]]; then
  "$PY" "$ROOT/evals/memory/state_bench.py" --allow-skip --domain "${STATE_DOMAIN:-travel}" \
    --out "$OUT/state_bench" 2>&1 | tee -a "$OUT/state_bench-run.log" || true
fi

echo "=== reports ===" | tee -a "$OUT/run.log"
for arm in horizon rag full; do
  "${COMMON[@]}" --arm "$arm" --n "$LME_N" --out "$OUT" --report-only | tee -a "$OUT/run.log"
  "${LOCOMO[@]}" --arm "$arm" --n "$LOCOMO_N" --out "$OUT/locomo" --report-only | tee -a "$OUT/run.log"
  "${BEAM[@]}" --arm "$arm" --n "$BEAM_N" --out "$OUT/beam" --report-only | tee -a "$OUT/run.log"
done
"$PY" "$ROOT/evals/memory/score_industry.py" --out "$OUT" 2>&1 | tee -a "$OUT/run.log" || true

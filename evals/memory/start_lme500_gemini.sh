#!/usr/bin/env bash
# Detached LongMemEval-S 500 on Gemini (reads GEMINI_API_KEY from repo .env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${OUT:-/tmp/lme500-gemini}"
mkdir -p "$OUT"
export PYTHONPATH="$ROOT/src" PYTHONUNBUFFERED=1 OUT BACKEND="${BACKEND:-auto}" WORKERS="${WORKERS:-1}"
if pgrep -f "longmemeval.py.*${OUT}" >/dev/null; then
  echo "already running (pgrep longmemeval ${OUT})"
  exit 0
fi
nohup "$ROOT/evals/memory/run_lme500_openai.sh" >>"$OUT/nohup.log" 2>&1 &
echo "started pid=$! log=$OUT/nohup.log"

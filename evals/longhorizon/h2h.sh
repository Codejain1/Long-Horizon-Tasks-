#!/bin/sh
# Head-to-head: notes file and MCP memory server vs Horizon (ward + rental).
# Runs one job at a time so four parallel Claude sessions don't share one usage limit.
#
#   evals/longhorizon/h2h.sh              # r1 then r2 (fresh /tmp/h2h)
#   evals/longhorizon/h2h.sh r2-only      # only batch r2 (after limit reset)
#
# Score:  python evals/longhorizon/score_h2h.py /tmp/h2h

set -e
cd "$(dirname "$0")/../.."
REPO_DENY="$HOME/Desktop/Long-Horizon-Tasks"
PY=.venv/bin/python
RUN=/tmp/h2h

run_one() {
  batch=$1
  arm=$2
  scenario=$3
  out=$RUN/$batch/$arm
  mkdir -p "$out"
  log=/tmp/h2h-$batch-$arm-$scenario.log
  echo "== $batch $scenario $arm -> $log"
  $PY evals/longhorizon/run.py --scenario "$scenario" --arm "$arm" --out "$out" \
    --deny-read "$RUN" --deny-read "$REPO_DENY" >"$log" 2>&1
  if grep -q ABORTED "$log"; then
    echo "ABORTED: $log"
    return 1
  fi
}

batch() {
  b=$1
  for arm in notes mcpmem; do
    for s in ward rental; do
      run_one "$b" "$arm" "$s" || exit 1
    done
  done
}

case "${1:-}" in
  r2-only)
    mkdir -p "$RUN/r2"
    batch r2
    ;;
  "")
    rm -rf "$RUN"
    mkdir -p "$RUN"
    batch r1
    batch r2
    ;;
  *)
    echo "usage: $0 [r2-only]" >&2
    exit 2
    ;;
esac
echo "head-to-head done; score with: python evals/longhorizon/score_h2h.py $RUN"

#!/bin/sh
# Decision layer gate: boxoffice + lessons, lean vs decide (serial; one Claude session at a time).
#
#   evals/longhorizon/decision_gate.sh
#   evals/longhorizon/decision_gate.sh wait   # poll until usage limit clears
#
# Score trap checks from results.json rule_checks hidden tests on the last session.

set -e
cd "$(dirname "$0")/../.."
REPO_DENY="$HOME/Desktop/Long-Horizon-Tasks"
PY=.venv/bin/python
RUN=/tmp/decision-gate

wait_limit() {
  while $PY -c "import subprocess,sys; r=subprocess.run(['claude','-p','echo ok','--max-turns','1'],
        capture_output=True,text=True); sys.exit(0 if 'session limit' not in r.stdout.lower() else 1)"; do
    echo "$(date '+%F %T') Claude limited, sleep 300s"
    sleep 300
  done
}

run_one() {
  scenario=$1
  arm=$2
  out=$RUN/$scenario-$arm
  mkdir -p "$(dirname "$out")"
  log=/tmp/decision-gate-$scenario-$arm.log
  echo "== $scenario / $arm -> $log"
  $PY evals/longhorizon/run.py --scenario "$scenario" --arm "$arm" --out "$out" \
    --deny-read "$RUN" --deny-read "$REPO_DENY" >"$log" 2>&1 || true
  grep -q ABORTED "$log" && echo "ABORTED $log" && return 1
  return 0
}

[ "${1:-}" = wait ] && wait_limit

rm -rf "$RUN" && mkdir -p "$RUN"
for scenario in boxoffice lessons; do
  for arm in lean decide; do
    run_one "$scenario" "$arm" || exit 1
  done
done
echo "done; compare with: python evals/longhorizon/report.py $RUN"

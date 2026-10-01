#!/bin/sh
# Live side-by-side: the same 12-ticket RentEase backlog, one fresh agent session per ticket, with and without
# Horizon. Run one side per terminal, at the same time:
#
#   demo/sidebyside/run.sh without     # the agent alone
#   demo/sidebyside/run.sh with        # the same agent with Horizon (lean; Jev picks out rules if a key is set)
#   demo/sidebyside/scoreboard.py      # when both are done (or any time, for the tickets so far)
#
# AGENT=claude|cursor (default claude), MODEL (default sonnet for claude), OUT (default /tmp/horizon-demo).
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${OUT:-/tmp/horizon-demo}
AGENT=${AGENT:-claude}
case "$1" in
  without) arm=baseline ;;
  with) arm=lean; export HORIZON_SCORER=${HORIZON_SCORER:-jev} ;;
  *) echo "usage: $0 without|with" >&2; exit 2 ;;
esac
if [ -e "$OUT/$1" ]; then echo "$OUT/$1 exists: remove it or set OUT=… for a fresh run" >&2; exit 1; fi
model=""; [ -n "$MODEL" ] && model="--model $MODEL"
echo "== $1 Horizon: $AGENT, 12 tickets, one fresh session each. Each line is one finished ticket."
# The read guard keeps each side out of the other's folder and out of the grading (this repo).
exec "$ROOT/.venv/bin/python" "$ROOT/evals/longhorizon/run.py" --scenario rental --arm "$arm" --agent "$AGENT" \
  --out "$OUT/$1" $model --deny-read "$OUT" --deny-read "$HOME/Desktop/Long-Horizon-Tasks"

#!/bin/sh
# Wait until Claude Code accepts a prompt, then run head-to-head batch r2 (serial).
# Keeps r1 under /tmp/h2h intact.
#
#   evals/longhorizon/h2h_wait_r2.sh >> /tmp/h2h-r2-serial.log 2>&1 &

set -e
cd "$(dirname "$0")/../.."
LOG=/tmp/h2h-r2-serial.log
now() { date '+%Y-%m-%dT%H:%M:%S%z'; }
echo "$(now) waiting for Claude usage limit to reset" | tee -a "$LOG"
while claude -p 'echo ok' --max-turns 1 2>&1 | grep -qi 'session limit'; do
  echo "$(now) still limited, sleep 300s" >>"$LOG"
  sleep 300
done
echo "$(now) limit clear, starting r2-only" | tee -a "$LOG"
exec evals/longhorizon/h2h.sh r2-only 2>&1 | tee -a "$LOG"

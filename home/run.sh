#!/bin/bash
# The bot's home connection, on a machine at home (the owner's Mac for now; an always-on machine later runs the same):
# a tunnel to the server (SSM, reopened when it drops) and the helper that opens the listed sites from here.
# Needs `aws login` first. Keeps the Mac awake while plugged in (caffeinate); on battery it may still sleep.
#   bash home/run.sh            stop: Ctrl-C
cd "$(dirname "$0")"
python3 agent.py &
AGENT=$!
trap 'kill $AGENT $TUNNEL 2>/dev/null; exit' INT TERM EXIT
while true; do
  caffeinate -is aws ssm start-session --region ap-south-1 --target i-0d0e2f600c143e705 \
    --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["8772"],"localPortNumber":["8772"]}' > /tmp/home-tunnel.log 2>&1 &
  TUNNEL=$!
  wait $TUNNEL
  echo "$(date '+%H:%M') home tunnel closed; reopening in 10 s (if it keeps closing: aws login)" >&2
  sleep 10
done

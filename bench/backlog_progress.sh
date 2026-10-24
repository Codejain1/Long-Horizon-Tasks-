#!/usr/bin/env bash
# Progress of backlog runs on the Hermes server:  bash bench/backlog_progress.sh <run-dir>... [report]
# Each dir: tickets done per arm, the last ticket's hidden score, errors in the arm logs; DONE when finished.
R=ap-south-1; I=i-0d0e2f600c143e705; REPORT=""; DIRS=()
for a in "$@"; do [ "$a" = report ] && REPORT=1 || DIRS+=("$a"); done
CMD=""
for D in "${DIRS[@]}"; do
  CMD+="echo == $D; cd /home/hermes/$D 2>/dev/null && for f in *.jsonl; do [ -f \"\$f\" ] && echo \"\${f%.jsonl}: \$(grep -c . \$f) tickets, last \$(tail -1 \$f | python3 -c 'import json,sys;r=json.load(sys.stdin);print(r[\"final\"],\"/\",r[\"total\"],\"fixups\",r[\"fixup_rounds\"])')\"; done; grep -l Traceback *.out 2>/dev/null | sed 's/^/ERROR in /'; grep -q 'BENCH DONE' /home/hermes/$D.log && echo DONE || echo running; "
  [ -n "$REPORT" ] && CMD+="cd /opt/longrun/v3b && /opt/longrun/venv/bin/python bench/backlog.py report --out /home/hermes/$D; "
done
P=$(python3 -c 'import json,sys;print(json.dumps({"commands":[sys.argv[1]]}))' "$CMD")
C=$(aws ssm send-command --region $R --instance-ids $I --document-name AWS-RunShellScript --parameters "$P" \
    --query Command.CommandId --output text) || exit 1
sleep 6
aws ssm get-command-invocation --region $R --command-id "$C" --instance-id $I --query StandardOutputContent --output text

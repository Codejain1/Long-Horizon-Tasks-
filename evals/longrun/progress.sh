#!/usr/bin/env bash
# Progress of the fast benchmark on the Hermes server.   bash evals/longrun/progress.sh [run-dir]   (default fastbench2)
# Needs a live `aws login`. Add "report" as a 2nd argument for the full report (rates, curve, confidence interval).
D=${1:-fastbench2}; R=ap-south-1; I=i-0d0e2f600c143e705
if [ "$2" = report ]; then
  CMD="cd /opt/longrun/fb4/evals/longrun && PYTHONPATH=/opt/longrun/fb4/src /opt/longrun/venv/bin/python bench.py report --out /home/hermes/$D"
else
  CMD="cd /home/hermes/$D && for f in *-[0-9].jsonl; do echo \"\${f%.jsonl}: \$(grep -c . \$f)/16 done, solved \$(grep -c 'solved\": true' \$f)\"; done 2>/dev/null; grep -q 'BENCH DONE' /home/hermes/$D.log && echo FINISHED || echo running"
fi
P=$(python3 -c 'import json,sys;print(json.dumps({"commands":[sys.argv[1]]}))' "$CMD")
C=$(aws ssm send-command --region $R --instance-ids $I --document-name AWS-RunShellScript --parameters "$P" \
    --query Command.CommandId --output text) || exit 1
sleep 6
aws ssm get-command-invocation --region $R --command-id "$C" --instance-id $I --query StandardOutputContent --output text

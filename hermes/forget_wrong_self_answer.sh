#!/usr/bin/env bash
# One-off: remove Hermes's saved wrong answer about its memory ("encrypted persistent disk...") and the question that
# produced it from the embed memory (backup first), restart the memory service, then ask Hermes again.
set -euo pipefail
R=ap-south-1; I=i-0d0e2f600c143e705
CID=$(aws ssm send-command --region $R --instance-ids $I --document-name AWS-RunShellScript --timeout-seconds 400 \
  --parameters '{"commands":["echo aW1wb3J0IHNxbGl0ZTMKYyA9IHNxbGl0ZTMuY29ubmVjdCgiL2hvbWUvaGVybWVzLy5lbWJlZC1tZW1vcnkvbWVtb3J5LmRiIikKcSA9ICJ0ZXh0IGxpa2UgJyVlbmNyeXB0ZWQgcGVyc2lzdGVudCBkaXNrJScgb3IgdGV4dCBsaWtlICcld2hhdCBtZW1vcnkgc3lzdGVtIGRvIHlvdSBoYXZlJSciCnJvd3MgPSBjLmV4ZWN1dGUoZiJzZWxlY3QgaWQsIHNwZWFrZXIsIHN1YnN0cih0ZXh0LDEsOTApIGZyb20gdHVybnMgd2hlcmUge3F9IikuZmV0Y2hhbGwoKQpmb3IgciBpbiByb3dzOgogICAgcHJpbnQocikKYy5leGVjdXRlKGYiZGVsZXRlIGZyb20gdHVybnMgd2hlcmUge3F9IikKYy5jb21taXQoKQpwcmludCgiZGVsZXRlZCIsIGxlbihyb3dzKSkK | base64 -d > /tmp/fix.py; cp -a /home/hermes/.embed-memory/memory.db /home/hermes/.embed-memory/memory.db.bak-20261015; sudo -u hermes /opt/embed-memory/venv/bin/python /tmp/fix.py; systemctl restart embed-memory; sleep 20; cd /home/hermes; timeout 240 sudo -u hermes -H /home/hermes/.hermes/hermes-agent/venv/bin/python /home/hermes/.hermes/hermes-agent/hermes chat -q \"In two sentences: what memory system do you have?\" 2>&1 | tail -12"],"executionTimeout":["400"]}' \
  --query Command.CommandId --output text)
while :; do st=$(aws ssm get-command-invocation --region $R --command-id $CID --instance-id $I --query Status --output text 2>/dev/null || echo Pending)
  case $st in Pending|InProgress|Delayed) sleep 5;; *) break;; esac; done
aws ssm get-command-invocation --region $R --command-id $CID --instance-id $I --query StandardOutputContent --output text

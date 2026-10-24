#!/bin/bash
# Run a shell script as root on the Hermes server over SSM and print its status, stdout and stderr.
#   deploy/ssm.sh <script-file> [timeout-seconds]
F=$1; TO=${2:-600}
CMD=$(python3 -c "import json,sys; print(json.dumps({'commands':[open(sys.argv[1]).read()],'executionTimeout':[sys.argv[2]]}))" "$F" "$TO")
ID=$(aws ssm send-command --region ap-south-1 --instance-ids i-0d0e2f600c143e705 --document-name AWS-RunShellScript \
     --parameters "$CMD" --timeout-seconds "$TO" --query Command.CommandId --output text) || exit 1
until s=$(aws ssm get-command-invocation --region ap-south-1 --instance-id i-0d0e2f600c143e705 --command-id "$ID" \
          --query Status --output text 2>/dev/null); [ -n "$s" ] && [ "$s" != InProgress ] && [ "$s" != Pending ]; do sleep 3; done
aws ssm get-command-invocation --region ap-south-1 --instance-id i-0d0e2f600c143e705 --command-id "$ID" \
    --query '[Status,StandardOutputContent,StandardErrorContent]' --output text

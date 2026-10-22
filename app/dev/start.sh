#!/bin/bash
# Start everything the iPhone app needs while we build it (run from the repo root, after `aws login`):
#   1. a tunnel from this Mac's 127.0.0.1:8770 to the bot's app API on the server (SSM, nothing public)
#   2. the relay so the iPhone on the same Wi-Fi reaches it (192.168.x.x:8771)
#   3. Expo: scan the QR code with the iPhone camera, it opens in Expo Go
cd "$(dirname "$0")/.."
aws ssm start-session --region ap-south-1 --target i-0d0e2f600c143e705 --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8770"],"localPortNumber":["8770"]}' > /tmp/assistant-tunnel.log 2>&1 &
TUNNEL=$!
python3 dev/relay.py 8771 8770 > /tmp/assistant-relay.log 2>&1 &
RELAY=$!
trap 'kill $TUNNEL $RELAY 2>/dev/null' EXIT
sleep 4
curl -s -m 5 http://127.0.0.1:8770/health && echo "  <- bot reachable" || echo "bot NOT reachable: check /tmp/assistant-tunnel.log (aws login?)"
npx expo start --lan

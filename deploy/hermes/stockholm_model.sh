#!/usr/bin/env bash
# Hermes's brain on a GPU in Stockholm, reached from the Mumbai server through an SSH tunnel (option A).
#
#   bash deploy/hermes/stockholm_model.sh up      # build it (~20-30 min); starts billing: g6.xlarge $0.85/h,
#                                                 # else g5.xlarge $1.07, g6e.xlarge $1.97, g4dn.xlarge $0.56
#   bash deploy/hermes/stockholm_model.sh switch  # point Hermes at the local model (backs up config first)
#   bash deploy/hermes/stockholm_model.sh revert  # put Hermes back on OpenRouter
#   bash deploy/hermes/stockholm_model.sh stop    # stop the GPU server (billing stops; disk ~$8/month)
#
# up: (1) SSH key on the Mumbai server, (2) fixed IP for Mumbai, (3) Stockholm firewall: SSH only from that IP,
# nothing else open, (4) g6.xlarge with vLLM serving the model on 127.0.0.1:8000 (never public), (5) a tunnel
# service on Mumbai so Hermes sees the model at 127.0.0.1:8000, (6) a test call.
set -euo pipefail
M=ap-south-1; S=eu-north-1; I=i-0d0e2f600c143e705
AMI=ami-01e6261616da3e7c5          # AWS Deep Learning Base OSS Nvidia Driver GPU AMI, Ubuntu 22.04 (eu-north-1)
VPC=vpc-03f78a6e48d65ea0a
SUBNETS="subnet-0c41efbff874d8aa9 subnet-052cc1af92920b4ca"
MODEL=Qwen/Qwen3-14B-AWQ
TYPES="g6.xlarge g5.xlarge g6e.xlarge g4dn.xlarge"   # owner-approved, tried in this order
STATE=$HOME/horizon-evals/stockholm; mkdir -p "$STATE"

ssm() {  # run stdin as a shell script on the Mumbai server, print its output
  local cid st
  cid=$(aws ssm send-command --region $M --instance-ids $I --document-name AWS-RunShellScript \
    --parameters "$(python3 -c 'import json,sys;print(json.dumps({"commands":[sys.stdin.read()],"executionTimeout":["1800"]}))')" \
    --query Command.CommandId --output text)
  while :; do
    st=$(aws ssm get-command-invocation --region $M --command-id "$cid" --instance-id $I --query Status --output text 2>/dev/null || echo Pending)
    case $st in Pending|InProgress|Delayed) sleep 5;; *) break;; esac
  done
  aws ssm get-command-invocation --region $M --command-id "$cid" --instance-id $I --query StandardOutputContent --output text
}

up() {
  echo "1/6 tunnel key on Mumbai"
  PUB=$(ssm <<'EOF'
[ -f /root/.ssh/hermes_model ] || ssh-keygen -q -t ed25519 -N "" -C hermes-mumbai-tunnel -f /root/.ssh/hermes_model
cat /root/.ssh/hermes_model.pub
EOF
)
  echo "2/6 fixed IP for Mumbai"
  if [ ! -s "$STATE/eip" ]; then
    ALLOC=$(aws ec2 allocate-address --region $M --domain vpc \
      --tag-specifications 'ResourceType=elastic-ip,Tags=[{Key=Name,Value=hermes-desktop-mumbai}]' --query AllocationId --output text)
    aws ec2 associate-address --region $M --instance-id $I --allocation-id "$ALLOC" >/dev/null
    aws ec2 describe-addresses --region $M --allocation-ids "$ALLOC" --query 'Addresses[0].PublicIp' --output text > "$STATE/eip"
  fi
  EIP=$(cat "$STATE/eip"); echo "   Mumbai IP $EIP"

  echo "3/6 Stockholm firewall: SSH from $EIP only"
  SG=$(aws ec2 describe-security-groups --region $S --filters Name=group-name,Values=hermes-model Name=vpc-id,Values=$VPC \
       --query 'SecurityGroups[0].GroupId' --output text)
  if [ "$SG" = None ]; then
    SG=$(aws ec2 create-security-group --region $S --vpc-id $VPC --group-name hermes-model \
         --description "Hermes model server: SSH from the Mumbai Hermes server only" --query GroupId --output text)
    aws ec2 authorize-security-group-ingress --region $S --group-id "$SG" --protocol tcp --port 22 --cidr "$EIP/32" >/dev/null
  fi

  echo "4/6 GPU server with vLLM (first type with room: $TYPES)"
  if [ ! -s "$STATE/instance" ]; then
    for type in $TYPES; do
      # Hermes refuses models under 64K context: Qwen3 is stretched 32K -> 64K with YaRN (both key names: vLLM
      # versions differ). On the T4 the 8B model gets the same window.
      case $type in  # the T4 (16 GB, no bf16) gets the 8B model; the 24-48 GB cards the 14B
        g4dn.*) model=Qwen/Qwen3-8B-AWQ; extra="--dtype half";;
        *) model=$MODEL; extra="";;
      esac
      cat > "$STATE/user-data.sh" <<EOF
#!/bin/bash
set -e
echo "$PUB" >> /home/ubuntu/.ssh/authorized_keys
apt-get update -q && apt-get install -y -q python3-venv
python3 -m venv /opt/vllm && /opt/vllm/bin/pip install -q vllm ninja
mkdir -p /opt/hf && chown ubuntu:ubuntu /opt/hf
cat > /etc/systemd/system/vllm.service <<U
[Unit]
Description=vLLM serving $model on 127.0.0.1:8000 (reached only through the SSH tunnel)
After=network-online.target
[Service]
User=ubuntu
Environment=HF_HOME=/opt/hf
Environment=PATH=/opt/vllm/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/vllm/bin/vllm serve $model --host 127.0.0.1 --port 8000 --served-model-name local --max-model-len 65536 --hf-overrides '{"rope_scaling":{"rope_type":"yarn","factor":2.0,"original_max_position_embeddings":32768,"rope_theta":1000000},"rope_parameters":{"rope_type":"yarn","factor":2.0,"original_max_position_embeddings":32768,"rope_theta":1000000},"max_position_embeddings":65536}' --gpu-memory-utilization 0.93 --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3 $extra
Restart=always
RestartSec=15
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload && systemctl enable --now vllm
EOF
      for sn in any $SUBNETS; do  # "any": let AWS pick the zone (it sometimes has room where named zones don't)
        [ "$sn" = any ] && place=() || place=(--subnet-id "$sn")
        out=$(aws ec2 run-instances --region $S --image-id $AMI --instance-type "$type" ${place[@]+"${place[@]}"} \
          --security-group-ids "$SG" --metadata-options HttpTokens=required \
          --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=100,VolumeType=gp3}' \
          --user-data "file://$STATE/user-data.sh" \
          --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=hermes-model-stockholm},{Key=Project,Value=hermes-desktop}]' \
          --query 'Instances[0].InstanceId' --output text 2>&1) || true
        case "$out" in i-*) echo "$out" > "$STATE/instance"; echo "   launched $type ($model)"; break 2;;
          *) echo "   $type $sn: ${out: -100}";; esac
      done
    done
  fi
  [ -s "$STATE/instance" ] || { echo "no GPU capacity in Stockholm right now; nothing is billing. Try again later."; exit 1; }
  GI=$(cat "$STATE/instance"); aws ec2 wait instance-running --region $S --instance-ids "$GI"
  GIP=$(aws ec2 describe-instances --region $S --instance-ids "$GI" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  echo "   $GI at $GIP"

  echo "5/6 tunnel service on Mumbai"
  ssm <<EOF
cat > /etc/systemd/system/hermes-model-tunnel.service <<U
[Unit]
Description=SSH tunnel: 127.0.0.1:8000 -> Stockholm model server
After=network-online.target
[Service]
ExecStart=/usr/bin/ssh -N -i /root/.ssh/hermes_model -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -L 127.0.0.1:8000:127.0.0.1:8000 ubuntu@$GIP
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload && systemctl enable hermes-model-tunnel && systemctl restart hermes-model-tunnel
EOF

  echo "6/6 waiting for the model to download and load (10-25 min)"
  for k in $(seq 60); do
    if ssm <<<'curl -sf -m 5 127.0.0.1:8000/v1/models' | grep -q '"local"'; then break; fi; sleep 30
  done
  ssm <<'EOF'
t=$(date +%s)
curl -s -m 120 127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"local","messages":[{"role":"user","content":"What is the weather in Pune? Use the tool."}],"tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],"max_tokens":300}' | python3 -c 'import json,sys;m=json.load(sys.stdin)["choices"][0]["message"];print("tool call:",m.get("tool_calls"))'
echo "seconds: $(( $(date +%s) - t ))"
EOF
  echo "up. Next: bash $0 switch"
}

switch() {
  ssm <<'EOF'
C=/home/hermes/.hermes/config.yaml
cp -a $C $C.before-local-model
sed -i '/^model:/,/^[a-z]/ { s#^  default: .*#  default: local#; s#^  provider: .*#  provider: custom#; s#^  base_url: .*#  base_url: http://127.0.0.1:8000/v1# }' $C
sed -n '/^model:/,/^[a-z]/p' $C | head -6
systemctl restart hermes-gateway; sleep 15; systemctl is-active hermes-gateway
EOF
}

revert() {
  ssm <<'EOF'
C=/home/hermes/.hermes/config.yaml
[ -f $C.before-local-model ] && cp -a $C.before-local-model $C && systemctl restart hermes-gateway && echo reverted
EOF
}

stop() { aws ec2 stop-instances --region $S --instance-ids "$(cat "$STATE/instance")" --query 'StoppingInstances[0].CurrentState.Name' --output text; }

"${1:-up}"

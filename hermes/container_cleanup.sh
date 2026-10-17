#!/usr/bin/env bash
# Hermes creates one podman sandbox per session (container_persistent: true) and never removes them.
# Daily (systemd timer hermes-container-cleanup): remove hermes-* containers created more than a day ago.
now=$(date +%s)
for n in $(sudo -u hermes podman ps -a --format '{{.Names}}' | grep '^hermes-'); do
  c=$(sudo -u hermes podman inspect -f '{{.Created.Unix}}' "$n" 2>/dev/null) || continue
  [ $((now - c)) -gt 86400 ] && sudo -u hermes podman rm -f "$n" >/dev/null && echo "removed $n"
done
echo "left: $(sudo -u hermes podman ps -a --format '{{.Names}}' | grep -c '^hermes-')"

#!/usr/bin/env bash
set -euo pipefail

SERVICES=(
  pigion-orchestrator.service
  pigion-web.service
)

run_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found; nothing to uninstall."
  exit 0
fi

for service in "${SERVICES[@]}"; do
  echo "Stopping and disabling $service..."
  run_sudo systemctl disable --now "$service" >/dev/null 2>&1 || true
done

for service in "${SERVICES[@]}"; do
  echo "Removing unit file for $service..."
  run_sudo rm -f "/etc/systemd/system/$service"
done

for service in "${SERVICES[@]}"; do
  echo "Removing enablement symlinks for $service..."
  run_sudo find /etc/systemd/system -xtype l -name "$service" -delete
  run_sudo find /etc/systemd/system -type l -name "$service" -delete
done

run_sudo systemctl daemon-reload
run_sudo systemctl reset-failed "${SERVICES[@]}" >/dev/null 2>&1 || true

echo "Uninstalled Pigion webserver/orchestrator services."

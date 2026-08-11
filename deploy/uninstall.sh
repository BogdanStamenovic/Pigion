#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVICES=(
  pigion-orchestrator.service
  pigion-web.service
)
OS_NAME="$(uname -s)"

run_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

if [ "$OS_NAME" = "Darwin" ]; then
  launch_dir="${HOME}/Library/LaunchAgents"
  for label in com.pigion.orchestrator com.pigion.web; do
    echo "Stopping and removing $label..."
    launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
    rm -f "$launch_dir/$label.plist"
  done
  rm -rf "$PROJECT_ROOT/.pigion-services"
  echo "Uninstalled Pigion webserver/orchestrator launch agents. Repository data was kept."
  exit 0
fi

if [ "$OS_NAME" != "Linux" ]; then
  echo "Unsupported OS for deploy/uninstall.sh: $OS_NAME. On Windows run deploy/uninstall.ps1." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found; no Pigion systemd services were removed." >&2
  exit 1
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

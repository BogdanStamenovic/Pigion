#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Setup script: installs requirements and creates a .env with API_KEY
# Usage: ./deploy/setup.sh

OS_NAME="$(uname -s)"

run_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    command -v sudo >/dev/null 2>&1 || {
      echo "sudo is required to install system packages or system services." >&2
      return 1
    }
    sudo "$@"
  fi
}

install_python() {
  echo "Python 3 is missing; installing it with the detected package manager..."
  if [ "$OS_NAME" = "Darwin" ]; then
    command -v brew >/dev/null 2>&1 || {
      echo "Install Homebrew from https://brew.sh or install Python 3 manually, then rerun setup." >&2
      exit 1
    }
    brew install python
  elif command -v apt-get >/dev/null 2>&1; then
    run_sudo apt-get update
    run_sudo apt-get install -y python3 python3-pip python3-venv
  elif command -v pacman >/dev/null 2>&1; then
    run_sudo pacman -S --needed --noconfirm python python-pip
  elif command -v dnf >/dev/null 2>&1; then
    run_sudo dnf install -y python3 python3-pip
  elif command -v yum >/dev/null 2>&1; then
    run_sudo yum install -y python3 python3-pip
  elif command -v zypper >/dev/null 2>&1; then
    run_sudo zypper --non-interactive install python3 python3-pip
  elif command -v apk >/dev/null 2>&1; then
    run_sudo apk add python3 py3-pip
  else
    echo "No supported package manager found. Install Python 3, pip, and venv support, then rerun setup." >&2
    exit 1
  fi
}

command -v python3 >/dev/null 2>&1 || install_python

PY=python3
APP_DIR="$(pwd)"
APP_USER="${PIGION_APP_USER:-${SUDO_USER:-$USER}}"
APP_GROUP="$(id -gn "$APP_USER")"
APP_HOME=""
if command -v getent >/dev/null 2>&1; then
  APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
fi
if [ -z "$APP_HOME" ]; then
  APP_HOME="$(eval echo "~$APP_USER")"
fi

repair_app_ownership() {
  local paths=()
  for path in .env .venv server/config.json server/devices.json server/jobs.json server/sessions.json server/installers orchestrator/tools orchestrator/exp; do
    if [ -e "$path" ]; then
      paths+=("$path")
    fi
  done
  if [ "${#paths[@]}" -gt 0 ]; then
    run_sudo chown -R "$APP_USER:$APP_GROUP" "${paths[@]}" || true
  fi
}

upsert_env() {
  local key="$1"
  local value="$2"
  local esc_value="${value//\"/\\\"}"
  if grep -q "^${key}=" .env; then
    awk -v key="$key" -v val="$esc_value" 'BEGIN{q="\""} $0 ~ "^" key "=" {print key "=" q val q; next} {print}' .env > .env.tmp && mv .env.tmp .env
  else
    printf '%s="%s"\n' "$key" "$esc_value" >> .env
  fi
}

detect_lan_ip() {
  local ip_addr=""
  if command -v hostname >/dev/null 2>&1; then
    ip_addr="$(hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $0 !~ /^127\./ {print; exit}')"
  fi
  if [ -z "$ip_addr" ] && command -v ip >/dev/null 2>&1; then
    ip_addr="$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}')"
  fi
  if [ -z "$ip_addr" ]; then
    ip_addr="127.0.0.1"
  fi
  printf '%s\n' "$ip_addr"
}

detect_tailscale_ip() {
  local ip_addr=""
  if command -v tailscale >/dev/null 2>&1; then
    ip_addr="$(tailscale ip -4 2>/dev/null | awk 'NF {print; exit}')"
  fi
  if [ -z "$ip_addr" ] && command -v ip >/dev/null 2>&1; then
    ip_addr="$(ip -4 addr show tailscale0 2>/dev/null | awk '/inet / {sub(/\/.*/, "", $2); print $2; exit}')"
  fi
  printf '%s\n' "$ip_addr"
}

detect_public_host() {
  local ip_addr
  ip_addr="$(detect_tailscale_ip)"
  if [ -n "$ip_addr" ]; then
    printf '%s\n' "$ip_addr"
    return
  fi
  detect_lan_ip
}

update_server_config_url() {
  local server_url="$1"
  if [ ! -f server/config.json ]; then
    return
  fi
  "$PY" - "$server_url" <<'PY'
import json
import sys
from pathlib import Path

server_url = sys.argv[1]
path = Path("server/config.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    data = {}
data["server_url"] = server_url
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

allow_tailscale_firewall() {
  local web_port="$1"
  if ! command -v ufw >/dev/null 2>&1; then
    return
  fi
  if ! ufw status 2>/dev/null | grep -qi '^Status: active'; then
    return
  fi
  if ip link show tailscale0 >/dev/null 2>&1; then
    echo "Allowing TCP $web_port on tailscale0 through UFW..."
    run_sudo ufw allow in on tailscale0 to any port "$web_port" proto tcp comment "Pigion webserver via Tailscale" || true
  fi
}

install_systemd_services() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; skipping service installation."
    return
  fi

  local web_host="${PIGION_WEB_HOST:-0.0.0.0}"
  local web_port="${PIGION_WEB_PORT:-8000}"
  local public_host="${PIGION_PUBLIC_HOST:-$(detect_public_host)}"
  local server_url="${PIGION_SERVER_URL:-http://${public_host}:${web_port}}"
  local service_python
  if [ -x "$APP_DIR/.venv/bin/python" ]; then
    service_python="$APP_DIR/.venv/bin/python"
  else
    service_python="$(command -v "$PY")"
  fi
  upsert_env "PIGION_SERVER_URL" "$server_url"
  upsert_env "PIGION_WEB_HOST" "$web_host"
  upsert_env "PIGION_WEB_PORT" "$web_port"
  update_server_config_url "$server_url"
  allow_tailscale_firewall "$web_port"
  repair_app_ownership

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' RETURN

  cat > "$tmp_dir/pigion-web.service" <<EOF
[Unit]
Description=Pigion webserver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=HOME=$APP_HOME
Environment=PYTHONUNBUFFERED=1
ExecStart=$service_python -m uvicorn server.server:app --host $web_host --port $web_port
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  cat > "$tmp_dir/pigion-orchestrator.service" <<EOF
[Unit]
Description=Pigion local orchestrator worker
After=network-online.target pigion-web.service
Wants=network-online.target pigion-web.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=HOME=$APP_HOME
Environment=PYTHONUNBUFFERED=1
ExecStart=$service_python -m server.orchestrator_client --server $server_url --python $service_python --project-root $APP_DIR
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  echo "Installing systemd services..."
  run_sudo install -m 0644 "$tmp_dir/pigion-web.service" /etc/systemd/system/pigion-web.service
  run_sudo install -m 0644 "$tmp_dir/pigion-orchestrator.service" /etc/systemd/system/pigion-orchestrator.service
  run_sudo systemctl daemon-reload
  run_sudo systemctl enable --now pigion-web.service pigion-orchestrator.service
  echo "Installed services: pigion-web.service, pigion-orchestrator.service"
  echo "Webserver should be reachable at: $server_url/login"
}

xml_escape() {
  "$PY" -c 'import html, sys; print(html.escape(sys.argv[1], quote=True))' "$1"
}

install_launchd_services() {
  local web_host="${PIGION_WEB_HOST:-0.0.0.0}"
  local web_port="${PIGION_WEB_PORT:-8000}"
  local public_host="${PIGION_PUBLIC_HOST:-$(detect_public_host)}"
  local server_url="${PIGION_SERVER_URL:-http://${public_host}:${web_port}}"
  local service_python="$APP_DIR/.venv/bin/python"
  [ -x "$service_python" ] || service_python="$(command -v "$PY")"

  upsert_env "PIGION_SERVER_URL" "$server_url"
  upsert_env "PIGION_WEB_HOST" "$web_host"
  upsert_env "PIGION_WEB_PORT" "$web_port"
  update_server_config_url "$server_url"
  repair_app_ownership

  local launch_dir="$APP_HOME/Library/LaunchAgents"
  local log_dir="$APP_DIR/.pigion-services"
  mkdir -p "$launch_dir" "$log_dir"
  cat > "$log_dir/web.sh" <<EOF
#!/usr/bin/env bash
set -a
. $(printf '%q' "$APP_DIR/.env")
set +a
cd $(printf '%q' "$APP_DIR")
exec $(printf '%q' "$service_python") -m uvicorn server.server:app --host $(printf '%q' "$web_host") --port $(printf '%q' "$web_port")
EOF
  cat > "$log_dir/orchestrator.sh" <<EOF
#!/usr/bin/env bash
set -a
. $(printf '%q' "$APP_DIR/.env")
set +a
cd $(printf '%q' "$APP_DIR")
exec $(printf '%q' "$service_python") -m server.orchestrator_client --server $(printf '%q' "$server_url") --python $(printf '%q' "$service_python") --project-root $(printf '%q' "$APP_DIR")
EOF
  chmod 0700 "$log_dir/web.sh" "$log_dir/orchestrator.sh"

  local app_dir_xml web_script_xml orchestrator_script_xml
  app_dir_xml="$(xml_escape "$APP_DIR")"
  web_script_xml="$(xml_escape "$log_dir/web.sh")"
  orchestrator_script_xml="$(xml_escape "$log_dir/orchestrator.sh")"

  cat > "$launch_dir/com.pigion.web.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.pigion.web</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$web_script_xml</string></array>
<key>WorkingDirectory</key><string>$app_dir_xml</string>
<key>EnvironmentVariables</key><dict><key>HOME</key><string>$(xml_escape "$APP_HOME")</string><key>PYTHONUNBUFFERED</key><string>1</string></dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$app_dir_xml/.pigion-services/web.log</string>
<key>StandardErrorPath</key><string>$app_dir_xml/.pigion-services/web.err.log</string>
</dict></plist>
EOF

  cat > "$launch_dir/com.pigion.orchestrator.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.pigion.orchestrator</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$orchestrator_script_xml</string></array>
<key>WorkingDirectory</key><string>$app_dir_xml</string>
<key>EnvironmentVariables</key><dict><key>HOME</key><string>$(xml_escape "$APP_HOME")</string><key>PYTHONUNBUFFERED</key><string>1</string></dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$app_dir_xml/.pigion-services/orchestrator.log</string>
<key>StandardErrorPath</key><string>$app_dir_xml/.pigion-services/orchestrator.err.log</string>
</dict></plist>
EOF

  launchctl bootout "gui/$(id -u "$APP_USER")/com.pigion.web" >/dev/null 2>&1 || true
  launchctl bootout "gui/$(id -u "$APP_USER")/com.pigion.orchestrator" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u "$APP_USER")" "$launch_dir/com.pigion.web.plist"
  launchctl bootstrap "gui/$(id -u "$APP_USER")" "$launch_dir/com.pigion.orchestrator.plist"
  echo "Installed launchd agents: com.pigion.web, com.pigion.orchestrator"
  echo "Webserver should be reachable at: $server_url/login"
}

install_services() {
  if [ "$OS_NAME" = "Darwin" ]; then
    install_launchd_services
  elif [ "$OS_NAME" = "Linux" ]; then
    install_systemd_services
  else
    echo "Unsupported OS for deploy/setup.sh: $OS_NAME. On Windows run deploy/setup.ps1." >&2
    exit 1
  fi
}

# Check for pip
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || install_python
fi
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "pip for python3 is still unavailable after dependency installation." >&2
  exit 1
fi

# Ask whether to create a virtualenv and install there (default: yes)
read -r -p "Create a virtualenv in .venv and install requirements there? [Y/n] " CREATE_VENV
CREATE_VENV=${CREATE_VENV:-Y}

if [[ "$CREATE_VENV" =~ ^[Yy] ]]; then
  if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    if ! "$PY" -m venv .venv; then
      rm -rf .venv
      install_python
      "$PY" -m venv .venv
    fi
  else
    echo "Using existing .venv virtual environment."
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PIP_CMD="pip"
else
  PIP_CMD="$PY -m pip"
fi

# Install requirements if requirements.txt exists
if [ -f requirements.txt ]; then
  echo "Installing requirements from requirements.txt..."
  if [[ "$PIP_CMD" == "pip" ]]; then
    pip install --upgrade pip
    pip install -r requirements.txt
  else
    $PIP_CMD install --upgrade pip
    $PIP_CMD install -r requirements.txt
  fi
else
  echo "No requirements.txt found in the current directory. Skipping pip install."
fi

# Prompt for API key and write to .env
read -r -p "Enter API_KEY (leave empty to set blank): " API_KEY

# Get absolute path of project folder
ABS_PATH="$APP_DIR"

# Ensure we safely handle double quotes in the entered key when inserting
esc_key="${API_KEY//\"/\\\"}"

if [ -f .env ]; then
  read -r -p ".env already exists. Overwrite it? [y/N] " OVERWRITE
  if [[ "$OVERWRITE" =~ ^[yY]$ ]]; then
    echo "Writing .env..."
    printf 'API_KEY="%s"\nABS_PATH="%s"\n' "$esc_key" "$ABS_PATH" > .env
  else
    echo "Updating API_KEY and ABS_PATH in existing .env (or appending if missing)..."
    # Update or add API_KEY
    if grep -q '^API_KEY=' .env; then
      awk -v val="$esc_key" 'BEGIN{q="\""} /^API_KEY=/{print "API_KEY=" q val q; next} {print}' .env > .env.tmp && mv .env.tmp .env
    else
      printf 'API_KEY="%s"\n' "$esc_key" >> .env
    fi
    # Update or add ABS_PATH
    if grep -q '^ABS_PATH=' .env; then
      awk -v val="$ABS_PATH" 'BEGIN{q="\""} /^ABS_PATH=/{print "ABS_PATH=" q val q; next} {print}' .env > .env.tmp && mv .env.tmp .env
    else
      printf 'ABS_PATH="%s"\n' "$ABS_PATH" >> .env
    fi
  fi
else
  printf 'API_KEY="%s"\nABS_PATH="%s"\n' "$esc_key" "$ABS_PATH" > .env
fi

# Prompt for sudo password (optional) and store in .env
read -s -r -p "Enter SUDO_PASSWORD to store in .env (leave empty to skip): " SUDO_PASSWORD
echo
esc_sudo="${SUDO_PASSWORD//\"/\\\"}"

if [ -f .env ]; then
  if [ -n "$SUDO_PASSWORD" ]; then
    if grep -q '^SUDO_PASSWORD=' .env; then
      awk -v val="$esc_sudo" 'BEGIN{q="\""} /^SUDO_PASSWORD=/{print "SUDO_PASSWORD=" q val q; next} {print}' .env > .env.tmp && mv .env.tmp .env
    else
      printf 'SUDO_PASSWORD="%s"\n' "$esc_sudo" >> .env
    fi
  fi
fi

chmod 600 .env || true
repair_app_ownership

echo "Done. .env created/updated."
if [[ "$CREATE_VENV" =~ ^[Yy] ]]; then
  echo "To activate the virtualenv: source .venv/bin/activate"
fi

install_services

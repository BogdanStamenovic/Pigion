#!/usr/bin/env bash
set -euo pipefail

# Setup script: installs requirements and creates a .env with API_KEY
# Usage: ./setup.sh

# Check for python3
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not found. Please install Python 3." >&2
  exit 1
fi

PY=python3

# Check for pip
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "pip for python3 is required but not found. Try: python3 -m ensurepip --upgrade or install pip." >&2
  exit 1
fi

# Ask whether to create a virtualenv and install there (default: yes)
read -r -p "Create a virtualenv in .venv and install requirements there? [Y/n] " CREATE_VENV
CREATE_VENV=${CREATE_VENV:-Y}

if [[ "$CREATE_VENV" =~ ^[Yy] ]]; then
  if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    "$PY" -m venv .venv
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
ABS_PATH="$(pwd)"

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

echo "Done. .env created/updated."
if [[ "$CREATE_VENV" =~ ^[Yy] ]]; then
  echo "To activate the virtualenv: source .venv/bin/activate"
fi

# Ask for device type
read -r -p "Is this device a Pi or a Laptop? [pi/laptop]: " DEVICE_TYPE
DEVICE_TYPE=${DEVICE_TYPE,,} # to lowercase

# Auto-detect OS and TERMINAL for this device
if [[ "$DEVICE_TYPE" == "pi" || "$DEVICE_TYPE" == "laptop" ]]; then
  # Try to get pretty OS name
  if [ -f /etc/os-release ]; then
    DEVICE_OS=$(grep '^PRETTY_NAME=' /etc/os-release | cut -d'=' -f2- | tr -d '"')
  else
    DEVICE_OS=$(uname -a)
  fi
  DEVICE_TERMINAL=$(basename "$SHELL")
fi

# Set env file paths based on device type
if [[ "$DEVICE_TYPE" == "pi" ]]; then
  ENV_PATH="pi_exp/enving.txt"
  OTHER_ENV_PATH="laptop_exp/enving.txt"
  OTHER_LABEL="laptop"
else
  ENV_PATH="laptop_exp/enving.txt"
  OTHER_ENV_PATH="pi_exp/enving.txt"
  OTHER_LABEL="pi"
fi

# Create or update enving.txt for this device
mkdir -p "$(dirname "$ENV_PATH")"
echo "OS: $DEVICE_OS" > "$ENV_PATH"
echo "TERMINAL: $DEVICE_TERMINAL" >> "$ENV_PATH"
echo "Populated $ENV_PATH with this device's specs."

# Ask for other device's enving.txt fields
read -r -p "Enter $OTHER_LABEL OS (e.g. Windows 10): " OTHER_OS
read -r -p "Enter $OTHER_LABEL TERMINAL (e.g. powershell): " OTHER_TERMINAL

mkdir -p "$(dirname "$OTHER_ENV_PATH")"
echo "OS: $OTHER_OS" > "$OTHER_ENV_PATH"
echo "TERMINAL: $OTHER_TERMINAL" >> "$OTHER_ENV_PATH"
echo "Populated $OTHER_ENV_PATH with $OTHER_LABEL specs."

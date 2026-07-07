from __future__ import annotations

import html
import io
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tarfile
import textwrap
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from maker import (
    create_instance,
    detect_environment_values,
    discover_platforms,
    discover_tools_for_platform,
    normalize_tools,
    render_environment,
)
from server.web_store import (
    CONFIG_PATH,
    DEVICES_PATH,
    JOBS_PATH,
    ORCHESTRATOR_DISPLAY_NAME,
    ORCHESTRATOR_JOB_TARGET,
    ORCHESTRATOR_ROOT,
    PROJECT_ROOT,
    SERVER_ROOT,
    SESSIONS_PATH,
    append_job_log,
    create_job,
    ensure_files,
    hash_password,
    now_ts,
    read_json,
    utc_now,
    write_json,
)


app = FastAPI(title="Pigion Orchestrator")
UNINSTALL_JOB_KIND = "uninstall"
UNINSTALL_JOB_GOAL = "__pigion_uninstall__"


def form_data(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def form_values(body: bytes) -> dict[str, list[str]]:
    return parse_qs(body.decode("utf-8"), keep_blank_values=True)


def e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def config() -> dict[str, Any]:
    ensure_files()
    return read_json(CONFIG_PATH, {})


def current_session(request: Request) -> dict[str, Any] | None:
    session_id = request.cookies.get("pigion_session")
    if not session_id:
        return None
    sessions_doc = read_json(SESSIONS_PATH, {"sessions": {}})
    session = sessions_doc.get("sessions", {}).get(session_id)
    if not session:
        return None
    if float(session.get("expires_at", 0)) < now_ts():
        sessions_doc["sessions"].pop(session_id, None)
        write_json(SESSIONS_PATH, sessions_doc)
        return None
    return {"id": session_id, **session}


def require_login(request: Request) -> None:
    if current_session(request) is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)} - Pigion</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f6f7f9; color: #17202a; }}
    header {{ background: #111827; color: white; padding: 14px 22px; display: flex; justify-content: space-between; align-items: center; }}
    header a {{ color: white; margin-left: 14px; text-decoration: none; }}
    main {{ max-width: 980px; margin: 24px auto; padding: 0 18px; }}
    section, form.panel {{ background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    label {{ display: block; font-weight: 650; margin: 12px 0 5px; }}
    input, select, textarea {{ box-sizing: border-box; width: 100%; padding: 9px; border: 1px solid #c8d0dc; border-radius: 6px; font: inherit; }}
    textarea {{ min-height: 130px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    button {{ padding: 9px 13px; border: 0; border-radius: 6px; background: #1f6feb; color: white; font-weight: 650; cursor: pointer; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #e4e8ef; vertical-align: top; }}
    code {{ background: #eef2f7; padding: 2px 5px; border-radius: 4px; }}
    pre {{ white-space: pre-wrap; background: #111827; color: #e5e7eb; padding: 12px; border-radius: 6px; overflow: auto; }}
    .ok {{ color: #147d3f; font-weight: 700; }}
    .down {{ color: #b42318; font-weight: 700; }}
    .muted {{ color: #667085; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }}
  </style>
</head>
<body>
  <header><strong>Pigion Orchestrator</strong><nav><a href="/">Dashboard</a><a href="/register">Register Device</a><a href="/remove">Remove Registered Device</a><a href="/goals">Send Goal</a><a href="/logout">Logout</a></nav></header>
  <main>{body}</main>
</body>
</html>"""


def device_is_down(device: dict[str, Any], timeout: int) -> bool:
    heartbeat = float(device.get("last_heartbeat_ts") or 0)
    return heartbeat <= 0 or (now_ts() - heartbeat) > timeout


def update_orchestrator_tool(device: dict[str, Any]) -> None:
    tools_dir = ORCHESTRATOR_ROOT / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    command = device["command"]
    module_path = tools_dir / f"{command}.py"
    module_path.write_text(
        textwrap.dedent(
            f"""
            import os

            from server.web_store import create_job, wait_for_job


            DEVICE_UUID = {device["uuid"]!r}


            def {command}(goal, memory, local_state, program_state=None):
                job = create_job(DEVICE_UUID, goal, source="orchestrator_tool")
                timeout_seconds = float(os.environ.get("PIGION_DEVICE_JOB_TIMEOUT", "900"))
                finished_job = wait_for_job(job["id"], timeout_seconds=timeout_seconds)
                local_state = dict(local_state)
                status = str(finished_job.get("status", "unknown"))
                if status == "finished":
                    result = finished_job.get("result")
                    output = "Device job " + job["id"] + " finished"
                    if result:
                        output += ": " + str(result)
                    ok = True
                else:
                    error = finished_job.get("error") or ("Device job " + job["id"] + " ended with status " + status)
                    output = str(error)
                    ok = False
                local_state["last_tool_output"] = output
                local_state["last_device_job"] = {{
                    "id": job["id"],
                    "device_uuid": DEVICE_UUID,
                    "status": status,
                    "result": finished_job.get("result"),
                    "error": finished_job.get("error"),
                    "finished_at": finished_job.get("finished_at"),
                }}
                return {{
                    "ok": ok,
                    "output": output,
                    "memory": memory,
                    "state": local_state,
                    "program_state": program_state or {{}},
                }}
            """
        ).lstrip(),
        encoding="utf-8",
    )

    td_path = ORCHESTRATOR_ROOT / "exp" / "td.txt"
    td_path.parent.mkdir(parents=True, exist_ok=True)
    current = td_path.read_text(encoding="utf-8") if td_path.exists() else ""
    lines = [line for line in current.splitlines() if f"Command - {command}:" not in line]
    status_text = "DEVICE IS DOWN" if device.get("is_down") else "ONLINE"
    capability = " ".join(str(device.get("capability_block", "")).split())
    doc = (
        f"{len(lines) + 1}.Device {device['name']} [{status_text}], "
        f"Description: {capability or 'Remote Pigion watchdog device'}, "
        f"Command - {command}:GOAL, Example - {command}:Check system temperature"
    )
    lines.append(doc)
    td_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_orchestrator_tool(device: dict[str, Any]) -> None:
    command = str(device.get("command", ""))
    if command:
        tool_path = ORCHESTRATOR_ROOT / "tools" / f"{command}.py"
        if tool_path.exists():
            tool_path.unlink()

    td_path = ORCHESTRATOR_ROOT / "exp" / "td.txt"
    if not td_path.exists():
        return

    lines = []
    for line in td_path.read_text(encoding="utf-8").splitlines():
        if command and f"Command - {command}:" in line:
            continue
        lines.append(line)

    renumbered = []
    for index, line in enumerate(lines, start=1):
        renumbered.append(re.sub(r"^\s*\d+\.", f"{index}.", line))
    td_path.write_text(("\n".join(renumbered) + "\n") if renumbered else "", encoding="utf-8")


def env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        try:
            parts = shlex.split(raw_value, posix=True)
            value = parts[0] if parts else ""
        except ValueError:
            value = raw_value.strip().strip('"').strip("'")
        values[key] = value
    return values


def cleanup_sudo_password() -> str:
    env_password = os.environ.get("SUDO_PASSWORD") or os.environ.get("TEST_SUDO_PASSWORD")
    if env_password:
        return env_password
    values = env_values(PROJECT_ROOT / ".env")
    return values.get("SUDO_PASSWORD") or values.get("TEST_SUDO_PASSWORD") or ""


def run_cleanup_command(
    command: list[str],
    messages: list[str],
    *,
    sudo: bool = False,
    sudo_password: str = "",
) -> None:
    full_command = list(command)
    input_text = None
    if sudo and os.geteuid() != 0:
        if sudo_password:
            full_command = ["sudo", "-S", "-p", "", *command]
            input_text = sudo_password + "\n"
        else:
            full_command = ["sudo", "-n", *command]
    result = subprocess.run(full_command, input=input_text, text=True, capture_output=True)
    detail = (result.stderr or result.stdout).strip()
    if result.returncode == 0:
        messages.append(f"ok: {' '.join(full_command)}")
    elif detail:
        messages.append(f"failed: {' '.join(full_command)} -> {detail}")
    else:
        messages.append(f"failed: {' '.join(full_command)}")


def remove_path_best_effort(path: Path, messages: list[str], sudo_password: str = "") -> None:
    if not path.exists():
        return
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        messages.append(f"removed: {path}")
    except PermissionError:
        run_cleanup_command(["rm", "-rf", str(path)], messages, sudo=True, sudo_password=sudo_password)


def remove_local_install(device: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    device_name = str(device.get("name", "")).strip()
    if not device_name:
        return messages

    sudo_password = cleanup_sudo_password()
    service_name = f"pigion_{device_name}.service"
    run_cleanup_command(["systemctl", "disable", "--now", service_name], messages, sudo=True, sudo_password=sudo_password)
    remove_path_best_effort(Path("/etc/systemd/system") / service_name, messages, sudo_password)
    run_cleanup_command(["systemctl", "daemon-reload"], messages, sudo=True, sudo_password=sudo_password)

    install_root = Path("/opt/pigion")
    remove_path_best_effort(install_root / device_name, messages, sudo_password)
    # Older test registrations used capitalization variants; remove exact case-insensitive matches.
    if install_root.exists():
        for child in install_root.iterdir():
            if child.name.lower() == device_name.lower():
                remove_path_best_effort(child, messages, sudo_password)
        try:
            if not any(install_root.iterdir()):
                remove_path_best_effort(install_root, messages, sudo_password)
        except PermissionError:
            pass
    return messages


def existing_uninstall_job(device_uuid: str) -> dict[str, Any] | None:
    jobs_doc = read_json(JOBS_PATH, {"jobs": {}})
    jobs = sorted(
        jobs_doc.get("jobs", {}).values(),
        key=lambda item: item.get("created_at", ""),
        reverse=True,
    )
    for job in jobs:
        if (
            job.get("device_uuid") == device_uuid
            and job.get("kind") == UNINSTALL_JOB_KIND
            and job.get("status") in {"waiting", "running"}
        ):
            return job
    return None


def mark_device_removal_pending(device_uuid: str, job: dict[str, Any]) -> None:
    devices_doc = read_json(DEVICES_PATH, {"devices": {}})
    device = devices_doc.get("devices", {}).get(device_uuid)
    if not device:
        return
    device["removal_pending"] = True
    device["removal_job_id"] = job.get("id")
    device["removal_requested_at"] = utc_now()
    write_json(DEVICES_PATH, devices_doc)


def queue_remote_uninstall(device_uuid: str) -> dict[str, Any]:
    existing = existing_uninstall_job(device_uuid)
    if existing:
        return existing
    job = create_job(
        device_uuid,
        UNINSTALL_JOB_GOAL,
        source="remove_registered_device",
        kind=UNINSTALL_JOB_KIND,
    )
    mark_device_removal_pending(device_uuid, job)
    return job


def wait_for_uninstall_ack(job_id: str, timeout_seconds: float) -> dict[str, Any] | None:
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        jobs_doc = read_json(JOBS_PATH, {"jobs": {}})
        job = jobs_doc.get("jobs", {}).get(job_id)
        if not job:
            devices_doc = read_json(DEVICES_PATH, {"devices": {}})
            if not any(
                device.get("removal_job_id") == job_id
                for device in devices_doc.get("devices", {}).values()
            ):
                return {
                    "id": job_id,
                    "status": "finished",
                    "result": "remote uninstall acknowledged and registry finalized",
                }
            return None
        if job.get("status") in {"finished", "failed"}:
            return job
        time.sleep(0.5)
    return read_json(JOBS_PATH, {"jobs": {}}).get("jobs", {}).get(job_id)


def finalize_registered_device_removal(device_uuid: str) -> list[str]:
    messages: list[str] = []
    devices_doc = read_json(DEVICES_PATH, {"devices": {}})
    device = devices_doc.get("devices", {}).pop(device_uuid, None)
    if not device:
        return ["Device was already absent from server registry."]

    write_json(DEVICES_PATH, devices_doc)
    remove_orchestrator_tool(device)

    jobs_doc = read_json(JOBS_PATH, {"jobs": {}})
    jobs_doc["jobs"] = {
        job_id: job
        for job_id, job in jobs_doc.get("jobs", {}).items()
        if job.get("device_uuid") != device_uuid
    }
    write_json(JOBS_PATH, jobs_doc)

    installer_path = device.get("installer_path")
    if installer_path:
        remove_path_best_effort(Path(str(installer_path)), messages)

    runner_path = device.get("runner_path")
    if runner_path:
        package_dir = (PROJECT_ROOT / str(runner_path)).parent
        if package_dir.is_relative_to(PROJECT_ROOT):
            remove_path_best_effort(package_dir, messages)

    messages.extend(remove_local_install(device))
    return messages


def remove_registered_device(device_uuid: str) -> list[str]:
    devices_doc = read_json(DEVICES_PATH, {"devices": {}})
    device = devices_doc.get("devices", {}).get(device_uuid)
    if not device:
        return ["Device was already absent from server registry."]

    messages: list[str] = []
    job = queue_remote_uninstall(device_uuid)
    messages.append(f"queued remote uninstall job: {job['id']}")
    timeout_seconds = float(os.environ.get("PIGION_REMOVE_UNINSTALL_TIMEOUT", "45"))
    finished_job = wait_for_uninstall_ack(str(job["id"]), timeout_seconds)
    if not finished_job or finished_job.get("status") not in {"finished", "failed"}:
        messages.append(
            "remote uninstall is still pending; device remains registered so it can receive the uninstall command"
        )
        return messages
    if finished_job.get("status") != "finished":
        messages.append(f"remote uninstall failed: {finished_job.get('error') or finished_job.get('result')}")
        messages.append("device remains registered so you can retry removal")
        return messages

    messages.append(str(finished_job.get("result") or "remote uninstall acknowledged"))
    devices_doc = read_json(DEVICES_PATH, {"devices": {}})
    if device_uuid not in devices_doc.get("devices", {}):
        messages.append("server registry cleanup is already finalized")
        return messages
    messages.extend(finalize_registered_device_removal(device_uuid))
    return messages


def server_url() -> str:
    return str(config().get("server_url", "http://127.0.0.1:8000")).rstrip("/")


def install_command(device_uuid: str) -> str:
    return f"curl -fsSL {server_url()}/install/{device_uuid}.sh | bash"


def job_target_label(device_uuid: str) -> str:
    if device_uuid == ORCHESTRATOR_JOB_TARGET:
        return ORCHESTRATOR_DISPLAY_NAME
    device = read_json(DEVICES_PATH, {"devices": {}}).get("devices", {}).get(device_uuid)
    return str(device.get("name") or device_uuid or "unknown")


def get_device_or_404(device_uuid: str) -> dict[str, Any]:
    device = read_json(DEVICES_PATH, {"devices": {}}).get("devices", {}).get(device_uuid)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def write_device_client(device: dict[str, Any]) -> str:
    return textwrap.dedent(
        f"""
        from __future__ import annotations

        import importlib
        import json
        import subprocess
        import time
        import traceback
        import urllib.error
        import urllib.request
        from pathlib import Path
        from typing import Any


        SERVER_URL = {server_url()!r}
        DEVICE_UUID = {device["uuid"]!r}
        RUNNER_MODULE = {device["runner_module"]!r}
        POLL_SECONDS = {float(device.get("poll_seconds", 5.0))!r}


        def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            data = None
            headers = {{}}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))


        def load_runner():
            module = importlib.import_module(RUNNER_MODULE)
            if hasattr(module, "run_agent"):
                return module.run_agent
            for name in dir(module):
                value = getattr(module, name)
                if callable(value) and name.startswith("run_"):
                    return value
            raise RuntimeError(f"No run_agent or run_* callable found in {{RUNNER_MODULE}}")


        def send_log(message: str, job_id: str | None = None) -> None:
            payload = {{"message": message}}
            if job_id:
                payload["job_id"] = job_id
            request_json("POST", f"{{SERVER_URL}}/api/device/{{DEVICE_UUID}}/logs", payload)


        def start_uninstall() -> str:
            script = Path(__file__).resolve().parent / "uninstall.sh"
            if not script.exists():
                raise RuntimeError(f"Uninstall script not found: {{script}}")
            subprocess.Popen(
                ["bash", str(script)],
                cwd=str(script.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return f"Started remote uninstall script: {{script}}"


        def main() -> None:
            while True:
                try:
                    request_json("POST", f"{{SERVER_URL}}/api/device/{{DEVICE_UUID}}/heartbeat", {{}})
                    job = request_json("GET", f"{{SERVER_URL}}/api/jobs/{{DEVICE_UUID}}").get("job")
                    if not job:
                        time.sleep(POLL_SECONDS)
                        continue

                    job_id = job["id"]
                    request_json("POST", f"{{SERVER_URL}}/api/jobs/{{job_id}}/started", {{}})
                    request_json(
                        "POST",
                        f"{{SERVER_URL}}/api/jobs/{{job_id}}/progress",
                        {{"progress": "running", "log": "Sent goal to watchdog"}},
                    )
                    try:
                        if job.get("kind") == {UNINSTALL_JOB_KIND!r} or job.get("goal") == {UNINSTALL_JOB_GOAL!r}:
                            result = start_uninstall()
                            request_json(
                                "POST",
                                f"{{SERVER_URL}}/api/jobs/{{job_id}}/finished",
                                {{"success": True, "result": result, "log": "Watchdog started uninstall"}},
                            )
                            time.sleep(2)
                            continue
                        run_agent = load_runner()
                        result = run_agent(job["goal"])
                        request_json(
                            "POST",
                            f"{{SERVER_URL}}/api/jobs/{{job_id}}/finished",
                            {{"success": True, "result": result, "log": "Watchdog finished goal"}},
                        )
                    except Exception as exc:
                        request_json(
                            "POST",
                            f"{{SERVER_URL}}/api/jobs/{{job_id}}/finished",
                            {{
                                "success": False,
                                "error": str(exc),
                                "log": traceback.format_exc(limit=8),
                            }},
                        )
                except urllib.error.URLError:
                    time.sleep(POLL_SECONDS)
                except Exception:
                    traceback.print_exc()
                    time.sleep(POLL_SECONDS)


        if __name__ == "__main__":
            main()
        """
    ).lstrip()


def write_install_script(device: dict[str, Any]) -> str:
    device_name = device["name"]
    device_uuid = device["uuid"]
    unit_name = f"pigion_{device_name}"
    api_token = str(device.get("api_token", ""))
    sudo_password = str(device.get("sudo_password", ""))
    return textwrap.dedent(
        f"""#!/usr/bin/env bash
        set -euo pipefail

        DEVICE_NAME={shlex.quote(device_name)}
        DEVICE_UUID={shlex.quote(device_uuid)}
        SERVER_URL={shlex.quote(server_url())}
        DEVICE_API_TOKEN={shlex.quote(api_token)}
        DEVICE_SUDO_PASSWORD={shlex.quote(sudo_password)}
        INSTALL_ROOT="${{PIGION_INSTALL_ROOT:-/opt/pigion}}"
        INSTALL_DIR="$INSTALL_ROOT/$DEVICE_NAME"
        SERVICE_NAME={shlex.quote(unit_name)}
        SERVICE_USER="${{PIGION_SERVICE_USER:-${{SUDO_USER:-$(id -un)}}}}"
        SERVICE_GROUP="${{PIGION_SERVICE_GROUP:-$(id -gn "$SERVICE_USER")}}"
        SERVICE_HOME="${{PIGION_SERVICE_HOME:-$(getent passwd "$SERVICE_USER" | cut -d: -f6)}}"
        if [ -z "$SERVICE_HOME" ]; then
          SERVICE_HOME="$(eval echo "~$SERVICE_USER")"
        fi
        PYTHON_BIN="${{PYTHON_BIN:-python3}}"
        VENV_DIR="$INSTALL_DIR/.venv"
        VENV_PYTHON="$VENV_DIR/bin/python"

        run_sudo() {{
          if [ "$(id -u)" -eq 0 ]; then
            "$@"
          elif [ -n "$DEVICE_SUDO_PASSWORD" ]; then
            printf '%s\\n' "$DEVICE_SUDO_PASSWORD" | sudo -S -p '' "$@"
          else
            sudo "$@"
          fi
        }}

        command -v curl >/dev/null 2>&1 || {{ echo "curl is required"; exit 1; }}
        command -v "$PYTHON_BIN" >/dev/null 2>&1 || {{ echo "$PYTHON_BIN is required"; exit 1; }}

        if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
          if command -v apt-get >/dev/null 2>&1; then
            run_sudo apt-get update
            run_sudo apt-get install -y python3-venv
          else
            echo "python venv support is required. Install python3-venv for your OS."
            exit 1
          fi
        fi

        TMP_DIR="$(mktemp -d)"
        trap 'rm -rf "$TMP_DIR"' EXIT

        curl -fsSL "$SERVER_URL/install/$DEVICE_UUID/bundle.tgz" -o "$TMP_DIR/pigion-watchdog.tgz"
        curl -fsSL "$SERVER_URL/install/$DEVICE_UUID/client.py" -o "$TMP_DIR/client.py"

        run_sudo mkdir -p "$INSTALL_DIR"
        run_sudo tar -xzf "$TMP_DIR/pigion-watchdog.tgz" -C "$INSTALL_DIR"
        run_sudo install -m 0644 "$TMP_DIR/client.py" "$INSTALL_DIR/client.py"
        run_sudo "$PYTHON_BIN" -m venv "$VENV_DIR"
        run_sudo "$VENV_PYTHON" -m pip install --upgrade pip
        run_sudo "$VENV_PYTHON" -m pip install -r "$INSTALL_DIR/requirements.txt"

        TMP_ENV="$TMP_DIR/pigion.env"
        cat > "$TMP_ENV" <<EOF
ABS_PATH="$INSTALL_DIR"
API_KEY="$DEVICE_API_TOKEN"
SUDO_PASSWORD="$DEVICE_SUDO_PASSWORD"
TEST_SUDO_PASSWORD="$DEVICE_SUDO_PASSWORD"
EOF
        run_sudo install -m 0600 "$TMP_ENV" "$INSTALL_DIR/.env"

        TMP_UNINSTALL="$TMP_DIR/uninstall.sh"
        cat > "$TMP_UNINSTALL" <<'EOF_UNINSTALL'
#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME={shlex.quote(unit_name)}
INSTALL_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
INSTALL_ROOT="$(dirname "$INSTALL_DIR")"
ENV_FILE="$INSTALL_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

DEVICE_SUDO_PASSWORD="${{SUDO_PASSWORD:-${{TEST_SUDO_PASSWORD:-}}}}"

run_sudo() {{
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif [ -n "$DEVICE_SUDO_PASSWORD" ]; then
    printf '%s\\n' "$DEVICE_SUDO_PASSWORD" | sudo -S -p '' "$@"
  else
    sudo "$@"
  fi
}}

FINAL_SCRIPT="$(mktemp "/tmp/${{SERVICE_NAME}}-uninstall.XXXXXX.sh")"
cat > "$FINAL_SCRIPT" <<EOF_FINAL
#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME=$(printf '%q' "$SERVICE_NAME")
INSTALL_DIR=$(printf '%q' "$INSTALL_DIR")
INSTALL_ROOT=$(printf '%q' "$INSTALL_ROOT")
systemctl disable "\\${{SERVICE_NAME}}.service" >/dev/null 2>&1 || true
rm -f "/etc/systemd/system/\\${{SERVICE_NAME}}.service"
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl reset-failed "\\${{SERVICE_NAME}}.service" >/dev/null 2>&1 || true
rm -rf "\\${{INSTALL_DIR}}"
rmdir "\\${{INSTALL_ROOT}}" >/dev/null 2>&1 || true
rm -f "\\$0"
systemctl stop "\\${{SERVICE_NAME}}.service" >/dev/null 2>&1 || true
EOF_FINAL
chmod 700 "$FINAL_SCRIPT"

if command -v systemd-run >/dev/null 2>&1; then
  run_sudo systemd-run --unit="${{SERVICE_NAME}}-uninstall" --collect /bin/bash "$FINAL_SCRIPT"
else
  run_sudo /bin/bash "$FINAL_SCRIPT" &
fi
EOF_UNINSTALL
        run_sudo install -m 0755 "$TMP_UNINSTALL" "$INSTALL_DIR/uninstall.sh"
        run_sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"

        TMP_SERVICE="$TMP_DIR/$SERVICE_NAME.service"
        cat > "$TMP_SERVICE" <<EOF
        [Unit]
        Description=Pigion watchdog client for $DEVICE_NAME
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        User=$SERVICE_USER
        Group=$SERVICE_GROUP
        WorkingDirectory=$SERVICE_HOME
        EnvironmentFile=$INSTALL_DIR/.env
        Environment=HOME=$SERVICE_HOME
        Environment=PYTHONUNBUFFERED=1
        ExecStart=$VENV_PYTHON $INSTALL_DIR/client.py
        Restart=always
        RestartSec=5

        [Install]
        WantedBy=multi-user.target
        EOF
        run_sudo install -m 0644 "$TMP_SERVICE" "/etc/systemd/system/$SERVICE_NAME.service"

        run_sudo systemctl daemon-reload
        run_sudo systemctl enable --now "$SERVICE_NAME.service"

        echo "Installed $SERVICE_NAME"
        echo "Status: systemctl status $SERVICE_NAME.service"
        """
    ).lstrip().replace("\n        ", "\n")


def build_device_bundle(device: dict[str, Any]) -> bytes:
    runner_path = PROJECT_ROOT / device["runner_path"]
    package_dir = runner_path.parent
    if not package_dir.exists():
        raise HTTPException(status_code=404, detail="Generated watchdog package not found")

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        archive.add(package_dir, arcname=package_dir.name)
        requirements = PROJECT_ROOT / "requirements.txt"
        if requirements.exists():
            archive.add(requirements, arcname="requirements.txt")
    output.seek(0)
    return output.read()


def refresh_device_availability() -> list[dict[str, Any]]:
    cfg = config()
    timeout = int(cfg.get("heartbeat_timeout_seconds", 60))
    devices_doc = read_json(DEVICES_PATH, {"devices": {}})
    changed = False
    devices = []
    for device in devices_doc.get("devices", {}).values():
        is_down = device_is_down(device, timeout)
        if device.get("is_down") != is_down:
            device["is_down"] = is_down
            update_orchestrator_tool(device)
            changed = True
        devices.append(device)
    if changed:
        write_json(DEVICES_PATH, devices_doc)
    return sorted(devices, key=lambda item: item.get("created_at", ""))


def job_counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"running": 0, "waiting": 0}
    for job in jobs:
        if job.get("status") in counts:
            counts[job["status"]] += 1
    return counts


@app.on_event("startup")
def startup() -> None:
    ensure_files()


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return layout(
        "Login",
        """<form class="panel" method="post" action="/login">
  <h1>Login</h1>
  <label>Username</label><input name="username" autocomplete="username" value="admin">
  <label>Password</label><input name="password" type="password" autocomplete="current-password">
  <p><button type="submit">Login</button></p>
</form>""",
    )


@app.post("/login")
async def login(request: Request) -> Response:
    data = form_data(await request.body())
    cfg = config()
    if data.get("username") != cfg.get("admin_username") or hash_password(data.get("password", "")) != cfg.get("admin_password_hash"):
        return HTMLResponse(layout("Login", "<section><p class='down'>Invalid login.</p><p><a href='/login'>Try again</a></p></section>"), status_code=401)
    session_id = secrets.token_urlsafe(32)
    sessions_doc = read_json(SESSIONS_PATH, {"sessions": {}})
    sessions_doc.setdefault("sessions", {})[session_id] = {
        "username": data.get("username"),
        "created_at": now_ts(),
        "expires_at": now_ts() + int(cfg.get("session_ttl_seconds", 86400)),
    }
    write_json(SESSIONS_PATH, sessions_doc)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("pigion_session", session_id, httponly=True, samesite="lax")
    return response


@app.get("/logout")
def logout(request: Request) -> Response:
    session_id = request.cookies.get("pigion_session")
    if session_id:
        sessions_doc = read_json(SESSIONS_PATH, {"sessions": {}})
        sessions_doc.get("sessions", {}).pop(session_id, None)
        write_json(SESSIONS_PATH, sessions_doc)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("pigion_session")
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> str:
    require_login(request)
    devices = refresh_device_availability()
    jobs = sorted(read_json(JOBS_PATH, {"jobs": {}}).get("jobs", {}).values(), key=lambda item: item.get("created_at", ""), reverse=True)
    counts = job_counts(jobs)
    device_rows = "".join(
        f"<tr><td>{'<span class=\"down\">x</span>' if d.get('is_down') else '<span class=\"ok\">✓</span>'} {e(d.get('name'))} {'<span class=\"down\">(DEVICE IS DOWN)</span>' if d.get('is_down') else ''}</td><td><code>{e(d.get('uuid'))}</code></td><td>{e(d.get('last_heartbeat') or 'never')}</td><td><form method='post' action='/devices/{e(d.get('uuid'))}/remove'><button type='submit'>Remove Registered Device</button></form></td></tr>"
        for d in devices
    ) or "<tr><td colspan='4' class='muted'>No devices registered.</td></tr>"
    recent_jobs = "".join(
        f"<tr><td>{e(j.get('status'))}</td><td>{e(j.get('goal'))}</td><td>{e(job_target_label(str(j.get('device_uuid', ''))))}</td><td>{e(j.get('updated_at'))}</td></tr>"
        for j in jobs[:8]
    ) or "<tr><td colspan='4' class='muted'>No goals yet.</td></tr>"
    recent_logs = []
    for job in jobs:
        for log in reversed(job.get("logs", [])[-3:]):
            recent_logs.append(f"<tr><td>{e(log.get('at'))}</td><td><code>{e(job.get('id'))}</code></td><td>{e(log.get('message'))}</td></tr>")
    body = f"""
<section><h1>Dashboard</h1><div class="grid"><p><strong>Queue</strong><br>{counts['running']} running<br>{counts['waiting']} waiting</p><p><strong>Devices</strong><br>{len(devices)} registered</p></div></section>
<section><h2>Devices</h2><table><tr><th>Device</th><th>UUID</th><th>Last heartbeat</th><th>Remove</th></tr>{device_rows}</table></section>
<section><h2>Recent Goals</h2><table><tr><th>Status</th><th>Goal</th><th>Target</th><th>Updated</th></tr>{recent_jobs}</table></section>
<section><h2>Recent Logs</h2><table><tr><th>Time</th><th>Job</th><th>Message</th></tr>{''.join(recent_logs[:12]) or "<tr><td colspan='3' class='muted'>No logs yet.</td></tr>"}</table></section>
"""
    return layout("Dashboard", body)


@app.get("/remove", response_class=HTMLResponse)
def remove_page(request: Request) -> str:
    require_login(request)
    devices = refresh_device_availability()
    rows = "".join(
        f"<tr><td>{e(device.get('name'))}</td><td><code>{e(device.get('uuid'))}</code></td><td><form method='post' action='/devices/{e(device.get('uuid'))}/remove'><button type='submit'>Remove Registered Device</button></form></td></tr>"
        for device in devices
    ) or "<tr><td colspan='3' class='muted'>No devices registered.</td></tr>"
    return layout(
        "Remove Registered Device",
        f"""<section>
  <h1>Remove Registered Device</h1>
  <table><tr><th>Device</th><th>UUID</th><th>Action</th></tr>{rows}</table>
</section>""",
    )


@app.post("/devices/{device_uuid}/remove", response_class=HTMLResponse)
def remove_device(request: Request, device_uuid: str) -> str:
    require_login(request)
    messages = remove_registered_device(device_uuid)
    body = "\n".join(messages) if messages else "Removed device."
    return layout(
        "Device Removed",
        f"""<section>
  <h1>Device Removed</h1>
  <pre>{e(body)}</pre>
  <p><a href="/">Back to dashboard</a></p>
</section>""",
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> str:
    require_login(request)
    platform_blocks = []
    platform_options = []
    for platform in discover_platforms():
        platform_options.append(f"<option value='{e(platform)}'>{e(platform)}</option>")
        tools = discover_tools_for_platform(platform)
        try:
            default_os, default_terminal = detect_environment_values(platform)
        except Exception:
            default_os, default_terminal = "", ""
        tool_checks = "".join(
            f"<label><input type='checkbox' name='tools' value='{e(tool)}'> {e(tool)}</label>"
            for tool in tools
        )
        platform_blocks.append(
            f"<section><h3>{e(platform)} tools</h3>{tool_checks or '<p class=\"muted\">No tools found.</p>'}<p class='muted'>Suggested OS: {e(default_os)}; terminal: {e(default_terminal)}</p></section>"
        )
    return layout(
        "Register Device",
        f"""<form class="panel" method="post" action="/register">
  <h1>Register Device</h1>
  <label>Watchdog name</label><input name="name" placeholder="kitchen_pi" required>
  <label>Platform template</label><select name="platform" required>{''.join(platform_options)}</select>
  <label>Device OS</label><input name="device_os" placeholder="Raspberry Pi OS / Ubuntu / Windows / ..." required>
  <label>Device terminal</label><input name="device_terminal" placeholder="bash / zsh / powershell / ..." required>
  <label>API token</label><input name="api_token" type="password" autocomplete="off" required>
  <label>Sudo password</label><input name="sudo_password" type="password" autocomplete="off">
  <label>td.txt capability block</label><textarea name="capability_block" placeholder="Linux Pi device that can run shell/search/memory actions..." required></textarea>
  <h2>Tools</h2>
  <p class="muted">Select the tools for the chosen platform. This intentionally does not auto-select everything.</p>
  {''.join(platform_blocks)}
  <p><button type="submit">Register Device</button></p>
</form>""",
    )


@app.post("/register", response_class=HTMLResponse)
async def register(request: Request) -> str:
    require_login(request)
    raw_body = await request.body()
    data = form_data(raw_body)
    values = form_values(raw_body)
    name = data.get("name", "").strip()
    platform = data.get("platform", "").strip().lower()
    device_os = data.get("device_os", "").strip()
    device_terminal = data.get("device_terminal", "").strip()
    api_token = data.get("api_token", "").strip()
    sudo_password = data.get("sudo_password", "")
    capability_block = data.get("capability_block", "").strip()
    if not api_token:
        raise HTTPException(status_code=400, detail="API token is required.")
    selected_tools = normalize_tools(",".join(values.get("tools", [])), discover_tools_for_platform(platform))
    if not selected_tools:
        raise HTTPException(status_code=400, detail="Select at least one tool.")
    pull = subprocess.run(["git", "pull"], cwd=PROJECT_ROOT, text=True, capture_output=True)
    if pull.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git pull failed: {pull.stderr or pull.stdout}")

    environment_text = render_environment(device_os, device_terminal)
    try:
        runner_path = create_instance(name, platform, selected_tools, environment_text=environment_text, force=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    device_uuid = str(uuid.uuid4())
    short = device_uuid.replace("-", "")[:12]
    command = f"device_{name}"
    runner_module = f"{name}.run_{name}"
    installer_dir = SERVER_ROOT / "installers"
    installer_dir.mkdir(parents=True, exist_ok=True)
    installer_path = installer_dir / f"install_{name}_{short}.sh"
    device = {
        "uuid": device_uuid,
        "name": name,
        "command": command,
        "platform": platform,
        "selected_tools": selected_tools,
        "device_os": device_os,
        "device_terminal": device_terminal,
        "api_token": api_token,
        "sudo_password": sudo_password,
        "capability_block": capability_block,
        "created_at": utc_now(),
        "last_heartbeat": None,
        "last_heartbeat_ts": None,
        "is_down": True,
        "runner_path": str(runner_path.relative_to(PROJECT_ROOT)),
        "runner_module": runner_module,
        "installer_path": str(installer_path),
        "git_pull_output": (pull.stdout or pull.stderr).strip(),
    }
    installer_path.write_text(write_install_script(device), encoding="utf-8")
    installer_path.chmod(0o755)
    device["installer_command"] = install_command(device_uuid)
    devices_doc = read_json(DEVICES_PATH, {"devices": {}})
    devices_doc.setdefault("devices", {})[device_uuid] = device
    write_json(DEVICES_PATH, devices_doc)
    update_orchestrator_tool(device)
    return layout(
        "Device Registered",
        f"""<section>
  <h1>Device Registered</h1>
  <p><strong>UUID:</strong> <code>{e(device_uuid)}</code></p>
  <p><strong>Installer command:</strong></p><pre>{e(device['installer_command'])}</pre>
  <p><strong>Installer path:</strong> <code>{e(installer_path)}</code></p>
  <p><strong>Platform:</strong> <code>{e(platform)}</code></p>
  <p><strong>Tools:</strong> <code>{e(', '.join(selected_tools))}</code></p>
  <p><strong>Runner:</strong> <code>{e(device['runner_path'])}</code></p>
  <p><a href="/">Back to dashboard</a></p>
</section>""",
    )


@app.get("/goals", response_class=HTMLResponse)
def goals_page(request: Request) -> str:
    require_login(request)
    devices = refresh_device_availability()
    options = f"<option value='{e(ORCHESTRATOR_JOB_TARGET)}'>{e(ORCHESTRATOR_DISPLAY_NAME)}</option>"
    options += "".join(f"<option value='{e(d['uuid'])}'>{e(d['name'])} {'(DEVICE IS DOWN)' if d.get('is_down') else ''}</option>" for d in devices)
    return layout(
        "Send Goal",
        f"""<form class="panel" method="post" action="/goals">
  <h1>Send Goal</h1>
  <label>Target</label><select name="device_uuid">{options}</select>
  <label>Goal</label><textarea name="goal" required></textarea>
  <p><button type="submit">Queue Goal</button></p>
</form>""",
    )


@app.post("/goals")
async def create_goal(request: Request) -> Response:
    require_login(request)
    data = form_data(await request.body())
    device_uuid = data.get("device_uuid", "").strip()
    goal = data.get("goal", "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Goal is required.")
    if device_uuid != ORCHESTRATOR_JOB_TARGET:
        get_device_or_404(device_uuid)
    create_job(device_uuid, goal, source="dashboard")
    return RedirectResponse("/", status_code=303)


@app.post("/api/orchestrator/goals")
async def api_create_orchestrator_goal(request: Request) -> JSONResponse:
    payload = await request.json()
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Goal is required.")
    job = create_job(ORCHESTRATOR_JOB_TARGET, goal, source="api_orchestrator")
    return JSONResponse({"ok": True, "job": job})


@app.get("/api/jobs/{device_uuid}")
def get_job(device_uuid: str) -> JSONResponse:
    jobs_doc = read_json(JOBS_PATH, {"jobs": {}})
    jobs = sorted(jobs_doc.get("jobs", {}).values(), key=lambda item: item.get("created_at", ""))
    for job in jobs:
        if job.get("device_uuid") == device_uuid and job.get("status") == "waiting":
            return JSONResponse({"job": job})
    return JSONResponse({"job": None})


@app.post("/api/jobs/{job_id}/started")
def job_started(job_id: str) -> JSONResponse:
    jobs_doc = read_json(JOBS_PATH, {"jobs": {}})
    job = jobs_doc.get("jobs", {}).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["status"] = "running"
    job["started_at"] = utc_now()
    job["updated_at"] = utc_now()
    write_json(JOBS_PATH, jobs_doc)
    return JSONResponse({"ok": True, "job": job})


@app.post("/api/jobs/{job_id}/progress")
async def job_progress(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    jobs_doc = read_json(JOBS_PATH, {"jobs": {}})
    job = jobs_doc.get("jobs", {}).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["progress"] = str(payload.get("progress", ""))
    if payload.get("log"):
        job.setdefault("logs", []).append({"at": utc_now(), "message": str(payload["log"])})
    job["updated_at"] = utc_now()
    write_json(JOBS_PATH, jobs_doc)
    return JSONResponse({"ok": True, "job": job})


@app.post("/api/jobs/{job_id}/finished")
async def job_finished(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()
    jobs_doc = read_json(JOBS_PATH, {"jobs": {}})
    job = jobs_doc.get("jobs", {}).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    success = bool(payload.get("success", True))
    job["status"] = "finished" if success else "failed"
    job["finished_at"] = utc_now()
    job["updated_at"] = utc_now()
    job["result"] = payload.get("result")
    job["error"] = payload.get("error")
    if payload.get("log"):
        job.setdefault("logs", []).append({"at": utc_now(), "message": str(payload["log"])})
    write_json(JOBS_PATH, jobs_doc)
    return JSONResponse({"ok": True, "job": job})


@app.post("/api/device/{device_uuid}/heartbeat")
def heartbeat(device_uuid: str) -> JSONResponse:
    devices_doc = read_json(DEVICES_PATH, {"devices": {}})
    device = devices_doc.get("devices", {}).get(device_uuid)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device["last_heartbeat"] = utc_now()
    device["last_heartbeat_ts"] = now_ts()
    device["is_down"] = False
    write_json(DEVICES_PATH, devices_doc)
    update_orchestrator_tool(device)
    return JSONResponse({"ok": True})


@app.post("/api/device/{device_uuid}/logs")
async def device_logs(device_uuid: str, request: Request) -> JSONResponse:
    payload = await request.json()
    job_id = payload.get("job_id")
    message = str(payload.get("message", ""))
    if job_id:
        job = append_job_log(str(job_id), message)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse({"ok": True})


@app.get("/installers/{filename}", response_class=PlainTextResponse)
def installer(filename: str) -> str:
    installer_dir = SERVER_ROOT / "installers"
    path = installer_dir / filename
    if not path.exists() or path.parent != installer_dir:
        raise HTTPException(status_code=404, detail="Installer not found")
    return path.read_text(encoding="utf-8")


@app.get("/install/{device_uuid}.sh", response_class=PlainTextResponse)
def generated_install_script(device_uuid: str) -> str:
    return write_install_script(get_device_or_404(device_uuid))


@app.get("/install/{device_uuid}/client.py", response_class=PlainTextResponse)
def generated_client(device_uuid: str) -> str:
    return write_device_client(get_device_or_404(device_uuid))


@app.get("/install/{device_uuid}/bundle.tgz")
def generated_bundle(device_uuid: str) -> StreamingResponse:
    bundle = build_device_bundle(get_device_or_404(device_uuid))
    return StreamingResponse(
        io.BytesIO(bundle),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="pigion-{device_uuid}.tgz"'},
    )

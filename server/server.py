from __future__ import annotations

import html
import io
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tarfile
import textwrap
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from maker import (
    FrameworkManifestError,
    PROFILE_FIELDS,
    concise_architecture_profile,
    create_instance,
    device_architecture_profile,
    framework_env_questions,
    framework_metadata,
    normalize_architecture_profile,
    valid_framework_runtimes,
    validate_tool_selection,
    validated_framework_manifest,
    render_tool_docs,
    render_environment,
    routing_architecture_profile,
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
    update_json,
    utc_now,
    write_json,
)


app = FastAPI(title="Pigion Orchestrator")
DEVICE_PROFILES_PATH = ORCHESTRATOR_ROOT / "exp" / "device_profiles.json"
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
    profile_summary = concise_architecture_profile(device.get("architecture_profile"))
    capability = " ".join(str(device.get("capability_block", "")).split()) or profile_summary
    doc = (
        f"{len(lines) + 1}.Device {device['name']} [{status_text}], "
        f"Description: {capability or 'Remote Pigion watchdog device'}, "
        f"Command - {command}:GOAL, Example - {command}:Check system temperature"
    )
    lines.append(doc)
    td_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    devices = read_json(DEVICES_PATH, {"devices": {}}).get("devices", {})
    public_profiles = {
        str(item.get("command") or item.get("name")): {
            "name": item.get("name"),
            "command": item.get("command"),
            "online": not bool(item.get("is_down")),
            "profile": routing_architecture_profile(item.get("architecture_profile")),
        }
        for item in devices.values()
    }
    write_json(DEVICE_PROFILES_PATH, {"devices": public_profiles})


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
    platform = str(device.get("platform", "linux")).strip().lower()
    if platform in {"windows", "win", "win32", "macos", "darwin", "osx"}:
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


def is_windows_platform(platform: str | None) -> bool:
    return str(platform or "").strip().lower() in {"windows", "win32", "win"}


def is_macos_platform(platform: str | None) -> bool:
    return str(platform or "").strip().lower() in {"macos", "darwin", "osx"}


def install_command(device_uuid: str, platform: str | None = None) -> str:
    if is_windows_platform(platform):
        script_url = f"{server_url()}/install/{device_uuid}.ps1"
        return (
            "powershell -NoProfile -ExecutionPolicy Bypass -Command "
            f"\"iex (iwr -UseBasicParsing '{script_url}').Content\""
        )
    return f"curl -fsSL {server_url()}/install/{device_uuid}.sh | bash"


def parse_framework_platform(raw_value: str) -> tuple[str, str]:
    value = raw_value.strip().lower()
    if "::" in value:
        framework, platform = value.split("::", 1)
    elif "/" in value:
        framework, platform = value.split("/", 1)
    else:
        framework, platform = "pigion", value
    framework = framework.strip()
    platform = platform.strip()
    if not framework or not platform:
        raise ValueError("Choose a framework/runtime template.")
    return framework, platform


def default_capability_block(framework: str, platform: str, selected_tools: list[str]) -> str:
    metadata = framework_metadata(platform, framework)
    description = str(metadata.get("description") or "").strip()
    tool_docs = " ".join(render_tool_docs(selected_tools, platform, framework).split())
    return (
        f"{framework}/{platform} watchdog device. "
        f"{description + ' ' if description else ''}"
        f"Automatically discovered tools: {', '.join(selected_tools)}. "
        f"Tool docs: {tool_docs}"
    ).strip()


def ensure_device_architecture_profile(device: dict[str, Any]) -> bool:
    """Backfill profiles for devices registered before architecture awareness existed."""
    if isinstance(device.get("architecture_profile"), dict):
        return False
    try:
        manifest = validated_framework_manifest(str(device["platform"]), str(device.get("framework") or "pigion"))
        profile = device_architecture_profile(
            manifest,
            device_name=str(device.get("name") or "unknown"),
            device_os=str(device.get("device_os") or "unknown"),
            device_terminal=str(device.get("device_terminal") or "unknown"),
            selected_tools=[str(value) for value in device.get("selected_tools", [])],
        )
    except (KeyError, ValueError, FrameworkManifestError):
        return False
    device["architecture_profile"] = profile
    runner_path = device.get("runner_path")
    if runner_path:
        profile_path = (PROJECT_ROOT / str(runner_path)).parent / "exp" / "architecture_profile.json"
        if not profile_path.exists():
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def normalize_extra_env(raw_values: dict[str, str], framework: str, platform: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    for question in framework_env_questions(platform, framework):
        name = str(question["name"])
        value = raw_values.get(f"framework_env__{name}", "")
        if value == "" and question.get("default") is not None:
            value = str(question.get("default") or "")
        if question.get("required") and value == "":
            label = str(question.get("label") or name)
            raise ValueError(f"{label} is required for {framework}/{platform}.")
        options = question.get("options")
        if options and value not in options:
            raise ValueError(f"{name} must be one of the configured options.")
        validation = dict(question.get("validation") or {})
        if len(value) < int(validation.get("min_length", 0)):
            raise ValueError(f"{name} is shorter than the allowed minimum.")
        if len(value) > int(validation.get("max_length", 2**31)):
            raise ValueError(f"{name} is longer than the allowed maximum.")
        if value and validation.get("pattern") and re.fullmatch(str(validation["pattern"]), value) is None:
            raise ValueError(f"{name} does not match the required format.")
        answers[name] = value
    return answers


def shell_env_assignment(key: str, value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"Invalid env key: {key}")
    return f"{key}={shlex.quote(str(value))}"


def dotenv_assignment(key: str, value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"Invalid env key: {key}")
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def powershell_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def device_env_values(device: dict[str, Any]) -> dict[str, str]:
    llm_provider = normalize_llm_provider(str(device.get("llm_provider", "gemini")))
    values = {
        "ABS_PATH": "__PIGION_INSTALL_DIR__",
        "API_KEY": str(device.get("api_token", "")),
        "LLM_PROVIDER": llm_provider,
        "LLM_MODEL": str(device.get("llm_model") or default_llm_model(llm_provider)),
        "LLM_API_KEY": str(device.get("api_token", "")),
        "GEMINI_API_KEY": str(device.get("api_token", "")),
        "OPENAI_API_KEY": str(device.get("api_token", "")),
        "OPENAI_BASE_URL": str(device.get("openai_base_url") or "https://api.openai.com/v1"),
        "OLLAMA_HOST": str(device.get("ollama_host") or "http://127.0.0.1:11434"),
        "SUDO_PASSWORD": str(device.get("sudo_password", "")),
        "TEST_SUDO_PASSWORD": str(device.get("sudo_password", "")),
    }
    values.update({str(key): str(value) for key, value in dict(device.get("framework_env") or {}).items()})
    return values


def public_framework_spec(manifest: dict[str, Any]) -> dict[str, Any]:
    questions = []
    for question in manifest.get("questions", []):
        public = dict(question)
        if public.get("secret"):
            public["default"] = ""
        questions.append(public)
    return {
        "schema_version": manifest["schema_version"],
        "framework": manifest["framework"],
        "runtime": manifest["runtime"],
        "display_name": manifest.get("display_name", manifest["framework"]),
        "description": manifest.get("description", ""),
        "supported_os": manifest["supported_os"],
        "allow_zero_tools": bool(manifest.get("allow_zero_tools", False)),
        "uses_pigion_model_config": bool(manifest.get("uses_pigion_model_config", True)),
        "architecture_profile": manifest["architecture_profile"],
        "tools": manifest["tools"],
        "questions": questions,
        "lifecycle": sorted(manifest.get("lifecycle", {})),
        "manifest_sha256": manifest["manifest_sha256"],
        "framework_revision": manifest["framework_revision"],
    }


def normalize_llm_provider(raw_value: str) -> str:
    provider = (raw_value or "gemini").strip().lower()
    aliases = {
        "google": "gemini",
        "google-genai": "gemini",
        "gpt": "openai",
        "local": "ollama",
    }
    provider = aliases.get(provider, provider)
    if provider not in {"gemini", "openai", "ollama"}:
        raise ValueError("Choose one of these model providers: gemini, openai, ollama.")
    return provider


def default_llm_model(provider: str) -> str:
    return {
        "gemini": "gemini-2.5-flash-lite",
        "openai": "gpt-4.1-mini",
        "ollama": "llama3.1",
    }.get(provider, "gemini-2.5-flash-lite")


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
    if is_windows_platform(str(device.get("platform", ""))):
        uninstall_command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "__SCRIPT__",
        ]
        uninstall_script = "uninstall.ps1"
    else:
        uninstall_command = ["bash", "__SCRIPT__"]
        uninstall_script = "uninstall.sh"
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


        def heartbeat_payload() -> dict[str, Any]:
            try:
                module = importlib.import_module(RUNNER_MODULE)
                profile_path = Path(module.__file__).resolve().parent / "exp" / "architecture_profile.json"
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                return {{"architecture_profile": profile}} if isinstance(profile, dict) else {{}}
            except (OSError, json.JSONDecodeError):
                return {{}}


        def send_log(message: str, job_id: str | None = None) -> None:
            payload = {{"message": message}}
            if job_id:
                payload["job_id"] = job_id
            request_json("POST", f"{{SERVER_URL}}/api/device/{{DEVICE_UUID}}/logs", payload)


        def start_uninstall() -> str:
            script = Path(__file__).resolve().parent / {uninstall_script!r}
            if not script.exists():
                raise RuntimeError(f"Uninstall script not found: {{script}}")
            command = {uninstall_command!r}
            command = [str(script) if item == "__SCRIPT__" else item for item in command]
            subprocess.Popen(
                command,
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
                    request_json("POST", f"{{SERVER_URL}}/api/device/{{DEVICE_UUID}}/heartbeat", heartbeat_payload())
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
    sudo_password = str(device.get("sudo_password", ""))
    env_lines = [shell_env_assignment(key, value) for key, value in sorted(device_env_values(device).items())]
    env_file_body = "\n".join(env_lines)
    lifecycle = dict(device.get("lifecycle") or {})
    install_phase = "install" in lifecycle
    upgrade_phase = "upgrade" in lifecycle
    verify_phase = "verify" in lifecycle
    return textwrap.dedent(
        f"""#!/usr/bin/env bash
        set -euo pipefail

        DEVICE_NAME={shlex.quote(device_name)}
        DEVICE_UUID={shlex.quote(device_uuid)}
        SERVER_URL={shlex.quote(server_url())}
        DEVICE_SUDO_PASSWORD={shlex.quote(sudo_password)}
        INSTALL_ROOT="${{PIGION_INSTALL_ROOT:-/opt/pigion}}"
        INSTALL_DIR="$INSTALL_ROOT/$DEVICE_NAME"
        WAS_INSTALLED=0
        [ -d "$INSTALL_DIR" ] && WAS_INSTALLED=1
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

        run_as_service() {{
          if [ "$(id -un)" = "$SERVICE_USER" ]; then
            HOME="$SERVICE_HOME" "$@"
          elif [ "$(id -u)" -eq 0 ]; then
            runuser -u "$SERVICE_USER" -- env HOME="$SERVICE_HOME" "$@"
          elif [ -n "$DEVICE_SUDO_PASSWORD" ]; then
            printf '%s\n' "$DEVICE_SUDO_PASSWORD" | sudo -S -p '' -u "$SERVICE_USER" -H "$@"
          else
            sudo -u "$SERVICE_USER" -H "$@"
          fi
        }}

        install_prerequisites() {{
          if command -v apt-get >/dev/null 2>&1; then
            run_sudo apt-get update
            run_sudo apt-get install -y curl tar python3 python3-pip python3-venv
          elif command -v pacman >/dev/null 2>&1; then
            run_sudo pacman -S --needed --noconfirm curl tar python python-pip
          elif command -v dnf >/dev/null 2>&1; then
            run_sudo dnf install -y curl tar python3 python3-pip
          elif command -v yum >/dev/null 2>&1; then
            run_sudo yum install -y curl tar python3 python3-pip
          elif command -v zypper >/dev/null 2>&1; then
            run_sudo zypper --non-interactive install curl tar python3 python3-pip
          elif command -v apk >/dev/null 2>&1; then
            run_sudo apk add curl tar python3 py3-pip
          else
            echo "No supported package manager found. Install curl, tar, Python 3, pip, and venv support." >&2
            exit 1
          fi
        }}

        command -v systemctl >/dev/null 2>&1 || {{ echo "This Linux installer requires systemd; systemctl was not found." >&2; exit 1; }}
        [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ] || {{ echo "This Linux installer requires systemd as PID 1." >&2; exit 1; }}
        if ! command -v curl >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1 || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
          install_prerequisites
        fi

        TMP_DIR="$(mktemp -d)"
        trap 'rm -rf "$TMP_DIR"' EXIT

        if ! "$PYTHON_BIN" -m venv "$TMP_DIR/venv-check" >/dev/null 2>&1; then
          install_prerequisites
          rm -rf "$TMP_DIR/venv-check"
          "$PYTHON_BIN" -m venv "$TMP_DIR/venv-check"
        fi
        rm -rf "$TMP_DIR/venv-check"

        curl -fsSL "$SERVER_URL/install/$DEVICE_UUID/bundle.tgz" -o "$TMP_DIR/pigion-watchdog.tgz"
        curl -fsSL "$SERVER_URL/install/$DEVICE_UUID/client.py" -o "$TMP_DIR/client.py"

        run_sudo mkdir -p "$INSTALL_DIR"
        run_sudo tar -xzf "$TMP_DIR/pigion-watchdog.tgz" -C "$INSTALL_DIR"
        run_sudo install -m 0644 "$TMP_DIR/client.py" "$INSTALL_DIR/client.py"
        run_sudo "$PYTHON_BIN" -m venv "$VENV_DIR"
        run_sudo "$VENV_PYTHON" -m pip install --upgrade pip
        run_sudo "$VENV_PYTHON" -m pip install -r "$INSTALL_DIR/requirements.txt"

        TMP_ENV="$TMP_DIR/pigion.env"
        cat > "$TMP_ENV" <<'EOF_ENV'
{env_file_body}
EOF_ENV
        sed -i "s|ABS_PATH=__PIGION_INSTALL_DIR__|ABS_PATH=\\"$INSTALL_DIR\\"|" "$TMP_ENV"
        run_sudo install -m 0600 "$TMP_ENV" "$INSTALL_DIR/.env"
        run_sudo chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"

        run_framework_phase() {{
          PHASE="$1"
          SCRIPT="$INSTALL_DIR/$DEVICE_NAME/lifecycle/$PHASE.sh"
          [ -f "$SCRIPT" ] || return 0
          run_as_service env \
            PIGION_INSTALL_DIR="$INSTALL_DIR" \
            PIGION_VENV_PYTHON="$VENV_PYTHON" \
            PIGION_DEVICE_NAME="$DEVICE_NAME" \
            PIGION_DEVICE_UUID="$DEVICE_UUID" \
            PIGION_SERVER_URL="$SERVER_URL" \
            PIGION_SELECTED_TOOLS={shlex.quote(','.join(device.get('selected_tools', [])))} \
            PIGION_FRAMEWORK={shlex.quote(str(device.get('framework', 'pigion')))} \
            PIGION_RUNTIME={shlex.quote(str(device.get('platform', 'linux')))} \
            PIGION_SERVICE_USER="$SERVICE_USER" \
            PIGION_SERVICE_HOME="$SERVICE_HOME" \
            /bin/bash -c 'set -a; . "$1"; set +a; exec /bin/bash "$2"' framework-lifecycle "$INSTALL_DIR/.env" "$SCRIPT"
        }}
        if [ "$WAS_INSTALLED" -eq 1 ] && {"true" if upgrade_phase else "false"}; then
          run_framework_phase upgrade
        else
          {"run_framework_phase install" if install_phase else ": # no framework install phase"}
        fi
        {"run_framework_phase verify" if verify_phase else ": # no framework verify phase"}

        TMP_UNINSTALL="$TMP_DIR/uninstall.sh"
        cat > "$TMP_UNINSTALL" <<'EOF_UNINSTALL'
#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME={shlex.quote(unit_name)}
INSTALL_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
INSTALL_ROOT="$(dirname "$INSTALL_DIR")"
ENV_FILE="$INSTALL_DIR/.env"
FRAMEWORK_UNINSTALL="$INSTALL_DIR/{e(str(device_name))}/lifecycle/uninstall.sh"

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

if [ -f "$FRAMEWORK_UNINSTALL" ]; then
  run_sudo env PIGION_INSTALL_DIR="$INSTALL_DIR" /bin/bash -c 'set -a; . "$1"; set +a; exec /bin/bash "$2"' framework-uninstall "$ENV_FILE" "$FRAMEWORK_UNINSTALL"
fi

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


def write_macos_install_script(device: dict[str, Any]) -> str:
    device_name = str(device["name"])
    device_uuid = str(device["uuid"])
    label = f"com.pigion.watchdog.{device_name}"
    env_file_body = "\n".join(
        shell_env_assignment(key, value) for key, value in sorted(device_env_values(device).items())
    )
    lifecycle = dict(device.get("lifecycle") or {})
    install_phase = "install" in lifecycle
    upgrade_phase = "upgrade" in lifecycle
    verify_phase = "verify" in lifecycle
    return textwrap.dedent(
        f'''#!/usr/bin/env bash
        set -euo pipefail

        [ "$(uname -s)" = "Darwin" ] || {{ echo "This installer is for macOS." >&2; exit 1; }}
        DEVICE_NAME={shlex.quote(device_name)}
        DEVICE_UUID={shlex.quote(device_uuid)}
        SERVER_URL={shlex.quote(server_url())}
        LABEL={shlex.quote(label)}
        INSTALL_ROOT="${{PIGION_INSTALL_ROOT:-$HOME/Library/Application Support/Pigion}}"
        INSTALL_DIR="$INSTALL_ROOT/$DEVICE_NAME"
        WAS_INSTALLED=0
        [ -d "$INSTALL_DIR" ] && WAS_INSTALLED=1
        PYTHON_BIN="${{PYTHON_BIN:-python3}}"
        VENV_DIR="$INSTALL_DIR/.venv"
        VENV_PYTHON="$VENV_DIR/bin/python"
        LAUNCH_DIR="$HOME/Library/LaunchAgents"
        PLIST="$LAUNCH_DIR/$LABEL.plist"

        command -v curl >/dev/null 2>&1 || {{ echo "curl is required." >&2; exit 1; }}
        if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
          if command -v brew >/dev/null 2>&1; then
            brew install python
          else
            echo "Python 3 is required. Install it manually or install Homebrew first." >&2
            exit 1
          fi
        fi

        TMP_DIR="$(mktemp -d)"
        trap 'rm -rf "$TMP_DIR"' EXIT
        "$PYTHON_BIN" -m venv "$TMP_DIR/venv-check" || {{ echo "Python venv support is required." >&2; exit 1; }}
        rm -rf "$TMP_DIR/venv-check"
        curl -fsSL "$SERVER_URL/install/$DEVICE_UUID/bundle.tgz" -o "$TMP_DIR/pigion-watchdog.tgz"
        curl -fsSL "$SERVER_URL/install/$DEVICE_UUID/client.py" -o "$TMP_DIR/client.py"

        mkdir -p "$INSTALL_DIR" "$LAUNCH_DIR"
        tar -xzf "$TMP_DIR/pigion-watchdog.tgz" -C "$INSTALL_DIR"
        install -m 0644 "$TMP_DIR/client.py" "$INSTALL_DIR/client.py"
        "$PYTHON_BIN" -m venv "$VENV_DIR"
        "$VENV_PYTHON" -m pip install --upgrade pip
        "$VENV_PYTHON" -m pip install -r "$INSTALL_DIR/requirements.txt"

        cat > "$INSTALL_DIR/.env" <<'EOF_ENV'
{env_file_body}
EOF_ENV
        "$PYTHON_BIN" - "$INSTALL_DIR/.env" "$INSTALL_DIR" <<'PY_ENV'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace("__PIGION_INSTALL_DIR__", sys.argv[2]), encoding="utf-8")
PY_ENV
        chmod 600 "$INSTALL_DIR/.env"

        run_framework_phase() {{
          local phase="$1"
          local script="$INSTALL_DIR/$DEVICE_NAME/lifecycle/$phase.sh"
          [ -f "$script" ] || return 0
          env PIGION_INSTALL_DIR="$INSTALL_DIR" PIGION_VENV_PYTHON="$VENV_PYTHON" \
            PIGION_DEVICE_NAME="$DEVICE_NAME" PIGION_DEVICE_UUID="$DEVICE_UUID" \
            PIGION_SERVER_URL="$SERVER_URL" \
            PIGION_SELECTED_TOOLS={shlex.quote(','.join(device.get('selected_tools', [])))} \
            PIGION_FRAMEWORK={shlex.quote(str(device.get('framework', 'pigion')))} \
            PIGION_RUNTIME=macos PIGION_SERVICE_USER="$(id -un)" PIGION_SERVICE_HOME="$HOME" \
            /bin/bash -c 'set -a; . "$1"; set +a; exec /bin/bash "$2"' framework-lifecycle "$INSTALL_DIR/.env" "$script"
        }}
        if [ "$WAS_INSTALLED" -eq 1 ] && {"true" if upgrade_phase else "false"}; then
          run_framework_phase upgrade
        else
          {"run_framework_phase install" if install_phase else ": # no framework install phase"}
        fi
        {"run_framework_phase verify" if verify_phase else ": # no framework verify phase"}

        cat > "$INSTALL_DIR/uninstall.sh" <<'EOF_UNINSTALL'
#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
LABEL={shlex.quote(label)}
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
FRAMEWORK_UNINSTALL="$INSTALL_DIR/{e(device_name)}/lifecycle/uninstall.sh"
if [ -f "$FRAMEWORK_UNINSTALL" ]; then
  set -a; . "$INSTALL_DIR/.env"; set +a
  PIGION_INSTALL_DIR="$INSTALL_DIR" /bin/bash "$FRAMEWORK_UNINSTALL"
fi
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
rm -f "$PLIST"
nohup /bin/bash -c 'sleep 2; rm -rf "$1"; rmdir "$(dirname "$1")" >/dev/null 2>&1 || true' cleanup "$INSTALL_DIR" >/dev/null 2>&1 &
EOF_UNINSTALL
        chmod 0755 "$INSTALL_DIR/uninstall.sh"

        xml_escape() {{ "$PYTHON_BIN" -c 'import html,sys; print(html.escape(sys.argv[1], quote=True))' "$1"; }}
        install_xml="$(xml_escape "$INSTALL_DIR")"
        python_xml="$(xml_escape "$VENV_PYTHON")"
        home_xml="$(xml_escape "$HOME")"
        cat > "$PLIST" <<EOF_PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$LABEL</string>
<key>ProgramArguments</key><array><string>$python_xml</string><string>$install_xml/client.py</string></array>
<key>WorkingDirectory</key><string>$install_xml</string>
<key>EnvironmentVariables</key><dict><key>HOME</key><string>$home_xml</string><key>PYTHONUNBUFFERED</key><string>1</string></dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$install_xml/watchdog.log</string>
<key>StandardErrorPath</key><string>$install_xml/watchdog.err.log</string>
</dict></plist>
EOF_PLIST
        launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST"
        echo "Installed $LABEL"
        echo "Install dir: $INSTALL_DIR"
        echo "Status: launchctl print gui/$(id -u)/$LABEL"
        '''
    ).lstrip().replace("\n        ", "\n")


def write_windows_install_script(device: dict[str, Any]) -> str:
    device_name = str(device["name"])
    device_uuid = str(device["uuid"])
    task_name = f"Pigion_{device_name}"
    env_lines = [dotenv_assignment(key, value) for key, value in sorted(device_env_values(device).items())]
    env_file_body = "\r\n".join(env_lines)
    lifecycle = dict(device.get("lifecycle") or {})
    framework_env_assignments = "\n".join(
        f"            $env:{key} = {powershell_single_quote(value)}"
        for key, value in sorted(device_env_values(device).items())
    )
    if "upgrade" in lifecycle:
        install_script = "install" if "install" in lifecycle else ""
        windows_setup_phase = f'''            if ($WasInstalled) {{
                $PhaseScript = Join-Path $InstallDir "{device_name}\\lifecycle\\upgrade.ps1"
            }} else {{
                $PhaseScript = Join-Path $InstallDir "{device_name}\\lifecycle\\{install_script}.ps1"
            }}
            if ($PhaseScript -and (Test-Path $PhaseScript)) {{ & $PhaseScript; if ($LASTEXITCODE -ne 0) {{ throw "Framework setup failed" }} }}'''
    elif "install" in lifecycle:
        windows_setup_phase = f'''            $PhaseScript = Join-Path $InstallDir "{device_name}\\lifecycle\\install.ps1"
            if (Test-Path $PhaseScript) {{ & $PhaseScript; if ($LASTEXITCODE -ne 0) {{ throw "Framework install failed" }} }}'''
    else:
        windows_setup_phase = ""
    windows_verify_phase = ""
    if "verify" in lifecycle:
        windows_verify_phase = f'''            $PhaseScript = Join-Path $InstallDir "{device_name}\\lifecycle\\verify.ps1"
            if (Test-Path $PhaseScript) {{ & $PhaseScript; if ($LASTEXITCODE -ne 0) {{ throw "Framework verify failed" }} }}'''
    return textwrap.dedent(
        f"""
        $ErrorActionPreference = "Stop"

        $DeviceName = {powershell_single_quote(device_name)}
        $DeviceUuid = {powershell_single_quote(device_uuid)}
        $ServerUrl = {powershell_single_quote(server_url())}
        $TaskName = {powershell_single_quote(task_name)}
        $InstallRoot = if ($env:PIGION_INSTALL_ROOT) {{ $env:PIGION_INSTALL_ROOT }} else {{ Join-Path $env:LOCALAPPDATA "Pigion" }}
        $InstallDir = Join-Path $InstallRoot $DeviceName
        $WasInstalled = Test-Path $InstallDir
        $VenvDir = Join-Path $InstallDir ".venv"
        $VenvPython = Join-Path $VenvDir "Scripts\\python.exe"
        $TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("pigion-" + [System.Guid]::NewGuid().ToString("N"))

        function Write-Utf8NoBom {{
            param([string]$Path, [string]$Text)
            [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
        }}

        function Find-Python {{
            $configured = $env:PYTHON_BIN
            if ($configured -and (Get-Command $configured -ErrorAction SilentlyContinue)) {{ return $configured }}
            foreach ($candidate in @("py", "python", "python3")) {{
                if (Get-Command $candidate -ErrorAction SilentlyContinue) {{ return $candidate }}
            }}
            throw "Python is required but was not found. Install Python 3 and rerun this installer."
        }}

        New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

        try {{
            $BundleZip = Join-Path $TempDir "pigion-watchdog.zip"
            $ClientPy = Join-Path $TempDir "client.py"
            Invoke-WebRequest -UseBasicParsing -Uri "$ServerUrl/install/$DeviceUuid/bundle.zip" -OutFile $BundleZip
            Invoke-WebRequest -UseBasicParsing -Uri "$ServerUrl/install/$DeviceUuid/client.py" -OutFile $ClientPy

            if (Test-Path (Join-Path $TempDir "bundle")) {{ Remove-Item -Recurse -Force (Join-Path $TempDir "bundle") }}
            Expand-Archive -Force -Path $BundleZip -DestinationPath (Join-Path $TempDir "bundle")
            Copy-Item -Recurse -Force (Join-Path $TempDir "bundle\\*") $InstallDir
            Copy-Item -Force $ClientPy (Join-Path $InstallDir "client.py")

            $PythonBin = Find-Python
            & $PythonBin -m venv $VenvDir
            & $VenvPython -m pip install --upgrade pip
            & $VenvPython -m pip install -r (Join-Path $InstallDir "requirements.txt")

            $EnvText = @'
{env_file_body}
'@
            $SafeInstallDir = $InstallDir -replace '"', '\\"'
            $EnvText = $EnvText -replace 'ABS_PATH="__PIGION_INSTALL_DIR__"', ('ABS_PATH="' + $SafeInstallDir + '"')
            Write-Utf8NoBom -Path (Join-Path $InstallDir ".env") -Text ($EnvText.TrimEnd() + "`r`n")

            $env:PIGION_INSTALL_DIR = $InstallDir
            $env:PIGION_VENV_PYTHON = $VenvPython
            $env:PIGION_DEVICE_NAME = $DeviceName
            $env:PIGION_DEVICE_UUID = $DeviceUuid
            $env:PIGION_SERVER_URL = $ServerUrl
            $env:PIGION_SELECTED_TOOLS = {powershell_single_quote(','.join(device.get('selected_tools', [])))}
            $env:PIGION_FRAMEWORK = {powershell_single_quote(str(device.get('framework', 'pigion')))}
            $env:PIGION_RUNTIME = {powershell_single_quote(str(device.get('platform', 'windows')))}
{framework_env_assignments}
{windows_setup_phase}
{windows_verify_phase}

            $MonitorScript = @'
$ErrorActionPreference = "Continue"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $InstallDir ".venv\\Scripts\\python.exe"
$Client = Join-Path $InstallDir "client.py"
Set-Location $InstallDir
while ($true) {{
    & $Python $Client
    Start-Sleep -Seconds 5
}}
'@
            Write-Utf8NoBom -Path (Join-Path $InstallDir "watchdog.ps1") -Text $MonitorScript

            $UninstallScript = @"
$ErrorActionPreference = "Continue"
`$InstallDir = Split-Path -Parent `$MyInvocation.MyCommand.Path
`$TaskName = "$TaskName"
`$FrameworkUninstall = Join-Path `$InstallDir "{device_name}\\lifecycle\\uninstall.ps1"
Start-Sleep -Seconds 5
Unregister-ScheduledTask -TaskName `$TaskName -Confirm:`$false -ErrorAction SilentlyContinue
if (Test-Path `$FrameworkUninstall) {{ & `$FrameworkUninstall }}
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {{ `$_.CommandLine -like "*`$InstallDir*" }} | ForEach-Object {{ Stop-Process -Id `$_.ProcessId -Force -ErrorAction SilentlyContinue }}
`$Parent = Split-Path -Parent `$InstallDir
Remove-Item -Recurse -Force `$InstallDir -ErrorAction SilentlyContinue
if (Test-Path `$Parent) {{
    try {{
        if (-not (Get-ChildItem -Force `$Parent -ErrorAction SilentlyContinue)) {{ Remove-Item -Force `$Parent -ErrorAction SilentlyContinue }}
    }} catch {{}}
}}
"@
            Write-Utf8NoBom -Path (Join-Path $InstallDir "uninstall.ps1") -Text $UninstallScript

            $TaskAction = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\\watchdog.ps1`""
            schtasks.exe /Create /TN $TaskName /TR $TaskAction /SC ONLOGON /F | Out-Host
            schtasks.exe /Run /TN $TaskName | Out-Host

            Write-Host "Installed $TaskName"
            Write-Host "Install dir: $InstallDir"
            Write-Host "Status: schtasks /Query /TN $TaskName"
        }} finally {{
            Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
        }}
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


def build_device_zip_bundle(device: dict[str, Any]) -> bytes:
    runner_path = PROJECT_ROOT / device["runner_path"]
    package_dir = runner_path.parent
    if not package_dir.exists():
        raise HTTPException(status_code=404, detail="Generated watchdog package not found")

    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package_dir.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(package_dir.parent)))
        requirements = PROJECT_ROOT / "requirements.txt"
        if requirements.exists():
            archive.write(requirements, arcname="requirements.txt")
    output.seek(0)
    return output.read()


def refresh_device_availability() -> list[dict[str, Any]]:
    cfg = config()
    timeout = int(cfg.get("heartbeat_timeout_seconds", 60))
    devices_doc = read_json(DEVICES_PATH, {"devices": {}})
    changed = False
    devices = []
    for device in devices_doc.get("devices", {}).values():
        if ensure_device_architecture_profile(device):
            changed = True
        is_down = device_is_down(device, timeout)
        if device.get("is_down") != is_down:
            device["is_down"] = is_down
            changed = True
        devices.append(device)
    if changed:
        write_json(DEVICES_PATH, devices_doc)
        for device in devices:
            update_orchestrator_tool(device)
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
    refresh_device_availability()


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
    runtimes, invalid = valid_framework_runtimes()
    platform_options = [
        f"<option value='{e(framework)}::{e(platform)}'>{e(framework)}/{e(platform)}</option>"
        for framework, platform in runtimes
    ]
    diagnostics = "".join(f"<li><code>{e(key)}</code>: {e(error)}</li>" for key, error in invalid.items())
    return layout(
        "Register Device",
        f"""<form class="panel" method="post" action="/register">
  <h1>Register Device</h1>
  <label>Watchdog name</label><input name="name" placeholder="kitchen_pi" required>
  <label>Framework/runtime template</label><select id="framework_platform" name="framework_platform" required>{''.join(platform_options)}</select>
  <section id="framework_spec"><p class="muted">Loading framework specification...</p></section>
  <label>Device OS</label><input name="device_os" placeholder="Raspberry Pi OS / Ubuntu / Windows / ..." required>
  <label>Device terminal</label><input name="device_terminal" placeholder="bash / zsh / powershell / ..." required>
  <section id="pigion_model_config"><h2>Pigion model configuration</h2>
  <label>Model provider</label><select name="llm_provider" required>
    <option value="gemini">Gemini</option>
    <option value="openai">OpenAI</option>
    <option value="ollama">Ollama</option>
  </select>
  <label>Model name</label><input name="llm_model" value="gemini-2.5-flash-lite" placeholder="gemini-2.5-flash-lite / gpt-4.1-mini / llama3.1" required>
  <label>Model API key</label><input name="api_token" type="password" autocomplete="off" placeholder="Leave blank for Ollama">
  <label>Ollama host</label><input name="ollama_host" placeholder="http://192.168.1.50:11434">
  <label>OpenAI base URL</label><input name="openai_base_url" value="https://api.openai.com/v1">
  </section>
  <label>Sudo password</label><input name="sudo_password" type="password" autocomplete="off">
  <label>Routing summary override (optional)</label><textarea name="capability_block" placeholder="Leave blank to derive the orchestrator summary from the architecture profile."></textarea>
  <label>Device profile additions (optional JSON)</label><textarea name="architecture_profile" placeholder='{{"physical_constraints":["Mounted indoors; cannot move."]}}'></textarea>
  {f'<details><summary>Invalid framework diagnostics</summary><ul>{diagnostics}</ul></details>' if diagnostics else ''}
  <p><button type="submit">Register Device</button></p>
</form>
<script src="/static/framework-registration.js"></script>""",
    )


@app.get("/api/frameworks/{framework}/{platform}", response_class=JSONResponse)
def framework_registration_spec(request: Request, framework: str, platform: str) -> dict[str, Any]:
    require_login(request)
    try:
        return public_framework_spec(validated_framework_manifest(platform, framework))
    except (FrameworkManifestError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/static/framework-registration.js", response_class=PlainTextResponse)
def framework_registration_javascript() -> Response:
    javascript = r"""
const selector = document.getElementById('framework_platform');
const panel = document.getElementById('framework_spec');
function esc(value) { const node = document.createElement('span'); node.textContent = value == null ? '' : value; return node.innerHTML; }
async function loadFrameworkSpec() {
  if (!selector || !selector.value) { panel.innerHTML = '<p>No valid frameworks are installed.</p>'; return; }
  const parts = selector.value.split('::');
  const response = await fetch('/api/frameworks/' + encodeURIComponent(parts[0]) + '/' + encodeURIComponent(parts[1]));
  if (!response.ok) { panel.textContent = await response.text(); return; }
  const spec = await response.json();
  const modelConfig = document.getElementById('pigion_model_config');
  if (modelConfig) {
    modelConfig.hidden = !spec.uses_pigion_model_config;
    modelConfig.querySelectorAll('input, select').forEach(function(control) { control.disabled = !spec.uses_pigion_model_config; });
  }
  let output = '<h2>' + esc(spec.display_name) + ' / ' + esc(spec.runtime) + '</h2><p>' + esc(spec.description) + '</p>';
  output += '<p class="muted">Supported OS: ' + spec.supported_os.map(esc).join(', ') + '. Install lifecycle: ' + (spec.lifecycle.map(esc).join(', ') || 'shared Pigion installer only') + '</p><h3>Tools</h3>';
  const renderedExclusiveGroups = new Set();
  for (const tool of spec.tools) {
    const checked = tool.policy === 'required' || tool.policy === 'default';
    const locked = tool.policy === 'required';
    const inputType = tool.exclusive_group ? 'radio' : 'checkbox';
    const inputName = tool.exclusive_group ? 'selected_tools__' + tool.exclusive_group : 'selected_tools';
    output += '<label><input type="' + inputType + '" name="' + esc(inputName) + '" value="' + esc(tool.name) + '" ' + (checked ? 'checked' : '') + ' ' + (locked ? 'disabled' : '') + '> ' + esc(tool.name) + ' <small>(' + esc(tool.policy) + ')</small></label>';
    if (tool.exclusive_group && !renderedExclusiveGroups.has(tool.exclusive_group)) {
      const groupDefault = spec.tools.find(function(candidate) {
        return candidate.exclusive_group === tool.exclusive_group && (candidate.policy === 'required' || candidate.policy === 'default');
      });
      output += '<input class="exclusive-tool-selection" type="hidden" name="selected_tools" value="' + esc(groupDefault ? groupDefault.name : '') + '" data-group="' + esc(tool.exclusive_group) + '">';
      renderedExclusiveGroups.add(tool.exclusive_group);
    }
    if (locked) output += '<input type="hidden" name="selected_tools" value="' + esc(tool.name) + '">';
    output += '<p class="muted">' + esc(tool.documentation) + '</p>';
  }
  output += '<h3>Framework configuration</h3>';
  if (!spec.questions.length) output += '<p class="muted">No framework-specific configuration.</p>';
  for (const question of spec.questions) {
    const name = 'framework_env__' + question.name;
    output += '<label>' + esc(question.label || question.name) + '</label>';
    if (question.options && question.options.length) {
      output += '<select name="' + esc(name) + '">' + question.options.map(function(option) { return '<option value="' + esc(option) + '" ' + (option === question.default ? 'selected' : '') + '>' + esc(option) + '</option>'; }).join('') + '</select>';
    } else {
      const type = question.secret ? 'password' : (question.type === 'url' ? 'url' : 'text');
      output += '<input name="' + esc(name) + '" type="' + type + '" value="' + esc(question.default || '') + '" ' + (question.required ? 'required' : '') + '>';
    }
    if (question.help) output += '<p class="muted">' + esc(question.help) + '</p>';
  }
  panel.innerHTML = output;
  panel.querySelectorAll('input[type="radio"][name^="selected_tools__"]').forEach(function(control) {
    control.addEventListener('change', function() {
      const group = control.name.substring('selected_tools__'.length);
      const hidden = panel.querySelector('.exclusive-tool-selection[data-group="' + CSS.escape(group) + '"]');
      if (hidden) hidden.value = control.value;
    });
  });
}
if (selector) { selector.addEventListener('change', loadFrameworkSpec); loadFrameworkSpec(); }
"""
    return Response(content=javascript, media_type="application/javascript")


@app.post("/register", response_class=HTMLResponse)
async def register(request: Request) -> str:
    require_login(request)
    raw_body = await request.body()
    data = form_data(raw_body)
    values = form_values(raw_body)
    name = data.get("name", "").strip()
    try:
        framework, platform = parse_framework_platform(
            data.get("framework_platform") or data.get("platform", "")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        manifest = validated_framework_manifest(platform, framework)
    except (FrameworkManifestError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    device_os = data.get("device_os", "").strip()
    device_terminal = data.get("device_terminal", "").strip()
    try:
        provider_value = data.get("llm_provider", "gemini") if manifest.get("uses_pigion_model_config", True) else "ollama"
        llm_provider = normalize_llm_provider(provider_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    llm_model = data.get("llm_model", "").strip() or default_llm_model(llm_provider)
    api_token = data.get("api_token", "").strip()
    ollama_host = data.get("ollama_host", "").strip() or "http://127.0.0.1:11434"
    openai_base_url = data.get("openai_base_url", "").strip() or "https://api.openai.com/v1"
    sudo_password = data.get("sudo_password", "")
    capability_block = data.get("capability_block", "").strip()
    try:
        framework_env = normalize_extra_env(data, framework, platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if manifest.get("uses_pigion_model_config", True) and llm_provider in {"gemini", "openai"} and not api_token:
        raise HTTPException(status_code=400, detail="Model API key is required for Gemini and OpenAI.")
    try:
        selected_tools = validate_tool_selection(values.get("selected_tools", []), platform, framework)
    except (FrameworkManifestError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    selected_specs = {tool["name"]: tool for tool in manifest.get("tools", [])}
    incompatible_tools = [
        name for name in selected_tools
        if selected_specs.get(name, {}).get("requires_provider")
        and selected_specs[name]["requires_provider"] != llm_provider
    ]
    if incompatible_tools:
        requirements = ", ".join(
            f"{name} requires {selected_specs[name]['requires_provider']}" for name in incompatible_tools
        )
        raise HTTPException(status_code=400, detail=f"Selected tool/provider mismatch: {requirements}.")
    raw_profile_overrides = data.get("architecture_profile", "").strip()
    try:
        profile_overrides = json.loads(raw_profile_overrides) if raw_profile_overrides else None
        architecture_profile = device_architecture_profile(
            manifest,
            device_name=name,
            device_os=device_os,
            device_terminal=device_terminal,
            selected_tools=selected_tools,
            overrides=profile_overrides,
        )
    except (json.JSONDecodeError, FrameworkManifestError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid device architecture profile: {exc}") from exc
    environment_text = render_environment(device_os, device_terminal)
    try:
        runner_path = create_instance(
            name,
            platform,
            selected_tools,
            framework=framework,
            environment_text=environment_text,
            framework_env=framework_env,
            architecture_profile=architecture_profile,
            force=True,
        )
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
    installer_suffix = "ps1" if is_windows_platform(platform) else "sh"
    installer_path = installer_dir / f"install_{name}_{short}.{installer_suffix}"
    device = {
        "uuid": device_uuid,
        "name": name,
        "command": command,
        "framework": framework,
        "platform": platform,
        "manifest_schema_version": manifest["schema_version"],
        "framework_revision": manifest["framework_revision"],
        "lifecycle": manifest.get("lifecycle", {}),
        "selected_tools": selected_tools,
        "device_os": device_os,
        "device_terminal": device_terminal,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
        "ollama_host": ollama_host,
        "openai_base_url": openai_base_url,
        "framework_env": framework_env,
        "api_token": api_token,
        "sudo_password": sudo_password,
        "capability_block": capability_block,
        "architecture_profile": architecture_profile,
        "created_at": utc_now(),
        "last_heartbeat": None,
        "last_heartbeat_ts": None,
        "is_down": True,
        "runner_path": str(runner_path.relative_to(PROJECT_ROOT)),
        "runner_module": runner_module,
        "installer_path": str(installer_path),
    }
    if is_windows_platform(platform):
        installer_text = write_windows_install_script(device)
    elif is_macos_platform(platform):
        installer_text = write_macos_install_script(device)
    else:
        installer_text = write_install_script(device)
    installer_path.write_text(installer_text, encoding="utf-8")
    installer_path.chmod(0o755)
    device["installer_command"] = install_command(device_uuid, platform)
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
  <p><strong>Framework:</strong> <code>{e(framework)}</code></p>
  <p><strong>Platform:</strong> <code>{e(platform)}</code></p>
  <p><strong>Model:</strong> <code>{e(llm_provider)} / {e(llm_model)}</code></p>
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
    def mark_started(jobs_doc: dict[str, Any]) -> dict[str, Any] | None:
        job = jobs_doc.get("jobs", {}).get(job_id)
        if not job:
            return None
        job["status"] = "running"
        job["started_at"] = utc_now()
        job["updated_at"] = utc_now()
        return job

    job = update_json(JOBS_PATH, {"jobs": {}}, mark_started)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse({"ok": True, "job": job})


@app.post("/api/jobs/{job_id}/progress")
async def job_progress(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()

    def mark_progress(jobs_doc: dict[str, Any]) -> dict[str, Any] | None:
        job = jobs_doc.get("jobs", {}).get(job_id)
        if not job:
            return None
        job["progress"] = str(payload.get("progress", ""))
        if payload.get("log"):
            job.setdefault("logs", []).append({"at": utc_now(), "message": str(payload["log"])})
        job["updated_at"] = utc_now()
        return job

    job = update_json(JOBS_PATH, {"jobs": {}}, mark_progress)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse({"ok": True, "job": job})


@app.post("/api/jobs/{job_id}/finished")
async def job_finished(job_id: str, request: Request) -> JSONResponse:
    payload = await request.json()

    def mark_finished(jobs_doc: dict[str, Any]) -> dict[str, Any] | None:
        job = jobs_doc.get("jobs", {}).get(job_id)
        if not job:
            return None
        success = bool(payload.get("success", True))
        job["status"] = "finished" if success else "failed"
        job["finished_at"] = utc_now()
        job["updated_at"] = utc_now()
        job["result"] = payload.get("result")
        job["error"] = payload.get("error")
        if payload.get("log"):
            job.setdefault("logs", []).append({"at": utc_now(), "message": str(payload["log"])})
        return job

    job = update_json(JOBS_PATH, {"jobs": {}}, mark_finished)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse({"ok": True, "job": job})


@app.post("/api/device/{device_uuid}/heartbeat")
async def heartbeat(device_uuid: str, request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    reported_profile = payload.get("architecture_profile") if isinstance(payload, dict) else None

    def mark_heartbeat(devices_doc: dict[str, Any]) -> dict[str, Any] | None:
        device = devices_doc.get("devices", {}).get(device_uuid)
        if not device:
            return None
        device["last_heartbeat"] = utc_now()
        device["last_heartbeat_ts"] = now_ts()
        device["is_down"] = False
        if isinstance(reported_profile, dict):
            try:
                # Runtime reports already contain provenance; validation prevents malformed facts.
                reported_facts = {
                    key: value
                    for key, value in reported_profile.items()
                    if key == "summary" or key in PROFILE_FIELDS
                }
                normalized = normalize_architecture_profile(reported_facts, source="watchdog runtime")
                normalized["device"] = reported_profile.get("device", device.get("architecture_profile", {}).get("device", {}))
                device["architecture_profile"] = normalized
            except FrameworkManifestError:
                pass
        return device

    device = update_json(DEVICES_PATH, {"devices": {}}, mark_heartbeat)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
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
    device = get_device_or_404(device_uuid)
    return write_macos_install_script(device) if is_macos_platform(device.get("platform")) else write_install_script(device)


@app.get("/install/{device_uuid}.ps1", response_class=PlainTextResponse)
def generated_windows_install_script(device_uuid: str) -> str:
    return write_windows_install_script(get_device_or_404(device_uuid))


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


@app.get("/install/{device_uuid}/bundle.zip")
def generated_zip_bundle(device_uuid: str) -> StreamingResponse:
    bundle = build_device_zip_bundle(get_device_or_404(device_uuid))
    return StreamingResponse(
        io.BytesIO(bundle),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="pigion-{device_uuid}.zip"'},
    )

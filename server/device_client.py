from __future__ import annotations

import argparse
import importlib
import json
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_runner(runner_module: str):
    module = importlib.import_module(runner_module)
    if hasattr(module, "run_agent"):
        return module.run_agent
    for name in dir(module):
        value = getattr(module, name)
        if callable(value) and name.startswith("run_"):
            return value
    raise RuntimeError(f"No run_agent or run_* callable found in {runner_module}")


def heartbeat_payload(runner_module: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(runner_module)
        profile_path = Path(module.__file__).resolve().parent / "exp" / "architecture_profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        return {"architecture_profile": profile} if isinstance(profile, dict) else {}
    except (AttributeError, OSError, json.JSONDecodeError):
        return {}


def run_client(server: str, device_uuid: str, runner_module: str, poll_seconds: float) -> None:
    server = server.rstrip("/")
    run_agent = load_runner(runner_module)
    while True:
        try:
            request_json("POST", f"{server}/api/device/{device_uuid}/heartbeat", heartbeat_payload(runner_module))
            job = request_json("GET", f"{server}/api/jobs/{device_uuid}").get("job")
            if not job:
                time.sleep(poll_seconds)
                continue

            job_id = job["id"]
            request_json("POST", f"{server}/api/jobs/{job_id}/started", {})
            request_json("POST", f"{server}/api/jobs/{job_id}/progress", {"progress": "running", "log": "Started goal"})
            try:
                result = run_agent(job["goal"])
                request_json(
                    "POST",
                    f"{server}/api/jobs/{job_id}/finished",
                    {"success": True, "result": result, "log": "Finished goal"},
                )
            except Exception as exc:
                request_json(
                    "POST",
                    f"{server}/api/jobs/{job_id}/finished",
                    {
                        "success": False,
                        "error": str(exc),
                        "log": traceback.format_exc(limit=8),
                    },
                )
        except urllib.error.URLError:
            time.sleep(poll_seconds)
        except Exception:
            traceback.print_exc()
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll a Pigion orchestrator for device jobs.")
    parser.add_argument("--server", required=True)
    parser.add_argument("--uuid", required=True)
    parser.add_argument("--runner", required=True, help="Generated Python module containing run_agent, for example workshop.run_workshop")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    run_client(args.server, args.uuid, args.runner, args.poll_seconds)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from server.web_store import ORCHESTRATOR_JOB_TARGET, PROJECT_ROOT


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_progress(server: str, job_id: str, progress: str, log: str | None = None) -> None:
    payload = {"progress": progress}
    if log:
        payload["log"] = log
    request_json("POST", f"{server}/api/jobs/{job_id}/progress", payload)


def run_orchestrator_goal(server: str, job: dict[str, Any], python_bin: str, project_root: Path) -> None:
    job_id = str(job["id"])
    goal = str(job["goal"])
    request_json("POST", f"{server}/api/jobs/{job_id}/started", {})
    post_progress(server, job_id, "running", "Started local orchestrator")

    env = os.environ.copy()
    env.setdefault("ABS_PATH", str(project_root))
    command = [python_bin, "-m", "orchestrator.run_orchestrator", goal]
    output_lines: list[str] = []

    try:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue
            output_lines.append(line)
            output_lines = output_lines[-200:]
            post_progress(server, job_id, "running", line[:2000])

        returncode = process.wait()
        output = "\n".join(output_lines)
        if returncode == 0:
            request_json(
                "POST",
                f"{server}/api/jobs/{job_id}/finished",
                {"success": True, "result": output[-20000:], "log": "Local orchestrator finished goal"},
            )
        else:
            request_json(
                "POST",
                f"{server}/api/jobs/{job_id}/finished",
                {
                    "success": False,
                    "error": f"Local orchestrator exited with code {returncode}",
                    "result": output[-20000:],
                    "log": output[-2000:] or f"Local orchestrator exited with code {returncode}",
                },
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


def run_client(server: str, poll_seconds: float, python_bin: str, project_root: Path) -> None:
    server = server.rstrip("/")
    while True:
        try:
            job = request_json("GET", f"{server}/api/jobs/{ORCHESTRATOR_JOB_TARGET}").get("job")
            if not job:
                time.sleep(poll_seconds)
                continue
            run_orchestrator_goal(server, job, python_bin, project_root)
        except urllib.error.URLError:
            time.sleep(poll_seconds)
        except Exception:
            traceback.print_exc()
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll the Pigion webserver for local orchestrator goals.")
    parser.add_argument("--server", default=os.environ.get("PIGION_SERVER_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("PIGION_ORCHESTRATOR_POLL_SECONDS", "5")))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    args = parser.parse_args()
    run_client(args.server, args.poll_seconds, args.python, Path(args.project_root).resolve())


if __name__ == "__main__":
    main()

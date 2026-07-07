from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback for local development.
    fcntl = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_ROOT = PROJECT_ROOT / "orchestrator"
SERVER_ROOT = PROJECT_ROOT / "server"
DATA_DIR = Path(os.environ.get("PIGION_ORCHESTRATOR_DATA", SERVER_ROOT))
ORCHESTRATOR_JOB_TARGET = "__orchestrator__"
ORCHESTRATOR_DISPLAY_NAME = "Local Orchestrator"

DEVICES_PATH = DATA_DIR / "devices.json"
JOBS_PATH = DATA_DIR / "jobs.json"
CONFIG_PATH = DATA_DIR / "config.json"
SESSIONS_PATH = DATA_DIR / "sessions.json"

_LOCK = threading.RLock()
T = TypeVar("T")


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_json_unlocked(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> float:
    return time.time()


def read_json(path: Path, default: Any) -> Any:
    with _LOCK:
        with file_lock(path):
            if not path.exists():
                write_json_unlocked(path, default)
                return default
            try:
                with path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return default


def write_json(path: Path, data: Any) -> None:
    with _LOCK:
        with file_lock(path):
            write_json_unlocked(path, data)


def update_json(path: Path, default: Any, updater: Callable[[Any], T]) -> T:
    with _LOCK:
        with file_lock(path):
            if not path.exists():
                data = default
            else:
                try:
                    with path.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                except json.JSONDecodeError:
                    data = default
            result = updater(data)
            write_json_unlocked(path, data)
            return result


def default_config() -> dict[str, Any]:
    password = os.environ.get("PIGION_ORCHESTRATOR_PASSWORD", "pigion")
    return {
        "admin_username": os.environ.get("PIGION_ORCHESTRATOR_USER", "admin"),
        "admin_password_hash": hash_password(password),
        "heartbeat_timeout_seconds": int(os.environ.get("PIGION_HEARTBEAT_TIMEOUT", "60")),
        "session_ttl_seconds": int(os.environ.get("PIGION_SESSION_TTL", "86400")),
        "server_url": os.environ.get("PIGION_SERVER_URL", "http://127.0.0.1:8000"),
    }


def ensure_files() -> None:
    read_json(DEVICES_PATH, {"devices": {}})
    read_json(JOBS_PATH, {"jobs": {}})
    read_json(CONFIG_PATH, default_config())
    read_json(SESSIONS_PATH, {"sessions": {}})


def hash_password(password: str) -> str:
    import hashlib

    salt = os.environ.get("PIGION_PASSWORD_SALT", "pigion-local-orchestrator")
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def create_job(
    device_uuid: str,
    goal: str,
    source: str = "dashboard",
    *,
    kind: str = "goal",
) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "device_uuid": device_uuid,
        "goal": goal,
        "source": source,
        "kind": kind,
        "status": "waiting",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "progress": "",
        "result": None,
        "error": None,
        "logs": [],
    }

    def add_job(jobs_doc: dict[str, Any]) -> dict[str, Any]:
        jobs_doc.setdefault("jobs", {})[job_id] = job
        return job

    update_json(JOBS_PATH, {"jobs": {}}, add_job)
    return job


def append_job_log(job_id: str, message: str) -> dict[str, Any] | None:
    def append_log(jobs_doc: dict[str, Any]) -> dict[str, Any] | None:
        job = jobs_doc.get("jobs", {}).get(job_id)
        if not job:
            return None
        job.setdefault("logs", []).append({"at": utc_now(), "message": message})
        job["updated_at"] = utc_now()
        return job

    return update_json(JOBS_PATH, {"jobs": {}}, append_log)


def get_job(job_id: str) -> dict[str, Any] | None:
    jobs_doc = read_json(JOBS_PATH, {"jobs": {}})
    return jobs_doc.get("jobs", {}).get(job_id)


def wait_for_job(job_id: str, timeout_seconds: float = 900.0, poll_seconds: float = 1.0) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_job: dict[str, Any] | None = None
    while time.time() < deadline:
        job = get_job(job_id)
        if job:
            last_job = job
            if job.get("status") in {"finished", "failed"}:
                return job
        time.sleep(poll_seconds)

    if last_job:
        return {
            **last_job,
            "status": "timeout",
            "error": f"Timed out waiting for device job {job_id} after {timeout_seconds:g} seconds.",
        }
    return {
        "id": job_id,
        "status": "timeout",
        "error": f"Timed out waiting for device job {job_id}; job record was not found.",
    }

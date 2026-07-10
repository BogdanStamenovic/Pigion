from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


MEMORY_PATH_ENV = "PIGION_PERMAMEMORY_PATH"


def _default_memory_path() -> Path:
    package_dir = Path(__file__).resolve().parents[1]
    return package_dir / "exp" / "permamemory.json"


def _memory_path() -> Path:
    configured = os.environ.get(MEMORY_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return _default_memory_path()


def _load_store(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Permanent memory file is not valid JSON: {path}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Permanent memory file must contain a JSON object: {path}")

    return {str(key): str(value) for key, value in raw.items()}


def _save_store(path: Path, store: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp.write("\n")
        tmp_path = Path(tmp.name)

    tmp_path.replace(path)


def _split_key_value(text: str) -> tuple[str, str]:
    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        raise ValueError("Permanent memory key cannot be empty.")
    return key, value


def _format_list(store: dict[str, str]) -> str:
    if not store:
        return "PERMANENT_MEMORY_EMPTY"
    return "\n".join(f"{key}={value}" for key, value in sorted(store.items()))


def _finish(
    *,
    ok: bool,
    output: str,
    memory: str,
    local_state: dict[str, Any],
    program_state: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    local_state["last_tool_output"] = output
    result = {
        "ok": ok,
        "output": output,
        "memory": memory,
        "state": local_state,
        "program_state": program_state,
        "interactive_mode": False,
        "completed": True,
    }
    if error is not None:
        result["error"] = error
    return result


def permamemory(command, memory, local_state, program_state):
    local_state = dict(local_state)
    program_state = dict(program_state or {})
    command = str(command or "").strip()
    path = _memory_path()

    try:
        store = _load_store(path)

        if not command or command.lower() in {"list", "all", "show"}:
            output = _format_list(store)
        else:
            parts = command.split(maxsplit=1)
            action = parts[0].strip().lower()
            rest = parts[1].strip() if len(parts) > 1 else ""

            if action in {"get", "read", "retrieve"}:
                if not rest:
                    raise ValueError("Usage: permamemory:get KEY")
                if rest not in store:
                    output = f"PERMANENT_MEMORY_KEY_NOT_FOUND: {rest}"
                else:
                    output = store[rest]
                    local_state["last_permanent_memory"] = {rest: store[rest]}
            elif action in {"set", "add", "put", "store"}:
                if "=" not in rest:
                    raise ValueError("Usage: permamemory:set KEY=VALUE")
                key, value = _split_key_value(rest)
                store[key] = value
                _save_store(path, store)
                output = f"PERMANENT_MEMORY_STORED: {key}"
                local_state["last_permanent_memory"] = {key: value}
            elif action in {"delete", "del", "remove", "forget"}:
                if not rest:
                    raise ValueError("Usage: permamemory:delete KEY")
                existed = rest in store
                store.pop(rest, None)
                if existed:
                    _save_store(path, store)
                    output = f"PERMANENT_MEMORY_DELETED: {rest}"
                else:
                    output = f"PERMANENT_MEMORY_KEY_NOT_FOUND: {rest}"
            elif action == "clear":
                store = {}
                _save_store(path, store)
                output = "PERMANENT_MEMORY_CLEARED"
            elif "=" in command:
                key, value = _split_key_value(command)
                store[key] = value
                _save_store(path, store)
                output = f"PERMANENT_MEMORY_STORED: {key}"
                local_state["last_permanent_memory"] = {key: value}
            else:
                key = command
                if key not in store:
                    output = f"PERMANENT_MEMORY_KEY_NOT_FOUND: {key}"
                else:
                    output = store[key]
                    local_state["last_permanent_memory"] = {key: store[key]}
    except OSError as exc:
        output = f"PERMANENT_MEMORY_ERROR: {type(exc).__name__}: {exc}"
        return _finish(
            ok=False,
            output=output,
            memory=memory,
            local_state=local_state,
            program_state=program_state,
            error=output,
        )
    except ValueError as exc:
        output = f"PERMANENT_MEMORY_ERROR: {exc}"
        return _finish(
            ok=False,
            output=output,
            memory=memory,
            local_state=local_state,
            program_state=program_state,
            error=output,
        )

    local_state["PERMANENT_MEMORY_KEYS"] = sorted(store.keys())
    program_state["permamemory_path"] = str(path)
    return _finish(
        ok=True,
        output=output,
        memory=memory,
        local_state=local_state,
        program_state=program_state,
    )

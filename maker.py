from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
PLATFORMS_ROOT = PROJECT_ROOT / "platforms"
DEFAULT_FRAMEWORK = "pigion"
TOOL_ORDER = ("shell", "memadd", "search", "permamemory", "return", "askuser")

TOOL_DOCS = {
    "shell": 'Shell, Description: Executes a shell command and returns stdout/stderr, Command - shell:COMMAND, Example - shell:echo "hi"',
    "memadd": "Memory, Description: Stores temporary context memory for the current session (not persistent), Command - memadd:TEXT_OR_KEY=VALUE, Example - memadd:User prefers Python",
    "permamemory": "PermanentMemory, Description: Stores and retrieves permanent key-value memories across sessions, Command - permamemory:ACTION_OR_KEY=VALUE, Example - permamemory:set user_name=Bogdan or permamemory:get user_name or permamemory:list",
    "search": "Search, Description: Performs web search for queries or extracts text content from a URL, Command - search:QUERY_OR_URL, Example - search:openai api or search:https://example.com",
    "return": "Return, Description: Returns something back to the user at the end of the goal if needed, Command - return:TEXT, Example - return:The task has been completed succesfully",
    "askuser": "AskUser, Description: Asks the user for information and returns the user answer, Command - askuser:TEXT, Example - askuser:Can you provide your location?",
}

TOOL_MODULE_NAMES = {
    "return": "return_value.py",
}

MANIFEST_SCHEMA_VERSION = 1
TOOL_POLICIES = {"required", "default", "optional"}
LIFECYCLE_PHASES = {"install", "verify", "upgrade", "uninstall"}
PROFILE_FIELDS = (
    "capabilities",
    "unavailable_actions",
    "observable_state",
    "required_dependencies",
    "privilege_boundaries",
    "physical_constraints",
    "known_failure_modes",
    "uncertainty",
)
PROFILE_PROVENANCE = {"declared", "observed", "inferred"}


class FrameworkManifestError(ValueError):
    """Raised when a framework/runtime manifest is unsafe or invalid."""


def normalize_architecture_profile(raw_profile: object, *, source: str) -> dict:
    """Validate a profile and normalize every fact to a provenance-bearing object."""
    if raw_profile is None:
        raw_profile = {}
    if not isinstance(raw_profile, dict):
        raise FrameworkManifestError("architecture_profile must be an object.")
    unknown = set(raw_profile) - ({"summary"} | set(PROFILE_FIELDS))
    if unknown:
        raise FrameworkManifestError(
            "architecture_profile has unknown fields: " + ", ".join(sorted(unknown))
        )

    normalized: dict[str, object] = {"summary": str(raw_profile.get("summary") or "").strip()}
    for field in PROFILE_FIELDS:
        values = raw_profile.get(field, [])
        if not isinstance(values, list):
            raise FrameworkManifestError(f"architecture_profile.{field} must be a list.")
        entries: list[dict[str, str]] = []
        for index, value in enumerate(values):
            if isinstance(value, str):
                statement = value.strip()
                provenance = "declared"
                entry_source = source
            elif isinstance(value, dict):
                statement = str(value.get("statement") or "").strip()
                provenance = str(value.get("provenance") or "declared").strip().lower()
                entry_source = str(value.get("source") or source).strip()
                extra = set(value) - {"statement", "provenance", "source", "observed_at", "match_terms"}
                if extra:
                    raise FrameworkManifestError(
                        f"architecture_profile.{field}[{index}] has unknown fields: "
                        + ", ".join(sorted(extra))
                    )
            else:
                raise FrameworkManifestError(
                    f"architecture_profile.{field}[{index}] must be a string or object."
                )
            if not statement:
                raise FrameworkManifestError(
                    f"architecture_profile.{field}[{index}] needs a non-empty statement."
                )
            if provenance not in PROFILE_PROVENANCE:
                raise FrameworkManifestError(
                    f"architecture_profile.{field}[{index}] has invalid provenance {provenance!r}."
                )
            entry = {"statement": statement, "provenance": provenance, "source": entry_source}
            if isinstance(value, dict) and value.get("observed_at"):
                entry["observed_at"] = str(value["observed_at"])
            if isinstance(value, dict) and "match_terms" in value:
                match_terms = value["match_terms"]
                if not isinstance(match_terms, list) or not match_terms or not all(
                    isinstance(term, str) and term.strip() for term in match_terms
                ):
                    raise FrameworkManifestError(
                        f"architecture_profile.{field}[{index}].match_terms must be a non-empty list of strings."
                    )
                entry["match_terms"] = [term.strip().lower() for term in match_terms]
            entries.append(entry)
        normalized[field] = entries
    return normalized


def device_architecture_profile(
    manifest: dict,
    *,
    device_name: str,
    device_os: str,
    device_terminal: str,
    selected_tools: list[str],
    overrides: object = None,
) -> dict:
    """Build the concrete local profile bundled with a registered watchdog."""
    profile = normalize_architecture_profile(
        manifest.get("architecture_profile"), source="framework manifest"
    )
    if overrides:
        additions = normalize_architecture_profile(overrides, source="device registration")
        if additions["summary"]:
            profile["summary"] = additions["summary"]
        for field in PROFILE_FIELDS:
            profile[field].extend(additions[field])

    profile["device"] = {
        "name": device_name,
        "framework": str(manifest["framework"]),
        "runtime": str(manifest["runtime"]),
        "operating_system": device_os,
        "terminal": device_terminal,
        "selected_tools": list(selected_tools),
    }
    profile["observable_state"].append(
        {
            "statement": f"Operating-system state observable through the selected tools: {', '.join(selected_tools) or 'none'}.",
            "provenance": "declared",
            "source": "device registration",
        }
    )
    return profile


def concise_architecture_profile(profile: object) -> str:
    """Render bounded routing context without exposing the watchdog's full internal state."""
    if not isinstance(profile, dict):
        return "No architecture profile reported."
    parts: list[str] = []
    summary = " ".join(str(profile.get("summary") or "").split())
    if summary:
        parts.append(summary)
    labels = {
        "capabilities": "Can",
        "unavailable_actions": "Cannot",
        "observable_state": "Observes",
        "required_dependencies": "Needs",
        "privilege_boundaries": "Privileges",
        "physical_constraints": "Physical limits",
        "known_failure_modes": "Known failures",
        "uncertainty": "Uncertain",
    }
    for field in PROFILE_FIELDS:
        statements = []
        for entry in profile.get(field, []) if isinstance(profile.get(field, []), list) else []:
            statement = entry.get("statement") if isinstance(entry, dict) else entry
            if statement:
                statements.append(" ".join(str(statement).split()))
        if statements:
            parts.append(f"{labels[field]}: {'; '.join(statements[:4])}")
    return " | ".join(parts)[:4000] or "No architecture profile reported."


def routing_architecture_profile(profile: object) -> dict:
    """Return the bounded, provenance-bearing subset an orchestrator needs for routing."""
    if not isinstance(profile, dict):
        return {}
    routed: dict[str, object] = {
        "summary": " ".join(str(profile.get("summary") or "").split()),
    }
    device = profile.get("device")
    if isinstance(device, dict):
        routed["device"] = {
            key: device.get(key)
            for key in ("name", "framework", "runtime", "operating_system", "selected_tools")
            if key in device
        }
    for field in PROFILE_FIELDS:
        entries = profile.get(field, [])
        if isinstance(entries, list) and entries:
            routed[field] = entries[:4]
    return routed


def discover_tools() -> list[str]:
    tools: set[str] = set()
    for framework in discover_frameworks():
        for platform in discover_platforms(framework):
            tools.update(discover_tools_for_platform(platform, framework))
    return sort_tools(tools)


def discover_tools_for_platform(platform: str, framework: str | None = None) -> list[str]:
    tools: list[str] = []
    seen: set[str] = set()
    for line in platform_tool_doc_lines(platform, framework):
        tool = extract_tool_name_from_doc(line)
        if tool and tool not in seen:
            tools.append(tool)
            seen.add(tool)

    tools_dir = platform_tools_dir(platform, framework)
    if not tools_dir.exists():
        return tools

    for path in tools_dir.glob("*.py"):
        name = path.stem
        if name == "__init__":
            continue
        if name == "return_value":
            name = "return"
        if name not in seen:
            tools.append(name)
            seen.add(name)
    return tools


def sort_tools(tools: set[str]) -> list[str]:
    return sorted(
        tools,
        key=lambda tool: (
            TOOL_ORDER.index(tool) if tool in TOOL_ORDER else len(TOOL_ORDER),
            tool,
        ),
    )


def normalize_framework(framework: str | None) -> str:
    framework = (framework or DEFAULT_FRAMEWORK).strip().lower()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", framework):
        raise ValueError(
            "Framework name must be a valid Python package-style name: letters, "
            "numbers, and underscores only, and it cannot start with a number."
        )
    return framework


def discover_frameworks() -> list[str]:
    if not PLATFORMS_ROOT.exists():
        return []
    frameworks: set[str] = set()
    for path in PLATFORMS_ROOT.iterdir():
        if not path.is_dir():
            continue
        child_platforms = any((child / "tools").is_dir() for child in path.iterdir() if child.is_dir())
        if (path / "core").is_dir() or child_platforms:
            frameworks.add(path.name)
    return sorted(frameworks)


def discover_platforms(framework: str | None = None) -> list[str]:
    framework_dir = PLATFORMS_ROOT / normalize_framework(framework)
    if not framework_dir.exists():
        return []
    return sorted(
        path.name
        for path in framework_dir.iterdir()
        if path.is_dir() and ((path / "tools").is_dir() or (path / "framework.json").is_file())
    )


def framework_root(framework: str | None = None) -> Path:
    return PLATFORMS_ROOT / normalize_framework(framework)


def platform_root(platform: str, framework: str | None = None) -> Path:
    return framework_root(framework) / platform


def runner_template_path(framework: str | None = None) -> Path:
    path = framework_root(framework) / "core" / "whatchdog.py"
    if not path.exists():
        raise FileNotFoundError(f"Framework runner template not found: {path}")
    return path


def platform_tools_dir(platform: str, framework: str | None = None) -> Path:
    return platform_root(platform, framework) / "tools"


def platform_td_path(platform: str, framework: str | None = None) -> Path:
    return platform_root(platform, framework) / "exp" / "td.txt"


def framework_metadata_path(platform: str, framework: str | None = None) -> Path:
    return platform_root(platform, framework) / "framework.json"


def framework_metadata(platform: str, framework: str | None = None) -> dict:
    path = framework_metadata_path(platform, framework)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_runtime_file(runtime_root: Path, raw_path: str, label: str) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise FrameworkManifestError(f"{label} must be a relative path inside {runtime_root}.")
    resolved_root = runtime_root.resolve()
    resolved = (runtime_root / relative).resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise FrameworkManifestError(f"{label} escapes framework runtime directory.")
    if not resolved.is_file():
        raise FrameworkManifestError(f"{label} does not exist: {resolved}")
    return resolved


def validated_framework_manifest(platform: str, framework: str | None = None) -> dict:
    framework = normalize_framework(framework)
    runtime_root = platform_root(platform, framework)
    path = framework_metadata_path(platform, framework)
    if not path.is_file():
        raise FrameworkManifestError(f"Framework manifest not found: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrameworkManifestError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise FrameworkManifestError(f"{path} must use schema_version {MANIFEST_SCHEMA_VERSION}.")
    if str(manifest.get("framework") or framework) != framework:
        raise FrameworkManifestError(f"Manifest framework must be {framework!r}.")
    if str(manifest.get("runtime") or platform) != platform:
        raise FrameworkManifestError(f"Manifest runtime must be {platform!r}.")
    if not isinstance(manifest.get("uses_pigion_model_config", True), bool):
        raise FrameworkManifestError("uses_pigion_model_config must be boolean.")
    architecture_profile = normalize_architecture_profile(
        manifest.get("architecture_profile"), source="framework manifest"
    )
    supported_os = manifest.get("supported_os")
    if not isinstance(supported_os, list) or not supported_os or not all(isinstance(x, str) and x for x in supported_os):
        raise FrameworkManifestError("supported_os must be a non-empty list of strings.")
    installer_os = (
        "windows" if platform in {"windows", "win", "win32"}
        else "macos" if platform in {"macos", "darwin", "osx"}
        else "linux"
    )
    if installer_os not in {item.strip().lower() for item in supported_os}:
        raise FrameworkManifestError(
            f"Runtime {platform!r} uses the {installer_os} installer but supported_os does not include {installer_os!r}."
        )
    source_runtime = str(manifest.get("source_runtime") or platform).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", source_runtime):
        raise FrameworkManifestError("source_runtime must name another runtime in the same framework.")
    source_runtime_root = platform_root(source_runtime, framework)
    if not source_runtime_root.is_dir():
        raise FrameworkManifestError(f"source_runtime does not exist: {source_runtime!r}.")

    runner = str(manifest.get("runner") or "../core/whatchdog.py")
    runner_relative = Path(runner)
    runner_path = (runtime_root / runner_relative).resolve()
    resolved_framework_root = framework_root(framework).resolve()
    if runner_relative.is_absolute() or runner_path == resolved_framework_root or resolved_framework_root not in runner_path.parents or not runner_path.is_file():
        raise FrameworkManifestError("runner must resolve to a file inside the framework directory.")

    tools = manifest.get("tools")
    if not isinstance(tools, list):
        raise FrameworkManifestError("tools must be a list.")
    seen: set[str] = set()
    normalized_tools: list[dict] = []
    for index, item in enumerate(tools):
        if not isinstance(item, dict):
            raise FrameworkManifestError(f"tools[{index}] must be an object.")
        name = str(item.get("name") or "").strip().lower()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in seen:
            raise FrameworkManifestError(f"Invalid or duplicate tool name: {name!r}.")
        policy = str(item.get("policy") or "").strip().lower()
        if policy not in TOOL_POLICIES:
            raise FrameworkManifestError(f"Tool {name!r} has invalid policy {policy!r}.")
        module = str(item.get("module") or TOOL_MODULE_NAMES.get(name, f"{name}.py"))
        _safe_runtime_file(source_runtime_root, f"tools/{module}", f"tool {name}")
        documentation = str(item.get("documentation") or "").strip()
        if not documentation or extract_tool_name_from_doc(documentation) != name:
            raise FrameworkManifestError(f"Tool {name!r} needs documentation with Command - {name}:...")
        normalized_tools.append({**item, "name": name, "policy": policy, "module": module, "documentation": documentation})
        seen.add(name)
    if not manifest.get("allow_zero_tools", False) and not tools:
        raise FrameworkManifestError("At least one tool is required when allow_zero_tools is false.")

    questions = manifest.get("questions", [])
    if not isinstance(questions, list):
        raise FrameworkManifestError("questions must be a list.")
    question_names: set[str] = set()
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise FrameworkManifestError(f"questions[{index}] must be an object.")
        name = str(question.get("name") or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in question_names:
            raise FrameworkManifestError(f"Invalid or duplicate question name: {name!r}.")
        if question.get("type", "text") not in {"text", "password", "select", "url"}:
            raise FrameworkManifestError(f"Question {name!r} has an invalid type.")
        options = question.get("options")
        if options is not None and (not isinstance(options, list) or not all(isinstance(x, str) for x in options)):
            raise FrameworkManifestError(f"Question {name!r} options must be strings.")
        validation = question.get("validation", {})
        if not isinstance(validation, dict) or set(validation) - {"pattern", "min_length", "max_length"}:
            raise FrameworkManifestError(f"Question {name!r} has invalid validation fields.")
        if "pattern" in validation:
            try:
                re.compile(str(validation["pattern"]))
            except re.error as exc:
                raise FrameworkManifestError(f"Question {name!r} has an invalid validation pattern.") from exc
        for length_key in ("min_length", "max_length"):
            if length_key in validation and (not isinstance(validation[length_key], int) or validation[length_key] < 0):
                raise FrameworkManifestError(f"Question {name!r} {length_key} must be a non-negative integer.")
        if validation.get("min_length", 0) > validation.get("max_length", 2**31):
            raise FrameworkManifestError(f"Question {name!r} minimum length exceeds maximum length.")
        question_names.add(name)

    lifecycle = manifest.get("lifecycle", {})
    if not isinstance(lifecycle, dict) or set(lifecycle) - LIFECYCLE_PHASES:
        raise FrameworkManifestError("lifecycle may contain only install, verify, upgrade, and uninstall.")
    normalized_lifecycle: dict[str, str] = {}
    for phase, script in lifecycle.items():
        if not isinstance(script, str) or not script:
            raise FrameworkManifestError(f"Lifecycle {phase!r} must be a script path.")
        source = _safe_runtime_file(source_runtime_root, script, f"lifecycle {phase}")
        normalized_lifecycle[phase] = str(source.relative_to(source_runtime_root))

    raw = path.read_bytes()
    revision = hashlib.sha256(raw)
    revision.update(runner_path.read_bytes())
    for tool in normalized_tools:
        revision.update(tool["name"].encode("utf-8"))
        revision.update(_safe_runtime_file(source_runtime_root, f"tools/{tool['module']}", f"tool {tool['name']}").read_bytes())
    for phase, relative in sorted(normalized_lifecycle.items()):
        revision.update(phase.encode("utf-8"))
        revision.update(_safe_runtime_file(source_runtime_root, relative, f"lifecycle {phase}").read_bytes())
    return {
        **manifest,
        "framework": framework,
        "runtime": platform,
        "source_runtime": source_runtime,
        "runner": runner,
        "tools": normalized_tools,
        "questions": questions,
        "lifecycle": normalized_lifecycle,
        "architecture_profile": architecture_profile,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "framework_revision": revision.hexdigest(),
    }


def valid_framework_runtimes() -> tuple[list[tuple[str, str]], dict[str, str]]:
    valid: list[tuple[str, str]] = []
    invalid: dict[str, str] = {}
    for framework in discover_frameworks():
        for platform in discover_platforms(framework):
            key = f"{framework}/{platform}"
            try:
                validated_framework_manifest(platform, framework)
            except FrameworkManifestError as exc:
                invalid[key] = str(exc)
            else:
                valid.append((framework, platform))
    return valid, invalid


def manifest_tools(platform: str, framework: str | None = None) -> list[dict]:
    return list(validated_framework_manifest(platform, framework)["tools"])


def default_tools_for_platform(platform: str, framework: str | None = None) -> list[str]:
    return [tool["name"] for tool in manifest_tools(platform, framework) if tool["policy"] in {"required", "default"}]


def validate_tool_selection(selected: list[str], platform: str, framework: str | None = None) -> list[str]:
    manifest = validated_framework_manifest(platform, framework)
    specs = {tool["name"]: tool for tool in manifest["tools"]}
    requested = list(dict.fromkeys(str(name).strip().lower() for name in selected if str(name).strip()))
    unknown = [name for name in requested if name not in specs]
    if unknown:
        raise ValueError(f"Unknown tool(s): {', '.join(unknown)}")
    required = [tool["name"] for tool in manifest["tools"] if tool["policy"] == "required"]
    selected_set = set(requested) | set(required)
    ordered = [tool["name"] for tool in manifest["tools"] if tool["name"] in selected_set]
    if not ordered and not manifest.get("allow_zero_tools", False):
        raise ValueError(f"At least one tool must be selected for {manifest['framework']}/{platform}.")
    return ordered


def framework_env_questions(platform: str, framework: str | None = None) -> list[dict]:
    return list(validated_framework_manifest(platform, framework).get("questions", []))


def platform_tool_doc_lines(platform: str, framework: str | None = None) -> list[str]:
    path = platform_td_path(platform, framework)
    if not path.exists():
        return []
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def extract_tool_name_from_doc(line: str) -> str | None:
    stripped = re.sub(r"^\s*\d+\.\s*", "", line)
    parts = [part.strip() for part in stripped.split(",")]
    if len(parts) >= 3:
        third = parts[2]
        if " - " in third:
            after = third.split(" - ", 1)[1]
            tool = after.split(":", 1)[0].strip()
            return tool or None

    match = re.search(r"Command\s*-\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", line)
    if match:
        return match.group(1)
    return None


def validate_instance_name(name: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(
            "Instance name must be a valid Python package name: letters, numbers, "
            "and underscores only, and it cannot start with a number."
        )
    if name in {"maker", "__pycache__"}:
        raise ValueError(f"Instance name '{name}' is reserved.")
    return name


def choose_numbered(label: str, options: list[str], default: str | None = None) -> str:
    default_index = options.index(default) + 1 if default in options else None
    print(f"\n{label}:")
    for index, option in enumerate(options, start=1):
        suffix = " (default)" if index == default_index else ""
        print(f"  {index}. {option}{suffix}")

    while True:
        prompt = "Choose number"
        if default_index is not None:
            prompt += f" [{default_index}]"
        prompt += ": "
        value = input(prompt).strip()
        if value == "" and default_index is not None:
            return options[default_index - 1]
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1]
        if value in options:
            return value
        print("Please choose one of the listed options.")


def normalize_tools(raw: str | None, available_tools: list[str]) -> list[str]:
    if raw is None or raw.strip() == "":
        return list(available_tools)

    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if any(item in {"all", "*"} for item in requested):
        return list(available_tools)

    available = set(available_tools)
    unknown = sorted(set(requested) - available)
    if unknown:
        raise ValueError(
            "Unknown tool(s): "
            + ", ".join(unknown)
            + ". Available tools: "
            + ", ".join(available_tools)
        )

    requested_set = set(requested)
    return [tool for tool in available_tools if tool in requested_set]


def prompt_value(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    if value == "" and default is not None:
        return default
    return value


def prompt_framework(available_frameworks: list[str], default: str = DEFAULT_FRAMEWORK) -> str:
    default = default if default in available_frameworks else available_frameworks[0]
    return choose_numbered("Available frameworks", available_frameworks, default)


def prompt_platform(available_platforms: list[str], default: str = "linux") -> str:
    default = default if default in available_platforms else available_platforms[0]
    return choose_numbered("Available platforms", available_platforms, default)


def prompt_tools(available_tools: list[str]) -> list[str]:
    print("\nAvailable tools:")
    for index, tool in enumerate(available_tools, start=1):
        print(f"  {index}. {tool}")

    print("\nSelect tools by number or name.")
    print("Examples: all | 1,3,5 | shell,search")
    raw = prompt_value("Tools to include", "all")
    if raw.strip().lower() in {"all", "*", ""}:
        return list(available_tools)

    selected: list[str] = []
    for item in [part.strip().lower() for part in raw.split(",") if part.strip()]:
        if item.isdigit():
            index = int(item)
            if not 1 <= index <= len(available_tools):
                raise ValueError(f"Tool number out of range: {item}")
            selected.append(available_tools[index - 1])
        else:
            selected.append(item)

    return normalize_tools(",".join(selected), available_tools)


def render_tool_docs(
    selected_tools: list[str],
    platform: str | None = None,
    framework: str | None = None,
) -> str:
    """Build `exp/td.txt` content.

    Prefer a platform-provided `platform/exp/td.txt` if present, then ensure
    every `selected_tools` entry exists (append missing docs). Falls back to
    the in-module `TOOL_DOCS` mapping for missing templates.
    """
    base_lines: list[str] = []

    # Try to load platform-provided td.txt first
    if platform:
        try:
            base_lines = [item["documentation"] for item in manifest_tools(platform, framework)]
        except FrameworkManifestError:
            base_lines = platform_tool_doc_lines(platform, framework)

    # Build a mapping of existing tool -> doc (keep first occurrence)
    existing: dict[str, str] = {}
    selected_set = set(selected_tools)
    for ln in base_lines:
        doc_text = re.sub(r"^\s*\d+\.\s*", "", ln)
        tool = extract_tool_name_from_doc(ln)
        if tool and tool in selected_set and (tool not in existing):
            existing[tool] = doc_text

    # Append missing selected tools
    for tool in selected_tools:
        if tool in existing:
            continue
        module_name = TOOL_MODULE_NAMES.get(tool, f"{tool}.py")
        command_name = "return" if tool == "return" else Path(module_name).stem
        doc = TOOL_DOCS.get(tool)
        if doc is None:
            doc = f"{tool.title()}, Description: Custom tool, Command - {command_name}:TEXT, Example - {command_name}:hello"
        existing[tool] = doc

    # Re-order: prefer order of existing docs from platform, then follow `selected_tools` order
    final_docs: list[str] = []
    # Add docs in the order they appeared in the platform file
    for ln in base_lines:
        tool = extract_tool_name_from_doc(ln)
        if tool and tool in selected_set and tool in existing:
            final_docs.append(existing.pop(tool))

    # Add any remaining tools in the requested order
    for tool in selected_tools:
        if tool in existing:
            final_docs.append(existing.pop(tool))

    # If anything is still left (unexpected), append it
    for leftover in existing.values():
        final_docs.append(leftover)

    lines = [f"{i+1}.{doc}" for i, doc in enumerate(final_docs)]
    return "\n".join(lines) + "\n"


def detect_environment_values(platform: str, framework: str | None = None) -> tuple[str, str]:
    if platform == "windows":
        terminal = "powershell" if os.name == "nt" else Path(os.environ.get("SHELL", "bash")).name
        os_name = os.environ.get("OS", "Windows") if os.name == "nt" else os.uname().sysname
        return os_name, terminal

    terminal = Path(os.environ.get("SHELL", "zsh" if platform == "macos" else "bash")).name
    if os.name == "nt":
        os_name = "Linux/Raspberry Pi"
    else:
        try:
            os_release = Path("/etc/os-release").read_text(encoding="utf-8")
            match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', os_release, re.MULTILINE)
            os_name = match.group(1) if match else os.uname().sysname
        except Exception:
            os_name = os.uname().sysname
    return os_name, terminal


def render_environment(os_name: str, terminal: str) -> str:
    return f"OS: {os_name}\nTERMINAL: {terminal}\n"


def normalize_framework_env(raw_values: list[str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in raw_values or []:
        if "=" not in item:
            raise ValueError(f"Framework env values must use KEY=VALUE syntax: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid framework env key: {key}")
        values[key] = value
    return values


def prompt_framework_env(platform: str, framework: str | None = None) -> dict[str, str]:
    questions = framework_env_questions(platform, framework)
    if not questions:
        return {}

    print("\nFramework questions:")
    values: dict[str, str] = {}
    for question in questions:
        name = str(question["name"])
        label = str(question.get("label") or name)
        default = str(question.get("default") or "")
        required = bool(question.get("required", False))
        value = prompt_value(label, default)
        if required and not value:
            raise ValueError(f"{label} is required for {normalize_framework(framework)}/{platform}.")
        values[name] = value
    return values


def render_framework_env(values: dict[str, str]) -> str:
    return "".join(f"{key}={value}\n" for key, value in sorted(values.items()))


def validate_framework_env(values: dict[str, str], platform: str, framework: str | None = None) -> dict[str, str]:
    questions = framework_env_questions(platform, framework)
    specs = {str(question["name"]): question for question in questions}
    unknown = sorted(set(values) - set(specs))
    if unknown:
        raise ValueError(f"Unknown framework setting(s): {', '.join(unknown)}")
    normalized: dict[str, str] = {}
    for name, question in specs.items():
        value = str(values.get(name, question.get("default", "")))
        if question.get("required") and not value:
            raise ValueError(f"{question.get('label') or name} is required.")
        options = question.get("options")
        if options and value not in options:
            raise ValueError(f"{name} must be one of: {', '.join(options)}")
        validation = dict(question.get("validation") or {})
        if len(value) < int(validation.get("min_length", 0)) or len(value) > int(validation.get("max_length", 2**31)):
            raise ValueError(f"{name} has an invalid length.")
        if value and validation.get("pattern") and re.fullmatch(str(validation["pattern"]), value) is None:
            raise ValueError(f"{name} does not match the required format.")
        normalized[name] = value
    return normalized


def prompt_environment(platform: str, framework: str | None = None) -> str:
    detected_os, detected_terminal = detect_environment_values(platform, framework)
    print("\nEnvironment for this instance:")
    os_name = prompt_value("OS", detected_os)
    terminal = prompt_value("Terminal", detected_terminal)
    return render_environment(os_name, terminal)


def copy_runner(instance_name: str, target_dir: Path, platform: str, framework: str | None = None) -> Path:
    manifest = validated_framework_manifest(platform, framework)
    source = (platform_root(platform, framework) / manifest["runner"]).resolve()
    runner_text = source.read_text(encoding="utf-8")
    runner_text = runner_text.replace('NAME = "pi"', f'NAME = "{instance_name}"')
    runner_text = runner_text.replace("run_pi", f"run_{instance_name}")
    runner_path = target_dir / f"run_{instance_name}.py"
    runner_path.write_text(runner_text, encoding="utf-8")
    return runner_path


def copy_tools(
    platform: str,
    selected_tools: list[str],
    target_tools_dir: Path,
    framework: str | None = None,
) -> None:
    manifest = validated_framework_manifest(platform, framework)
    source_tools_dir = platform_tools_dir(manifest["source_runtime"], framework)
    modules = {item["name"]: item["module"] for item in manifest["tools"]}
    for tool in selected_tools:
        module_name = modules[tool]
        source = source_tools_dir / module_name
        if not source.exists():
            raise FileNotFoundError(
                f"Tool '{tool}' does not exist for framework '{normalize_framework(framework)}' "
                f"platform '{platform}': {source}"
            )
        shutil.copy2(source, target_tools_dir / module_name)


def copy_lifecycle(platform: str, target_dir: Path, framework: str | None = None) -> dict[str, str]:
    manifest = validated_framework_manifest(platform, framework)
    runtime_root = platform_root(manifest["source_runtime"], framework)
    lifecycle_dir = target_dir / "lifecycle"
    copied: dict[str, str] = {}
    for phase, relative in manifest["lifecycle"].items():
        lifecycle_dir.mkdir(exist_ok=True)
        source = _safe_runtime_file(runtime_root, relative, f"lifecycle {phase}")
        suffix = source.suffix or (".ps1" if platform == "windows" else ".sh")
        destination = lifecycle_dir / f"{phase}{suffix}"
        shutil.copy2(source, destination)
        destination.chmod(destination.stat().st_mode | 0o100)
        copied[phase] = str(destination.relative_to(target_dir))
    return copied


def create_instance(
    instance_name: str,
    platform: str,
    selected_tools: list[str],
    *,
    framework: str | None = None,
    environment_text: str | None = None,
    framework_env: dict[str, str] | None = None,
    architecture_profile: dict | None = None,
    force: bool = False,
) -> Path:
    instance_name = validate_instance_name(instance_name)
    framework = normalize_framework(framework)
    platform = platform.strip().lower()
    available_frameworks = discover_frameworks()
    if framework not in available_frameworks:
        raise ValueError(
            "Unknown framework: "
            + framework
            + ". Available frameworks: "
            + ", ".join(available_frameworks)
        )
    available_platforms = discover_platforms(framework)
    if platform not in available_platforms:
        raise ValueError(
            "Unknown platform: "
            + platform
            + f" for framework {framework}. Available platforms: "
            + ", ".join(available_platforms)
        )
    selected_tools = validate_tool_selection(selected_tools, platform, framework)
    framework_env = validate_framework_env(framework_env or {}, platform, framework)

    target_dir = PROJECT_ROOT / instance_name
    if target_dir.exists():
        if not force:
            raise FileExistsError(f"{target_dir} already exists. Use --force to replace it.")
        shutil.rmtree(target_dir)

    exp_dir = target_dir / "exp"
    tools_dir = target_dir / "tools"
    exp_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)

    (target_dir / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")

    runner_path = copy_runner(instance_name, target_dir, platform, framework)
    copy_tools(platform, selected_tools, tools_dir, framework)
    lifecycle = copy_lifecycle(platform, target_dir, framework)

    (exp_dir / "td.txt").write_text(render_tool_docs(selected_tools, platform, framework), encoding="utf-8")
    if environment_text is None:
        environment_text = render_environment(*detect_environment_values(platform, framework))
    (exp_dir / "enving.txt").write_text(environment_text, encoding="utf-8")
    if framework_env:
        (exp_dir / "framework_env.txt").write_text(render_framework_env(framework_env), encoding="utf-8")
    (exp_dir / "tool_import.txt").write_text(" ".join(selected_tools) + "\n", encoding="utf-8")
    manifest = validated_framework_manifest(platform, framework)
    if architecture_profile is None:
        architecture_profile = device_architecture_profile(
            manifest,
            device_name=instance_name,
            device_os=environment_text.splitlines()[0].partition(":")[2].strip(),
            device_terminal=environment_text.splitlines()[1].partition(":")[2].strip()
            if len(environment_text.splitlines()) > 1 else "",
            selected_tools=selected_tools,
        )
    (exp_dir / "architecture_profile.json").write_text(
        json.dumps(architecture_profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (exp_dir / "framework_manifest.json").write_text(
        json.dumps({**manifest, "selected_tools": selected_tools, "bundled_lifecycle": lifecycle}, indent=2) + "\n",
        encoding="utf-8",
    )

    return runner_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new Pigion Watchdog instance from a central platform template."
    )
    parser.add_argument("name", nargs="?", help="New Python package/instance name.")
    parser.add_argument(
        "--framework",
        default=DEFAULT_FRAMEWORK,
        help="Framework wrapper to use. See platforms/<framework>/core and platforms/<framework>/<platform>.",
    )
    parser.add_argument(
        "--platform",
        help="Platform/runtime tool template to copy from. See platforms/<framework>/<platform>/tools.",
    )
    parser.add_argument(
        "--tools",
        help="Comma-separated tools to include, or 'all'. If omitted, uses discovered tools automatically.",
    )
    parser.add_argument(
        "--env-os",
        help="OS text to write into exp/enving.txt. If omitted, maker asks interactively.",
    )
    parser.add_argument(
        "--env-terminal",
        help="Terminal text to write into exp/enving.txt. If omitted, maker asks interactively.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing generated instance directory.",
    )
    parser.add_argument(
        "--framework-env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set a framework-specific generated env value. Repeat for multiple values.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    available_frameworks = discover_frameworks()
    if not available_frameworks:
        raise RuntimeError(f"No frameworks were discovered in {PLATFORMS_ROOT}.")

    framework = normalize_framework(args.framework or prompt_framework(available_frameworks))
    if framework not in available_frameworks:
        raise RuntimeError(
            f"Framework {framework!r} was not discovered. Available frameworks: {', '.join(available_frameworks)}"
        )
    available_platforms = discover_platforms(framework)
    if not available_platforms:
        raise RuntimeError(f"No platforms were discovered for framework {framework}.")

    instance_name = validate_instance_name(args.name or prompt_value("New instance name"))
    platform = (args.platform or prompt_platform(available_platforms)).strip().lower()
    platform_tools = [item["name"] for item in manifest_tools(platform, framework)]
    selected_tools = validate_tool_selection(
        normalize_tools(args.tools, platform_tools) if args.tools else default_tools_for_platform(platform, framework),
        platform,
        framework,
    )
    detected_os, detected_terminal = detect_environment_values(platform, framework)
    if args.env_os or args.env_terminal:
        environment_text = render_environment(
            args.env_os or detected_os,
            args.env_terminal or detected_terminal,
        )
    elif sys.stdin.isatty():
        environment_text = prompt_environment(platform, framework)
    else:
        environment_text = render_environment(detected_os, detected_terminal)
    framework_env = normalize_framework_env(args.framework_env)
    if not framework_env and sys.stdin.isatty():
        framework_env = prompt_framework_env(platform, framework)

    runner_path = create_instance(
        instance_name=instance_name,
        platform=platform,
        selected_tools=selected_tools,
        framework=framework,
        environment_text=environment_text,
        framework_env=framework_env,
        force=args.force,
    )

    print("\nCreated Watchdog instance:")
    print(f"  name: {instance_name}")
    print(f"  framework: {framework}")
    print(f"  platform template: {platform}")
    print(f"  tools: {', '.join(selected_tools)}")
    print("  environment:")
    for line in environment_text.strip().splitlines():
        print(f"    {line}")
    if framework_env:
        print("  framework env:")
        for key, value in sorted(framework_env.items()):
            print(f"    {key}={value}")
    print(f"  runner: {runner_path.relative_to(PROJECT_ROOT)}")
    print("\nRun it with:")
    print(f"  python {runner_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()

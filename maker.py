from __future__ import annotations

import argparse
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
        if path.is_dir() and (path / "tools").is_dir()
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
    if platform in {"windows", "laptop"}:
        terminal = "powershell" if os.name == "nt" else Path(os.environ.get("SHELL", "bash")).name
        os_name = os.environ.get("OS", "Windows") if os.name == "nt" else os.uname().sysname
        return os_name, terminal

    terminal = Path(os.environ.get("SHELL", "bash")).name
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


def prompt_environment(platform: str, framework: str | None = None) -> str:
    detected_os, detected_terminal = detect_environment_values(platform, framework)
    print("\nEnvironment for this instance:")
    os_name = prompt_value("OS", detected_os)
    terminal = prompt_value("Terminal", detected_terminal)
    return render_environment(os_name, terminal)


def copy_runner(instance_name: str, target_dir: Path, framework: str | None = None) -> Path:
    runner_text = runner_template_path(framework).read_text(encoding="utf-8")
    runner_text = runner_text.replace('NAME = "laptop"', f'NAME = "{instance_name}"')
    runner_text = runner_text.replace('NAME = "pi"', f'NAME = "{instance_name}"')
    runner_text = runner_text.replace("run_laptop", f"run_{instance_name}")
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
    source_tools_dir = platform_tools_dir(platform, framework)
    for tool in selected_tools:
        module_name = TOOL_MODULE_NAMES.get(tool, f"{tool}.py")
        source = source_tools_dir / module_name
        if not source.exists():
            raise FileNotFoundError(
                f"Tool '{tool}' does not exist for framework '{normalize_framework(framework)}' "
                f"platform '{platform}': {source}"
            )
        shutil.copy2(source, target_tools_dir / module_name)


def create_instance(
    instance_name: str,
    platform: str,
    selected_tools: list[str],
    *,
    framework: str | None = None,
    environment_text: str | None = None,
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
    if not selected_tools:
        raise ValueError("At least one tool must be selected.")

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

    runner_path = copy_runner(instance_name, target_dir, framework)
    copy_tools(platform, selected_tools, tools_dir, framework)

    (exp_dir / "td.txt").write_text(render_tool_docs(selected_tools, platform, framework), encoding="utf-8")
    if environment_text is None:
        environment_text = render_environment(*detect_environment_values(platform, framework))
    (exp_dir / "enving.txt").write_text(environment_text, encoding="utf-8")
    (exp_dir / "tool_import.txt").write_text(" ".join(selected_tools) + "\n", encoding="utf-8")

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

    available_tools = discover_tools()
    if not available_tools:
        raise RuntimeError(f"No tools were discovered in {PLATFORMS_ROOT}.")

    instance_name = validate_instance_name(args.name or prompt_value("New instance name"))
    platform = (args.platform or prompt_platform(available_platforms)).strip().lower()
    platform_tools = discover_tools_for_platform(platform, framework)
    selected_tools = normalize_tools(args.tools, platform_tools) if args.tools else list(platform_tools)
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

    runner_path = create_instance(
        instance_name=instance_name,
        platform=platform,
        selected_tools=selected_tools,
        framework=framework,
        environment_text=environment_text,
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
    print(f"  runner: {runner_path.relative_to(PROJECT_ROOT)}")
    print("\nRun it with:")
    print(f"  python {runner_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()

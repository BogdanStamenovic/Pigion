from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
PLATFORMS_ROOT = PROJECT_ROOT / "platforms"
RUNNER_TEMPLATE = PROJECT_ROOT / "laptop" / "run_laptop.py"
TOOL_ORDER = ("shell", "memadd", "search", "return", "askuser")

TOOL_DOCS = {
    "shell": 'Shell, Description: Executes a shell command and returns stdout/stderr, Command - shell:COMMAND, Example - shell:echo "hi"',
    "memadd": "Memory, Description: Stores temporary context memory for the current session (not persistent), Command - memadd:TEXT_OR_KEY=VALUE, Example - memadd:User prefers Python",
    "search": "Search, Description: Performs web search for queries or extracts text content from a URL, Command - search:QUERY_OR_URL, Example - search:openai api or search:https://example.com",
    "return": "Return, Description: Returns something back to the user at the end of the goal if needed, Command - return:TEXT, Example - return:The task has been completed succesfully",
    "askuser": "AskUser, Description: Asks the user for information and returns the user answer, Command - askuser:TEXT, Example - askuser:Can you provide your location?",
}

TOOL_MODULE_NAMES = {
    "return": "return_value.py",
}


def discover_tools() -> list[str]:
    tools: set[str] = set()
    for platform in discover_platforms():
        tools.update(discover_tools_for_platform(platform))
    return sort_tools(tools)


def discover_tools_for_platform(platform: str) -> list[str]:
    tools: set[str] = set()
    tools_dir = platform_tools_dir(platform)
    if not tools_dir.exists():
        return []

    for path in tools_dir.glob("*.py"):
        name = path.stem
        if name == "__init__":
            continue
        if name == "return_value":
            name = "return"
        tools.add(name)
    return sort_tools(tools)


def sort_tools(tools: set[str]) -> list[str]:
    return sorted(
        tools,
        key=lambda tool: (
            TOOL_ORDER.index(tool) if tool in TOOL_ORDER else len(TOOL_ORDER),
            tool,
        ),
    )


def discover_platforms() -> list[str]:
    if not PLATFORMS_ROOT.exists():
        return []
    return sorted(
        path.name
        for path in PLATFORMS_ROOT.iterdir()
        if path.is_dir() and (path / "tools").is_dir()
    )


def platform_tools_dir(platform: str) -> Path:
    return PLATFORMS_ROOT / platform / "tools"


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


def prompt_platform(available_platforms: list[str], default: str = "laptop") -> str:
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


def render_tool_docs(selected_tools: list[str]) -> str:
    lines = []
    for index, tool in enumerate(selected_tools, start=1):
        doc = TOOL_DOCS.get(tool)
        if doc is None:
            module_name = TOOL_MODULE_NAMES.get(tool, f"{tool}.py")
            command_name = "return" if tool == "return" else Path(module_name).stem
            doc = f"{tool.title()}, Description: Custom tool, Command - {command_name}:TEXT, Example - {command_name}:hello"
        lines.append(f"{index}.{doc}")
    return "\n".join(lines) + "\n"


def detect_environment_values(platform: str) -> tuple[str, str]:
    if platform == "laptop":
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


def prompt_environment(platform: str) -> str:
    detected_os, detected_terminal = detect_environment_values(platform)
    print("\nEnvironment for this instance:")
    os_name = prompt_value("OS", detected_os)
    terminal = prompt_value("Terminal", detected_terminal)
    return render_environment(os_name, terminal)


def copy_runner(instance_name: str, target_dir: Path) -> Path:
    runner_text = RUNNER_TEMPLATE.read_text(encoding="utf-8")
    runner_text = runner_text.replace('NAME = "laptop"', f'NAME = "{instance_name}"')
    runner_text = runner_text.replace("run_laptop", f"run_{instance_name}")
    runner_path = target_dir / f"run_{instance_name}.py"
    runner_path.write_text(runner_text, encoding="utf-8")
    return runner_path


def copy_tools(platform: str, selected_tools: list[str], target_tools_dir: Path) -> None:
    source_tools_dir = platform_tools_dir(platform)
    for tool in selected_tools:
        module_name = TOOL_MODULE_NAMES.get(tool, f"{tool}.py")
        source = source_tools_dir / module_name
        if not source.exists():
            raise FileNotFoundError(f"Tool '{tool}' does not exist for platform '{platform}': {source}")
        shutil.copy2(source, target_tools_dir / module_name)


def create_instance(
    instance_name: str,
    platform: str,
    selected_tools: list[str],
    *,
    environment_text: str | None = None,
    force: bool = False,
) -> Path:
    instance_name = validate_instance_name(instance_name)
    platform = platform.strip().lower()
    available_platforms = discover_platforms()
    if platform not in available_platforms:
        raise ValueError(
            "Unknown platform: "
            + platform
            + ". Available platforms: "
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

    runner_path = copy_runner(instance_name, target_dir)
    copy_tools(platform, selected_tools, tools_dir)

    (exp_dir / "td.txt").write_text(render_tool_docs(selected_tools), encoding="utf-8")
    if environment_text is None:
        environment_text = render_environment(*detect_environment_values(platform))
    (exp_dir / "enving.txt").write_text(environment_text, encoding="utf-8")
    (exp_dir / "tool_import.txt").write_text(" ".join(selected_tools) + "\n", encoding="utf-8")

    return runner_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new Pigion Watchdog instance from a central platform template."
    )
    parser.add_argument("name", nargs="?", help="New Python package/instance name.")
    parser.add_argument(
        "--platform",
        help="Platform tool template to copy from. See platforms/<name>/tools.",
    )
    parser.add_argument(
        "--tools",
        help="Comma-separated tools to include, or 'all'. If omitted interactively, defaults to all.",
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
    available_platforms = discover_platforms()
    if not available_platforms:
        raise RuntimeError(f"No platforms were discovered in {PLATFORMS_ROOT}.")

    available_tools = discover_tools()
    if not available_tools:
        raise RuntimeError(f"No tools were discovered in {PLATFORMS_ROOT}.")

    instance_name = validate_instance_name(args.name or prompt_value("New instance name"))
    platform = (args.platform or prompt_platform(available_platforms)).strip().lower()
    platform_tools = discover_tools_for_platform(platform)
    selected_tools = normalize_tools(args.tools, platform_tools) if args.tools else prompt_tools(platform_tools)
    detected_os, detected_terminal = detect_environment_values(platform)
    if args.env_os or args.env_terminal:
        environment_text = render_environment(
            args.env_os or detected_os,
            args.env_terminal or detected_terminal,
        )
    elif sys.stdin.isatty():
        environment_text = prompt_environment(platform)
    else:
        environment_text = render_environment(detected_os, detected_terminal)

    runner_path = create_instance(
        instance_name=instance_name,
        platform=platform,
        selected_tools=selected_tools,
        environment_text=environment_text,
        force=args.force,
    )

    print("\nCreated Watchdog instance:")
    print(f"  name: {instance_name}")
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

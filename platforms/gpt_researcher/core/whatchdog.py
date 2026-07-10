from __future__ import annotations

import asyncio
import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


NAME = "gpt_researcher"

REQUIRED_PACKAGES = {
    "gpt_researcher": "gpt-researcher",
    "langchain_ollama": "langchain-ollama",
    "playwright": "playwright",
    "trafilatura": "trafilatura",
}
OPTIONAL_PACKAGES = {
    "crawl4ai": "crawl4ai",
    "qdrant_client": "qdrant-client",
}


def _parse_env_file(path: Path) -> dict[str, str]:
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


def _load_generated_env() -> None:
    base_dir = Path(__file__).resolve().parent
    env_files = [
        base_dir / ".env",
        base_dir / "exp" / "framework_env.txt",
        Path.cwd() / ".env",
    ]
    for env_file in env_files:
        for key, value in _parse_env_file(env_file).items():
            os.environ.setdefault(key, value)


def _run(command: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout)


def _module_missing(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is None


def _pip_install(packages: list[str]) -> None:
    if not packages:
        return
    result = _run([sys.executable, "-m", "pip", "install", *packages], timeout=1200)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Failed to install GPT Researcher dependencies: {detail}")


def _install_playwright_browser() -> None:
    if os.environ.get("PIGION_GPT_RESEARCHER_SKIP_PLAYWRIGHT_INSTALL") == "1":
        return
    browser = os.environ.get("GPT_RESEARCHER_PLAYWRIGHT_BROWSER", "chromium")
    result = _run([sys.executable, "-m", "playwright", "install", browser], timeout=1200)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Failed to install Playwright browser '{browser}': {detail}")


def _ensure_dependencies() -> list[str]:
    if os.environ.get("PIGION_GPT_RESEARCHER_SKIP_DEP_INSTALL") == "1":
        return ["dependency install skipped by PIGION_GPT_RESEARCHER_SKIP_DEP_INSTALL=1"]

    packages = dict(REQUIRED_PACKAGES)
    extraction = os.environ.get("GPT_RESEARCHER_EXTRACTION", "trafilatura").strip().lower()
    if extraction == "crawl4ai":
        packages["crawl4ai"] = OPTIONAL_PACKAGES["crawl4ai"]
    if os.environ.get("GPT_RESEARCHER_QDRANT_URL", "").strip():
        packages["qdrant_client"] = OPTIONAL_PACKAGES["qdrant_client"]

    missing = [package for module, package in packages.items() if _module_missing(module)]
    if missing:
        _pip_install(missing)

    notes = [f"installed python packages: {', '.join(missing)}" if missing else "python packages already present"]
    if _module_missing("playwright"):
        notes.append("playwright import unavailable after dependency install")
    else:
        _install_playwright_browser()
        notes.append("playwright browser installed")
    return notes


def _pull_ollama_model(model: str) -> str:
    if os.environ.get("PIGION_GPT_RESEARCHER_SKIP_OLLAMA_PULL") == "1":
        return f"skipped ollama pull for {model}"
    if not model:
        return "no ollama model configured"
    if not shutil_which("ollama"):
        return "ollama CLI not found; model pull skipped"
    result = _run(["ollama", "pull", model], timeout=1800)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        return f"ollama pull {model} failed: {detail}"
    return f"ollama model ready: {model}"


def shutil_which(binary: str) -> str | None:
    for item in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(item) / binary
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _configure_research_env() -> dict[str, str]:
    _load_generated_env()
    model = (
        os.environ.get("GPT_RESEARCHER_LLM_MODEL")
        or os.environ.get("LLM_MODEL")
        or "llama3.1"
    ).strip()
    embedding_model = os.environ.get("GPT_RESEARCHER_EMBEDDING_MODEL", "nomic-embed-text").strip()
    ollama_base_url = (
        os.environ.get("GPT_RESEARCHER_OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or "http://127.0.0.1:11434"
    ).strip()
    searxng_url = os.environ.get("GPT_RESEARCHER_SEARXNG_URL", "http://127.0.0.1:8080").strip()
    extraction = os.environ.get("GPT_RESEARCHER_EXTRACTION", "trafilatura").strip().lower()
    report_type = os.environ.get("GPT_RESEARCHER_REPORT_TYPE", "research_report").strip()
    qdrant_url = os.environ.get("GPT_RESEARCHER_QDRANT_URL", "").strip()

    os.environ.setdefault("LLM_PROVIDER", "ollama")
    os.environ["OLLAMA_BASE_URL"] = ollama_base_url
    os.environ["OLLAMA_HOST"] = ollama_base_url
    os.environ["FAST_LLM"] = f"ollama:{model}"
    os.environ["SMART_LLM"] = f"ollama:{model}"
    os.environ["STRATEGIC_LLM"] = f"ollama:{model}"
    os.environ["EMBEDDING"] = f"ollama:{embedding_model}"
    os.environ["RETRIEVER"] = "searx"
    os.environ["SEARX_URL"] = searxng_url
    os.environ["SEARXNG_URL"] = searxng_url
    os.environ["BROWSER"] = "playwright"
    os.environ["PIGION_GPT_RESEARCHER_EXTRACTION"] = extraction
    if qdrant_url:
        os.environ["QDRANT_URL"] = qdrant_url

    return {
        "llm": model,
        "ollama_base_url": ollama_base_url,
        "embedding": embedding_model,
        "retriever": "searx",
        "searxng_url": searxng_url,
        "browser": "playwright",
        "extraction": extraction,
        "qdrant_url": qdrant_url,
        "report_type": report_type,
    }


async def _run_research(goal: str, report_type: str) -> str:
    from gpt_researcher import GPTResearcher

    researcher = GPTResearcher(query=goal, report_type=report_type)
    await researcher.conduct_research()
    return await researcher.write_report()


def run_agent(goal: str) -> dict[str, Any]:
    config = _configure_research_env()
    install_notes = _ensure_dependencies()
    pull_notes = [
        _pull_ollama_model(config["llm"]),
        _pull_ollama_model(config["embedding"]),
    ]
    if os.environ.get("PIGION_GPT_RESEARCHER_DRY_RUN") == "1":
        return {
            "ok": True,
            "framework": "gpt_researcher",
            "dry_run": True,
            "goal": goal,
            "config": config,
            "install_notes": install_notes,
            "pull_notes": pull_notes,
        }

    report = asyncio.run(_run_research(goal, config["report_type"]))
    return {
        "ok": True,
        "framework": "gpt_researcher",
        "goal": goal,
        "config": config,
        "install_notes": install_notes,
        "pull_notes": pull_notes,
        "output": report,
    }


def run_gpt_researcher(goal: str) -> dict[str, Any]:
    return run_agent(goal)


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]).strip() or "Smoke test GPT Researcher wrapper"
    print(run_agent(topic))

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


NAME = "gpt_researcher"


def _architecture_profile() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "exp" / "architecture_profile.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _capability_block() -> str:
    profile = _architecture_profile()
    lines: list[str] = []
    summary = " ".join(str(profile.get("summary") or "").split())
    if summary:
        lines.append(summary)
    capabilities = profile.get("capabilities", [])
    if isinstance(capabilities, list):
        for item in capabilities:
            statement = item.get("statement") if isinstance(item, dict) else item
            statement = " ".join(str(statement or "").split())
            if statement:
                lines.append(f"- {statement}")
    return "\n".join(lines) or "Research capability is provided by the configured GPT Researcher runtime."

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
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
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

    profiled_goal = (
        "CAPABILITY BLOCK:\n"
        + _capability_block()
        + "\n\nRUNTIME ARCHITECTURE:\n"
          "This wrapper starts one GPT Researcher run for this goal. It does not receive private context from a "
          "previous wrapper call; only the user goal below and state managed internally by GPT Researcher are "
          "available.\n\nUSER GOAL:\n"
        + goal
    )
    researcher = GPTResearcher(query=profiled_goal, report_type=report_type)
    await researcher.conduct_research()
    return await researcher.write_report()


def run_agent(goal: str) -> dict[str, Any]:
    config = _configure_research_env()
    if os.environ.get("PIGION_GPT_RESEARCHER_DRY_RUN") == "1":
        return {
            "ok": True,
            "framework": "gpt_researcher",
            "dry_run": True,
            "goal": goal,
            "config": config,
            "architecture_profile": _architecture_profile(),
        }

    report = asyncio.run(_run_research(goal, config["report_type"]))
    return {
        "ok": True,
        "framework": "gpt_researcher",
        "goal": goal,
        "config": config,
        "output": report,
    }


def run_gpt_researcher(goal: str) -> dict[str, Any]:
    return run_agent(goal)


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]).strip() or "Smoke test GPT Researcher wrapper"
    print(run_agent(topic))

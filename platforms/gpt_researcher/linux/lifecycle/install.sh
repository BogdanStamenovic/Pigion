#!/usr/bin/env bash
set -euo pipefail

packages=(gpt-researcher langchain-ollama playwright trafilatura)
if [ "${GPT_RESEARCHER_EXTRACTION:-trafilatura}" = "crawl4ai" ]; then packages+=(crawl4ai); fi
if [ -n "${GPT_RESEARCHER_QDRANT_URL:-}" ]; then packages+=(qdrant-client); fi
"$PIGION_VENV_PYTHON" -m pip install "${packages[@]}"
"$PIGION_VENV_PYTHON" -m playwright install "${GPT_RESEARCHER_PLAYWRIGHT_BROWSER:-chromium}"
if command -v ollama >/dev/null 2>&1 && [[ "${GPT_RESEARCHER_OLLAMA_BASE_URL:-http://127.0.0.1:11434}" == http://127.0.0.1:* || "${GPT_RESEARCHER_OLLAMA_BASE_URL:-}" == http://localhost:* ]]; then
  ollama pull "${GPT_RESEARCHER_LLM_MODEL:-llama3.1}"
  ollama pull "${GPT_RESEARCHER_EMBEDDING_MODEL:-nomic-embed-text}"
fi

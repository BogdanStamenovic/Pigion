from __future__ import annotations

import os
from typing import Any

from google import genai
from google.genai import types


def _coerce_query(payload: str | dict[str, Any] | None) -> str:
    if isinstance(payload, dict):
        payload = payload.get("google_search", payload.get("search", ""))
    return str(payload or "").strip()


def _web_sources(response: Any) -> tuple[list[str], list[dict[str, str]]]:
    candidates = list(getattr(response, "candidates", None) or [])
    metadata = getattr(candidates[0], "grounding_metadata", None) if candidates else None
    queries = [str(value) for value in (getattr(metadata, "web_search_queries", None) or [])]
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in getattr(metadata, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        uri = str(getattr(web, "uri", None) or "").strip()
        if not uri or uri in seen:
            continue
        seen.add(uri)
        sources.append({
            "title": str(getattr(web, "title", None) or uri).strip(),
            "url": uri,
        })
    return queries, sources


def run_google_search(payload: str | dict[str, Any] | None) -> dict[str, Any]:
    query = _coerce_query(payload)
    if not query:
        return {"ok": False, "error": "A non-empty Google Search query is required."}

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        return {"ok": False, "error": "GEMINI_API_KEY is required for Google Search grounding."}
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider not in {"gemini", "google", "google-genai"}:
        return {
            "ok": False,
            "error": f"Google Search grounding requires LLM_PROVIDER=gemini, not {provider or 'unset'}.",
        }

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=(
                os.getenv("GOOGLE_SEARCH_MODEL")
                or os.getenv("GEMINI_MODEL")
                or os.getenv("LLM_MODEL")
                or "gemini-2.5-flash-lite"
            ),
            contents=(
                "Search the public web for the query below. Return a concise, factual answer grounded only in the "
                "search results. Include relevant dates when freshness matters.\n\nQUERY:\n" + query
            ),
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=int(os.getenv("GOOGLE_SEARCH_MAX_OUTPUT_TOKENS", "1400")),
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        text = str(getattr(response, "text", None) or "").strip()
        queries, sources = _web_sources(response)
        if not text:
            return {"ok": False, "query": query, "error": "Google Search grounding returned no text."}
        return {
            "ok": True,
            "mode": "google_search_grounding",
            "query": query,
            "answer": text,
            "search_queries": queries,
            "sources": sources,
        }
    except Exception as exc:
        return {
            "ok": False,
            "mode": "google_search_grounding",
            "query": query,
            "error": f"{type(exc).__name__}: {exc}",
        }


def google_search(command, memory, local_state, program_state):
    local_state = dict(local_state)
    result = run_google_search(command)
    output = str(result)
    local_state["last_tool_output"] = output
    print(output)
    response = {
        "ok": bool(result.get("ok")),
        "output": output,
        "memory": memory,
        "state": local_state,
        "program_state": program_state,
    }
    if not result.get("ok"):
        response["error"] = str(result.get("error") or "Google Search grounding failed.")
    return response

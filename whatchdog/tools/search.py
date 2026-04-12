from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ddgs import DDGS


def _coerce_input(payload: str | dict[str, Any] | None) -> str:
    if payload is None:
        return input("search: ").strip()

    if isinstance(payload, dict):
        value = payload.get("search", "")
    else:
        value = payload

    text = str(value).strip()
    if text.startswith("www."):
        text = "https://" + text
    return text


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def search(
    payload: str | dict[str, Any] | None = None,
    *,
    max_results: int = 3,
    max_chars: int = 3000,
    region: str = "us-en",
) -> dict[str, Any]:
    """
    Jedan ulaz za dva moda:

    - search("openai agents sdk")
    - search({"search": "openai agents sdk"})
    - search("https://example.com")
    - search()  # pita u konzoli: search:
    """
    query = _coerce_input(payload)

    if not query:
        return {
            "ok": False,
            "error": "Prosledi query/link ili pozovi search() pa unesi vrednost kroz prompt."
        }

    try:
        ddgs = DDGS(timeout=10)

        # Ako je link -> parsiraj stranicu
        if _is_url(query):
            page = ddgs.extract(query, fmt="text_plain")
            content = str(page.get("content", "")).strip()

            return {
                "ok": True,
                "mode": "extract",
                "input": query,
                "url": page.get("url", query),
                "content": content[:max_chars],
                "truncated": len(content) > max_chars,
                "content_length": len(content),
            }

        # Ako nije link -> search
        results = ddgs.text(
            query=query,
            region=region,
            safesearch="moderate",
            max_results=max_results,
            backend="duckduckgo",
        )

        normalized = []
        for i, item in enumerate(results, start=1):
            normalized.append(
                {
                    "rank": i,
                    "title": item.get("title", ""),
                    "url": item.get("href") or item.get("url") or "",
                    "snippet": item.get("body") or item.get("content") or "",
                }
            )

        return {
            "ok": True,
            "mode": "search",
            "input": query,
            "results": normalized,
            "count": len(normalized),
        }

    except Exception as e:
        return {
            "ok": False,
            "mode": "extract" if _is_url(query) else "search",
            "input": query,
            "error": f"{type(e).__name__}: {e}",
        }
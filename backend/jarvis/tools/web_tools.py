"""Web intelligence: search and readable page fetch.

Uses DuckDuckGo (no API key required) for search and httpx for fetching. All
operations are read-only and SAFE. When offline, tools return a clear,
actionable error instead of failing silently.
"""
from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote_plus

from jarvis.permissions.engine import PermissionLevel, RiskLevel
from jarvis.tools.base import ToolResult, tool

try:
    import httpx
    _HAS_HTTPX = True
except Exception:  # pragma: no cover
    _HAS_HTTPX = False

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JARVIS/1.0"
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)


def _strip_html(raw: str) -> str:
    raw = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


@tool(
    name="web_search",
    description="Search the web and return the top results (title, url, snippet). "
                "Use for current information, documentation, prices, news.",
    category="web",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 5, "maximum": 10},
        },
        "required": ["query"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def web_search(query: str, limit: int = 5) -> ToolResult:
    if not _HAS_HTTPX:
        return ToolResult.failure("httpx is not installed; web search unavailable.")
    results: list[dict[str, Any]] = []
    # 1) Instant answer (definitions, quick facts)
    instant = ""
    try:
        with httpx.Client(timeout=12, headers={"User-Agent": _UA}) as client:
            r = client.get("https://api.duckduckgo.com/",
                           params={"q": query, "format": "json", "no_html": 1})
            if r.status_code == 200:
                data = r.json()
                instant = data.get("AbstractText") or data.get("Answer") or ""
            # 2) HTML result list
            r2 = client.post("https://html.duckduckgo.com/html/",
                             data={"q": query})
            titles = _RESULT_RE.findall(r2.text)
            snippets = _SNIPPET_RE.findall(r2.text)
            for i, (href, title) in enumerate(titles[:limit]):
                snippet = _strip_html(snippets[i]) if i < len(snippets) else ""
                results.append({"title": _strip_html(title),
                                "url": html.unescape(href),
                                "snippet": snippet})
    except httpx.HTTPError as exc:
        return ToolResult.failure(f"Web search failed (are you online?): {exc}")

    if not results and not instant:
        return ToolResult.success([], summary=f"No results found for '{query}'.")
    lines = ([f"Answer: {instant}"] if instant else [])
    lines += [f"{i+1}. {r['title']} — {r['url']}\n   {r['snippet']}"
              for i, r in enumerate(results)]
    return ToolResult.success(
        {"instant": instant, "results": results},
        summary="\n".join(lines),
    )


@tool(
    name="web_fetch",
    description="Fetch a web page and return its readable text (HTML stripped). "
                "Use to read/summarise an article or documentation page.",
    category="web",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "default": 6000, "maximum": 20000},
        },
        "required": ["url"],
    },
    permission_level=PermissionLevel.SAFE,
    risk_level=RiskLevel.LOW,
)
def web_fetch(url: str, max_chars: int = 6000) -> ToolResult:
    if not _HAS_HTTPX:
        return ToolResult.failure("httpx is not installed; web fetch unavailable.")
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    try:
        with httpx.Client(timeout=15, headers={"User-Agent": _UA},
                          follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")
            if "html" in content_type or not content_type:
                text = _strip_html(r.text)
            else:
                text = r.text
    except httpx.HTTPError as exc:
        return ToolResult.failure(f"Could not fetch page (are you online?): {exc}")
    text = text[:max_chars]
    return ToolResult.success({"url": url, "text": text},
                              summary=f"Fetched {len(text)} chars from {url}.")

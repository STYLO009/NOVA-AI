"""NOVA backend — web search tool (ddgs first, HTML scrape as a fallback)."""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Optional
from urllib.parse import parse_qs, quote_plus, urlparse

from . import tool

log = logging.getLogger("nova.search")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _unwrap(href: str) -> str:
    """DuckDuckGo wraps results in ``//duckduckgo.com/l/?uddg=<url>``."""
    href = unescape(href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        try:
            query = parse_qs(urlparse(href).query)
            targets = query.get("uddg")
            if targets:
                return targets[0]
        except Exception:
            pass
    return href


def _normalise(item: dict) -> Optional[dict]:
    title = _clean_html(str(item.get("title") or item.get("heading") or ""))
    url = str(item.get("href") or item.get("url") or item.get("link") or item.get("base") or "")
    snippet = _clean_html(str(item.get("body") or item.get("snippet") or item.get("description") or ""))
    if not url:
        return None
    url = _unwrap(url)
    if not title:
        title = url
    return {"title": title[:160], "url": url, "snippet": snippet[:400]}


def _search_ddgs(query: str, max_results: int) -> Optional[list[dict]]:
    try:
        from ddgs import DDGS
    except Exception as exc:
        log.info("ddgs unavailable: %s", exc)
        return None
    try:
        with DDGS() as ddgs:
            raw = ddgs.text(query, max_results=max_results)
        results = []
        for item in raw or []:
            normalised = _normalise(item if isinstance(item, dict) else {})
            if normalised:
                results.append(normalised)
        return results or None
    except Exception as exc:
        log.info("ddgs query failed: %s", exc)
        return None


def _search_scrape(query: str, max_results: int) -> Optional[list[dict]]:
    """Fallback: scrape ``https://html.duckduckgo.com/html/?q=``."""
    try:
        import requests
    except Exception as exc:
        log.info("requests unavailable: %s", exc)
        return None
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        html = resp.text or ""
    except Exception as exc:
        log.warning("scrape fallback failed: %s", exc)
        return None

    links = re.findall(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I
    )
    if not links:
        links = re.findall(
            r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>',
            html,
            re.S | re.I,
        )
    snippets = re.findall(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', html, re.S | re.I)

    results: list[dict] = []
    for index, (href, title) in enumerate(links[:max_results]):
        results.append(
            {
                "title": _clean_html(title)[:160],
                "url": _unwrap(href),
                "snippet": _clean_html(snippets[index])[:400] if index < len(snippets) else "",
            }
        )
    return results or None


def web_search(query: str, max_results: int = 5) -> list[dict]:
    return _search_ddgs(query, max_results) or _search_scrape(query, max_results) or []


@tool(
    "web_search",
    "Search the web (DuckDuckGo) and return title/url/snippet results.",
    {
        "query": {"type": "string", "required": True, "description": "Search query"},
        "max_results": {"type": "integer", "required": False, "description": "How many results", "default": 5},
    },
    examples=["search the web for fastapi", "google windows terminal"],
)
def web_search_tool(args: dict, ctx: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "output": "A `query` is required.", "data": {}}
    try:
        max_results = int(args.get("max_results") or 5)
    except Exception:
        max_results = 5
    max_results = max(1, min(max_results, 10))

    results = web_search(query, max_results)
    if not results:
        return {
            "ok": False,
            "output": f"No results for “{query}” — the network may be unavailable.",
            "data": {"query": query, "results": []},
        }

    lines = []
    for index, item in enumerate(results, start=1):
        lines.append(f"{index}. **{item['title']}**")
        lines.append(f"   → {item['url']}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet'][:180]}")
    return {
        "ok": True,
        "output": f"**{len(results)} results** for “{query}”\n" + "\n".join(lines),
        "data": {"query": query, "results": results},
    }


__all__ = ["web_search"]

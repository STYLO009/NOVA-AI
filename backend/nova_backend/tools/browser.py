"""NOVA backend — browser tools (persistent Playwright context + stdlib fallback)."""

from __future__ import annotations

import atexit
import logging
import queue
import re
import subprocess
import threading
import webbrowser
from typing import Optional

from ..config import settings
from . import tool

log = logging.getLogger("nova.browser")

_MAX_TEXT = 800
_JOB_TIMEOUT = 35.0

_lock = threading.Lock()
_jobs: "queue.Queue" = queue.Queue()
_worker: Optional[threading.Thread] = None
_STOP = object()


# ---------------------------------------------------------------------------
# single worker thread: every Playwright call happens on the same thread
# ---------------------------------------------------------------------------


def _worker_main() -> None:  # pragma: no cover - exercised only with a real browser
    pw = None
    browser = None
    context = None
    page = None

    def reset() -> None:
        nonlocal pw, browser, context, page
        for obj in (context, browser, pw):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        pw = browser = context = page = None

    def ensure():
        nonlocal pw, browser, context, page
        if page is None:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()
        return page

    while True:
        job = _jobs.get()
        if job is None or job is _STOP:
            break
        name, payload, out = job
        try:
            if name == "open":
                p = ensure()
                p.goto(payload["url"], wait_until="domcontentloaded", timeout=25000)
                try:
                    p.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                out.put((True, {"title": _safe_title(p), "url": p.url}))
            elif name == "extract":
                p = ensure()
                url = payload.get("url")
                if url and getattr(p, "url", None) != url:
                    p.goto(url, wait_until="domcontentloaded", timeout=25000)
                selector = payload.get("selector") or "body"
                try:
                    node = p.query_selector(selector)
                    text = (node.inner_text() if node else "") or ""
                except Exception:
                    text = ""
                if not text.strip() and not payload.get("selector"):
                    text = (p.content() or "")
                    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
                    text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                out.put((True, {"title": _safe_title(p), "text": text[:4000], "url": getattr(p, "url", url)}))
            elif name == "ping":
                out.put((True, {"ok": True}))
            else:  # pragma: no cover
                out.put((False, {"error": f"unknown browser job {name}"}))
                continue
        except Exception as exc:
            reset()
            out.put((False, {"error": f"{type(exc).__name__}: {exc}"}))
    reset()


def _safe_title(page) -> str:
    try:
        return str(page.title() or "")
    except Exception:
        return ""


def _submit(name: str, payload: dict, timeout: float = _JOB_TIMEOUT):
    """Run a browser job on the worker thread (thread-safe, persistent context)."""
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_main, daemon=True, name="nova-browser")
            _worker.start()
    out: "queue.Queue" = queue.Queue(1)
    _jobs.put((name, payload, out))
    try:
        return out.get(timeout=timeout)
    except queue.Empty:
        return False, {"error": "browser automation timed out"}


def _shutdown() -> None:
    try:
        _jobs.put_nowait(_STOP)
    except Exception:
        pass


atexit.register(_shutdown)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_TLD = (
    r"(?:com|org|net|io|dev|ai|app|co|gov|edu|me|sh|xyz|info|news|tv|cloud|store|tech|site|"
    r"online|blog|shop|uk|de|fr|in|jp|cn|ca|au|us|nl|es|it|ru|br|ch|se|no|fi|pl|kr|sg|hk|tw|mx|za|eu)"
)
_BARE = re.compile(rf"(?i)^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.{_TLD}(?::\d+)?(?:[/?#]\S*)?$")


def normalize_url(raw: object) -> Optional[str]:
    text = str(raw or "").strip().strip("<>\"'")
    if not text:
        return None
    if text.startswith("//"):
        return "https:" + text
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*://", text):
        return text
    if text.startswith("www.") or _BARE.match(text):
        return "https://" + text
    return None


def _is_com_url(url: str) -> bool:
    """True when the URL points to a .com domain."""
    return bool(re.search(r"\.com(?::\d+)?(?:[/?#]|$)", url, re.I))


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


@tool(
    "browser_open",
    "Open a URL in the browser (persistent Playwright window when enabled, else the default browser).",
    {"url": {"type": "string", "required": True, "description": "Absolute or bare URL"}},
    verify=lambda args, result: _verify_open(args, result),
    examples=["open https://example.com", "go to github.com"],
)
def browser_open(args: dict, ctx: dict) -> dict:
    url = normalize_url(args.get("url"))
    if not url:
        return {"ok": False, "output": f"Invalid URL: {args.get('url')!r}", "data": {"url": args.get("url")}}

    if settings.enable_browser_automation:
        ok, data = _submit("open", {"url": url})
        if ok:
            title = data.get("title") or ""
            return {
                "ok": True,
                "output": f"Opened **{title or url}** in the NOVA browser → {url}",
                "data": {"url": url, "title": title, "engine": "playwright"},
            }
        log.warning("playwright failed, falling back to the default browser: %s", data.get("error"))

    try:
        if _is_com_url(url):
            opened = _open_in_chrome(url)
        else:
            opened = webbrowser.open(url)
    except Exception as exc:
        return {"ok": False, "output": f"Could not open {url}: {exc}", "data": {"url": url}}
    if not opened:
        return {"ok": False, "output": f"Windows refused to open {url}", "data": {"url": url}}
    engine = "chrome" if _is_com_url(url) else "default"
    return {
        "ok": True,
        "output": f"Opened {url} in Chrome",
        "data": {"url": url, "engine": engine, "title": ""},
    }


def _open_in_chrome(url: str) -> bool:
    """Open a URL specifically in Google Chrome via subprocess."""
    try:
        subprocess.Popen(
            ["chrome.exe", "--new-tab", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except Exception:
        pass
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def _verify_open(args: dict, result: dict) -> Optional[str]:
    if not result.get("ok"):
        return None
    data = result.get("data") or {}
    if data.get("engine") == "default":
        return None  # "opened in default browser"
    if data.get("engine") == "playwright" and not data.get("title"):
        log.info("page opened without a title: %s", data.get("url"))
    return None


@tool(
    "browser_extract",
    "Fetch a page with Playwright and return its title plus a text snippet (optionally from a CSS selector).",
    {
        "url": {"type": "string", "required": True, "description": "Page URL"},
        "selector": {"type": "string", "required": False, "description": "CSS selector", "default": ""},
    },
    examples=["extract https://example.com"],
)
def browser_extract(args: dict, ctx: dict) -> dict:
    url = normalize_url(args.get("url"))
    if not url:
        return {"ok": False, "output": f"Invalid URL: {args.get('url')!r}", "data": {}}
    if not settings.enable_browser_automation:
        return {
            "ok": False,
            "output": "Browser automation is disabled (ENABLE_BROWSER_AUTOMATION=false). "
            "Set it to true and run `python -m playwright install chromium` to extract pages.",
            "data": {"url": url},
        }
    selector = str(args.get("selector") or "").strip()
    ok, data = _submit("extract", {"url": url, "selector": selector}, timeout=40.0)
    if not ok:
        return {
            "ok": False,
            "output": f"Playwright could not read {url}: {data.get('error')}. "
            "If the browser is missing run `python -m playwright install chromium`.",
            "data": {"url": url, "error": data.get("error")},
        }
    title = data.get("title") or url
    text = (data.get("text") or "")[:_MAX_TEXT]
    return {
        "ok": True,
        "output": f"**{title}**\n{text}",
        "data": {"url": url, "title": title, "text": text, "engine": "playwright"},
    }


__all__ = ["normalize_url"]

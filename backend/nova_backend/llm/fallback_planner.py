"""NOVA backend — deterministic intent inference + plan builder (offline mode).

This module is what keeps NOVA useful when no Hugging Face token is
configured.  It is a keyword/regex rule engine that maps natural language onto
a validated, multi step plan.

Confidence for every rule match is ``0.9``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

CONFIDENCE = 0.9
CATEGORIES = {
    "apps",
    "files",
    "browser",
    "powershell",
    "search",
    "system",
    "memory",
    "chat",
}

# ---------------------------------------------------------------------------
# static copy used by the free-form "respond" steps
# ---------------------------------------------------------------------------

CAPABILITY_LINES = [
    "Open and close Windows applications",
    "Create, read, rename, move, copy and delete files and folders",
    "Search the web and open results in the browser",
    "Run PowerShell commands — risky ones always ask for confirmation first",
    "Report system health: CPU, RAM, disk, network",
    "Keep long-term memory (\"remember that X is Y\")",
]

EXAMPLE_PHRASINGS = [
    "\"open notepad\"",
    "\"create a file called notes.txt in my workspace with hello\"",
    "\"search the web for fastapi and open the first result\"",
    "\"run powershell Get-Process\"",
    "\"list what's in my workspace\"",
    "\"delete the file notes.txt in my workspace\"",
]

ENGINEERING_REPLY = "\n".join(
    [
        "**Hugging Face model required for open-ended engineering tasks.**",
        "Jobs like *\"Optimize build pipeline\"*, *\"Run test suite\"*, *\"Analyze logs\"* or "
        "*\"Deploy to staging\"* need a real LLM to reason about — NOVA is currently running on the "
        "deterministic rule engine (`llm_mode: fallback`), which executes concrete actions instead.",
        "",
        "**Here is what you can do right now:**",
        "→ Set `HF_TOKEN` in `backend/.env` and restart NOVA to unlock open-ended reasoning.",
        "→ Say **\"list my apps\"** and I will report every detected application on this PC.",
        "→ Say **\"run `bun run build` in powershell\"** and I will ask for confirmation before executing it.",
        "→ Say **\"run powershell Get-Process\"** for an immediate read-only system query.",
        "→ Say **\"search the web for <topic> and open the first result\"** to research instead.",
    ]
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_SHELLISH = re.compile(
    r"(?i)^(get-|set-|new-|remove-|start-|stop-|test-|out-|clear-|format-|invoke-|ipconfig|"
    r"ifconfig|systeminfo|whoami|hostname|tasklist|taskkill|netstat|ping|tracert|nslookup|"
    r"dir|ls|cd|pwd|type|cat|echo|copy|move|del|erase|reg\b|sc\b|schtasks|wmic|bcdedit|"
    r"npm|pnpm|yarn|bun|node|git|docker|kubectl|python|pip|poetry|uv|cargo|make|dotnet|"
    r"java|go\b|rspec|pytest|composer|php|ruby|perl|curl|wget|robocopy|xcopy|chkdsk|sfc)"
)

_TLD = (
    r"(?:com|org|net|io|dev|ai|app|co|gov|edu|me|sh|xyz|info|news|tv|cloud|store|tech|site|"
    r"online|blog|shop|xyz|uk|de|fr|in|jp|cn|ca|au|us|nl|es|it|ru|br|ch|se|no|fi|pl|kr|sg|hk|tw|mx|za|eu)"
)

_URL_RE = re.compile(rf"(?i)^(?:https?://\S+|www\.\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.{_TLD})(?:[/:?#]\S*)?$")

_EXT_RE = re.compile(r"(?i)\.[a-z0-9]{1,7}$")

_ENGINEERING_RE = re.compile(
    r"(?i)\b(?:"
    r"optimi[sz]e\s+(?:the\s+)?(?:build\s+)?pipeline"
    r"|optimi[sz]e\s+(?:the\s+)?(?:build|ci|cd|release|deploy)[\w\s-]{0,30}"
    r"|run\s+(?:the\s+|my\s+)?tests?(?:\s+suite)?"
    r"|execute\s+(?:the\s+|my\s+)?tests?(?:\s+suite)?"
    r"|test\s*suite"
    r"|analy[sz]e\s+(?:the\s+)?logs?"
    r"|review\s+(?:the\s+)?logs?"
    r"|deploy\s+(?:to|on|into)?\s*(?:staging|production|prod|live|env\w*|server)?"
    r"|(?:build|ci\s*/\s*cd|release|rollout|rollback|deployment)\s+pipeline"
    r"|pipeline"
    r"|refactor"
    r"|code\s+review"
    r"|architect\s+\w+"
    r")"
)

_NON_APP_WORDS = re.compile(
    r"(?i)^(?:the\s+|a\s+)?(?:test|tests|testing|suite|pipeline|log|logs|build|server|"
    r"database|api|project|code|benchmark|scenario|suite of tests)(\s+\w+){0,3}$"
)

_SYSTEM_RE = re.compile(
    r"(?i)\b(?:system\s*(?:info|status|stats)|cpu\s*(?:usage|temp|load)|ram\s*(?:usage|info)|"
    r"memory\s*(?:usage|info)|disk\s*(?:usage|space|free)|storage\s*(?:usage|space)|"
    r"how\s+much\s+(?:storage|space|memory|ram|disk)|free\s+(?:space|memory)|"
    r"network\s*(?:usage|speed)|hardware\s*(?:info|specs)|machine\s+specs|performance\s*stats?)\b"
)

_GREETING_RE = re.compile(r"(?i)^\s*(?:hi|hello|hey|yo|hiya|howdy|greetings|sup)(?:\s+(?:there|nova|mate|friend))?[!\s.,?]*$")
_THANKS_RE = re.compile(r"(?i)^\s*(?:thanks|thank\s*you|thx|ty|cheers|appreciate\s+it|tysm)[!\s.,?]*$")
_IDENTITY_RE = re.compile(
    r"(?i)^\s*(?:who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|what\s+do\s+you\s+do|"
    r"tell\s+me\s+about\s+yourself|your\s+capabilities|what\s+are\s+you\s+capable\s+of|"
    r"how\s+do\s+you\s+work|help\s+me\s+understand|help)\s*[?.!]*\s*$"
)
_APPS_LIST_RE = re.compile(r"(?i)\b(?:list|show|what(?:'| )s)\b.*\bapps?\b|\bwhich\s+apps\b|\bapplications\b.*\bhave\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _clean(token: str) -> str:
    token = _norm(token)
    token = re.sub(r"^(?:my|the|a|an|some|that|this)\s+", "", token, flags=re.I)
    token = token.strip(" \t.!?\"'`“”")
    return token


def _is_app_name(name: str) -> bool:
    """True when ``name`` plausibly refers to an installed application."""
    n = _norm(name).lower()
    if not n:
        return False
    # A .com domain is a website, not an application.
    if ".com" in n and re.search(r"\.com(?::\d+)?$", n):
        return False
    if n.endswith(".exe"):
        return True
    for word in ("app", "application", "program", "software"):
        if n.endswith(" " + word):
            n = n[: -len(word) - 1].strip()
    from ..tools.apps import APP_ALIASES  # local import: avoids a cycle at import time

    if n in APP_ALIASES:
        return True
    for alias in APP_ALIASES:
        if n.startswith(alias + " ") or n.endswith(" " + alias):
            return True
    return len(n.split()) == 1  # single word => treat as an app name


def _split_content_and_location(rest: str) -> tuple[str, Optional[str], Optional[str]]:
    """``"nova-test.txt in my workspace with hello"`` → (name, location, content)."""
    rest = _norm(rest)
    content: Optional[str] = None
    location: Optional[str] = None

    m = re.search(
        r"\s+(?:with|containing|that\s+contains|loaded\s+with)\s*[:：=]?\s+(.+)$", rest, re.I
    )
    if m:
        content = m.group(1).strip().strip(" \t:：=-")
        rest = rest[: m.start()].strip()

    m = re.search(r"\s+(?:in|under|inside|at)\s+(.+)$", rest, re.I)
    if m:
        location = m.group(1).strip()
        rest = rest[: m.start()].strip()

    m = re.match(r"^(?:called|named)\s+(.+)$", rest, re.I)
    if m:
        rest = m.group(1).strip()
    return rest.strip(" \t.!?\"'"), location, (content.rstrip(" .!") if content else content)


#: trailing filler that users add after a path but that is not part of the path
_DELETE_QUALIFIERS = re.compile(
    r"(?i)\s*(?:,"
    r"|\b(?:permanently|forever|completely|entirely|immediately|instantly|right\s+now|"
    r"straight\s+away|for\s+me|off\s+my\s+system|off\s+this\s+computer|"
    r"from\s+(?:my\s+)?(?:disk|computer|system|machine|drive|pc)|please|kindly|thanks)\b"
    r")+[\s.,;:!?.]*$"
)


def _strip_delete_qualifiers(text: str) -> str:
    """``"notes.txt permanently"`` → ``"notes.txt"`` (keeps stripping filler)."""
    cleaned = _norm(text)
    while True:
        stripped = _DELETE_QUALIFIERS.sub("", cleaned)
        if stripped == cleaned:
            return stripped
        cleaned = stripped


WORKSPACE_ALIASES = {
    "my workspace",
    "the workspace",
    "workspace",
    "nova",
    "my nova workspace",
    "nova workspace",
    "this folder",
    "current folder",
    "current directory",
    "my documents",
    "documents",
    "my docs",
}


def _alias_path(raw: str) -> str:
    """Map friendly folder names ("my workspace") onto a resolvable path."""
    cleaned = _clean(raw)
    if cleaned.lower().strip(" .") in WORKSPACE_ALIASES:
        return "."
    return cleaned


def _location_to_path(location: Optional[str], subject: str) -> str:
    subject = _alias_path(subject) or subject
    if not location:
        return subject
    if re.match(r"^(?:[a-zA-Z]:[\\/]|~|/|\.\.)", subject):
        return subject
    if _alias_path(location) == ".":
        return subject
    return f"{_clean(location)}/{subject}"


def _step(tool: str, title: str, detail: str, args: dict) -> dict:
    return {"tool": tool, "title": title, "detail": detail, "args": args}


def _respond(text: str, title: str = "Reply to you") -> dict:
    return _step("respond", title, "Chat response — no side effects", {"text": text})


# ---------------------------------------------------------------------------
# match result
# ---------------------------------------------------------------------------


@dataclass
class Match:
    category: str
    action: str
    summary: str
    entities: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)


def _capability_reply() -> str:
    lines = ["**I'm NOVA — your Windows copilot.** Here is what I can do right now:"]
    lines += [f"→ {c}" for c in CAPABILITY_LINES]
    lines.append("")
    lines.append("**Try one of these:**")
    lines += [f"→ {e}" for e in EXAMPLE_PHRASINGS]
    return "\n".join(lines)


def _apps_reply() -> str:
    try:
        from ..tools.apps import detect_apps

        apps = detect_apps(limit=8)
    except Exception:
        apps = []
    if not apps:
        return (
            "**No applications detected yet.**\n"
            "→ I scan PATH with `where.exe` plus a table of known install locations.\n"
            "→ Try \"open notepad\" — that always works."
        )
    lines = ["**Applications detected on this PC:**"]
    lines += [f"→ **{a['name']}** — `{a['command']}`" for a in apps]
    lines.append("")
    lines.append("→ Say \"open <name>\" to launch one.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# intent rules (first match wins)
# ---------------------------------------------------------------------------


def _match(text: str) -> Optional[Match]:
    raw = _norm(text)
    t = raw.lower().rstrip(" ?.!")

    # 1 ── powershell -------------------------------------------------------
    cmd = _extract_command(raw, t)
    if cmd:
        return Match(
            "powershell",
            "run_powershell",
            f"Run PowerShell: {cmd[:60]}",
            {"command": cmd},
            [_step("run_powershell", "Run PowerShell command", cmd[:120], {"command": cmd})],
        )

    # 2 ── remember ---------------------------------------------------------
    m = re.match(r"(?is)^\s*(?:please\s+)?remember\s+(?:that\s+)?(.+)$", raw)
    if m:
        rest = m.group(1).strip().rstrip(".!")
        im = re.match(r"^(?P<k>.+?)\s+(?:is|=)\s+(?P<v>.+)$", rest, re.I)
        key, value = (im.group("k").strip(), im.group("v").strip()) if im else (rest, rest)
        return Match(
            "memory",
            "remember",
            f"Remember: {key}",
            {"key": key, "value": value},
            [_step("memory_save", f"Remember “{key[:40]}”", "Save to long-term memory", {"key": key, "value": value})],
        )

    # 3 ── recall -----------------------------------------------------------
    m = re.match(
        r"(?is)^\s*(?:what\s+do\s+you\s+remember|do\s+you\s+remember|recall|remind\s+me(?:\s+about)?|"
        r"what(?:'| )s\s+in\s+(?:your\s+)?memory|show\s+(?:me\s+)?(?:your\s+)?memory|memory)\b"
        r"(?:\s+(?:about|for|regarding)\s+(?P<q>.+))?\s*[?.!]*\s*$",
        raw,
    )
    if m:
        q = (m.group("q") or "").strip().rstrip(".!?")
        detail = f"Recall memory matching “{q}”" if q else "Recall everything remembered"
        return Match(
            "memory",
            "recall",
            detail,
            {"query": q},
            [_step("memory_recall", "Recall from memory", detail, {"query": q})],
        )

    # 4 ── web search -------------------------------------------------------
    m = re.match(
        r"(?is)^\s*(?:please\s+)?(?:(?:search|google|look\s+up|look\s+that\s+up|find\s+out|web\s+search)"
        r"\s+(?:the\s+)?(?:web|internet|online|web)?\s*(?:for)?\s*)(?P<q>.+?)\s*$",
        raw,
    )
    if m and not re.search(r"(?i)\b(?:files?|folders?|directories|documents?\s+named)\b", m.group("q")):
        query, nth = _split_open_result(m.group("q"))
        if query:
            steps = [
                _step(
                    "web_search",
                    f"Search the web for “{query[:40]}”",
                    f"Top 5 results via DuckDuckGo",
                    {"query": query, "max_results": 5},
                )
            ]
            if nth is not None:
                steps.append(
                    _step(
                        "browser_open",
                        f"Open the {nth + 1} result",
                        "Opens the selected search result in the browser",
                        {"url": "{{1.data.results.%d.url}}" % nth},
                    )
                )
            return Match(
                "search",
                "search_web",
                f"Web search: {query}",
                {"query": query, "open_result": nth},
                steps,
            )

    # 5 ── file operations --------------------------------------------------
    fm = _match_files(raw, t)
    if fm:
        return fm

    # 6 ── browser ----------------------------------------------------------
    m = re.match(
        r"(?is)^\s*(?:please\s+)?(?:go\s+to|open|launch|visit|navigate\s+to|browse\s+to|open\s+up)\s+(?P<u>\S.*?)\s*[?.!]*\s*$",
        raw,
    )
    if m and _URL_RE.match(m.group("u")):
        url = m.group("u")
        return Match(
            "browser",
            "open_url",
            f"Open {url}",
            {"url": url},
            [_step("browser_open", f"Open {url[:50]}", "Navigate the browser", {"url": url})],
        )

    # 7 ── close app --------------------------------------------------------
    m = re.match(
        r"(?is)^\s*(?:please\s+)?(?:close|kill|quit|exit|stop|shut\s+down)\s+(?:the\s+|my\s+)?(?P<app>.+?)\s*[?.!]*\s*$",
        raw,
    )
    if m and not re.search(r"(?i)\b(?:window|tab|browser tab|program that)\b", m.group("app")):
        app = _clean(m.group("app"))
        if _is_app_name(app) and not _ENGINEERING_RE.search(app):
            return Match(
                "apps",
                "close_app",
                f"Close {app}",
                {"app": app},
                [_step("close_app", f"Close {app}", f"Force quit {app}", {"app": app})],
            )

    # 8 ── system info ------------------------------------------------------
    if _SYSTEM_RE.search(t):
        m = re.search(r"(?i)(cpu|ram|memory|disk|storage|network|system)", t)
        section = (m.group(1).lower() if m else "overview")
        if section in {"memory", "ram"}:
            section = "memory"
        elif section in {"disk", "storage"}:
            section = "disk"
        elif section == "network":
            section = "network"
        elif section == "cpu":
            section = "cpu"
        else:
            section = "overview"
        return Match(
            "system",
            "system_info",
            "Report system health",
            {"section": section},
            [_step("system_info", "Collect system health", f"CPU · RAM · disk · network ({section})", {"section": section})],
        )

    # 9 ── open-ended engineering tasks (need the LLM) ----------------------
    if _ENGINEERING_RE.search(t):
        return Match(
            "chat",
            "engineering",
            "Open-ended engineering task — Hugging Face model required",
            {"needs_llm": True},
            [_respond(ENGINEERING_REPLY, "Explain HF model requirement")],
        )

    # 10 ── open an application -------------------------------------------
    m = re.match(
        r"(?is)^\s*(?:please\s+)?(?:open|launch|start|run|fire\s+up|spin\s+up)\s+(?:up\s+)?(?:the\s+|my\s+)?(?P<app>.+?)\s*[?.!]*\s*$",
        raw,
    )
    if m:
        app = _clean(m.group("app")).strip()
        app = re.sub(r"^(?:for\s+me|please)\s+", "", app, flags=re.I).strip()
        if _is_app_name(app):
            return Match(
                "apps",
                "open_app",
                f"Open {app}",
                {"app": app},
                [_step("open_app", f"Open {app}", "Launch the application", {"app": app})],
            )

    # 10b ── standalone .com URL (e.g. "google.com") → browser ----------
    _COM_ONLY_RE = re.compile(rf"(?i)^\s*(?:please\s+)?(?P<url>[a-z0-9][a-z0-9-]*\.com(?:/[^\s]*)?)\s*[?.!]*\s*$")
    if _COM_ONLY_RE.match(raw):
        url_match = _COM_ONLY_RE.match(raw)
        if url_match and _URL_RE.match(url_match.group("url")):
            url = url_match.group("url")
            return Match(
                "browser",
                "open_url",
                f"Open {url}",
                {"url": url},
                [_step("browser_open", f"Open {url[:50]}", "Open in Chrome", {"url": url})],
            )

    # 11 ── detected apps listing ------------------------------------------
    if _APPS_LIST_RE.search(t):
        return Match(
            "apps",
            "list_apps",
            "List detected applications",
            {},
            [_respond(_apps_reply(), "List detected apps")],
        )

    # 12 ── social ----------------------------------------------------------
    if _GREETING_RE.match(raw):
        return Match(
            "chat",
            "greeting",
            "Greeting",
            {},
            [
                _respond(
                    "**Hey — NOVA online.**\n"
                    + "\n".join(f"→ {c}" for c in CAPABILITY_LINES)
                    + "\n→ Say \"what can you do\" for examples.",
                    "Greet you",
                )
            ],
        )
    if _THANKS_RE.match(raw):
        return Match("chat", "thanks", "Thank you", {}, [_respond("You're welcome — anytime. ✦", "Acknowledge thanks")])
    if _IDENTITY_RE.match(raw) or re.match(r"(?i)^\s*who\s+is\s+nova\s*[?.!]*\s*$", raw):
        return Match("chat", "identity", "Capability overview", {}, [_respond(_capability_reply(), "Explain capabilities")])

    return None


def _extract_command(raw: str, lowered: str) -> Optional[str]:
    m = re.match(
        r"(?is)^\s*(?:please\s+)?(?:run|execute|invoke)\s+(?:in\s+)?(?:powershell|powershell\s*7|ps|the\s+(?:shell|terminal))"
        r"\s*(?:,|:|-)?\s*(?P<c>.+)$",
        raw,
    )
    if m:
        return m.group("c").strip().strip("\"'`")
    m = re.match(r"^\s*(?:please\s+)?(?:run|execute)\s+[\"'`](?P<c>.+?)[\"'`](?:\s+in\s+(?:powershell|ps|the\s+(?:shell|terminal)))?\s*$", raw, re.I)
    if m:
        return m.group("c").strip()
    m = re.match(
        r"^\s*(?:please\s+)?(?:run|execute)\s+(?P<c>.+?)(?:\s+in\s+(?:powershell|ps|the\s+(?:shell|terminal)))?\s*$",
        raw,
        re.I,
    )
    if m and _SHELLISH.match(m.group("c").strip()):
        return m.group("c").strip().rstrip(" .")
    return None


def _split_open_result(query: str) -> tuple[str, Optional[int]]:
    """``"fastapi and open the first result"`` → ``("fastapi", 0)``."""
    m = re.match(
        r"(?is)^(?P<q>.+?)\s+and\s+(?:then\s+)?open\s+(?:the\s+)?"
        r"(?P<n>first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th))\s+result\s*$",
        query,
    )
    if not m:
        return _norm(query), None
    word = m.group("n").lower()
    mapping = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4, "last": -1}
    if word in mapping:
        idx = mapping[word]
    else:
        digits = re.sub(r"\D", "", word)
        idx = max(0, int(digits) - 1)
    return _norm(m.group("q")), idx


def _match_files(raw: str, lowered: str) -> Optional[Match]:
    # -- create file / folder ------------------------------------------------
    m = re.match(
        r"(?is)^\s*(?:please\s+)?(?:create|make|new|write|add|build)(?:\s+me)?\s+(?:a\s+|an\s+)?"
        r"(?:new\s+)?(?P<kind>file|folder|directory|dir)\b\s*(?:called|named)?\s*(?P<rest>.+)$",
        raw,
    )
    if not m:
        m = re.match(
            r"(?is)^\s*(?:please\s+)?(?:create|make|new|mkdir)\s+(?P<rest>(?P<kind>[\w.\- ]+?\.(?:[a-z0-9]{1,7})))\s*$",
            raw,
        )
    if m:
        kind = (m.group("kind") or "").lower()
        name, loc, content = _split_content_and_location(m.group("rest"))
        if name:
            path = _location_to_path(loc, name)
            if kind in {"folder", "directory", "dir"}:
                return Match(
                    "files",
                    "create_folder",
                    f"Create folder {name}",
                    {"path": path},
                    [_step("create_folder", f"Create folder {name}", f"mkdir {path}", {"path": path})],
                )
            args = {"path": path}
            if content is not None:
                args["content"] = content
            detail = f"Write {path}" + (f" with {len(content)} chars" if content else "")
            return Match(
                "files",
                "create_file",
                f"Create file {name}",
                {"path": path, "content": content or ""},
                [_step("create_file", f"Create {name}", detail, args)],
            )

    # -- read file -----------------------------------------------------------
    m = re.match(
        r"(?is)^\s*(?:please\s+)?(?:read|open|show|cat|type|display)\s+(?:the\s+|my\s+)?"
        r"(?:file\s+)?(?P<p>[^?!]+?)\s*[?.!]*\s*$",
        raw,
    )
    if m:
        p = m.group("p").strip()
        looks_path = bool(_EXT_RE.search(p) or re.search(r"[\\/~]", p) or re.search(r"(?i)\bfile\b", raw))
        # A bare domain ("github.com") matches _EXT_RE too — never read it as a
        # file; the browser rule further down owns URLs.
        if looks_path and not _URL_RE.match(p) and not (re.fullmatch(_APP_WORD_RE, p or "")):
            name, loc, _ = _split_content_and_location(p)
            path = _location_to_path(loc, name)
            return Match(
                "files",
                "read_file",
                f"Read {name}",
                {"path": path},
                [_step("read_file", f"Read {name}", f"First 120 lines of {path}", {"path": path, "max_lines": 120})],
            )

    # -- delete --------------------------------------------------------------
    m = re.match(
        r"(?is)^\s*(?:please\s+)?(?:delete|remove|drop|erase|trash|destroy|purge)\s+"
        r"(?:the\s+|my\s+)?(?:file\s+|folder\s+|directory\s+)?(?P<p>[^?!]+?)\s*[?.!]*\s*$",
        raw,
    )
    if m and not re.search(r"(?i)\b(?:app|program|software|package|cache only)\b", m.group("p")):
        name, loc, _ = _split_content_and_location(_strip_delete_qualifiers(m.group("p")))
        path = _location_to_path(loc, name)
        return Match(
            "files",
            "delete",
            f"Delete {name}",
            {"path": path},
            [_step("delete_path", f"Delete {name}", "Permanently removes it from disk", {"path": path})],
        )

    # -- rename / move / copy ------------------------------------------------
    m = re.match(r"(?is)^\s*(?:please\s+)?rename\s+(?P<src>.+?)\s+to\s+(?P<dst>.+?)\s*[?.!]*\s*$", raw)
    if m:
        name, loc, _ = _split_content_and_location(m.group("src"))
        path = _location_to_path(loc, name)
        new_name = _clean(m.group("dst"))
        return Match(
            "files",
            "rename",
            f"Rename {name} → {new_name}",
            {"path": path, "new_name": new_name},
            [_step("rename_path", f"Rename {name}", f"→ {new_name}", {"path": path, "new_name": new_name})],
        )

    m = re.match(r"(?is)^\s*(?:please\s+)?move\s+(?P<src>.+?)\s+(?:to|into|over\s+to)\s+(?P<dst>.+?)\s*[?.!]*\s*$", raw)
    if m:
        name, loc, _ = _split_content_and_location(m.group("src"))
        path = _location_to_path(loc, name)
        dest = _clean(m.group("dst"))
        return Match(
            "files",
            "move",
            f"Move {name} → {dest}",
            {"path": path, "destination": dest},
            [_step("move_path", f"Move {name}", f"→ {dest}", {"path": path, "destination": dest})],
        )

    m = re.match(r"(?is)^\s*(?:please\s+)?(?:copy|duplicate)\s+(?P<src>.+?)\s+(?:to|into)\s+(?P<dst>.+?)\s*[?.!]*\s*$", raw)
    if m:
        name, loc, _ = _split_content_and_location(m.group("src"))
        path = _location_to_path(loc, name)
        dest = _clean(m.group("dst"))
        return Match(
            "files",
            "copy",
            f"Copy {name} → {dest}",
            {"path": path, "destination": dest},
            [_step("copy_path", f"Copy {name}", f"→ {dest}", {"path": path, "destination": dest})],
        )

    # -- find files ----------------------------------------------------------
    if re.search(r"(?i)\b(?:files?|folders?|directories|documents?|\*)\b", t_of(raw)) and re.search(
        r"(?i)^\s*(?:please\s+)?(?:find|search\s+for|locate|look\s+for)\b", raw
    ):
        m = re.match(
            r"(?is)^\s*(?:please\s+)?(?:find|search\s+for|locate|look\s+for)\s+(?:me\s+)?"
            r"(?:any\s+|all\s+)?(?:files?|folders?|directories|documents?)?\s*"
            r"(?:named|called|matching|containing)?\s*(?P<p>.+?)\s*[?.!]*\s*$",
            raw,
        )
        if m:
            pattern = _clean(m.group("p"))
            if pattern:
                _, loc, _ = _split_content_and_location(pattern)
                path = _location_to_path(loc, ".")
                return Match(
                    "files",
                    "find_files",
                    f"Find files matching {pattern}",
                    {"pattern": pattern, "path": path},
                    [_step("search_files", f"Find files “{pattern[:40]}”", "Recursive search, max 500 hits", {"pattern": pattern, "path": path})],
                )

    # -- list directory ------------------------------------------------------
    m2 = re.search(r"(?i)(?:what(?:'| )s|whats)\s+(?:in|inside)\s+(?P<w>.+?)\s*[?.!]*\s*$", raw)
    if m2:
        p = m2.group("w").strip()
        if p and not _EXT_RE.search(p) and not re.search(r"(?i)\b(?:apps?|applications?|programs?)\b", p):
            path = _alias_path(p)
            return Match(
                "files",
                "list_dir",
                f"List {p}",
                {"path": path},
                [_step("list_dir", f"List {p}", f"Entries of {path}", {"path": path})],
            )
    m = re.match(
        r"(?is)^\s*(?:what(?:'| )s|whats)\s+(?:in|inside|inside\s+of)\s+(?P<p>.+?)\s*[?.!]*\s*$"
        r"|^\s*(?:please\s+)?(?:list|show|ls|dir)\s+(?:me\s+)?(?:the\s+)?(?:contents\s+of\s+)?(?:files\s+in\s+|directory\s+|folder\s+)?(?P<p2>[^?!]+?)\s*[?.!]*\s*$",
        raw,
    )
    if m:
        p = (m.group("p") or m.group("p2") or "").strip()
        if (
            p
            and not _EXT_RE.search(p)
            and not re.search(r"(?i)\b(?:apps?|applications?|programs?)\b", p)
        ):
            path = _alias_path(p)
            return Match(
                "files",
                "list_dir",
                f"List {p}",
                {"path": path},
                [_step("list_dir", f"List {p}", f"Entries of {path}", {"path": path})],
            )
    return None


_APP_WORD_RE = r"(?i)(?:notepad|calc|calculator|cmd|powershell|explorer|taskmgr|chrome|edge|firefox|vscode|code|word|excel|powerpoint|paint|spotify|steam|blender|figma|terminal)"


def t_of(text: str) -> str:
    return _norm(text)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def infer_intent(text: str) -> dict:
    """Rule based intent: ``{category, action, summary, confidence, entities}``."""
    match = _match(text)
    if match is None:
        return {
            "category": "chat",
            "action": "help",
            "summary": "General request — capability overview",
            "confidence": 0.5,
            "entities": {},
        }
    return {
        "category": match.category if match.category in CATEGORIES else "chat",
        "action": match.action,
        "summary": match.summary,
        "confidence": CONFIDENCE,
        "entities": match.entities,
    }


def build_plan(text: str, intent: Optional[dict] = None) -> list[dict]:
    """Rule based multi step plan (ids are assigned 1..n)."""
    match = _match(text)
    steps: list[dict] = list(match.steps) if match else []
    if not steps:
        steps = [_respond(_capability_reply(), "Explain what NOVA can do")]
    for i, step in enumerate(steps, start=1):
        step["id"] = i
        step.setdefault("args", {})
        step.setdefault("detail", "")
        step.setdefault("title", step["tool"])
        step["destructive"] = False  # recomputed by the planner from the registry
    return steps


__all__ = ["infer_intent", "build_plan", "CONFIDENCE", "CATEGORIES"]

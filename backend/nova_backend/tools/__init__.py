"""NOVA backend — tool registry.

The registry is the **only** authority for destructiveness: every step's
``destructive`` flag is recomputed from :func:`step_risk`, never from the LLM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import settings

log = logging.getLogger("nova.tools")

Handler = Callable[[dict, dict], dict]
RiskCheck = Callable[[dict], Optional[str]]
VerifyHook = Callable[[dict, dict], Optional[str]]


@dataclass
class Tool:
    name: str
    description: str
    args_schema: dict[str, dict]
    handler: Handler
    destructive_check: RiskCheck = field(default=lambda args: None)
    verify: Optional[VerifyHook] = None
    examples: list[str] = field(default_factory=list)

    # -- convenience -------------------------------------------------------
    @property
    def risk(self) -> Optional[str]:
        """Default risk for this tool (ignoring args) — used in the catalog."""
        try:
            return self.destructive_check({})
        except Exception:
            return None


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    _REGISTRY[tool.name] = tool
    return tool


def tool(
    name: str,
    description: str,
    args_schema: Optional[dict[str, dict]] = None,
    destructive_check: Optional[RiskCheck] = None,
    verify: Optional[VerifyHook] = None,
    examples: Optional[list[str]] = None,
) -> Callable[[Handler], Handler]:
    """Decorator registering a handler; the handler can never raise."""

    def deco(fn: Handler) -> Handler:
        def safe_handler(args: dict, ctx: dict) -> dict:
            try:
                result = fn(args or {}, ctx or {})
                if not isinstance(result, dict):
                    return {"ok": True, "output": str(result), "data": {}}
                result.setdefault("ok", True)
                result.setdefault("output", "")
                result.setdefault("data", {})
                return result
            except Exception as exc:  # tools must never raise
                log.warning("tool %s raised: %s", name, exc)
                return {
                    "ok": False,
                    "output": f"{type(exc).__name__}: {exc}",
                    "data": {"error": str(exc)},
                }

        def _risk(args: dict) -> Optional[str]:
            if destructive_check is None:
                return None
            try:
                return destructive_check(args or {})
            except Exception as exc:  # conservative: unknown risk => ask
                log.warning("risk check failed for %s: %s", name, exc)
                return f"Risk could not be assessed ({exc})"

        def _verify(args: dict, result: dict) -> Optional[str]:
            if verify is None:
                return None
            try:
                return verify(args or {}, result or {})
            except Exception as exc:
                return f"verification error: {exc}"

        register(
            Tool(
                name=name,
                description=description,
                args_schema=args_schema or {},
                handler=safe_handler,
                destructive_check=_risk,
                verify=_verify if verify is not None else None,
                examples=list(examples or []),
            )
        )
        return fn

    return deco


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------


def get_tool(name: str) -> Optional[Tool]:
    if not name:
        return None
    return _REGISTRY.get(str(name).strip().lower())


def all_tools() -> list[Tool]:
    return [ _REGISTRY[k] for k in sorted(_REGISTRY) ]


def known_tool_names() -> list[str]:
    return sorted(_REGISTRY)


def step_risk(name: str, args: dict) -> Optional[str]:
    """Risk reason for a step (registry is the only authority)."""
    t = get_tool(name)
    if t is None:
        return None
    return t.destructive_check(args or {})


def is_destructive(name: str, args: dict) -> bool:
    return step_risk(name, args) is not None


def _probe_args() -> dict:
    """Placeholder arguments used to discover a tool's static risk for the catalog."""
    try:
        ws = Path(settings.ensure_workspace())
        probe_path = str(ws)
        probe_dest = str(ws.parent)
        probe_new = ws.name
    except Exception:  # pragma: no cover
        probe_path = probe_dest = probe_new = "."
    return {
        "app": "notepad",
        "path": probe_path,
        "destination": probe_dest,
        "new_name": probe_new,
        "command": "Remove-Item C:\\temp\\x.txt",
        "key": "key",
        "value": "value",
        "url": "https://example.com",
        "pattern": "*.log",
        "text": "hello",
        "section": "overview",
    }


def catalog_text() -> str:
    """Full tool catalog formatted for the planner prompt."""
    probe = _probe_args()
    lines: list[str] = []
    for tool_obj in all_tools():
        args_for_risk = {k: probe[k] for k in (tool_obj.args_schema or {}) if k in probe}
        risk = tool_obj.destructive_check(args_for_risk)
        flag = " [DESTRUCTIVE - always asks the user for confirmation]" if risk else ""
        lines.append(f"- {tool_obj.name} — {tool_obj.description}{flag}")
        if tool_obj.args_schema:
            for arg, spec in tool_obj.args_schema.items():
                typ = spec.get("type", "string")
                desc = spec.get("description", "")
                required = (
                    "required"
                    if spec.get("required")
                    else f"optional, default={spec.get('default', None)!r}"
                )
                lines.append(f"    · {arg} ({typ}, {required}) {desc}")
        for example in tool_obj.examples[:3]:
            lines.append(f'    e.g. "{example}"')
    return "\n".join(lines)


def default_ctx() -> dict:
    """Context handed to every tool handler."""
    return {
        "settings": settings,
        "workspace": Path(settings.ensure_workspace()),
    }


def coerce_args(name: str, args: Any) -> dict:
    """Keep only schema keys and coerce values to the declared types."""
    t = get_tool(name)
    if t is None:
        return {}
    if not isinstance(args, dict):
        args = {}
    cleaned: dict[str, Any] = {}
    schema = t.args_schema or {}
    lowered = {k.lower(): k for k in schema}
    for key, value in args.items():
        std = lowered.get(str(key).lower())
        if std is None:
            continue  # unknown argument -> drop
        spec = schema[std] or {}
        typ = str(spec.get("type", "string")).lower()
        try:
            if typ in {"integer", "int"} and not isinstance(value, bool):
                value = int(value)
            elif typ in {"float", "number"} and not isinstance(value, bool):
                value = float(value)
            elif typ in {"boolean", "bool"}:
                if isinstance(value, str):
                    value = value.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    value = bool(value)
            elif typ in {"array", "list"} and not isinstance(value, list):
                value = [value]
            else:
                if value is not None and not isinstance(value, (str, int, float, bool, list)):
                    value = str(value)
                if value is not None and isinstance(value, str):
                    value = value.strip()
        except Exception:
            value = spec.get("default")
        if value is None and spec.get("default") is not None:
            value = spec["default"]
        cleaned[std] = value
    # defaults
    for arg, spec in schema.items():
        if arg not in cleaned and spec.get("default") is not None and not spec.get("required"):
            cleaned[arg] = spec["default"]
    return cleaned


def validate_step(step: Any) -> Optional[dict]:
    """Validate one planner step against the registry.

    Returns a normalised step ``{tool, title, detail, args, destructive, risk}``
    or ``None`` when the step must be dropped (unknown tool).
    """
    if not isinstance(step, dict):
        return None
    name = step.get("tool") or step.get("name") or step.get("action") or ""
    t = get_tool(str(name))
    if t is None:
        return None
    args = coerce_args(t.name, step.get("args") or step.get("parameters") or {})
    risk = t.destructive_check(args)
    title = str(step.get("title") or step.get("summary") or t.name).strip()
    detail = str(step.get("detail") or step.get("description") or t.description).strip()
    return {
        "tool": t.name,
        "title": title[:120],
        "detail": detail[:300],
        "args": args,
        "destructive": risk is not None,
        "risk": risk,
    }


# ---------------------------------------------------------------------------
# small, side-effect-light tools that belong next to the registry
# ---------------------------------------------------------------------------


@tool(
    "memory_save",
    "Save a key/value pair into NOVA's long-term SQLite memory.",
    {"key": {"type": "string", "required": True, "description": "Memory key"},
     "value": {"type": "string", "required": False, "description": "Value to remember", "default": ""}},
    examples=["remember that my favorite editor is vscode"],
)
def memory_save(args: dict, ctx: dict) -> dict:
    from ..memory.store import store

    key = str(args.get("key") or "").strip()
    if not key:
        return {"ok": False, "output": "A non-empty `key` is required.", "data": {}}
    item = store.add_memory(
        key, str(args.get("value") or ""), user_id=ctx.get("user_id")
    )
    return {
        "ok": True,
        "output": f"Remembered **{item['key']}** = {item['value']}",
        "data": {"item": item},
    }


@tool(
    "memory_recall",
    "Recall entries from NOVA's long-term memory (optionally filtered by a query).",
    {"query": {"type": "string", "required": False, "description": "Substring filter", "default": ""}},
    examples=["what do you remember about editors"],
)
def memory_recall(args: dict, ctx: dict) -> dict:
    from ..memory.store import store

    query = str(args.get("query") or "").strip()
    items = store.search_memory(query, limit=20, user_id=ctx.get("user_id"))
    if not items:
        return {
            "ok": True,
            "output": f"I don't have any memory entries matching “{query}”." if query else "My memory is empty.",
            "data": {"items": []},
        }
    lines = [f"→ **{i['key']}** = {i['value']}" for i in items]
    header = f"**{len(items)} memory entr{'y' if len(items) == 1 else 'ies'}**"
    if query:
        header += f" matching “{query}”"
    return {"ok": True, "output": header + "\n" + "\n".join(lines), "data": {"items": items}}


@tool(
    "respond",
    "Reply with a plain chat message — no side effects.",
    {"text": {"type": "string", "required": True, "description": "Markdown-ish reply for the user"}},
    examples=["Hello!"],
)
def respond(args: dict, ctx: dict) -> dict:
    text = str(args.get("text") or "").strip() or "…"
    return {"ok": True, "output": text, "data": {"text": text}}


# ---------------------------------------------------------------------------
# self registration (importing the package registers every built-in tool)
# ---------------------------------------------------------------------------

from . import apps as _apps  # noqa: E402,F401
from . import browser as _browser  # noqa: E402,F401
from . import files as _files  # noqa: E402,F401
from . import powershell as _powershell  # noqa: E402,F401
from . import system as _system  # noqa: E402,F401
from . import websearch as _websearch  # noqa: E402,F401

__all__ = [
    "Tool",
    "register",
    "tool",
    "get_tool",
    "all_tools",
    "known_tool_names",
    "step_risk",
    "is_destructive",
    "catalog_text",
    "default_ctx",
    "coerce_args",
    "validate_step",
]

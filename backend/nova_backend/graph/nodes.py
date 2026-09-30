"""NOVA backend — LangGraph nodes: understand → plan → execute → verify."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

from ..llm import fallback_planner
from ..llm.huggingface_client import llm
from ..memory.store import store
from ..runtime.tasks import get_context
from ..tools import catalog_text, default_ctx, get_tool, step_risk, validate_step
from .state import NovaState

log = logging.getLogger("nova.graph")

CATEGORIES = fallback_planner.CATEGORIES
CONFIDENCE_FLOOR = 0.5
STEP_TIMEOUTS = {
    "run_powershell": 60,
    "browser_open": 60,
    "browser_extract": 75,
    "web_search": 45,
    "search_files": 45,
    "open_app": 30,
    "close_app": 40,
}
DEFAULT_STEP_TIMEOUT = 30


# ---------------------------------------------------------------------------
# understand
# ---------------------------------------------------------------------------

UNDERSTAND_SYSTEM = (
    "You are NOVA, the reasoning core of a personal Windows AI agent.\n"
    "Classify the user's request and reply with ONLY a single JSON object:\n"
    '{"category": "...", "action": "...", "summary": "...", "confidence": 0.0, "entities": {}}\n'
    "Rules:\n"
    "- category is one of: apps, files, browser, powershell, search, system, memory, chat\n"
    "- action: a short snake_case verb such as open_app, close_app, create_file, delete, "
    "search_web, run_powershell, system_info, remember, respond\n"
    "- summary: one short line (max 60 chars) describing what the user wants\n"
    "- confidence: 0..1\n"
    "- entities: the concrete values extracted (app, path, query, url, command, key, value)"
)


def understand(state: NovaState) -> dict:
    ctx = get_context(state["task_id"])
    user_input = str(state.get("user_input") or "")
    ctx.phase("understand", "running", "Reading your request", 0)

    try:
        # recent entries give continuity; Qdrant recall adds whatever is
        # semantically related to *this* request
        memory_context = store.recent_entries(5, user_id=ctx.user_id)
        related = store.search_memory(user_input, limit=3, user_id=ctx.user_id)
        seen = {line.lower() for line in memory_context}
        for hit in related:
            line = f"{hit['key']}: {hit['value']}"
            if line.lower() not in seen:
                memory_context.append(line)
                seen.add(line.lower())
        memory_context = memory_context[:8]
    except Exception as exc:  # pragma: no cover
        log.warning("memory load failed: %s", exc)
        memory_context = []
    if memory_context:
        ctx.log(f"Context loaded · {len(memory_context)} memory entries", "done")
    else:
        ctx.log("No prior memory — starting fresh", "done")

    intent: Optional[dict] = None
    if llm.available:
        try:
            messages = [
                {"role": "system", "content": UNDERSTAND_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        ("".join(f"- {line}\n" for line in memory_context) or "")
                        + f"User request: {user_input}"
                    ),
                },
            ]
            intent = _normalise_intent(llm.chat_json(messages, temperature=0.0, max_tokens=500))
        except Exception as exc:
            log.info("LLM intent failed (%s) — using the rule engine", exc)
            ctx.log("Model unavailable · rule engine took over", "done")
            intent = None
    else:
        ctx.log("Offline mode · rule engine classifying", "done")

    if not intent:
        intent = fallback_planner.infer_intent(user_input)

    ctx.emit(
        {
            "type": "intent",
            "category": intent.get("category", "chat"),
            "action": intent.get("action", "respond"),
            "summary": intent.get("summary", ""),
            "confidence": round(float(intent.get("confidence", 0.5)), 2),
        }
    )
    ctx.phase("understand", "done", "Intent parsed · Context loaded", 15)
    return {
        "intent": intent,
        "memory_context": memory_context,
        "phase": "understand",
        "status": "running",
    }


def _normalise_intent(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    category = str(raw.get("category") or "").strip().lower()
    if category not in CATEGORIES:
        return None
    try:
        confidence = float(raw.get("confidence", 0))
    except Exception:
        confidence = 0.0
    if confidence < CONFIDENCE_FLOOR:
        return None
    entities = raw.get("entities")
    if not isinstance(entities, dict):
        entities = {}
    return {
        "category": category,
        "action": str(raw.get("action") or "respond")[:60],
        "summary": str(raw.get("summary") or category)[:120],
        "confidence": max(0.0, min(confidence, 1.0)),
        "entities": entities,
    }


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

PLAN_SYSTEM = (
    "You are NOVA's planner for a Windows PC assistant.\n"
    "Reply ONLY with a JSON object of this shape:\n"
    '{"steps": [{"tool": "open_app", "title": "Open Notepad", "detail": "...", "args": {"app": "notepad"}}]}\n'
    "Rules:\n"
    "- Only use tool names that appear in the catalog below, with exactly their arguments.\n"
    "- Multi-step tasks are encouraged (e.g. web_search then browser_open).\n"
    "- `destructive` is decided by the system, never by you.\n"
    "- Use an empty args object only for tools that take none.\n"
    "- If nothing in the catalog fits, return {\"steps\": []}.\n\n"
    "CATALOG:\n"
)


def plan(state: NovaState) -> dict:
    ctx = get_context(state["task_id"])
    user_input = str(state.get("user_input") or "")
    intent = state.get("intent") or {}
    ctx.phase("plan", "running", "Designing an action plan", 15)

    raw_steps: list = []
    if llm.available:
        try:
            catalog = catalog_text()
            messages = [
                {"role": "system", "content": PLAN_SYSTEM + catalog},
                {
                    "role": "user",
                    "content": (
                        f"Intent: {json.dumps(intent, ensure_ascii=False)}\n"
                        f"Request: {user_input}\n"
                        "Plan the steps."
                    ),
                },
            ]
            payload = llm.chat_json(messages, temperature=0.0, max_tokens=1200)
            raw_steps = _extract_steps(payload)
        except Exception as exc:
            log.info("LLM plan failed (%s) — falling back to rules", exc)
            ctx.log("Planner model unavailable · rule engine planning", "done")
            raw_steps = []

    steps = _validate_plan(raw_steps)
    if not steps:
        steps = _validate_plan(fallback_planner.build_plan(user_input, intent))
        if raw_steps:
            ctx.log("Discarded invalid model plan · rule engine planning", "done")

    if not steps:
        ctx.phase("plan", "done", "0 actions queued", 30)
        return {
            "plan": [],
            "phase": "plan",
            "status": "failed",
            "error": "No executable plan could be built for that request.",
        }

    ctx.emit(
        {
            "type": "plan",
            "steps": [
                {
                    "id": s["id"],
                    "tool": s["tool"],
                    "title": s["title"],
                    "detail": s["detail"],
                    "destructive": s["destructive"],
                    "status": "pending",
                }
                for s in steps
            ],
        }
    )
    ctx.log(f"Validated {len(steps)} actions against the tool registry", "done")
    ctx.phase("plan", "done", f"{len(steps)} actions queued", 30)
    return {"plan": steps, "cursor": 0, "phase": "plan", "status": "running"}


def _extract_steps(payload: Any) -> list:
    if isinstance(payload, list):
        return [s for s in payload if isinstance(s, dict)]
    if isinstance(payload, dict):
        for key in ("steps", "plan", "actions"):
            value = payload.get(key)
            if isinstance(value, list):
                return [s for s in value if isinstance(s, dict)]
    return []


def _validate_plan(raw_steps: list) -> list[dict]:
    """Validate against the registry and recompute `destructive` from it."""
    validated: list[dict] = []
    for raw in raw_steps or []:
        step = validate_step(raw)
        if step is None:
            log.info("dropping unknown tool in plan: %r", raw)
            continue
        risk = step_risk(step["tool"], step["args"])  # registry is the authority
        step["risk"] = risk
        step["destructive"] = risk is not None
        step["id"] = len(validated) + 1
        step["status"] = "pending"
        validated.append(step)
        if len(validated) >= 12:
            break
    return validated


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def execute(state: NovaState) -> dict:
    ctx = get_context(state["task_id"])
    steps: list[dict] = list(state.get("plan") or [])
    results: list[dict] = list(state.get("results") or [])
    cursor = int(state.get("cursor") or 0)
    total = len(steps)

    if not steps:
        return {
            "phase": "execute",
            "status": "failed",
            "error": "empty plan",
            "cursor": 0,
            "results": results,
        }

    ctx.phase("execute", "running", f"Running task {min(cursor + 1, total)} of {total}", 30)
    tool_ctx = default_ctx()
    tool_ctx["user_id"] = ctx.user_id
    index = cursor

    while index < total:
        step = steps[index]
        step_progress = 30 + int(55 * index / total)
        ctx.emit(
            {
                "type": "step_start",
                "id": step["id"],
                "index": index + 1,
                "total": total,
                "title": step["title"],
                "progress": step_progress,
            }
        )

        # -- confirmation gate (registry risk only) ------------------------
        risk = step.get("risk") or step_risk(step["tool"], step.get("args") or {})
        if risk and not step.get("_approved"):
            ctx.log(f"Confirmation required · {risk}", "active")
            approved = ctx.confirm(step, risk)
            if not approved:
                results.append(
                    _record(step, "rejected", "Declined by user", {"ok": False, "output": "Declined by user", "data": {"risk": risk}}, step.get("args") or {})
                )
                ctx.emit(
                    {
                        "type": "step_done",
                        "id": step["id"],
                        "status": "rejected",
                        "detail": "Declined by user",
                        "output": risk,
                    }
                )
                ctx.log(f"{step['title']} · declined", "failed")
                index += 1
                continue
            step["_approved"] = True
            ctx.log(f"{step['title']} · approved", "done")

        # -- argument templating (e.g. "{{1.data.results.0.url}}") ---------
        args, resolved_ok = _resolve_args(step.get("args") or {}, results)
        if not resolved_ok:
            results.append(
                _record(step, "skipped", "Missing reference to an earlier result", {"ok": False, "output": "skipped", "data": {}}, step.get("args") or {})
            )
            ctx.emit(
                {
                    "type": "step_done",
                    "id": step["id"],
                    "status": "skipped",
                    "detail": "Missing reference to an earlier result",
                    "output": "",
                }
            )
            index += 1
            continue

        # -- run with a per-step timeout -----------------------------------
        tool = get_tool(step["tool"])
        if tool is None:  # pragma: no cover - validated earlier
            results.append(_record(step, "skipped", "Unknown tool", {"ok": False, "output": "unknown tool", "data": {}}, args))
            ctx.emit({"type": "step_done", "id": step["id"], "status": "skipped", "detail": "Unknown tool", "output": ""})
            index += 1
            continue

        ctx.log(f"{tool.name} · running", "active")
        result = _run_handler(tool, args, tool_ctx, STEP_TIMEOUTS.get(tool.name, DEFAULT_STEP_TIMEOUT))
        status = "done" if result.get("ok") else "failed"
        output = str(result.get("output") or "")
        detail = "Completed" if status == "done" else _clip(output.splitlines()[0] if output else "failed", 120)
        results.append(_record(step, status, detail, result, args))
        ctx.emit(
            {
                "type": "step_done",
                "id": step["id"],
                "status": status,
                "detail": detail,
                "output": _clip(output, 400),
            }
        )
        ctx.log(f"{step['title']} · {status}", "done" if status == "done" else "failed")
        ctx.progress(30 + int(55 * (index + 1) / total))
        index += 1

    ctx.phase("execute", "done", f"{sum(1 for r in results if r['status'] == 'done')} of {total} actions completed", 85)
    return {"cursor": total, "results": results, "phase": "execute", "status": "running"}


def _record(step: dict, status: str, detail: str, result: dict, args: dict) -> dict:
    return {
        "step_id": step.get("id"),
        "tool": step.get("tool"),
        "title": step.get("title"),
        "args": args,
        "status": status,
        "detail": detail,
        "output": str(result.get("output") or ""),
        "result": result,
    }


def _clip(text: Any, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _run_handler(tool, args: dict, tool_ctx: dict, timeout: int) -> dict:
    """Run a tool handler on a helper thread with a hard timeout."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = tool.handler(args, tool_ctx)
        except Exception as exc:  # tools must not raise, but never trust that
            box["result"] = {"ok": False, "output": f"{type(exc).__name__}: {exc}", "data": {}}

    worker = threading.Thread(target=target, daemon=True, name=f"nova-tool-{tool.name}")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return {
            "ok": False,
            "output": f"**{tool.name}** timed out after {timeout}s",
            "data": {"timed_out": True, "timeout": timeout},
        }
    result = box.get("result")
    if not isinstance(result, dict):  # pragma: no cover
        return {"ok": False, "output": "tool returned no result", "data": {}}
    return result


def _resolve_args(args: dict, results: list[dict]) -> tuple[dict, bool]:
    resolved: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
            found, looked_up = _lookup(value[2:-2].strip(), results)
            if not found:
                return {}, False
            if isinstance(looked_up, (dict, list)):
                looked_up = json.dumps(looked_up, ensure_ascii=False)
            resolved[key] = looked_up
        else:
            resolved[key] = value
    return resolved, True


def _lookup(path: str, results: list[dict]) -> tuple[bool, Any]:
    parts = [p for p in str(path).split(".") if p]
    if not parts:
        return False, None
    head = parts[0]
    if head == "last":
        entry = results[-1] if results else None
    else:
        entry = next((r for r in results if str(r.get("step_id")) == str(head)), None)
    if entry is None:
        return False, None
    node: Any = entry.get("result")
    for part in parts[1:]:
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.lstrip("-").isdigit():
            try:
                node = node[int(part)]
            except Exception:
                return False, None
        elif isinstance(node, dict) and part in entry:
            node = entry[part]
        else:
            return False, None
    return True, node


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def verify(state: NovaState) -> dict:
    ctx = get_context(state["task_id"])
    results = list(state.get("results") or [])
    intent = state.get("intent") or {}

    ctx.phase("verify", "running", "Awaiting results", 85)

    checks: list[dict] = []
    failures: list[str] = []
    for item in results:
        if item.get("status") != "done":
            continue
        tool = get_tool(item.get("tool") or "")
        if tool is None or tool.verify is None:
            continue
        try:
            issue = tool.verify(item.get("args") or {}, item.get("result") or {})
        except Exception as exc:  # pragma: no cover
            issue = f"verification error: {exc}"
        issue_text = str(issue).strip() if issue else ""
        if issue_text.lower().startswith(("ok:", "ok —", "ok -")):
            issue_text = ""  # explicit pass note
        checks.append({"tool": tool.name, "ok": not issue_text, "note": issue_text})
        if issue_text:
            failures.append(f"{tool.name}: {issue_text}")
            ctx.log(f"{tool.name} · {issue_text}", "failed")
        else:
            ctx.log(f"{tool.name} · verified", "done")

    status = _final_status(results)
    summary = _summary(intent, results, status)
    message = _compose(intent, results, checks, failures, state, status)

    ctx.phase(
        "verify",
        "done",
        "All checks passed" if not failures else f"{len(failures)} of {len(checks)} checks failed",
        100,
    )
    ctx.emit(
        {
            "type": "message",
            "role": "nova",
            "content": message,
            "tokens": max(1, round(len(message) / 4)),
            "ts": _now(),
        }
    )
    return {
        "phase": "verify",
        "status": status,
        "summary": summary,
        "results": results,
        "error": "; ".join(failures)[:300],
    }


def _now() -> str:
    from ..memory.store import now_iso

    return now_iso()


def _final_status(results: list[dict]) -> str:
    if not results:
        return "completed"
    statuses = [r.get("status") for r in results]
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s == "rejected" for s in statuses):
        return "rejected"
    return "completed"


def _summary(intent: dict, results: list[dict], status: str) -> str:
    base = str(intent.get("summary") or "").strip()
    done = sum(1 for r in results if r.get("status") == "done")
    if status == "failed":
        return f"{base or 'Task'} · {done}/{len(results)} actions succeeded"
    if status == "rejected":
        return f"{base or 'Task'} · declined by you"
    if not base:
        base = results[0]["title"] if results else "Task complete"
    if len(results) > 1:
        return f"{base} · {done}/{len(results)} actions"
    return base


def _compose(
    intent: dict,
    results: list[dict],
    checks: list[dict],
    failures: list[str],
    state: NovaState,
    status: str,
) -> str:
    # pure chat answers come straight from the respond tool
    if len(results) == 1 and results[0].get("tool") == "respond" and results[0].get("status") == "done":
        return str(results[0].get("output") or "").strip()

    lines: list[str] = []
    if status == "failed":
        lines.append("**NOVA couldn't finish that task.**")
    elif status == "rejected":
        lines.append("**Stopped — you declined an action.**")
    else:
        lines.append(f"**{intent.get('summary') or 'Task complete'}**")

    lines.append("")
    for item in results:
        mark = {"failed": "✗", "rejected": "→ declined", "skipped": "· skipped"}.get(
            item.get("status"), "→"
        )
        title = item.get("title") or item.get("tool") or "step"
        out_lines = [ln for ln in str(item.get("output") or "").splitlines() if ln.strip()]
        if item.get("status") != "done":
            lines.append(f"{mark} **{title}** — {item.get('detail') or item.get('status')}")
        elif len(out_lines) <= 1:
            lines.append(f"{mark} **{title}**" + (f" — {_clip(out_lines[0], 240)}" if out_lines else ""))
        else:
            lines.append(f"{mark} **{title}**")
            lines.extend(_clip(line, 300) for line in out_lines[:25])
        lines.append("")

    if checks:
        if failures:
            lines.append(f"**Verification** — {len(checks) - len(failures)}/{len(checks)} checks passed")
            for failure in failures[:5]:
                lines.append(f"→ ⚠ {failure}")
        else:
            lines.append(f"**Verified** — all {len(checks)} checks passed")

    if status == "failed" and state.get("error"):
        lines.append("")
        lines.append(f"→ {state['error']}")

    text = "\n".join(line for line in lines if line != "").strip()
    if len(text) > 6000:
        text = text[:5997] + "…"
    return text


__all__ = ["understand", "plan", "execute", "verify"]

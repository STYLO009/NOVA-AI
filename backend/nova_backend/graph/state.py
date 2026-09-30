"""NOVA backend — LangGraph state definition."""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class NovaState(TypedDict, total=False):
    task_id: str
    session_id: str
    user_input: str
    intent: dict
    plan: list
    cursor: int
    results: list
    status: str
    phase: str
    summary: str
    error: str
    memory_context: list


PHASES = ("understand", "plan", "execute", "verify")
STATUSES = ("queued", "running", "awaiting_confirmation", "completed", "failed", "rejected")


def initial_state(task_id: str, session_id: str, user_input: str) -> NovaState:
    return NovaState(
        task_id=task_id,
        session_id=session_id,
        user_input=user_input,
        intent={},
        plan=[],
        cursor=0,
        results=[],
        status="queued",
        phase="",
        summary="",
        error="",
        memory_context=[],
    )


__all__ = ["NovaState", "initial_state", "PHASES", "STATUSES"]

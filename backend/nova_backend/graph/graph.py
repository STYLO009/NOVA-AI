"""NOVA backend — LangGraph pipeline wiring.

``START → understand → plan → execute → (conditional) → verify → END``

The checkpointer is a ``MemorySaver`` keyed by ``thread_id`` (= task id) so a
task can be resumed/inspected while it runs.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from langgraph.graph import END, START, StateGraph

try:  # langgraph >= 1.0
    from langgraph.checkpoint.memory import InMemorySaver as MemorySaver
except ImportError:  # langgraph 0.2.x
    from langgraph.checkpoint.memory import MemorySaver

from .nodes import execute, plan, understand, verify
from .state import NovaState, initial_state

log = logging.getLogger("nova.graph")

_lock = threading.Lock()
_GRAPH = None


def _route_after_execute(state: NovaState) -> str:
    """Skip verification only when the pipeline failed before doing any work."""
    if str(state.get("status") or "") == "failed" and not state.get("results"):
        return "end"
    return "verify"


def build_graph():
    global _GRAPH
    with _lock:
        if _GRAPH is None:
            builder = StateGraph(NovaState)
            builder.add_node("understand", understand)
            builder.add_node("plan", plan)
            builder.add_node("execute", execute)
            builder.add_node("verify", verify)

            builder.add_edge(START, "understand")
            builder.add_edge("understand", "plan")
            builder.add_edge("plan", "execute")
            builder.add_conditional_edges(
                "execute",
                _route_after_execute,
                {"verify": "verify", "end": END},
            )
            builder.add_edge("verify", END)

            try:
                _GRAPH = builder.compile(checkpointer=MemorySaver())
            except TypeError:  # pragma: no cover - very old langgraph
                _GRAPH = builder.checkpointer(MemorySaver()).compile()
            log.debug("langgraph pipeline compiled")
    return _GRAPH


def run_task(task_id: str, session_id: str, user_input: str) -> NovaState:
    """Execute the full pipeline for one user request (blocking)."""
    graph = build_graph()
    state = initial_state(task_id, session_id, user_input)
    config = {"configurable": {"thread_id": task_id}}
    result = graph.invoke(state, config)
    return result if isinstance(result, dict) else dict(state)


__all__ = ["build_graph", "run_task"]

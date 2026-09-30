"""NOVA backend — task manager: background threads, event bus, confirmation gate."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..config import settings
from ..memory.store import now_iso, store

log = logging.getLogger("nova.tasks")

TERMINAL = {"completed", "failed", "rejected"}

#: task events mirrored to the global realtime bus (telemetry, not history)
GLOBAL_TASK_EVENTS = {"started", "confirmation", "confirmation_resolved", "done"}


# ---------------------------------------------------------------------------
# per task context handed to the graph nodes
# ---------------------------------------------------------------------------


@dataclass
class TaskContext:
    task_id: str
    emit: Callable[[dict], None]
    confirm: Callable[[dict, str], bool]
    #: owning user — scopes memory reads/writes to this account
    user_id: Optional[int] = None

    # -- convenience emitters --------------------------------------------
    def phase(self, phase: str, status: str, detail: str, progress: int) -> None:
        self.emit(
            {
                "type": "phase",
                "phase": phase,
                "status": status,
                "detail": str(detail),
                "progress": int(progress),
            }
        )

    def log(self, text: str, status: str = "active") -> None:
        self.emit({"type": "log", "text": str(text), "status": status})

    def progress(self, value: int) -> None:
        self.emit({"type": "progress", "value": int(value)})


_CONTEXTS: dict[str, TaskContext] = {}
_CTX_LOCK = threading.Lock()


def set_context(task_id: str, ctx: TaskContext) -> None:
    with _CTX_LOCK:
        _CONTEXTS[task_id] = ctx


def get_context(task_id: str) -> TaskContext:
    with _CTX_LOCK:
        ctx = _CONTEXTS.get(task_id)
    if ctx is None:
        raise RuntimeError(f"no execution context for task {task_id}")
    return ctx


def clear_context(task_id: str) -> None:
    with _CTX_LOCK:
        _CONTEXTS.pop(task_id, None)


# ---------------------------------------------------------------------------
# task record
# ---------------------------------------------------------------------------


@dataclass
class TaskRecord:
    id: str
    input: str
    session_id: str
    created_at: str
    created_ts: float
    cond: threading.Condition = field(default_factory=threading.Condition)
    events: list = field(default_factory=list)
    status: str = "queued"
    phase: str = ""
    progress: int = 0
    steps: list = field(default_factory=list)
    summary: str = ""
    final_message: str = ""
    message_sent: bool = False
    duration_ms: int = 0
    finished: bool = False
    pending: Optional[dict] = None  # {"step", "event", "approved"}
    #: owning user (None = legacy/unauthenticated row)
    user_id: Optional[int] = None


class TaskManager:
    """Runs the LangGraph pipeline on a worker thread and broadcasts events."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        #: task_id -> last confirmation decision
        self.decision: dict[str, bool] = {}

    # -- lifecycle --------------------------------------------------------
    def start(
        self, user_input: str, session_id: str = "", user_id: Optional[int] = None
    ) -> str:
        task_id = uuid.uuid4().hex[:20]
        created_ts = time.time()
        record = TaskRecord(
            id=task_id,
            input=user_input,
            session_id=session_id or "",
            created_at=now_iso(),
            created_ts=created_ts,
            user_id=int(user_id) if user_id else None,
        )
        with self._lock:
            self._tasks[task_id] = record
            self._order.append(task_id)
        self.emit(record, {"type": "started", "task_id": task_id, "input": user_input, "ts": now_iso()})
        thread = threading.Thread(
            target=self._run, args=(record,), daemon=True, name=f"nova-task-{task_id[:8]}"
        )
        thread.start()
        return task_id

    def _run(self, record: TaskRecord) -> None:
        started = time.time()
        status = "completed"
        summary = ""
        try:
            from ..graph.graph import run_task  # lazy: avoids an import cycle

            ctx = TaskContext(
                task_id=record.id,
                emit=lambda event: self.emit(record, event),
                confirm=lambda step, risk: self.wait_confirmation(record.id, step, risk),
                user_id=record.user_id,
            )
            set_context(record.id, ctx)
            state = run_task(record.id, record.session_id, record.input) or {}
            status = str(state.get("status") or "completed")
            summary = str(state.get("summary") or "")
        except Exception as exc:  # noqa: BLE001 - a task must never kill the process
            log.exception("task %s crashed", record.id)
            status = "failed"
            summary = f"Task failed: {exc}"
            try:
                self.emit(record, {"type": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            clear_context(record.id)

        duration_ms = int((time.time() - started) * 1000)
        with record.cond:
            already_finished = record.finished
        try:
            if not record.message_sent:
                text = record.final_message or summary or "Task finished."
                if status == "failed" and not record.final_message:
                    text = f"**NOVA hit an error.**\n→ {summary}"
                self.emit(
                    record,
                    {
                        "type": "message",
                        "role": "nova",
                        "content": text,
                        "tokens": max(1, round(len(text) / 4)),
                        "ts": now_iso(),
                    },
                )
            if status not in TERMINAL:
                status = "completed" if status in {"", "running", "queued"} else "failed"
            if not summary:
                summary = self._summary_for(record, status)
            if not already_finished:
                self.emit(
                    record,
                    {
                        "type": "done",
                        "status": status,
                        "summary": summary,
                        "duration_ms": duration_ms,
                    },
                )
        except Exception:  # pragma: no cover
            log.exception("failed to finalise task %s", record.id)

        self._persist(record, status, summary, duration_ms)

    @staticmethod
    def _summary_for(record: TaskRecord, status: str) -> str:
        with record.cond:
            steps = list(record.steps)
        done = sum(1 for s in steps if s.get("status") == "done")
        if not steps:
            return record.input[:80]
        if status == "completed":
            return f"{done}/{len(steps)} actions completed"
        if status == "rejected":
            return "Declined by user"
        return f"{status} · {done}/{len(steps)} actions completed"

    def _persist(self, record: TaskRecord, status: str, summary: str, duration_ms: int) -> None:
        try:
            with record.cond:
                steps = [dict(s) for s in record.steps]
                phase = record.phase
                progress = record.progress
                message = record.final_message
            store.save_task(
                task_id=record.id,
                session_id=record.session_id,
                user_input=record.input,
                status=status,
                phase=phase,
                progress=progress,
                steps=steps,
                summary=summary,
                created_at=record.created_at,
                created_ts=record.created_ts,
                duration_ms=duration_ms,
                user_id=record.user_id,
            )
            if message:
                store.save_message(record.id, record.session_id, "nova", message, max(1, round(len(message) / 4)))
        except Exception:
            log.exception("could not persist task %s", record.id)

    # -- events -----------------------------------------------------------
    def emit(self, record: TaskRecord, event: dict) -> dict:
        with record.cond:
            return self._emit_locked(record, event)

    def _emit_locked(self, record: TaskRecord, event: dict) -> dict:
        payload = dict(event)
        payload["seq"] = len(record.events) + 1
        record.events.append(payload)
        self._track(record, payload)
        self._publish_global(record, payload)
        record.cond.notify_all()
        return payload

    @staticmethod
    def _publish_global(record: TaskRecord, event: dict) -> None:
        """Mirror the high-signal task events onto the global SSE bus."""
        kind = event.get("type")
        if kind not in GLOBAL_TASK_EVENTS:
            return
        try:
            from ..realtime import bus

            bus.publish(
                "task",
                {
                    "event": kind,
                    "task_id": record.id,
                    "session_id": record.session_id,
                    "user_id": record.user_id,
                    "status": record.status,
                    "progress": record.progress,
                    "phase": record.phase,
                    "input": record.input,
                    "step_title": str((event.get("step") or {}).get("title") or "")
                    or str(event.get("title") or ""),
                    "summary": str(event.get("summary") or ""),
                    "approved": event.get("approved"),
                },
            )
        except Exception:  # pragma: no cover - telemetry must never break a task
            log.debug("global publish failed for %s", record.id, exc_info=True)

    @staticmethod
    def _set_step(record: TaskRecord, step_id: Any, status: str) -> None:
        for step in record.steps:
            if str(step.get("id")) == str(step_id):
                step["status"] = status
                return

    def _track(self, record: TaskRecord, event: dict) -> None:
        kind = event.get("type")
        if kind == "started":
            record.status = "queued"
        elif kind == "phase":
            record.phase = event.get("phase") or record.phase
            record.progress = max(record.progress, int(event.get("progress") or 0))
            if event.get("status") == "running" and record.status in {"queued", "running"}:
                record.status = "running"
        elif kind == "progress":
            record.progress = max(record.progress, int(event.get("value") or 0))
        elif kind == "plan":
            record.steps = [
                {
                    "id": s.get("id"),
                    "title": s.get("title", ""),
                    "detail": s.get("detail", ""),
                    "tool": s.get("tool", ""),
                    "destructive": bool(s.get("destructive")),
                    "status": s.get("status", "pending"),
                }
                for s in event.get("steps") or []
            ]
        elif kind == "step_start":
            record.progress = max(record.progress, int(event.get("progress") or 0))
            self._set_step(record, event.get("id"), "active")
            if record.status in {"queued", "awaiting_confirmation"}:
                record.status = "running"
        elif kind == "step_done":
            self._set_step(record, event.get("id"), str(event.get("status") or "done"))
        elif kind == "confirmation":
            record.status = "awaiting_confirmation"
        elif kind == "confirmation_resolved":
            record.status = "running"
        elif kind == "message":
            record.message_sent = True
            record.final_message = str(event.get("content") or "")
        elif kind == "error":
            record.status = "failed"
        elif kind == "done":
            record.status = str(event.get("status") or record.status)
            record.summary = str(event.get("summary") or record.summary)
            record.duration_ms = int(event.get("duration_ms") or 0)
            record.finished = True

    # -- confirmation gate ------------------------------------------------
    def wait_confirmation(self, task_id: str, step: dict, risk: str, timeout: Optional[float] = None) -> bool:
        """Block until the UI decides (timeout ⇒ rejected)."""
        record = self._tasks.get(task_id)
        if record is None:
            return False
        gate = threading.Event()
        with record.cond:
            if record.finished:
                return False
            record.pending = {"step": step, "event": gate, "approved": None}
            self.decision.pop(task_id, None)
            self._emit_locked(
                record,
                {
                    "type": "confirmation",
                    "task_id": task_id,
                    "step": {
                        "id": step.get("id"),
                        "title": step.get("title", ""),
                        "detail": step.get("detail", ""),
                        "risk": risk,
                    },
                    "question": f"NOVA wants to run “{step.get('title', '')}” — {risk}. Allow it?",
                },
            )
        wait_for = float(timeout if timeout is not None else settings.confirmation_timeout)
        approved = gate.wait(timeout=wait_for)
        decision = bool(self.decision.get(task_id, False)) if approved else False
        with record.cond:
            record.pending = None
            self._emit_locked(
                record,
                {"type": "confirmation_resolved", "step_id": step.get("id"), "approved": decision},
            )
        if not approved:
            log.info("task %s: confirmation timed out after %ss", task_id, wait_for)
        return decision

    def decide(self, task_id: str, approved: bool) -> Optional[str]:
        """Resolve a pending confirmation. ``None`` when nothing is pending."""
        record = self._tasks.get(task_id)
        if record is None:
            return None
        with record.cond:
            pending = record.pending
            if pending is None:
                return None
            self.decision[task_id] = bool(approved)
            pending["approved"] = bool(approved)
            pending["event"].set()
            record.cond.notify_all()
            return "running"

    def has_pending(self, task_id: str) -> bool:
        record = self._tasks.get(task_id)
        if record is None:
            return False
        with record.cond:
            return record.pending is not None

    # -- reads ------------------------------------------------------------
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if not t.finished)

    def get(self, task_id: str) -> Optional[TaskRecord]:
        return self._tasks.get(task_id)

    def snapshot(self, task_id: str) -> Optional[dict]:
        record = self._tasks.get(task_id)
        if record is None:
            row = store.get_task(task_id)
            if not row:
                return None
            return {
                "id": row["id"],
                "input": row["input"],
                "status": row["status"],
                "phase": row["phase"],
                "progress": int(row["progress"] or 0),
                "steps": row["steps"],
                "summary": row["summary"],
                "created_at": row["created_at"],
                "duration_ms": int(row["duration_ms"] or 0),
            }
        with record.cond:
            return {
                "id": record.id,
                "input": record.input,
                "status": record.status,
                "phase": record.phase,
                "progress": record.progress,
                "steps": [dict(s) for s in record.steps],
                "summary": record.summary,
                "created_at": record.created_at,
                "duration_ms": record.duration_ms,
            }

    def event_count(self, task_id: str) -> int:
        record = self._tasks.get(task_id)
        if record is None:
            return 0
        with record.cond:
            return len(record.events)

    # -- SSE --------------------------------------------------------------
    def stream(self, task_id: str, from_seq: int = 0):
        """Replay events with ``seq > from_seq`` then follow live until ``done``.

        The lock must never be held across a ``yield``: Starlette drains this
        generator on its anyio worker pool, so a suspended generator can resume
        on a *different* thread and an ``RLock`` would then refuse to release
        (``cannot release un-acquired lock``) and leave the task deadlocked.
        """
        record = self._tasks.get(task_id)
        if record is None:
            return
        cursor = 0
        min_seq = max(int(from_seq or 0), 0)
        last_beat = time.monotonic()

        while True:
            with record.cond:
                batch = record.events[cursor:]
                cursor += len(batch)
                finished = record.finished
                if not batch and not finished:
                    record.cond.wait(timeout=1.0)

            if batch:
                for event in batch:
                    if int(event.get("seq") or 0) <= min_seq:
                        continue
                    yield f"data: {_dumps(event)}\n\n"
                    if event.get("type") == "done":
                        return
                continue

            if finished:
                return
            now = time.monotonic()
            if now - last_beat >= 15.0:
                last_beat = now
                yield ": keepalive\n\n"


def _dumps(payload: dict) -> str:
    import json

    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:  # pragma: no cover
        return json.dumps({"type": "error", "message": "unserialisable event"}, default=str)


manager = TaskManager()

__all__ = ["TaskManager", "TaskRecord", "TaskContext", "manager", "get_context", "set_context", "clear_context"]

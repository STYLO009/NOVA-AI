"""NOVA backend — global realtime event bus (one SSE feed for the whole app).

Everything that changes without the user asking for it is published here:
system telemetry, health, vector-store status and task lifecycle.  The
frontend keeps a single subscription open and drops per-panel polling.

Subscribers are plain :class:`queue.Queue` objects, so a slow or vanished
client can never block a producer — full queues are drained instead.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Iterator, Optional

log = logging.getLogger("nova.realtime")

QUEUE_SIZE = 64
KEEPALIVE_S = 15.0

#: event types every authenticated client sees
SYSTEM_TYPES = {"system", "health", "vector", "task", "notification"}


class EventBus:
    """Fan-out pub/sub with a monotonically increasing sequence number."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, queue.Queue] = {}
        self._next_id = 1
        self._seq = 0
        #: last payload per type so a late joiner sees current state
        self._latest: dict[str, dict] = {}

    # -- subscription -----------------------------------------------------
    def subscribe(self) -> tuple[int, queue.Queue]:
        q: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subscribers[sub_id] = q
            snapshot = [dict(v) for v in self._latest.values()]
        for item in snapshot:
            try:
                q.put_nowait(item)
            except queue.Full:  # pragma: no cover - defensive
                break
        return sub_id, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subscribers.pop(sub_id, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # -- publishing -------------------------------------------------------
    def publish(self, event_type: str, payload: Optional[dict] = None, **fields: Any) -> dict:
        with self._lock:
            self._seq += 1
            event = {
                "type": event_type,
                "seq": self._seq,
                "ts": time.time(),
                **(payload or {}),
                **fields,
            }
            if event_type in SYSTEM_TYPES:
                self._latest[event_type] = event
            subscribers = list(self._subscribers.values())
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # drop-oldest: a stalled client must not stall everyone else
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):  # pragma: no cover
                    pass
        return event

    # -- streaming --------------------------------------------------------
    def stream(self, q: queue.Queue, heartbeat: float = KEEPALIVE_S) -> Iterator[dict]:
        """Yields events forever; the caller wraps this in a StreamingResponse."""
        while True:
            try:
                yield q.get(timeout=heartbeat)
            except queue.Empty:
                yield {"type": "ping", "ts": time.time()}


bus = EventBus()

__all__ = ["EventBus", "bus", "KEEPALIVE_S"]

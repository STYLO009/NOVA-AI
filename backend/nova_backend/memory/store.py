"""NOVA backend — SQLite history + memory store."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..config import BACKEND_DIR

log = logging.getLogger("nova.store")

DATA_DIR = BACKEND_DIR / "data"
DB_PATH = DATA_DIR / "nova.db"


def now_iso() -> str:
    """Local time, ISO-8601 (parseable by ``new Date(...)`` in the client)."""
    return datetime.now().isoformat(timespec="seconds")


class MemoryStore:
    """Small thread-safe SQLite wrapper (history + long term memory)."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()

    # -- schema ----------------------------------------------------------
    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    salt          TEXT NOT NULL,
                    iterations    INTEGER DEFAULT 390000,
                    created_at    TEXT,
                    created_ts    REAL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token      TEXT PRIMARY KEY,
                    user_id    INTEGER NOT NULL,
                    created_at TEXT,
                    created_ts REAL,
                    expires_ts REAL,
                    user_agent TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id          TEXT PRIMARY KEY,
                    user_id     INTEGER,
                    session_id  TEXT,
                    input       TEXT,
                    status      TEXT,
                    phase       TEXT,
                    progress    INTEGER DEFAULT 0,
                    steps_json  TEXT DEFAULT '[]',
                    summary     TEXT DEFAULT '',
                    created_at  TEXT,
                    created_ts  REAL,
                    duration_ms INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id    TEXT,
                    session_id TEXT,
                    role       TEXT,
                    content    TEXT,
                    tokens     INTEGER DEFAULT 0,
                    created_at TEXT,
                    created_ts REAL
                );
                CREATE TABLE IF NOT EXISTS memory (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER,
                    key        TEXT NOT NULL,
                    value      TEXT DEFAULT '',
                    created_at TEXT,
                    created_ts REAL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_ts DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                """
            )
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        for table, column, ddl in (
            ("tasks", "user_id", "INTEGER"),
            ("memory", "user_id", "INTEGER"),
        ):
            try:
                columns = {
                    row["name"]
                    for row in self._conn.execute(f"PRAGMA table_info({table})")
                }
                if column not in columns:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                    )
            except Exception:  # pragma: no cover - defensive
                log.warning("migration failed for %s.%s", table, column)

    # -- users / sessions -------------------------------------------------
    def create_user(
        self,
        username: str,
        password_hash: str,
        salt: str,
        iterations: int,
    ) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO users (username, password_hash, salt, iterations, created_at, created_ts)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    username.strip(),
                    password_hash,
                    salt,
                    int(iterations),
                    now_iso(),
                    time.time(),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return self._user_row(row)

    def get_user(self, username: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                ((username or "").strip(),),
            ).fetchone()
        return self._user_row(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (int(user_id),)
            ).fetchone()
        return self._user_row(row) if row else None

    def count_users(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"] if row else 0)

    def create_session(
        self,
        token: str,
        user_id: int,
        expires_ts: float,
        user_agent: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, created_ts, expires_ts, user_agent)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    token,
                    int(user_id),
                    now_iso(),
                    time.time(),
                    float(expires_ts),
                    (user_agent or "")[:200],
                ),
            )
            self._conn.commit()
        return {"token": token, "user_id": int(user_id), "expires_ts": float(expires_ts)}

    def get_session(self, token: str) -> Optional[dict[str, Any]]:
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE token = ?", (token,)
            ).fetchone()
        if row is None:
            return None
        if float(row["expires_ts"] or 0) < time.time():
            self.delete_session(token)
            return None
        return {
            "token": row["token"],
            "user_id": int(row["user_id"]),
            "expires_ts": float(row["expires_ts"] or 0),
            "created_at": row["created_at"],
        }

    def delete_session(self, token: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE token = ?", (token,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def purge_expired_sessions(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE expires_ts < ?", (time.time(),)
            )
            self._conn.commit()
            return cur.rowcount

    @staticmethod
    def _user_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "username": row["username"],
            "password_hash": row["password_hash"],
            "salt": row["salt"],
            "iterations": int(row["iterations"] or 390000),
            "created_at": row["created_at"],
        }

    # -- tasks -----------------------------------------------------------
    def save_task(
        self,
        task_id: str,
        session_id: str,
        user_input: str,
        status: str,
        phase: str,
        progress: int,
        steps: list[dict],
        summary: str,
        created_at: str,
        created_ts: float,
        duration_ms: int,
        user_id: Optional[int] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (id, user_id, session_id, input, status, phase, progress,
                                   steps_json, summary, created_at, created_ts, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status, phase=excluded.phase, progress=excluded.progress,
                    steps_json=excluded.steps_json, summary=excluded.summary,
                    duration_ms=excluded.duration_ms
                """,
                (
                    task_id,
                    int(user_id) if user_id else None,
                    session_id,
                    user_input,
                    status,
                    phase,
                    int(progress),
                    json.dumps(steps or [], ensure_ascii=False),
                    summary or "",
                    created_at,
                    created_ts,
                    int(duration_ms),
                ),
            )
            self._conn.commit()

    def get_task(self, task_id: str, user_id: Optional[int] = None) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        task = self._task_row(row)
        # Cross-user reads are denied; tasks owned by nobody stay visible
        # (legacy rows and e2e runs without an authenticated session).
        owner = row["user_id"]
        if owner is not None and user_id is not None and int(owner) != int(user_id):
            return None
        return task

    def list_tasks(
        self, limit: int = 20, user_id: Optional[int] = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 20), 500))
        with self._lock:
            if user_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM tasks ORDER BY created_ts DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE user_id = ?"
                    " ORDER BY created_ts DESC LIMIT ?",
                    (int(user_id), limit),
                ).fetchall()
        return [self._task_row(r) for r in rows]

    @staticmethod
    def _task_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            steps = json.loads(row["steps_json"] or "[]")
        except Exception:
            steps = []
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "input": row["input"],
            "status": row["status"],
            "phase": row["phase"],
            "progress": row["progress"],
            "steps": steps,
            "steps_count": len(steps) if isinstance(steps, list) else 0,
            "summary": row["summary"],
            "created_at": row["created_at"],
            "created_ts": row["created_ts"],
            "duration_ms": row["duration_ms"],
        }

    def save_message(
        self, task_id: str, session_id: str, role: str, content: str, tokens: int = 0
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (task_id, session_id, role, content, tokens, created_at, created_ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, session_id, role, content, int(tokens), now_iso(), time.time()),
            )
            self._conn.commit()

    # -- memory ----------------------------------------------------------
    def add_memory(
        self, key: str, value: str, user_id: Optional[int] = None
    ) -> dict[str, Any]:
        key = (key or "").strip()
        value = value if value is not None else ""
        owner = int(user_id) if user_id else None
        with self._lock:
            if owner is None:
                existing = self._conn.execute(
                    "SELECT id FROM memory WHERE lower(key) = lower(?) ORDER BY id DESC LIMIT 1",
                    (key,),
                ).fetchone()
            else:
                existing = self._conn.execute(
                    "SELECT id FROM memory WHERE lower(key) = lower(?) AND user_id = ?"
                    " ORDER BY id DESC LIMIT 1",
                    (key, owner),
                ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE memory SET value = ?, created_at = ?, created_ts = ? WHERE id = ?",
                    (value, now_iso(), time.time(), existing["id"]),
                )
                row_id = existing["id"]
            else:
                cur = self._conn.execute(
                    "INSERT INTO memory (user_id, key, value, created_at, created_ts)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (owner, key, value, now_iso(), time.time()),
                )
                row_id = cur.lastrowid
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM memory WHERE id = ?", (row_id,)
            ).fetchone()
        entry = self._memory_row(row)
        self._index_vectors([entry])
        return entry

    def list_memory(self, user_id: Optional[int] = None) -> list[dict[str, Any]]:
        with self._lock:
            if user_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM memory ORDER BY id DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memory WHERE user_id = ? ORDER BY id DESC",
                    (int(user_id),),
                ).fetchall()
        return [self._memory_row(r) for r in rows]

    def delete_memory(self, item_id: int, user_id: Optional[int] = None) -> bool:
        with self._lock:
            if user_id is None:
                cur = self._conn.execute(
                    "DELETE FROM memory WHERE id = ?", (int(item_id),)
                )
            else:
                cur = self._conn.execute(
                    "DELETE FROM memory WHERE id = ? AND user_id = ?",
                    (int(item_id), int(user_id)),
                )
            self._conn.commit()
            deleted = cur.rowcount > 0
        if deleted:
            from .vectors import vectors

            vectors.delete(int(item_id))
        return deleted

    def search_memory(
        self, query: str = "", limit: int = 20, user_id: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Semantic recall through Qdrant first, SQL ``LIKE`` as the fallback.

        A hit is only returned for the requesting user; unowned (legacy)
        entries remain visible to everyone so history never disappears.
        """
        limit = max(1, int(limit or 20))
        q = (query or "").strip()
        if q:
            from .vectors import vectors

            hits = vectors.search(q, limit=limit * 2, payload_filter=None)
            if hits:
                by_id = {int(m["id"]): m for m in self.list_memory(user_id=user_id)}
                results = []
                for hit in hits:
                    item = by_id.get(int(hit["payload"].get("item_id", -1)))
                    if item:
                        item = dict(item, score=hit["score"])
                        results.append(item)
                if results:
                    return results[:limit]
        items = self.list_memory(user_id=user_id)
        if q:
            ql = q.lower()
            items = [
                i
                for i in items
                if ql in i["key"].lower() or ql in i["value"].lower()
            ]
        return items[:limit]

    def recent_entries(self, limit: int = 5, user_id: Optional[int] = None) -> list[str]:
        """Recent memory entries formatted as ``key: value`` context lines."""
        return [
            f"{i['key']}: {i['value']}"
            for i in self.list_memory(user_id=user_id)[: max(1, limit)]
        ]

    def reindex_all(self) -> int:
        """(Re)builds the Qdrant collection from SQLite — used at boot and by
        the settings view so a fresh database is never unsearchable."""
        from .vectors import vectors  # local import: keeps store import-safe

        if not vectors.ensure():
            return 0
        return int(self._index_vectors(self.list_memory()))

    @staticmethod
    def _index_vectors(entries: list[dict[str, Any]]) -> int:
        from .vectors import vectors  # local import: keeps store import-safe

        written = 0
        for entry in entries:
            text = f"{entry['key']} {entry['value']}".strip()
            payload = {
                "item_id": int(entry["id"]),
                "key": entry["key"],
                "value": entry["value"],
            }
            if entry.get("user_id") is not None:
                payload["user_id"] = int(entry["user_id"])
            if vectors.upsert(int(entry["id"]), text, payload):
                written += 1
        return written

    @staticmethod
    def _memory_row(row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": int(row["id"]),
            "key": row["key"],
            "value": row["value"],
            "created_at": row["created_at"],
            "user_id": int(row["user_id"]) if "user_id" in keys and row["user_id"] else None,
        }

    # -- misc ------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


store = MemoryStore()

__all__ = ["MemoryStore", "store", "DB_PATH", "now_iso"]

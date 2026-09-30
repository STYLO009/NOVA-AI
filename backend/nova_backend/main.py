"""NOVA backend — FastAPI application (REST + SSE).

Route policy
------------
* ``/api/health``, ``/api/auth/login``, ``/api/auth/register``, ``/`` and the
  OpenAPI docs are public — the login screen needs them before a session exists.
* Every other ``/api/*`` route requires a session token (unless
  ``NOVA_AUTH_ENABLED=false``) and only ever sees the caller's own data.
* ``/api/stream`` is the single realtime feed: system, health, vector and task
  events in one SSE connection instead of per-panel polling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import auth as auth_service
from .config import settings, setup_logging
from .llm.huggingface_client import llm
from .memory.store import store
from .memory.vectors import vectors
from .realtime import bus
from .runtime.tasks import manager
from .schemas import (
    AuthRequest,
    ChatRequest,
    DecisionRequest,
    MemoryCreate,
    MemorySearchRequest,
    RegisterRequest,
)

setup_logging()
log = logging.getLogger("nova.api")
settings.ensure_workspace()

#: telemetry cadence for the global stream
SYSTEM_TICK_S = 2.5
HEALTH_TICK_S = 15.0


# ---------------------------------------------------------------------------
# lifespan: telemetry publisher + memory reindex
# ---------------------------------------------------------------------------


async def _telemetry_loop() -> None:
    """Publishes system/health/vector events while anyone is listening."""
    loop = asyncio.get_running_loop()
    last_system = last_health = 0.0
    while True:
        now = time.monotonic()
        if bus.subscriber_count == 0:
            last_system = last_health = 0.0
            await asyncio.sleep(1.0)
            continue
        if now - last_system >= SYSTEM_TICK_S:
            last_system = now
            try:
                payload = await loop.run_in_executor(None, _collect_system)
                bus.publish("system", payload)
            except Exception as exc:  # pragma: no cover - telemetry is best effort
                log.debug("system tick failed: %s", exc)
        if now - last_health >= HEALTH_TICK_S:
            last_health = now
            try:
                bus.publish("health", _health_payload())
                bus.publish("vector", vectors.info())
            except Exception as exc:  # pragma: no cover
                log.debug("health tick failed: %s", exc)
        await asyncio.sleep(0.5)


def _collect_system() -> dict:
    from .tools.system import collect_system

    return collect_system()


def _health_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "1.0.0",
        "llm_mode": llm.mode(),
        "model": llm.model_name,
        "workspace": str(settings.workspace),
        "features": _features(),
        "auth": settings.auth_enabled,
        "users": store.count_users() if settings.auth_enabled else 0,
        "uptime_s": round(time.time() - APP_STARTED, 1),
    }


def _features() -> dict[str, bool]:
    """Capabilities are platform dependent — Windows-only tools are reported
    as unavailable when the backend runs in the Linux Docker image."""
    windows = settings.is_windows
    return {
        "apps": windows,
        "files": True,
        "browser": True,
        "powershell": windows,
        "search": True,
        "system": True,
        "voice": bool(settings.hf_token),
        "auth": settings.auth_enabled,
        "vectors": vectors.available,
    }


APP_STARTED = time.time()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_telemetry_loop())
    loop = asyncio.get_running_loop()
    try:
        # Connect the vector store first: with an empty database nothing else
        # would touch it, and /api/health would report "disabled" forever.
        await loop.run_in_executor(None, vectors.ensure)
        # Then (re)build it from SQLite so an existing memory database is
        # searchable from the very first request.
        await loop.run_in_executor(None, store.reindex_all)
    except Exception as exc:  # pragma: no cover
        log.warning("memory reindex skipped: %s", exc)
    try:
        store.purge_expired_sessions()
    except Exception:  # pragma: no cover
        pass
    log.info("NOVA backend up (auth=%s, vectors=%s)", settings.auth_enabled, vectors.mode)
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # pragma: no cover
            pass


app = FastAPI(
    title="NOVA Backend",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# authentication plumbing
# ---------------------------------------------------------------------------

#: paths reachable without a session (the login screen and ops probes)
PUBLIC_PATHS = {
    "/",
    "/api/health",
    "/api/auth/login",
    "/api/auth/register",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def _extract_token(request: Request) -> Optional[str]:
    token = auth_service.token_from_header(request.headers.get("authorization"))
    if token:
        return token
    return request.cookies.get("nova_session")


def _resolve_user(request: Request) -> Optional[dict[str, Any]]:
    return auth_service.authenticate(_extract_token(request))


async def current_user(request: Request) -> Optional[dict[str, Any]]:
    """Optional dependency: the caller if authenticated, else ``None``."""
    if not settings.auth_enabled:
        return None
    user = getattr(request.state, "user", None)
    if user is None:
        user = _resolve_user(request)
        request.state.user = user
    return user


async def require_user(request: Request) -> dict[str, Any]:
    """Hard dependency: 401 when no valid session token is presented."""
    if not settings.auth_enabled:
        return {"id": None, "username": "local"}
    user = getattr(request.state, "user", None)
    if user is None:
        user = _resolve_user(request)
        request.state.user = user
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return user


def _uid(user: Optional[dict[str, Any]]) -> Optional[int]:
    if not user:
        return None
    value = user.get("id")
    return int(value) if value else None


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "anon"


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # CORS preflights carry no credentials and must never be answered with a
    # 401 — the browser would block the whole cross-origin call before it even
    # reaches the API. `CORSMiddleware` sits outside this layer and replies to
    # them; skipping here keeps that true even if the order changes later.
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_PATHS and settings.auth_enabled:
        request.state.user = None
        user = _resolve_user(request)
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "Sign in to continue"})
        request.state.user = user
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# `*` (default) => open CORS *without* credentials; the frontend authenticates
# with `Authorization: Bearer`, so it never needs cookie-based CORS. An explicit
# origin list switches on credentialed requests for exactly those origins.
#
# Registered last, so Starlette puts it OUTSIDE the two middlewares above.
# That matters: it lets CORS answer preflights itself *and* stamp the
# `Access-Control-Allow-Origin` header on every response — including 401s — so
# the browser reports "session expired" instead of an opaque CORS failure.
_ALLOW_ALL = settings.cors_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=not _ALLOW_ALL,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Token"],
)


# ---------------------------------------------------------------------------
# auth routes
# ---------------------------------------------------------------------------


@app.post("/api/auth/register")
def register(body: RegisterRequest, request: Request) -> dict:
    if not settings.auth_enabled:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    try:
        user = auth_service.register(body.username, body.password)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    session = auth_service.create_session(int(user["id"]), request.headers.get("user-agent", ""))
    payload = {"user": user, "token": session["token"], "expires_ts": session["expires_ts"]}
    response = JSONResponse(content=payload)
    _attach_session_cookie(response, session)
    return response


@app.post("/api/auth/login")
def login(body: AuthRequest, request: Request) -> dict:
    if not settings.auth_enabled:
        raise HTTPException(status_code=403, detail="Authentication is disabled")
    try:
        result = auth_service.login(
            body.username, body.password, request.headers.get("user-agent", "")
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    response = JSONResponse(content=result)
    _attach_session_cookie(response, {"token": result["token"]})
    return response


@app.post("/api/auth/logout")
def logout(request: Request) -> dict:
    auth_service.logout(_extract_token(request))
    response = JSONResponse(content={"ok": True})
    response.delete_cookie("nova_session", path="/")
    return response


@app.get("/api/auth/me")
def me(user: dict = Depends(require_user)) -> dict:
    return {"user": user}


def _attach_session_cookie(response: JSONResponse, session: dict) -> None:
    """Same-origin deployments (Docker/nginx) also get an HttpOnly cookie."""
    response.set_cookie(
        "nova_session",
        session["token"],
        max_age=int(settings.session_ttl_hours * 3600),
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    payload = _health_payload()
    payload["vector"] = vectors.info()
    payload["tasks_active"] = manager.active_count()
    return payload


# ---------------------------------------------------------------------------
# realtime: the single global stream
# ---------------------------------------------------------------------------


@app.get("/api/stream")
async def stream(request: Request, _user: dict = Depends(require_user)):
    sub_id, queue = bus.subscribe()

    async def generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        asyncio.to_thread(queue.get, True, 15.0), timeout=20.0
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    event = {"type": "ping", "ts": time.time()}
                except Exception:  # queue shut down
                    break
                import json as _json

                yield f"data: {_json.dumps(event, default=str)}\n\n"
        finally:
            bus.unsubscribe(sub_id)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# chat / tasks
# ---------------------------------------------------------------------------


@app.post("/api/chat")
def chat(body: ChatRequest, user: dict = Depends(require_user)) -> dict:
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="`message` must not be empty")
    task_id = manager.start(message, body.session_id or "", user_id=_uid(user))
    log.info("queued task %s for %r", task_id, message[:60])
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, user: dict = Depends(require_user)) -> dict:
    snapshot = manager.snapshot(task_id)
    if snapshot is None:
        stored = store.get_task(task_id, user_id=_uid(user))
        if stored is None:
            raise HTTPException(status_code=404, detail="unknown task")
        return stored
    owner = store.get_task(task_id, user_id=_uid(user))
    if owner is None and settings.auth_enabled:
        raise HTTPException(status_code=404, detail="unknown task")
    return snapshot


@app.get("/api/tasks/{task_id}/events")
def task_events(
    task_id: str,
    from_: int = Query(0, alias="from"),
    user: dict = Depends(require_user),
) -> StreamingResponse:
    if manager.get(task_id) is None and store.get_task(task_id, user_id=_uid(user)) is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return StreamingResponse(
        manager.stream(task_id, from_),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/tasks/{task_id}/decision")
def task_decision(
    task_id: str, body: DecisionRequest, user: dict = Depends(require_user)
) -> dict:
    status = manager.decide(task_id, bool(body.approved))
    if status is None:
        raise HTTPException(status_code=404, detail="no pending confirmation")
    return {"ok": True, "status": status}


# ---------------------------------------------------------------------------
# history / memory
# ---------------------------------------------------------------------------


@app.get("/api/history")
def history(
    limit: int = Query(20, ge=1, le=500), user: dict = Depends(require_user)
) -> dict:
    rows = store.list_tasks(limit, user_id=_uid(user))
    return {
        "tasks": [
            {
                "id": row["id"],
                "input": row["input"],
                "summary": row["summary"],
                "status": row["status"],
                "steps": int(row.get("steps_count") or len(row.get("steps") or [])),
                "phase": row["phase"],
                "created_at": row["created_at"],
                "duration_ms": int(row["duration_ms"] or 0),
            }
            for row in rows
        ]
    }


@app.get("/api/memory")
def list_memory(user: dict = Depends(require_user)) -> dict:
    return {"items": store.list_memory(user_id=_uid(user))}


@app.post("/api/memory/search")
def search_memory(
    body: MemorySearchRequest, user: dict = Depends(require_user)
) -> dict:
    """Semantic recall through Qdrant (SQL ``LIKE`` fallback when offline)."""
    items = store.search_memory(
        query=body.query or "", limit=body.limit, user_id=_uid(user)
    )
    return {"query": body.query, "items": items, "engine": vectors.mode}


@app.post("/api/memory")
def create_memory(body: MemoryCreate, user: dict = Depends(require_user)) -> dict:
    key = (body.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="`key` must not be empty")
    return {"ok": True, "item": store.add_memory(key, body.value or "", user_id=_uid(user))}


@app.delete("/api/memory/{item_id}")
def delete_memory(item_id: int, user: dict = Depends(require_user)) -> dict:
    store.delete_memory(item_id, user_id=_uid(user))
    return {"ok": True}


@app.post("/api/memory/reindex")
def reindex_memory(user: dict = Depends(require_user)) -> dict:
    indexed = store.reindex_all()
    bus.publish("vector", vectors.info())
    return {"ok": True, "indexed": indexed, "vector": vectors.info()}


# ---------------------------------------------------------------------------
# machine facing data
# ---------------------------------------------------------------------------


@app.get("/api/system")
def system(user: dict = Depends(require_user)) -> dict:
    return _collect_system()


@app.get("/api/apps")
def apps(user: dict = Depends(require_user)) -> dict:
    if not settings.is_windows:
        return {"apps": [], "supported": False}
    from .tools.apps import detect_apps

    return {"apps": detect_apps(limit=8), "supported": True}


@app.get("/api/files/recent")
def recent_files(
    limit: int = Query(5, ge=1, le=100), user: dict = Depends(require_user)
) -> dict:
    from .tools.files import ago, ext_color

    root = Path(settings.ensure_workspace())
    candidates: list[Path] = []
    scanned = 0
    try:
        for path in root.rglob("*"):
            scanned += 1
            if scanned > 20000 or len(candidates) >= 2000:
                break
            try:
                if not path.is_file():
                    continue
                relative = path.relative_to(root)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                candidates.append(path)
            except Exception:
                continue
    except Exception as exc:  # pragma: no cover
        log.warning("recent file scan failed: %s", exc)

    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    files = []
    for path in candidates[:limit]:
        try:
            stat = path.stat()
        except Exception:
            continue
        files.append(
            {
                "name": path.name,
                "ext": path.suffix.lstrip(".").lower() or "file",
                "time": ago(stat.st_mtime),
                "color": ext_color(path),
                "path": str(path),
            }
        )
    return {"files": files}


@app.get("/api/files/list")
def list_files(
    path: str = Query("", description="workspace relative path"),
    user: dict = Depends(require_user),
) -> dict:
    """Browses the workspace for the Files view (directories + files only)."""
    from .tools.files import ext_color, resolve_path, workspace

    root = workspace()
    try:
        target = resolve_path(path) if path else root
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="directory not found")

    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith("."):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "relative": str(child.relative_to(root)) if child.is_relative_to(root) else str(child),
                    "is_dir": child.is_dir(),
                    "size": int(stat.st_size),
                    "ext": child.suffix.lstrip(".").lower() if child.is_file() else "dir",
                    "color": ext_color(child),
                    "time": ago(stat.st_mtime),
                }
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="permission denied") from exc
    return {
        "path": str(target),
        "relative": str(target.relative_to(root)) if target.is_relative_to(root) else "",
        "parent": str(target.parent) if target != root else "",
        "entries": entries[:500],
    }


# ---------------------------------------------------------------------------
# voice (browser Web Speech API is the primary path)
# ---------------------------------------------------------------------------


def _extract_multipart_file(content_type: str, body: bytes) -> Optional[bytes]:
    """Stdlib multipart/form-data parsing (python-multipart is not a dependency)."""
    if not content_type or "multipart/form-data" not in content_type:
        return None
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    try:
        message = BytesParser(policy=policy.default).parsebytes(header + body)
    except Exception:
        return None
    fallback: Optional[bytes] = None
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            continue
        if not payload:
            continue
        disposition = (part.get_content_disposition() or "").lower()
        name = (part.get_param("name", header="content-disposition") or "").lower()
        filename = part.get_filename()
        if filename or name == "file" or disposition == "attachment":
            return payload
        if fallback is None:
            fallback = payload
    return fallback


@app.post("/api/voice/transcribe")
async def transcribe(request: Request, _user: dict = Depends(current_user)):
    if not settings.hf_token:
        return JSONResponse(
            status_code=503,
            content={"error": "HF_TOKEN not configured — voice transcription disabled"},
        )
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    audio = _extract_multipart_file(content_type, body)
    if not audio:
        return JSONResponse(
            status_code=400,
            content={"error": "multipart field `file` with audio data is required"},
        )
    try:
        text = llm.transcribe(audio)
    except Exception as exc:
        log.warning("transcription failed: %s", exc)
        return JSONResponse(status_code=502, content={"error": f"transcription failed: {exc}"})
    return {"text": text}


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


@app.get("/")
def root() -> dict:
    return {
        "name": "NOVA backend",
        "version": "1.0.0",
        "health": "/api/health",
        "stream": "/api/stream",
        "docs": "/docs",
        "auth": settings.auth_enabled,
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


__all__ = ["app"]

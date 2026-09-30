"""End-to-end smoke test for the production NOVA backend.

Run:  .venv/Scripts/python.exe scripts/smoke_backend.py
Exits non-zero on the first failed assertion.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

import requests

# Override with NOVA_BASE=http://127.0.0.1:8010 to test a container instead of
# the local dev server.
BASE = os.environ.get("NOVA_BASE", "http://127.0.0.1:8000").rstrip("/")
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    mark = "ok  " if ok else "FAIL"
    print(f"[{mark}] {name}{'' if ok else f' -> {detail}'}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    # 1. health is public and reports auth + vector status
    r = requests.get(f"{BASE}/api/health", timeout=10)
    health = r.json()
    check("health is public", r.status_code == 200)
    check("health has vector info", "vector" in health, health.keys())
    check("health reports auth_enabled", health.get("auth") is True, health)
    check(
        "vector store connected (server or embedded Qdrant)",
        health.get("vector", {}).get("mode") in ("server", "local"),
        health.get("vector"),
    )
    print(f"       vector mode={health['vector']['mode']} "
          f"embeddings={health['vector']['embeddings']}")
    # The container image must not expose Windows-only tools.
    feats = health.get("features", {})
    if os.name != "nt":
        check(
            "windows-only tools report unsupported on this platform",
            feats.get("apps") is False and feats.get("powershell") is False,
            feats,
        )

    # 2. unauthenticated access to protected routes is rejected
    r = requests.get(f"{BASE}/api/history", timeout=10)
    check("history requires a session", r.status_code == 401, r.status_code)
    r = requests.get(f"{BASE}/api/stream", timeout=10, stream=True)
    check("stream requires a session", r.status_code == 401, r.status_code)

    # 3. register (unique so repeated runs work) + login
    name = f"smoke_{uuid.uuid4().hex[:8]}"
    pw = "s3cret-pass"
    r = requests.post(f"{BASE}/api/auth/register", json={"username": name, "password": pw}, timeout=10)
    check("register creates a session", r.status_code == 200, r.text)
    token = r.json().get("token")
    check("register returns a token", bool(token))
    hdr = {"Authorization": f"Bearer {token}"}

    r = requests.get(f"{BASE}/api/auth/me", headers=hdr, timeout=10)
    check("me returns the user", r.status_code == 200 and r.json()["user"]["username"] == name, r.text)

    # 4. weak passwords / duplicate usernames are rejected
    r = requests.post(f"{BASE}/api/auth/register", json={"username": name, "password": pw}, timeout=10)
    check("duplicate username -> 409", r.status_code == 409, r.status_code)
    r = requests.post(f"{BASE}/api/auth/register", json={"username": "ab", "password": pw}, timeout=10)
    check("short username -> 400/422", r.status_code in (400, 422), r.status_code)
    r = requests.post(f"{BASE}/api/auth/register", json={"username": f"pw_{uuid.uuid4().hex[:6]}", "password": "123"}, timeout=10)
    check("short password -> 400/422", r.status_code in (400, 422), r.status_code)
    r = requests.post(f"{BASE}/api/auth/login", json={"username": name, "password": "wrong"}, timeout=10)
    check("bad password -> 401", r.status_code == 401, r.status_code)
    r = requests.post(f"{BASE}/api/auth/login", json={"username": name, "password": pw}, timeout=10)
    check("login succeeds", r.status_code == 200, r.text)
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}

    # 5. per-user isolation: a second account sees nothing of the first
    other = f"smoke2_{uuid.uuid4().hex[:8]}"
    r2 = requests.post(f"{BASE}/api/auth/register", json={"username": other, "password": pw}, timeout=10)
    hdr2 = {"Authorization": f"Bearer {r2.json()['token']}"}

    # 6. memory: create -> list -> semantic search -> delete, isolated per user
    r = requests.post(f"{BASE}/api/memory", headers=hdr, json={"key": "favorite editor", "value": "VS Code"}, timeout=10)
    check("memory create", r.status_code == 200, r.text)
    item_id = r.json()["item"]["id"]

    r = requests.get(f"{BASE}/api/memory", headers=hdr, timeout=10)
    keys = [i["key"] for i in r.json()["items"]]
    check("memory list contains the entry", "favorite editor" in keys, keys)
    r = requests.get(f"{BASE}/api/memory", headers=hdr2, timeout=10)
    keys2 = [i["key"] for i in r.json()["items"]]
    check("memory is isolated per user", "favorite editor" not in keys2, keys2)

    r = requests.post(f"{BASE}/api/memory/search", headers=hdr, json={"query": "which editor do i like", "limit": 5}, timeout=20)
    body = r.json()
    check("semantic search responds", r.status_code == 200, r.text)
    check("semantic search returns hits", len(body["items"]) > 0, body)
    check("semantic search reports an engine", body.get("engine") in ("local", "server"), body)

    r = requests.post(f"{BASE}/api/memory/reindex", headers=hdr, timeout=20)
    check("reindex works", r.status_code == 200 and isinstance(r.json().get("indexed"), int), r.text)

    r = requests.delete(f"{BASE}/api/memory/{item_id}", headers=hdr2, timeout=10)
    check("other user cannot delete my memory", requests.get(f"{BASE}/api/memory", headers=hdr, timeout=10).json()["items"] is not None)
    r = requests.get(f"{BASE}/api/memory", headers=hdr, timeout=10)
    check("memory still there after foreign delete", "favorite editor" in [i["key"] for i in r.json()["items"]])
    requests.delete(f"{BASE}/api/memory/{item_id}", headers=hdr, timeout=10)

    # 7. realtime stream: subscribe, publish a task, see it, then history
    r = requests.get(f"{BASE}/api/stream", headers=hdr, timeout=15, stream=True)
    check("stream is authenticated sse", r.status_code == 200 and "text/event-stream" in r.headers.get("content-type", ""), r.headers)

    frames: list[dict] = []
    it = r.iter_lines(decode_unicode=True)
    deadline = time.time() + 6

    def pump(until: str | None = None, timeout: float = 6.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = next(it)
            except StopIteration:
                return
            except Exception:
                return
            if line and line.startswith("data:"):
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                frames.append(ev)
                if until and ev.get("type") == until:
                    return

    pump(timeout=3.0)
    check("stream delivers realtime frames", len(frames) > 0, frames[:3])
    check("stream carries a system/health/vector frame",
          any(f.get("type") in ("system", "health", "vector", "ping") for f in frames),
          [f.get("type") for f in frames])

    msg = "remember that my favourite terminal is Windows Terminal"
    r = requests.post(f"{BASE}/api/chat", headers=hdr, json={"message": msg}, timeout=15)
    check("chat queues a task", r.status_code == 200, r.text)
    task_id = r.json().get("task_id")

    pump(until="task", timeout=8.0)
    check("task events appear on the global stream",
          any(f.get("type") == "task" for f in frames),
          [f.get("type") for f in frames][-6:])

    # wait for the task to finish via the task-scoped stream
    seen: list[dict] = []
    done = False
    for line in requests.get(f"{BASE}/api/tasks/{task_id}/events", headers=hdr, timeout=20, stream=True).iter_lines(decode_unicode=True):
        if line and line.startswith("data:"):
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            seen.append(ev)
            if ev.get("type") in ("done", "error"):
                done = True
                break
    check("task stream terminates", done, [e.get("type") for e in seen][-6:])
    check("task emitted a plan", any(e.get("type") == "plan" for e in seen), [e.get("type") for e in seen])

    # 8. history is scoped to the owner
    r = requests.get(f"{BASE}/api/history", headers=hdr, timeout=10)
    hist = r.json()["tasks"]
    check("history has the task", any(t["id"] == task_id for t in hist), hist[:1])
    r = requests.get(f"{BASE}/api/history", headers=hdr2, timeout=10)
    check("history isolated per user", not any(t["id"] == task_id for t in r.json()["tasks"]))
    r = requests.get(f"{BASE}/api/tasks/{task_id}", headers=hdr2, timeout=10)
    check("task detail hidden from other users", r.status_code == 404, r.status_code)

    # 9. filesystem listing (works on Linux too)
    r = requests.get(f"{BASE}/api/files/list", headers=hdr, timeout=15)
    check("file browser works", r.status_code == 200 and "entries" in r.json(), r.text[:200])
    r = requests.get(f"{BASE}/api/system", headers=hdr, timeout=15)
    check("system telemetry works", r.status_code == 200 and "cpu" in r.json(), r.text[:200])
    r = requests.get(f"{BASE}/api/apps", headers=hdr, timeout=15)
    check("apps endpoint answers", r.status_code == 200 and "supported" in r.json(), r.text[:200])
    if os.name != "nt":
        check("apps endpoint reports supported=false here", r.json()["supported"] is False, r.text[:200])

    # 10. logout invalidates the token
    r = requests.post(f"{BASE}/api/auth/logout", headers=hdr, timeout=10)
    check("logout ok", r.status_code == 200, r.text)
    r = requests.get(f"{BASE}/api/auth/me", headers=hdr, timeout=10)
    check("token revoked after logout", r.status_code == 401, r.status_code)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("all backend smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

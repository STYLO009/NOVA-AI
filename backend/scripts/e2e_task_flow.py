"""Full agent round trip: create → destructive confirmation → approve → verify.

Unlike ``scripts/e2e.ts`` (which provisions the victim file on the host), this
drives everything through the API, so it works against the Dockerised backend
too — the agent creates and deletes the file itself, inside its own workspace.

    .venv/Scripts/python.exe scripts/e2e_task_flow.py
    NOVA_BASE=http://127.0.0.1:8010 .venv/Scripts/python.exe scripts/e2e_task_flow.py
"""

from __future__ import annotations

import json
import os
import sys
import time

import requests

BASE = os.environ.get("NOVA_BASE", "http://127.0.0.1:8000").rstrip("/")
FILENAME = "flow-test.txt"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"[{'ok  ' if ok else 'FAIL'}] {name}{'' if ok else f' -> {detail}'}")
    if not ok:
        FAILURES.append(name)


def sign_in() -> dict[str, str]:
    """Anonymous access is rejected, so register a throwaway account."""
    r = requests.get(f"{BASE}/api/history", timeout=10)
    if r.status_code != 401 and r.status_code != 200:
        raise SystemExit(f"unexpected status from /api/history: {r.status_code}")
    if r.status_code == 200:
        print("       auth disabled — continuing anonymously")
        return {}
    name = f"flow_{int(time.time())}"
    res = requests.post(
        f"{BASE}/api/auth/register",
        json={"username": name, "password": "flow-password-123"},
        timeout=15,
    )
    res.raise_for_status()
    print(f"       signed in as {res.json()['user']['username']}")
    return {"Authorization": f"Bearer {res.json()['token']}"}


def run_task(
    hdr: dict[str, str],
    message: str,
    *,
    approve: bool = False,
    timeout: float = 120.0,
) -> tuple[str, list[dict]]:
    """Start a chat task and stream its events until it finishes.

    When ``approve`` is set, any confirmation is answered from inside the read
    loop — the same thing a user clicking **Approve** in the UI does.
    """
    r = requests.post(
        f"{BASE}/api/chat", headers=hdr, json={"message": message}, timeout=20
    )
    r.raise_for_status()
    task_id = r.json()["task_id"]

    events: list[dict] = []
    decided = False
    stream = requests.get(
        f"{BASE}/api/tasks/{task_id}/events",
        headers=hdr,
        params={"from": 0},
        timeout=(15, timeout),
        stream=True,
    )
    deadline = time.time() + timeout
    for line in stream.iter_lines(decode_unicode=True):
        if time.time() > deadline:
            events.append({"type": "timeout"})
            break
        if not line or not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except Exception:
            continue
        events.append(ev)
        etype = ev.get("type")
        if etype == "confirmation" and approve and not decided:
            decided = True
            time.sleep(0.4)  # let the UI-side gate settle
            d = requests.post(
                f"{BASE}/api/tasks/{task_id}/decision",
                headers=hdr,
                json={"approved": True},
                timeout=20,
            )
            print(f"       approval answered: HTTP {d.status_code} {d.text[:80]}")
        if etype in ("done", "error"):
            break
    return task_id, events


def kinds(events: list[dict]) -> str:
    return " -> ".join(str(e.get("type")) for e in events)


def main() -> int:
    print(f"target: {BASE}")
    health = requests.get(f"{BASE}/api/health", timeout=10).json()
    check("backend healthy", health.get("status") == "ok", health)
    print(
        f"       vectors={health.get('vector', {}).get('mode')} "
        f"auth={health.get('auth')} features.apps={health.get('features', {}).get('apps')}"
    )
    hdr = sign_in()

    # 1 ── the agent creates a file in its own workspace (non-destructive)
    _, ev1 = run_task(
        hdr,
        f'create a file called {FILENAME} in my workspace with "hello from the flow test"',
    )
    check("create task finished", ev1 and ev1[-1].get("type") == "done", kinds(ev1))
    check("create task produced a plan", any(e.get("type") == "plan" for e in ev1), kinds(ev1))
    check(
        "create task raised no confirmation",
        not any(e.get("type") == "confirmation" for e in ev1),
        kinds(ev1),
    )

    # 2 ── deleting it is destructive: the graph must pause for approval
    tid, ev2 = run_task(hdr, f"delete {FILENAME} in my workspace", approve=True)
    check(
        "destructive task raised a confirmation",
        any(e.get("type") == "confirmation" for e in ev2),
        kinds(ev2),
    )
    check(
        "approval was consumed by the graph",
        any(e.get("type") == "confirmation_resolved" for e in ev2),
        kinds(ev2),
    )
    check(
        "task completed after approval",
        ev2 and ev2[-1].get("type") == "done",
        kinds(ev2),
    )

    # 3 ── the file is really gone
    _, ev3 = run_task(hdr, f"what is in my workspace")
    text = " ".join(
        str(e.get("content", "")) + " " + str(e.get("detail", ""))
        for e in ev3
        if e.get("type") in ("message", "log", "result")
    )
    check(
        f"{FILENAME} no longer in the workspace",
        FILENAME.lower() not in text.lower(),
        text[:300],
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print("full task flow passed (create -> confirm -> approve -> verify)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

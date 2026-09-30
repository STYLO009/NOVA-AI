# NOVA — the Personal Computer AI Agent

NOVA is a desktop AI agent that understands natural language (text **or voice**) and actually
*does things* on your machine: opens and closes applications, manages files and folders,
automates the browser, runs PowerShell, searches the web, reports system information, and plans
multi-step tasks — with a live **Understand → Plan → Execute → Verify** pipeline and an explicit
confirmation gate for destructive actions.

It is **neutral until you ask**: the app boots into a *Standing by* state with no seeded chat,
no invented telemetry and no fake history. Nothing runs until you send a command.

The UI is the Figma design, implemented pixel-for-pixel and wired to a real backend through APIs.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Frontend — Bun + React 19 + Vite 8 + Tailwind v4   (port 8443)             │
│  Figma UI: Login · Sidebar · Header · NOVA Chat · Pipeline · Voice Mode      │
│            Sections: Dashboard · Tasks · Apps · Files · History · Settings   │
│            ↕ REST  /api/*        ↕ SSE  /api/tasks/{id}/events              │
│                                 ↕ SSE  /api/stream   (one realtime feed)     │
│  Backend — Python + FastAPI + LangGraph + Hugging Face    (port 8000)        │
│  Graph: understand → plan → execute → (confirmation gate) → verify           │
│  Auth: signup / login, PBKDF2 passwords, bearer tokens + HttpOnly cookie     │
│  Tools: apps · files · browser · powershell · web_search · system · memory   │
│  Store: SQLite (users, sessions, task history, long-term memory)             │
│  Vectors: Qdrant (server → embedded → SQL LIKE fallback)                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Features

| Capability | How it works |
|---|---|
| **Neutral first state** | No seeded messages, logs, stats or history. The chat opens on *Standing by*; every panel renders an explicit empty state until real data arrives |
| **Multi-user accounts** | `POST /api/auth/register` / `login` → PBKDF2-HMAC-SHA256 password hash + random salt, opaque session tokens (TTL), throttled logins. Tasks and memory are scoped per user |
| **Realtime feed** | One SSE connection (`/api/stream`) broadcasts system telemetry, health, vector-store status and task lifecycle; polling only runs while reconnecting |
| **Working sidebar** | Dashboard (chat + right panel), **Tasks** (per-run plan/steps/re-run), **Apps** (launch by click), **Files** (workspace browser with breadcrumbs), **History** (task log + semantic recall), **Settings** (account, backend, Qdrant, memory manager) |
| **Text commands** | Chat input → `POST /api/chat` → LangGraph task → streamed back over SSE |
| **Voice commands** | Web Speech API in the chat bar and in the full-screen **Voice Mode** overlay (server-side Whisper transcription via Hugging Face as a fallback path) |
| **Open / close Windows apps** | `open_app` / `close_app` tools — alias table + `where.exe` resolution, `psutil` verification, system processes are protected |
| **File / folder management** | `list_dir`, `search_files`, `create_file`, `create_folder`, `read_file`, `rename_path`, `move_path`, `copy_path`, `delete_path` |
| **Browser automation** | Playwright session (`browser_open`, `browser_extract`) with stdlib `webbrowser` fallback |
| **PowerShell execution** | `run_powershell` with timeout, output capture and a destructive-pattern risk scanner |
| **Web search** | `ddgs` (DuckDuckGo) with an HTML-scrape fallback; can chain into `browser_open` |
| **System information** | `psutil` CPU / RAM / SSD / network / GPU temperature, streamed live into the right panel |
| **Multi-step planning** | The `plan` node emits an ordered step list; steps execute sequentially with per-step verification |
| **Task history & memory** | SQLite persistence — `/api/history` powers Tasks/History, `/api/memory` stores long-term facts |
| **Qdrant vector memory** | Every memory is embedded and upserted into a Qdrant collection; `POST /api/memory/search` does semantic recall (and feeds the planner with what is *relevant* to the request, not just the newest) |
| **Graceful degradation** | Server Qdrant → embedded local Qdrant → SQL `LIKE`. SQLite is always the source of truth, so memory works even with no vector DB |
| **Destructive-action confirmation** | The registry (not the LLM) decides risk. Risky steps pause the graph and raise a confirmation dialog until you **Approve** or **Reject** (180 s timeout = reject) |
| **Full stack in Docker** | `docker compose up --build` → nginx (SPA + reverse proxy), FastAPI, Qdrant |

---

## Repository layout

```
├── package.json              # Bun scripts: dev / build / preview / backend / e2e
├── bun.lock                  # Bun lockfile (this repo is Bun-first)
├── .env.example              # VITE_API_URL (use `/` for the Docker build)
├── start-windows.ps1         # Windows: install-if-needed + start both services
├── index.html                # Vite shell
├── vite.config.ts            # React + Tailwind v4 + @ alias
├── Dockerfile.frontend       # bun run build → nginx
├── Dockerfile.backend        # python:3.12-slim + uvicorn
├── docker-compose.yml        # frontend + backend + qdrant, named volumes
├── deploy/nginx.conf         # SPA + /api reverse proxy (SSE-safe)
├── scripts/e2e.ts            # end-to-end: signup → task → confirmation → recall
└── src/
    ├── App.tsx               # auth gate + section router
    ├── components/           # Sidebar · Header · ChatPanel · RightPanel · VoiceMode · ConfirmDialog · LoginScreen
    ├── views/SectionViews.tsx# Tasks · Apps · Files · History · Settings
    ├── store/NovaStore.tsx   # reducer fed by SSE (chat, pipeline, logs, realtime)
    ├── store/AuthStore.tsx   # signup / login / logout / session bootstrap
    ├── lib/api.ts            # typed REST client + SSE readers + token handling
    ├── lib/types.ts          # frontend ⇄ backend contract
    └── hooks/useSpeech.ts    # speech recognition + speech synthesis
└── backend/
    ├── requirements.txt      # Python dependencies (incl. qdrant-client)
    ├── .env.example          # every backend setting, documented
    ├── scripts/smoke_backend.py    # 39-check API/auth/vector smoke test
    ├── scripts/e2e_task_flow.py    # create → confirm → approve → verify (works in Docker)
    ├── run.ps1               # Windows launcher (venv + uvicorn)
    └── nova_backend/
        ├── main.py           # FastAPI app, auth middleware, REST + SSE routes
        ├── auth.py           # PBKDF2 hashing, sessions, login throttle
        ├── config.py         # settings from backend/.env
        ├── realtime.py       # global event bus behind /api/stream
        ├── graph/            # LangGraph state, nodes, compiled graph
        ├── llm/              # Hugging Face client + deterministic fallback planner
        ├── tools/            # apps · files · browser · powershell · websearch · system
        ├── memory/store.py   # SQLite users/sessions/history/memory + vector hooks
        ├── memory/vectors.py # Qdrant wrapper (server → embedded → disabled)
        └── runtime/tasks.py  # background task runner, event bus, confirmation gate
```

---

## Prerequisites

* **Bun** ≥ 1.1 — <https://bun.sh> (`powershell -c "irm bun.sh/install.ps1 | iex"`)
* **Python** 3.12+ and **uv** (recommended) — <https://docs.astral.sh/uv/>
* Optional for the full stack: **Docker Desktop** with the Compose plugin
* Windows 10/11 for the Windows-only tools (app control, PowerShell); on Linux/macOS those
  features report `supported: false` and everything else keeps working
* Optional: a **Hugging Face access token** (<https://huggingface.co/settings/tokens>) for LLM
  reasoning, natural-language summaries, Whisper transcription and HF embeddings
* Optional: `playwright install chromium` for full browser automation

---

## Setup

### 1. Frontend (Bun)

```bash
bun install          # installs dependencies and writes bun.lock
cp .env.example .env # optional — VITE_API_URL defaults to http://127.0.0.1:8000
bun run dev          # http://localhost:8443
```

### 2. Backend (Python + LangGraph + Hugging Face + Qdrant)

```bash
cd backend
cp .env.example .env          # add HF_TOKEN=hf_... to enable the LLM
uv venv .venv --python 3.13
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
uv run --python .venv/Scripts/python.exe -m uvicorn nova_backend.main:app --host 127.0.0.1 --port 8000
```

No `uv`? Use the bundled script (it creates the venv with the `py` launcher when needed):

```powershell
.\start-windows.ps1 -InstallOnly     # install everything
.\start-windows.ps1 -BackendOnly     # run just the backend
```

Open `http://localhost:8443`, choose **Sign up**, create the first account and start working.

### 3. Windows — run everything in one command

```powershell
# from the repository root, in PowerShell
Set-ExecutionPolicy -Scope Process Bypass -Force   # once, if script execution is blocked
.\start-windows.ps1
```

### 4. Full stack in Docker

```bash
docker compose up --build
# UI    http://localhost:8080        (nginx serves the build and proxies /api)
# API   http://localhost:8000        (direct, if you need it)
# Docs  http://localhost:8080/docs
# Qdrant dashboard: uncomment the `ports:` block under `qdrant` in
#                  docker-compose.yml, then http://localhost:6333/dashboard
```

Compose starts three services:

| Service | Image | Purpose |
|---|---|---|
| `frontend` | `Dockerfile.frontend` | `bun run build` → static bundle served by nginx, `/api` reverse-proxied with buffering off (SSE-safe) |
| `backend` | `Dockerfile.backend` | `python:3.12-slim`, non-root user, Windows-only tools auto-disabled on Linux |
| `qdrant` | `qdrant/qdrant` | vector DB on its own named volume, reachable only inside the compose network (uncomment its `ports:` block to open the dashboard on `:6333`); the backend still falls back to embedded mode if it is unavailable |

Persistent data lives in the `nova-data`, `nova-db` and `qdrant-data` volumes, so
`docker compose down` never loses accounts or memory. Override ports and credentials with the
variables in `docker-compose.yml` (`NOVA_PORT`, `NOVA_BACKEND_PORT`, `HF_TOKEN`, `NOVA_AUTH_ENABLED`, …).

> **Ports.** The compose backend publishes `8000` by default. If the local dev backend
> (`bun run backend`) is already running there, start the stack on another port instead:
> `NOVA_BACKEND_PORT=8010 docker compose up -d` — the UI stays on `http://localhost:8080`
> and nginx keeps proxying to the container, so nothing else changes.
> Likewise pick a free `NOVA_PORT` if `8080` is taken.

Equivalent **Bun commands**:

| Action | Command |
|---|---|
| Install frontend deps | `bun install` |
| Install backend deps | `bun run backend:install` |
| Dev server (frontend) | `bun run dev` |
| Dev server (backend) | `bun run backend` |
| Both (Windows) | `bun run start:windows` |
| Production build | `bun run build` |
| Preview build | `bun run preview` |
| Format sources | `bun run format` |
| Check formatting | `bun run format:check` |
| Typecheck | `bun run typecheck` |
| Unit tests | `bun test` |
| End-to-end API test | `bun run e2e` |
| Docker stack | `docker compose up --build` |

> Use Bun, not npm/yarn/pnpm: `bun install`, `bun run dev`, `bun run build`.

### Quality checks

```bash
bun run typecheck     # tsc --noEmit — 0 errors
bun test              # 6 reducer tests (neutral boot, SSE pipeline, gate, offline)
bun run format:check  # oxfmt --check over src, scripts, vite.config.ts, index.html
bun run e2e           # signup → 26-event SSE run → confirmation → semantic recall
```

`bun run e2e` expects the backend on `http://127.0.0.1:8000` (start it with `bun run backend`).

Backend tests are API-driven, so they run against the local server **or** a container
(`NOVA_BASE` picks the target):

```bash
cd backend
NOVA_BASE=http://127.0.0.1:8000 .venv/Scripts/python.exe scripts/smoke_backend.py    # auth, isolation, vectors, realtime, logout
NOVA_BASE=http://127.0.0.1:8010 .venv/Scripts/python.exe scripts/e2e_task_flow.py    # create → gate → approve → verify
```

---

## Configuration

### Frontend — `.env`

```dotenv
# Local dev: absolute backend origin
VITE_API_URL=http://127.0.0.1:8000
# Docker / same-origin build: set it to `/` (the build arg does this for you)
```

### Backend — `backend/.env`

See `backend/.env.example` for the full, commented list. The important groups:

```dotenv
# server
NOVA_HOST=127.0.0.1
NOVA_PORT=8000
NOVA_CORS_ORIGINS=*
NOVA_WORKSPACE=                        # empty => %USERPROFILE%\Documents\NOVA

# model (optional — the rule engine takes over without a token)
HF_TOKEN=
HF_MODEL=Qwen/Qwen2.5-7B-Instruct

# vector database (Qdrant)
QDRANT_URL=http://localhost:6333       # unreachable => embedded mode at QDRANT_LOCAL_PATH
QDRANT_COLLECTION=nova_memory
EMBEDDING_PROVIDER=auto                # auto | hf | hashing
EMBEDDING_DIM=384

# multi-user auth
NOVA_AUTH_ENABLED=true
SESSION_TTL_HOURS=168
PBKDF2_ITERATIONS=390000
```

**Without `HF_TOKEN` NOVA still works**: intent parsing, planning, verification and every tool
run through the deterministic planner (`llm_mode: "fallback"` in `/api/health`), and embeddings
fall back to deterministic signed feature hashing (384 dims, no network).

**Vector DB ladder** — tried in order, then it degrades:

1. `QDRANT_URL` reachable → **server mode** (production / Docker)
2. not reachable → **embedded mode** at `QDRANT_LOCAL_PATH` (a local file store, same API)
3. disabled → **SQL `LIKE` search** over SQLite

SQLite is the source of truth in all three; Qdrant only accelerates recall. `Settings →
Vector database → Reindex` rebuilds the collection from SQLite.

---

## Authentication

* **Sign up** creates a user (`username` 3–32 chars `[A-Za-z0-9_.-]`, password ≥ 8 chars).
* Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user random salt and per-user
  iteration count — no plaintext, no extra dependency.
* Login returns an opaque 32-byte session token (default TTL 7 days). The frontend keeps it in
  `localStorage` and sends `Authorization: Bearer <token>`; same-origin deployments (Docker)
  also get an `HttpOnly` cookie. `POST /api/auth/logout` revokes it server-side.
* Brute force is throttled per username + client (8 attempts / 5 minutes → HTTP 429).
* Everything under `/api/*` except `/api/health`, `/api/auth/login` and `/api/auth/register`
  requires a session, and each caller only ever sees **their own** tasks and memory — a
  foreign task id returns `404`, a foreign memory cannot be listed or deleted.
* Set `NOVA_AUTH_ENABLED=false` for single-user experiments; the login screen then shows a
  notice instead of a form.

---

## API contract

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | public — status, `llm_mode`, model, feature flags, vector status |
| POST | `/api/auth/register` | public — `{username, password}` → `{user, token, expires_ts}` |
| POST | `/api/auth/login` | public — `{username, password}` → `{user, token, expires_ts}` |
| POST | `/api/auth/logout` | revokes the presented token |
| GET | `/api/auth/me` | resolves the session back to a user |
| POST | `/api/chat` | `{message, session_id?}` → `{task_id}` — starts a background LangGraph run |
| GET | `/api/tasks/{id}` | task snapshot (phase, progress, steps, summary) |
| GET | `/api/tasks/{id}/events?from=0` | **SSE** stream of task events (`data:` frames only) |
| POST | `/api/tasks/{id}/decision` | `{approved: bool}` — resolves a pending confirmation |
| GET | `/api/stream` | **SSE** — the single realtime feed: `system`, `health`, `vector`, `task`, `ping` |
| GET | `/api/history?limit=20` | persisted task history for the signed-in user |
| GET/POST/DELETE | `/api/memory` | long-term memory (facts NOVA remembers) |
| POST | `/api/memory/search` | `{query, limit}` → semantic recall + the engine that served it |
| POST | `/api/memory/reindex` | rebuild the Qdrant collection from SQLite |
| GET | `/api/system` | CPU / RAM / SSD / network / GPU temperature |
| GET | `/api/apps` | detected Windows applications (`supported: false` on Linux) |
| GET | `/api/files/recent` | recently modified files in the workspace |
| GET | `/api/files/list?path=` | workspace browser (directories + files, sizes, mtimes) |
| POST | `/api/voice/transcribe` | multipart audio → text (Whisper via Hugging Face) |

Task SSE event types: `started`, `phase`, `intent`, `plan`, `log`, `step_start`, `step_done`,
`confirmation`, `confirmation_resolved`, `progress`, `message`, `done`, `error`.

Example session:

```powershell
$login = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/auth/login `
        -ContentType 'application/json' -Body '{"username":"alex","password":"…"}'
$hdr = @{ Authorization = "Bearer $($login.token)" }

$task = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat -Headers $hdr `
        -ContentType 'application/json' -Body '{"message":"open notepad"}'
curl.exe -N -H "Authorization: Bearer $($login.token)" `
        "http://127.0.0.1:8000/api/tasks/$($task.task_id)/events?from=0"
```

Destructive example (pause → approve → resume):

```powershell
$task = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat -Headers $hdr `
        -ContentType 'application/json' -Body '{"message":"delete nova-test.txt in my workspace"}'
# … the SSE stream emits a `confirmation` event and the UI shows the dialog …
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/tasks/$($task.task_id)/decision" `
        -Headers $hdr -ContentType 'application/json' -Body '{"approved":true}'
```

---

## Safety model

* The **tool registry** — never the LLM — decides whether a step is destructive.
* Destructive steps (close app, delete/rename/overwrite, risky PowerShell) always raise a
  confirmation dialog in the UI; nothing runs without an explicit **Approve**.
* Confirmation times out as **reject** after `CONFIRMATION_TIMEOUT_SECONDS`.
* PowerShell runs with `-NoProfile -NonInteractive`, a hard timeout and captured output.
* File operations resolve inside `NOVA_WORKSPACE` by default; operations elsewhere are treated
  as risky.
* Passwords are never stored or logged in clear text; session tokens are revocable and expire.
* Security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Cache-Control: no-store` on API responses) are set on every response.
* CORS is origin-controlled via `NOVA_CORS_ORIGINS`: the default `*` opens CORS
  **without** credentials (the frontend authenticates with an `Authorization`
  bearer header, and the session cookie is `SameSite=Lax`), while an explicit
  origin list restricts callers and switches on credentialed requests.
* Keep the backend bound to `127.0.0.1` for local use, or put it behind the nginx proxy in the
  Docker stack — do not expose port 8000 to the internet.

---

## Try these commands

```
open notepad
close notepad
create a file called hello.txt in my workspace with "hi from NOVA"
what's in my workspace
search the web for LangGraph documentation
open github.com
show my system information
run powershell: Get-Process | Select-Object -First 5
remember that my favorite editor is VS Code
what do you remember?
delete hello.txt in my workspace        ← triggers the confirmation dialog
```

Then open **History** and search *"which editor do I prefer"* — that is the Qdrant recall.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Sidebar shows **Connecting** / **Offline** | Start the backend: `bun run backend` — check `http://127.0.0.1:8000/api/health` |
| Chat replies *Backend offline* | Same as above; confirm `VITE_API_URL` in `.env` matches the backend port |
| Login screen shows 401 everywhere | The token expired — sign in again. `POST /api/auth/logout` also revokes it server-side |
| Locked out of a test instance | Delete `backend/data/nova.db` (and `backend/data/qdrant`) and restart |
| Preview panel blocks API calls | Open the app directly in a browser tab: `http://localhost:8443` (loopback origins are exempt from mixed-content rules) |
| `llm_mode: fallback` | Set `HF_TOKEN` in `backend/.env` and restart the backend |
| Vector engine says `disabled` | Start Qdrant (`QDRANT_URL`) or allow embedded mode (`QDRANT_LOCAL_PATH` writable). Memory still works via SQL |
| Semantic search returns nothing | `Settings → Reindex vectors` — SQLite is the source of truth and rebuilds the collection |
| Mic does nothing | Web Speech API needs **Chrome/Edge**; grant microphone permission |
| Apps / PowerShell panels say *Windows only* | Expected in the Linux container — those tools are gated by `settings.is_windows` |
| Playwright browser missing | `backend\.venv\Scripts\python.exe -m playwright install chromium` (otherwise NOVA falls back to the default browser) |
| `docker compose build` fails with `Unavailable … EOF` | Docker Desktop's buildkit dropped the connection (usually while exporting attestation manifests). Restart Docker Desktop, `docker builder prune -f`, then build again — `docker-compose.yml` already sets `provenance: "false"` for this |
| `docker compose` can't find a file | You are not in the repository root, or the daemon is not running — start Docker Desktop |
| Port already in use | `.\start-windows.ps1 -BackendPort 8100` (and set `VITE_API_URL` accordingly) |
| Script blocked | `Set-ExecutionPolicy -Scope Process Bypass -Force` |

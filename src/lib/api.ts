// ─────────────────────────────────────────────────────
// NOVA — typed API client (REST + SSE over fetch)
//
// Every request carries the session token (`Authorization: Bearer …`);
// `auth: {token}` also reads it from localStorage so the SSE streams —
// which cannot set headers through EventSource — authenticate the same way.
// ─────────────────────────────────────────────────────

import type {
  AppsResponse,
  AuthResponse,
  BackendEvent,
  ChatResponse,
  DecisionResponse,
  FileListResponse,
  FilesResponse,
  Health,
  HistoryResponse,
  MemoryItem,
  MemorySearchResponse,
  StreamEvent,
  SystemResponse,
  User,
} from "./types"

const RAW_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  "http://127.0.0.1:8000"

/**
 * `VITE_API_URL=/` (or an empty string) means "same origin" — that is what
 * the Docker/nginx build uses, so `/api/...` goes straight to the reverse
 * proxy in front of FastAPI. Anything else is an absolute backend origin.
 */
export const API_BASE = RAW_BASE === "/" ? "" : RAW_BASE.replace(/\/+$/, "")

const TOKEN_KEY = "nova.token"

let authToken: string | null = null

export function setToken(token: string | null): void {
  authToken = token
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* private mode / SSR — in-memory token still works */
  }
}

export function getToken(): string | null {
  if (authToken) return authToken
  try {
    authToken = localStorage.getItem(TOKEN_KEY)
  } catch {
    authToken = null
  }
  return authToken
}

/** Fired once whenever the backend answers 401 — the shell drops to login. */
type UnauthorizedListener = () => void
const unauthorizedListeners = new Set<UnauthorizedListener>()

export function onUnauthorized(fn: UnauthorizedListener): () => void {
  unauthorizedListeners.add(fn)
  return () => unauthorizedListeners.delete(fn)
}

function notifyUnauthorized(): void {
  setToken(null)
  unauthorizedListeners.forEach((fn) => fn())
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = 15000,
): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...authHeaders(),
        ...(init?.headers ?? {}),
      },
      signal: controller.signal,
    })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const body = (await res.json()) as { detail?: string; error?: string }
        detail = body.detail || body.error || detail
      } catch {
        /* non-JSON error body */
      }
      if (res.status === 401) notifyUnauthorized()
      throw new Error(`${res.status}: ${detail}`)
    }
    return (await res.json()) as T
  } finally {
    clearTimeout(timer)
  }
}

/** Reads an SSE response and dispatches each `data:` frame. */
async function readStream(
  res: Response,
  onFrame: (payload: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!res.ok || !res.body)
    throw new Error(`event stream failed (${res.status})`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  const dispatchFrame = (frame: string) => {
    for (const line of frame.split("\n")) {
      if (!line.startsWith("data:")) continue
      const payload = line.slice(5).trim()
      if (payload) onFrame(payload)
    }
  }

  for (;;) {
    if (signal?.aborted) {
      await reader.cancel().catch(() => undefined)
      break
    }
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx = buffer.indexOf("\n\n")
    while (idx !== -1) {
      dispatchFrame(buffer.slice(0, idx))
      buffer = buffer.slice(idx + 2)
      idx = buffer.indexOf("\n\n")
    }
  }
  if (buffer.trim()) dispatchFrame(buffer)
}

export const api = {
  base: API_BASE,

  health: () => request<Health>("/api/health", undefined, 6000),

  // ── auth ───────────────────────────────────────────
  login: (username: string, password: string) =>
    request<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  register: (username: string, password: string) =>
    request<AuthResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  logout: async (): Promise<void> => {
    try {
      await request<unknown>("/api/auth/logout", { method: "POST" }, 6000)
    } catch {
      /* a dead backend is already "logged out" */
    }
    setToken(null)
  },

  me: () => request<{ user: User }>("/api/auth/me", undefined, 6000),

  // ── chat / tasks ───────────────────────────────────
  chat: (message: string, sessionId?: string) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    }),

  decide: (taskId: string, approved: boolean) =>
    request<DecisionResponse>(`/api/tasks/${taskId}/decision`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),

  history: (limit = 20) =>
    request<HistoryResponse>(`/api/history?limit=${limit}`),

  task: (taskId: string) =>
    request<{
      id: string
      input: string
      status: string
      phase: string
      progress: number
      summary: string
      steps: Array<{
        id: number
        title: string
        detail: string
        status: string
        destructive?: boolean
      }>
      created_at?: string
      duration_ms?: number
    }>(`/api/tasks/${taskId}`),

  system: () => request<SystemResponse>("/api/system", undefined, 8000),

  apps: () => request<AppsResponse>("/api/apps"),

  recentFiles: () => request<FilesResponse>("/api/files/recent?limit=5"),

  listFiles: (path = "") =>
    request<FileListResponse>(
      `/api/files/list?path=${encodeURIComponent(path)}`,
    ),

  // ── memory (Qdrant backed) ─────────────────────────
  searchMemory: (query: string, limit = 10) =>
    request<MemorySearchResponse>("/api/memory/search", {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    }),

  listMemory: () => request<{ items: MemoryItem[] }>("/api/memory"),

  createMemory: (key: string, value: string) =>
    request<{ ok: boolean; item: MemoryItem }>("/api/memory", {
      method: "POST",
      body: JSON.stringify({ key, value }),
    }),

  deleteMemory: (id: number) =>
    request<{ ok: boolean }>(`/api/memory/${id}`, { method: "DELETE" }),

  reindexMemory: () =>
    request<{ ok: boolean; indexed: number }>("/api/memory/reindex", {
      method: "POST",
    }),

  /**
   * Subscribes to a task's SSE stream. Frames are `data: <json>\n\n` only.
   * Resolves when the server closes the stream after the terminal `done` event.
   */
  async streamTask(
    taskId: string,
    onEvent: (event: BackendEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const res = await fetch(`${API_BASE}/api/tasks/${taskId}/events?from=0`, {
      headers: { Accept: "text/event-stream", ...authHeaders() },
      signal,
    })
    await readStream(
      res,
      (payload) => {
        try {
          onEvent(JSON.parse(payload) as BackendEvent)
        } catch {
          /* ignore malformed frames */
        }
      },
      signal,
    )
  },

  /**
   * The single global realtime feed: system stats, health, vector status and
   * task lifecycle events. Resolves when the connection ends.
   */
  async streamGlobal(
    onEvent: (event: StreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const res = await fetch(`${API_BASE}/api/stream`, {
      headers: { Accept: "text/event-stream", ...authHeaders() },
      signal,
    })
    await readStream(
      res,
      (payload) => {
        try {
          onEvent(JSON.parse(payload) as StreamEvent)
        } catch {
          /* ignore malformed frames */
        }
      },
      signal,
    )
  },
}

/** 09:41 — matches the Figma timestamp style. */
export function nowTime(): string {
  return new Date().toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}

/** "3m ago" / "2h ago" / "Yesterday" / "2 days ago" — matches the Figma history style. */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const diff = Date.now() - then
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return "Just now"
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return "Yesterday"
  return `${days} days ago`
}

/** Human readable byte size for the Files view. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—"
  if (bytes < 1024) return `${bytes} B`
  const units = ["KB", "MB", "GB", "TB"]
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`
}

// ─────────────────────────────────────────────────────
// NOVA — application store
//
// The app boots *neutral*: no seeded conversation, no invented stats, no
// fabricated history. It connects to the backend, waits for the first
// command, and only then starts working.
//
// Everything that changes on its own (system telemetry, health, vector DB
// status, task lifecycle) arrives over the single realtime feed
// `GET /api/stream`; polling only ever runs as a reconnect fallback.
// ─────────────────────────────────────────────────────

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react"
import { api, nowTime, relativeTime } from "../lib/api"
import type {
  BackendEvent,
  ChatMessage,
  ConfirmRequest,
  Health,
  HistoryItem,
  LogEntry,
  LogStatus,
  Phase,
  PipelineStep,
  PlanStep,
  RecentApp,
  RecentFile,
  StreamEvent,
  SystemStats,
  TaskStatus,
} from "../lib/types"

// ─────────────────────────────────────────────────────
// Static copy
// ─────────────────────────────────────────────────────

export const IDLE_PIPELINE: PipelineStep[] = [
  {
    id: "understand",
    label: "Understand",
    status: "pending",
    detail: "Awaiting input",
  },
  { id: "plan", label: "Plan", status: "pending", detail: "Not planned yet" },
  {
    id: "execute",
    label: "Execute",
    status: "pending",
    detail: "No actions queued",
  },
  {
    id: "verify",
    label: "Verify",
    status: "pending",
    detail: "Awaiting results",
  },
]

export const SUGGESTIONS = [
  "What can you do?",
  "Analyze my project repository",
  "Remember: my preferred editor is VS Code",
  "Run a system health check",
]

const PHASE_ORDER: Phase[] = ["understand", "plan", "execute", "verify"]

// ─────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────

export interface NovaState {
  messages: ChatMessage[]
  streamId: string | null
  pipeline: PipelineStep[]
  logs: LogEntry[]
  planSteps: PlanStep[]
  stepLogIndex: Record<number, number>
  progress: number
  activeTaskId: string | null
  taskStatus: TaskStatus
  confirm: ConfirmRequest | null
  history: HistoryItem[]
  apps: RecentApp[]
  files: RecentFile[]
  system: SystemStats | null
  health: Health | null
  /** Backend reachable — only known after the first health probe. */
  online: boolean
  /** True until the first health response arrives ("Connecting…"). */
  connecting: boolean
  running: number
  alerts: number
  modelTag: string
  contextTokens: number
}

/** Neutral boot state: nothing has happened yet. */
export const initialState: NovaState = {
  messages: [],
  streamId: null,
  pipeline: IDLE_PIPELINE.map((s) => ({ ...s })),
  logs: [],
  planSteps: [],
  stepLogIndex: {},
  progress: 0,
  activeTaskId: null,
  taskStatus: "idle",
  confirm: null,
  history: [],
  apps: [],
  files: [],
  system: null,
  health: null,
  online: false,
  connecting: true,
  running: 0,
  alerts: 0,
  modelTag: "—",
  contextTokens: 0,
}

type Action =
  | { type: "send"; content: string }
  | { type: "event"; event: BackendEvent }
  | { type: "offline"; message: string }
  | { type: "confirm/answer"; approved: boolean }
  | { type: "history/set"; items: HistoryItem[] }
  | { type: "apps/set"; items: RecentApp[] }
  | { type: "files/set"; items: RecentFile[] }
  | { type: "system/set"; stats: SystemStats | null }
  | { type: "health/set"; health: Health | null; online: boolean }
  | { type: "stream/system"; stats: SystemStats }
  | { type: "stream/task"; input: string; status: string; phase: string }
  | { type: "notifications/clear" }

// ── helpers ──────────────────────────────────────────

let uid = 0
const nextId = (prefix: string) =>
  `${prefix}-${Date.now().toString(36)}-${uid++}`

function patchStream(state: NovaState, patch: Partial<ChatMessage>): NovaState {
  if (!state.streamId) return state
  return {
    ...state,
    messages: state.messages.map((m) =>
      m.id === state.streamId ? { ...m, ...patch } : m,
    ),
  }
}

function setLogStatus(
  logs: LogEntry[],
  index: number,
  status: LogStatus,
  text?: string,
): LogEntry[] {
  if (index < 0 || index >= logs.length) return logs
  return logs.map((log, i) =>
    i === index ? { ...log, status, text: text ?? log.text } : log,
  )
}

/**
 * Plan steps are rendered as log rows right after the "Plan generated" header.
 * Their exact positions are recorded when the plan event arrives, so later
 * log entries can never shift them.
 */
function logIndexForStep(state: NovaState, stepId: number): number {
  return state.stepLogIndex[stepId] ?? -1
}

function totals(state: NovaState): { contextTokens: number } {
  return {
    contextTokens: state.messages.reduce((sum, m) => sum + (m.tokens ?? 0), 0),
  }
}

// ── event reducer ────────────────────────────────────

function applyEvent(state: NovaState, ev: BackendEvent): NovaState {
  switch (ev.type) {
    case "started": {
      return patchStream(
        {
          ...state,
          activeTaskId: ev.task_id,
          taskStatus: "running",
          progress: 0,
          pipeline: IDLE_PIPELINE.map((s) => ({ ...s })),
          logs: [{ text: `Task accepted · ${ev.input}`, status: "active" }],
          planSteps: [],
          stepLogIndex: {},
          confirm: null,
          running: state.running + 1,
        },
        { content: "Initializing task pipeline…" },
      )
    }

    case "phase": {
      const idx = PHASE_ORDER.indexOf(ev.phase)
      const pipeline = state.pipeline.map((step, i) => {
        if (i < idx) return { ...step, status: "done" as const }
        if (i > idx) return { ...step, status: "pending" as const }
        return {
          ...step,
          status:
            ev.status === "done" ? ("done" as const) : ("active" as const),
          detail: ev.detail || step.detail,
        }
      })
      const progress = Number.isFinite(ev.progress)
        ? Math.max(state.progress, ev.progress)
        : state.progress

      const text =
        ev.phase === "understand"
          ? ev.status === "done"
            ? "Intent parsed — designing the execution plan…"
            : "Parsing intent and loading context…"
          : ev.phase === "plan"
            ? ev.status === "done"
              ? ev.detail
              : "Planning the optimal sequence of actions…"
            : ev.phase === "execute"
              ? ev.status === "done"
                ? "Execution complete — verifying results…"
                : "Executing the plan…"
              : ev.status === "done"
                ? "Verification complete."
                : "Verifying results…"

      return patchStream({ ...state, pipeline, progress }, { content: text })
    }

    case "intent": {
      return {
        ...patchStream(state, {
          content: `**Understood.** ${ev.summary}\n\nCategory: **${ev.category}** · Confidence **${Math.round(ev.confidence * 100)}%**`,
        }),
        logs: [
          ...state.logs,
          { text: "Intent parsed · Context loaded", status: "done" },
        ],
      }
    }

    case "plan": {
      const header: LogEntry = {
        text: `Plan generated · ${ev.steps.length} actions queued`,
        status: "done",
      }
      const rows: LogEntry[] = ev.steps.map((step) => ({
        text: step.title,
        status: "pending",
      }))
      const base = state.logs.length
      const stepLogIndex: Record<number, number> = {}
      ev.steps.forEach((step, i) => {
        stepLogIndex[step.id] = base + 1 + i
      })
      return patchStream(
        {
          ...state,
          planSteps: ev.steps,
          stepLogIndex,
          logs: [...state.logs, header, ...rows],
          progress: Math.max(state.progress, 30),
          pipeline: state.pipeline.map((s) =>
            s.id === "plan"
              ? { ...s, detail: `${ev.steps.length} actions queued` }
              : s,
          ),
        },
        {
          content: `Plan ready — **${ev.steps.length} actions queued.** Executing now.`,
        },
      )
    }

    case "log": {
      const logs = [...state.logs, { text: ev.text, status: ev.status }]
      return { ...state, logs: logs.slice(-60) }
    }

    case "step_start": {
      const logs = setLogStatus(
        state.logs,
        logIndexForStep(state, ev.id),
        "active",
      )
      const pipeline = state.pipeline.map((s) =>
        s.id === "execute"
          ? { ...s, detail: `Running task ${ev.index} of ${ev.total}` }
          : s,
      )
      return patchStream(
        {
          ...state,
          logs,
          pipeline,
          progress: Math.max(state.progress, ev.progress ?? state.progress),
        },
        {
          content: `Running task **${ev.index} of ${ev.total}** — ${ev.title}`,
        },
      )
    }

    case "step_done": {
      const idx = logIndexForStep(state, ev.id)
      const step = state.planSteps.find((p) => p.id === ev.id)
      const title = step?.title ?? ""
      const suffix =
        ev.status === "rejected"
          ? " · declined"
          : ev.status === "skipped"
            ? " · skipped"
            : ""
      const status: LogStatus = ev.status === "done" ? "done" : "failed"
      const logs = setLogStatus(
        state.logs,
        idx,
        status,
        title ? `${title}${suffix}` : undefined,
      )
      return { ...state, logs }
    }

    case "confirmation": {
      return patchStream(
        {
          ...state,
          taskStatus: "awaiting_confirmation",
          confirm: {
            taskId: ev.task_id,
            stepId: ev.step.id,
            title: ev.step.title,
            detail: ev.step.detail,
            risk: ev.step.risk,
            question: ev.question,
          },
          logs: [
            ...state.logs,
            {
              text: `Awaiting confirmation · ${ev.step.risk}`,
              status: "active",
            },
          ],
        },
        { content: `**Confirmation required.** ${ev.question}` },
      )
    }

    case "confirmation_resolved": {
      const logs = state.logs.map((log, i) => {
        if (i !== state.logs.length - 1 || log.status !== "active") return log
        return {
          ...log,
          status: ev.approved ? ("done" as const) : ("failed" as const),
          text: `${log.text}${ev.approved ? " · approved" : " · declined"}`,
        }
      })
      return patchStream(
        { ...state, confirm: null, taskStatus: "running", logs },
        {
          content: ev.approved
            ? "Approved — continuing execution…"
            : "Declined — skipping that action…",
        },
      )
    }

    case "progress": {
      return { ...state, progress: Math.max(state.progress, ev.value) }
    }

    case "message": {
      const next = patchStream(state, {
        content: ev.content,
        tokens: ev.tokens,
        active: false,
        time: nowTime(),
      })
      return { ...next, ...totals(next) }
    }

    case "done": {
      const completed = ev.status === "completed"
      const verifyDetail = completed
        ? "All checks passed"
        : ev.status === "rejected"
          ? "Stopped by user"
          : "Completed with errors"
      const pipeline = state.pipeline.map((s) =>
        s.id === "verify"
          ? { ...s, status: "done" as const, detail: verifyDetail }
          : { ...s, status: "done" as const },
      )
      const stillStreaming =
        Boolean(state.streamId) &&
        state.messages.some((m) => m.id === state.streamId && m.active)
      const withSummary = stillStreaming
        ? patchStream(state, { content: ev.summary, active: false })
        : state
      const logs = state.logs.map((log) =>
        log.status === "active" ? { ...log, status: "done" as const } : log,
      )
      return {
        ...withSummary,
        logs,
        pipeline,
        progress: 100,
        taskStatus: ev.status,
        running: Math.max(0, state.running - 1),
        alerts: completed ? state.alerts : state.alerts + 1,
        confirm: null,
        ...totals(withSummary),
      }
    }

    case "error": {
      const next = patchStream(
        {
          ...state,
          taskStatus: "failed",
          running: Math.max(0, state.running - 1),
          alerts: state.alerts + 1,
          confirm: null,
          logs: [
            ...state.logs,
            { text: `Error · ${ev.message}`, status: "failed" },
          ],
        },
        { content: `**Error.** ${ev.message}`, active: false, error: true },
      )
      return { ...next, ...totals(next) }
    }

    default:
      return state
  }
}

export function reducer(state: NovaState, action: Action): NovaState {
  switch (action.type) {
    case "send": {
      const id = nextId("msg")
      return {
        ...state,
        taskStatus: "queued",
        confirm: null,
        streamId: id,
        progress: 0,
        // Any bubble still marked "streaming" from an earlier command is
        // frozen — only the new reply may animate.
        messages: [
          ...state.messages.map((m) =>
            m.active ? { ...m, active: false } : m,
          ),
          {
            id: nextId("user"),
            role: "user",
            content: action.content,
            time: nowTime(),
          },
          {
            id,
            role: "nova",
            content: "Connecting to NOVA backend…",
            time: nowTime(),
            active: true,
          },
        ],
      }
    }

    case "event":
      return applyEvent(state, action.event)

    case "offline": {
      const next = patchStream(
        {
          ...state,
          online: false,
          connecting: false,
          taskStatus: "failed",
          alerts: state.alerts + 1,
          confirm: null,
          logs: [
            ...state.logs,
            {
              text: "Backend unreachable · start the Python service",
              status: "failed",
            },
          ],
        },
        {
          content: `**Backend offline.** I couldn't reach the NOVA service at \`${api.base}\`.\n\nStart it with \`start-windows.ps1\` (or \`uv run uvicorn nova_backend.main:app --port 8000\` inside \`backend/\`) and try again.\n\nDetail: ${action.message}`,
          active: false,
          error: true,
        },
      )
      return { ...next, ...totals(next) }
    }

    case "confirm/answer":
      return { ...state, confirm: null }

    case "history/set":
      return { ...state, history: action.items }

    case "apps/set":
      return { ...state, apps: action.items }

    case "files/set":
      return { ...state, files: action.items }

    case "system/set":
      return { ...state, system: action.stats }

    case "stream/system":
      return { ...state, system: action.stats, online: true, connecting: false }

    case "stream/task": {
      // Another tab / another user's live task shows up in the sidebar feed.
      if (action.status === "running" || action.status === "queued") {
        return {
          ...state,
          running: Math.max(state.running, 1),
          logs:
            state.streamId && state.taskStatus !== "idle"
              ? state.logs
              : [
                  ...state.logs,
                  {
                    text: `Task running · ${action.input}`,
                    status: "active" as const,
                  },
                ].slice(-60),
        }
      }
      return {
        ...state,
        running: Math.max(0, state.running - 1),
        logs: state.logs.map((log) =>
          log.status === "active"
            ? {
                ...log,
                status: action.status === "completed" ? "done" : "failed",
              }
            : log,
        ),
      }
    }

    case "notifications/clear":
      return { ...state, alerts: 0 }

    case "health/set": {
      const model =
        action.health?.model?.split("/").pop() ??
        (action.online ? state.modelTag : state.modelTag)
      return {
        ...state,
        health: action.health,
        online: action.online,
        connecting: false,
        modelTag: model && model !== "—" ? model : state.modelTag,
      }
    }

    default:
      return state
  }
}

// ─────────────────────────────────────────────────────
// Context
// ─────────────────────────────────────────────────────

export interface NovaApi {
  state: NovaState
  /** Dispatches a command to the backend. Returns false when it was ignored. */
  sendMessage: (text: string) => boolean
  answerConfirm: (approved: boolean) => void
  refreshSystem: () => void
  refreshHealth: () => void
  refreshHistory: () => void
  refreshData: () => void
  clearAlerts: () => void
}

const NovaContext = createContext<NovaApi | null>(null)

function toStats(sys: {
  cpu: number
  ram: number
  ssd: number
  network_mbps: number
  network_display: string
  gpu_temp: number | null
}): SystemStats {
  return {
    cpu: sys.cpu,
    ram: sys.ram,
    ssd: sys.ssd,
    network: Math.min(100, Math.round(sys.network_mbps * 2)),
    networkDisplay: sys.network_display,
    gpuTemp: sys.gpu_temp,
  }
}

export function NovaProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const sessionRef = useRef(`nova-${Math.random().toString(36).slice(2, 10)}`)
  const busyRef = useRef(false)
  const bootedRef = useRef(false)

  const refreshHealth = useCallback(async () => {
    try {
      const health = await api.health()
      dispatch({ type: "health/set", health, online: true })
    } catch {
      dispatch({ type: "health/set", health: null, online: false })
    }
  }, [])

  const refreshSystem = useCallback(async () => {
    try {
      const sys = await api.system()
      dispatch({ type: "system/set", stats: toStats(sys) })
    } catch {
      /* realtime feed owns telemetry once connected */
    }
  }, [])

  const refreshHistory = useCallback(async () => {
    try {
      const res = await api.history(50)
      dispatch({
        type: "history/set",
        items: res.tasks.slice(0, 50).map((t) => ({
          id: t.id,
          title: t.input,
          time: relativeTime(t.created_at),
          steps: t.steps,
          status: t.status,
        })),
      })
    } catch {
      /* keep whatever the realtime feed last delivered */
    }
  }, [])

  const refreshApps = useCallback(async () => {
    try {
      const res = await api.apps()
      dispatch({ type: "apps/set", items: res.apps.slice(0, 8) })
    } catch {
      /* apps stay empty when the backend is down */
    }
  }, [])

  const refreshFiles = useCallback(async () => {
    try {
      const res = await api.recentFiles()
      dispatch({ type: "files/set", items: res.files.slice(0, 5) })
    } catch {
      /* files stay empty when the backend is down */
    }
  }, [])

  const refreshData = useCallback(() => {
    void refreshHistory()
    void refreshApps()
    void refreshFiles()
  }, [refreshHistory, refreshApps, refreshFiles])

  // ── realtime feed: one connection for every live panel ──
  useEffect(() => {
    let cancelled = false
    let controller: AbortController | null = null
    let retry = 0
    let fallbackTimer = 0

    const connect = async () => {
      if (cancelled) return
      controller = new AbortController()
      try {
        await api.streamGlobal((ev) => {
          retry = 0
          if (ev.type === "system") {
            dispatch({
              type: "stream/system",
              stats: toStats(ev as unknown as Parameters<typeof toStats>[0]),
            })
          } else if (ev.type === "health") {
            dispatch({
              type: "health/set",
              health: ev as unknown as Health,
              online: true,
            })
          } else if (ev.type === "task") {
            dispatch({
              type: "stream/task",
              input: String(ev.input ?? ""),
              status: String(ev.status ?? ""),
              phase: String(ev.phase ?? ""),
            })
          }
        }, controller.signal)
      } catch {
        /* handled below */
      }
      if (cancelled) return
      retry = Math.min(retry + 1, 6)
      // while the stream is down, poll so the UI never freezes
      fallbackTimer = window.setTimeout(() => void connect(), retry * 2000)
      void refreshHealth()
      void refreshSystem()
    }

    void connect()
    return () => {
      cancelled = true
      controller?.abort()
      window.clearTimeout(fallbackTimer)
    }
  }, [refreshHealth, refreshSystem])

  // ── boot: health probe + first page of real data ──
  useEffect(() => {
    if (bootedRef.current) return
    bootedRef.current = true
    void refreshHealth()
    refreshData()
    // health only (telemetry arrives via the realtime stream)
    const healthTimer = window.setInterval(() => void refreshHealth(), 20000)
    return () => window.clearInterval(healthTimer)
  }, [refreshData, refreshHealth])

  const sendMessage = useCallback(
    (text: string): boolean => {
      const content = text.trim()
      if (!content) return false
      if (busyRef.current) return false
      if (
        state.taskStatus === "queued" ||
        state.taskStatus === "running" ||
        state.taskStatus === "awaiting_confirmation"
      ) {
        return false
      }
      busyRef.current = true
      dispatch({ type: "send", content })

      void (async () => {
        const controller = new AbortController()
        const watchdog = window.setTimeout(
          () => controller.abort(),
          10 * 60 * 1000, // a healthy task always finishes (or confirms) well before this
        )
        try {
          const { task_id } = await api.chat(content, sessionRef.current)
          await api.streamTask(
            task_id,
            (ev) => dispatch({ type: "event", event: ev }),
            controller.signal,
          )
          refreshData()
        } catch (err) {
          if (controller.signal.aborted) {
            dispatch({
              type: "event",
              event: {
                type: "error",
                seq: 0,
                message: "The task stream timed out before NOVA finished.",
              },
            })
          } else {
            dispatch({
              type: "offline",
              message: err instanceof Error ? err.message : String(err),
            })
          }
        } finally {
          window.clearTimeout(watchdog)
          busyRef.current = false
        }
      })()
      return true
    },
    [state.taskStatus, refreshData],
  )

  const answerConfirm = useCallback(
    (approved: boolean) => {
      const req = state.confirm
      dispatch({ type: "confirm/answer", approved })
      if (!req) return
      void api.decide(req.taskId, approved).catch(() => {
        /* the stream timeout resolves the step as rejected */
      })
    },
    [state.confirm],
  )

  const clearAlerts = useCallback(
    () => dispatch({ type: "notifications/clear" }),
    [],
  )

  const value = useMemo<NovaApi>(
    () => ({
      state,
      sendMessage,
      answerConfirm,
      refreshSystem,
      refreshHealth,
      refreshHistory,
      refreshData,
      clearAlerts,
    }),
    [
      state,
      sendMessage,
      answerConfirm,
      refreshSystem,
      refreshHealth,
      refreshHistory,
      refreshData,
      clearAlerts,
    ],
  )

  return <NovaContext.Provider value={value}>{children}</NovaContext.Provider>
}

export function useNova(): NovaApi {
  const ctx = useContext(NovaContext)
  if (!ctx) throw new Error("useNova must be used inside <NovaProvider>")
  return ctx
}

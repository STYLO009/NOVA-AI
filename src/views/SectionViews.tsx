// NOVA — section views reachable from the left sidebar.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react"
import { api, formatBytes, relativeTime } from "../lib/api"
import type { FileEntry, Health, MemoryItem, NavSection } from "../lib/types"
import { useNova } from "../store/NovaStore"
import { useAuth } from "../store/AuthStore"
import { Icon } from "../components/icons"
import { Glass, SectionLabel, renderBold } from "../components/primitives"

// ── shared bits ──────────────────────────────────────

const COL = {
  accent: "#6488fa",
  success: "#3fbf7a",
  warn: "#dfa23f",
  danger: "#e5545c",
  idle: "#71767f",
}

const STATUS_COLOR: Record<string, string> = {
  completed: COL.success,
  running: COL.accent,
  queued: COL.idle,
  awaiting_confirmation: COL.warn,
  failed: COL.danger,
  rejected: COL.danger,
}

/** `awaiting_confirmation` → `Awaiting confirmation` */
function humanize(status: string) {
  const text = status.replace(/_/g, " ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function EmptyState({
  title,
  hint,
  action,
  onAction,
}: {
  title: string
  hint: string
  action?: string
  onAction?: () => void
}) {
  return (
    <div className="empty">
      <div className="empty__icon">{Icon.dashboard("currentColor")}</div>
      <div className="empty__title">{title}</div>
      <div className="empty__hint">{hint}</div>
      {action && onAction && (
        <button className="btn" onClick={onAction} style={{ marginTop: "6px" }}>
          {action}
        </button>
      )}
    </div>
  )
}

function ViewShell({
  label,
  action,
  onAction,
  children,
}: {
  label: string
  action?: string
  onAction?: () => void
  children: React.ReactNode
}) {
  return (
    <Glass
      style={{
        padding: "20px 22px",
        overflowY: "auto",
        height: "100%",
      }}
    >
      <SectionLabel action={action} onAction={onAction}>
        {label}
      </SectionLabel>
      {children}
    </Glass>
  )
}

// ── Tasks ────────────────────────────────────────────

export function TasksView({
  onNavigate,
}: {
  onNavigate: (section: NavSection) => void
}) {
  const { state, refreshHistory, sendMessage } = useNova()
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<{
    status: string
    summary: string
    steps: Array<{ id: number; title: string; status: string; detail: string }>
  } | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  useEffect(() => {
    void refreshHistory()
  }, [refreshHistory])

  useEffect(() => {
    if (!openId) {
      setDetail(null)
      return
    }
    let cancelled = false
    setLoadingDetail(true)
    void (async () => {
      try {
        const body = await api.task(openId)
        if (!cancelled) setDetail(body)
      } catch {
        if (!cancelled) setDetail(null)
      } finally {
        if (!cancelled) setLoadingDetail(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [openId])

  const active = state.taskStatus === "running" || state.taskStatus === "queued"

  return (
    <ViewShell
      label="Tasks"
      action="New command"
      onAction={() => onNavigate("dashboard")}
    >
      {active && (
        <div
          style={{
            marginBottom: "14px",
            padding: "12px 14px",
            borderRadius: "10px",
            background: "var(--accent-soft)",
            display: "flex",
            alignItems: "center",
            gap: "12px",
          }}
        >
          <span
            className="status-dot status-dot--live"
            style={{ background: "var(--accent)" }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: "13px",
                fontWeight: 500,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {state.history[0]?.title ?? "Working…"}
            </div>
            <div
              style={{
                fontSize: "12px",
                color: "var(--text-faint)",
                marginTop: "2px",
              }}
            >
              {state.pipeline.find((s) => s.status === "active")?.detail ??
                "In progress"}
              {" · "}
              {state.progress}%
            </div>
          </div>
        </div>
      )}

      {state.history.length === 0 ? (
        <EmptyState
          title="No tasks yet"
          hint="Ask NOVA for something — open an app, search the web, remember a fact. Every run lands here with its plan, steps and result."
          action="Open the chat"
          onAction={() => onNavigate("dashboard")}
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {state.history.map((task) => {
            const isOpen = openId === task.id
            const color = STATUS_COLOR[task.status ?? ""] ?? COL.idle
            return (
              <div
                key={task.id}
                style={{
                  borderRadius: "12px",
                  border: `1px solid ${isOpen ? "var(--accent-line)" : "var(--line)"}`,
                  background: isOpen
                    ? "var(--accent-soft)"
                    : "var(--surface-2)",
                  overflow: "hidden",
                }}
              >
                <button
                  className="btn btn--ghost"
                  onClick={() => setOpenId(isOpen ? null : task.id)}
                  style={{
                    width: "100%",
                    padding: "13px 15px",
                    borderRadius: "11px",
                    gap: "12px",
                    textAlign: "left",
                  }}
                >
                  <span className="status-dot" style={{ background: color }} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span
                      style={{
                        display: "block",
                        fontSize: "13.5px",
                        fontWeight: 500,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {task.title}
                    </span>
                    <span
                      className="mono"
                      style={{
                        display: "block",
                        fontSize: "11px",
                        color: "var(--text-faint)",
                        marginTop: "3px",
                      }}
                    >
                      {task.steps} steps · {task.time} ·{" "}
                      {task.status ?? "queued"}
                    </span>
                  </span>
                  <span
                    style={{
                      fontSize: "11.5px",
                      fontWeight: 600,
                      color,
                    }}
                  >
                    {humanize(task.status ?? "")}
                  </span>
                </button>

                {isOpen && (
                  <div
                    style={{
                      padding: "12px 15px 15px",
                      borderTop: "1px solid var(--line)",
                    }}
                  >
                    {loadingDetail && (
                      <div
                        style={{
                          fontSize: "12.5px",
                          color: "var(--text-faint)",
                        }}
                      >
                        Loading steps…
                      </div>
                    )}
                    {detail && (
                      <>
                        <div
                          style={{
                            fontSize: "13px",
                            color: "var(--text-dim)",
                            lineHeight: "1.6",
                            marginBottom: "10px",
                          }}
                        >
                          {renderBold(detail.summary || "—", COL.accent)}
                        </div>
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: "6px",
                          }}
                        >
                          {detail.steps.map((step) => (
                            <div
                              key={step.id}
                              style={{
                                display: "flex",
                                gap: "10px",
                                alignItems: "flex-start",
                                fontSize: "12.5px",
                                color: "var(--text-dim)",
                                padding: "8px 10px",
                                borderRadius: "8px",
                                background: "rgba(255,255,255,0.025)",
                              }}
                            >
                              <span
                                style={{
                                  color:
                                    step.status === "done"
                                      ? COL.success
                                      : step.status === "rejected" ||
                                          step.status === "failed"
                                        ? COL.danger
                                        : COL.idle,
                                  flexShrink: 0,
                                }}
                              >
                                {step.status === "done"
                                  ? "✓"
                                  : step.status === "rejected" ||
                                      step.status === "failed"
                                    ? "✕"
                                    : "○"}
                              </span>
                              <span style={{ flex: 1 }}>
                                {step.title}
                                {step.detail && (
                                  <span
                                    style={{
                                      display: "block",
                                      color: "var(--text-faint)",
                                      marginTop: "2px",
                                      fontSize: "11.5px",
                                    }}
                                  >
                                    {step.detail}
                                  </span>
                                )}
                              </span>
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                    <div style={{ display: "flex", gap: "8px" }}>
                      <button
                        className="btn"
                        onClick={() => sendMessage(task.title)}
                        style={{
                          marginTop: "10px",
                          padding: "7px 13px",
                          fontSize: "12.5px",
                        }}
                      >
                        Run again
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </ViewShell>
  )
}

// ── Apps ─────────────────────────────────────────────

export function AppsView({
  onNavigate,
}: {
  onNavigate: (section: NavSection) => void
}) {
  const { state, sendMessage } = useNova()
  const [health, setHealth] = useState<Health | null>(state.health)

  useEffect(() => {
    void api
      .health()
      .then(setHealth)
      .catch(() => undefined)
  }, [])

  const supported = health?.features?.apps !== false

  return (
    <ViewShell label="Applications">
      {!supported ? (
        <EmptyState
          title="Windows only"
          hint="App detection and launch control run through Windows APIs. The backend is running on a different platform, so this panel stays empty — file, web, memory and system tools all still work."
          action="Back to chat"
          onAction={() => onNavigate("dashboard")}
        />
      ) : state.apps.length === 0 ? (
        <EmptyState
          title="No applications detected yet"
          hint="NOVA scans the machine for installed applications the first time this view opens."
          action="Back to chat"
          onAction={() => onNavigate("dashboard")}
        />
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
            gap: "10px",
          }}
        >
          {state.apps.map((app) => (
            <button
              key={app.name}
              className="row"
              onClick={() => sendMessage(`open ${app.name}`)}
              title={`Ask NOVA to open ${app.name}`}
              style={{
                fontSize: "13px",
                color: "var(--text-dim)",
                textAlign: "left",
              }}
            >
              <span
                style={{
                  width: "32px",
                  height: "32px",
                  borderRadius: "8px",
                  flexShrink: 0,
                  background: "var(--surface-3)",
                  border: "1px solid var(--line-strong)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "11px",
                  fontWeight: 600,
                  color: app.color,
                  fontFamily: '"JetBrains Mono", monospace',
                }}
              >
                {app.abbr}
              </span>
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {app.name}
              </span>
            </button>
          ))}
        </div>
      )}
    </ViewShell>
  )
}

// ── Files ────────────────────────────────────────────

function fileIcon(entry: FileEntry) {
  return (
    <span
      className="mono"
      style={{
        padding: "3px 7px",
        borderRadius: "5px",
        fontSize: "10px",
        fontWeight: 500,
        background: "var(--surface-3)",
        border: "1px solid var(--line-strong)",
        color: entry.color,
        flexShrink: 0,
        minWidth: "40px",
        textAlign: "center",
      }}
    >
      {entry.is_dir ? "DIR" : `.${entry.ext}`}
    </span>
  )
}

export function FilesView({
  onNavigate,
}: {
  onNavigate: (s: NavSection) => void
}) {
  const { sendMessage } = useNova()
  const [path, setPath] = useState("")
  const [entries, setEntries] = useState<FileEntry[]>([])
  const [crumb, setCrumb] = useState<string>("")
  const [parent, setParent] = useState<string>("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (target: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.listFiles(target)
      setEntries(res.entries)
      setCrumb(res.relative || "workspace")
      setParent(res.parent)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setEntries([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(path)
  }, [load, path])

  const crumbs = useMemo(() => crumb.split("/").filter(Boolean), [crumb])

  return (
    <ViewShell
      label="Workspace files"
      action="Ask NOVA"
      onAction={() => onNavigate("dashboard")}
    >
      {/* breadcrumb */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          flexWrap: "wrap",
          marginBottom: "14px",
          fontSize: "12.5px",
        }}
      >
        <button
          onClick={() => setPath("")}
          className="mono"
          style={{
            color: path ? "var(--text-faint)" : "var(--accent)",
            fontSize: "12.5px",
          }}
        >
          workspace
        </button>
        {crumbs.map((part, i) => (
          <span key={`${part}-${i}`} style={{ display: "flex", gap: "6px" }}>
            <span style={{ color: "var(--text-faint)" }}>/</span>
            <span
              className="mono"
              style={{
                color:
                  i === crumbs.length - 1
                    ? "var(--accent)"
                    : "var(--text-faint)",
              }}
            >
              {part}
            </span>
          </span>
        ))}
        {parent && (
          <button
            className="btn"
            onClick={() => setPath(parent)}
            style={{
              marginLeft: "auto",
              padding: "5px 11px",
              fontSize: "12px",
            }}
          >
            ↑ Up one level
          </button>
        )}
      </div>

      {error && (
        <div className="notice notice--error" style={{ marginBottom: "12px" }}>
          {error}
        </div>
      )}

      {loading ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "7px",
            padding: "6px 0",
          }}
          aria-busy="true"
          aria-label="Loading workspace"
        >
          {[86, 72, 80, 64, 76, 58].map((width, i) => (
            <div key={i} className="row row--static">
              <div
                className="skeleton"
                style={{
                  width: 42,
                  height: 20,
                  borderRadius: 6,
                  flexShrink: 0,
                  animationDelay: `${i * 0.11}s`,
                }}
              />
              <div
                className="skeleton"
                style={{
                  width: `${width}%`,
                  maxWidth: 340,
                  animationDelay: `${i * 0.11}s`,
                }}
              />
              <div
                className="skeleton"
                style={{
                  width: 56,
                  height: 10,
                  marginLeft: "auto",
                  flexShrink: 0,
                  animationDelay: `${i * 0.11}s`,
                }}
              />
            </div>
          ))}
        </div>
      ) : entries.length === 0 && !error ? (
        <EmptyState
          title="Empty directory"
          hint="Nothing here yet. Ask NOVA to create a file — for example “create notes.txt in my workspace with hello”."
          action="Ask NOVA"
          onAction={() => onNavigate("dashboard")}
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
          {entries.map((entry) => (
            <div
              key={entry.path}
              className="row"
              onClick={() =>
                entry.is_dir
                  ? setPath(entry.path)
                  : sendMessage(`read the file "${entry.path}"`)
              }
              title={
                entry.is_dir
                  ? `Open ${entry.name}`
                  : `Ask NOVA to read ${entry.path}`
              }
              style={{ cursor: "pointer" }}
            >
              {fileIcon(entry)}
              <span
                style={{
                  flex: 1,
                  fontSize: "13px",
                  color: entry.is_dir ? "var(--text)" : "var(--text-dim)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {entry.name}
              </span>
              {!entry.is_dir && (
                <span
                  className="mono"
                  style={{ fontSize: "11.5px", color: "var(--text-faint)" }}
                >
                  {formatBytes(entry.size)}
                </span>
              )}
              <span
                className="mono"
                style={{
                  fontSize: "11.5px",
                  color: "var(--text-faint)",
                  minWidth: "58px",
                  textAlign: "right",
                }}
              >
                {entry.time}
              </span>
              {Icon.chevron("var(--text-faint)")}
            </div>
          ))}
        </div>
      )}
    </ViewShell>
  )
}

// ── History ──────────────────────────────────────────

export function HistoryView({
  onNavigate,
}: {
  onNavigate: (section: NavSection) => void
}) {
  const { state, refreshHistory } = useNova()
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<MemoryItem[] | null>(null)
  const [engine, setEngine] = useState<string>("")
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)

  useEffect(() => {
    void refreshHistory()
  }, [refreshHistory])

  const search = async (e: FormEvent) => {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
    setSearching(true)
    setSearchError(null)
    try {
      const res = await api.searchMemory(q, 12)
      setResults(res.items)
      setEngine(res.engine)
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : String(err))
    } finally {
      setSearching(false)
    }
  }

  return (
    <ViewShell label="History & memory">
      {/* semantic recall (Qdrant) */}
      <form
        onSubmit={search}
        style={{
          display: "flex",
          gap: "9px",
          marginBottom: "16px",
          alignItems: "center",
        }}
      >
        <input
          className="input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search what NOVA remembers — semantic recall…"
          style={{ flex: 1 }}
        />
        <button
          type="submit"
          className="btn btn--primary"
          disabled={searching || !query.trim()}
        >
          {searching ? "…" : "Search"}
        </button>
      </form>

      {searchError && (
        <div
          style={{ fontSize: "12.5px", color: "#f0a2a7", marginBottom: "12px" }}
        >
          {searchError}
        </div>
      )}

      {results !== null && (
        <div style={{ marginBottom: "20px" }}>
          <div
            className="mono"
            style={{
              fontSize: "11.5px",
              color: "var(--text-faint)",
              marginBottom: "8px",
            }}
          >
            {results.length} result{results.length === 1 ? "" : "s"} · engine:{" "}
            {engine || "sql"}
          </div>
          {results.length === 0 ? (
            <div className="notice notice--info">
              Nothing matched. Teach NOVA with{" "}
              <span style={{ color: "var(--accent)" }}>
                “remember that … is …”
              </span>
              .
            </div>
          ) : (
            <div
              style={{ display: "flex", flexDirection: "column", gap: "6px" }}
            >
              {results.map((item) => (
                <div
                  key={item.id}
                  className="row row--static"
                  style={{ fontSize: "13px" }}
                >
                  <span style={{ color: "var(--accent)", fontWeight: 600 }}>
                    {item.key}
                  </span>
                  <span style={{ flex: 1, color: "var(--text-dim)" }}>
                    {item.value && `= ${item.value}`}
                  </span>
                  {item.score != null && (
                    <span
                      className="mono"
                      style={{ fontSize: "11px", color: "var(--text-faint)" }}
                    >
                      {item.score.toFixed(2)}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <SectionLabel>Task history</SectionLabel>
      {state.history.length === 0 ? (
        <EmptyState
          title="Nothing in history"
          hint="Completed, failed and declined runs appear here automatically — with steps, timing and status."
          action="Start a task"
          onAction={() => onNavigate("dashboard")}
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {state.history.map((task) => {
            const color = STATUS_COLOR[task.status ?? ""] ?? COL.idle
            return (
              <div key={task.id} className="row row--static">
                <span className="status-dot" style={{ background: color }} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span
                    style={{
                      display: "block",
                      fontSize: "13px",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {task.title}
                  </span>
                  <span
                    className="mono"
                    style={{
                      display: "block",
                      fontSize: "11px",
                      color: "var(--text-faint)",
                      marginTop: "2px",
                    }}
                  >
                    {task.steps} steps · {task.time}
                  </span>
                </span>
                <span style={{ fontSize: "11.5px", fontWeight: 600, color }}>
                  {humanize(task.status ?? "")}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </ViewShell>
  )
}

// ── Settings ─────────────────────────────────────────

function Row({
  label,
  value,
  mono = false,
  color,
}: {
  label: string
  value: string
  mono?: boolean
  color?: string
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: "16px",
        padding: "10px 0",
        borderBottom: "1px solid var(--line)",
        fontSize: "13px",
      }}
    >
      <span style={{ color: "var(--text-faint)" }}>{label}</span>
      <span
        className={mono ? "mono" : undefined}
        style={{
          color: color ?? "var(--text-dim)",
          textAlign: "right",
          overflow: "hidden",
          textOverflow: "ellipsis",
          maxWidth: "60%",
        }}
      >
        {value}
      </span>
    </div>
  )
}

export function SettingsView() {
  const { user, logout, authEnabled } = useAuth()
  const { state, clearAlerts } = useNova()
  const [health, setHealth] = useState<Health | null>(state.health)
  const [memory, setMemory] = useState<MemoryItem[]>([])
  const [reindexing, setReindexing] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [newKey, setNewKey] = useState("")
  const [newValue, setNewValue] = useState("")

  const reload = useCallback(async () => {
    try {
      setHealth(await api.health())
    } catch {
      /* offline */
    }
    try {
      const res = await api.listMemory()
      setMemory(res.items)
    } catch {
      /* memory list needs a live backend */
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const vector = health?.vector

  const addMemory = async (e: FormEvent) => {
    e.preventDefault()
    const key = newKey.trim()
    if (!key) return
    try {
      await api.createMemory(key, newValue.trim())
      setNewKey("")
      setNewValue("")
      setNotice("Saved and indexed.")
      await reload()
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err))
    }
  }

  const reindex = async () => {
    setReindexing(true)
    try {
      const res = await api.reindexMemory()
      setNotice(`Reindexed ${res.indexed} entries into Qdrant.`)
      await reload()
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err))
    } finally {
      setReindexing(false)
    }
  }

  const remove = async (id: number) => {
    try {
      await api.deleteMemory(id)
      setMemory((prev) => prev.filter((m) => m.id !== id))
    } catch {
      /* ignore */
    }
  }

  const feats = health?.features ?? {}

  return (
    <ViewShell label="Settings">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: "20px",
          alignItems: "start",
        }}
      >
        {/* account */}
        <div>
          <SectionLabel>Account</SectionLabel>
          <Row
            label="Signed in as"
            value={user?.username ?? (authEnabled ? "—" : "local (auth off)")}
          />
          <Row
            label="Account created"
            value={user?.created_at ? relativeTime(user.created_at) : "—"}
          />
          <Row
            label="Authentication"
            value={authEnabled ? "Enabled (multi-user)" : "Disabled"}
          />
          <button
            className="btn btn--danger-quiet"
            onClick={() => void logout()}
            style={{ marginTop: "14px" }}
          >
            Sign out
          </button>
        </div>

        {/* backend */}
        <div>
          <SectionLabel>Backend</SectionLabel>
          <Row
            label="Status"
            value={state.online ? "Connected" : "Offline"}
            color={state.online ? COL.success : COL.danger}
          />
          <Row label="Endpoint" value={api.base} mono />
          <Row label="Model" value={state.modelTag} />
          <Row
            label="Mode"
            value={
              health?.llm_mode === "huggingface"
                ? "Hugging Face"
                : health?.llm_mode === "local"
                  ? "Local model"
                  : health?.llm_mode
                    ? "Rule engine (no token)"
                    : "—"
            }
          />
          <Row label="Workspace" value={health?.workspace ?? "—"} mono />
          <Row
            label="Uptime"
            value={
              health?.uptime_s
                ? `${Math.floor(health.uptime_s / 60)}m ${Math.round(health.uptime_s % 60)}s`
                : "—"
            }
            mono
          />
          <Row
            label="Active tasks"
            value={String(health?.tasks_active ?? 0)}
            mono
          />
        </div>

        {/* vector db */}
        <div>
          <SectionLabel action="Reindex" onAction={() => void reindex()}>
            Vector database
          </SectionLabel>
          <Row
            label="Engine"
            value={
              vector?.mode === "server"
                ? "Qdrant server"
                : vector?.mode === "local"
                  ? "Qdrant (embedded)"
                  : "Unavailable"
            }
            color={
              vector?.available
                ? COL.success
                : vector?.mode
                  ? COL.warn
                  : COL.danger
            }
          />
          <Row label="Collection" value={vector?.collection ?? "—"} mono />
          <Row label="Embeddings" value={vector?.embeddings ?? "—"} mono />
          <Row label="Vectors" value={String(vector?.count ?? 0)} mono />
          <Row
            label="Capabilities"
            value={
              Object.entries(feats)
                .filter(([, on]) => on)
                .map(([k]) => k)
                .join(", ") || "—"
            }
          />
          {vector?.error && (
            <div
              style={{
                marginTop: "10px",
                fontSize: "12px",
                color: "var(--amber)",
                lineHeight: "1.5",
              }}
            >
              {vector.error}
            </div>
          )}
          {notice && (
            <div
              style={{
                marginTop: "10px",
                fontSize: "12px",
                color: "var(--accent)",
              }}
            >
              {notice}
            </div>
          )}
          {reindexing && (
            <div
              style={{
                marginTop: "8px",
                fontSize: "12px",
                color: "var(--text-faint)",
              }}
            >
              Reindexing…
            </div>
          )}
        </div>

        {/* notifications */}
        <div>
          <SectionLabel>Notifications</SectionLabel>
          <Row label="Unread alerts" value={String(state.alerts)} mono />
          <button
            className="btn"
            onClick={clearAlerts}
            disabled={state.alerts === 0}
            style={{ marginTop: "14px" }}
          >
            Mark all as read
          </button>
        </div>
      </div>

      {/* memory manager */}
      <div style={{ marginTop: "26px" }}>
        <SectionLabel action="Reindex vectors" onAction={() => void reindex()}>
          Long-term memory · {memory.length} entr
          {memory.length === 1 ? "y" : "ies"}
        </SectionLabel>

        <form
          onSubmit={addMemory}
          style={{ display: "flex", gap: "9px", marginBottom: "12px" }}
        >
          <input
            className="input"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            placeholder="Key (e.g. preferred editor)"
            style={{ flex: "1 1 40%" }}
          />
          <input
            className="input"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            placeholder="Value (e.g. VS Code)"
            style={{ flex: "1 1 40%" }}
          />
          <button
            type="submit"
            className="btn btn--primary"
            disabled={!newKey.trim()}
          >
            Save
          </button>
        </form>

        {memory.length === 0 ? (
          <div className="notice notice--info" style={{ lineHeight: "1.6" }}>
            Memory is empty. Say{" "}
            <span style={{ color: "var(--accent)" }}>
              “remember that my preferred editor is VS Code”
            </span>{" "}
            and it is stored in SQLite plus embedded into Qdrant for semantic
            recall.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {memory.map((item) => (
              <div key={item.id} className="row row--static">
                <span
                  style={{
                    fontSize: "13px",
                    color: "var(--accent)",
                    fontWeight: 600,
                  }}
                >
                  {item.key}
                </span>
                <span
                  style={{
                    flex: 1,
                    fontSize: "13px",
                    color: "var(--text-dim)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {item.value}
                </span>
                <span
                  className="mono"
                  style={{ fontSize: "11.5px", color: "var(--text-faint)" }}
                >
                  {relativeTime(item.created_at)}
                </span>
                <button
                  onClick={() => void remove(item.id)}
                  title="Forget this"
                  style={{
                    color: "var(--text-faint)",
                    fontSize: "16px",
                    lineHeight: 1,
                    padding: "0 2px",
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </ViewShell>
  )
}

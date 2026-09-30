import type { NavSection } from "../lib/types"
import { Icon } from "./icons"
import { useAuth } from "../store/AuthStore"

const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "tasks", label: "Tasks" },
  { id: "apps", label: "Apps" },
  { id: "files", label: "Files" },
  { id: "history", label: "History" },
  { id: "settings", label: "Settings" },
] as const

export function Sidebar({
  active,
  onNav,
  online,
  connecting = false,
  llmMode,
}: {
  active: NavSection
  onNav: (s: NavSection) => void
  online: boolean
  connecting?: boolean
  llmMode?: string
}) {
  const { user, logout, authEnabled } = useAuth()

  const status = connecting
    ? {
        label: "Connecting",
        detail: "Reaching the backend…",
        color: "var(--amber)",
      }
    : online
      ? {
          label: "Connected",
          detail:
            llmMode && llmMode !== "fallback"
              ? llmMode === "huggingface"
                ? "Hugging Face model ready"
                : "Local model ready"
              : "Rule engine · no model token",
          color: "var(--green)",
        }
      : {
          label: "Disconnected",
          detail: "Backend not reachable",
          color: "var(--red)",
        }

  return (
    <aside
      className="sidebar"
      style={{
        width: "220px",
        flexShrink: 0,
        height: "100%",
        borderRight: "1px solid var(--line)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Brand */}
      <div style={{ padding: "20px 16px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "11px" }}>
          <div className="mark" style={{ width: "34px", height: "34px" }}>
            <span style={{ fontSize: "15px" }}>N</span>
          </div>
          <div>
            <div className="wordmark">NOVA</div>
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-faint)",
                marginTop: "1px",
              }}
            >
              Personal agent
            </div>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav
        style={{
          flex: 1,
          padding: "4px 10px",
          display: "flex",
          flexDirection: "column",
          gap: "2px",
          overflowY: "auto",
        }}
      >
        {NAV_ITEMS.map(({ id, label }) => (
          <button
            key={id}
            className={`nav-item${active === id ? " is-active" : ""}`}
            onClick={() => onNav(id)}
          >
            {Icon[id](active === id ? "var(--text)" : "var(--text-faint)")}
            {label}
          </button>
        ))}
      </nav>

      {/* Status + account */}
      <div style={{ padding: "12px 12px 16px", display: "grid", gap: "10px" }}>
        <div
          className="row row--static"
          style={{ alignItems: "flex-start", background: "var(--surface-2)" }}
        >
          <span
            className="status-dot status-dot--live"
            style={{ background: status.color, marginTop: "5px" }}
          />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: "12px", fontWeight: "600" }}>
              {status.label}
            </div>
            <div
              style={{
                fontSize: "11px",
                color: "var(--text-faint)",
                marginTop: "1px",
                lineHeight: 1.45,
              }}
            >
              {status.detail}
            </div>
          </div>
        </div>

        {authEnabled && user && (
          <div
            className="row row--static"
            style={{ background: "var(--surface-2)", gap: "10px" }}
          >
            <div
              title={user.username}
              style={{
                width: "26px",
                height: "26px",
                borderRadius: "7px",
                flexShrink: 0,
                background: "var(--surface-3)",
                border: "1px solid var(--line-strong)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: "11px",
                fontWeight: "600",
                color: "var(--text-dim)",
              }}
            >
              {user.username.slice(0, 1).toUpperCase()}
            </div>
            <span
              style={{
                flex: 1,
                fontSize: "12.5px",
                color: "var(--text-dim)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {user.username}
            </span>
            <button
              className="text-btn"
              onClick={() => void logout()}
              title="Sign out"
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </aside>
  )
}

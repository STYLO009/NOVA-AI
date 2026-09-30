import { useEffect, useState } from "react"
import { NovaProvider, useNova } from "./store/NovaStore"
import { AuthProvider, useAuth } from "./store/AuthStore"
import { Sidebar } from "./components/Sidebar"
import { Header } from "./components/Header"
import { ChatPanel } from "./components/ChatPanel"
import { RightPanel } from "./components/RightPanel"
import { VoiceMode } from "./components/VoiceMode"
import { ConfirmDialog } from "./components/ConfirmDialog"
import { LoginScreen } from "./components/LoginScreen"
import {
  AppsView,
  FilesView,
  HistoryView,
  SettingsView,
  TasksView,
} from "./views/SectionViews"
import type { NavSection } from "./lib/types"

const SECTION_LABELS: Record<NavSection, string> = {
  dashboard: "Dashboard",
  tasks: "Tasks",
  apps: "Apps",
  files: "Files",
  history: "History",
  settings: "Settings",
}

function Workspace() {
  const { state, answerConfirm } = useNova()
  const [activeNav, setActiveNav] = useState<NavSection>("dashboard")
  const [voiceMode, setVoiceMode] = useState(false)
  const dashboard = activeNav === "dashboard"

  /* Cursor light: the spotlight layer is a fixed overlay whose gradient
     origin follows the pointer via CSS variables. rAF-throttled so a
     busy mouse never triggers a style write per event. */
  useEffect(() => {
    let frame = 0
    const onMove = (e: MouseEvent) => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        const root = document.documentElement
        root.style.setProperty("--mx", `${e.clientX}px`)
        root.style.setProperty("--my", `${e.clientY}px`)
      })
    }
    window.addEventListener("mousemove", onMove)
    return () => {
      window.removeEventListener("mousemove", onMove)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <div
      className="app-root"
      style={{
        color: "var(--text)",
        height: "100vh",
        display: "flex",
      }}
    >
      <div className="spotlight" aria-hidden="true" />

      <Sidebar
        active={activeNav}
        onNav={setActiveNav}
        online={state.online}
        connecting={state.connecting}
        llmMode={state.health?.llm_mode}
      />

      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "clip",
          minWidth: 0,
        }}
      >
        <Header
          onVoiceMode={() => setVoiceMode(true)}
          running={state.running}
          alerts={state.alerts}
          sectionLabel={SECTION_LABELS[activeNav]}
        />

        <div
          style={{
            flex: 1,
            display: "grid",
            gridTemplateColumns: dashboard
              ? "minmax(0, 1fr) 340px"
              : "minmax(0, 1fr)",
            gridTemplateRows: "minmax(0, 1fr)",
            gap: "14px",
            padding: "14px 16px 16px",
            overflow: "clip",
            // `clip` is not a scroll container, so min-height:auto stays
            // content-based (unlike `hidden`): without these zeros the grid
            // refuses to shrink below its content, overflows the column and
            // clips the bottom of the dashboard — nothing below the fold
            // stays reachable and .right-col never becomes a scroller.
            minHeight: 0,
          }}
        >
          <div
            key={activeNav}
            className="view-enter"
            style={{ overflow: "clip", minWidth: 0, minHeight: 0 }}
          >
            {dashboard && <ChatPanel />}
            {activeNav === "tasks" && <TasksView onNavigate={setActiveNav} />}
            {activeNav === "apps" && <AppsView onNavigate={setActiveNav} />}
            {activeNav === "files" && <FilesView onNavigate={setActiveNav} />}
            {activeNav === "history" && (
              <HistoryView onNavigate={setActiveNav} />
            )}
            {activeNav === "settings" && <SettingsView />}
          </div>

          {dashboard && <RightPanel onNav={setActiveNav} />}
        </div>
      </div>

      {voiceMode && <VoiceMode onClose={() => setVoiceMode(false)} />}
      {state.confirm && (
        <ConfirmDialog request={state.confirm} onDecide={answerConfirm} />
      )}
    </div>
  )
}

/** Session bootstrap: while we resolve the token, show a neutral splash. */
function Boot() {
  const { status, authEnabled } = useAuth()
  const { state } = useNova()

  if (status === "checking") {
    return (
      <div
        className="app-root"
        style={{
          color: "var(--text-faint)",
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: "16px",
        }}
      >
        <div
          className="mark mark--float"
          style={{ width: "46px", height: "46px" }}
        >
          <span style={{ fontSize: "20px" }}>N</span>
        </div>
        <div style={{ fontSize: "12.5px" }}>Loading your session…</div>
      </div>
    )
  }

  if (authEnabled && status === "anonymous") {
    return <LoginScreen health={state.health?.model ?? null} />
  }

  return <Workspace />
}

function Shell() {
  const { status, user } = useAuth()
  // Remount the workspace per session so signing out always returns NOVA to
  // a neutral state — no leftover chat, pipeline or history from the last user.
  return (
    <NovaProvider key={`${status}:${user?.id ?? "anon"}`}>
      <Boot />
    </NovaProvider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}

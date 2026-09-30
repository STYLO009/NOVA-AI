import { useEffect, useState } from "react"
import { Icon } from "./icons"

function clock() {
  return new Date().toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}

export function Header({
  onVoiceMode,
  running,
  alerts,
  sectionLabel,
}: {
  onVoiceMode: () => void
  running: number
  alerts: number
  sectionLabel: string
}) {
  const [time, setTime] = useState(clock)
  useEffect(() => {
    const id = setInterval(() => setTime(clock()), 15000)
    return () => clearInterval(id)
  }, [])

  const dateStr = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "short",
    day: "numeric",
  })

  return (
    <header
      className="topbar"
      style={{
        height: "58px",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "16px",
        padding: "0 20px",
        borderBottom: "1px solid var(--line)",
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: "14.5px", fontWeight: "600" }}>
          {sectionLabel}
        </div>
        <div
          style={{
            fontSize: "12px",
            color: "var(--text-faint)",
            marginTop: "1px",
          }}
        >
          {dateStr}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        {running > 0 && (
          <span className="chip">
            <span
              className="status-dot status-dot--live"
              style={{ background: "var(--accent)" }}
            />
            {running} running
          </span>
        )}
        {alerts > 0 && (
          <span className="chip" style={{ color: "var(--amber)" }}>
            {alerts} alert{alerts === 1 ? "" : "s"}
          </span>
        )}

        <span className="chip mono" style={{ padding: "4px 9px" }}>
          {time}
        </span>

        <button className="btn" onClick={onVoiceMode} title="Open voice mode">
          {Icon.mic("currentColor")}
          Voice
        </button>
      </div>
    </header>
  )
}

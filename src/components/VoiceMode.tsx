import { useEffect, useRef, useState } from "react"
import { useNova } from "../store/NovaStore"
import { speak, useSpeech } from "../hooks/useSpeech"
import { Icon } from "./icons"
import type { LogEntry, PipelineStep } from "../lib/types"

function StepRow({ step }: { step: PipelineStep }) {
  const state = step.status
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "12px",
        padding: "9px 0",
        borderBottom: "1px solid var(--line)",
      }}
    >
      <div
        className={`step-dot${state === "done" ? " step-dot--done" : state === "active" ? " step-dot--active" : ""}`}
        style={{
          width: "24px",
          height: "24px",
          fontSize: "10px",
          borderWidth: "1px",
        }}
      >
        {state === "done" ? "✓" : state === "active" ? "•" : "○"}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: "12.5px",
            fontWeight: "500",
            color: state === "pending" ? "var(--text-faint)" : "var(--text)",
          }}
        >
          {step.label}
        </div>
        <div
          className="mono"
          style={{
            fontSize: "10.5px",
            color: "var(--text-faint)",
            marginTop: "1px",
          }}
        >
          {step.detail}
        </div>
      </div>
      {state === "active" && (
        <span
          className="status-dot status-dot--live"
          style={{ background: "var(--accent)" }}
        />
      )}
    </div>
  )
}

function LogRow({ log, showCursor }: { log: LogEntry; showCursor: boolean }) {
  const marker =
    log.status === "done"
      ? "✓"
      : log.status === "active"
        ? "›"
        : log.status === "failed"
          ? "✕"
          : "·"
  const markerColor =
    log.status === "done"
      ? "var(--green)"
      : log.status === "active"
        ? "var(--accent)"
        : log.status === "failed"
          ? "var(--red)"
          : "var(--text-faint)"
  const textColor =
    log.status === "done"
      ? "var(--text-faint)"
      : log.status === "active"
        ? "var(--text)"
        : log.status === "failed"
          ? "#f0a2a7"
          : "var(--text-faint)"

  return (
    <div style={{ display: "flex", gap: "9px", alignItems: "flex-start" }}>
      <span style={{ color: markerColor, flexShrink: 0, lineHeight: "1.5" }}>
        {marker}
      </span>
      <span style={{ color: textColor, lineHeight: "1.5" }}>
        {log.text}
        {showCursor && log.status === "active" && (
          <span style={{ animation: "blink 1s step-end infinite" }}>▍</span>
        )}
      </span>
    </div>
  )
}

export function VoiceMode({ onClose }: { onClose: () => void }) {
  const { state, sendMessage } = useNova()

  const [idleProgress, setIdleProgress] = useState(0)
  const [lastTranscript, setLastTranscript] = useState("")
  const spokenRef = useRef<string | null>(null)

  const busy =
    state.taskStatus === "queued" ||
    state.taskStatus === "running" ||
    state.taskStatus === "awaiting_confirmation"

  const speech = useSpeech((text) => {
    setLastTranscript(text)
    sendMessage(text)
  })

  // Auto-listen on open so the "Listening" indicator reflects reality.
  useEffect(() => {
    if (speech.supported) speech.start()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // No idle simulation — the overlay shows a standby state until a real
  // task is running, exactly like the rest of the app.
  useEffect(() => {
    if (busy) setIdleProgress(0)
  }, [busy])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  // Speak NOVA's replies while the voice overlay is open.
  const lastMessage = state.messages[state.messages.length - 1]
  useEffect(() => {
    if (!lastMessage || lastMessage.active) return
    if (spokenRef.current === lastMessage.id) return
    spokenRef.current = lastMessage.id
    if (lastMessage.role === "nova") speak(lastMessage.content)
  }, [lastMessage])

  const pipeline = state.pipeline

  const logs: LogEntry[] = state.logs
  const activeLogIndex = logs.reduce(
    (found, log, i) => (log.status === "active" ? i : found),
    -1,
  )
  const progress = busy ? state.progress : idleProgress

  const lastUser = [...state.messages].reverse().find((m) => m.role === "user")
  const taskTitle =
    busy && lastUser
      ? lastUser.content.length > 34
        ? `${lastUser.content.slice(0, 34)}…`
        : lastUser.content
      : "Standing by"

  const transcript =
    speech.listening && speech.interim
      ? `“${speech.interim}…”`
      : lastTranscript
        ? `“${lastTranscript}”`
        : "…"

  const listening = speech.listening

  return (
    <div
      className="app-root"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 200,
        display: "flex",
        flexDirection: "column",
        fontFamily: "Inter, sans-serif",
      }}
    >
      {/* Close */}
      <button
        className="btn btn--icon"
        onClick={onClose}
        title="Close voice mode"
        style={{
          position: "absolute",
          top: "20px",
          right: "20px",
          zIndex: 10,
        }}
      >
        {Icon.close("currentColor")}
      </button>

      {/* Top bar */}
      <div
        style={{
          padding: "22px 36px",
          display: "flex",
          alignItems: "center",
          gap: "14px",
          borderBottom: "1px solid var(--line)",
          flexShrink: 0,
        }}
      >
        <span className="wordmark">NOVA</span>
        <span
          style={{
            width: "1px",
            height: "16px",
            background: "var(--line-strong)",
          }}
        />
        <span style={{ fontSize: "13px", color: "var(--text-faint)" }}>
          Voice
        </span>
        <div
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: "8px",
            marginRight: "56px",
          }}
        >
          <span
            className={`status-dot${listening ? " status-dot--live" : ""}`}
            style={{
              background: listening ? "var(--accent)" : "var(--text-faint)",
            }}
          />
          <span
            style={{
              fontSize: "12.5px",
              color: listening ? "var(--accent)" : "var(--text-faint)",
              fontWeight: "500",
            }}
          >
            {listening ? "Listening" : speech.supported ? "Ready" : "Text only"}
          </span>
        </div>
      </div>

      {/* Body */}
      <div
        style={{
          flex: 1,
          display: "flex",
          gap: "44px",
          padding: "0 44px 36px",
          alignItems: "center",
          zIndex: 1,
          overflow: "hidden",
        }}
      >
        {/* Left: microphone + waveform + transcript */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: "40px",
            minWidth: 0,
          }}
        >
          {/* Microphone */}
          <div
            style={{ position: "relative", width: "148px", height: "148px" }}
          >
            <span className="orb-halo" />
            {listening && <span className="orb-orbit" />}
            {listening && (
              <span
                style={{
                  position: "absolute",
                  inset: 0,
                  borderRadius: "50%",
                  border: "1px solid var(--accent)",
                  animation: "ring-out 2.4s ease-out infinite",
                }}
              />
            )}
            <div
              style={{
                position: "absolute",
                inset: 0,
                borderRadius: "50%",
                background: "var(--surface-2)",
                border: `1px solid ${listening ? "var(--accent)" : "var(--line-strong)"}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                transition: "border-color 0.25s ease",
              }}
            >
              <div
                style={{
                  width: "62px",
                  height: "62px",
                  borderRadius: "50%",
                  background: listening
                    ? "var(--accent-soft)"
                    : "var(--surface-3)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: listening ? "var(--accent)" : "var(--text-faint)",
                  transition: "all 0.25s ease",
                }}
              >
                <span style={{ display: "flex", transform: "scale(1.7)" }}>
                  {Icon.mic("currentColor")}
                </span>
              </div>
            </div>
          </div>

          {/* Waveform */}
          <div
            style={{
              display: "flex",
              gap: "4px",
              alignItems: "center",
              height: "48px",
              maxWidth: "100%",
              justifyContent: "center",
            }}
          >
            {Array.from({ length: 36 }, (_, i) => (
              <div
                key={i}
                style={{
                  width: "3px",
                  height: "44px",
                  borderRadius: "2px",
                  background: "var(--accent)",
                  transformOrigin: "center",
                  transform: listening ? undefined : "scaleY(0.1)",
                  opacity: listening ? 0.7 : 0.25,
                  animation: listening
                    ? `wave ${0.45 + (i % 7) * 0.12}s ease-in-out infinite`
                    : "none",
                  animationDelay: `${i * 0.045}s`,
                }}
              />
            ))}
          </div>

          {/* Transcript */}
          <div style={{ textAlign: "center", maxWidth: "560px" }}>
            <div
              style={{
                fontSize: "21px",
                lineHeight: "1.55",
                marginBottom: "10px",
                color: "var(--text)",
              }}
            >
              {transcript}
              {listening && (
                <span className="caret" style={{ height: "20px" }} />
              )}
            </div>
            <div style={{ fontSize: "12.5px", color: "var(--text-faint)" }}>
              {speech.supported
                ? "Speak naturally · Esc to dismiss"
                : "Speech recognition is unavailable in this browser · Esc to dismiss"}
            </div>
          </div>
        </div>

        {/* Right: execution + logs */}
        <div
          style={{
            width: "380px",
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            gap: "14px",
          }}
        >
          <div className="card card-pad">
            <div className="section-head">
              <span className="eyebrow">Execution</span>
              <span
                className="mono"
                style={{ fontSize: "12px", color: "var(--accent)" }}
              >
                {Math.round(progress)}%
              </span>
            </div>

            <div style={{ marginBottom: "16px" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginBottom: "7px",
                  gap: "12px",
                  fontSize: "12.5px",
                }}
              >
                <span
                  style={{
                    color: "var(--text-dim)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {taskTitle}
                </span>
              </div>
              <div className="bar">
                <div className="bar__fill" style={{ width: `${progress}%` }} />
              </div>
            </div>

            {pipeline.map((step) => (
              <StepRow key={step.id} step={step} />
            ))}
          </div>

          <div className="card card-pad">
            <div className="section-head" style={{ marginBottom: "12px" }}>
              <span
                className="eyebrow"
                style={{ display: "flex", alignItems: "center", gap: "8px" }}
              >
                <span
                  className={`status-dot${busy ? " status-dot--live" : ""}`}
                  style={{
                    background: busy ? "var(--accent)" : "var(--line-strong)",
                  }}
                />
                Logs
              </span>
            </div>

            <div
              className="mono"
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "8px",
                fontSize: "11.5px",
              }}
            >
              {logs.length === 0 && (
                <div style={{ color: "var(--text-faint)", padding: "8px 0" }}>
                  Waiting for a command…
                </div>
              )}
              {logs.map((log, i) => (
                <LogRow
                  key={`${i}-${log.text}`}
                  log={log}
                  showCursor={i === activeLogIndex}
                />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

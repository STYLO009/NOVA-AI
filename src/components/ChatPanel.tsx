import { useEffect, useRef, useState } from "react"
import { useNova, SUGGESTIONS } from "../store/NovaStore"
import { useSpeech } from "../hooks/useSpeech"
import type { ChatMessage } from "../lib/types"
import { Icon } from "./icons"
import { Glass, renderBold } from "./primitives"

function ChatBubble({ msg }: { msg: ChatMessage }) {
  const isNova = msg.role === "nova"

  return (
    <div
      className="enter"
      style={{
        display: "flex",
        gap: "12px",
        flexDirection: isNova ? "row" : "row-reverse",
      }}
    >
      {isNova && (
        <div
          className="mark"
          style={{ width: "30px", height: "30px", fontSize: "13px" }}
        >
          N
        </div>
      )}

      <div
        style={{
          maxWidth: isNova ? "82%" : "78%",
          display: "flex",
          flexDirection: "column",
          gap: "5px",
          alignItems: isNova ? "flex-start" : "flex-end",
        }}
      >
        {isNova ? (
          <div
            style={{
              fontSize: "14px",
              lineHeight: "1.7",
              color: msg.error ? "#f0a2a7" : "var(--text)",
              whiteSpace: "pre-line",
            }}
          >
            {renderBold(
              msg.content,
              msg.error ? "var(--red)" : "var(--accent)",
            )}
            {msg.active && <span className="caret" />}
          </div>
        ) : (
          <div className="bubble">{msg.content}</div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span
            className="mono"
            style={{ fontSize: "11px", color: "var(--text-faint)" }}
          >
            {msg.time}
          </span>
          {msg.tokens != null && msg.tokens > 0 && (
            <span
              className="mono"
              style={{ fontSize: "10.5px", color: "var(--text-faint)" }}
            >
              {msg.tokens}t
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export function ChatPanel() {
  const { state, sendMessage } = useNova()
  const [input, setInput] = useState("")
  const bottomRef = useRef<HTMLDivElement>(null)

  const speech = useSpeech((text) => sendMessage(text))

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [state.messages.length])

  const submit = () => {
    const text = input.trim()
    if (!text) return
    if (sendMessage(text)) setInput("")
  }

  const active = speech.listening
  const ready = input.trim().length > 0

  return (
    <Glass
      style={{
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        height: "100%",
        minWidth: 0,
      }}
    >
      {/* Chat header */}
      <div
        style={{
          padding: "13px 18px",
          borderBottom: "1px solid var(--line)",
          display: "flex",
          alignItems: "center",
          gap: "10px",
          flexShrink: 0,
        }}
      >
        <span
          className={`status-dot${state.online ? " status-dot--live" : ""}`}
          style={{ background: state.online ? "var(--green)" : "var(--red)" }}
        />
        <span style={{ fontSize: "13px", fontWeight: "600" }}>Chat</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: "6px" }}>
          <span className="chip mono">
            {(state.contextTokens / 1000).toFixed(1)}k context
          </span>
          <span className="chip mono">{state.modelTag}</span>
        </div>
      </div>

      {/* Messages */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "22px 20px",
          display: "flex",
          flexDirection: "column",
          gap: "24px",
        }}
      >
        {state.messages.length === 0 && (
          <div
            style={{
              margin: "auto",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "12px",
              textAlign: "center",
              maxWidth: "520px",
            }}
          >
            <div
              className="mark mark--float"
              style={{ width: "44px", height: "44px" }}
            >
              <span style={{ fontSize: "19px" }}>N</span>
            </div>
            <div style={{ fontSize: "14px", fontWeight: "600" }}>
              Standing by
            </div>
            <div
              style={{
                fontSize: "13.5px",
                lineHeight: "1.7",
                color: "var(--text-faint)",
              }}
            >
              Nothing has run yet — nothing runs until you ask. Describe the
              goal and NOVA will plan it, show every step, and wait for your
              approval before touching anything destructive.
            </div>
            {state.connecting && (
              <div className="notice notice--info">
                Waiting for the backend on{" "}
                <span className="mono">127.0.0.1:8000</span>…
              </div>
            )}
          </div>
        )}

        {state.messages.map((msg) => (
          <ChatBubble key={msg.id} msg={msg} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div
        style={{
          padding: "14px 16px 16px",
          borderTop: "1px solid var(--line)",
          flexShrink: 0,
        }}
      >
        <div className="composer">
          <button
            className={`btn btn--icon${active ? " btn--accent" : ""}`}
            onClick={speech.toggle}
            title={
              speech.supported
                ? "Voice command"
                : "Speech recognition is unavailable in this browser"
            }
          >
            {Icon.mic("currentColor")}
          </button>

          {active ? (
            <div
              style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                gap: "3px",
                height: "36px",
                overflow: "hidden",
              }}
            >
              {Array.from({ length: 24 }, (_, i) => (
                <div
                  key={i}
                  style={{
                    flex: 1,
                    maxWidth: "4px",
                    height: "100%",
                    background: "var(--accent)",
                    borderRadius: "2px",
                    transformOrigin: "center",
                    animation: `wave ${0.5 + (i % 6) * 0.12}s ease-in-out infinite`,
                    animationDelay: `${i * 0.045}s`,
                    opacity: 0.75,
                  }}
                />
              ))}
              <span
                style={{
                  fontSize: "12.5px",
                  color: "var(--accent)",
                  marginLeft: "10px",
                  flexShrink: 0,
                }}
              >
                {speech.interim ? speech.interim.slice(0, 28) : "Listening…"}
              </span>
            </div>
          ) : (
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask NOVA to do something…"
              rows={1}
              onInput={(e) => {
                const el = e.currentTarget
                el.style.height = "auto"
                el.style.height = Math.min(el.scrollHeight, 120) + "px"
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  submit()
                }
              }}
            />
          )}

          <button
            className={`btn btn--icon${ready || active ? " btn--accent" : ""}`}
            onClick={() => {
              if (active) speech.stop()
              else submit()
            }}
            disabled={!ready && !active}
            title={active ? "Stop listening" : "Send"}
          >
            {Icon.send("currentColor")}
          </button>
        </div>

        <div
          style={{
            display: "flex",
            gap: "7px",
            marginTop: "11px",
            flexWrap: "wrap",
          }}
        >
          {SUGGESTIONS.map((s) => (
            <button key={s} className="suggestion" onClick={() => setInput(s)}>
              {s}
            </button>
          ))}
        </div>
      </div>
    </Glass>
  )
}

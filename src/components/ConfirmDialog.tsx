import { useEffect } from "react"
import type { ConfirmRequest } from "../lib/types"

/**
 * Destructive-action confirmation — raised by the tool registry, never by
 * the model. Escape and Reject both decline.
 */
export function ConfirmDialog({
  request,
  onDecide,
}: {
  request: ConfirmRequest
  onDecide: (approved: boolean) => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation()
        onDecide(false)
      }
    }
    window.addEventListener("keydown", onKey, true)
    return () => window.removeEventListener("keydown", onKey, true)
  }, [onDecide])

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 300,
        background: "rgba(6,7,9,0.72)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "Inter, sans-serif",
      }}
    >
      <div
        className="card enter"
        style={{
          width: "460px",
          maxWidth: "calc(100vw - 40px)",
          boxShadow: "var(--shadow-pop)",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "18px 22px",
            display: "flex",
            alignItems: "center",
            gap: "13px",
            borderBottom: "1px solid var(--line)",
          }}
        >
          <div
            style={{
              width: "34px",
              height: "34px",
              borderRadius: "9px",
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "var(--amber-soft)",
              border: "1px solid rgba(223, 162, 63, 0.4)",
              color: "var(--amber)",
              fontSize: "16px",
            }}
          >
            !
          </div>
          <div>
            <div style={{ fontSize: "14.5px", fontWeight: "600" }}>
              Approval required
            </div>
            <div
              style={{
                fontSize: "12.5px",
                color: "var(--text-faint)",
                marginTop: "2px",
              }}
            >
              This action can&apos;t be undone
            </div>
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: "20px 22px 6px" }}>
          <div style={{ marginBottom: "12px" }}>
            <span
              className="chip"
              style={{
                background: "var(--amber-soft)",
                borderColor: "rgba(223, 162, 63, 0.4)",
                color: "var(--amber)",
              }}
            >
              {request.risk}
            </span>
          </div>

          <div
            style={{
              fontSize: "14.5px",
              lineHeight: "1.6",
              marginBottom: "10px",
            }}
          >
            {request.question || `Allow NOVA to run “${request.title}”?`}
          </div>

          <div
            style={{
              fontSize: "13px",
              color: "var(--text-dim)",
              lineHeight: "1.6",
            }}
          >
            {request.detail}
          </div>

          <div
            className="mono"
            style={{
              marginTop: "16px",
              padding: "10px 12px",
              borderRadius: "8px",
              background: "var(--surface-2)",
              borderLeft: "2px solid var(--accent)",
              fontSize: "11.5px",
              color: "var(--text-dim)",
            }}
          >
            step {request.stepId} · awaiting your decision
          </div>
        </div>

        {/* Actions */}
        <div
          style={{
            padding: "18px 22px 22px",
            display: "flex",
            gap: "10px",
            justifyContent: "flex-end",
          }}
        >
          <button className="btn" onClick={() => onDecide(false)}>
            Reject
          </button>
          <button
            autoFocus
            className="btn btn--danger"
            onClick={() => onDecide(true)}
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}

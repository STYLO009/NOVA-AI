// NOVA — shared visual primitives.

import type { ReactNode } from "react"

export function Glass({
  children,
  style = {},
  className = "",
}: {
  children: ReactNode
  style?: React.CSSProperties
  className?: string
}) {
  return (
    <div className={`card ${className}`} style={style}>
      {children}
    </div>
  )
}

export function SectionLabel({
  children,
  action,
  onAction,
}: {
  children: ReactNode
  action?: string
  onAction?: () => void
}) {
  return (
    <div className="section-head">
      <span className="eyebrow">{children}</span>
      {action && (
        <button className="text-btn" onClick={onAction}>
          {action}
        </button>
      )}
    </div>
  )
}

export function RingProgress({
  value,
  color,
  size = 60,
}: {
  value: number
  color: string
  size?: number
}) {
  const r = size / 2 - 5.5
  const circ = 2 * Math.PI * r
  const offset = circ - (value / 100) * circ
  return (
    <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--surface-3)"
        strokeWidth="3.5"
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth="3.5"
        strokeDasharray={circ}
        strokeDashoffset={offset}
        strokeLinecap="round"
        style={{
          transition: "stroke-dashoffset 1.2s cubic-bezier(0.4,0,0.2,1)",
        }}
      />
    </svg>
  )
}

export function renderBold(text: string, accentColor: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} style={{ color: accentColor, fontWeight: "600" }}>
        {part.slice(2, -2)}
      </strong>
    ) : (
      part
    ),
  )
}

import { useNova } from "../store/NovaStore"
import { Icon } from "./icons"
import { Glass, RingProgress, SectionLabel } from "./primitives"
import { useCountUp } from "../hooks/useCountUp"
import type { NavSection } from "../lib/types"

/**
 * Stat values are `null` until the realtime feed delivers the first real
 * sample — nothing here is ever faked. Live numbers tally to their new
 * value instead of jumping.
 */
function StatRing({ label, value }: { label: string; value: number | null }) {
  const live = value != null
  const shown = useCountUp(live ? Math.max(0, Math.min(100, value)) : null)
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "7px",
      }}
    >
      <div style={{ position: "relative", width: "58px", height: "58px" }}>
        <RingProgress value={shown ?? 0} color="var(--accent)" size={58} />
        <div
          className="mono"
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "11px",
            fontWeight: "500",
            color: live ? "var(--text)" : "var(--text-faint)",
          }}
        >
          {live && shown != null ? `${Math.round(shown)}%` : "—"}
        </div>
      </div>
      <span className="eyebrow">{label}</span>
    </div>
  )
}

function EmptyRow({ text }: { text: string }) {
  return (
    <div
      style={{
        padding: "14px 12px",
        borderRadius: "10px",
        border: "1px dashed var(--line-strong)",
        fontSize: "12px",
        lineHeight: "1.55",
        color: "var(--text-faint)",
        textAlign: "center",
      }}
    >
      {text}
    </div>
  )
}

export function RightPanel({ onNav }: { onNav: (s: NavSection) => void }) {
  const { state, sendMessage } = useNova()

  const sys = state.system
  const cpu = sys?.cpu ?? null
  const ram = sys?.ram ?? null
  const storage = sys?.ssd ?? null
  const network = sys?.network ?? null
  const networkDisplay = sys?.networkDisplay ?? "—"
  const gpuTemp = sys?.gpuTemp ?? null
  const running =
    state.taskStatus === "running" ||
    state.taskStatus === "queued" ||
    state.taskStatus === "awaiting_confirmation"

  const apps = state.apps
  const files = state.files
  const history = state.history

  return (
    <div
      className="right-col"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "14px",
        overflowY: "auto",
        height: "100%",
        minWidth: 0,
        paddingRight: "2px",
      }}
    >
      {/* Pipeline */}
      <Glass className="card-pad">
        <SectionLabel>Task pipeline</SectionLabel>

        <div style={{ position: "relative", marginBottom: "18px" }}>
          {/* Track — inset to the first and last step centres */}
          <div
            style={{
              position: "absolute",
              top: "17px",
              left: "34px",
              right: "34px",
              height: "2px",
              background: "var(--surface-3)",
            }}
          >
            <div
              className={`track-fill${running ? " is-flowing" : ""}`}
              style={{ width: `${state.progress}%` }}
            />
          </div>

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              position: "relative",
              zIndex: 1,
            }}
          >
            {state.pipeline.map((step) => (
              <div
                key={step.id}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: "8px",
                  width: "68px",
                }}
              >
                <div
                  className={`step-dot${
                    step.status === "done"
                      ? " step-dot--done"
                      : step.status === "active"
                        ? " step-dot--active"
                        : ""
                  }`}
                >
                  {step.status === "done"
                    ? "✓"
                    : step.status === "active"
                      ? "•"
                      : "○"}
                </div>
                <span
                  style={{
                    fontSize: "11px",
                    fontWeight: step.status === "pending" ? "500" : "600",
                    textAlign: "center",
                    color:
                      step.status === "pending"
                        ? "var(--text-faint)"
                        : "var(--text-dim)",
                  }}
                >
                  {step.label}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
          {state.pipeline.map((step) => (
            <div
              key={step.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "10px",
                padding: "8px 10px",
                borderRadius: "8px",
                background:
                  step.status === "active"
                    ? "var(--accent-soft)"
                    : "transparent",
              }}
            >
              <span
                className={`status-dot${
                  step.status === "active" ? " status-dot--live" : ""
                }`}
                style={{
                  background:
                    step.status === "done"
                      ? "var(--green)"
                      : step.status === "active"
                        ? "var(--accent)"
                        : "var(--line-strong)",
                }}
              />
              <span
                style={{
                  flex: 1,
                  fontSize: "12.5px",
                  color:
                    step.status === "active"
                      ? "var(--text)"
                      : step.status === "done"
                        ? "var(--text-dim)"
                        : "var(--text-faint)",
                  fontWeight: step.status === "active" ? "500" : "400",
                }}
              >
                {step.detail}
              </span>
              {step.status === "active" && (
                <span
                  className="mono"
                  style={{
                    fontSize: "10px",
                    color: "var(--accent)",
                    fontWeight: "500",
                  }}
                >
                  running
                </span>
              )}
            </div>
          ))}
        </div>
      </Glass>

      {/* System stats */}
      <Glass className="card-pad">
        <SectionLabel>System</SectionLabel>

        <div
          style={{
            display: "flex",
            justifyContent: "space-around",
            marginBottom: "18px",
          }}
        >
          <StatRing label="CPU" value={cpu} />
          <StatRing label="RAM" value={ram} />
          <StatRing label="SSD" value={storage} />
        </div>

        {[
          {
            label: "Network",
            value: network ?? null,
            display: networkDisplay,
            color: "var(--accent)",
          },
          {
            label: "GPU temp",
            value: gpuTemp == null ? null : Math.min(100, gpuTemp),
            display: gpuTemp == null ? "—" : `${Math.round(gpuTemp)}°C`,
            color: "var(--amber)",
          },
        ].map((s) => (
          <div key={s.label} style={{ marginBottom: "12px" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginBottom: "6px",
                fontSize: "12px",
              }}
            >
              <span style={{ color: "var(--text-faint)" }}>{s.label}</span>
              <span className="mono" style={{ color: "var(--text-dim)" }}>
                {s.display}
              </span>
            </div>
            <div className="bar">
              <div
                className="bar__fill"
                style={{
                  width: `${Math.max(0, Math.min(100, s.value ?? 0))}%`,
                  background: s.color,
                }}
              />
            </div>
          </div>
        ))}
      </Glass>

      {/* Recent apps */}
      <Glass className="card-pad">
        <SectionLabel action="View all" onAction={() => onNav("apps")}>
          Recent apps
        </SectionLabel>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "8px",
          }}
        >
          {apps.length === 0 && (
            <div style={{ gridColumn: "1 / -1" }}>
              <EmptyRow text="No applications detected yet — NOVA scans this machine on first use." />
            </div>
          )}
          {apps.map((app) => (
            <button
              key={app.name}
              className="row"
              title={`Open ${app.name}`}
              onClick={() => sendMessage(`open ${app.name}`)}
              style={{
                fontSize: "12.5px",
                color: "var(--text-dim)",
                minWidth: 0,
              }}
            >
              <span
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
                  fontSize: "10px",
                  fontWeight: "600",
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
      </Glass>

      {/* Recent files */}
      <Glass className="card-pad">
        <SectionLabel action="Browse" onAction={() => onNav("files")}>
          Recent files
        </SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
          {files.length === 0 && (
            <EmptyRow text="No recent files yet — create one by asking NOVA." />
          )}
          {files.map((file) => (
            <div
              key={file.name}
              className="row"
              title={file.path ?? file.name}
              onClick={() =>
                file.path && sendMessage(`read the file "${file.path}"`)
              }
              style={{ cursor: file.path ? "pointer" : "default" }}
            >
              <span
                className="mono"
                style={{
                  padding: "2px 6px",
                  borderRadius: "5px",
                  fontSize: "10px",
                  fontWeight: "500",
                  background: "var(--surface-3)",
                  border: "1px solid var(--line-strong)",
                  color: "var(--text-dim)",
                  flexShrink: 0,
                }}
              >
                {file.ext}
              </span>
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
                {file.name}
              </span>
              <span
                className="mono"
                style={{
                  fontSize: "11px",
                  color: "var(--text-faint)",
                  flexShrink: 0,
                }}
              >
                {file.time}
              </span>
            </div>
          ))}
        </div>
      </Glass>

      {/* Task history */}
      <Glass className="card-pad">
        <SectionLabel action="View all" onAction={() => onNav("history")}>
          Recent tasks
        </SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
          {history.length === 0 && (
            <EmptyRow text="No tasks run yet — history appears after your first command." />
          )}
          {history.map((task) => (
            <div
              key={task.id}
              className="row"
              onClick={() => onNav("history")}
              style={{ cursor: "pointer" }}
            >
              <span
                className="status-dot"
                style={{
                  background:
                    task.status === "failed" || task.status === "rejected"
                      ? "var(--red)"
                      : task.status === "completed"
                        ? "var(--green)"
                        : "var(--accent)",
                }}
              />
              <div style={{ flex: 1, overflow: "hidden" }}>
                <div
                  style={{
                    fontSize: "12.5px",
                    color: "var(--text-dim)",
                    fontWeight: "500",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {task.title}
                </div>
                <div
                  className="mono"
                  style={{
                    fontSize: "10.5px",
                    color: "var(--text-faint)",
                    marginTop: "2px",
                  }}
                >
                  {task.steps} steps · {task.time}
                </div>
              </div>
              {Icon.chevron("var(--text-faint)")}
            </div>
          ))}
        </div>
      </Glass>
    </div>
  )
}

// NOVA — sign in / create account screen.

import { useEffect, useState, type FormEvent } from "react"
import { useAuth } from "../store/AuthStore"
import { renderBold } from "./primitives"

type Mode = "login" | "register"

export function LoginScreen({ health }: { health?: string | null }) {
  const { authEnabled, busy, error, login, register, clearError } = useAuth()
  const [mode, setMode] = useState<Mode>("login")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")

  useEffect(() => {
    clearError()
  }, [mode, clearError])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const name = username.trim()
    if (!name || !password) return
    if (mode === "register") {
      if (password !== confirm) return
      void register(name, password)
    } else {
      void login(name, password)
    }
  }

  const canSubmit =
    !busy &&
    username.trim().length > 0 &&
    password.length > 0 &&
    (mode === "login" || (confirm.length > 0 && password === confirm))

  return (
    <div
      className="app-root"
      style={{
        color: "var(--text)",
        height: "100vh",
        width: "100vw",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        className="card enter"
        style={{
          width: "min(420px, calc(100vw - 40px))",
          padding: "32px 30px 28px",
          boxShadow: "var(--shadow-pop)",
        }}
      >
        {/* Brand */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "12px",
            marginBottom: "26px",
          }}
        >
          <div className="mark" style={{ width: "42px", height: "42px" }}>
            <span style={{ fontSize: "19px" }}>N</span>
          </div>
          <div>
            <div className="wordmark" style={{ fontSize: "15px" }}>
              NOVA
            </div>
            <div
              style={{
                fontSize: "12px",
                color: "var(--text-faint)",
                marginTop: "1px",
              }}
            >
              Personal agent
            </div>
          </div>
        </div>

        <h1 style={{ margin: 0, fontSize: "17px", fontWeight: "600" }}>
          {mode === "login" ? "Welcome back" : "Create your account"}
        </h1>
        <p
          style={{
            margin: "7px 0 22px",
            fontSize: "13px",
            lineHeight: "1.6",
            color: "var(--text-faint)",
          }}
        >
          {mode === "login"
            ? "Sign in to reach your own history, memory and tasks."
            : "Each account keeps its own task history and long-term memory."}
        </p>

        <form onSubmit={submit} style={{ display: "grid", gap: "14px" }}>
          <label>
            <span className="field-label">Username</span>
            <input
              className="input"
              name="username"
              autoComplete="username"
              placeholder="e.g. alex"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>

          <label>
            <span className="field-label">Password</span>
            <input
              className="input"
              name="password"
              type="password"
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              placeholder={mode === "login" ? "••••••••" : "8+ characters"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {mode === "register" && (
            <label>
              <span className="field-label">Confirm password</span>
              <input
                className="input"
                name="confirm"
                type="password"
                autoComplete="new-password"
                placeholder="Repeat it"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </label>
          )}

          {error && <div className="notice notice--error">{error}</div>}
          {mode === "register" && confirm && password !== confirm && (
            <div style={{ fontSize: "12px", color: "#f0a2a7" }}>
              Passwords do not match.
            </div>
          )}

          <button
            type="submit"
            className="btn btn--primary"
            disabled={!canSubmit}
            style={{
              marginTop: "6px",
              padding: "12px 16px",
              fontSize: "13.5px",
            }}
          >
            {busy
              ? "Please wait…"
              : mode === "login"
                ? "Sign in"
                : "Create account"}
          </button>
        </form>

        <div
          style={{
            marginTop: "18px",
            paddingTop: "16px",
            borderTop: "1px solid var(--line)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "10px",
            fontSize: "13px",
            color: "var(--text-faint)",
          }}
        >
          <span>
            {mode === "login" ? "No account yet?" : "Already have an account?"}
          </span>
          <button
            type="button"
            className="text-btn"
            style={{ color: "var(--accent)", fontSize: "13px" }}
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login" ? "Sign up" : "Sign in"}
          </button>
        </div>

        {!authEnabled && (
          <div
            style={{
              marginTop: "14px",
              fontSize: "12px",
              lineHeight: "1.5",
              color: "var(--text-faint)",
            }}
          >
            {renderBold(
              "Authentication is disabled on this backend (`NOVA_AUTH_ENABLED=false`).",
              "var(--amber)",
            )}
          </div>
        )}

        {health && (
          <div
            className="mono"
            style={{
              marginTop: "12px",
              fontSize: "11px",
              color: "var(--text-faint)",
              textAlign: "center",
            }}
          >
            {health}
          </div>
        )}
      </div>
    </div>
  )
}

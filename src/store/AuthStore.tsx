// ─────────────────────────────────────────────────────
// NOVA — authentication store (multi-user signup + login)
//
// The session token is kept in memory and mirrored to localStorage so a
// reload keeps the user signed in; the backend also sets an HttpOnly
// cookie for same-origin (Docker/nginx) deployments.
// ─────────────────────────────────────────────────────

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react"
import { api, getToken, onUnauthorized, setToken } from "../lib/api"
import type { User } from "../lib/types"

export type AuthStatus = "checking" | "anonymous" | "authenticated"

export interface AuthApi {
  status: AuthStatus
  user: User | null
  /** Backend allows sign-in at all (`NOVA_AUTH_ENABLED`). */
  authEnabled: boolean
  error: string | null
  busy: boolean
  login: (username: string, password: string) => Promise<boolean>
  register: (username: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
  clearError: () => void
}

const AuthContext = createContext<AuthApi | null>(null)

function messageOf(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err)
  const body = raw.replace(/^\d+:\s*/, "")
  return body || "Something went wrong"
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("checking")
  const [user, setUser] = useState<User | null>(null)
  const [authEnabled, setAuthEnabled] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Resolve the session (token or cookie) on mount.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const health = await api.health()
        if (!cancelled) setAuthEnabled(health.auth !== false)
      } catch {
        if (!cancelled) setAuthEnabled(true)
      }
      if (!getToken()) {
        if (!cancelled) setStatus("anonymous")
        return
      }
      try {
        const { user: me } = await api.me()
        if (!cancelled) {
          setUser(me)
          setStatus("authenticated")
        }
      } catch {
        if (!cancelled) setStatus("anonymous")
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // Any 401 from the API drops the shell back to the login screen.
  useEffect(
    () =>
      onUnauthorized(() => {
        setUser(null)
        setStatus("anonymous")
      }),
    [],
  )

  const finish = useCallback((res: { user: User; token: string }) => {
    setToken(res.token)
    setUser(res.user)
    setStatus("authenticated")
    setError(null)
  }, [])

  const login = useCallback(
    async (username: string, password: string) => {
      setBusy(true)
      setError(null)
      try {
        finish(await api.login(username, password))
        return true
      } catch (err) {
        setError(messageOf(err))
        return false
      } finally {
        setBusy(false)
      }
    },
    [finish],
  )

  const register = useCallback(
    async (username: string, password: string) => {
      setBusy(true)
      setError(null)
      try {
        finish(await api.register(username, password))
        return true
      } catch (err) {
        setError(messageOf(err))
        return false
      } finally {
        setBusy(false)
      }
    },
    [finish],
  )

  const logout = useCallback(async () => {
    await api.logout()
    setUser(null)
    setStatus("anonymous")
    setError(null)
  }, [])

  const value = useMemo<AuthApi>(
    () => ({
      status,
      user,
      authEnabled,
      error,
      busy,
      login,
      register,
      logout,
      clearError: () => setError(null),
    }),
    [status, user, authEnabled, error, busy, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthApi {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>")
  return ctx
}

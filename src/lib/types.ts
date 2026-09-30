// ─────────────────────────────────────────────────────
// NOVA — shared types (frontend ⇄ backend contract)
// ─────────────────────────────────────────────────────

export type NavSection =
  | "dashboard"
  | "tasks"
  | "apps"
  | "files"
  | "history"
  | "settings"

export type Phase = "understand" | "plan" | "execute" | "verify"

export type StepStatus = "done" | "active" | "pending"

export type LogStatus = "done" | "active" | "pending" | "failed"

export type TaskStatus =
  | "idle"
  | "queued"
  | "running"
  | "awaiting_confirmation"
  | "completed"
  | "failed"
  | "rejected"

// ── UI models ────────────────────────────────────────

export interface ChatMessage {
  id: string
  role: "nova" | "user"
  content: string
  time: string
  tokens?: number
  active?: boolean
  error?: boolean
}

export interface PipelineStep {
  id: string
  label: string
  status: StepStatus
  detail: string
}

export interface LogEntry {
  text: string
  status: LogStatus
}

export interface HistoryItem {
  id: string
  title: string
  time: string
  steps: number
  status?: string
}

export interface RecentApp {
  name: string
  abbr: string
  color: string
}

export interface RecentFile {
  name: string
  ext: string
  time: string
  color: string
  path?: string
}

export interface SystemStats {
  cpu: number
  ram: number
  ssd: number
  network: number
  networkDisplay: string
  gpuTemp: number | null
}

export interface ConfirmRequest {
  taskId: string
  stepId: number
  title: string
  detail: string
  risk: string
  question: string
}

export interface PlanStep {
  id: number
  tool: string
  title: string
  detail: string
  destructive: boolean
  status: string
}

// ── Backend payload models ───────────────────────────

export interface Health {
  status: string
  version: string
  llm_mode: "huggingface" | "local" | "fallback"
  model: string
  workspace?: string
  features: Record<string, boolean>
}

export interface HistoryResponse {
  tasks: Array<{
    id: string
    input: string
    summary: string
    status: string
    steps: number
    phase: string
    created_at: string
    duration_ms: number
  }>
}

export interface SystemResponse {
  cpu: number
  ram: number
  ssd: number
  network_mbps: number
  network_display: string
  gpu_temp: number | null
  os?: string
  hostname?: string
  user?: string
  python?: string
  cores?: number
  ram_total_gb?: number
  uptime_s?: number
}

export interface AppsResponse {
  apps: RecentApp[]
}

export interface FilesResponse {
  files: Array<RecentFile & { path?: string }>
}

export interface ChatResponse {
  task_id: string
  status: string
}

export interface DecisionResponse {
  ok: boolean
  status: string
}

// ── Auth ─────────────────────────────────────────────

export interface User {
  id: number
  username: string
  created_at?: string
}

export interface AuthResponse {
  user: User
  token: string
  expires_ts: number
}

export interface MemoryItem {
  id: number
  key: string
  value: string
  created_at: string
  score?: number
}

export interface MemorySearchResponse {
  query: string
  items: MemoryItem[]
  engine: string
}

export interface FileEntry {
  name: string
  path: string
  relative: string
  is_dir: boolean
  size: number
  ext: string
  color: string
  time: string
}

export interface FileListResponse {
  path: string
  relative: string
  parent: string
  entries: FileEntry[]
}

export interface VectorInfo {
  mode: "server" | "local" | "disabled"
  available: boolean
  collection: string
  embeddings: string
  count: number
  error?: string
}

export interface Health {
  status: string
  version: string
  llm_mode: "huggingface" | "local" | "fallback"
  model: string
  workspace?: string
  features: Record<string, boolean>
  auth?: boolean
  users?: number
  uptime_s?: number
  vector?: VectorInfo
  tasks_active?: number
}

// ── SSE events (emitted by the FastAPI backend) ──────

export type BackendEvent =
  | {
      type: "started"
      seq: number
      task_id: string
      input: string
      ts: string
    }
  | {
      type: "phase"
      seq: number
      phase: Phase
      status: "running" | "done"
      detail: string
      progress: number
    }
  | {
      type: "intent"
      seq: number
      category: string
      action: string
      summary: string
      confidence: number
    }
  | { type: "plan"; seq: number; steps: PlanStep[] }
  | {
      type: "log"
      seq: number
      text: string
      status: LogStatus
    }
  | {
      type: "step_start"
      seq: number
      id: number
      index: number
      total: number
      title: string
      progress: number
    }
  | {
      type: "step_done"
      seq: number
      id: number
      status: "done" | "failed" | "rejected" | "skipped"
      detail: string
      output?: string
    }
  | {
      type: "confirmation"
      seq: number
      task_id: string
      step: { id: number; title: string; detail: string; risk: string }
      question: string
    }
  | {
      type: "confirmation_resolved"
      seq: number
      step_id: number
      approved: boolean
    }
  | { type: "progress"; seq: number; value: number }
  | {
      type: "message"
      seq: number
      role: "nova"
      content: string
      tokens: number
      ts: string
    }
  | {
      type: "done"
      seq: number
      status: "completed" | "failed" | "rejected"
      summary: string
      duration_ms: number
    }
  | { type: "error"; seq: number; message: string }

// ── Global realtime feed (GET /api/stream) ───────────

export interface StreamEvent {
  type: "system" | "health" | "vector" | "task" | "notification" | "ping"
  seq?: number
  ts?: number
  [key: string]: unknown
}

export interface TaskStreamPayload {
  event: "started" | "confirmation" | "confirmation_resolved" | "done"
  task_id: string
  session_id: string
  user_id: number | null
  status: string
  progress: number
  phase: string
  input: string
  step_title: string
  summary: string
  approved: boolean | null
}

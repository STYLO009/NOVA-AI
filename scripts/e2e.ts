// End-to-end check: drives the real frontend API client (src/lib/api.ts)
// against the running backend, including the destructive-confirmation round trip.
//
//   bun run e2e        (backend must be up: http://127.0.0.1:8000)
//
// Creates a throwaway file in the NOVA workspace, asks NOVA to delete it,
// approves the confirmation prompt, and asserts the streamed event sequence.
// When auth is enabled (the default) it signs up a throwaway account first, so
// the run also proves that protected routes and per-user scoping work.
import { mkdirSync, writeFileSync, existsSync } from "node:fs"
import { join } from "node:path"
import { homedir } from "node:os"
import { api, setToken } from "../src/lib/api"
import type { BackendEvent } from "../src/lib/types"

const workspace = join(homedir(), "Documents", "NOVA")
const victim = join(workspace, "approval-test.txt")

const steps: string[] = []
let sawConfirm = false

function log(ev: BackendEvent) {
  steps.push(`${ev.type}(seq=${(ev as { seq?: number }).seq ?? "-"})`)
  if (ev.type === "confirmation") {
    sawConfirm = true
    console.log("  CONFIRM:", ev.step.title, "| risk:", ev.step.risk)
  }
  if (ev.type === "message")
    console.log("  REPLY:", ev.content.slice(0, 160).replace(/\n/g, " "))
  if (ev.type === "error") console.log("  ERROR:", ev.message)
}

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`assertion failed: ${message}`)
}

async function run() {
  const health = await api.health()
  console.log(
    "health:",
    health.status,
    "| llm:",
    health.llm_mode,
    "| workspace:",
    health.workspace,
  )
  assert(health.status === "ok", "backend health should be ok")

  // Sign in first when the backend requires it, then prove the gate is real.
  const authEnabled = health.auth !== false
  if (authEnabled) {
    const anon = await fetch(`${api.base}/api/history`).catch(() => null)
    assert(
      anon && anon.status === 401,
      "protected routes reject anonymous callers",
    )

    const username = `e2e_${Math.random().toString(36).slice(2, 10)}`
    const password = "e2e-password-123"
    const res = await api.register(username, password)
    setToken(res.token)
    console.log("registered:", res.user.username, "→ session active")

    const me = await api.me()
    assert(
      me.user.username === username,
      "the session resolves back to the new account",
    )
  } else {
    console.log("auth disabled (NOVA_AUTH_ENABLED=false) — skipping sign-in")
  }

  // Provision the file the task will delete (repeatable runs).
  mkdirSync(workspace, { recursive: true })
  writeFileSync(
    victim,
    "temporary file for the NOVA confirmation round trip",
    "utf8",
  )
  assert(existsSync(victim), "test file should exist before the task")

  const chat = await api.chat("delete the file approval-test.txt", "e2e-run")
  console.log("chat queued:", chat.task_id, chat.status)

  // Answer the confirmation the moment the backend asks for it.
  const answered = new Set<string>()
  await api.streamTask(chat.task_id, (ev) => {
    log(ev)
    if (ev.type === "confirmation" && !answered.has(ev.task_id)) {
      answered.add(ev.task_id)
      api
        .decide(chat.task_id, true)
        .then((r) => console.log("  DECISION:", JSON.stringify(r)))
        .catch((e) => console.log("  DECISION FAILED:", e.message))
    }
  })

  console.log("sequence:", steps.join(" -> "))

  // Assertions on the streamed contract.
  assert(steps[0]?.startsWith("started"), "stream starts with `started`")
  assert(
    steps.some((s) => s.startsWith("plan")),
    "a plan event was streamed",
  )
  assert(sawConfirm, "destructive step raised a confirmation")
  assert(
    steps.some((s) => s.startsWith("confirmation_resolved")),
    "confirmation was resolved",
  )
  assert(steps.at(-1)?.startsWith("done"), "stream ends with `done`")
  assert(!existsSync(victim), "approved delete removed the file")

  const history = await api.history(5)
  console.log(
    "history top:",
    history.tasks[0]?.status,
    "|",
    history.tasks[0]?.input,
  )
  assert(
    history.tasks[0]?.status === "completed",
    "task recorded as completed in history",
  )

  const sys = await api.system()
  console.log("system:", sys.cpu, "% cpu |", sys.ram, "% ram")

  // Long-term memory round trip: SQLite write + Qdrant (or SQL fallback) recall.
  const saved = await api.createMemory("e2e note", "the e2e run stores this")
  const recall = await api.searchMemory("e2e note", 5)
  console.log(
    "recall:",
    recall.items.length,
    "hit(s) via",
    recall.engine,
    "engine",
  )
  assert(
    recall.items.some((i) => i.key === "e2e note"),
    "semantic search finds the memory that was just stored",
  )
  assert(
    ["local", "server"].includes(recall.engine),
    "a vector engine (embedded or server Qdrant) handled the query",
  )
  await api.deleteMemory(saved.item.id)

  console.log(
    `\nE2E OK — ${steps.length} events, confirmation gate enforced and approved.`,
  )
}

run().then(
  () => process.exit(0),
  (e) => {
    console.error("E2E FAILED:", e?.message ?? e)
    process.exit(1)
  },
)

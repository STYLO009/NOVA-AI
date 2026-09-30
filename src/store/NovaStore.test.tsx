import { describe, expect, test } from "bun:test"
import type { BackendEvent } from "@/lib/types"
import { initialState, reducer, type NovaState } from "@/store/NovaStore"

/** Applies a sequence of backend events to a state, as the SSE stream would. */
function fold(events: BackendEvent[], from: NovaState): NovaState {
  return events.reduce(
    (state, event) => reducer(state, { type: "event", event }),
    from,
  )
}

const send = (content: string) =>
  reducer(initialState, { type: "send", content })

const started: BackendEvent = {
  type: "started",
  seq: 1,
  task_id: "task-1",
  input: "open notepad",
  ts: "12:00:00",
}

const plan: BackendEvent = {
  type: "plan",
  seq: 4,
  steps: [
    {
      id: 0,
      tool: "open_app",
      title: "Open Notepad",
      detail: "Launch notepad.exe",
      destructive: false,
      status: "pending",
    },
    {
      id: 1,
      tool: "write_file",
      title: "Write hello.txt",
      detail: "Create a text file",
      destructive: false,
      status: "pending",
    },
  ],
}

const completed: BackendEvent = {
  type: "done",
  seq: 12,
  status: "completed",
  summary: "Notepad is open and hello.txt was written.",
  duration_ms: 1234,
}

describe("NovaStore reducer", () => {
  test("boots neutral — nothing has happened until the user asks", () => {
    expect(initialState.messages).toHaveLength(0)
    expect(initialState.history).toHaveLength(0)
    expect(initialState.apps).toHaveLength(0)
    expect(initialState.files).toHaveLength(0)
    expect(initialState.logs).toHaveLength(0)
    expect(initialState.progress).toBe(0)
    expect(initialState.taskStatus).toBe("idle")
    expect(initialState.activeTaskId).toBeNull()
    expect(initialState.confirm).toBeNull()
    expect(initialState.alerts).toBe(0)
    expect(initialState.connecting).toBe(true)
    expect(initialState.modelTag).toBe("—")
    // Pipeline placeholders are all pending — nothing pretends to be running.
    expect(initialState.pipeline.every((s) => s.status === "pending")).toBe(
      true,
    )
  })

  test("runs a full task through Understand → Plan → Execute → Verify", () => {
    // The app probes /api/health on boot, so the session starts connected.
    const online = reducer(send("open notepad"), {
      type: "health/set",
      health: {
        status: "ok",
        version: "1.0.0",
        llm_mode: "fallback",
        model: "meta-llama/Llama-3.1-8B-Instruct",
        features: {},
      },
      online: true,
    })

    const state = fold(
      [
        started,
        {
          type: "phase",
          seq: 2,
          phase: "understand",
          status: "running",
          detail: "",
          progress: 10,
        },
        {
          type: "intent",
          seq: 3,
          category: "application",
          action: "open",
          summary: "Open Notepad",
          confidence: 0.92,
        },
        {
          type: "phase",
          seq: 5,
          phase: "plan",
          status: "done",
          detail: "2 actions queued",
          progress: 30,
        },
        plan,
        {
          type: "step_start",
          seq: 6,
          id: 0,
          index: 1,
          total: 2,
          title: "Open Notepad",
          progress: 45,
        },
        {
          type: "step_done",
          seq: 7,
          id: 0,
          status: "done",
          detail: "launched",
        },
        {
          type: "step_start",
          seq: 8,
          id: 1,
          index: 2,
          total: 2,
          title: "Write hello.txt",
          progress: 70,
        },
        { type: "step_done", seq: 9, id: 1, status: "done", detail: "created" },
        {
          type: "phase",
          seq: 10,
          phase: "execute",
          status: "done",
          detail: "2/2 ok",
          progress: 90,
        },
        {
          type: "phase",
          seq: 11,
          phase: "verify",
          status: "done",
          detail: "All checks passed",
          progress: 95,
        },
        {
          type: "message",
          seq: 12,
          role: "nova",
          content: "Working on it…",
          tokens: 42,
          ts: "12:00:01",
        },
        completed,
      ],
      online,
    )

    expect(state.taskStatus).toBe("completed")
    expect(state.progress).toBe(100)
    expect(state.running).toBe(0)
    expect(state.online).toBe(true)
    expect(state.planSteps).toHaveLength(2)

    // The whole pipeline (Understand, Plan, Execute, Verify) is done.
    expect(state.pipeline.map((s) => s.status)).toEqual([
      "done",
      "done",
      "done",
      "done",
    ])

    // Plan rows were mirrored into the log with their recorded indexes.
    const planLogIndex = state.stepLogIndex[0]
    expect(planLogIndex).toBeGreaterThan(0)
    expect(state.logs[planLogIndex]?.text).toBe("Open Notepad")

    // The detailed `message` reply wins: `done.summary` only applies while
    // still streaming (the backend sends message → done).
    const last = state.messages.at(-1)
    expect(last?.role).toBe("nova")
    expect(last?.content).toBe("Working on it…")
    expect(last?.active).toBe(false)

    // Context token counter includes the backend message.
    expect(state.contextTokens).toBeGreaterThan(0)
  })

  test("blocks on a destructive confirmation and resumes on approve", () => {
    const pending = fold(
      [
        started,
        {
          type: "phase",
          seq: 2,
          phase: "execute",
          status: "running",
          detail: "",
          progress: 50,
        },
        {
          type: "confirmation",
          seq: 3,
          task_id: "task-1",
          step: {
            id: 0,
            title: "Delete report.pdf",
            detail: "C:\\tmp\\report.pdf",
            risk: "high",
          },
          question: "Delete report.pdf permanently?",
        },
      ],
      send("delete report.pdf"),
    )

    // The task stays blocked until the user decides.
    expect(pending.taskStatus).toBe("awaiting_confirmation")
    expect(pending.confirm).not.toBeNull()
    expect(pending.confirm?.title).toBe("Delete report.pdf")
    expect(pending.confirm?.risk).toBe("high")
    expect(pending.confirm?.taskId).toBe("task-1")

    const resumed = fold(
      [
        { type: "confirmation_resolved", seq: 4, step_id: 0, approved: true },
        { type: "step_done", seq: 5, id: 0, status: "done", detail: "deleted" },
        {
          type: "phase",
          seq: 6,
          phase: "verify",
          status: "done",
          detail: "All checks passed",
          progress: 95,
        },
        completed,
      ],
      pending,
    )

    expect(resumed.confirm).toBeNull()
    expect(resumed.taskStatus).toBe("completed")
    // The "Awaiting confirmation" log row was closed as approved.
    const confirmationLog = resumed.logs.find((l) =>
      l.text.startsWith("Awaiting confirmation"),
    )
    expect(confirmationLog?.status).toBe("done")
    expect(confirmationLog?.text).toContain("approved")
  })

  test("declining a confirmation ends the task as rejected", () => {
    const pending = fold(
      [
        started,
        {
          type: "confirmation",
          seq: 2,
          task_id: "task-1",
          step: {
            id: 0,
            title: "Delete folder",
            detail: "C:\\tmp\\old",
            risk: "high",
          },
          question: "Delete folder?",
        },
      ],
      send("delete old folder"),
    )

    const declined = fold(
      [
        { type: "confirmation_resolved", seq: 3, step_id: 0, approved: false },
        {
          type: "step_done",
          seq: 4,
          id: 0,
          status: "rejected",
          detail: "declined by user",
        },
        {
          type: "done",
          seq: 5,
          status: "rejected",
          summary: "Stopped — the destructive action was declined.",
          duration_ms: 900,
        },
      ],
      pending,
    )

    expect(declined.confirm).toBeNull()
    expect(declined.taskStatus).toBe("rejected")
    expect(declined.alerts).toBeGreaterThan(initialState.alerts)
    expect(declined.running).toBe(0)
    const confirmationLog = declined.logs.find((l) =>
      l.text.startsWith("Awaiting confirmation"),
    )
    expect(confirmationLog?.text).toContain("declined")
  })

  test("offline dispatch marks the agent offline with an error reply", () => {
    const offline = reducer(send("open notepad"), {
      type: "offline",
      message: "connect ECONNREFUSED",
    })

    expect(offline.online).toBe(false)
    expect(offline.taskStatus).toBe("failed")
    expect(offline.alerts).toBe(initialState.alerts + 1)
    expect(offline.confirm).toBeNull()

    const last = offline.messages.at(-1)
    expect(last?.error).toBe(true)
    expect(last?.content).toContain("Backend offline")
    expect(last?.content).toContain("connect ECONNREFUSED")
    expect(last?.active).toBe(false)

    // Recovery via health/set.
    const recovered = reducer(offline, {
      type: "health/set",
      health: {
        status: "ok",
        version: "1.0.0",
        llm_mode: "fallback",
        model: "meta-llama/Llama-3.1-8B-Instruct",
        features: {},
      },
      online: true,
    })
    expect(recovered.online).toBe(true)
    expect(recovered.modelTag).toBe("Llama-3.1-8B-Instruct")
  })

  test("sending a new command resets task-specific UI and appends the message", () => {
    // Simulate a busy mid-task state first.
    const busy = fold(
      [
        started,
        {
          type: "phase",
          seq: 2,
          phase: "execute",
          status: "running",
          detail: "",
          progress: 60,
        },
        { type: "progress", seq: 3, value: 60 },
      ],
      send("first command"),
    )
    expect(busy.taskStatus).toBe("running")
    expect(busy.progress).toBeGreaterThan(0)

    const fresh = reducer(busy, { type: "send", content: "open notepad" })

    expect(fresh.taskStatus).toBe("queued")
    expect(fresh.progress).toBe(0)
    expect(fresh.confirm).toBeNull()
    expect(fresh.streamId).not.toBeNull()

    // The new user + assistant messages were appended after the old ones.
    expect(fresh.messages.at(-2)).toMatchObject({
      role: "user",
      content: "open notepad",
    })
    expect(fresh.messages.at(-1)).toMatchObject({ role: "nova", active: true })
    expect(fresh.messages.length).toBe(busy.messages.length + 2)

    // Seeded "streaming" chat bubbles are frozen after the first real command.
    expect(
      fresh.messages.some((m) => m.active && m.id !== fresh.streamId),
    ).toBe(false)
  })
})

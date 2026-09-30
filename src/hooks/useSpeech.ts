// ─────────────────────────────────────────────────────
// NOVA — voice input (Web Speech API) + voice output (speechSynthesis)
// Falls back gracefully when the browser has no speech support.
// ─────────────────────────────────────────────────────

import { useCallback, useEffect, useRef, useState } from "react"

/* eslint-disable @typescript-eslint/no-explicit-any */

type RecognitionCtor = new () => any

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null
  const w = window as any
  return (w.SpeechRecognition ||
    w.webkitSpeechRecognition ||
    null) as RecognitionCtor | null
}

export interface SpeechApi {
  supported: boolean
  listening: boolean
  interim: string
  start: () => void
  stop: () => void
  toggle: () => void
}

/**
 * Single-utterance voice command capture. The final transcript is handed to
 * `onFinal` (which dispatches the command to the backend) and listening stops.
 */
export function useSpeech(onFinal: (text: string) => void): SpeechApi {
  const [supported] = useState<boolean>(() => !!recognitionCtor())
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState("")
  const recRef = useRef<any>(null)
  const finalRef = useRef(onFinal)
  finalRef.current = onFinal

  const stop = useCallback(() => {
    try {
      recRef.current?.stop()
    } catch {
      /* already stopped */
    }
    setListening(false)
    setInterim("")
  }, [])

  const start = useCallback(() => {
    const Ctor = recognitionCtor()
    if (!Ctor) return
    if (!recRef.current) {
      const rec = new Ctor()
      rec.lang = "en-US"
      rec.continuous = false
      rec.interimResults = true
      rec.maxAlternatives = 1

      rec.onresult = (event: any) => {
        let live = ""
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i]
          const transcript: string = result[0]?.transcript ?? ""
          if (result.isFinal) {
            const text = transcript.trim()
            setInterim("")
            setListening(false)
            if (text) finalRef.current(text)
          } else {
            live += transcript
          }
        }
        if (live) setInterim(live)
      }
      rec.onerror = () => {
        setListening(false)
        setInterim("")
      }
      rec.onend = () => {
        setListening(false)
        setInterim("")
      }
      recRef.current = rec
    }
    try {
      recRef.current.start()
      setListening(true)
    } catch {
      setListening(false)
    }
  }, [])

  const toggle = useCallback(() => {
    if (listening) stop()
    else start()
  }, [listening, start, stop])

  useEffect(() => {
    return () => {
      try {
        recRef.current?.abort?.()
      } catch {
        /* noop */
      }
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel()
      }
    }
  }, [])

  return { supported, listening, interim, start, stop, toggle }
}

/** Reads NOVA's reply aloud (used while the Voice Mode overlay is open). */
export function speak(text: string): void {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return
  try {
    window.speechSynthesis.cancel()
    const clean = text
      .replace(/\*\*/g, "")
      .replace(/[→✓▶·]/g, ".")
      .replace(/\s+/g, " ")
      .trim()
    if (!clean) return
    const utterance = new SpeechSynthesisUtterance(clean)
    utterance.lang = "en-US"
    utterance.rate = 1.05
    utterance.pitch = 1
    window.speechSynthesis.speak(utterance)
  } catch {
    /* TTS unavailable */
  }
}

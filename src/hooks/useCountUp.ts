import { useEffect, useRef, useState } from "react"

/**
 * Animates a number toward its latest value with an ease-out curve, so
 * stats tally up instead of jumping. Returns null until the first value
 * arrives; reduced-motion users get the raw value immediately.
 */
export function useCountUp(target: number | null, duration = 800) {
  const [value, setValue] = useState(target)
  const currentRef = useRef(target)

  useEffect(() => {
    if (target == null) {
      setValue(null)
      currentRef.current = null
      return
    }

    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const from = currentRef.current ?? 0

    if (reduced || Math.abs(target - from) < 0.5) {
      currentRef.current = target
      setValue(target)
      return
    }

    let raf = 0
    const start = performance.now()

    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      const next = from + (target - from) * eased
      currentRef.current = next
      setValue(next)
      if (t < 1) raf = requestAnimationFrame(tick)
      else currentRef.current = target
    }

    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])

  return value
}

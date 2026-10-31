import { useEffect, useMemo, useRef, useState } from 'react'

const DEFAULT_DURATION_SEC = 30 * 60
const CRITICAL_THRESHOLD_SEC = 5 * 60

function formatCountdown(remainingSec: number) {
  const minutes = Math.floor(remainingSec / 60)
  const seconds = remainingSec % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export function useApprovalCountdown({
  active,
  durationSec = DEFAULT_DURATION_SEC,
  onElapsed,
}: {
  active: boolean
  durationSec?: number
  onElapsed?: () => void
}) {
  const [deadlineAt, setDeadlineAt] = useState<number | null>(null)
  const [remainingSec, setRemainingSec] = useState(durationSec)
  const elapsedRef = useRef(false)

  useEffect(() => {
    if (!active) {
      elapsedRef.current = false
      setDeadlineAt(null)
      setRemainingSec(durationSec)
      return
    }

    setDeadlineAt(Date.now() + durationSec * 1000)
    setRemainingSec(durationSec)
  }, [active, durationSec])

  useEffect(() => {
    if (!active || deadlineAt == null) {
      return
    }

    const tick = () => {
      const nextRemaining = Math.max(0, Math.ceil((deadlineAt - Date.now()) / 1000))
      setRemainingSec(nextRemaining)

      if (nextRemaining === 0 && !elapsedRef.current) {
        elapsedRef.current = true
        onElapsed?.()
      }
    }

    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [active, deadlineAt, onElapsed])

  const progressPct = useMemo(() => {
    if (durationSec <= 0) {
      return 0
    }
    return Math.max(0, Math.min(100, (remainingSec / durationSec) * 100))
  }, [durationSec, remainingSec])

  return {
    remainingSec,
    formatted: formatCountdown(remainingSec),
    progressPct,
    isCritical: remainingSec > 0 && remainingSec < CRITICAL_THRESHOLD_SEC,
  }
}

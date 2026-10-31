import { useEffect, useRef } from 'react'
import { useSystemStore } from '../stores/systemStore'
import { useUIStore } from '../stores/uiStore'

const LOW_PERF_VRAM_THRESHOLD_MB = 14 * 1024
const LOW_PERF_SUSTAIN_MS = 4000
const LOW_PERF_CPU_THRESHOLD = 6

function readHardwareConcurrency() {
  if (typeof navigator === 'undefined') {
    return null
  }

  return typeof navigator.hardwareConcurrency === 'number'
    ? navigator.hardwareConcurrency
    : null
}

export function useLowPerf() {
  const vramUsedMB = useSystemStore((state) => state.vramUsedMB)
  const setLowPerf = useUIStore((state) => state.setLowPerf)
  const highVramSinceRef = useRef<number | null>(null)

  useEffect(() => {
    const cpuLow =
      (readHardwareConcurrency() ?? Number.POSITIVE_INFINITY) < LOW_PERF_CPU_THRESHOLD
    const highVram = vramUsedMB > LOW_PERF_VRAM_THRESHOLD_MB

    if (!highVram) {
      highVramSinceRef.current = null
      setLowPerf(cpuLow)
      return
    }

    const now = Date.now()
    if (highVramSinceRef.current == null) {
      highVramSinceRef.current = now
    }

    const elapsedMs = now - highVramSinceRef.current
    if (cpuLow || elapsedMs >= LOW_PERF_SUSTAIN_MS) {
      setLowPerf(true)
      return
    }

    setLowPerf(cpuLow)

    const timeoutMs = LOW_PERF_SUSTAIN_MS - elapsedMs
    const timer = window.setTimeout(() => {
      const stillHigh = useSystemStore.getState().vramUsedMB > LOW_PERF_VRAM_THRESHOLD_MB
      const cpuStillLow =
        (readHardwareConcurrency() ?? Number.POSITIVE_INFINITY) < LOW_PERF_CPU_THRESHOLD
      useUIStore.getState().setLowPerf(cpuStillLow || stillHigh)
    }, timeoutMs)

    return () => window.clearTimeout(timer)
  }, [setLowPerf, vramUsedMB])
}

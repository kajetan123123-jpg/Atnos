import { useEffect, useRef } from 'react'
import { fetchHealth } from '../lib/api'
import { useSystemStore } from '../stores/systemStore'

const HEALTH_POLL_MS = 10_000

function readBrowserOnline() {
  if (typeof navigator === 'undefined') {
    return true
  }
  return navigator.onLine !== false
}

export function useNetworkStatus() {
  const latestBrowserOnlineRef = useRef(readBrowserOnline())

  useEffect(() => {
    let active = true
    let controller: AbortController | null = null

    const setBrowserSignal = () => {
      latestBrowserOnlineRef.current = readBrowserOnline()
      useSystemStore.getState().setNetworkSignals({
        browserOnline: latestBrowserOnlineRef.current,
      })
    }

    const pingHealth = async () => {
      controller?.abort()
      const requestController = new AbortController()
      controller = requestController
      const startedAt = performance.now()
      try {
        const payload = await fetchHealth({ signal: requestController.signal })
        if (!active || requestController.signal.aborted || controller !== requestController) {
          return
        }
        useSystemStore.getState().setNetworkSignals({
          browserOnline: latestBrowserOnlineRef.current,
          backendReachable: payload.status === 'ok',
          rttMs: Math.max(0, Math.round(performance.now() - startedAt)),
        })
      } catch {
        if (!active || requestController.signal.aborted || controller !== requestController) {
          return
        }
        useSystemStore.getState().setNetworkSignals({
          browserOnline: latestBrowserOnlineRef.current,
          backendReachable: false,
          rttMs: null,
        })
      }
    }

    setBrowserSignal()
    void pingHealth()

    window.addEventListener('online', setBrowserSignal)
    window.addEventListener('offline', setBrowserSignal)
    const intervalId = window.setInterval(() => {
      void pingHealth()
    }, HEALTH_POLL_MS)

    return () => {
      active = false
      controller?.abort()
      window.removeEventListener('online', setBrowserSignal)
      window.removeEventListener('offline', setBrowserSignal)
      window.clearInterval(intervalId)
    }
  }, [])
}

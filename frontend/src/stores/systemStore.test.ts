import { beforeEach, describe, expect, it } from 'vitest'
import { resetAllStores } from '../test/resetStores'
import { useSystemStore } from './systemStore'

describe('useSystemStore', () => {
  beforeEach(() => {
    resetAllStores()
  })

  it('starts with the canonical StatusHUD defaults', () => {
    const state = useSystemStore.getState()

    expect(state.agentStage).toBe('idle')
    expect(state.vramUsedMB).toBe(0)
    expect(state.vramTotalMB).toBe(16384)
    expect(state.model).toEqual({ name: 'Nieustalony model', provider: 'ollama' })
    expect(state.backendCheckedAt).toBeNull()
    expect(state.chroma).toBe('down')
    expect(state.network).toBe('offline')
    expect(state.rttMs).toBeNull()
  })

  it('keeps network offline only when browser and backend signals are both false', () => {
    useSystemStore.getState().setNetworkSignals({
      browserOnline: false,
      backendReachable: true,
      rttMs: 18,
    })

    expect(useSystemStore.getState().network).toBe('online')
    expect(useSystemStore.getState().rttMs).toBe(18)

    useSystemStore.getState().setNetworkSignals({
      browserOnline: true,
      backendReachable: false,
      rttMs: null,
    })

    expect(useSystemStore.getState().network).toBe('degraded')
    expect(useSystemStore.getState().rttMs).toBeNull()

    useSystemStore.getState().setNetworkSignals({
      browserOnline: false,
      backendReachable: false,
      rttMs: null,
    })

    expect(useSystemStore.getState().network).toBe('offline')
  })

  it('does not mistake the browser online signal for a completed backend health check', () => {
    useSystemStore.getState().setNetworkSignals({ browserOnline: true })
    expect(useSystemStore.getState().backendCheckedAt).toBeNull()
    useSystemStore.getState().setNetworkSignals({ backendReachable: false })
    expect(useSystemStore.getState().backendCheckedAt).toEqual(expect.any(Number))
    expect(useSystemStore.getState().networkSignals.backendReachable).toBe(false)
  })
})

import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLowPerf } from './useLowPerf'
import { useSystemStore } from '../stores/systemStore'
import { useUIStore } from '../stores/uiStore'
import { resetAllStores } from '../test/resetStores'

function Harness() {
  useLowPerf()
  return null
}

describe('useLowPerf', () => {
  beforeEach(() => {
    resetAllStores()
    vi.useFakeTimers()
    Object.defineProperty(window.navigator, 'hardwareConcurrency', {
      configurable: true,
      value: 8,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('enables lowPerf immediately on low CPU concurrency', () => {
    Object.defineProperty(window.navigator, 'hardwareConcurrency', {
      configurable: true,
      value: 4,
    })

    render(<Harness />)

    expect(useUIStore.getState().lowPerf).toBe(true)
  })

  it('requires sustained high VRAM before enabling lowPerf', () => {
    render(<Harness />)

    act(() => {
      useSystemStore.getState().setMetrics({ vramUsedMB: 14500 })
    })

    act(() => {
      vi.advanceTimersByTime(3999)
    })
    expect(useUIStore.getState().lowPerf).toBe(false)

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(useUIStore.getState().lowPerf).toBe(true)
  })

  it('drops lowPerf after VRAM recovers on capable hardware', () => {
    render(<Harness />)

    act(() => {
      useSystemStore.getState().setMetrics({ vramUsedMB: 14500 })
    })
    act(() => {
      vi.advanceTimersByTime(4000)
    })
    expect(useUIStore.getState().lowPerf).toBe(true)

    act(() => {
      useSystemStore.getState().setMetrics({ vramUsedMB: 4096 })
    })
    expect(useUIStore.getState().lowPerf).toBe(false)
  })
})

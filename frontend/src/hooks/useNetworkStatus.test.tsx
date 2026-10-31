import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSystemStore } from '../stores/systemStore'
import { resetAllStores } from '../test/resetStores'
import { useNetworkStatus } from './useNetworkStatus'

function Harness() {
  useNetworkStatus()
  return null
}

function setNavigatorOnline(value: boolean) {
  Object.defineProperty(window.navigator, 'onLine', {
    configurable: true,
    value,
  })
}

describe('useNetworkStatus', () => {
  beforeEach(() => {
    resetAllStores()
    setNavigatorOnline(true)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('keeps NETWORK online when backend health responds even if navigator is offline', async () => {
    setNavigatorOnline(false)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 })),
    )

    render(<Harness />)

    await waitFor(() => {
      expect(useSystemStore.getState().network).toBe('online')
    })
    expect(useSystemStore.getState().networkSignals).toMatchObject({
      browserOnline: false,
      backendReachable: true,
    })
  })

  it('shows offline only when browser and backend are both unavailable', async () => {
    setNavigatorOnline(false)
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('failed to fetch')
    }))

    render(<Harness />)

    window.dispatchEvent(new Event('offline'))
    await waitFor(() => {
      expect(useSystemStore.getState().network).toBe('offline')
    })
  })

  it('polls health every 10 seconds and cleans up interval/listeners on unmount', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const addEventListenerSpy = vi.spyOn(window, 'addEventListener')
    const removeEventListenerSpy = vi.spyOn(window, 'removeEventListener')

    const { unmount } = render(<Harness />)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    unmount()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(addEventListenerSpy).toHaveBeenCalledWith('online', expect.any(Function))
    expect(addEventListenerSpy).toHaveBeenCalledWith('offline', expect.any(Function))
    expect(removeEventListenerSpy).toHaveBeenCalledWith('online', expect.any(Function))
    expect(removeEventListenerSpy).toHaveBeenCalledWith('offline', expect.any(Function))
  })

  it('does not mark a healthy backend offline when an older aborted poll rejects', async () => {
    vi.useFakeTimers()
    useSystemStore.getState().setNetworkSignals({ backendReachable: true })
    let rejectFirst!: (error: Error) => void
    vi.stubGlobal('fetch', vi.fn()
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectFirst = reject }))
      .mockImplementation(() => new Promise(() => undefined)))
    const { unmount } = render(<Harness />)
    await vi.advanceTimersByTimeAsync(10_000)
    await act(async () => { rejectFirst(new DOMException('Aborted', 'AbortError')) })
    expect(useSystemStore.getState().networkSignals.backendReachable).toBe(true)
    unmount()
  })

  it('ignores a stale success after the current health poll failed', async () => {
    vi.useFakeTimers()
    let resolveFirst!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn()
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
      .mockRejectedValue(new TypeError('current backend unavailable')))
    const { unmount } = render(<Harness />)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(useSystemStore.getState().networkSignals.backendReachable).toBe(false)
    await act(async () => { resolveFirst(new Response(JSON.stringify({ status: 'ok' }), { status: 200 })) })
    expect(useSystemStore.getState().networkSignals.backendReachable).toBe(false)
    unmount()
  })
})

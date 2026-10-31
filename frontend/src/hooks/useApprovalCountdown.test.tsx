import { renderHook } from '@testing-library/react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useApprovalCountdown } from './useApprovalCountdown'

describe('useApprovalCountdown', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-15T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('counts down and reports progress', () => {
    const onElapsed = vi.fn()
    const { result } = renderHook(() =>
      useApprovalCountdown({ active: true, durationSec: 10, onElapsed }),
    )

    expect(result.current.formatted).toBe('00:10')
    expect(result.current.progressPct).toBe(100)

    act(() => {
      vi.advanceTimersByTime(5000)
    })

    expect(result.current.formatted).toBe('00:05')
    expect(result.current.progressPct).toBe(50)
    expect(onElapsed).not.toHaveBeenCalled()

    act(() => {
      vi.advanceTimersByTime(5000)
    })

    expect(result.current.formatted).toBe('00:00')
    expect(result.current.progressPct).toBe(0)
    expect(onElapsed).toHaveBeenCalledTimes(1)
  })
})

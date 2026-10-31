import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('local-host API capability header', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.stubGlobal('fetch', vi.fn())
    window.sessionStorage.clear()
    const expiresAt = Math.floor(Date.now() / 1000) + 3600
    window.history.replaceState(
      {},
      '',
      `/#atnos_local_capability=api-test-token&atnos_local_capability_expires=${expiresAt}`,
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.resetModules()
    window.sessionStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  it('passes the launcher capability to desktop input requests', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        action_kind: 'mouse_click',
        command: 'click',
        exit_code: 0,
        stdout: 'ok',
        stderr: '',
        workdir: 'E:/Atnos/App',
        elevated: false,
        started_at: '2026-07-26T00:00:00Z',
        finished_at: '2026-07-26T00:00:01Z',
      }),
      text: async () => '',
    } as Response)
    const { dispatchLocalHostInput } = await import('./api')

    await dispatchLocalHostInput({ action: 'click_mouse' })

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/local-host/input'),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'X-Atnos-Local-Capability': 'api-test-token',
        }),
      }),
    )
  })
})

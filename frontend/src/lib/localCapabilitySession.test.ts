import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const TOKEN = 'frontend-focused-capability'

async function importSessionModule(expiresAt: number, token = TOKEN) {
  vi.resetModules()
  window.history.replaceState(
    {},
    '',
    `/#atnos_local_capability=${encodeURIComponent(
      token,
    )}&atnos_local_capability_expires=${expiresAt}`,
  )
  return import('./localCapabilitySession')
}

describe('local capability session', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  afterEach(() => {
    vi.resetModules()
    window.sessionStorage.clear()
    window.history.replaceState({}, '', '/')
  })

  it('consumes the launcher fragment and exposes only the request header', async () => {
    const expiresAt = Math.floor(Date.now() / 1000) + 3600
    const session = await importSessionModule(expiresAt)

    expect(session.localCapabilityHeaders()).toEqual({
      'X-Atnos-Local-Capability': TOKEN,
    })
    expect(window.location.hash).toBe('')
    expect(window.location.href).not.toContain(TOKEN)
  })

  it('rejects an expired launcher capability', async () => {
    const session = await importSessionModule(Math.floor(Date.now() / 1000) - 1)

    expect(session.localCapabilityHeaders()).toEqual({})
    expect(window.sessionStorage.length).toBe(0)
    expect(window.location.hash).toBe('')
  })

  it('keeps a valid capability only for the current browser session', async () => {
    const expiresAt = Math.floor(Date.now() / 1000) + 3600
    const initial = await importSessionModule(expiresAt)
    expect(initial.hasLocalCapabilitySession()).toBe(true)

    vi.resetModules()
    window.history.replaceState({}, '', '/')
    const reloaded = await import('./localCapabilitySession')

    expect(reloaded.localCapabilityHeaders()).toEqual({
      'X-Atnos-Local-Capability': TOKEN,
    })
    expect(window.localStorage.length).toBe(0)
  })
})

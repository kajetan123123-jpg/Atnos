const SESSION_STORAGE_KEY = 'atnos.localCapabilitySession.v1'
const TOKEN_FRAGMENT_KEY = 'atnos_local_capability'
const EXPIRY_FRAGMENT_KEY = 'atnos_local_capability_expires'
export const LOCAL_CAPABILITY_HEADER = 'X-Atnos-Local-Capability'

interface LocalCapabilitySession {
  token: string
  expiresAt: number
}

let cachedSession: LocalCapabilitySession | null = bootstrapSession()

export function localCapabilityHeaders(): Record<string, string> {
  if (!cachedSession || cachedSession.expiresAt <= nowSeconds()) {
    clearLocalCapabilitySession()
    return {}
  }
  return { [LOCAL_CAPABILITY_HEADER]: cachedSession.token }
}

export function hasLocalCapabilitySession(): boolean {
  return Object.keys(localCapabilityHeaders()).length > 0
}

export function clearLocalCapabilitySession(): void {
  cachedSession = null
  if (typeof window !== 'undefined') {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY)
  }
}

function bootstrapSession(): LocalCapabilitySession | null {
  if (typeof window === 'undefined') return null

  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const token = fragment.get(TOKEN_FRAGMENT_KEY)?.trim() ?? ''
  const expiresAt = Number(fragment.get(EXPIRY_FRAGMENT_KEY) ?? 0)
  if (token && Number.isFinite(expiresAt) && expiresAt > nowSeconds()) {
    const session = { token, expiresAt }
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
    removeCapabilityFragment(fragment)
    return session
  }

  removeCapabilityFragment(fragment)
  try {
    const stored = JSON.parse(
      window.sessionStorage.getItem(SESSION_STORAGE_KEY) ?? 'null',
    ) as Partial<LocalCapabilitySession> | null
    if (
      stored &&
      typeof stored.token === 'string' &&
      stored.token.length > 0 &&
      typeof stored.expiresAt === 'number' &&
      stored.expiresAt > nowSeconds()
    ) {
      return { token: stored.token, expiresAt: stored.expiresAt }
    }
  } catch {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY)
  }
  return null
}

function removeCapabilityFragment(fragment: URLSearchParams): void {
  if (typeof window === 'undefined') return
  const hadToken = fragment.has(TOKEN_FRAGMENT_KEY) || fragment.has(EXPIRY_FRAGMENT_KEY)
  if (!hadToken) return
  fragment.delete(TOKEN_FRAGMENT_KEY)
  fragment.delete(EXPIRY_FRAGMENT_KEY)
  const remaining = fragment.toString()
  const cleanUrl = `${window.location.pathname}${window.location.search}${
    remaining ? `#${remaining}` : ''
  }`
  window.history.replaceState(window.history.state, '', cleanUrl)
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000)
}

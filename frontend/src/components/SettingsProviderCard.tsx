import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Eye, EyeOff } from 'lucide-react'
import { formatDateTime } from '../lib/format'
import { EASE } from '../lib/motion'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { PROVIDER_MAP } from '../lib/providers'
import type { ConnectionState, ProviderCardTestResult } from '../types/system'

export interface SettingsProviderCardProps {
  provider: 'openai' | 'anthropic' | 'ollama' | 'gemini'
  state: ConnectionState
  apiKey?: string
  baseUrl?: string
  lastSync: number | null
  onSave: (next: { apiKey?: string; baseUrl?: string }) => Promise<void>
  onTest: (draft?: { apiKey?: string; baseUrl?: string }) => Promise<ProviderCardTestResult>
}

function maskSecret(value: string | undefined) {
  if (!value) {
    return ''
  }

  const trimmed = value.trim()
  if (!trimmed) {
    return ''
  }
  if (trimmed.includes('*')) {
    return trimmed
  }
  if (trimmed.length <= 4) {
    return trimmed
  }

  const suffix = trimmed.slice(-4)
  const prefix = trimmed.startsWith('sk-') ? 'sk-' : ''
  return `${prefix}${'*'.repeat(Math.max(trimmed.length - suffix.length - prefix.length, 8))}${suffix}`
}

function normalizeConnectionState(result: ProviderCardTestResult): ConnectionState {
  if (result.connectionState === 'connected') {
    return { kind: 'connected' }
  }
  if (result.connectionState === 'rate_limited') {
    return { kind: 'rate_limited', resetInSec: result.resetInSec }
  }
  return {
    kind: 'error',
    message: result.message || result.detail || 'Test połączenia nie powiódł się.',
  }
}

function buildStatusCopy(state: ConnectionState, provider: SettingsProviderCardProps['provider']) {
  switch (state.kind) {
    case 'connected':
      return { label: 'ready', detail: 'Połączony', tone: 'success' as const }
    case 'configured':
      return {
        label: 'configured',
        detail: state.message || 'Konfiguracja zapisana. Uruchom test connection, aby potwierdzic runtime.',
        tone: 'warning' as const,
      }
    case 'testing':
      return { label: 'testing', detail: 'Testowanie…', tone: 'warning' as const }
    case 'error':
      return { label: 'unhealthy', detail: state.message, tone: 'error' as const }
    case 'rate_limited':
      return {
        label: 'rate limited',
        detail:
          state.resetInSec != null
            ? `Limit zapytań, czekam ${state.resetInSec}s`
            : 'Limit zapytań, czekam',
        tone: 'warning' as const,
      }
    default:
      return {
        label: provider === 'ollama' ? 'disabled' : 'missing key',
        detail:
          provider === 'ollama'
            ? 'Lokalny runtime nie jest gotowy albo provider jest wyłączony.'
            : 'Dodaj klucz API i uruchom test połączenia.',
        tone: 'muted' as const,
      }
  }
}

export function SettingsProviderCard({
  provider,
  state,
  apiKey,
  baseUrl,
  lastSync,
  onSave,
  onTest,
}: SettingsProviderCardProps) {
  const reducedMotion = useReducedMotionStrict()
  const meta = PROVIDER_MAP[provider]
  const [draftApiKey, setDraftApiKey] = useState('')
  const [draftBaseUrl, setDraftBaseUrl] = useState(baseUrl ?? meta.defaultBaseUrl ?? '')
  const [revealed, setRevealed] = useState(false)
  const [localState, setLocalState] = useState<ConnectionState>(state)
  const [localLastSync, setLocalLastSync] = useState<number | null>(lastSync)
  const [countdown, setCountdown] = useState<number | null>(
    state.kind === 'rate_limited' ? state.resetInSec : null,
  )
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    setLocalState(state)
  }, [state])

  useEffect(() => {
    setDraftBaseUrl(baseUrl ?? meta.defaultBaseUrl ?? '')
  }, [baseUrl, meta.defaultBaseUrl])

  useEffect(() => {
    setLocalLastSync(lastSync)
  }, [lastSync])

  useEffect(() => {
    if (state.kind === 'rate_limited') {
      setCountdown(state.resetInSec)
      return
    }
    setCountdown(null)
  }, [state])

  useEffect(() => {
    if (localState.kind !== 'rate_limited' || countdown == null || countdown <= 0) {
      return
    }

    const timer = window.setTimeout(() => {
      setCountdown((current) => (current == null ? null : Math.max(current - 1, 0)))
    }, 1000)

    return () => window.clearTimeout(timer)
  }, [countdown, localState])

  const status = useMemo(
    () =>
      localState.kind === 'rate_limited'
        ? buildStatusCopy({ kind: 'rate_limited', resetInSec: countdown }, provider)
        : buildStatusCopy(localState, provider),
    [countdown, localState, provider],
  )
  const maskedSecret = useMemo(() => maskSecret(apiKey), [apiKey])
  const Logo = meta.Logo
  const testEnabled =
    provider === 'ollama' ? true : Boolean(maskedSecret || draftApiKey.trim().length >= 16)
  const statusDotClass =
    status.tone === 'success'
      ? 'bg-sem-success'
      : status.tone === 'error'
        ? 'bg-sem-error'
        : status.tone === 'warning'
          ? 'bg-sem-warning'
          : 'bg-text-tertiary'

  const handleSave = async () => {
    setSaving(true)
    try {
      await onSave({
        apiKey: provider === 'ollama' ? undefined : draftApiKey.trim() || undefined,
        baseUrl: draftBaseUrl.trim() || undefined,
      })
      setDraftApiKey('')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setLocalState({ kind: 'testing' })

    try {
      const [result] = await Promise.all([
        onTest({
          apiKey: provider === 'ollama' ? undefined : draftApiKey.trim() || undefined,
          baseUrl: draftBaseUrl.trim() || undefined,
        }),
        new Promise((resolve) => window.setTimeout(resolve, 600)),
      ])

      setLocalState(normalizeConnectionState(result))
      setLocalLastSync(result.testedAt ? Date.parse(result.testedAt) : null)
      setCountdown(result.resetInSec)
    } catch (error) {
      setLocalState({
        kind: 'error',
        message: error instanceof Error ? error.message : 'Test połączenia nie powiódł się.',
      })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="w-full rounded-lg border border-border-default bg-panel p-4 md:w-[320px]">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3 text-text-primary">
          <Logo className="h-6 w-6 shrink-0" />
          <div>
            <h3 className="type-h3 text-text-primary">{meta.displayName}</h3>
            <div className="mt-1 text-xs text-text-tertiary">
              {provider === 'ollama' ? 'Local runtime' : 'Cloud API provider'}
            </div>
          </div>
        </div>
        <motion.div
          animate={
            reducedMotion
              ? undefined
              : localState.kind === 'connected'
                ? { scale: [1, 1.4, 1] }
                : localState.kind === 'error'
                  ? { x: [0, -2, 2, -2, 2, -2, 0] }
                  : undefined
          }
          transition={
            reducedMotion
              ? undefined
              : localState.kind === 'connected'
                ? { duration: 0.32, ease: EASE.spring, times: [0, 0.5, 1] }
                : localState.kind === 'error'
                  ? {
                      duration: 0.2,
                      ease: 'easeInOut',
                      times: [0, 0.16, 0.32, 0.48, 0.64, 0.8, 1],
                    }
                  : undefined
          }
          className="flex min-w-0 items-center gap-2"
        >
          <motion.span
            aria-hidden="true"
            className={`h-2 w-2 shrink-0 rounded-full ${statusDotClass}`}
            style={{ opacity: 1 }}
            animate={
              reducedMotion
                ? undefined
                : localState.kind === 'testing'
                  ? { opacity: [0.4, 1, 0.4] }
                  : localState.kind === 'rate_limited'
                    ? { opacity: [0.45, 1, 0.45] }
                    : undefined
            }
            transition={
              reducedMotion
                ? undefined
                : localState.kind === 'rate_limited'
                  ? { duration: 2, repeat: Infinity, ease: 'easeInOut' }
                  : localState.kind === 'testing'
                    ? { duration: 1, repeat: Infinity, ease: 'easeInOut' }
                    : undefined
            }
          />
          <div className="text-right">
            <div className="type-body-sm text-text-primary">{status.label}</div>
            <div className="max-w-[150px] truncate text-[11px] text-text-tertiary" title={status.detail}>
              {status.detail}
            </div>
          </div>
        </motion.div>
      </div>

      <div className="mt-4 space-y-4">
        {provider === 'ollama' ? (
          <label className="block">
            <div className="type-label text-text-tertiary">ŚCIEŻKA SERWERA</div>
            <input
              value={draftBaseUrl}
              onChange={(event) => setDraftBaseUrl(event.target.value)}
              className="mt-2 h-11 w-full rounded-md border border-border-default bg-elevated px-4 type-mono-sm text-text-primary outline-none transition duration-base focus:border-border-strong focus:shadow-glow-medium"
              placeholder={meta.defaultBaseUrl}
              aria-label="Ścieżka serwera Ollama"
            />
          </label>
        ) : (
          <label className="block">
            <div className="type-label text-text-tertiary">API KEY</div>
            <div className="mt-2 flex items-center rounded-md border border-border-default bg-elevated px-4">
              <input
                value={draftApiKey}
                onChange={(event) => setDraftApiKey(event.target.value)}
                type={revealed ? 'text' : 'password'}
                className="h-11 flex-1 bg-transparent type-mono-sm text-text-primary outline-none placeholder:text-text-tertiary"
                placeholder={maskedSecret || 'sk-************************XYZ'}
                aria-label={`Klucz API ${meta.displayName}`}
              />
              <button
                type="button"
                onClick={() => setRevealed((value) => !value)}
                className="rounded-sm p-1 text-text-secondary transition hover:text-text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent-primary"
                aria-label={revealed ? 'Ukryj klucz API' : 'Pokaż klucz API'}
              >
                {revealed ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </label>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="rounded-md border border-border-default px-4 py-2 type-body-sm text-text-secondary transition duration-fast ease-standard hover:bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:text-text-disabled"
          >
            Zapisz
          </button>
          <button
            type="button"
            onClick={() => void handleTest()}
            disabled={testing || !testEnabled}
            className="rounded-md border border-accent-primary px-4 py-2 type-body-sm text-accent-primary transition duration-fast ease-standard hover:shadow-glow-medium disabled:cursor-not-allowed disabled:border-border-default disabled:text-text-disabled"
          >
            Test connection
          </button>
        </div>
      </div>

      <div className="mt-4 border-t border-border-subtle pt-3">
        <div className="type-label text-text-tertiary">OSTATNIA SYNCHRONIZACJA</div>
        <div className="mt-2 type-mono-sm text-text-secondary">
          {localLastSync != null && Number.isFinite(localLastSync)
            ? formatDateTime(new Date(localLastSync).toISOString())
            : '—'}
        </div>
      </div>
    </div>
  )
}

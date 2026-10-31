import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../lib/api'

type PublicIntegration = {
  id: string
  name: string
  category: string
  capabilities: string[]
  freshness: string
  cost: string
  auth_type: string
  permissions: string[]
  docs_url: string
  summary: string
  limits: string
  enabled: boolean
  throttle_kind: string
  setup_status?: string
  app_name?: string
  contact?: string
  last_health?: string
  last_used?: string
}

type TestResult = {
  status: string
  detail: string
}

function WikimediaContactForm({
  appName,
  contact,
  busy,
  onSave,
}: {
  appName: string
  contact: string
  busy: boolean
  onSave: (appName: string, contact: string) => void
}) {
  const [name, setName] = useState(appName)
  const [value, setValue] = useState(contact)
  return (
    <form
      className="mt-3 space-y-2 border border-border-default p-3"
      onSubmit={(event) => {
        event.preventDefault()
        onSave(name, value)
      }}
    >
      <p className="text-xs text-text-secondary">
        Wikimedia requires API clients to provide contact information in the client identification.
      </p>
      <label className="block text-xs text-text-tertiary">
        Application name
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="mt-1 w-full border border-border-default bg-void px-2 py-1 text-sm text-text-primary"
        />
      </label>
      <label className="block text-xs text-text-tertiary">
        Contact
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="e-mail, publiczny URL albo User:Nazwa"
          className="mt-1 w-full border border-border-default bg-void px-2 py-1 text-sm text-text-primary"
        />
      </label>
      <button
        type="submit"
        disabled={busy}
        className="border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary"
      >
        Save
      </button>
    </form>
  )
}

const FRESHNESS_LABEL: Record<string, string> = {
  delayed: 'opóźnione, nie realtime',
  historical: 'historyczne',
  realtime: 'realtime',
  static: 'referencyjne',
  reference: 'hasło referencyjne, nie cytat do lektora',
}

export default function PublicDataIntegrationsPanel() {
  const [items, setItems] = useState<PublicIntegration[]>([])
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, TestResult>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const load = () => {
    void fetch(`${API_BASE_URL}/integrations/public-data`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        setItems((await response.json()) as PublicIntegration[])
        setError(null)
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : 'Nie udało się wczytać katalogu.')
      })
  }

  useEffect(() => {
    load()
  }, [])

  const testOne = async (id: string) => {
    setBusy(id)
    try {
      const response = await fetch(`${API_BASE_URL}/integrations/public-data/${id}/test`, { method: 'POST' })
      const payload = (await response.json()) as TestResult
      setResults((current) => ({ ...current, [id]: payload }))
    } catch (reason: unknown) {
      setResults((current) => ({
        ...current,
        [id]: { status: 'OFFLINE', detail: reason instanceof Error ? reason.message : 'Test nie doszedł do API.' },
      }))
    } finally {
      setBusy(null)
    }
  }

  const saveContact = async (id: string, appName: string, contact: string) => {
    setBusy(id)
    setResults((current) => ({ ...current, [id]: { status: 'VALIDATING', detail: 'Checking the Wikimedia connection.' } }))
    try {
      const response = await fetch(`${API_BASE_URL}/integrations/public-data/wikimedia/identity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ app_name: appName, contact }),
      })
      const payload = (await response.json()) as TestResult
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`)
      setResults((current) => ({ ...current, [id]: payload }))
      setError(null)
      load()
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Nie udało się zapisać kontaktu.')
    } finally {
      setBusy(null)
    }
  }

  const setEnabled = async (id: string, enabled: boolean) => {
    setBusy(id)
    try {
      const response = await fetch(`${API_BASE_URL}/integrations/public-data/${id}/enabled`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      setResults((current) => {
        const next = { ...current }
        delete next[id]
        return next
      })
      load()
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Nie udało się zmienić stanu integracji.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="space-y-3" data-testid="public-data-integrations">
      <div>
        <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-tertiary">Integracje</div>
        <p className="mt-2 max-w-3xl text-sm text-text-secondary">
          Źródła wybierane po capability, nie po nazwie w kodzie agenta. Lokalne narzędzia (Ollama, ComfyUI, F5-TTS,
          YouTube) zostają w swoich dotychczasowych panelach.
        </p>
        <p className="mt-2 max-w-3xl text-xs text-text-tertiary">
          Przerwa 1 s między wywołaniami tego samego źródła to INTERNAL_THROTTLE Atnosa. Nie jest oficjalnym limitem
          dostawcy. Liczbowy próg Frankfurtera pozostaje UNVERIFIED.
        </p>
      </div>
      {error ? <div className="border border-error/40 bg-error/10 p-3 text-sm text-error">{error}</div> : null}
      {items.map((item) => {
        const result = results[item.id]
        const shownStatus = result?.status ?? (item.setup_status === 'REQUIRES_SETUP' ? 'REQUIRES_SETUP' : item.enabled ? null : 'DISABLED')
        return (
          <article key={item.id} className="border border-border-default bg-void p-4" data-integration-id={item.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-display text-sm uppercase tracking-[0.12em] text-text-primary">{item.name}</h3>
                <p className="mt-2 text-sm text-text-secondary">{item.summary}</p>
              </div>
              <span className="border border-border-default px-2 py-1 font-mono text-[10px] uppercase text-text-tertiary">
                {item.cost} / {item.category}
              </span>
            </div>
            <p className="mt-2 text-xs text-text-tertiary">
              Capability: {item.capabilities.join(', ')}. Świeżość: {FRESHNESS_LABEL[item.freshness] ?? item.freshness}.
            </p>
            <p className="mt-1 text-xs text-text-tertiary">
              Może: {item.permissions.join(', ')}. Auth: {item.auth_type}. Throttle: {item.throttle_kind}.
            </p>
            <p className="mt-1 text-xs text-text-tertiary">{item.limits}</p>
            {item.last_health || item.last_used ? (
              <p className="mt-1 text-xs text-text-tertiary">
                {item.last_health ? `Last health check: ${item.last_health}. ` : ''}
                {item.last_used ? `Last used: ${item.last_used}.` : ''}
              </p>
            ) : null}
            {shownStatus ? (
              <p className="mt-3 text-sm text-text-primary" data-testid={`integration-status-${item.id}`}>
                {shownStatus}
                {result?.detail
                  ? `: ${result.detail}`
                  : shownStatus === 'REQUIRES_SETUP'
                    ? ': Add a contact email, public URL, or Wikimedia user page before testing the connection.'
                    : item.enabled
                      ? ''
                      : ': Integracja jest wyłączona.'}
              </p>
            ) : null}
            {item.id === 'wikimedia' ? (
              <WikimediaContactForm
                appName={item.app_name || 'Atnos'}
                contact={item.contact || ''}
                busy={busy === item.id}
                onSave={(appName, contact) => void saveContact(item.id, appName, contact)}
              />
            ) : null}
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void testOne(item.id)}
                disabled={busy === item.id || !item.enabled}
                className="border border-accent-primary px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:opacity-50"
              >
                {busy === item.id ? 'Sprawdzam' : 'Test connection'}
              </button>
              <button
                type="button"
                onClick={() => void setEnabled(item.id, !item.enabled)}
                disabled={busy === item.id}
                className="border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary"
              >
                {item.enabled ? 'Disable' : 'Enable'}
              </button>
              <a className="border border-border-default px-3 py-2 text-[11px] uppercase text-text-secondary" href={item.docs_url}>
                Setup guide
              </a>
            </div>
          </article>
        )
      })}
    </section>
  )
}

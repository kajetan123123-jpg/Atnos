import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchRuntimeInventory, type RuntimeInventoryPayload } from '../lib/api'

const TASK_LABELS: Record<string, string> = {
  general_chat: 'Rozmowa',
  orchestration: 'Planowanie i koordynacja',
  coding: 'Kodowanie',
  search_research: 'Research',
  vision: 'Analiza obrazu',
  transcription: 'Transkrypcja',
  synthesis: 'Synteza dokumentów',
  image_generation: 'Generowanie obrazów',
}

type StateTone = 'local' | 'external' | 'missing'

function StatusBadge({ label, tone }: { label: string; tone: StateTone }) {
  const style = tone === 'local'
    ? 'border-sem-success/40 text-sem-success'
    : tone === 'external'
      ? 'border-sem-warning/40 text-sem-warning'
      : 'border-border-strong text-text-tertiary'
  return (
    <span className={`inline-flex rounded-pill border px-3 py-1 font-mono text-xs uppercase tracking-[0.12em] ${style}`}>
      {label}
    </span>
  )
}

export function RuntimeCapabilityStatusPanel() {
  const [inventory, setInventory] = useState<RuntimeInventoryPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setInventory(await fetchRuntimeInventory())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Nie udało się sprawdzić runtime.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const providers = useMemo(
    () => new Map((inventory?.providers ?? []).map((provider) => [provider.provider_key, provider])),
    [inventory?.providers],
  )
  const models = useMemo(
    () => new Map((inventory?.models ?? []).map((model) => [model.model_key, model])),
    [inventory?.models],
  )

  const modelState = (modelKey: string | null) => {
    if (!modelKey) return { label: 'brak przypisanego modelu', tone: 'missing' as const, detail: '' }
    const model = models.get(modelKey)
    const provider = providers.get(model?.provider_key ?? modelKey.split('/')[0])
    if (!model || !provider || !provider.is_enabled || model.status !== 'ready') {
      return { label: 'wymaga konfiguracji', tone: 'missing' as const, detail: model?.runtime_status_reason ?? '' }
    }
    if (provider.local_first) {
      if (provider.health_status === 'healthy' && model.runtime_model_present) {
        return {
          label: 'model lokalny wykryty',
          tone: 'local' as const,
          detail: model.resolved_runtime_model_name ?? model.model_key,
        }
      }
      return {
        label: model.runtime_model_present === false ? 'brak modelu lokalnego' : 'runtime lokalny niezweryfikowany',
        tone: 'missing' as const,
        detail: model.runtime_status_reason || provider.health_reason,
      }
    }
    if (provider.health_status === 'healthy') {
      return {
        label: 'API odpowiada; wybór trybu zewnętrznego może kosztować',
        tone: 'external' as const,
        detail: provider.label,
      }
    }
    return {
      label: provider.health_status === 'not_supported' ? 'adapter gotowy; połączenie niezweryfikowane' : 'wymaga konta lub konfiguracji',
      tone: 'missing' as const,
      detail: provider.health_reason || model.runtime_status_reason,
    }
  }

  const providerState = (provider: RuntimeInventoryPayload['providers'][number]) => {
    if (!provider.is_enabled || provider.status === 'disabled') return { label: 'wyłączony', tone: 'missing' as const }
    if (provider.status !== 'ready') return { label: 'wymaga konfiguracji', tone: 'missing' as const }
    if (provider.local_first) {
      return provider.health_status === 'healthy'
        ? { label: 'serwer lokalny odpowiada', tone: 'local' as const }
        : { label: 'runtime wymaga sprawdzenia', tone: 'missing' as const }
    }
    return provider.health_status === 'healthy'
      ? { label: 'zewnętrzne API odpowiada', tone: 'external' as const }
      : { label: 'adapter gotowy; połączenie niepotwierdzone', tone: 'missing' as const }
  }

  return (
    <section data-testid="runtime-capability-status" className="rounded-xl border border-border-default bg-elevated p-4" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-base text-text-primary">Rzeczywisty status możliwości</h3>
          <p className="mt-1 max-w-3xl type-body-sm text-text-secondary">
            Ten odczyt potwierdza połączenie i obecność modelu. Jakość oraz wykonanie konkretnego zadania wymagają osobnego testu. Zewnętrzne API wymaga właściwego trybu, może wymagać konta i może naliczać koszt.
          </p>
        </div>
        <button type="button" onClick={() => void refresh()} disabled={loading} className="rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary disabled:opacity-60">
          {loading ? 'Sprawdzam...' : 'Odśwież test runtime'}
        </button>
      </div>

      {error ? <div role="alert" className="mt-3 rounded-lg border border-error/50 bg-error/10 px-3 py-2 type-body-sm text-text-primary">Test runtime nie powiódł się: {error}</div> : null}

      {inventory ? (
        <div className="mt-4 grid gap-4 xl:grid-cols-[1.25fr_1fr]">
          <div>
            <div className="label-eyebrow mb-2">Zadania i modele</div>
            <div className="space-y-2">
              {inventory.tasks.map((task) => {
                const local = modelState(task.default_local_model_key)
                const external = task.default_external_model_key ? modelState(task.default_external_model_key) : null
                return (
                  <div key={task.task} className="rounded-lg border border-border-default bg-panel px-3 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="type-body text-text-primary">{TASK_LABELS[task.task] ?? task.task}</div>
                      <StatusBadge label={`Lokalnie: ${local.label}`} tone={local.tone} />
                    </div>
                    {local.detail ? <div className="mt-1 break-all font-mono text-xs text-text-tertiary">{local.detail}</div> : null}
                    {external ? (
                      <div className="mt-2 border-t border-border-default pt-2">
                        <div className="type-body-sm text-text-secondary">Zewnętrzny fallback: {external.label}</div>
                        {external.detail ? <div className="mt-1 break-all font-mono text-xs text-text-tertiary">{external.detail}</div> : null}
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>

          <div className="space-y-4">
            <div>
              <div className="label-eyebrow mb-2">Dostawcy</div>
              <div className="space-y-2">
                {inventory.providers.map((provider) => {
                  const state = providerState(provider)
                  return (
                    <div key={provider.provider_key} className="flex items-start justify-between gap-3 rounded-lg border border-border-default bg-panel px-3 py-2">
                      <div>
                        <div className="type-body-sm text-text-primary">{provider.label}{provider.local_first ? ' · lokalny' : ' · zewnętrzny'}</div>
                        <div className="mt-1 type-caption text-text-tertiary">{provider.health_reason || provider.status_reason}</div>
                      </div>
                      <StatusBadge label={state.label} tone={state.tone} />
                    </div>
                  )
                })}
              </div>
            </div>

            <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="type-body text-text-primary">Transkrypcja mowy</div>
                <StatusBadge
                  label={inventory.voice_stt.effective_engine.startsWith('browser') ? 'tryb przeglądarkowy; lokalność niepotwierdzona' : inventory.voice_stt.status === 'ready' ? inventory.voice_stt.effective_engine : inventory.voice_stt.status}
                  tone={inventory.voice_stt.effective_engine.startsWith('browser') ? 'external' : inventory.voice_stt.status === 'ready' ? 'local' : 'missing'}
                />
              </div>
              <div className="mt-1 type-body-sm text-text-tertiary">{inventory.voice_stt.status_reason}</div>
            </div>
          </div>
        </div>
      ) : !loading && !error ? (
        <div className="mt-3 type-body-sm text-text-secondary">Brak odczytu runtime. Użyj odświeżenia, aby sprawdzić możliwości.</div>
      ) : null}
    </section>
  )
}

import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  fetchProductionF5TTSRuntimeStatus,
  fetchProductionProviderDoctor,
  runProductionProviderDoctorAction,
  startProductionF5TTSRuntime,
  stopProductionF5TTSRuntime,
  type F5TTSRuntimeStatus,
  type ProductionProviderDoctorAction,
  type ProductionProviderDoctorCard,
  type ProductionProviderDoctorPayload,
} from '../lib/api'

const STATUS_LABELS: Record<string, string> = {
  ready: 'Gotowe',
  blocked: 'Blokada',
  warning: 'Uwaga',
  unknown: 'Brak danych',
  needs_attention: 'Wymaga uwagi',
}

const ACTION_LABELS: Record<string, string> = {
  run_voice_provider_doctor: 'Uruchom test głosu',
  run_visual_backend_doctor: 'Uruchom test wideo',
  connect_youtube_oauth_for_blocked_accounts: 'Podłącz YouTube OAuth',
  enable_allow_public_upload_only_after_owner_review: 'Włącz public upload dopiero po review',
  regenerate_with_premium_voice_after_provider_fix: 'Regeneruj po naprawie głosu',
  regenerate_with_public_visual_backend_after_provider_fix: 'Regeneruj po naprawie grafiki',
  continue_with_cost_guard_enabled: 'Kontynuuj z limitem kosztów',
  generate_private_sample_to_collect_evidence: 'Wygeneruj prywatną próbkę',
  start_configured_f5_tts_runtime: 'Uruchom lokalny F5-TTS',
  f5_tts_ready_run_voice_provider_doctor: 'Uruchom test głosu',
  configure_f5_tts_cli: 'Uzupełnij runtime i referencyjną próbkę głosu F5-TTS',
}

function statusClass(status: string) {
  if (status === 'ready') return 'border-success/50 bg-success/10 text-success'
  if (status === 'blocked' || status === 'needs_attention') return 'border-error/50 bg-error/10 text-error'
  if (status === 'warning') return 'border-warning/50 bg-warning/10 text-warning'
  return 'border-text-tertiary/30 bg-elevated/40 text-text-secondary'
}

function statusIcon(status: string) {
  if (status === 'ready') return <CheckCircle2 size={14} />
  if (status === 'blocked') return <XCircle size={14} />
  return <AlertTriangle size={14} />
}

function humanAction(action: string) {
  return ACTION_LABELS[action] ?? action.replaceAll('_', ' ')
}

function evidenceLine(card: ProductionProviderDoctorCard) {
  if (card.key === 'latest_production_evidence') {
    const jobId = String(card.evidence.job_id ?? '')
    const blockers = Array.isArray(card.evidence.blockers) ? card.evidence.blockers.length : 0
    return jobId ? `Ostatni job: ${jobId} / blokery: ${blockers}` : 'Brak ostatniego joba z błędem.'
  }
  if (card.key === 'voice_provider') {
    const latest = card.evidence.latest_job_voice as Record<string, unknown> | undefined
    if (card.evidence.report_stale === true) {
      const age = card.evidence.report_age_hours
      return age == null ? 'Raport glosu jest nieaktualny.' : `Raport glosu ma ${String(age)} h i wymaga ponownego testu.`
    }
    return latest?.quality_tier ? `Ostatni głos: ${String(latest.quality_tier)}` : 'Brak potwierdzonego głosu premium.'
  }
  if (card.key === 'visual_backend') {
    const latest = card.evidence.latest_job_visual as Record<string, unknown> | undefined
    if (latest?.fallback_used != null) return `Fallback wideo: ${String(latest.fallback_used)}`
    if (card.status === 'ready') return 'Backend potwierdzony przez ostatni live provider check.'
    return 'Brak potwierdzonego backendu public-quality.'
  }
  if (card.key === 'cost_guard') {
    const remaining = card.evidence.monthly_remaining_pln
    return remaining == null ? 'Brak miesięcznego limitu.' : `Pozostało w miesiącu: ${String(remaining)} PLN`
  }
  return card.critical ? 'Krytyczne dla automatyzacji.' : 'Informacja operatorska.'
}

function providerStatusLabel(card: ProductionProviderDoctorCard) {
  if (card.key === 'voice_provider' || card.key === 'visual_backend') {
    return card.status === 'ready' ? 'Dostawca gotowy' : 'Dostawca blokada'
  }
  if (card.key === 'cost_guard' && card.status === 'ready') return 'Limit aktywny'
  return STATUS_LABELS[card.status] ?? card.status
}

function latestRenderEvidence(card: ProductionProviderDoctorCard) {
  if (card.key === 'voice_provider') {
    const latest = card.evidence.latest_job_voice as Record<string, unknown> | undefined
    const qualityTier = String(latest?.quality_tier ?? '')
    return {
      status: qualityTier === 'premium' || qualityTier === 'public_quality' ? 'ready' : 'warning',
      text: qualityTier ? `Głos: ${qualityTier}` : 'Brak renderu potwierdzającego jakość głosu.',
    }
  }
  if (card.key === 'visual_backend') {
    const latest = card.evidence.latest_job_visual as Record<string, unknown> | undefined
    const fallback = latest?.fallback_used
    return {
      status: fallback === false ? 'ready' : 'warning',
      text: fallback === false ? 'Render bez fallbacku wizualnego.' : 'Brak renderu potwierdzającego publiczną jakość obrazu.',
    }
  }
  return null
}

function f5RuntimeStatus(runtime: F5TTSRuntimeStatus) {
  if (runtime.transport_ready ?? (runtime.endpoint_ready && runtime.reference_audio_exists)) return 'ready'
  if (runtime.install_exists && runtime.runtime_package_installed && runtime.launch_supported) return 'warning'
  return 'blocked'
}

function f5RuntimeSummary(runtime: F5TTSRuntimeStatus) {
  return runtime.diagnosis_message
}

export function ProviderDoctorPanel({ accountId }: { accountId?: string }) {
  const [payload, setPayload] = useState<ProductionProviderDoctorPayload | null>(null)
  const [f5Runtime, setF5Runtime] = useState<F5TTSRuntimeStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [f5StartLoading, setF5StartLoading] = useState(false)
  const [actionLoadingKey, setActionLoadingKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [doctorResult, runtimeResult] = await Promise.allSettled([
        fetchProductionProviderDoctor(accountId),
        fetchProductionF5TTSRuntimeStatus(),
      ])
      setF5Runtime(runtimeResult.status === 'fulfilled' ? runtimeResult.value : null)
      if (doctorResult.status === 'rejected') throw doctorResult.reason
      setPayload(doctorResult.value)
    } catch (loadError) {
      setPayload(null)
      setError(loadError instanceof Error ? loadError.message : 'Nie udało się pobrać testów połączeń.')
    } finally {
      setLoading(false)
    }
  }

  const startF5Runtime = async () => {
    setF5StartLoading(true)
    setError(null)
    try {
      const runtime = await startProductionF5TTSRuntime()
      setF5Runtime(runtime)
      setPayload(await fetchProductionProviderDoctor(accountId))
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : 'Nie udało się uruchomić lokalnego F5-TTS.')
    } finally {
      setF5StartLoading(false)
    }
  }

  const stopF5Runtime = async () => {
    setF5StartLoading(true)
    setError(null)
    try {
      const runtime = await stopProductionF5TTSRuntime()
      setF5Runtime(runtime)
      setPayload(await fetchProductionProviderDoctor(accountId))
    } catch (stopError) {
      setError(stopError instanceof Error ? stopError.message : 'Nie udało się zatrzymać lokalnego F5-TTS.')
    } finally {
      setF5StartLoading(false)
    }
  }

  const runOperatorAction = async (action: ProductionProviderDoctorAction) => {
    if (action.kind !== 'frontend_api' || !action.safe_to_run) {
      return
    }
    setActionLoadingKey(action.id)
    setError(null)
    try {
      const result = await runProductionProviderDoctorAction(action.id, accountId)
      setPayload(result.provider_doctor)
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Nie udało się uruchomić testu providera.')
    } finally {
      setActionLoadingKey(null)
    }
  }

  useEffect(() => {
    void load()
  }, [accountId])

  const orderedCards = useMemo(() => {
    return [...(payload?.cards ?? [])].sort((a, b) => {
      const weight = { blocked: 0, warning: 1, unknown: 2, ready: 3 } as Record<string, number>
      return (weight[a.status] ?? 4) - (weight[b.status] ?? 4)
    })
  }, [payload])

  const actionsByCard = useMemo(() => {
    const grouped = new Map<string, ProductionProviderDoctorAction[]>()
    for (const action of payload?.operator_actions ?? []) {
      grouped.set(action.card_key, [...(grouped.get(action.card_key) ?? []), action])
    }
    return grouped
  }, [payload])

  return (
    <section className="border border-border-default bg-panel">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-default px-4 py-3">
        <div>
          <div className="font-display text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">
            Testy połączeń
          </div>
          <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
            Głos / grafika / OAuth / budżet / ostatni render
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`border px-2 py-1 font-mono text-[10px] uppercase ${statusClass(payload?.status ?? 'unknown')}`}>
            {STATUS_LABELS[payload?.status ?? 'unknown'] ?? payload?.status ?? 'Brak danych'}
          </span>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-2 border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Odśwież
          </button>
        </div>
      </div>
      {error ? (
        <div className="m-4 border border-error/40 bg-error/10 p-3 text-xs text-error">{error}</div>
      ) : null}
      <div className="grid gap-3 p-4 xl:grid-cols-3">
        {f5Runtime ? (
          <article className={`border p-3 ${statusClass(f5RuntimeStatus(f5Runtime))}`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 font-display text-[12px] font-semibold uppercase tracking-[0.12em]">
                  {statusIcon(f5RuntimeStatus(f5Runtime))}
                  <span className="truncate">Lokalny F5-TTS</span>
                </div>
                <div className="mt-2 text-xs normal-case leading-relaxed text-text-secondary">
                  {f5RuntimeSummary(f5Runtime)}
                </div>
              </div>
              <span className="shrink-0 border border-current/40 px-2 py-1 font-mono text-[9px] uppercase">
                {f5RuntimeStatus(f5Runtime) === 'ready' ? 'Konfiguracja gotowa' : 'Wymaga uwagi'}
              </span>
            </div>
            <div className="mt-3 border-t border-current/20 pt-3 font-mono text-[10px] uppercase text-text-tertiary">
              {f5Runtime.transport === 'cli' ? 'Synteza lokalna · CPU' : `Endpoint: ${f5Runtime.endpoint}`}
            </div>
            {f5Runtime.transport !== 'cli' && f5Runtime.endpoint_error ? (
              <div className="mt-2 font-mono text-[10px] normal-case text-text-secondary">
                {f5Runtime.endpoint_error}
              </div>
            ) : null}
            {f5Runtime.blocking_reasons.length > 0 ? (
              <ul className="mt-2 space-y-1 border border-current/20 bg-void/40 p-2 text-[11px] normal-case text-text-secondary">
                {f5Runtime.blocking_reasons.map((reason) => (
                  <li key={reason}>• {reason}</li>
                ))}
              </ul>
            ) : null}
            <div className="mt-2 font-mono text-[10px] uppercase text-text-secondary">
              Akcja: {humanAction(f5Runtime.next_action)}
            </div>
            {f5Runtime.transport !== 'cli' && f5Runtime.launch_supported && !f5Runtime.endpoint_ready ? (
              <div className="mt-3 border border-current/20 bg-void/40 p-2">
                <button
                  type="button"
                  onClick={() => void startF5Runtime()}
                  disabled={f5StartLoading}
                  className="inline-flex items-center gap-2 border border-current/40 px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] disabled:cursor-wait disabled:opacity-50"
                >
                  <RefreshCw size={12} className={f5StartLoading ? 'animate-spin' : ''} />
                  Uruchom lokalny F5-TTS
                </button>
                <div className="mt-2 text-[11px] normal-case leading-relaxed text-text-secondary">
                  Ręczna akcja operatora. Może załadować model do GPU; nie uruchamiaj jej równolegle z ComfyUI.
                </div>
              </div>
            ) : null}
            {f5Runtime.endpoint_ready ? (
              <div className="mt-3 border border-current/20 bg-void/40 p-2">
                <button
                  type="button"
                  onClick={() => void stopF5Runtime()}
                  disabled={f5StartLoading}
                  className="inline-flex items-center gap-2 border border-current/40 px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] disabled:cursor-wait disabled:opacity-50"
                >
                  <XCircle size={12} />
                  Zatrzymaj F5-TTS
                </button>
                <div className="mt-2 text-[11px] normal-case leading-relaxed text-text-secondary">
                  Zatrzymaj głos przed uruchomieniem ComfyUI. Atnos blokuje równoległy start obu usług GPU.
                </div>
              </div>
            ) : null}
          </article>
        ) : null}
        {orderedCards.map((card) => (
          <article key={card.key} className={`border p-3 ${statusClass(card.status)}`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 font-display text-[12px] font-semibold uppercase tracking-[0.12em]">
                  {statusIcon(card.status)}
                  <span className="truncate">{card.label}</span>
                </div>
                <div className="mt-2 text-xs normal-case leading-relaxed text-text-secondary">{card.summary}</div>
              </div>
              <span className="shrink-0 border border-current/40 px-2 py-1 font-mono text-[9px] uppercase">
                {providerStatusLabel(card)}
              </span>
            </div>
            <div className="mt-3 border-t border-current/20 pt-3 font-mono text-[10px] uppercase text-text-tertiary">
              {evidenceLine(card)}
            </div>
            {latestRenderEvidence(card) ? (
              <div className={`mt-2 border p-2 font-mono text-[10px] uppercase ${statusClass(latestRenderEvidence(card)?.status ?? 'unknown')}`}>
                <span className="block text-[9px] opacity-75">Ostatni render</span>
                {latestRenderEvidence(card)?.text}
              </div>
            ) : null}
            <div className="mt-2 font-mono text-[10px] uppercase text-text-secondary">
              Akcja: {humanAction(card.next_action)}
            </div>
            {(actionsByCard.get(card.key) ?? []).length > 0 ? (
              <div className="mt-3 space-y-2">
                {(actionsByCard.get(card.key) ?? []).map((action) => {
                  const clickable = action.kind === 'frontend_api' && action.safe_to_run
                  return (
                    <div key={action.id} className="border border-current/20 bg-void/40 p-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={() => void runOperatorAction(action)}
                          disabled={!clickable || actionLoadingKey === action.id}
                          className="inline-flex items-center gap-2 border border-current/40 px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <RefreshCw size={12} className={actionLoadingKey === action.id ? 'animate-spin' : ''} />
                          {action.label || humanAction(action.id)}
                        </button>
                        {action.requires_owner_action ? (
                          <span className="font-mono text-[9px] uppercase text-warning">wymaga decyzji właściciela</span>
                        ) : null}
                      </div>
                      <div className="mt-2 text-[11px] normal-case leading-relaxed text-text-secondary">
                        {action.reason}
                      </div>
                      {action.endpoint ? (
                        <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">
                          {action.method ?? 'OPEN'} {action.endpoint}
                        </div>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            ) : null}
          </article>
        ))}
        {!loading && orderedCards.length === 0 ? (
          <div className="border border-dashed border-border-default p-5 text-sm text-text-secondary xl:col-span-3">
            Brak danych Provider Doctora. Odśwież panel albo uruchom backend.
          </div>
        ) : null}
      </div>
    </section>
  )
}

import { useEffect, useState, type ReactNode } from 'react'
import { fetchMediaObservability } from '../lib/api'
import type { MediaObservabilityAlert, MediaObservabilityErrorItem, MediaObservabilityPayload } from '../lib/api'

const ALERT_CLASS: Record<MediaObservabilityAlert, string> = {
  HEALTHY: 'border-success/40 bg-success/10 text-success',
  DEGRADED: 'border-warning/40 bg-warning/10 text-warning',
  BLOCKED: 'border-warning/50 bg-warning/15 text-warning',
  ERROR: 'border-error/50 bg-error/10 text-error',
}

function alertOf(value: unknown): MediaObservabilityAlert {
  const raw = String(value || 'DEGRADED').toUpperCase()
  if (raw === 'HEALTHY' || raw === 'DEGRADED' || raw === 'BLOCKED' || raw === 'ERROR') return raw
  return 'DEGRADED'
}

function fmtRate(value: unknown): string {
  if (value == null || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  return `${(n * 100).toFixed(1)}%`
}

function fmtNum(value: unknown, suffix = ''): string {
  if (value == null || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)
  return `${n}${suffix}`
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border-default bg-void p-3">
      <div className="font-display text-[10px] uppercase tracking-[0.14em] text-text-tertiary">{label}</div>
      <div className="mt-2 font-mono text-sm text-text-primary">{value}</div>
    </div>
  )
}

function Section({
  title,
  alert,
  children,
}: {
  title: string
  alert?: MediaObservabilityAlert
  children: ReactNode
}) {
  return (
    <div className="border border-border-default bg-void/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-display text-[11px] uppercase tracking-[0.14em] text-text-primary">{title}</div>
        {alert ? (
          <span className={['border px-2 py-1 font-mono text-[10px]', ALERT_CLASS[alert]].join(' ')}>{alert}</span>
        ) : null}
      </div>
      <div className="mt-3 space-y-2 text-sm text-text-secondary">{children}</div>
    </div>
  )
}

export function MediaObservabilityPanel() {
  const [data, setData] = useState<MediaObservabilityPayload | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'error'>('idle')
  const [message, setMessage] = useState('')
  const [simulate, setSimulate] = useState('')

  const refresh = async (sim?: string) => {
    setStatus('loading')
    setMessage('')
    try {
      const payload = await fetchMediaObservability({
        historyHours: 24,
        maxJobs: 40,
        simulate: sim || undefined,
      })
      setData(payload)
      setStatus('idle')
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'Nie udało się pobrać media observability.')
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const overall = alertOf(data?.overall_alert)
  const errors = (data?.errors ?? []) as MediaObservabilityErrorItem[]
  const channels = (data?.channels ?? {}) as Record<string, Record<string, unknown>>
  const perf = (data?.performance ?? {}) as Record<string, unknown>
  const comfy = (data?.comfy ?? {}) as Record<string, unknown>
  const gpu = (data?.gpu ?? {}) as Record<string, unknown>
  const cache = (data?.cache ?? {}) as Record<string, unknown>
  const assets = (data?.assets ?? {}) as Record<string, unknown>
  const quality = (data?.quality ?? {}) as Record<string, unknown>
  const models = (data?.models ?? {}) as Record<string, unknown>
  const orders = (data?.orders ?? {}) as Record<string, unknown>

  return (
    <section className="space-y-4 border border-border-default bg-void p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">
            Media Production Observability
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-text-secondary">
            Jedno miejsce: Comfy, GPU, cache, Asset Brain, quality gates, model routing, błędy i produkcja kanałów.
            Stan uwzględnia wymagane capability — nie tylko HTTP 200.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={['border px-2 py-1 font-mono text-[11px]', ALERT_CLASS[overall]].join(' ')}>
            {data?.overall_alert ?? '…'}
          </span>
          <button
            type="button"
            onClick={() => void refresh(simulate)}
            className="border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary"
          >
            Odśwież
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="font-mono text-[11px] text-text-tertiary">Symulacja (dev/test):</label>
        <select
          value={simulate}
          onChange={(event) => setSimulate(event.target.value)}
          className="border border-border-default bg-void px-2 py-1 font-mono text-[11px] text-text-primary"
        >
          <option value="">brak</option>
          <option value="comfy_down">Comfy down</option>
          <option value="gpu_locked">GPU locked</option>
          <option value="cache_miss">Cache miss</option>
          <option value="asset_rejected">Asset rejected</option>
          <option value="quality_fail">Quality gate fail</option>
          <option value="model_fallback">Model fallback</option>
          <option value="comfy_down,gpu_locked,quality_fail">Wszystkie krytyczne</option>
        </select>
        <button
          type="button"
          onClick={() => void refresh(simulate)}
          className="border border-border-default px-2 py-1 font-mono text-[11px] text-text-secondary hover:border-accent-primary"
        >
          Zastosuj
        </button>
      </div>

      {message ? <div className="border border-error/40 bg-error/10 p-2 font-mono text-[11px] text-error">{message}</div> : null}
      {status === 'loading' && !data ? (
        <div className="font-mono text-[11px] text-text-tertiary">Ładowanie…</div>
      ) : null}

      {data ? (
        <>
          <div className="grid gap-3 md:grid-cols-4 xl:grid-cols-8">
            <Metric label="Comfy" value={String(comfy.status ?? '—')} />
            <Metric label="GPU" value={gpu.locked ? `locked:${String(gpu.current_owner ?? gpu.owner ?? '')}` : 'free'} />
            <Metric label="Cache hit" value={fmtRate(cache.hit_rate)} />
            <Metric label="WB approve" value={fmtRate(assets.wb_asset_approval_rate ?? assets.approval_rate)} />
            <Metric label="QC fail" value={fmtRate(quality.qc_failure_rate)} />
            <Metric label="Orders run" value={fmtNum(orders.running)} />
            <Metric label="Accepted/h" value={fmtNum(perf.accepted_assets_per_hour)} />
            <Metric label="Retries" value={fmtNum(perf.average_retries_before_accepted ?? perf.retries)} />
          </div>

          <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
            <Section title="ComfyUI" alert={alertOf(comfy.alert)}>
              <div className="font-mono text-[11px] leading-5">
                PID {String(comfy.pid ?? '—')} · port {String(comfy.port ?? '—')} · uptime {fmtNum(comfy.uptime_s, 's')}
                <br />
                restart {String(comfy.last_restart ?? '—')} · startup {fmtNum(comfy.startup_time_s, 's')}
                <br />
                {String(comfy.detail ?? '')}
              </div>
            </Section>
            <Section title="GPU" alert={alertOf(gpu.alert)}>
              <div className="font-mono text-[11px] leading-5">
                owner {String(gpu.current_owner ?? gpu.owner ?? '—')} · duration {fmtNum(gpu.job_duration_s ?? gpu.duration_s, 's')}
                <br />
                last job {String(gpu.last_job ?? '—')} · alive {String(gpu.owner_alive ?? '—')}
              </div>
            </Section>
            <Section title="Cache" alert={alertOf(cache.alert)}>
              <div className="font-mono text-[11px] leading-5">
                hits {fmtNum(cache.hits)} / misses {fmtNum(cache.misses)} · rate {fmtRate(cache.hit_rate)}
              </div>
            </Section>
            <Section title="Asset Brain" alert={alertOf(assets.alert)}>
              <div className="font-mono text-[11px] leading-5">
                gen {fmtNum(assets.generated)} · review {fmtNum(assets.awaiting_review)} · ok {fmtNum(assets.approved)} ·
                rej {fmtNum(assets.rejected)}
                <br />
                approval {fmtRate(assets.approval_rate)} · characters {fmtRate(assets.character_approval_rate)}
              </div>
            </Section>
            <Section title="Quality gates" alert={alertOf(quality.alert)}>
              <div className="font-mono text-[11px] leading-5">
                VALIDATED {fmtNum(quality.validated)} · FAIL {fmtNum(quality.fail)} · fail rate{' '}
                {fmtRate(quality.qc_failure_rate)}
                <br />
                DC visual QC {fmtRate(quality.dc_visual_qc_pass_rate)}
              </div>
            </Section>
            <Section title="Orders" alert={alertOf(orders.alert)}>
              <div className="font-mono text-[11px] leading-5">
                running {fmtNum(orders.running)} · failed {fmtNum(orders.failed)} · done {fmtNum(orders.done)}
                <br />
                avg processing {fmtNum(orders.average_processing_time_s, 's')}
              </div>
            </Section>
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            {(['wealth_blueprint', 'wonder_crew', 'dark_chronicles'] as const).map((channelId) => {
              const ch = channels[channelId] || {}
              const model = (ch.model as Record<string, unknown> | undefined) || {}
              const labels: Record<string, string> = {
                wealth_blueprint: 'Wealth Blueprint',
                wonder_crew: 'Wonder Crew',
                dark_chronicles: 'Dark Chronicles',
              }
              return (
                <Section key={channelId} title={labels[channelId]} alert={alertOf(ch.alert)}>
                  <div className="font-mono text-[11px] leading-5">
                    model {String(model.active_primary_model ?? model.quality_model ?? '—')}
                    <br />
                    {channelId === 'wealth_blueprint' ? (
                      <>
                        remotion {String((ch.remotion_health as { ok?: boolean } | undefined)?.ok ?? '—')} · Asset Brain{' '}
                        {fmtRate((ch.asset_brain as { approval_rate?: number } | undefined)?.approval_rate ?? ch.approval_rate)}
                        <br />
                        avg prod {fmtNum(ch.average_production_time_s, 's')}
                      </>
                    ) : null}
                    {channelId === 'wonder_crew' ? (
                      <>
                        refs {fmtNum(ch.approved_character_references)} · 3D{' '}
                        {String((ch.handoff_3d_readiness as { status?: string } | undefined)?.status ?? '—')}
                      </>
                    ) : null}
                    {channelId === 'dark_chronicles' ? (
                      <>
                        visual QC {fmtRate(ch.visual_qc_pass_rate)} · acceptance {fmtRate(ch.asset_acceptance_rate)}
                      </>
                    ) : null}
                  </div>
                </Section>
              )
            })}
          </div>

          <Section title="Quality + throughput" alert={alertOf(quality.alert)}>
            <div className="grid gap-2 md:grid-cols-3 lg:grid-cols-6">
              <Metric label="Avg gen s" value={fmtNum(perf.average_generation_seconds, 's')} />
              <Metric label="Accepted/h" value={fmtNum(perf.accepted_assets_per_hour)} />
              <Metric label="Render s" value={fmtNum(perf.render_time_s, 's')} />
              <Metric label="Cache hit" value={fmtRate(perf.cache_hit_rate)} />
              <Metric label="QC fail" value={fmtRate(perf.qc_failure_rate)} />
              <Metric label="Retries→accept" value={fmtNum(perf.average_retries_before_accepted)} />
            </div>
            <div className="mt-2 grid gap-2 md:grid-cols-3">
              <Metric label="Character approve" value={fmtRate(perf.character_approval_rate)} />
              <Metric label="WB asset approve" value={fmtRate(perf.wb_asset_approval_rate)} />
              <Metric label="DC visual QC pass" value={fmtRate(perf.dc_visual_qc_pass_rate)} />
            </div>
          </Section>

          <Section title="Model routing" alert={alertOf(models.alert)}>
            <div className="grid gap-2 md:grid-cols-3">
              {Object.entries((models.channels as Record<string, Record<string, unknown>>) || {}).map(([id, routing]) => (
                <div key={id} className="border border-border-default p-2 font-mono text-[11px] leading-5">
                  <div className="text-text-primary">{id}</div>
                  primary {String(routing.active_primary_model ?? '—')}
                  <br />
                  fallback {String(routing.fallback_model ?? '—')}
                  <br />
                  last fail{' '}
                  {routing.last_model_failure
                    ? String((routing.last_model_failure as MediaObservabilityErrorItem).error_code)
                    : '—'}
                </div>
              ))}
            </div>
          </Section>

          <Section title="Ostatnie błędy" alert={errors.length ? 'ERROR' : 'HEALTHY'}>
            {errors.length === 0 ? (
              <div className="font-mono text-[11px] text-text-tertiary">Brak błędów w oknie historii.</div>
            ) : (
              <div className="space-y-2">
                {errors.slice(0, 12).map((err, index) => (
                  <div key={`${err.job_request_id ?? 'x'}-${index}`} className="border border-error/30 bg-error/5 p-2 font-mono text-[11px] leading-5">
                    <div>
                      [{err.error_code}] {err.component} · {err.timestamp ?? '—'}
                    </div>
                    <div>{err.short_message}</div>
                    <div className="text-text-tertiary">
                      job {err.job_request_id ?? '—'} · retries {err.retry_count}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>

          <div className="font-mono text-[10px] text-text-tertiary">
            {String(data.timestamp)} · historia {String((data.history as { hours?: number })?.hours ?? 24)}h · źródło{' '}
            {String((data.history as { source?: string })?.source ?? '—')}
          </div>
        </>
      ) : null}
    </section>
  )
}

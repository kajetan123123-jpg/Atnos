import { LoaderCircle } from 'lucide-react'
import type { ProductionJobPayload, ProductionJobStep } from '../lib/api'
import { productionBlocker, productionStepLabel } from '../lib/productionMessages'

const TERMINAL = new Set(['ready', 'script_ready', 'blocked', 'failed', 'cancelled', 'error', 'qa_passed', 'qa_failed'])
const STALE_MS = 90_000

export type ProductionActivityKind = 'working' | 'starting' | 'waiting_worker' | 'stale' | 'done' | 'blocked'

export function productionActivityKind(job: ProductionJobPayload, nowMs = Date.now()): ProductionActivityKind {
  if (job.status === 'blocked' || job.status === 'failed' || job.status === 'error') {
    return 'blocked'
  }
  if (TERMINAL.has(job.status)) {
    return 'done'
  }
  if (job.worker_live) {
    return 'working'
  }
  if (job.status === 'queued' && (!job.steps || job.steps.length === 0)) {
    return 'starting'
  }
  if (['running', 'retrying', 'queued'].includes(job.status)) {
    const updated = job.updated_at ? Date.parse(job.updated_at) : NaN
    if (Number.isFinite(updated) && nowMs - updated > STALE_MS) {
      return 'stale'
    }
    return 'waiting_worker'
  }
  return 'waiting_worker'
}

export function activityHeadline(kind: ProductionActivityKind): string {
  if (kind === 'working') return 'Atnos pracuje teraz'
  if (kind === 'starting') return 'Start produkcji'
  if (kind === 'waiting_worker') return 'Czeka na proces wykonawczy'
  if (kind === 'stale') return 'Brak sygnału — możliwe zawieszenie'
  if (kind === 'done') return 'Zakończone'
  return 'Wymaga decyzji'
}

export function productionActivityStepLabel(job: ProductionJobPayload): string {
  const correction=job.current_correction
  const stage=job.current_step || ''
  if (correction?.status==='registered' && correction.kind==='render' && !TERMINAL.has(job.status)
    && !stage.startsWith('remotion') && !stage.startsWith('semantic') && !['rendering','edit','covers','media_qa','package','cover_quality','ready'].includes(stage)) {
    return /voice|caption|narration|audio/.test(stage)
      ? 'Sprawdzanie zapisanej narracji i napisów przed montażem'
      : 'Sprawdzanie zapisanych etapów przed montażem'
  }
  return productionStepLabel(job.current_step)
}

export function activityDetail(job: ProductionJobPayload, kind: ProductionActivityKind): string {
  const step = productionActivityStepLabel(job)
  if (kind === 'working') {
    return `Aktualnie: ${step}.`
  }
  if (kind === 'starting') {
    return 'Zlecenie zapisane. Worker powinien zaraz zacząć pierwszy etap.'
  }
  if (kind === 'waiting_worker') {
    return `Etap „${step}” jest w toku, ale proces wykonawczy nie zgłasza się jako aktywny. Odśwież lub wznów, jeśli to potrwa dłużej.`
  }
  if (kind === 'stale') {
    return `Ostatni sygnał jest stary. Status: ${job.status}, etap: ${step}. Sprawdź API / wznów produkcję.`
  }
  if (kind === 'done') {
    return step
  }
  return job.error ? productionBlocker(String(job.error)) : step
}

function formatAge(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return 'brak'
  const ts = Date.parse(iso)
  if (!Number.isFinite(ts)) return 'brak'
  const sec = Math.max(0, Math.round((nowMs - ts) / 1000))
  if (sec < 5) return 'przed chwilą'
  if (sec < 60) return `${sec}s temu`
  const min = Math.round(sec / 60)
  if (min < 60) return `${min} min temu`
  return `${Math.round(min / 60)} h temu`
}

function expectedRoadmap(job: ProductionJobPayload): string[] {
  const scope = String(job.request?.production_scope || 'film')
  if (scope === 'script_preview') {
    return ['script_draft', 'script_review', 'script', 'script_ready']
  }
  return ['script', 'asset_planning', 'voice', 'captions', 'edit', 'covers', 'qa', 'package', 'ready']
}

function mergeTimeline(job: ProductionJobPayload): Array<{
  key: string
  label: string
  status: string
  completedAt?: string | null
  reused?: boolean
}> {
  if (job.current_correction) {
    return job.current_correction.stages.map(item => ({
      key: item.stage, label: productionStepLabel(item.stage),
      status: (item.status === 'running' || item.status === 'retrying') && job.worker_live === false ? 'interrupted' : item.status,
      completedAt: item.completed_at, reused: item.reused,
    }))
  }
  const steps = Array.isArray(job.steps) ? job.steps : []
  const byKey = new Map<string, ProductionJobStep>()
  for (const step of steps) {
    byKey.set(step.step, step)
  }
  const keys: string[] = []
  for (const step of steps) {
    if (!keys.includes(step.step)) keys.push(step.step)
  }
  for (const expected of expectedRoadmap(job)) {
    const already = keys.some((key) => key === expected || key.startsWith(`${expected}/`) || key.startsWith(expected))
    if (!already) keys.push(expected)
  }
  const current = job.current_step || ''
  return keys.map((key) => {
    const row = byKey.get(key)
    let status = row?.status || 'pending'
    if (!row && current && (current === key || current.startsWith(`${key}/`) || key.startsWith(`${current.split('/')[0]}`))) {
      if (job.worker_live || job.status === 'running' || job.status === 'retrying') {
        status = 'running'
      }
    }
    if (!row && job.status === 'script_ready' && key === 'script_ready') {
      status = 'done'
    }
    if (!row && job.status === 'ready' && key === 'ready') {
      status = 'done'
    }
    if ((status === 'running' || status === 'retrying') && job.worker_live === false) {
      status = 'interrupted'
    }
    return {
      key,
      label: productionStepLabel(key),
      status,
      completedAt: row?.completed_at ?? null,
    }
  })
}

function tone(kind: ProductionActivityKind): string {
  if (kind === 'working') return 'border-accent-primary/45 bg-accent-primary/10'
  if (kind === 'stale' || kind === 'blocked') return 'border-warning/45 bg-warning/10'
  if (kind === 'done') return 'border-success/40 bg-success/10'
  return 'border-border-default bg-panel'
}

function stepDot(status: string): string {
  if (status === 'done') return 'bg-success'
  if (status === 'running' || status === 'retrying') return 'bg-accent-primary animate-pulse'
  if (status === 'failed') return 'bg-error'
  return 'bg-border-strong'
}

export function ProductionLiveActivityPanel({
  job,
  nowMs = Date.now(),
}: {
  job: ProductionJobPayload
  nowMs?: number
}) {
  const kind = productionActivityKind(job, nowMs)
  const timeline = mergeTimeline(job)
  const doneCount = timeline.filter((item) => item.status === 'done').length

  return (
    <section
      data-testid="production-live-activity"
      aria-live="polite"
      className={`mb-5 border p-4 ${tone(kind)}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-display text-xs uppercase tracking-[0.14em] text-text-primary">
            {kind === 'working' ? <LoaderCircle size={14} className="animate-spin text-accent-primary" /> : null}
            {activityHeadline(kind)}
          </div>
          <p className="mt-2 text-sm leading-6 text-text-secondary">{activityDetail(job, kind)}</p>
          {job.current_correction?.status === 'registered' ? <p className="mt-1 text-xs text-text-secondary">
            {job.current_correction.kind === 'voice_and_render' ? 'Bieżąca korekta głosu i montażu' : 'Bieżące odświeżenie montażu'}
            {' · postęp tej próby, bez wcześniejszych wyników'}
          </p> : null}
          {job.current_correction?.status === 'unverified' ? <p className="mt-1 text-xs text-warning">
            Zakres bieżącej korekty wymaga sprawdzenia. Wcześniejsze wyniki nie potwierdzają jej ukończenia.
          </p> : null}
        </div>
        <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-text-tertiary">
          <div>Worker: {job.worker_live === true ? 'żywy' : job.worker_live === false ? 'nieaktywny' : 'brak danych'}</div>
          <div className="mt-1">Ostatni sygnał: {formatAge(job.updated_at, nowMs)}</div>
          <div className="mt-1">
            AI calls: {job.provider_calls ?? 0}
            {job.local_work_unlimited ? ' · lokalnie bez limitu' : job.max_provider_calls != null ? ` / ${job.max_provider_calls}` : ''}
          </div>
          {job.local_image_action ? <div className="mt-1">
            {job.local_image_action.status === 'verified'
              ? `Ilustracje tej akcji: ${job.local_image_action.used_new_requests}/${job.local_image_action.max_new_requests}`
              : 'Ilustracje tej akcji: integralność niepotwierdzona'}
          </div> : null}
          <div className="mt-1">
            {job.current_correction ? 'Etapy tej korekty' : 'Etapy'}: {doneCount}/{timeline.length}
          </div>
          {job.current_correction?.expensive_stage_attempt_limit ? <div className="mt-1">
            Limit tej akcji: {job.current_correction.expensive_stage_attempt_limit} wykonanie kosztownego etapu
          </div> : null}
        </div>
      </div>

      <ol className="mt-4 space-y-2" data-testid="production-live-timeline">
        {timeline.map((item) => (
          <li key={item.key} className="flex items-start gap-3 text-sm">
            <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${stepDot(item.status)}`} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className={item.status === 'running' ? 'text-text-primary' : 'text-text-secondary'}>
                  {item.label}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-text-tertiary">
                  {item.status === 'done'
                    ? `${item.reused ? 'sprawdzone z cache' : 'done'} · ${formatAge(item.completedAt, nowMs)}`
                    : item.status === 'running'
                      ? 'teraz'
                      : item.status === 'pending'
                        ? 'kolejka'
                        : item.status === 'interrupted'
                          ? 'przerwano'
                        : item.status}
                </span>
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-text-tertiary">{item.key}</div>
            </div>
          </li>
        ))}
      </ol>
      {job.current_correction && job.steps.length > 0 ? <details className="mt-4 text-xs text-text-tertiary">
        <summary className="cursor-pointer">Historia wcześniejszych i pomocniczych etapów</summary>
        <ul className="mt-2 space-y-1">{job.steps.map(step => <li key={step.step}>
          {productionStepLabel(step.step)} · {step.status} · próba {step.attempt ?? '—'}
        </li>)}</ul>
      </details> : null}
    </section>
  )
}

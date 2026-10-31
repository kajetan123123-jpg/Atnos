import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Clapperboard, ExternalLink, LoaderCircle, RefreshCw } from 'lucide-react'
import {
  fetchProductionStudioJob,
  type ProductionJobPayload,
} from '../lib/api'
import { CHAT_STATUS, humanizeChatError } from '../lib/chatStatusCopy'
import { productionBlocker, productionStepLabel } from '../lib/productionMessages'
import {
  activityDetail,
  activityHeadline,
  productionActivityKind,
} from './ProductionLiveActivityPanel'

function statusLabel(status: string) {
  if (status === 'ready') return 'Gotowe'
  if (status === 'script_ready') return 'Scenariusz gotowy'
  if (status === 'blocked') return 'Wymaga sprawdzenia'
  if (status === 'queued') return 'W kolejce'
  if (status === 'running') return 'Produkcja trwa'
  if (status === 'cancelled') return 'Anulowane'
  if (status === 'failed') return 'Błąd'
  return status.replace(/_/g, ' ')
}

function statusTone(status: string, workerLive?: boolean, stale?: boolean) {
  if (status === 'ready' || status === 'script_ready') return 'border-sem-success/40 bg-sem-success/10 text-sem-success'
  if (stale || status === 'blocked' || status === 'failed') {
    return 'border-sem-warning/40 bg-sem-warning/10 text-sem-warning'
  }
  if (status === 'running' || workerLive) {
    return 'border-accent-primary/40 bg-accent-primary/10 text-accent-primary'
  }
  return 'border-border-default bg-elevated text-text-secondary'
}

export function ProductionOrderStatusCard({
  jobId,
  initialTopic,
  initialStatus,
  initialStep,
  initialWorkerLive,
  initialError,
}: {
  jobId: string
  initialTopic?: string | null
  initialStatus?: string | null
  initialStep?: string | null
  initialWorkerLive?: boolean | null
  initialError?: string | null
}) {
  const [job, setJob] = useState<ProductionJobPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())

  const status = job?.status ?? initialStatus ?? 'queued'
  const step = job?.current_step ?? initialStep ?? null
  const workerLive = job?.worker_live ?? Boolean(initialWorkerLive)
  const topic = job?.topic_title ?? initialTopic ?? 'Produkcja rolki'
  const errorText = job?.error ?? initialError ?? null
  const artifact =
    job?.upload_package_path || job?.qa_report_path || job?.artifact_dir || null
  const normalizedJobId = jobId.trim()
  const activityJob: ProductionJobPayload = job ?? {
    id: normalizedJobId,
    account_id: '',
    topic_id: '',
    topic_title: topic,
    status,
    current_step: step,
    worker_live: workerLive,
    error: errorText,
    ready_for_review: false,
    ready_for_private_upload: false,
    steps: [],
    updated_at: null,
  }
  const kind = productionActivityKind(activityJob, nowMs)

  const refresh = useCallback(async () => {
    if (!normalizedJobId) return
    setLoading(true)
    setRefreshError(null)
    try {
      const next = await fetchProductionStudioJob(normalizedJobId)
      setJob(next)
      setNowMs(Date.now())
    } catch (error) {
      setRefreshError(humanizeChatError(error))
    } finally {
      setLoading(false)
    }
  }, [normalizedJobId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (status === 'ready' || status === 'script_ready' || status === 'cancelled' || status === 'failed') {
      return
    }
    const timer = window.setInterval(() => {
      void refresh()
    }, 4000)
    return () => window.clearInterval(timer)
  }, [refresh, status])

  useEffect(() => {
    if (status === 'ready' || status === 'script_ready' || status === 'cancelled' || status === 'failed') {
      return
    }
    const timer = window.setInterval(() => setNowMs(Date.now()), 5000)
    return () => window.clearInterval(timer)
  }, [status])

  if (!normalizedJobId) {
    return null
  }

  return (
    <div
      data-testid="production-order-status-card"
      className="overflow-hidden rounded-md border border-border-default bg-elevated/40"
    >
      <div className="flex items-start justify-between gap-3 border-b border-border-subtle px-3 py-3">
        <div className="min-w-0">
          <div className="label-eyebrow flex items-center gap-2">
            <Clapperboard size={12} />
            Zlecenie produkcji
          </div>
          <div className="mt-1 truncate type-body font-semibold text-text-primary">{topic}</div>
          <div className="mt-1 type-mono-sm text-text-tertiary">ID: {normalizedJobId}</div>
        </div>
        <span className={`shrink-0 rounded-pill border px-2.5 py-1 type-mono-sm ${statusTone(status, workerLive, kind === 'stale')}`}>
          {kind === 'stale' ? 'Brak sygnału' : statusLabel(status)}
        </span>
      </div>

      <div className="space-y-2 px-3 py-3 type-body-sm text-text-secondary">
        <div className="flex items-center gap-2 text-text-primary">
          {kind === 'working' ? <LoaderCircle size={13} className="animate-spin text-accent-primary" /> : null}
          <span className="font-medium">{activityHeadline(kind)}</span>
        </div>
        <p className="leading-6">{activityDetail(activityJob, kind)}</p>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span>
            Etap:{' '}
            <span className="text-text-primary">{productionStepLabel(step)}</span>
          </span>
          <span>
            Worker:{' '}
            <span className={workerLive ? 'text-sem-success' : 'text-text-tertiary'}>
              {workerLive ? 'żywy' : 'nieaktywny'}
            </span>
          </span>
          {job?.provider_calls != null ? (
            <span>
              AI:{' '}
              <span className="text-text-primary">
                {job.provider_calls}
                {job.local_work_unlimited ? ' · lokalnie bez limitu' : job.max_provider_calls != null ? ` / ${job.max_provider_calls}` : ''}
              </span>
            </span>
          ) : null}
        </div>
        {errorText && kind !== 'blocked' ? (
          <div className="rounded-md border border-sem-warning/35 bg-sem-warning/10 px-2.5 py-2 text-sem-warning">
            {productionBlocker(errorText)}
          </div>
        ) : null}
        {artifact ? (
          <div className="truncate text-text-tertiary" title={artifact}>
            Artefakt: <span className="text-text-secondary">{artifact}</span>
          </div>
        ) : null}
        {refreshError ? (
          <div className="text-sem-warning">
            {refreshError === CHAT_STATUS.apiUnavailable
              ? refreshError
              : `Odświeżenie statusu: ${refreshError}`}
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle px-3 py-2.5">
        <Link
          to={`/studio/${encodeURIComponent(normalizedJobId)}`}
          className="inline-flex items-center gap-1.5 rounded-md border border-accent-primary/45 bg-accent-primary/10 px-3 py-1.5 type-body-sm text-accent-primary hover:border-accent-primary"
        >
          Otwórz studio <ExternalLink size={13} />
        </Link>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-md border border-border-default px-3 py-1.5 type-body-sm text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:opacity-50"
        >
          {loading ? <LoaderCircle size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          Odśwież
        </button>
      </div>
    </div>
  )
}

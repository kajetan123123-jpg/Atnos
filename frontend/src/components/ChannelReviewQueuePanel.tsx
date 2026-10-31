import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react'
import { fetchChannelReviewQueue, type ChannelReviewQueuePayload } from '../lib/api'

const READY_STATUSES = new Set(['manual_upload_ready', 'approval_pending'])
const REPAIR_STATUSES = new Set(['qa_failed', 'blocked'])

function formatScore(score: number | null) {
  if (score == null || Number.isNaN(score)) {
    return 'brak'
  }
  return score >= 20 ? `${Math.round(score)}/100` : `${score.toFixed(1)}/10`
}

function statusLabel(status: string) {
  switch (status) {
    case 'manual_upload_ready':
      return 'gotowe do review'
    case 'approval_pending':
      return 'czeka na approval'
    case 'qa_failed':
      return 'QA do naprawy'
    case 'blocked':
      return 'zablokowane'
    case 'private_uploaded':
      return 'wrzucone prywatnie'
    default:
      return status || 'unknown'
  }
}

function statusClassName(status: string) {
  if (READY_STATUSES.has(status)) {
    return 'border-accent-secondary text-accent-secondary'
  }
  if (REPAIR_STATUSES.has(status)) {
    return 'border-warning text-warning'
  }
  return 'border-border-default text-text-secondary'
}

function ReviewQueueItem({
  item,
}: {
  item: ChannelReviewQueuePayload['items'][number]
}) {
  const primaryScore = item.visualQualityScore ?? item.qualityScore

  return (
    <article className="rounded-lg border border-border-default bg-panel px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="type-body-sm font-semibold text-text-primary">{item.topic}</div>
          <div className="mt-1 type-mono-sm text-text-tertiary">
            {item.channelId} / {item.itemId}
          </div>
        </div>
        <span
          className={`rounded-pill border px-3 py-1 type-mono-sm ${statusClassName(item.status)}`}
        >
          {statusLabel(item.status)}
        </span>
      </div>

      <div className="mt-4 grid gap-2 type-body-sm text-text-secondary md:grid-cols-3">
        <div>
          <span className="text-text-tertiary">Visual</span>{' '}
          <span className="text-text-primary">{formatScore(primaryScore)}</span>
        </div>
        <div>
          <span className="text-text-tertiary">Script</span>{' '}
          <span className="text-text-primary">{formatScore(item.scriptQualityScore)}</span>
        </div>
        <div>
          <span className="text-text-tertiary">Render</span>{' '}
          <span className="text-text-primary">
            {item.renderOk === true ? 'OK' : item.renderOk === false ? 'FAILED' : 'brak'}
          </span>
        </div>
      </div>

      {item.finalMp4 ? (
        <div className="mt-4 rounded-md border border-border-default bg-shell px-3 py-2">
          <div className="label-eyebrow">FINAL MP4</div>
          <code className="mt-1 block break-all type-mono-sm text-text-secondary">
            {item.finalMp4}
          </code>
        </div>
      ) : null}

      {item.reviewPath ? (
        <div className="mt-3 type-mono-sm text-text-tertiary">
          Review: <span className="break-all text-text-secondary">{item.reviewPath}</span>
        </div>
      ) : null}
    </article>
  )
}

export function ChannelReviewQueuePanel() {
  const [queue, setQueue] = useState<ChannelReviewQueuePayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadQueue = async () => {
    setLoading(true)
    setError(null)
    try {
      setQueue(await fetchChannelReviewQueue())
    } catch (loadError) {
      const message =
        loadError instanceof Error ? loadError.message : 'Nie udało się pobrać kolejki review.'
      console.error('Channel review queue load failed', loadError)
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadQueue()
  }, [])

  const visibleItems = queue?.items.slice(0, 8) ?? []

  return (
    <section className="shell-panel-strong overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-default px-5 py-4">
        <div>
          <div className="label-eyebrow">YOUTUBE REVIEW QUEUE</div>
          <h2 className="mt-1 type-h3 text-text-primary">Shorty gotowe do oceny</h2>
          <p className="mt-1 max-w-2xl type-body-sm text-text-tertiary">
            Tu widzisz lokalne paczki po QA: gotowe MP4, raporty i elementy wymagające
            naprawy przed uploadem.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void loadQueue()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-md border border-border-default px-3 py-2 type-body-sm text-text-secondary transition duration-fast ease-standard hover:border-accent-primary hover:text-text-primary disabled:cursor-wait disabled:opacity-60"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Odśwież
        </button>
      </div>

      <div className="px-5 py-5">
        {error ? (
          <div className="rounded-lg border border-error bg-error/10 px-4 py-4 text-error">
            <div className="flex items-center gap-2 type-body-sm font-semibold">
              <AlertTriangle size={16} />
              Nie można wczytać kolejki
            </div>
            <div className="mt-2 type-body-sm">{error}</div>
          </div>
        ) : null}

        {!error ? (
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-lg border border-border-default bg-panel px-4 py-4">
              <div className="type-mono-sm text-text-tertiary">TOTAL</div>
              <div className="mt-2 type-h2 text-text-primary">{queue?.itemCount ?? '...'}</div>
            </div>
            <div className="rounded-lg border border-accent-secondary bg-panel px-4 py-4">
              <div className="flex items-center gap-2 type-mono-sm text-accent-secondary">
                <CheckCircle2 size={14} />
                READY
              </div>
              <div className="mt-2 type-h2 text-text-primary">{queue?.readyCount ?? '...'}</div>
            </div>
            <div className="rounded-lg border border-warning bg-panel px-4 py-4">
              <div className="flex items-center gap-2 type-mono-sm text-warning">
                <AlertTriangle size={14} />
                NEEDS REPAIR
              </div>
              <div className="mt-2 type-h2 text-text-primary">{queue?.repairCount ?? '...'}</div>
            </div>
          </div>
        ) : null}

        {!error && queue ? (
          <>
            <div className="mt-5 grid gap-3">
              {visibleItems.length > 0 ? (
                visibleItems.map((item) => <ReviewQueueItem key={item.itemId} item={item} />)
              ) : (
                <div className="rounded-lg border border-dashed border-border-default bg-panel px-4 py-5 type-body-sm text-text-tertiary">
                  Brak shortów w kolejce review. Uruchom daily channel ops, żeby wygenerować
                  kolejną paczkę.
                </div>
              )}
            </div>

            <div className="mt-5 grid gap-2 type-mono-sm text-text-tertiary">
              <div>
                JSON: <span className="break-all text-text-secondary">{queue.jsonPath}</span>
              </div>
              <div>
                Markdown:{' '}
                <span className="break-all text-text-secondary">{queue.markdownPath}</span>
              </div>
            </div>
          </>
        ) : null}
      </div>
    </section>
  )
}

import { useMemo, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import type { BriefingPayload, BriefingSection } from '../types/briefing'
import { formatDateTime } from '../lib/format'

export interface BriefingCardProps {
  payload: BriefingPayload
  onDismiss?: () => void
  onRefresh?: () => void | Promise<void>
  onResolveDecision?: (decisionId: number) => void | Promise<void>
}

function urgencyBadgeClass(urgency: 'high' | 'medium' | 'low') {
  if (urgency === 'high') {
    return 'border-red-500/40 bg-red-500/10 text-red-200'
  }
  if (urgency === 'medium') {
    return 'border-amber-500/40 bg-amber-500/10 text-amber-100'
  }
  return 'border-border-default bg-elevated text-text-tertiary'
}

function urgencyLabel(urgency: 'high' | 'medium' | 'low') {
  if (urgency === 'high') return 'HIGH'
  if (urgency === 'medium') return 'MEDIUM'
  return 'LOW'
}

function resolveDecisionId(section: BriefingSection, metadata: Record<string, unknown>): number | null {
  if (section.kind !== 'decisions') return null
  const raw = metadata.decision_id
  const value = typeof raw === 'number' ? raw : Number(raw)
  return Number.isFinite(value) ? value : null
}

function buildBriefingKey(parts: Array<string | number | null | undefined>): string {
  return parts
    .map((part) => String(part ?? 'none').replace(/:/g, '-'))
    .join(':')
}

export function BriefingCard({
  payload,
  onDismiss,
  onRefresh,
  onResolveDecision,
}: BriefingCardProps) {
  const [dismissed, setDismissed] = useState(false)
  const [resolvingIds, setResolvingIds] = useState<number[]>([])
  const [resolvedIds, setResolvedIds] = useState<number[]>([])

  const visibleSections = useMemo(
    () =>
      payload.sections
        .map((section) => ({
          ...section,
          items: section.items.filter((item) => {
            const decisionId = resolveDecisionId(section, item.metadata)
            return decisionId == null || !resolvedIds.includes(decisionId)
          }),
        }))
        .filter((section) => section.items.length > 0),
    [payload.sections, resolvedIds],
  )

  if (dismissed) {
    return null
  }

  async function handleResolveDecision(decisionId: number) {
    if (!onResolveDecision || resolvingIds.includes(decisionId)) return
    setResolvingIds((current) => [...current, decisionId])
    try {
      await onResolveDecision(decisionId)
      setResolvedIds((current) => [...current, decisionId])
    } finally {
      setResolvingIds((current) => current.filter((item) => item !== decisionId))
    }
  }

  return (
    <div className="rounded-lg border border-cyan-400/30 bg-cyan-400/5 px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="label-eyebrow">BRIEFING</div>
          <div className="mt-2 type-body-sm font-medium text-text-primary">
            Briefing - {formatDateTime(payload.generatedAt)}
          </div>
          <div className="mt-1 type-body-sm text-text-secondary">
            Krotki stan dnia z najwazniejszymi rzeczami do ruszenia.
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              setDismissed(true)
              onDismiss?.()
            }}
            className="rounded-md border border-border-default px-3 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary"
          >
            Zamknij
          </button>
          <button
            type="button"
            disabled={!onRefresh}
            onClick={() => void onRefresh?.()}
            className="inline-flex items-center gap-2 rounded-md border border-border-default px-3 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:text-text-disabled"
          >
            <RefreshCw size={14} />
            Odswiez
          </button>
        </div>
      </div>

      {visibleSections.length === 0 ? (
        <div className="mt-4 rounded-md border border-border-default bg-elevated px-4 py-4 type-body-sm text-text-secondary">
          Brak pozycji.
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {visibleSections.map((section, sectionIndex) => {
            const sectionKey = buildBriefingKey([
              'briefing-section',
              payload.generatedAt,
              section.kind,
              sectionIndex,
              section.title,
              section.summary,
            ])
            return (
            <section
              key={sectionKey}
              className="rounded-md border border-border-default bg-elevated px-4 py-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="label-eyebrow">{section.title}</div>
                  <div className="mt-2 type-body-sm text-text-secondary">{section.summary}</div>
                </div>
                <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
                  {section.items.length} pozycji
                </span>
              </div>

              <div className="mt-4 space-y-3">
                {section.items.map((item, index) => {
                  const decisionId = resolveDecisionId(section, item.metadata)
                  const isResolving = decisionId != null && resolvingIds.includes(decisionId)
                  const itemKey = buildBriefingKey([
                    'briefing-item',
                    payload.generatedAt,
                    section.kind,
                    sectionIndex,
                    index,
                    decisionId,
                    item.actionUrl,
                    item.label,
                    item.detail,
                  ])
                  return (
                    <div
                      key={itemKey}
                      className="rounded-md border border-border-default bg-panel px-3 py-3"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="type-body-sm font-medium text-text-primary">
                            {item.label}
                          </div>
                          <div className="mt-1 whitespace-pre-wrap type-body-sm text-text-secondary">
                            {item.detail}
                          </div>
                        </div>
                        <span
                          className={`rounded-pill border px-3 py-1 type-mono-sm ${urgencyBadgeClass(item.urgency)}`}
                        >
                          {urgencyLabel(item.urgency)}
                        </span>
                      </div>

                      <div className="mt-3 flex flex-wrap gap-2">
                        {item.actionUrl ? (
                          <a
                            href={item.actionUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="rounded-md border border-border-default px-3 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary"
                          >
                            Otworz
                          </a>
                        ) : null}
                        {decisionId != null ? (
                          <button
                            type="button"
                            disabled={!onResolveDecision || isResolving}
                            onClick={() => void handleResolveDecision(decisionId)}
                            className="rounded-md border border-border-default px-3 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:text-text-disabled"
                          >
                            {isResolving ? 'Zamykanie...' : 'Oznacz jako zamkniete'}
                          </button>
                        ) : null}
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>
            )
          })}
        </div>
      )}
    </div>
  )
}

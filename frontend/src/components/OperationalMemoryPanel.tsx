import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchOperationalMemorySnapshot,
  previewOperationalMemoryPromotion,
  promoteOperationalMemorySelectedNote,
  type OperationalMemoryPromotionPreview,
  type OperationalMemoryDecisionEntry,
  type OperationalMemorySnapshot,
  type OperationalMemorySourceNote,
  type OperationalMemoryWorkItem,
} from '../lib/api'

function formatDateTime(value: string) {
  if (!value) return 'brak'
  const timestamp = Date.parse(value)
  if (Number.isNaN(timestamp)) return value
  return new Date(timestamp).toLocaleString('pl-PL')
}

function priorityClass(priority: OperationalMemoryWorkItem['priority']) {
  if (priority === 'P0') {
    return 'border-sem-warning/40 text-sem-warning'
  }
  if (priority === 'P1') {
    return 'border-accent-primary/40 text-accent-primary'
  }
  return 'border-border-strong text-text-tertiary'
}

function workItemStatusClass(status: OperationalMemoryWorkItem['status']) {
  if (status === 'active') {
    return 'border-sem-success/35 text-sem-success'
  }
  if (status === 'blocked') {
    return 'border-sem-warning/40 text-sem-warning'
  }
  if (status === 'waiting') {
    return 'border-accent-primary/35 text-accent-primary'
  }
  return 'border-border-default text-text-tertiary'
}

function workItemReviewStateClass(reviewState: OperationalMemoryWorkItem['reviewState']) {
  if (reviewState === 'in_review') {
    return 'border-accent-primary/40 text-accent-primary'
  }
  if (reviewState === 'reviewed') {
    return 'border-sem-success/35 text-sem-success'
  }
  return 'border-border-strong text-text-tertiary'
}

function workItemReviewStateLabel(reviewState: OperationalMemoryWorkItem['reviewState']) {
  if (reviewState === 'in_review') {
    return 'in review'
  }
  if (reviewState === 'reviewed') {
    return 'reviewed'
  }
  return 'unreviewed'
}

function decisionStatusClass(status: OperationalMemoryDecisionEntry['status']) {
  if (status === 'resolved') {
    return 'border-sem-success/35 text-sem-success'
  }
  if (status === 'expired') {
    return 'border-sem-warning/40 text-sem-warning'
  }
  return 'border-border-strong text-text-tertiary'
}

function noteSeverityClass(severity: OperationalMemorySourceNote['severity']) {
  if (severity === 'P0') {
    return 'border-sem-warning/40 text-sem-warning'
  }
  if (severity === 'P1') {
    return 'border-accent-primary/40 text-accent-primary'
  }
  return 'border-border-strong text-text-tertiary'
}

function noteStatusClass(status: OperationalMemorySourceNote['status']) {
  if (status === 'selected') {
    return 'border-accent-primary/45 text-accent-primary'
  }
  if (status === 'closed') {
    return 'border-border-default text-text-tertiary opacity-70'
  }
  return 'border-border-strong text-text-tertiary'
}

function MetricCard({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="rounded-lg border border-border-default bg-panel px-4 py-4">
      <div className="label-eyebrow">{label}</div>
      <div className="mt-2 type-h3 text-text-primary">{value}</div>
    </div>
  )
}

function formatDecisionCount(count: number) {
  if (count === 1) return '1 aktywna decyzja projektowa'
  if (count >= 2 && count <= 4) return `${count} aktywne decyzje projektowe`
  return `${count} aktywnych decyzji projektowych`
}

function WorkItemCard({
  item,
}: {
  item: OperationalMemoryWorkItem
}) {
  const [decisionHistoryOpen, setDecisionHistoryOpen] = useState(false)
  const recentDecisionEntries = item.decisionContext?.recentEntries ?? []
  const hasDecisionHistory = recentDecisionEntries.length > 0

  return (
    <article
      className="rounded-xl border border-border-default bg-elevated px-4 py-4"
      data-testid={`operational-memory-work-item-${item.id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="type-body-sm font-semibold text-text-primary">{item.title}</div>
          <div className="mt-1 type-mono-sm text-text-tertiary">
            updated {formatDateTime(item.updatedAt)}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className={`rounded-pill border px-3 py-1 type-mono-sm ${priorityClass(item.priority)}`}>
            {item.priority}
          </span>
          <span
            className={`rounded-pill border px-3 py-1 type-mono-sm ${workItemReviewStateClass(item.reviewState)}`}
          >
            {workItemReviewStateLabel(item.reviewState)}
          </span>
          <span className={`rounded-pill border px-3 py-1 type-mono-sm ${workItemStatusClass(item.status)}`}>
            {item.status}
          </span>
        </div>
      </div>

      <div className="mt-3 type-body-sm text-text-secondary">{item.summary}</div>

      {item.nextStep ? (
        <div className="mt-4 rounded-lg border border-border-default bg-panel px-3 py-3">
          <div className="label-eyebrow">Next step</div>
          <div className="mt-2 type-body-sm text-text-primary">{item.nextStep}</div>
        </div>
      ) : null}

      {item.blockedReason ? (
        <div className="mt-4 rounded-lg border border-sem-warning/30 bg-sem-warning/10 px-3 py-3">
          <div className="label-eyebrow">Blocked reason</div>
          <div className="mt-2 type-body-sm text-text-secondary">{item.blockedReason}</div>
        </div>
      ) : null}

      {item.sourceNote ? (
        <div className="mt-4 rounded-lg border border-accent-primary/20 bg-panel px-3 py-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="label-eyebrow">Source note context</div>
            <div className="flex flex-wrap gap-2">
              <span
                className={`rounded-pill border px-3 py-1 type-mono-sm ${noteSeverityClass(item.sourceNote.severity)}`}
              >
                {item.sourceNote.severity}
              </span>
              <span
                className={`rounded-pill border px-3 py-1 type-mono-sm ${noteStatusClass(item.sourceNote.status)}`}
              >
                {item.sourceNote.status}
              </span>
            </div>
          </div>
          <div className="mt-2 type-body-sm text-text-primary">{item.sourceNote.friction}</div>
          <div className="mt-3 flex flex-wrap gap-3 type-mono-sm text-text-tertiary">
            <span>{item.sourceNote.source}</span>
            <span>{item.sourceNote.view}</span>
            <span>{item.sourceNote.decision}</span>
          </div>
          {item.sourceNote.proposedFix ? (
            <div className="mt-3 type-body-sm text-text-secondary">{item.sourceNote.proposedFix}</div>
          ) : null}
        </div>
      ) : null}

      {item.decisionContext ? (
        <div className="mt-4 rounded-lg border border-border-default bg-panel px-3 py-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="label-eyebrow">Decision context</div>
            {hasDecisionHistory ? (
              <button
                type="button"
                onClick={() => setDecisionHistoryOpen((value) => !value)}
                className="rounded-md border border-border-strong px-3 py-1.5 text-sm text-text-secondary transition hover:border-accent-primary/35 hover:text-accent-primary"
              >
                {decisionHistoryOpen ? 'Ukryj decyzje' : 'Pokaz decyzje'}
              </button>
            ) : null}
          </div>
          <div className="mt-2 type-body-sm text-text-primary">
            {formatDecisionCount(item.decisionContext.activeCount)}
          </div>
          {item.decisionContext.latestSummary ? (
            <div className="mt-2 type-body-sm text-text-secondary">
              {item.decisionContext.latestSummary}
            </div>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-3 type-mono-sm text-text-tertiary">
            <span>{item.decisionContext.latestKind ?? 'brak typu'}</span>
            <span>
              due {item.decisionContext.latestDueAt ? formatDateTime(item.decisionContext.latestDueAt) : 'brak'}
            </span>
          </div>

          {decisionHistoryOpen && hasDecisionHistory ? (
            <div
              className="mt-4 space-y-3 rounded-lg border border-accent-primary/20 bg-elevated px-3 py-3"
              data-testid={`operational-memory-decision-history-${item.id}`}
            >
              <div className="label-eyebrow">Recent decisions</div>
              {recentDecisionEntries.map((entry) => (
                <div
                  key={entry.id}
                  className="rounded-lg border border-border-default bg-panel px-3 py-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 type-body-sm text-text-primary">{entry.summary}</div>
                    <span
                      className={`rounded-pill border px-3 py-1 type-mono-sm ${decisionStatusClass(entry.status)}`}
                    >
                      {entry.status}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-3 type-mono-sm text-text-tertiary">
                    <span>{entry.kind}</span>
                    <span>made {formatDateTime(entry.madeAt)}</span>
                    <span>due {entry.dueAt ? formatDateTime(entry.dueAt) : 'brak'}</span>
                  </div>
                  {entry.resolutionSummary ? (
                    <div className="mt-3 type-body-sm text-text-secondary">
                      {entry.resolutionSummary}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-4 type-mono-sm text-text-tertiary">
        <span>project {item.projectId ?? 'brak'}</span>
        <span>source note {item.sourceNoteId ?? 'brak'}</span>
      </div>
    </article>
  )
}

function DecisionCard({
  entry,
}: {
  entry: OperationalMemoryDecisionEntry
}) {
  return (
    <article className="rounded-xl border border-border-default bg-elevated px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="type-body-sm font-semibold text-text-primary">{entry.summary}</div>
          <div className="mt-1 type-mono-sm text-text-tertiary">{entry.kind}</div>
        </div>
        <span className={`rounded-pill border px-3 py-1 type-mono-sm ${decisionStatusClass(entry.status)}`}>
          {entry.status}
        </span>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
          <div className="label-eyebrow">Made</div>
          <div className="mt-2 type-body-sm text-text-primary">{formatDateTime(entry.madeAt)}</div>
        </div>
        <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
          <div className="label-eyebrow">Due</div>
          <div className="mt-2 type-body-sm text-text-primary">
            {entry.dueAt ? formatDateTime(entry.dueAt) : 'brak'}
          </div>
        </div>
      </div>

      {entry.resolutionSummary ? (
        <div className="mt-3 rounded-lg border border-border-default bg-panel px-3 py-3 type-body-sm text-text-secondary">
          {entry.resolutionSummary}
        </div>
      ) : null}
    </article>
  )
}

export function OperationalMemoryPanel({
  projectId,
  projectName,
}: {
  projectId: number | null
  projectName: string | null
}) {
  const [snapshot, setSnapshot] = useState<OperationalMemorySnapshot | null>(null)
  const [promotionPreview, setPromotionPreview] = useState<OperationalMemoryPromotionPreview | null>(
    null,
  )
  const [loading, setLoading] = useState(projectId != null)
  const [error, setError] = useState<string | null>(null)
  const [promotionError, setPromotionError] = useState<string | null>(null)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [createPending, setCreatePending] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [createSuccess, setCreateSuccess] = useState<string | null>(null)

  const loadPanelData = useCallback(async () => {
    if (projectId == null) {
      setSnapshot(null)
      setPromotionPreview(null)
      setError(null)
      setPromotionError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    setError(null)
    setPromotionError(null)

    const [snapshotResult, promotionResult] = await Promise.allSettled([
      fetchOperationalMemorySnapshot({ projectId }),
      previewOperationalMemoryPromotion({ projectId }),
    ])

    if (snapshotResult.status === 'fulfilled') {
      setSnapshot(snapshotResult.value)
    } else {
      const message =
        snapshotResult.reason instanceof Error
          ? snapshotResult.reason.message
          : 'Nie udalo sie wczytac Operational Memory.'
      console.error('Operational memory load failed', snapshotResult.reason)
      setError(message)
    }

    if (promotionResult.status === 'fulfilled') {
      setPromotionPreview(promotionResult.value)
    } else {
      const message =
        promotionResult.reason instanceof Error
          ? promotionResult.reason.message
          : 'Nie udalo sie przygotowac promotion preview.'
      console.error('Operational memory promotion preview failed', promotionResult.reason)
      setPromotionError(message)
      setPromotionPreview(null)
    }

    setLoading(false)
  }, [projectId])

  useEffect(() => {
    setReviewOpen(false)
    setCreateError(null)
    setCreateSuccess(null)
    void loadPanelData()
  }, [loadPanelData])

  const counts = useMemo(() => {
    const workItems = snapshot?.workItems ?? []
    return {
      workItems: workItems.length,
      active: workItems.filter((item) => item.status === 'active').length,
      blocked: workItems.filter((item) => item.status === 'blocked').length,
      decisions: snapshot?.activeDecisions.length ?? 0,
      reviewUnreviewed: workItems.filter((item) => item.reviewState === 'unreviewed').length,
      reviewInReview: workItems.filter((item) => item.reviewState === 'in_review').length,
      reviewReviewed: workItems.filter((item) => item.reviewState === 'reviewed').length,
    }
  }, [snapshot])

  const handlePrepareReview = () => {
    setCreateError(null)
    setCreateSuccess(null)
    setReviewOpen(true)
  }

  const handleCancelReview = () => {
    setCreateError(null)
    setReviewOpen(false)
  }

  const handleConfirmCreate = async () => {
    if (projectId == null || promotionPreview?.note == null) {
      return
    }

    setCreatePending(true)
    setCreateError(null)
    setCreateSuccess(null)
    try {
      await promoteOperationalMemorySelectedNote({
        projectId,
        noteId: promotionPreview.note.id,
      })
      setCreateSuccess('Work item zapisany w Operational Memory.')
      setReviewOpen(false)
      await loadPanelData()
    } catch (createFailure) {
      const message =
        createFailure instanceof Error
          ? createFailure.message
          : 'Nie udalo sie utworzyc work itemu.'
      console.error('Operational memory promotion create failed', createFailure)
      setCreateError(message)
    } finally {
      setCreatePending(false)
    }
  }

  if (projectId == null) {
    return (
      <div
        className="rounded-xl border border-dashed border-border-default bg-elevated px-4 py-5 type-body-sm text-text-secondary"
        data-testid="operational-memory-panel"
      >
        Wybierz aktywny projekt w Chacie, zeby podejrzec jego stan pracy i decyzje w
        Operational Memory.
      </div>
    )
  }

  if (error) {
    return (
      <div
        className="rounded-xl border border-sem-warning/40 bg-sem-warning/10 px-4 py-5"
        data-testid="operational-memory-panel"
      >
        <div className="type-body-sm font-semibold text-sem-warning">
          Nie mozna wczytac Operational Memory
        </div>
        <div className="mt-2 type-body-sm text-text-secondary">{error}</div>
      </div>
    )
  }

  if (loading && !snapshot) {
    return (
      <div
        className="rounded-xl border border-border-default bg-elevated px-4 py-5 type-body-sm text-text-secondary"
        data-testid="operational-memory-panel"
      >
        Wczytuje stan pracy dla wybranego projektu...
      </div>
    )
  }

  const workItems = snapshot?.workItems ?? []
  const activeDecisions = snapshot?.activeDecisions ?? []
  const sourceNote = snapshot?.sourceNote ?? null
  const selectedNote = promotionPreview?.note ?? null
  const promotionDraft = promotionPreview?.workItemDraft ?? null
  const showPromotionNoteCard = selectedNote != null
  const promotionEligible = Boolean(promotionPreview?.eligible && promotionDraft && selectedNote)
  const promotionReason = promotionPreview?.reason ?? null

  return (
    <div className="space-y-4" data-testid="operational-memory-panel">
      <div className="rounded-xl border border-border-default bg-elevated px-4 py-4">
        <div className="label-eyebrow">Project scope</div>
        <div className="mt-2 type-body text-text-primary">
          {projectName ?? `Projekt #${projectId}`}
        </div>
        <div className="mt-2 type-body-sm text-text-secondary">
          To jest spokojny stan pracy dla aktualnego projektu. Tylko review i pamiec
          operacyjna: bez wykonania, bez connectorow, bez autopilota.
        </div>
      </div>

      {createSuccess ? (
        <div className="rounded-xl border border-sem-success/35 bg-sem-success/10 px-4 py-4 type-body-sm text-sem-success">
          {createSuccess}
        </div>
      ) : null}

      {promotionError ? (
        <div className="rounded-xl border border-sem-warning/40 bg-sem-warning/10 px-4 py-4">
          <div className="type-body-sm font-semibold text-sem-warning">
            Nie mozna przygotowac promotion preview
          </div>
          <div className="mt-2 type-body-sm text-text-secondary">{promotionError}</div>
        </div>
      ) : showPromotionNoteCard ? (
        <div
          className="rounded-xl border border-accent-primary/30 bg-panel px-4 py-4"
          data-testid="operational-memory-promotion"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="label-eyebrow">Selected note</div>
              <div className="mt-2 type-body text-text-primary">{selectedNote.friction}</div>
              <div className="mt-2 type-body-sm text-text-secondary">
                Tylko planowanie. Ta notatka moze stac sie kandydatem do work itemu, ale
                Atnos niczego nie wykonuje bez Twojej zgody.
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <span
                className={`rounded-pill border px-3 py-1 type-mono-sm ${noteSeverityClass(selectedNote.severity)}`}
              >
                {selectedNote.severity}
              </span>
              <span
                className={`rounded-pill border px-3 py-1 type-mono-sm ${noteStatusClass(selectedNote.status)}`}
              >
                {selectedNote.status}
              </span>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap gap-4 type-mono-sm text-text-tertiary">
            <span>{selectedNote.source}</span>
            <span>{selectedNote.view}</span>
            <span>{selectedNote.decision}</span>
          </div>

          {selectedNote.proposedFix ? (
            <div className="mt-3 rounded-lg border border-border-default bg-elevated px-3 py-3">
              <div className="label-eyebrow">Proposed fix</div>
              <div className="mt-2 type-body-sm text-text-secondary">
                {selectedNote.proposedFix}
              </div>
            </div>
          ) : null}

          {promotionReason && !promotionEligible ? (
            <div className="mt-3 rounded-lg border border-border-default bg-elevated px-3 py-3 type-body-sm text-text-secondary">
              {promotionReason}
            </div>
          ) : null}

          {promotionEligible && !reviewOpen ? (
            <div className="mt-4">
              <button
                type="button"
                onClick={handlePrepareReview}
                className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary transition hover:bg-accent-primary/10"
              >
                Przygotuj work item
              </button>
            </div>
          ) : null}

          {promotionEligible && reviewOpen && promotionDraft ? (
            <div className="mt-4 rounded-xl border border-accent-primary/25 bg-elevated px-4 py-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="label-eyebrow">Work item preview</div>
                  <div className="mt-2 type-body-sm text-text-secondary">
                    Review candidate. Nadal bez wykonania, bez connectorow i bez autopilota.
                  </div>
                </div>
                <span className={`rounded-pill border px-3 py-1 type-mono-sm ${priorityClass(promotionDraft.priority)}`}>
                  {promotionDraft.priority}
                </span>
              </div>

              <div className="mt-4 grid gap-3 lg:grid-cols-2">
                <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
                  <div className="label-eyebrow">Title</div>
                  <div className="mt-2 type-body-sm text-text-primary">
                    {promotionDraft.title}
                  </div>
                </div>
                <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
                  <div className="label-eyebrow">Priority</div>
                  <div className="mt-2 type-body-sm text-text-primary">
                    {promotionDraft.priority}
                  </div>
                </div>
              </div>

              <div className="mt-3 rounded-lg border border-border-default bg-panel px-3 py-3">
                <div className="label-eyebrow">Summary</div>
                <div className="mt-2 type-body-sm text-text-primary">
                  {promotionDraft.summary}
                </div>
              </div>

              <div className="mt-3 rounded-lg border border-border-default bg-panel px-3 py-3">
                <div className="label-eyebrow">Next step</div>
                <div className="mt-2 type-body-sm text-text-primary">
                  {promotionDraft.nextStep ?? 'brak'}
                </div>
              </div>

              {promotionPreview?.warnings.length ? (
                <div className="mt-3 rounded-lg border border-sem-warning/40 bg-sem-warning/10 px-3 py-3">
                  <div className="label-eyebrow">Warnings</div>
                  <div className="mt-2 space-y-1 type-body-sm text-text-secondary">
                    {promotionPreview.warnings.map((warning) => (
                      <div key={warning}>- {warning}</div>
                    ))}
                  </div>
                </div>
              ) : null}

              {createError ? (
                <div className="mt-3 rounded-lg border border-sem-warning/40 bg-sem-warning/10 px-3 py-3 type-body-sm text-text-secondary">
                  {createError}
                </div>
              ) : null}

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleConfirmCreate}
                  disabled={createPending}
                  className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary transition hover:bg-accent-primary/10 disabled:border-border-default disabled:text-text-disabled"
                >
                  {createPending ? 'Tworze...' : 'Potwierdz utworzenie'}
                </button>
                <button
                  type="button"
                  onClick={handleCancelReview}
                  disabled={createPending}
                  className="rounded-md border border-border-strong px-3 py-2 text-sm text-text-secondary disabled:text-text-disabled"
                >
                  Anuluj
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-border-default bg-elevated px-4 py-5 type-body-sm text-text-secondary">
          {promotionReason ??
            'Brak wybranej notatki do promowania. Najpierw wskaz jedna note w Improvement Notes.'}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Work items" value={String(counts.workItems)} />
        <MetricCard label="Active" value={String(counts.active)} />
        <MetricCard label="Blocked" value={String(counts.blocked)} />
        <MetricCard label="Active decisions" value={String(counts.decisions)} />
      </div>

      <div
        className="rounded-xl border border-border-default bg-elevated px-4 py-4"
        data-testid="operational-memory-review-summary"
      >
        <div className="label-eyebrow">Review state summary</div>
        <div className="mt-2 type-body-sm text-text-secondary">
          Szybki podglad tego, ile work itemow czeka jeszcze na review, ile jest aktualnie
          przegladanych i ile zostalo juz domknietych od strony review.
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <MetricCard label="Unreviewed" value={String(counts.reviewUnreviewed)} />
          <MetricCard label="In review" value={String(counts.reviewInReview)} />
          <MetricCard label="Reviewed" value={String(counts.reviewReviewed)} />
        </div>
      </div>

      {sourceNote ? (
        <div className="rounded-xl border border-accent-primary/30 bg-panel px-4 py-4">
          <div className="label-eyebrow">Source note</div>
          <div className="mt-2 type-body text-text-primary">{sourceNote.friction}</div>
          <div className="mt-3 flex flex-wrap gap-2 type-mono-sm text-text-tertiary">
            <span>{sourceNote.severity}</span>
            <span>{sourceNote.source}</span>
            <span>{sourceNote.view}</span>
            <span>{sourceNote.status}</span>
          </div>
          {sourceNote.proposedFix ? (
            <div className="mt-3 rounded-lg border border-border-default bg-elevated px-3 py-3 type-body-sm text-text-secondary">
              {sourceNote.proposedFix}
            </div>
          ) : null}
        </div>
      ) : null}

      {workItems.length === 0 && activeDecisions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border-default bg-elevated px-4 py-5 type-body-sm text-text-secondary">
          Brak zapisanego stanu pracy dla tego projektu. Operational Memory wypelni
          sie, gdy wybrane review notes przejda do work itemow lub decyzji projektowych.
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[1.15fr_0.95fr]">
          <div className="space-y-3">
            <div className="label-eyebrow">Work items</div>
            {workItems.length ? (
              workItems.map((item) => <WorkItemCard key={item.id} item={item} />)
            ) : (
              <div className="rounded-xl border border-dashed border-border-default bg-elevated px-4 py-5 type-body-sm text-text-secondary">
                Dla tego projektu nie ma jeszcze work itemow.
              </div>
            )}
          </div>

          <div className="space-y-3">
            <div className="label-eyebrow">Active decisions</div>
            {activeDecisions.length ? (
              activeDecisions.map((entry) => <DecisionCard key={entry.id} entry={entry} />)
            ) : (
              <div className="rounded-xl border border-dashed border-border-default bg-elevated px-4 py-5 type-body-sm text-text-secondary">
                Brak aktywnych decyzji dla tego projektu.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

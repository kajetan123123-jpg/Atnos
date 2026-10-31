import type { AgentActionPreview } from '../types/system'

interface PendingApprovalsPanelProps {
  approvals: AgentActionPreview[]
  pendingRunId: number | null
  onApprove: (runId: number) => void
  onReject: (runId: number) => void
}

export function PendingApprovalsPanel({
  approvals,
  pendingRunId,
  onApprove,
  onReject,
}: PendingApprovalsPanelProps) {
  return (
    <section className="shell-bento rounded-lg p-5" data-testid="pending-approvals-panel">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="type-label text-text-tertiary">PENDING APPROVALS</div>
          <h2 className="mt-2 type-h3 text-text-primary">Decyzje wymagajace czlowieka</h2>
        </div>
        <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-secondary">
          {approvals.length}
        </span>
      </div>

      <div className="mt-4 space-y-3">
        {approvals.length ? (
          approvals.map((approval) => {
            const pending = pendingRunId === approval.executionRunId
            return (
              <div key={approval.executionRunId} className="rounded-lg border border-border-default bg-elevated/60 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="type-body text-text-primary">{approval.title}</div>
                    <div className="mt-1 type-body-sm text-text-secondary">{approval.summary}</div>
                    {approval.impactSummary ? (
                      <div className="mt-2 type-body-sm text-warning">{approval.impactSummary}</div>
                    ) : null}
                  </div>
                  <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
                    {approval.kind}
                  </span>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => onApprove(approval.executionRunId)}
                    className="rounded-pill border border-accent-primary px-4 py-2 text-xs text-accent-primary transition hover:bg-accent-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {pending ? 'Przetwarzam...' : 'Approve'}
                  </button>
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => onReject(approval.executionRunId)}
                    className="rounded-pill border border-sem-error/50 px-4 py-2 text-xs text-sem-error transition hover:bg-sem-error/10 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Reject
                  </button>
                </div>
              </div>
            )
          })
        ) : (
          <div className="rounded-lg border border-border-default bg-elevated/60 p-4 type-body-sm text-text-tertiary">
            Brak oczekujacych akceptacji.
          </div>
        )}
      </div>
    </section>
  )
}

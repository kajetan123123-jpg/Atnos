import type { AgentArtifactSummary } from '../types/system'

interface RecentArtifactsPanelProps {
  artifacts: AgentArtifactSummary[]
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function RecentArtifactsPanel({ artifacts }: RecentArtifactsPanelProps) {
  return (
    <section className="shell-bento rounded-lg p-5" data-testid="recent-artifacts-panel">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="type-label text-text-tertiary">RECENT ARTIFACTS</div>
          <h2 className="mt-2 type-h3 text-text-primary">Ostatnie wygenerowane pliki</h2>
        </div>
        <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-secondary">
          {artifacts.length}
        </span>
      </div>

      <div className="mt-4 space-y-2">
        {artifacts.length ? (
          artifacts.map((artifact) => (
            <div
              key={`${artifact.path}-${artifact.updatedAt}`}
              className="rounded-md border border-border-default bg-elevated/60 px-3 py-2"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate type-body-sm text-text-primary">{artifact.filename}</div>
                  <div className="mt-1 break-all text-[11px] text-text-tertiary">{artifact.storagePath}</div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="type-mono-sm text-text-secondary">{artifact.extension || 'file'}</div>
                  <div className="mt-1 text-[11px] text-text-tertiary">{formatSize(artifact.sizeBytes)}</div>
                </div>
              </div>
            </div>
          ))
        ) : (
          <div className="rounded-lg border border-border-default bg-elevated/60 p-4 type-body-sm text-text-tertiary">
            Brak nowych artefaktow w katalogach Atnosa.
          </div>
        )}
      </div>
    </section>
  )
}

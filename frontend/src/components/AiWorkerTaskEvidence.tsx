import type { AiWorkerTask } from '../lib/api'

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function textValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function boolLabel(value: unknown): string {
  if (value === true) return 'tak'
  if (value === false) return 'nie'
  return 'brak danych'
}

function formatTimestamp(value: string | null): string {
  if (!value) return 'brak danych'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('pl-PL')
}

function EvidenceValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border border-border-default bg-panel/60 p-2">
      <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-text-tertiary">{label}</div>
      <div className="mt-1 break-words font-mono text-[10px] leading-5 text-text-secondary">{value}</div>
    </div>
  )
}

export function AiWorkerTaskEvidence({ task }: { task: AiWorkerTask }) {
  const result = asRecord(task.resultJson)
  const verification = asRecord(result?.verification)
  const rollback = asRecord(result?.rollback)
  const changedPaths = asStrings(verification?.changed_paths).length
    ? asStrings(verification?.changed_paths)
    : asStrings(result?.changed_paths)
  const diffPath = textValue(result?.diff_path)
  const diffSha256 = textValue(result?.diff_sha256)
  const verificationCommand = Array.isArray(verification?.command)
    ? verification.command.filter((item): item is string => typeof item === 'string').join(' ')
    : null
  const verificationOutput = textValue(verification?.stdout_tail)
  const proposalState = textValue(result?.proposal_state)
  const rollbackStatus = textValue(rollback?.status)
  const hasStructuredEvidence = Boolean(
    result || task.runs.length || changedPaths.length || diffPath || diffSha256 || verification || rollback,
  )

  return (
    <details
      data-testid={`ai-task-evidence-${task.id}`}
      className="mt-3 border-t border-border-default pt-3"
    >
      <summary className="cursor-pointer select-none font-display text-[10px] uppercase tracking-[0.14em] text-accent-primary">
        Evidence Log
      </summary>
      <div className="mt-3 space-y-3">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <EvidenceValue label="Worker" value={task.workerKey ?? 'nieprzypisany'} />
          <EvidenceValue label="Cykl życia" value={`${task.status} / ${task.verificationStatus}`} />
          <EvidenceValue label="Start" value={formatTimestamp(task.startedAt)} />
          <EvidenceValue label="Koniec" value={formatTimestamp(task.completedAt)} />
        </div>

        {task.runs.length ? (
          <div>
            <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-text-tertiary">Przebieg wykonania</div>
            <div className="mt-2 space-y-1">
              {task.runs.map((run) => (
                <div key={run.id} className="flex flex-wrap justify-between gap-2 border border-border-default bg-panel/60 px-2 py-1 font-mono text-[10px] text-text-secondary">
                  <span>#{run.id} / {run.workerKey} / {run.status}</span>
                  <span>{run.durationMs === null ? 'czas: brak danych' : `czas: ${run.durationMs} ms`}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {(() => {
          const used = Array.isArray(result?.capabilities_used)
            ? result.capabilities_used
            : Array.isArray((task as { capabilitiesUsed?: unknown }).capabilitiesUsed)
              ? (task as { capabilitiesUsed?: unknown[] }).capabilitiesUsed
              : null
          if (!Array.isArray(used) || used.length === 0) return null
          return (
            <div data-testid={`ai-task-capabilities-${task.id}`}>
              <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-text-tertiary">Capabilities used</div>
              <ul className="mt-2 space-y-1">
                {used.map((item, index) => {
                  const row = asRecord(item)
                  const capability = textValue(row?.capability) ?? 'UNKNOWN'
                  const status = textValue(row?.status) ?? 'UNKNOWN'
                  return (
                    <li
                      key={`${capability}-${index}`}
                      className="border border-border-default bg-panel/60 px-2 py-1 font-mono text-[10px] text-text-secondary"
                    >
                      {capability} — {status}
                    </li>
                  )
                })}
              </ul>
            </div>
          )
        })()}

        {changedPaths.length ? (
          <div>
            <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-text-tertiary">Zmienione pliki</div>
            <ul className="mt-2 space-y-1">
              {changedPaths.map((path) => (
                <li key={path} className="break-all border border-border-default bg-panel/60 px-2 py-1 font-mono text-[10px] text-text-secondary">{path}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {verification ? (
          <div className="grid gap-2 sm:grid-cols-2">
            <EvidenceValue label="Niezależna weryfikacja" value={`${textValue(verification.status) ?? 'brak statusu'}${textValue(verification.reason) ? `: ${textValue(verification.reason)}` : ''}`} />
            <EvidenceValue label="Polecenie testowe" value={verificationCommand ?? 'brak danych'} />
            {verificationOutput ? <EvidenceValue label="Wynik testu" value={verificationOutput} /> : null}
          </div>
        ) : null}

        {diffPath || diffSha256 ? (
          <div className="grid gap-2 sm:grid-cols-2">
            <EvidenceValue label="Artefakt diff" value={diffPath ?? 'brak danych'} />
            <EvidenceValue label="SHA-256" value={diffSha256 ?? 'brak danych'} />
          </div>
        ) : null}

        {rollback ? (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <EvidenceValue label="Stan propozycji" value={proposalState ?? rollbackStatus ?? 'brak danych'} />
            <EvidenceValue label="Główny worktree zmieniony" value={boolLabel(rollback.main_worktree_modified)} />
            <EvidenceValue label="Izolowany worktree usunięty" value={boolLabel(rollback.isolated_worktree_removed)} />
            <EvidenceValue label="Apply zatwierdzony" value={boolLabel(rollback.apply_authorized)} />
          </div>
        ) : null}

        {!hasStructuredEvidence ? (
          <div className="border border-warning/30 bg-warning/5 p-2 text-xs leading-5 text-warning">
            Worker nie zapisał jeszcze strukturalnych dowodów wykonania.
          </div>
        ) : null}
      </div>
    </details>
  )
}

import type { AgentEmergencyStopResult } from '../types/system'

interface EmergencyStopButtonProps {
  pending: boolean
  result: AgentEmergencyStopResult | null
  onClick: () => void
}

export function EmergencyStopButton({ pending, result, onClick }: EmergencyStopButtonProps) {
  return (
    <section className="rounded-lg border border-sem-error/40 bg-sem-error/10 p-5" data-testid="emergency-stop-panel">
      <div className="type-label text-sem-error">EMERGENCY STOP</div>
      <h2 className="mt-2 type-h3 text-text-primary">Zatrzymaj aktywne akcje</h2>
      <p className="mt-2 type-body-sm text-text-secondary">
        Nie kasuje danych. Oznacza aktywne runy jako przerwane i probuje zatrzymac procesy Atnosa na Ubuntu sandboxie.
      </p>
      <button
        type="button"
        disabled={pending}
        onClick={onClick}
        className="mt-4 rounded-pill border border-sem-error px-4 py-2 text-sm text-sem-error transition hover:bg-sem-error/15 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? 'Zatrzymuje...' : 'Emergency stop'}
      </button>
      {result ? (
        <div className="mt-3 rounded-md border border-sem-error/30 bg-panel px-3 py-2 type-body-sm text-text-secondary">
          {result.message || 'Emergency stop wykonany.'} Ubuntu stop: {result.ubuntuStop.ok ? 'ok' : 'problem'}.
        </div>
      ) : null}
    </section>
  )
}

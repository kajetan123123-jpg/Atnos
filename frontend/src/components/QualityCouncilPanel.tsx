import { useEffect, useState } from 'react'
import {
  fetchQualityCouncilReports,
  fetchQualityCouncilStatus,
  runQualityCouncil,
} from '../lib/api'
import type { QualityCouncilReport, QualityCouncilRun, QualityCouncilStatusPayload } from '../lib/api'

const severityClass: Record<string, string> = {
  critical: 'border-error/50 bg-error/10 text-error',
  high: 'border-error/40 bg-error/5 text-error',
  medium: 'border-warning/40 bg-warning/10 text-warning',
  low: 'border-border-default bg-void text-text-secondary',
}

function reportStatus(report: QualityCouncilReport) {
  if (report.problems.some((item) => item.severity === 'critical')) return 'critical'
  if (report.problems.some((item) => item.severity === 'high')) return 'high'
  if (report.problems.some((item) => item.severity === 'medium')) return 'medium'
  return 'low'
}

export function QualityCouncilPanel() {
  const [status, setStatus] = useState<QualityCouncilStatusPayload | null>(null)
  const [runs, setRuns] = useState<QualityCouncilRun[]>([])
  const [selected, setSelected] = useState<QualityCouncilReport | null>(null)
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')

  const refresh = async () => {
    const [statusResult, runsResult] = await Promise.allSettled([
      fetchQualityCouncilStatus(),
      fetchQualityCouncilReports(),
    ])
    if (statusResult.status === 'fulfilled') setStatus(statusResult.value)
    if (runsResult.status === 'fulfilled') setRuns(runsResult.value)
    const errors = [statusResult, runsResult]
      .filter((item): item is PromiseRejectedResult => item.status === 'rejected')
      .map((item) => (item.reason instanceof Error ? item.reason.message : 'Nieznany błąd odczytu Quality Council.'))
    setMessage(errors.join(' '))
  }

  useEffect(() => {
    void refresh()
  }, [])

  const runAudit = async () => {
    setPending(true)
    setMessage('')
    try {
      const result = await runQualityCouncil()
      setRuns((current) => [result, ...current.filter((item) => item.runId !== result.runId)])
      setSelected(result.coordinator)
      setMessage(
        result.delivery.sent
          ? 'Raport został wysłany.'
          : `Raport zapisano lokalnie. Gmail: ${result.delivery.status}. ${result.delivery.reason}`,
      )
      await refresh()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Quality Council nie zakończył audytu.')
    } finally {
      setPending(false)
    }
  }

  const latest = runs[0] ?? status?.latestRun ?? null

  return (
    <section className="min-w-0 max-w-full border border-accent-primary/30 bg-accent-primary/5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">Quality Council</div>
          <p className="mt-2 max-w-4xl text-sm leading-6 text-text-secondary">
            Sześć niezależnych raportów: aplikacja/UI, filmy, inwestycje, bezpieczeństwo, handoff Claude oraz handoff Kimi.
            Każdy wynik oddziela dowody od rzeczy niezweryfikowanych.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void refresh()}
            className="border border-border-strong px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary"
          >
            Odśwież
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => void runAudit()}
            className="border border-accent-primary bg-accent-primary/10 px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] text-accent-primary disabled:border-border-default disabled:bg-transparent disabled:text-text-disabled"
          >
            {pending ? 'Audyt trwa...' : 'Uruchom pełny audyt'}
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div className="min-w-0 border border-border-default bg-void p-3">
          <div className="font-mono text-[9px] uppercase text-text-tertiary">Raporty lokalne</div>
          <div className="mt-1 font-display text-sm text-text-primary">{runs.length}</div>
          <div className="mt-1 truncate font-mono text-[10px] text-text-secondary">{status?.reportsDir || 'brak danych'}</div>
        </div>
        <div className="min-w-0 border border-border-default bg-void p-3">
          <div className="font-mono text-[9px] uppercase text-text-tertiary">Gmail raporty</div>
          <div className={['mt-1 font-display text-sm uppercase', status?.gmailDelivery.ready ? 'text-success' : 'text-warning'].join(' ')}>
            {status?.gmailDelivery.ready ? 'gotowy do draftu' : 'zablokowane'}
          </div>
          <div className="mt-1 break-words text-xs leading-5 text-text-secondary">{status?.gmailDelivery.reason || 'Odczyt statusu trwa.'}</div>
        </div>
        <div className="min-w-0 border border-border-default bg-void p-3">
          <div className="font-mono text-[9px] uppercase text-text-tertiary">Ostatni coordinator</div>
          <div className="mt-1 font-display text-sm text-text-primary">{latest?.runId || 'brak uruchomień'}</div>
          <div className="mt-1 text-xs text-text-secondary">{latest?.delivery.status || 'Raport nie istnieje.'}</div>
        </div>
      </div>

      {latest ? (
        <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {[...latest.reports, latest.coordinator].map((report) => {
            const state = reportStatus(report)
            return (
              <button
                type="button"
                key={`${report.key}-${report.markdownPath}`}
                onClick={() => setSelected(report)}
                className={['border p-3 text-left transition hover:border-accent-primary', severityClass[state]].join(' ')}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="font-display text-[11px] uppercase tracking-[0.1em]">{report.title}</span>
                  <span className="font-mono text-[9px] uppercase">{state}</span>
                </div>
                <div className="mt-2 text-xs leading-5 text-text-secondary">{report.summary}</div>
                <div className="mt-2 font-mono text-[9px] uppercase text-text-tertiary">
                  pewność: {report.confidence} / problemy: {report.problems.length}
                </div>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="mt-4 border border-dashed border-border-default bg-void p-4 text-sm leading-6 text-text-secondary">
          Brak raportów. Uruchom audyt, aby zapisać lokalne dowody i przygotować kontrolowane handoffy Claude/Kimi.
        </div>
      )}

      {selected ? (
        <div className="mt-4 border border-border-strong bg-void p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="font-display text-[12px] uppercase tracking-[0.14em] text-text-primary">{selected.title}</div>
              <div className="mt-1 font-mono text-[10px] text-text-tertiary">{selected.markdownPath || 'brak ścieżki raportu'}</div>
            </div>
            <span className="border border-border-default px-2 py-1 font-mono text-[9px] uppercase text-text-secondary">pewność: {selected.confidence}</span>
          </div>
          <p className="mt-3 text-sm leading-6 text-text-secondary">{selected.summary}</p>
          <div className="mt-4 grid gap-3 xl:grid-cols-3">
            <ReportList label="Działa dobrze" items={selected.worksWell} tone="text-success" />
            <ReportList label="Nie działa / problemy" items={selected.notWorking} tone="text-error" />
            <ReportList label="Niezweryfikowane" items={selected.notVerified} tone="text-warning" />
          </div>
          {selected.problems.length ? (
            <div className="mt-4 space-y-2">
              {selected.problems.map((problem) => (
                <div key={`${problem.severity}-${problem.title}`} className={['border p-3', severityClass[problem.severity] ?? severityClass.low].join(' ')}>
                  <div className="font-display text-[11px] uppercase tracking-[0.1em]">{problem.severity}: {problem.title}</div>
                  <div className="mt-2 text-xs leading-5 text-text-secondary">Przyczyna: {problem.cause}</div>
                  <div className="mt-1 text-xs leading-5 text-text-primary">Naprawa: {problem.suggestedFix}</div>
                  <div className="mt-2 font-mono text-[9px] leading-5 text-text-tertiary">Weryfikacja: {problem.verification}</div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {message ? <div className="mt-4 border border-border-default bg-void px-3 py-2 font-mono text-[10px] leading-5 text-text-secondary">{message}</div> : null}
    </section>
  )
}

function ReportList({ label, items, tone }: { label: string; items: string[]; tone: string }) {
  return (
    <div className="border border-border-default bg-panel p-3">
      <div className={['font-display text-[10px] uppercase tracking-[0.12em]', tone].join(' ')}>{label}</div>
      <ul className="mt-2 space-y-1 text-xs leading-5 text-text-secondary">
        {items.length ? items.slice(0, 5).map((item) => <li key={item}>• {item}</li>) : <li>• Brak.</li>}
      </ul>
    </div>
  )
}

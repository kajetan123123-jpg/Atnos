import { useCallback, useEffect, useState } from 'react'
import { BrainCircuit, Check, Microscope, Play, RefreshCw, TriangleAlert } from 'lucide-react'
import {
  fetchReferenceLearningStatus,
  startReferenceLearning,
  type ReferenceLearningStatus,
} from '../lib/api'

const ACTIVE = new Set(['starting', 'running'])

function statusCopy(status: string) {
  if (status === 'completed') return 'Profil gotowy'
  if (status === 'failed') return 'Sesja przerwana'
  if (ACTIVE.has(status)) return 'Atnos analizuje'
  return 'Gotowe do uruchomienia'
}

export function ReferenceLearningPanel() {
  const [state, setState] = useState<ReferenceLearningStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      setState(await fetchReferenceLearningStatus())
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nie udało się odczytać sesji nauki.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!state || !ACTIVE.has(state.status)) return
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => window.clearInterval(timer)
  }, [refresh, state])

  const start = async () => {
    setLoading(true)
    try {
      setState(await startReferenceLearning())
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Atnos nie uruchomił sesji nauki.')
    } finally {
      setLoading(false)
    }
  }

  const target = state?.target_count || 40
  const selected = state?.selected_count || 0
  const progress = Math.max(0, Math.min(100, Math.round((selected / target) * 100)))
  const running = Boolean(state && ACTIVE.has(state.status))
  const completed = state?.status === 'completed'
  const channelDistribution = Object.entries(state?.channel_distribution || {})

  return (
    <section className="relative overflow-hidden border border-accent-primary/40 bg-void p-4 lg:col-span-2">
      <div className="relative border-l-2 border-accent-primary pl-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-accent-primary">
              <Microscope size={14} /> Laboratorium nauki
            </div>
            <h3 className="mt-2 font-display text-base font-semibold uppercase tracking-[0.08em] text-text-primary">
              Scientific batch / 40 Shortów
            </h3>
            <p className="mt-2 text-sm leading-6 text-text-secondary">
              Atnos najpierw równoważy próbkę między 5 źródłami, a niedobory uzupełnia z pozostałej puli do dokładnie
              40 materiałów. Wyciąga wyłącznie zagregowane wzorce języka, rytmu, animacji i montażu. Nie kopiuje
              skryptów, kadrów, głosu ani identyfikacji twórców.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              className="border border-border-default p-2 text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:opacity-40"
              aria-label="Odśwież status nauki"
            >
              <RefreshCw size={15} className={running ? 'animate-spin' : ''} />
            </button>
            <button
              type="button"
              onClick={() => void start()}
              disabled={loading || running}
              className="flex items-center gap-2 border border-accent-primary bg-accent-primary px-4 py-2 font-display text-[10px] font-semibold uppercase tracking-[0.14em] text-void disabled:cursor-not-allowed disabled:opacity-45"
            >
              <Play size={13} /> {completed ? 'Uruchom ponownie' : 'Analizuj 40 filmów'}
            </button>
          </div>
        </div>

        <div className="mt-5 h-1.5 overflow-hidden bg-panel">
          <div className="h-full bg-accent-primary transition-[width] duration-500" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-2 flex justify-between font-mono text-[9px] uppercase tracking-[0.12em] text-text-tertiary">
          <span>{statusCopy(state?.status || 'not_started')}</span>
          <span>{selected} / {target} · {progress}%</span>
        </div>

        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {[
            ['Wybrane filmy', selected],
            ['Transkrypcje', state?.transcript_count || 0],
            ['Analizy obrazu', state?.visual_count || 0],
            ['Błędy źródeł', state?.failure_count || 0],
          ].map(([label, value]) => (
            <div key={String(label)} className="border border-border-default bg-panel px-3 py-3">
              <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-text-tertiary">{label}</div>
              <div className="mt-1 font-display text-xl text-text-primary">{value}</div>
            </div>
          ))}
        </div>

        <div className="mt-4 grid gap-2 text-xs text-text-secondary md:grid-cols-3">
          <div className="flex items-center gap-2 border-l-2 border-accent-primary pl-3"><BrainCircuit size={14} /> Konstrukcja zdań i hooków</div>
          <div className="flex items-center gap-2 border-l-2 border-accent-primary pl-3"><BrainCircuit size={14} /> Ruch, światło i tempo cięć</div>
          <div className="flex items-center gap-2 border-l-2 border-accent-primary pl-3"><BrainCircuit size={14} /> Reguły odrzucenia w QA</div>
        </div>

        {channelDistribution.length ? (
          <div className="mt-4 border border-border-default bg-panel p-3">
            <div className="font-mono text-[9px] uppercase tracking-[0.14em] text-text-tertiary">
              Faktyczne pokrycie źródeł
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {channelDistribution.map(([channel, count]) => (
                <div key={channel} className="border border-border-default bg-void px-2 py-1 font-mono text-[10px] text-text-secondary">
                  <span>{channel}</span> <span className="text-accent-primary">{count}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {state?.message ? <div className="mt-4 text-xs leading-5 text-text-secondary">{state.message}</div> : null}
        {error ? (
          <div className="mt-4 flex items-start gap-2 border border-error/40 bg-error/10 p-3 text-xs text-error">
            <TriangleAlert size={15} className="mt-0.5 shrink-0" /> {error}
          </div>
        ) : null}
        {completed ? (
          <div className="mt-4 flex items-start gap-2 border border-success/40 bg-success/10 p-3 text-xs text-success">
            <Check size={15} className="mt-0.5 shrink-0" /> Profil jest aktywny w generatorze skryptu, plannerze scen i QA.
          </div>
        ) : null}
      </div>
    </section>
  )
}

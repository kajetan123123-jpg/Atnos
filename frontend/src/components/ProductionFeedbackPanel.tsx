import { useEffect, useState } from 'react'
import { addProductionFeedback, disableProductionFeedback, fetchProductionFeedback } from '../lib/api'
import type { ProductionFeedbackPayload } from '../lib/api'

const SCOPES = { production: 'Tylko ta rolka', series: 'Ta seria', project: 'Cały Wealth Blueprint' }
const CATEGORIES: Record<string, string> = { narration: 'Narracja', voice: 'Głos', visuals: 'Obraz i montaż', captions: 'Napisy', thumbnail: 'Miniatura' }

export default function ProductionFeedbackPanel({ jobId, hasSeries, storyMode = false }: { jobId: string; hasSeries: boolean; storyMode?: boolean }) {
  const scopes = storyMode ? { ...SCOPES, production: 'Tylko ta opowieść', project: 'Cały ten kanał' } : SCOPES
  const [records, setRecords] = useState<ProductionFeedbackPayload[]>([])
  const [note, setNote] = useState('')
  const [scope, setScope] = useState<ProductionFeedbackPayload['scope']>('production')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')

  useEffect(() => {
    let current = true
    void fetchProductionFeedback(jobId).then(result => {
      if (current) setRecords(result.feedback)
    }).catch(() => {
      if (current) setError('Nie udało się odczytać zapisanych uwag.')
    })
    return () => { current = false }
  }, [jobId])

  const save = async () => {
    setBusy(true)
    setError('')
    setSaved('')
    try {
      const record = await addProductionFeedback(jobId, { note: note.trim(), scope })
      setRecords(previous => [...previous, record])
      setNote('')
      setSaved(scope === 'production' ? 'Uwaga zapisana przy tej rolce. Nie uruchamia ponownego renderu.' : 'Reguła zapisana. Atnos pobierze ją do kolejnych zleceń.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nie udało się zapisać uwagi.')
    } finally {
      setBusy(false)
    }
  }

  const disable = async (record: ProductionFeedbackPayload) => {
    setBusy(true)
    setError('')
    setSaved('')
    try {
      await disableProductionFeedback(record.source_job_id, record.id)
      setRecords(previous => previous.map(item => item.id === record.id ? { ...item, active: false } : item))
      setSaved('Uwaga wycofana z kolejnych zleceń. Historia wcześniejszych rolek pozostaje zachowana.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nie udało się wycofać uwagi.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="border border-border-default bg-panel p-4">
      <h2 className="font-display text-xs uppercase tracking-[0.16em] text-text-primary">Uwagi do kolejnych rolek</h2>
      <label className="mt-3 block text-sm text-text-secondary" htmlFor="production-feedback-note">Co poprawić?</label>
      <textarea id="production-feedback-note" value={note} onChange={event => setNote(event.target.value)} maxLength={1000}
        placeholder={storyMode ? 'Np. prostsze słownictwo, wyraźniejszy finał, bardziej spójny opis postaci.' : 'Np. krótsze napisy, więcej konkretnych przykładów, mniej scen giełdowych.'}
        className="mt-2 min-h-24 w-full border border-border-default bg-void p-3 text-sm text-text-primary" />
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex min-w-0 flex-1 flex-col gap-2 text-sm text-text-secondary">
          Zakres uwagi
          <select value={scope} onChange={event => setScope(event.target.value as ProductionFeedbackPayload['scope'])}
            className="border border-border-default bg-void p-2 text-text-primary">
            <option value="production">{scopes.production}</option>
            {hasSeries ? <option value="series">{scopes.series}</option> : null}
            <option value="project">{scopes.project}</option>
          </select>
        </label>
        <button type="button" disabled={busy || note.trim().length < 5} onClick={() => void save()}
          className="border border-border-default px-3 py-2 text-sm text-text-primary hover:border-accent-primary disabled:opacity-40">Zapisz uwagę</button>
      </div>
      {error ? <p role="alert" className="mt-3 text-sm text-error">{error}</p> : null}
      {saved ? <p role="status" className="mt-3 text-sm text-text-secondary">{saved}</p> : null}
      <ul className="mt-4 space-y-3">
        {records.filter(record => record.active).map(record => (
          <li key={record.id} className="border-t border-border-default pt-3">
            <div className="text-xs text-text-tertiary">{scopes[record.scope]} · {CATEGORIES[record.category] ?? record.category}</div>
            <p className="mt-1 break-words text-sm text-text-primary">{record.note}</p>
            <button type="button" disabled={busy} onClick={() => void disable(record)}
              className="mt-2 text-xs text-text-secondary underline disabled:opacity-40" aria-label={`Wycofaj uwagę: ${record.note}`}>Wycofaj uwagę</button>
          </li>
        ))}
      </ul>
    </section>
  )
}

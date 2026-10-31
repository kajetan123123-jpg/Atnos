import { useRef, useState } from 'react'
import { applyPresentationRepair, fetchPresentationRepair, type PresentationRepairSnapshot } from '../lib/api'

export default function StudioPresentationRepair({ jobId, onChanged }: { jobId: string; onChanged: () => Promise<void> }) {
  const [snapshot, setSnapshot] = useState<PresentationRepairSnapshot | null>(null)
  const [note, setNote] = useState('Dopracuj prowadzenie uwagi i tempo, zachowując scenariusz, grafikę oraz wybrany głos.')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  const request = useRef<Parameters<typeof applyPresentationRepair>[1] | null>(null)
  async function prepare() {
    if (pending.current) return
    pending.current = true; setBusy(true); setError('')
    try {
      const next=await fetchPresentationRepair(jobId)
      setSnapshot(next); request.current = null
      if (!next.can_repair) await onChanged()
    }
    catch (e) { setError(e instanceof Error ? e.message : 'Nie udało się sprawdzić poprawki.') }
    finally { pending.current = false; setBusy(false) }
  }
  async function apply() {
    if (pending.current || !snapshot?.can_repair || !snapshot.package_sha256 || !snapshot.renderer_source || note.trim().length < 20) return
    pending.current = true; setBusy(true); setError('')
    request.current ??= { request_key: crypto.randomUUID(), expected_package_sha256: snapshot.package_sha256,
      expected_renderer_source: snapshot.renderer_source, note: note.trim() }
    try { await applyPresentationRepair(jobId, request.current); await onChanged() }
    catch (e) { setError(e instanceof Error ? e.message : 'Nie potwierdzono rozpoczęcia. Ponowienie użyje tego samego zlecenia poprawki.') }
    finally { pending.current = false; setBusy(false) }
  }
  return <section className="rounded-xl border border-border-default p-4" aria-label="Poprawka montażu">
    <h2 className="font-semibold">Popraw montaż tej rolki</h2>
    <p className="my-2 text-sm text-text-secondary">Zachowuje scenariusz, ilustracje, głos i napisy. Używa aktualnych kompozycji Atnosa, ponownie renderuje i sprawdza wynik. Wpisana uwaga zostaje w historii; ten tryb nie zmienia dowolnie scen ani nie tworzy nowych grafik.</p>
    {!snapshot ? <button type="button" disabled={busy} onClick={() => void prepare()}>Sprawdź dostępność poprawki</button> : snapshot.can_repair ? <>
      <label className="block">Powód poprawki montażu<textarea value={note} maxLength={2000} disabled={busy || Boolean(request.current)}
        onChange={e => { setNote(e.target.value); request.current = null }} className="mt-2 block w-full bg-void p-2" /></label>
      <button type="button" className="mt-3" disabled={busy || note.trim().length < 20} onClick={() => void apply()}>
        {busy ? 'Zlecanie poprawki…' : request.current ? 'Sprawdź lub ponów tę samą poprawkę' : 'Zleć poprawkę montażu'}
      </button>
    </> : <p role="status">{snapshot.reason}</p>}
    {error ? <><p role="alert" className="mt-2 text-error">{error}</p>
      <button type="button" disabled={busy} onClick={() => void prepare()}>Sprawdź aktualny pakiet i dostępność</button></> : null}
  </section>
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchPublicationStatus, retryPublication, savePublicationPolicy, verifyPublicationChannel } from '../lib/api'
import type { PublicationStatusPayload } from '../lib/api'

const LABELS: Record<string, string> = {
  queued: 'W kolejce', uploading: 'Wysyłanie prywatnego filmu', uploaded_private: 'Film przesłany prywatnie',
  processing: 'YouTube przetwarza film', scheduling: 'Ustawianie terminu', scheduled: 'Termin potwierdzony przez YouTube',
  published: 'Opublikowano publicznie', retry_wait: 'Oczekiwanie na ponowną kontrolę',
  blocked_user_action: 'Potrzebna Twoja decyzja', blocked_technical: 'Publikacja zatrzymana',
  insufficient_evidence: 'Niepewny wynik wysyłki. Sprawdź YouTube Studio', cancelled: 'Anulowano',
}

export default function PublicationPanel({ jobId }: { jobId: string }) {
  const [snapshot, setSnapshot] = useState<PublicationStatusPayload | null>(null)
  const [channel, setChannel] = useState('')
  const [times, setTimes] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => {
    let active = true
    let initialized = false
    const load = async () => {
      try {
        const result = await fetchPublicationStatus(jobId)
        if (!active) return
        setSnapshot(result)
        if (!initialized) {
          setChannel(result.policy.channel_id ?? '')
          setTimes(result.policy.times_utc.join(', '))
          setEnabled(result.policy.enabled)
          initialized = true
        }
      } catch {
        if (active) setError('Nie udało się odczytać stanu publikacji.')
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 30000)
    return () => { active = false; window.clearInterval(timer) }
  }, [jobId])

  const action = async (kind: 'save' | 'verify' | 'retry') => {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      if (kind === 'save') {
        await savePublicationPolicy({ enabled, channel_id: channel.trim() || null, times_utc: times.split(',').map(t => t.trim()).filter(Boolean) })
        setSnapshot(await fetchPublicationStatus(jobId))
        setMessage(enabled ? 'Zapisano zgodę na publiczną publikację nowych, gotowych rolek według harmonogramu.' : 'Wstrzymano nowe działania publikacyjne.')
      } else if (kind === 'verify') {
        const result = await verifyPublicationChannel()
        if (result.status === 'blocked_user_action') setError(result.message)
        else {
          if (result.channel_id) setChannel(result.channel_id)
          setMessage([result.title, result.message].filter(Boolean).join(' · '))
        }
      } else setSnapshot(await retryPublication(jobId))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Operacja nie powiodła się.')
    } finally { setBusy(false) }
  }

  const job = snapshot?.jobs[0]
  return (
    <section className="border border-border-default bg-panel p-4">
      <h2 className="font-display text-xs uppercase tracking-[0.16em] text-text-primary">Agent publikacji</h2>
      <p className="mt-3 text-sm text-text-secondary" role="status">
        {!snapshot ? 'Odczytywanie stanu publikacji…' : job ? LABELS[job.status] ?? 'Stan nieznany' : snapshot.policy.enabled ? 'Zgoda zapisana. Czekam na gotową rolkę i potwierdzony kanał.' : 'Publikacja wstrzymana.'}
      </p>
      {job ? <p className="mt-2 text-sm text-text-secondary">Termin: {new Date(job.publish_at).toLocaleString('pl-PL')} (czas tego urządzenia)</p> : null}
      {job?.youtube_url ? <a href={job.youtube_url} target="_blank" rel="noreferrer" className="mt-2 block text-sm text-accent-primary underline">Otwórz film w YouTube</a> : null}
      {job?.error ? <p className="mt-2 break-words text-sm text-warning">{job.error}</p> : null}
      {snapshot?.write_blocked ? <p className="mt-2 text-sm text-warning">Tryb nauki blokuje wysyłkę do YouTube.</p> : null}
      {snapshot?.runtime?.running === false ? <p className="mt-2 text-sm text-warning">Wykonawca publikacji nie działa. Uruchom ponownie Atnosa.</p> : null}
      {snapshot?.runtime?.error ? <p className="mt-2 text-sm text-warning">{snapshot.runtime.error}</p> : null}
      {job && ['blocked_user_action', 'blocked_technical'].includes(job.status) ? (
        <button type="button" disabled={busy} onClick={() => void action('retry')} className="mt-3 border border-border-default px-3 py-2 text-sm text-text-primary disabled:opacity-40">Sprawdź ponownie po naprawie</button>
      ) : null}
      <details className="mt-4 border-t border-border-default pt-3">
        <summary className="cursor-pointer text-sm text-text-secondary">Ustawienia publikacji Wealth Blueprint</summary>
        <div className="mt-3 space-y-3 text-sm text-text-secondary">
          <label className="flex items-start gap-2"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} disabled={busy || !snapshot} className="mt-1" />Publikuj publicznie nowe rolki po zaliczeniu kontroli jakości</label>
          <label className="block">Identyfikator kanału YouTube (UC…)
            <input aria-label="Identyfikator kanału YouTube" value={channel} onChange={e => setChannel(e.target.value)} maxLength={24} className="mt-2 w-full border border-border-default bg-void p-2 text-text-primary" />
          </label>
          <label className="block">Godziny UTC, oddzielone przecinkiem
            <input aria-label="Godziny publikacji UTC" value={times} onChange={e => setTimes(e.target.value)} placeholder="14:00, 20:00" className="mt-2 w-full border border-border-default bg-void p-2 text-text-primary" />
          </label>
          <p>Dotyczy nowych zleceń od pierwszego włączenia. Stare rolki nie trafią automatycznie do publikacji. Wstrzymanie nie usuwa terminów już zapisanych w YouTube Studio.</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy || !snapshot} onClick={() => void action('save')} className="border border-border-default px-3 py-2 text-text-primary hover:border-accent-primary disabled:opacity-40">Zapisz ustawienia publikacji</button>
            <button type="button" disabled={busy} onClick={() => void action('verify')} className="border border-border-default px-3 py-2 text-text-primary hover:border-accent-primary disabled:opacity-40">Sprawdź połączenie z kanałem</button>
          </div>
          <Link to="/settings" className="block text-accent-primary underline">Otwórz ustawienia połączeń</Link>
        </div>
      </details>
      {error ? <p role="alert" className="mt-3 break-words text-sm text-error">{error}</p> : null}
      {message ? <p className="mt-3 text-sm text-text-secondary">{message}</p> : null}
    </section>
  )
}

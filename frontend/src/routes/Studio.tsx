import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowLeft, Check, Copy, Film, FolderOpen, Image as ImageIcon, RefreshCw } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import {
  authorizeProductionCallLimit,
  buildProductionReviewArtifactUrl,
  fetchProductionStudioJob,
  fetchProductionStudioReviewPackage,
  fetchProductionOrderRecovery,
  repairProductionOrderScript,
  resumeProductionOrder,
  reconcileProductionLocalOperation,
} from '../lib/api'
import type { ProductionJobPayload, ProductionOrderRecovery, ProductionReviewPackage } from '../lib/api'
import ProductionFeedbackPanel from '../components/ProductionFeedbackPanel'
import { ProductionAssetReviewPanel } from '../components/ProductionAssetReviewPanel'
import {
  activityDetail,
  productionActivityStepLabel,
  productionActivityKind,
  ProductionLiveActivityPanel,
} from '../components/ProductionLiveActivityPanel'
import PublicationPanel from '../components/PublicationPanel'
import StudioPresentationRepair from '../components/StudioPresentationRepair'
import { productionBlocker, productionBlockerIssues, productionBlockerKind, productionIssueMessage } from '../lib/productionMessages'

const RUNNING_STATUSES = new Set(['queued', 'running', 'retrying'])

function statusLabel(status: string) {
  if (status === 'ready') return 'Gotowe do oceny'
  if (status === 'script_ready') return 'Scenariusz gotowy do przeczytania'
  if (status === 'blocked') return 'Produkcja zatrzymana'
  if (status === 'failed') return 'Błąd produkcji'
  if (status === 'cancelled') return 'Produkcja anulowana'
  if (status === 'qa_passed') return 'Gotowe do review'
  if (status === 'qa_failed') return 'Wymaga poprawek'
  if (RUNNING_STATUSES.has(status)) return 'Produkcja trwa'
  if (status === 'error') return 'Błąd produkcji'
  return status.replace(/_/g, ' ')
}

export default function StudioRoute() {
  const { jobId = '' } = useParams()
  const [job, setJob] = useState<ProductionJobPayload | null>(null)
  const [review, setReview] = useState<ProductionReviewPackage | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [reviewError, setReviewError] = useState('')
  const [copied, setCopied] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [reconciling, setReconciling] = useState(false)
  const [resumeError, setResumeError] = useState('')
  const [recovery, setRecovery] = useState<ProductionOrderRecovery | null>(null)
  const [recoveryError, setRecoveryError] = useState('')
  const [repairNote, setRepairNote] = useState('')
  const [repairing, setRepairing] = useState(false)
  const [repairError, setRepairError] = useState('')
  const [repairNotice, setRepairNotice] = useState('')
  const repairRequest = useRef<{ key: string; note: string } | null>(null)
  const repairPending = useRef(false)
  const [folderCopied, setFolderCopied] = useState(false)
  const [clipboardError, setClipboardError] = useState('')
  const [callLimit, setCallLimit] = useState('')
  const [savingCallLimit, setSavingCallLimit] = useState(false)
  const [callLimitError, setCallLimitError] = useState('')
  const [nowMs, setNowMs] = useState(() => Date.now())
  const activeJobId = useRef(jobId)
  const mounted = useRef(true)
  const loadSequence = useRef(0)
  const inFlight = useRef<{ jobId: string; sequence: number; promise: Promise<void> } | null>(null)
  activeJobId.current = jobId
  const jobStatus = job?.status
  const autonomyEnabled = job?.autonomous_recovery?.enabled === true
  const pollStatus = Boolean(jobStatus && (RUNNING_STATUSES.has(jobStatus) || job?.worker_live
    || (autonomyEnabled && ['blocked', 'failed', 'error'].includes(jobStatus))))

  const load = useCallback((force = false): Promise<void> => {
    if (!jobId) return Promise.resolve()
    if (!force && inFlight.current?.jobId === jobId) return inFlight.current.promise
    const sequence = ++loadSequence.current
    setRefreshing(true)
    const promise = (async () => { try {
      const [nextJob, nextReview, recoveryResult] = await Promise.all([
        fetchProductionStudioJob(jobId),
        fetchProductionStudioReviewPackage(jobId).then(result => ({ result, failure: null })).catch((failure: unknown) => ({ result: null, failure })),
        fetchProductionOrderRecovery(jobId).then(result => ({ result, failed: false }))
          .catch(() => ({ result: null, failed: true })),
      ])
      if (!mounted.current || activeJobId.current !== jobId || sequence !== loadSequence.current) return
      const resolvedRecovery = recoveryResult.result ?? nextJob.recovery ?? null
      setRecoveryError(recoveryResult.failed ? 'Nie udało się sprawdzić dostępnych akcji naprawy. Odśwież status, aby spróbować ponownie.' : '')
      setReviewError(nextReview.result ? '' : 'Nie udało się odczytać pakietu podglądu. Status zlecenia jest dostępny; film i jego gotowość wymagają ponownego sprawdzenia.')
      setJob(nextJob)
      setReview(nextReview.result ?? {
        job_id: jobId, status: nextJob.status, ready_for_review: false,
        title: nextJob.topic_title || '', description: '', tags: [], video_ready: false, thumbnail_ready: false,
        video_url: null, thumbnail_url: null, blocking_failures: resolvedRecovery ? [resolvedRecovery.message] : [], quality_review_status: 'unavailable',
      })
      setRecovery(resolvedRecovery)
      setError('')
      setNowMs(Date.now())
    } catch (caught) {
      if (!mounted.current || activeJobId.current !== jobId || sequence !== loadSequence.current) return
      setError(caught instanceof Error ? caught.message : 'Nie udało się odczytać pakietu rolki.')
    } finally {
      if (inFlight.current?.sequence === sequence) inFlight.current = null
      if (mounted.current && activeJobId.current === jobId && sequence === loadSequence.current) {
        setLoading(false)
        setRefreshing(false)
      }
    } })()
    inFlight.current = { jobId, sequence, promise }
    return promise
  }, [jobId])

  useEffect(() => {
    mounted.current = true
    setJob(null)
    setReview(null)
    setRecovery(null)
    setLoading(true)
    setError('')
    setReviewError('')
    setResumeError('')
    setRepairError('')
    setRepairNotice('')
    setRepairNote('')
    setResuming(false)
    setRepairing(false)
    setSavingCallLimit(false)
    repairPending.current = false
    repairRequest.current = null
    void load()
    return () => { mounted.current = false; ++loadSequence.current; inFlight.current = null }
  }, [load])

  useEffect(() => {
    if (!pollStatus) return
    const timer = window.setInterval(() => { if (!document.hidden) void load() }, RUNNING_STATUSES.has(jobStatus ?? '') ? 2000 : 5000)
    return () => window.clearInterval(timer)
  }, [jobStatus, pollStatus, load])

  useEffect(() => {
    if (!jobStatus || !RUNNING_STATUSES.has(jobStatus)) return
    const timer = window.setInterval(() => setNowMs(Date.now()), 5000)
    return () => window.clearInterval(timer)
  }, [jobStatus])

  useEffect(() => {
    setCallLimit(job?.max_provider_calls != null ? String(Math.min(100000, job.max_provider_calls + 100)) : '')
    setCallLimitError('')
  }, [job?.id, job?.max_provider_calls])

  const requestedCallLimit = Number(callLimit)
  const validCallLimit = job?.max_provider_calls != null && Number.isInteger(requestedCallLimit)
    && requestedCallLimit > job.max_provider_calls && requestedCallLimit <= 100000

  const extendCallLimit = async () => {
    if (!validCallLimit || !job?.can_extend_call_limit || savingCallLimit) return
    setSavingCallLimit(true)
    setCallLimitError('')
    try {
      const nextJob = await authorizeProductionCallLimit(jobId, requestedCallLimit)
      if (!mounted.current || activeJobId.current !== jobId) return
      setJob(nextJob)
      await load(true)
    } catch (caught) {
      if (!mounted.current || activeJobId.current !== jobId) return
      setCallLimitError(caught instanceof Error ? caught.message : 'Nie udało się zapisać limitu wywołań.')
    } finally {
      if (mounted.current && activeJobId.current === jobId) setSavingCallLimit(false)
    }
  }

  const copyDescription = async () => {
    if (!review?.description) return
    try {
      await navigator.clipboard.writeText(review.description)
      setClipboardError('')
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch { setClipboardError('Nie udało się skopiować tekstu. Możesz zaznaczyć go i skopiować ręcznie.') }
  }

  const copyFolder = async () => {
    if (!job?.artifact_dir) return
    try {
      await navigator.clipboard.writeText(job.artifact_dir)
      setClipboardError('')
      setFolderCopied(true)
      window.setTimeout(() => setFolderCopied(false), 1800)
    } catch { setClipboardError('Nie udało się skopiować ścieżki. Możesz zaznaczyć ją poniżej.') }
  }

  const reconcileOperation = async () => {
    if (reconciling || resuming || repairPending.current || job?.worker_live || error) return
    setReconciling(true)
    setResumeError('')
    try {
      const nextJob = await reconcileProductionLocalOperation(jobId)
      if (!mounted.current || activeJobId.current !== jobId) return
      setJob(nextJob)
      await load(true)
    } catch (caught) {
      if (mounted.current && activeJobId.current === jobId) setResumeError(caught instanceof Error ? caught.message : 'Nie udało się rozstrzygnąć wyniku operacji.')
    } finally {
      if (mounted.current && activeJobId.current === jobId) setReconciling(false)
    }
  }

  const resume = async () => {
    if (resuming || repairPending.current || job?.worker_live || error) return
    setResuming(true)
    setResumeError('')
    try {
      const nextJob = await resumeProductionOrder(jobId)
      if (!mounted.current || activeJobId.current !== jobId) return
      setJob(nextJob)
      await load(true)
    } catch (caught) {
      if (!mounted.current || activeJobId.current !== jobId) return
      setResumeError(productionBlocker(caught instanceof Error ? caught.message : 'Nie udało się wznowić produkcji.'))
    } finally {
      if (mounted.current && activeJobId.current === jobId) setResuming(false)
    }
  }

  const repairScript = async () => {
    if (!recovery?.can_repair_script || recovery.worker_live || job?.worker_live || repairPending.current || resuming || error) return
    repairPending.current = true
    setRepairing(true)
    setRepairError('')
    setRepairNotice('')
    const note = repairNote.trim()
    if (!repairRequest.current || repairRequest.current.note !== note) {
      repairRequest.current = { key: crypto.randomUUID(), note }
    }
    try {
      const nextJob = await repairProductionOrderScript(jobId, { note, requestKey: repairRequest.current.key })
      if (!mounted.current || activeJobId.current !== jobId) return
      setJob(nextJob)
      setRecovery(nextJob.recovery ?? null)
      setRepairNote('')
      repairRequest.current = null
      setRepairNotice('Zlecono Atnosowi poprawkę scenariusza i ponowną kontrolę. Postęp znajdziesz poniżej.')
      await load(true)
    } catch (caught) {
      if (!mounted.current || activeJobId.current !== jobId) return
      setRepairError(productionBlocker(caught instanceof Error ? caught.message : 'Nie udało się zlecić poprawki scenariusza.'))
    } finally {
      if (mounted.current && activeJobId.current === jobId) {
        repairPending.current = false
        setRepairing(false)
      }
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[calc(100vh-56px)] items-center justify-center p-6">
        <div className="font-mono text-xs uppercase tracking-[0.2em] text-text-tertiary">Ładowanie rolki…</div>
      </div>
    )
  }

  if (!job || !review) {
    return (
      <div className="p-4">
        <div className="border border-error/40 bg-error/10 p-6">
          <div className="font-display text-lg uppercase text-error">Nie można otworzyć rolki</div>
          <div className="mt-2 text-sm text-text-secondary">{error || 'Nie znaleziono joba produkcyjnego.'}</div>
          <Link to="/create" className="mt-4 inline-block border border-border-default px-3 py-2 text-text-primary">
            Wróć do produkcji
          </Link>
          <button type="button" onClick={() => void load()} disabled={refreshing} className="ml-3 border border-border-default px-3 py-2 text-text-primary disabled:opacity-50">Spróbuj ponownie</button>
        </div>
      </div>
    )
  }

  const version = encodeURIComponent(review.updated_at || job.updated_at || '')
  const videoBase = buildProductionReviewArtifactUrl(review.video_url)
  const thumbnailBase = buildProductionReviewArtifactUrl(review.thumbnail_url)
  const videoUrl = videoBase ? `${videoBase}?v=${version}` : null
  const thumbnailUrl = thumbnailBase ? `${thumbnailBase}?v=${version}` : null
  const interrupted = ['running', 'retrying'].includes(job.status) && job.worker_live === false
  const awaitingStart = job.status === 'queued' && job.worker_live === false
  const productionRunning = RUNNING_STATUSES.has(job.status) && !interrupted && !awaitingStart
  const activityKind = productionActivityKind(job, nowMs)
  const packageReady = review.ready_for_review && review.video_ready && review.thumbnail_ready && !review.blocking_failures.length
  const storyPreview = job.request?.production_scope === 'script_preview'
  const scriptReady = storyPreview && job.status === 'script_ready' && Boolean(review.script_preview) && !review.blocking_failures.length
  const storyNeedsCorrection = storyPreview && job.status === 'script_ready' && review.blocking_failures.length > 0
  const showLiveActivity = !packageReady && !scriptReady
  const blockerText = [job.error, ...review.blocking_failures].filter(Boolean).join(' ')
  const blockerKind = job.error?.includes('execution_outcome_unknown')
    ? 'other'
    : recovery?.kind ?? productionBlockerKind(blockerText, job.current_step)
  const imagePlanUnsupported = /IMAGE_PLAN_UNSUPPORTED|requires a supported physical illustration/i.test(blockerText)
  const parsedIssues = [job.error, ...review.blocking_failures].flatMap(message => message ? productionBlockerIssues(message) : [])
  const blockerIssues = recovery?.issues.length ? recovery.issues : parsedIssues.filter((issue, index, all) =>
    all.findIndex(other => other.code === issue.code && other.segment_ids.join(',') === issue.segment_ids.join(',')) === index)
  const stopped = !packageReady && !scriptReady && !productionRunning && !job.worker_live
  const recoveryLimitMessage = job.autonomous_recovery?.limit_reached
    ? error
      ? 'Ostatni odczyt statusu może być nieaktualny. Wykorzystano limit automatycznych wznowień; odśwież status, aby sprawdzić bieżący stan produkcji.'
      : productionRunning && job.worker_live === true
        ? 'Produkcja nadal trwa. Wykorzystano limit automatycznych wznowień, więc kolejne zatrzymanie nie zostanie już wznowione automatycznie.'
        : stopped
          ? 'Limit automatycznych wznowień został wykorzystany. Sprawdź przyczynę zatrzymania poniżej.'
          : 'Wykorzystano limit automatycznych wznowień. Bieżący status nie potwierdza, czy produkcja nadal trwa; odśwież go, aby to sprawdzić.'
    : 'Atnos może sam odzyskać obsługiwane zatrzymania. Status odświeża się automatycznie; pracę potwierdza aktywny worker.'
  const scriptBlocked = stopped && job.status === 'blocked' && blockerKind === 'script'
  const canResume = job.can_resume && stopped && !repairing && !error && blockerKind !== 'integrity' && !scriptBlocked
  const intake = job.request?.intake
  const localOnly = job.request?.delivery_mode === 'local_only'
    || (typeof intake === 'object' && intake !== null && 'delivery_mode' in intake && intake.delivery_mode === 'local_only')

  return (
    <div className="min-h-[calc(100vh-56px)] overflow-x-hidden bg-void p-4 lg:p-6">
      <Link to="/create" className="mb-5 inline-flex items-center gap-2 text-sm text-text-secondary hover:text-accent-primary"><ArrowLeft size={15} /> Twoje produkcje</Link>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4 border-b border-border-default pb-5">
        <div className="min-w-0 max-w-full flex-1 basis-full md:basis-auto">
          <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-accent-primary">
            {storyPreview ? 'Atnos / Scenariusz opowieści' : 'Atnos / Produkcja rolki'}
          </div>
          <h1 className="mt-2 w-full max-w-4xl break-words [overflow-wrap:anywhere] font-display text-lg font-bold uppercase leading-tight tracking-normal text-text-primary sm:text-2xl sm:tracking-[0.06em]">
            {review.title || job.topic_title || 'Rolka bez tytułu'}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-[10px] uppercase tracking-[0.12em] text-text-tertiary">
            <span>{storyNeedsCorrection ? 'Scenariusz wymaga poprawki' : interrupted ? 'Produkcja przerwana' : awaitingStart ? 'Oczekuje na uruchomienie' : activityKind === 'stale' ? 'Brak sygnału' : statusLabel(job.status)}</span>
            <span>•</span>
            <span>{job.id}</span>
          </div>
          <p className="mt-2 text-sm text-text-secondary" aria-live="polite">{storyNeedsCorrection ? 'Sprawdź problemy wskazane w kontroli poniżej.' : productionActivityStepLabel(job)}</p>
          {job.request?.project === 'wealth_blueprint' ? <p className="mt-2 text-sm text-text-tertiary">Projekt zlecenia: Wealth Blueprint</p> : null}
          {localOnly ? <p className="mt-2 text-sm text-text-tertiary">{storyPreview ? 'Lokalny podgląd scenariusza opowieści.' : 'Tylko lokalnie. Publikacja wyłączona dla tej rolki.'}</p> : null}
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={refreshing}
          className="flex items-center gap-2 border border-border-default px-4 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:opacity-50"
        >
          <RefreshCw size={13} className={productionRunning || activityKind === 'working' ? 'animate-spin' : ''} />
          Odśwież
        </button>
      </header>

      {error ? <div role="alert" className="mb-5 border border-warning/40 bg-warning/10 p-4 text-sm text-warning">Nie udało się odświeżyć statusu. Poniżej jest ostatni odczyt, który może być nieaktualny. {productionBlocker(error)}</div> : null}
      {reviewError ? <p role="alert" className="mb-5 border border-warning/40 bg-warning/10 p-4 text-sm text-warning">{reviewError}</p> : null}
      {autonomyEnabled ? <section aria-label="Automatyczne odzyskiwanie produkcji" className="mb-5 rounded-lg border border-border-default bg-panel px-4 py-3 text-sm">
        <p className="font-semibold">Automatyczne odzyskiwanie włączone</p>
        {job.autonomous_recovery?.used != null && job.autonomous_recovery.maximum != null ? <p className="mt-1 text-text-secondary">Wykorzystane wznowienia: {job.autonomous_recovery.used} z {job.autonomous_recovery.maximum}.</p> : null}
        <p className="mt-1 text-text-secondary">{recoveryLimitMessage}</p>
        {job.autonomous_recovery?.publication_enabled === false ? <p className="mt-1 text-text-tertiary">Automatyczne odzyskiwanie nie obejmuje publikacji.</p> : null}
      </section> : null}

      {job.request && job.reserved_cost_units != null ? <div className="mb-5 flex flex-wrap gap-x-8 gap-y-2 rounded-lg border border-border-default bg-panel px-4 py-3 text-sm">
        <span className="text-text-secondary">Zarezerwowany koszt: <strong className="text-text-primary">${(job.reserved_cost_units / 1000000).toFixed(2)}</strong>{job.budget_limit_usd != null ? ` z limitu $${job.budget_limit_usd.toFixed(2)}` : ''}</span>
        <span className="text-text-tertiary">Rozliczenie końcowe zależy od dostawców usług.</span>
      </div> : null}

      {job.max_provider_calls != null && job.provider_calls != null ? <div className="mb-5 rounded-lg border border-border-default bg-panel px-4 py-3 text-sm">
        <p className="text-text-secondary">{job.local_work_unlimited ? 'Wywołania płatne lub bez potwierdzonego kosztu:' : 'Wykorzystane wywołania AI:'} <strong className="text-text-primary">{job.local_work_unlimited ? job.limited_provider_calls ?? job.provider_calls : job.provider_calls} z {job.max_provider_calls}</strong></p>
        {job.local_work_unlimited ? <p className="mt-1 text-text-secondary">Lokalna praca: bez limitu prób i wywołań. Historia wszystkich wywołań: {job.provider_calls}.</p> : null}
        {job.can_extend_call_limit ? <form className="mt-3" onSubmit={event => { event.preventDefault(); void extendCallLimit() }}>
          <p id="call-limit-help" className="max-w-2xl text-sm leading-6 text-text-secondary">Produkcja osiągnęła limit wywołań. Możesz zwiększyć go dla tej rolki. Limit kosztu pozostanie taki sam. Po zapisaniu limitu wybierz „Wznów produkcję”.</p>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="text-sm text-text-secondary">Nowy łączny limit wywołań
              <input type="number" min={job.max_provider_calls + 1} max={100000} step={1} value={callLimit}
                onChange={event => setCallLimit(event.target.value)} disabled={savingCallLimit} aria-describedby="call-limit-help"
                className="mt-1 block w-36 rounded-md border border-border-default bg-void px-3 py-2 text-text-primary" />
            </label>
            <button type="submit" disabled={!validCallLimit || savingCallLimit}
              className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:opacity-50">
              {savingCallLimit ? 'Zapisywanie limitu…' : 'Zwiększ limit wywołań'}
            </button>
          </div>
          {callLimitError ? <p role="alert" className="mt-2 text-sm text-error">{callLimitError}</p> : null}
        </form> : null}
      </div> : null}

      {job.local_image_action ? <div className="mb-5 rounded-lg border border-border-default bg-panel px-4 py-3 text-sm" data-testid="local-image-action-budget">
        {job.local_image_action.status === 'verified' ? <>
          <p className="text-text-secondary">Ta akcja ilustracji ma osobny zakres: <strong className="text-text-primary">{job.local_image_action.used_new_requests} z {job.local_image_action.max_new_requests}</strong> nowych lokalnych generacji. Pozostało: {job.local_image_action.remaining_new_requests}.</p>
          <p className="mt-1 text-xs text-text-tertiary">Zużycie pozostaje zapisane po wznowieniu. Ogólna polityka lokalnej pracy nie rozszerza tej akcji.</p>
          {job.local_image_action.pending_or_unknown ? <p className="mt-1 text-sm text-warning">Wynik ostatniej generacji jest w toku lub wymaga rozstrzygnięcia. Kolejny POST pozostaje zablokowany.</p>
            : job.local_image_action.remaining_new_requests === 0 ? <p className="mt-1 text-sm text-warning">Zakres generacji wykorzystany. Najpierw oceń istniejące grafiki i przygotuj konkretną poprawkę; wznowienie nie przywraca prób.</p> : null}
        </> : <p role="alert" className="text-warning">Nie można potwierdzić integralności zakresu ilustracji. Nie zakładamy, że pozostały wolne próby.</p>}
      </div> : null}

      <div className="mb-5">
          <section
            className={[
              'border p-4',
              packageReady || scriptReady ? 'border-success/40 bg-success/10' : 'border-warning/40 bg-warning/10',
            ].join(' ')}
          >
            <div className="flex items-start gap-3">
              {!packageReady && !scriptReady ? (
                <AlertTriangle size={18} className="mt-0.5 shrink-0 text-warning" />
              ) : (
                <Check size={18} className="mt-0.5 shrink-0 text-success" />
              )}
              <div>
                <div className="font-display text-xs uppercase tracking-[0.14em] text-text-primary">
                  {scriptReady ? 'Scenariusz gotowy do przeczytania' : packageReady ? 'Pakiet gotowy do oceny' : productionRunning ? storyPreview ? 'Atnos pisze opowieść' : 'Atnos pracuje nad rolką' : scriptBlocked ? 'Scenariusz wymaga poprawki' : stopped && blockerKind === 'integrity' ? 'Sprawdź spójność plików' : stopped && blockerKind === 'assets' ? imagePlanUnsupported ? 'Plan obrazu wymaga poprawki' : 'Grafiki wymagają oceny lub poprawki' : stopped && blockerKind === 'resource' ? 'Zatrzymano na zasobach lub limitach' : 'Wymaga decyzji lub poprawki'}
                </div>
                <div className="mt-2 text-sm leading-6 text-text-secondary">
                  {scriptReady ? 'Scenariusz przeszedł automatyczną kontrolę fabuły. Możesz przeczytać każdą scenę poniżej. Obraz, lektor i montaż opowieści są jeszcze w przygotowaniu.' : packageReady
                    ? localOnly ? 'Rolka, opis i miniatura są kompletne. Ten pakiet jest przeznaczony wyłącznie do lokalnej oceny.' : 'Rolka, opis i miniatura są kompletne. Stan publikacji znajdziesz poniżej.'
                    : interrupted ? 'Proces wykonawczy nie działa. Zachowane etapy można wykorzystać przy wznowieniu.'
                    : awaitingStart ? 'Zlecenie jest zapisane. Wybierz „Wznów produkcję”, aby uruchomić kolejne etapy.'
                    : stopped && recovery?.message ? productionBlocker(recovery.message)
                    : review.blocking_failures.length
                      ? review.blocking_failures.map(productionBlocker).join(' · ')
                      : productionRunning || activityKind === 'working' || activityKind === 'stale' || activityKind === 'waiting_worker'
                        ? activityDetail(job, activityKind)
                        : 'Pakiet nie jest jeszcze kompletny.'}
                </div>
                {stopped && blockerIssues.length ? <ul aria-label="Problemy scenariusza" className="mt-3 space-y-2 text-sm leading-6 text-text-secondary">
                  {blockerIssues.map((issue, index) => <li key={`${issue.code}-${index}`}>
                    <span>{productionIssueMessage(issue)}</span>
                    {issue.segment_ids.length ? <span className="block text-xs text-text-tertiary">Sceny: {issue.segment_ids.join(', ')}</span> : null}
                  </li>)}
                </ul> : null}
                {scriptBlocked ? <div className="mt-4 max-w-2xl border-t border-warning/30 pt-3">
                  {recovery?.can_repair_script ? <form onSubmit={event => { event.preventDefault(); void repairScript() }}>
                    <p id="script-repair-help" className="text-sm leading-6 text-text-secondary">Atnos poprawi wskazane fragmenty i ponownie sprawdzi scenariusz. Zapisane etapy oraz limity kosztów i wywołań zostaną uwzględnione.</p>
                    <label htmlFor="script-repair-note" className="mt-3 block text-sm text-text-secondary">Uwaga do poprawki (opcjonalnie)</label>
                    <textarea id="script-repair-note" value={repairNote} onChange={event => setRepairNote(event.target.value)}
                      maxLength={2000} disabled={repairing || resuming || recovery.worker_live || Boolean(error)} aria-describedby="script-repair-help"
                      placeholder="Np. pokaż konkretną czynność, którą widz może wykonać po obejrzeniu rolki."
                      className="mt-2 min-h-24 w-full border border-border-default bg-void p-3 text-sm leading-6 text-text-primary disabled:opacity-50" />
                    <button type="submit" disabled={repairing || resuming || recovery.worker_live || Boolean(error)}
                      className="mt-3 border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:opacity-50">
                      {repairing ? 'Zlecanie poprawki…' : 'Popraw scenariusz przez Atnosa'}
                    </button>
                  </form> : <p className="text-sm leading-6 text-text-secondary">{recovery?.repair_blocker ? productionBlocker(recovery.repair_blocker) : 'Naprawa scenariusza przez Atnosa nie jest teraz dostępna. Odśwież status, aby sprawdzić dostępne akcje.'}</p>}
                </div> : null}
                {stopped && blockerKind === 'assets' ? <a href="#production-assets" className="mt-3 inline-block text-sm text-accent-primary underline">{imagePlanUnsupported ? 'Zobacz stan przygotowania grafiki' : 'Przejdź do grafik do oceny'}</a> : null}
                {stopped && blockerKind === 'resource' ? <Link to="/settings" className="mt-3 inline-block text-sm text-accent-primary underline">Sprawdź narzędzia i modele</Link> : null}
                {canResume ? (
                  <button type="button" onClick={() => void resume()} disabled={resuming}
                    className="mt-3 border border-border-default px-3 py-2 text-sm text-text-primary hover:border-accent-primary disabled:opacity-50">
                    {resuming ? 'Wznawianie…' : 'Wznów produkcję'}
                  </button>
                ) : null}
                {stopped && job.error?.includes('execution_outcome_unknown')
                  && job.current_step?.startsWith('assets/image_local/flux2-trial/') ? (
                  <button type="button" onClick={() => void reconcileOperation()} disabled={reconciling || resuming || Boolean(error)}
                    className="mt-3 border border-border-default px-3 py-2 text-sm text-text-primary hover:border-accent-primary disabled:opacity-50">
                    {reconciling ? 'Sprawdzanie wyniku…' : 'Sprawdź wynik przerwanej generacji'}
                  </button>
                ) : null}
                {resumeError ? <p role="alert" className="mt-2 text-sm text-error">{resumeError}</p> : null}
                {repairError ? <p role="alert" className="mt-2 text-sm text-error">{repairError}</p> : null}
                {repairNotice ? <p role="status" className="mt-2 text-sm text-text-secondary">{repairNotice}</p> : null}
                {recoveryError && stopped ? <p role="alert" className="mt-2 text-sm text-error">{recoveryError}</p> : null}
                {stopped && job.error ? <details className="mt-3 text-xs leading-5 text-text-tertiary">
                  <summary className="cursor-pointer">Szczegóły techniczne zatrzymania</summary>
                  <pre className="mt-2 max-w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{job.error}</pre>
                </details> : null}
              </div>
            </div>
          </section>
      </div>

      {showLiveActivity ? <ProductionLiveActivityPanel job={job} nowMs={nowMs} /> : null}

      {job.request?.project === 'wealth_blueprint' && !storyPreview ? <div id="production-assets"><ProductionAssetReviewPanel key={job.id} jobId={job.id} refreshVersion={job.updated_at}
        emptyMessage={stopped && blockerKind === 'assets'
          ? imagePlanUnsupported
            ? 'Nie ma tu grafiki do oceny. Walidacja planu wykryła nieobsługiwaną scenę przed wygenerowaniem obrazów. Szczegóły i wymagane działanie są opisane w komunikacie zatrzymania powyżej.'
            : 'Nie znaleziono grafik do oceny. Zlecenie może być zatrzymane przed generowaniem albo wymagać ponownego odczytu statusu.'
          : null} /></div> : null}

      <div className="grid min-w-0 grid-cols-1 gap-5 xl:grid-cols-[minmax(320px,0.8fr)_minmax(380px,1.2fr)]">
        {storyPreview ? <section className="min-w-0 border border-border-default bg-panel p-4">
          <h2 className="mb-4 text-lg font-semibold">Scenariusz opowieści</h2>
          {review.script_preview ? <>
            <p className="mb-4 text-xs text-text-secondary">Angielski · {review.script_preview.word_count} słów · kontrola automatyczna</p>
            <div className="space-y-5">{review.script_preview.segments.map((segment, index) => <article key={segment.id} className="border-t border-border-default pt-4">
              <h3 className="mb-2 text-sm font-semibold">Scena {index + 1} · {segment.on_screen}</h3>
              <p className="break-words text-sm leading-7">{segment.text}</p>
              <details className="mt-2 text-xs leading-6 text-text-secondary"><summary className="cursor-pointer">Obraz do tej sceny</summary>{segment.visual_intent}</details>
            </article>)}</div>
            <details className="mt-5 text-sm"><summary className="cursor-pointer font-semibold">Postaci i wygląd</summary>{review.script_preview.characters.map(character => <p key={character.id} className="mt-3 text-sm leading-6 text-text-secondary"><strong>{character.name}</strong> · {character.appearance}</p>)}</details>
          </> : <p className="text-sm text-text-secondary">Scenariusz pojawi się po sprawdzeniu fabuły i zgodności z odbiorcą.</p>}
        </section> : <section className="min-w-0 border border-border-default bg-panel p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2 font-display text-xs uppercase tracking-[0.16em] text-text-primary">
              <Film size={15} className="text-accent-primary" /> Rolka
            </div>
            <span className="font-mono text-[9px] uppercase text-text-tertiary">9:16 / MP4</span>
          </div>
          <div className="mx-auto flex aspect-[9/16] max-h-[72vh] w-full max-w-[430px] items-center justify-center overflow-hidden bg-black">
            {videoUrl ? (
              <video key={videoUrl} src={videoUrl} controls playsInline preload="metadata" className="h-full min-w-0 w-full object-contain" />
            ) : (
              <div className="px-8 text-center">
                <Film size={34} className="mx-auto text-text-tertiary" />
                <div className="mt-3 text-sm text-text-secondary">
                  {productionRunning ? 'Rolka powstaje. Bieżący etap znajdziesz nad podglądem.' : 'Brak finalnego pliku MP4.'}
                </div>
              </div>
            )}
          </div>
        </section>}

        <div className="min-w-0 space-y-5">
          {!storyPreview ? <section className="border border-border-default bg-panel p-4">
            <div className="mb-3 flex items-center gap-2 font-display text-xs uppercase tracking-[0.16em] text-text-primary">
              <ImageIcon size={15} className="text-accent-primary" /> Miniatura
            </div>
            <div className="aspect-video overflow-hidden bg-black">
              {thumbnailUrl ? (
                <img src={thumbnailUrl} alt={`Miniatura: ${review.title}`} className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full items-center justify-center text-sm text-text-tertiary">
                  Miniatura nie jest jeszcze gotowa.
                </div>
              )}
            </div>
          </section> : null}

          <section className="border border-border-default bg-panel p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="font-display text-xs uppercase tracking-[0.16em] text-text-primary">Opis publikacyjny</div>
              <button
                type="button"
                onClick={() => void copyDescription()}
                disabled={!review.description}
                className="flex items-center gap-2 border border-border-default px-3 py-2 font-mono text-[10px] uppercase text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:opacity-40"
              >
                {copied ? <Check size={13} /> : <Copy size={13} />}
                {copied ? 'Skopiowano' : 'Kopiuj'}
              </button>
            </div>
            <textarea
              readOnly
              value={review.description}
              placeholder="Opis pojawi się po ukończeniu skryptu."
              className="min-h-52 w-full resize-y border border-border-default bg-void p-3 text-sm leading-6 text-text-primary outline-none"
            />
            {review.tags.length ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {review.tags.map((tag) => (
                  <span key={tag} className="border border-border-default px-2 py-1 font-mono text-[9px] text-text-tertiary">
                    #{tag.replace(/\s+/g, '')}
                  </span>
                ))}
              </div>
            ) : null}
          </section>

          {job.artifact_dir ? <section className="rounded-lg border border-border-default bg-panel p-4">
            <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="inline-flex items-center gap-2 text-sm font-semibold"><FolderOpen size={16} /> Pliki tej produkcji</h2><button type="button" onClick={() => void copyFolder()} className="rounded-md border border-border-default px-3 py-2 text-xs">{folderCopied ? 'Skopiowano ścieżkę' : 'Kopiuj ścieżkę folderu'}</button></div>
            <p className="mt-3 break-all text-xs leading-5 text-text-secondary">{job.artifact_dir}</p>
            <p className="mt-2 text-xs leading-5 text-text-tertiary">{storyPreview ? 'Wklej ścieżkę w Eksploratorze plików. Folder zawiera scenariusz i wyniki jego kontroli.' : 'Wklej ścieżkę w pasku adresu Eksploratora plików. Gotowy film i pozostałe pliki pojawią się tu po zakończeniu produkcji.'}</p>
          </section> : null}
          {clipboardError ? <p role="alert" className="text-sm text-error">{clipboardError}</p> : null}
          {packageReady && localOnly && job.request?.project === 'wealth_blueprint' ? <StudioPresentationRepair key={jobId} jobId={jobId} onChanged={() => load(true)} /> : null}
          {job.request ? <ProductionFeedbackPanel key={job.id} jobId={job.id} hasSeries={Boolean(job.request.series_key)} storyMode={storyPreview} /> : null}
          {job.request && !localOnly && blockerKind !== 'integrity' && !reviewError && !error ? <PublicationPanel key={`publication-${job.id}`} jobId={job.id} /> : null}
        </div>
      </div>
    </div>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  approveProductionAssetCandidate,
  buildProductionReviewArtifactUrl,
  fetchProductionAssetCandidates,
  fetchProductionAssetRejectReasons,
  rejectProductionAssetCandidate,
  type ProductionAssetCandidate,
} from '../lib/api'

const REJECT_REASON_LABELS: Record<string, string> = {
  FACE_DRIFT: 'Niezgodne rysy twarzy',
  OUTFIT_DRIFT: 'Niezgodny strój',
  COLOR_DRIFT: 'Niezgodne kolory',
  ANATOMY: 'Błąd anatomii',
  HAND_ERROR: 'Błąd dłoni',
  ACCESSORY_ERROR: 'Błąd dodatków',
  STYLE_MISMATCH: 'Niezgodny styl',
  LOW_QUALITY: 'Niska jakość',
  BACKGROUND_ERROR: 'Błąd tła',
  ALPHA_QC_FAIL: 'Błąd przezroczystości',
  OTHER: 'Inny powód',
}

const QUALITY_LABELS: Record<string, string> = {
  AWAITING_REVIEW: 'Czeka na ręczną ocenę',
  TECHNICALLY_VALID: 'Kontrola techniczna zaliczona',
  GENERATED: 'Wygenerowana, wymaga kontroli technicznej',
  NEEDS_REPROCESS: 'Wymaga poprawy technicznej',
  REJECTED: 'Odrzucona',
  APPROVED: 'Zatwierdzona',
  SUPERSEDED: 'Zastąpiona',
}

const QC_LABELS: Record<string, string> = {
  TECHNICALLY_VALID: 'zaliczona',
  PRE_GATE_FAIL: 'niezaliczona',
  NEEDS_REPROCESS: 'wymaga poprawy',
  REJECTED: 'odrzucona przez kontrolę jakości',
}

function candidateStatus(candidate: ProductionAssetCandidate): string {
  const automaticReview = candidate.reviewed_by?.startsWith('atnos_automatic_visual_qc:') === true
  if (candidate.quality_status === 'APPROVED' && automaticReview) {
    return 'Zakwalifikowana automatycznie do tej produkcji'
  }
  if (candidate.quality_status === 'REJECTED' && automaticReview) {
    return 'Odrzucona przez automatyczną ocenę obrazu'
  }
  if (candidate.quality_status === 'GENERATED' && candidate.qc_verdict === 'PRE_GATE_FAIL') {
    return 'Nie przeszła kontroli technicznej'
  }
  if (candidate.quality_status === 'REJECTED' && candidate.qc_verdict === 'REJECTED') {
    return 'Odrzucona przez kontrolę jakości'
  }
  if (candidate.quality_status === 'REJECTED' && candidate.reviewed_by) {
    return 'Odrzucona po ręcznej ocenie'
  }
  return QUALITY_LABELS[candidate.quality_status] ?? candidate.quality_status.replaceAll('_', ' ').toLowerCase()
}

export function ProductionAssetReviewPanel({ jobId, refreshVersion, emptyMessage }: {
  jobId: string
  refreshVersion?: string | null
  emptyMessage?: string | null
}) {
  const [candidates, setCandidates] = useState<ProductionAssetCandidate[]>([])
  const [reasons, setReasons] = useState<string[]>([])
  const [reasonByAsset, setReasonByAsset] = useState<Record<string, string>>({})
  const [failedImages, setFailedImages] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [busyAssetId, setBusyAssetId] = useState<string | null>(null)
  const loadSequence = useRef(0)

  const reload = useCallback(async () => {
    const sequence = ++loadSequence.current
    try {
      const result = await fetchProductionAssetCandidates(jobId)
      if (sequence !== loadSequence.current) return
      setCandidates(result.candidates)
      setLoadError('')
      setFailedImages({})
      if (result.candidates.some(item => item.quality_status === 'AWAITING_REVIEW' && item.can_reject)) {
        const choices = await fetchProductionAssetRejectReasons()
        if (sequence !== loadSequence.current) return
        setReasons(choices.reasons)
      }
    } catch (caught) {
      if (sequence !== loadSequence.current) return
      setLoadError(caught instanceof Error ? caught.message : 'Nie udało się odczytać grafik do oceny.')
    } finally {
      if (sequence === loadSequence.current) setLoading(false)
    }
  }, [jobId])

  useEffect(() => {
    void reload()
    return () => { ++loadSequence.current }
  }, [reload, refreshVersion])

  const decide = async (assetId: string, action: 'approve' | 'reject') => {
    if (busyAssetId) return
    const candidate = candidates.find(item => item.asset_id === assetId)
    if (!candidate || candidate.quality_status !== 'AWAITING_REVIEW' || !candidate.image_url || failedImages[assetId]) return
    if (action === 'approve' && !candidate.can_approve) return
    if (action === 'reject' && (!candidate.can_reject || !reasons.includes(reasonByAsset[assetId]))) return
    setBusyAssetId(assetId)
    setActionError('')
    try {
      if (action === 'approve') {
        await approveProductionAssetCandidate(assetId)
      } else {
        await rejectProductionAssetCandidate(assetId, reasonByAsset[assetId])
      }
      await reload()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : 'Nie udało się zapisać decyzji o grafice.')
    } finally {
      setBusyAssetId(null)
    }
  }

  if (loading) return <p className="text-sm text-text-secondary">Wczytywanie grafik do oceny…</p>
  if (!candidates.length && !loadError && !emptyMessage) return null

  return (
    <section aria-label={candidates.length ? 'Ocena wygenerowanych grafik' : 'Stan przygotowania grafik'} className="mb-5 border border-border-default bg-panel p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">{candidates.length ? 'Grafiki do oceny' : 'Stan grafik'}</h2>
        <button type="button" onClick={() => void reload()} className="border border-border-default px-3 py-2 text-xs text-text-secondary hover:text-accent-primary">Odśwież statusy</button>
      </div>
      <p className="mt-2 text-sm text-text-secondary">Ocena automatyczna kwalifikuje grafikę do produkcji i nie oznacza Twojej akceptacji wyglądu. Grafiki oczekujące na ręczną ocenę zatwierdzisz lub odrzucisz poniżej.</p>
      {loadError ? <p role="alert" className="mt-3 text-sm text-error">{loadError}</p> : null}
      {actionError ? <p role="alert" className="mt-3 text-sm text-error">{actionError}</p> : null}
      {!candidates.length && !loadError && emptyMessage
        ? <p role="status" className="mt-4 border border-border-default bg-void p-3 text-sm leading-6 text-text-secondary">{emptyMessage}</p>
        : null}
      <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {candidates.map(candidate => {
          const imageUrl = buildProductionReviewArtifactUrl(candidate.image_url)
          const imageReady = Boolean(imageUrl) && !failedImages[candidate.asset_id]
          const eligible = candidate.quality_status === 'AWAITING_REVIEW' && imageReady
          return <article key={`${candidate.scene_id}:${candidate.asset_id}`} className="min-w-0 border border-border-default bg-void p-3">
            <div className="aspect-square overflow-hidden bg-black">
              {imageReady ? <img src={imageUrl!} alt={`Grafika do sceny ${candidate.scene_id}`} loading="lazy" onError={() => setFailedImages(current => ({ ...current, [candidate.asset_id]: true }))} className="h-full w-full object-contain" />
                : <div className="flex h-full items-center justify-center p-3 text-center text-sm text-text-tertiary">Podgląd grafiki niedostępny</div>}
            </div>
            <div className="mt-3 text-sm font-semibold">Scena {candidate.scene_id}</div>
            <div className="mt-1 text-sm text-text-secondary">{candidateStatus(candidate)}</div>
            {candidate.reviewed_by?.startsWith('atnos_automatic_visual_qc:') ? <p className="mt-1 text-xs text-text-tertiary">Akceptacja wyglądu przez użytkownika: niewykonana</p> : null}
            <div className="mt-1 break-all font-mono text-[10px] text-text-tertiary">{candidate.asset_id}</div>
            {candidate.qc_verdict ? <p className="mt-2 text-xs text-text-secondary">Kontrola techniczna: {QC_LABELS[candidate.qc_verdict] ?? candidate.qc_verdict}</p> : null}
            {candidate.qc_failed.length ? <p className="mt-1 text-xs text-text-secondary">Niezaliczone kontrole: {candidate.qc_failed.join(', ')}</p> : null}
            {candidate.reject_reason ? <p className="mt-1 text-xs text-text-secondary">Powód odrzucenia: {REJECT_REASON_LABELS[candidate.reject_reason] ?? candidate.reject_reason}</p> : null}
            {candidate.approval_blocker ? <p className="mt-2 text-xs text-warning">{candidate.approval_blocker}</p> : null}
            {eligible && (candidate.can_approve || candidate.can_reject) ? <div className="mt-3 space-y-2">
              {candidate.can_approve ? <button type="button" disabled={Boolean(busyAssetId)} onClick={() => void decide(candidate.asset_id, 'approve')} className="w-full border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:opacity-50">Zatwierdź grafikę</button> : null}
              {candidate.can_reject ? <div className="flex gap-2">
                <label className="sr-only" htmlFor={`reject-reason-${candidate.asset_id}`}>Powód odrzucenia sceny {candidate.scene_id}</label>
                <select id={`reject-reason-${candidate.asset_id}`} value={reasonByAsset[candidate.asset_id] ?? ''} onChange={event => setReasonByAsset(current => ({ ...current, [candidate.asset_id]: event.target.value }))} className="min-w-0 flex-1 border border-border-default bg-panel px-2 py-2 text-sm text-text-primary">
                  <option value="">Wybierz powód</option>
                  {reasons.map(reason => <option key={reason} value={reason}>{REJECT_REASON_LABELS[reason] ?? reason}</option>)}
                </select>
                <button type="button" disabled={Boolean(busyAssetId) || !reasons.includes(reasonByAsset[candidate.asset_id])} onClick={() => void decide(candidate.asset_id, 'reject')} className="border border-error px-3 py-2 text-sm text-error disabled:opacity-50">Odrzuć</button>
              </div> : null}
            </div> : null}
          </article>
        })}
      </div>
    </section>
  )
}

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { ProductionJobPayload, ProductionReviewPackage } from '../lib/api'
import StudioRoute from './Studio'

vi.mock('../components/PublicationPanel', () => ({ default: () => <div data-testid="publication-panel" /> }))
vi.mock('../components/ProductionFeedbackPanel', () => ({ default: () => <div data-testid="feedback-panel" /> }))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchProductionStudioJob: vi.fn(),
    fetchProductionStudioReviewPackage: vi.fn(),
    fetchProductionAssetCandidates: vi.fn(),
    fetchProductionAssetRejectReasons: vi.fn(),
    resumeProductionOrder: vi.fn(),
    reconcileProductionLocalOperation: vi.fn(),
    authorizeProductionCallLimit: vi.fn(),
    fetchProductionOrderRecovery: vi.fn(),
    repairProductionOrderScript: vi.fn(),
  }
})

const job = {
  id: 'prod-review',
  status: 'qa_failed',
  current_step: 'upload_package',
  topic_title: 'Czas wygrywa z modą',
  updated_at: '2026-08-30T20:00:00Z',
} as ProductionJobPayload

const review: ProductionReviewPackage = {
  job_id: 'prod-review',
  status: 'qa_failed',
  ready_for_review: false,
  title: 'Czas wygrywa z modą',
  description: 'Gotowy opis publikacyjny bez automatycznej publikacji.',
  tags: ['shorts', 'finanse'],
  video_ready: true,
  thumbnail_ready: true,
  video_url: '/api/production/jobs/prod-review/review-artifacts/video',
  thumbnail_url: '/api/production/jobs/prod-review/review-artifacts/thumbnail',
  blocking_failures: ['Wymagana ręczna akceptacja głosu'],
  quality_review_status: 'blocked',
  updated_at: '2026-08-30T20:00:00Z',
}

function renderStudio() {
  return render(
    <MemoryRouter initialEntries={['/studio/prod-review']}>
      <Routes>
        <Route path="/studio/:jobId" element={<StudioRoute />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('StudioRoute', () => {
  it('shows the bounded illustration action even with globally unlimited local work', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, local_work_unlimited: true,
      status: 'blocked', current_step: 'image_generation', worker_live: false,
      error: JSON.stringify({ category: 'execution_failure', message: 'local_image_retry_paused: three consecutive unaccepted local candidates in this run; inspect their QC before resuming' }),
      local_image_action: { status: 'verified', provider: 'comfyui-local', action_key: 'registered-action',
        max_new_requests: 3, used_new_requests: 3, remaining_new_requests: 0, pending_or_unknown: false } })
    renderStudio()
    const budget = await screen.findByTestId('local-image-action-budget')
    expect(budget).toHaveTextContent('3 z 3')
    expect(budget).toHaveTextContent('Pozostało: 0')
    expect(budget).toHaveTextContent('wznowienie nie przywraca prób')
    expect(screen.getByText('Ilustracje tej akcji: 3/3')).toBeInTheDocument()
    expect(screen.getByText('Grafiki wymagają oceny lub poprawki')).toBeInTheDocument()
  })

  it('does not report free image attempts when authorization integrity is unknown', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job,
      local_image_action: { status: 'integrity_blocked', provider: 'comfyui-local' } })
    renderStudio()
    expect(await screen.findByTestId('local-image-action-budget')).toHaveTextContent('Nie można potwierdzić integralności')
    expect(screen.queryByText(/Pozostało:/)).not.toBeInTheDocument()
  })

  it('shows unlimited local work separately from bounded paid or unknown calls', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, request: { delivery_mode: 'local_only' },
      local_work_unlimited: true, provider_calls: 291, limited_provider_calls: 2, max_provider_calls: 30 })
    renderStudio()
    expect(await screen.findByText(/Lokalna praca: bez limitu prób i wywołań/)).toBeInTheDocument()
    expect(screen.getByText('2 z 30')).toBeInTheDocument()
    expect(screen.queryByText('291 z 30')).not.toBeInTheDocument()
  })

  it('does not present an old script verdict as sufficient when current validation finds a story defect', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status: 'script_ready', current_step: 'script_ready', request: { production_scope: 'script_preview', delivery_mode: 'local_only' } })
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({ ...review, status: 'script_ready', blocking_failures: ['The requested opening must remain locked.'],
      video_ready: false, thumbnail_ready: false, video_url: null, thumbnail_url: null,
      script_preview: { text: 'The door was open.', word_count: 100, qa: { status: 'passed', human_review: 'not_performed' }, characters: [], segments: [] } })
    renderStudio()
    expect(await screen.findByText('Wymaga decyzji lub poprawki')).toBeInTheDocument()
    expect(screen.queryByText(/Scenariusz przeszedł automatyczną kontrolę fabuły/)).not.toBeInTheDocument()
    expect(screen.getByText('Scenariusz wymaga poprawki')).toBeInTheDocument()
    expect(screen.queryByText(/Scenariusz gotowy do przeczytania/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Scenariusz ukończony; gotowy do przeczytania/)).not.toBeInTheDocument()
    expect(screen.getByTestId('production-live-activity')).toBeInTheDocument()
  })

  it('shows a completed story script without implying that its film or thumbnail exists', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status: 'script_ready', current_step: 'script_ready', request: { production_scope: 'script_preview', delivery_mode: 'local_only' } })
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({ ...review, status: 'script_ready', blocking_failures: [],
      video_ready: false, thumbnail_ready: false, video_url: null, thumbnail_url: null,
      script_preview: { text: 'The fox found a lantern.', word_count: 100, qa: { status: 'passed', human_review: 'not_performed' }, characters: [],
        segments: [{ id: 's01', text: 'The fox found a lantern.', on_screen: 'A little light', visual_intent: 'A fox stands by a lantern.' }] } })
    const { container } = renderStudio()
    expect(await screen.findByText('The fox found a lantern.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Scenariusz opowieści' })).toBeInTheDocument()
    expect(screen.queryByText('Pakiet gotowy do oceny')).not.toBeInTheDocument()
    expect(screen.queryByText('Brak finalnego pliku MP4.')).not.toBeInTheDocument()
    expect(screen.queryByText('Miniatura nie jest jeszcze gotowa.')).not.toBeInTheDocument()
    expect(container.querySelector('video')).toBeNull()
    expect(screen.queryByTestId('publication-panel')).not.toBeInTheDocument()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(job)
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue(review)
    vi.mocked(api.fetchProductionOrderRecovery).mockResolvedValue(null)
    vi.mocked(api.fetchProductionAssetCandidates).mockResolvedValue({ job_id: 'prod-review', candidates: [] })
    vi.mocked(api.fetchProductionAssetRejectReasons).mockResolvedValue({ reasons: ['OTHER'] })
  })

  it('shows candidates from this Wealth Blueprint order in Studio', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status: 'blocked', current_step: 'asset_review', request: { project: 'wealth_blueprint' } })
    vi.mocked(api.fetchProductionAssetCandidates).mockResolvedValue({
      job_id: 'prod-review',
      candidates: [{
        scene_id: 's01', asset_id: 'candidate-1', acquisition_status: 'awaiting_approval',
        quality_status: 'AWAITING_REVIEW', qc_verdict: 'TECHNICALLY_VALID', qc_failed: [],
        reject_reason: null, reject_notes: '', reviewed_by: null, pre_gate: { passed: true },
        image_url: '/api/production/orders/prod-review/asset-candidates/candidate-1/image',
        can_approve: true, can_reject: true, approval_blocker: null,
      }],
    })
    renderStudio()

    expect(await screen.findByRole('heading', { name: 'Grafiki do oceny' })).toBeInTheDocument()
    expect(api.fetchProductionAssetCandidates).toHaveBeenCalledWith('prod-review')
    expect(screen.getByAltText('Grafika do sceny s01')).toHaveAttribute('src', expect.stringContaining('/prod-review/asset-candidates/candidate-1/image'))
  })

  it('does not send an unsupported image plan to an empty review list', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status: 'blocked', worker_live: false,
      current_step: 'asset_planning', request: { project: 'wealth_blueprint', delivery_mode: 'local_only' },
      error: JSON.stringify({ category: 'execution_failure', message:
        'IMAGE_PLAN_UNSUPPORTED: Image plan requires a supported physical illustration for every scene; no image was generated. Unsupported scenes: s01 (asset type: icon).' }) })
    vi.mocked(api.fetchProductionAssetCandidates).mockResolvedValue({ job_id: 'prod-review', candidates: [] })
    renderStudio()

    expect(await screen.findByText('Plan obrazu wymaga poprawki')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Zobacz stan przygotowania grafiki' })).toHaveAttribute('href', '#production-assets')
    expect(await screen.findByRole('heading', { name: 'Stan grafik' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Walidacja planu wykryła nieobsługiwaną scenę')
    expect(screen.queryByRole('heading', { name: 'Grafiki do oceny' })).not.toBeInTheDocument()
  })

  const scriptIssue = { code: 'practical_share', segment_ids: ['s02', 's03'], detail: 'Not enough concrete practical instruction' }
  const scriptRecovery = { job_id: 'prod-review', kind: 'script' as const,
    message: 'Scenariusz wymaga poprawki przed przygotowaniem materiałów.', issues: [scriptIssue],
    can_repair_script: true, repair_blocker: null, worker_live: false }
  const scriptBlockedJob = { ...job, status: 'blocked', current_step: 'script_repair/1', can_resume: true, worker_live: false,
    error: JSON.stringify({ category: 'invalid_output', message: `Repair did not resolve the flagged validation errors: ${JSON.stringify([scriptIssue])}` }) }

  it('explains a nested script error and uses the dedicated repair action with an optional note', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(scriptBlockedJob)
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({ ...review, blocking_failures: [scriptBlockedJob.error] })
    vi.mocked(api.fetchProductionOrderRecovery).mockResolvedValue(scriptRecovery)
    vi.mocked(api.repairProductionOrderScript).mockImplementation(() => new Promise(() => {}))
    renderStudio()
    const repairButton = await screen.findByRole('button', { name: 'Popraw scenariusz przez Atnosa' })
    expect(screen.getByText('Scenariusz wymaga poprawki')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Problemy scenariusza' })).toHaveTextContent('Za mało konkretnych działań dla widza.')
    expect(screen.getByRole('list', { name: 'Problemy scenariusza' })).toHaveTextContent('Sceny: s02, s03')
    expect(screen.queryByRole('button', { name: 'Wznów produkcję' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: 'Uwaga do poprawki (opcjonalnie)' }), { target: { value: '  Dodaj konkretną czynność.  ' } })
    fireEvent.click(repairButton)
    expect(screen.getByRole('button', { name: 'Zlecanie poprawki…' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Uwaga do poprawki (opcjonalnie)' })).toBeDisabled()
    fireEvent.submit(repairButton.closest('form')!)
    expect(api.repairProductionOrderScript).toHaveBeenCalledTimes(1)
    expect(api.repairProductionOrderScript).toHaveBeenCalledWith('prod-review', { note: 'Dodaj konkretną czynność.', requestKey: expect.any(String) })
    expect(api.resumeProductionOrder).not.toHaveBeenCalled()
  })

  it('keeps the same request key after an uncertain repair failure and makes the rejection readable', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(scriptBlockedJob)
    vi.mocked(api.fetchProductionOrderRecovery).mockResolvedValue(scriptRecovery)
    vi.mocked(api.repairProductionOrderScript).mockRejectedValue(new Error('Proces produkcji już działa.'))
    renderStudio()
    fireEvent.click(await screen.findByRole('button', { name: 'Popraw scenariusz przez Atnosa' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Proces produkcji już działa.')
    fireEvent.click(screen.getByRole('button', { name: 'Popraw scenariusz przez Atnosa' }))
    await waitFor(() => expect(api.repairProductionOrderScript).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.repairProductionOrderScript).mock.calls[1][1].requestKey).toBe(vi.mocked(api.repairProductionOrderScript).mock.calls[0][1].requestKey)
  })

  it('shows real worker progress after an accepted repair', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValueOnce(scriptBlockedJob)
      .mockResolvedValue({ ...scriptBlockedJob, status: 'running', worker_live: true })
    vi.mocked(api.fetchProductionOrderRecovery).mockResolvedValueOnce(scriptRecovery).mockResolvedValue({ ...scriptRecovery, can_repair_script: false, worker_live: true })
    vi.mocked(api.repairProductionOrderScript).mockResolvedValue({ ...scriptBlockedJob, status: 'running', worker_live: true })
    renderStudio()
    fireEvent.click(await screen.findByRole('button', { name: 'Popraw scenariusz przez Atnosa' }))
    expect(await screen.findByText('Atnos pracuje nad rolką')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Zlecono Atnosowi poprawkę scenariusza')
    expect(screen.queryByRole('button', { name: 'Popraw scenariusz przez Atnosa' })).not.toBeInTheDocument()
    expect(api.resumeProductionOrder).not.toHaveBeenCalled()
  })

  it('does not offer a pretend script repair when the backend cannot provide it', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(scriptBlockedJob)
    renderStudio()
    expect(await screen.findByText(/Naprawa scenariusza przez Atnosa nie jest teraz dostępna/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Popraw scenariusz przez Atnosa' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Wznów produkcję' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reset|pomiń|zatwierdź mimo/i })).not.toBeInTheDocument()
  })

  it('keeps the production visible when recovery lookup fails', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(scriptBlockedJob)
    vi.mocked(api.fetchProductionOrderRecovery).mockRejectedValue(new Error('network unavailable'))
    renderStudio()
    expect(await screen.findByRole('alert')).toHaveTextContent('Nie udało się sprawdzić dostępnych akcji naprawy.')
    expect(screen.getByText('Czas wygrywa z modą')).toBeInTheDocument()
    expect(screen.queryByText('Nie można otworzyć rolki')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Popraw scenariusz przez Atnosa' })).not.toBeInTheDocument()
  })

  it('does not resume or repair an order with an integrity blocker', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(scriptBlockedJob)
    vi.mocked(api.fetchProductionOrderRecovery).mockResolvedValue({ ...scriptRecovery, kind: 'integrity', issues: [],
      message: 'Pliki nie zgadzają się ze sprawdzonym manifestem.', can_repair_script: false, repair_blocker: 'Sprawdź spójność plików.' })
    renderStudio()
    expect(await screen.findByText('Sprawdź spójność plików')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Wznów produkcję' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Popraw scenariusz przez Atnosa' })).not.toBeInTheDocument()
  })

  it('keeps the integrity decision visible when the review endpoint rejects changed artifacts', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...scriptBlockedJob, request: { project: 'wealth_blueprint' } })
    vi.mocked(api.fetchProductionStudioReviewPackage).mockRejectedValue(new Error('Artefakt nie zgadza się z zatwierdzonym manifestem QA.'))
    vi.mocked(api.fetchProductionOrderRecovery).mockResolvedValue({ ...scriptRecovery, kind: 'integrity', issues: [],
      message: 'Pliki nie zgadzają się ze sprawdzonym manifestem.', can_repair_script: false, repair_blocker: 'Sprawdź spójność plików.' })
    const { container } = renderStudio()
    expect(await screen.findByText('Sprawdź spójność plików')).toBeInTheDocument()
    expect(screen.queryByText('Nie można otworzyć rolki')).not.toBeInTheDocument()
    expect(container.querySelector('video')).toBeNull()
    expect(screen.queryByTestId('publication-panel')).not.toBeInTheDocument()
    expect(screen.queryByText('Pakiet gotowy do oceny')).not.toBeInTheDocument()
  })

  it('shows only the finished Short, thumbnail, description and QA decision', async () => {
    const { container } = renderStudio()

    expect(await screen.findByText('Czas wygrywa z modą')).toBeInTheDocument()
    expect(screen.getByDisplayValue(review.description)).toBeInTheDocument()
    expect(screen.getByAltText('Miniatura: Czas wygrywa z modą')).toHaveAttribute(
      'src',
      expect.stringContaining('/review-artifacts/thumbnail'),
    )
    expect(container.querySelector('video')).toHaveAttribute('src', expect.stringContaining('/review-artifacts/video'))
    expect(screen.getByText('Wymagana ręczna akceptacja głosu')).toBeInTheDocument()
    expect(screen.queryByText(/model|cfg|upload/i)).not.toBeInTheDocument()
  })

  it('keeps the order accessible without claiming readiness when the review package cannot be loaded', async () => {
    vi.mocked(api.fetchProductionStudioReviewPackage).mockRejectedValue(new Error('Pakiet niedostępny'))
    const { container } = renderStudio()
    expect(await screen.findByRole('alert')).toHaveTextContent('Nie udało się odczytać pakietu podglądu')
    expect(screen.getByText('Czas wygrywa z modą')).toBeInTheDocument()
    expect(screen.queryByText('Nie można otworzyć rolki')).not.toBeInTheDocument()
    expect(screen.queryByText('Pakiet gotowy do oceny')).not.toBeInTheDocument()
    expect(container.querySelector('video')).toBeNull()
    expect(screen.queryByTestId('publication-panel')).not.toBeInTheDocument()
  })

  it('retries an unavailable order from Studio without creating a duplicate production', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockRejectedValueOnce(new Error('API niedostępne')).mockResolvedValue(job)
    renderStudio()
    expect(await screen.findByText('Nie można otworzyć rolki')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Spróbuj ponownie' }))
    expect(await screen.findByText('Czas wygrywa z modą')).toBeInTheDocument()
    expect(api.resumeProductionOrder).not.toHaveBeenCalled()
  })

  it('follows an autonomous blocked order into a real running worker without a manual resume', async () => {
    vi.useFakeTimers()
    const autonomousJob = { ...job, status: 'blocked', worker_live: false, can_resume: true,
      autonomous_recovery: { enabled: true, used: 2, maximum: 6, limit_reached: false, publication_enabled: false } }
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValueOnce(autonomousJob)
      .mockResolvedValue({ ...autonomousJob, status: 'running', worker_live: true, current_step: 'assets/image_review/s01/comparison' })
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({ ...review, blocking_failures: [], video_ready: false, thumbnail_ready: false, video_url: null, thumbnail_url: null })
    const view = renderStudio()
    try {
      await act(async () => { await Promise.resolve() })
      expect(screen.getByLabelText('Automatyczne odzyskiwanie produkcji')).toHaveTextContent('Wykorzystane wznowienia: 2 z 6')
      expect(screen.getByTestId('production-live-activity')).toHaveTextContent('Worker: nieaktywny')
      await act(async () => { vi.advanceTimersByTime(5000) })
      expect(screen.getByTestId('production-live-activity')).toHaveTextContent('Worker: żywy')
      expect(screen.getByText('Atnos pracuje nad rolką')).toBeInTheDocument()
      expect(api.resumeProductionOrder).not.toHaveBeenCalled()
      expect(api.repairProductionOrderScript).not.toHaveBeenCalled()
    } finally { view.unmount(); vi.useRealTimers() }
  })

  it.each([
    ['running', true, 'Produkcja nadal trwa. Wykorzystano limit automatycznych wznowień, więc kolejne zatrzymanie nie zostanie już wznowione automatycznie.'],
    ['blocked', false, 'Limit automatycznych wznowień został wykorzystany. Sprawdź przyczynę zatrzymania poniżej.'],
    ['running', undefined, 'Wykorzystano limit automatycznych wznowień. Bieżący status nie potwierdza, czy produkcja nadal trwa; odśwież go, aby to sprawdzić.'],
  ])('describes an exhausted recovery limit accurately for %s with worker_live=%s', async (status, workerLive, message) => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status, worker_live: workerLive,
      autonomous_recovery: { enabled: true, used: 20, maximum: 20, limit_reached: true, publication_enabled: false } })
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({ ...review, blocking_failures: [], video_ready: false, thumbnail_ready: false, video_url: null, thumbnail_url: null })
    renderStudio()
    const autonomy = await screen.findByLabelText('Automatyczne odzyskiwanie produkcji')
    expect(autonomy).toHaveTextContent(message)
    if (status === 'running' && workerLive === true) {
      expect(autonomy).not.toHaveTextContent('Sprawdź przyczynę zatrzymania poniżej')
    }
  })

  it('does not replace a known order with an error screen or allow stale resume after a transient status failure', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValueOnce({ ...job, status: 'blocked', can_resume: true, worker_live: false })
      .mockRejectedValueOnce(new Error('Brak odpowiedzi API'))
    renderStudio()
    expect(await screen.findByRole('button', { name: 'Wznów produkcję' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Odśwież' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ostatni odczyt, który może być nieaktualny')
    expect(screen.getByText('Czas wygrywa z modą')).toBeInTheDocument()
    expect(screen.queryByText('Nie można otworzyć rolki')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Wznów produkcję' })).not.toBeInTheDocument()
    expect(api.resumeProductionOrder).not.toHaveBeenCalled()
  })

  it('ignores an old order response when the user opens another production', async () => {
    let resolveOld!: (value: ProductionJobPayload) => void
    const oldResponse = new Promise<ProductionJobPayload>(resolve => { resolveOld = resolve })
    vi.mocked(api.fetchProductionStudioJob).mockImplementation(id => id === 'prod-review' ? oldResponse : Promise.resolve({ ...job, id, topic_title: 'Nowa produkcja' }))
    vi.mocked(api.fetchProductionStudioReviewPackage).mockImplementation(id => Promise.resolve({ ...review, job_id: id, title: id === 'prod-other' ? 'Nowa produkcja' : review.title }))
    render(<MemoryRouter initialEntries={['/studio/prod-review']}>
      <Link to="/studio/prod-other">Inna produkcja</Link>
      <Routes><Route path="/studio/:jobId" element={<StudioRoute />} /></Routes>
    </MemoryRouter>)
    fireEvent.click(screen.getByRole('link', { name: 'Inna produkcja' }))
    expect(await screen.findByText('Nowa produkcja')).toBeInTheDocument()
    await act(async () => { resolveOld(job) })
    expect(screen.getByText('Nowa produkcja')).toBeInTheDocument()
    expect(screen.queryByText('Czas wygrywa z modą')).not.toBeInTheDocument()
  })

  it('does not claim readiness while the script is still being created with no errors', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({
      ...job,
      status: 'running',
      current_step: 'script_draft',
      worker_live: true,
      provider_calls: 1,
      max_provider_calls: 100,
      steps: [{ step: 'script_draft', status: 'running', progress: 0, attempt: 1, message: null, started_at: '2026-09-16T22:00:00Z', completed_at: null }],
      updated_at: new Date().toISOString(),
      request: { production_scope: 'script_preview', delivery_mode: 'local_only' },
    })
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({ ...review, blocking_failures: [], video_ready: false, thumbnail_ready: false, video_url: null, thumbnail_url: null })
    renderStudio()
    expect(await screen.findByTestId('production-live-activity')).toHaveTextContent('Pisanie pierwszej wersji scenariusza')
    expect(screen.getByText('Atnos pisze opowieść')).toBeInTheDocument()
    expect(screen.getByTestId('production-live-activity')).toHaveTextContent('Atnos pracuje teraz')
    expect(screen.getByTestId('production-live-activity')).toHaveTextContent('Worker: żywy')
    expect(screen.queryByText('Pakiet gotowy do oceny')).not.toBeInTheDocument()
    expect(screen.queryByText(/Wynik pojawi się po zakończeniu/)).not.toBeInTheDocument()
  })

  it('resumes the same interrupted order and keeps a retry rejection visible', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status: 'blocked', can_resume: true, worker_live: false })
    vi.mocked(api.resumeProductionOrder).mockRejectedValue(new Error('Limit prób został wyczerpany.'))
    renderStudio()
    fireEvent.click(await screen.findByRole('button', { name: 'Wznów produkcję' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Limit prób został wyczerpany.')
    expect(api.resumeProductionOrder).toHaveBeenCalledTimes(1)
    expect(api.resumeProductionOrder).toHaveBeenCalledWith('prod-review')
    expect(screen.getByText('Wymagana ręczna akceptacja głosu')).toBeInTheDocument()
  })

  it('does not pretend work continues after the worker process dies', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status: 'running', current_step: 'script_repair/1', can_resume: true, worker_live: false })
    renderStudio()
    expect(await screen.findByText('Produkcja przerwana')).toBeInTheDocument()
    expect(screen.getByText(/Proces wykonawczy nie działa/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Wznów produkcję' })).toBeInTheDocument()
    expect(screen.queryByText('Atnos pracuje nad rolką')).not.toBeInTheDocument()
  })

  it.each([
    ['script_calculation_plan', 'Sprawdzanie założeń i obliczeń'],
    ['script_calculations', 'Sprawdzanie założeń i obliczeń'],
    ['scene_quality/s01/1', 'Poprawianie wadliwej sceny'],
    ['scene_timing/s03', 'Dopasowywanie scen do narracji'],
    ['render/s01', 'Montaż i renderowanie'],
    ['editing', 'Montaż i renderowanie'],
    ['qa', 'Kontrola jakości rolki'],
  ])('shows a useful Polish phase for %s', async (current_step, label) => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({
      ...job,
      status: 'running',
      current_step,
      worker_live: true,
      steps: [],
      updated_at: new Date().toISOString(),
    })
    renderStudio()
    expect((await screen.findAllByText(label)).length).toBeGreaterThan(0)
  })

  it.each([{ delivery_mode: 'local_only' }, { intake: { delivery_mode: 'local_only' } }])('hides publication for a local-only order', async request => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, request })
    renderStudio()
    expect(await screen.findByText('Tylko lokalnie. Publikacja wyłączona dla tej rolki.')).toBeInTheDocument()
    expect(screen.queryByTestId('publication-panel')).not.toBeInTheDocument()
    expect(screen.getByTestId('feedback-panel')).toBeInTheDocument()
  })

  it('keeps the existing publication panel for project-policy orders', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, request: { project: 'wealth_blueprint' } })
    renderStudio()
    expect(await screen.findByTestId('publication-panel')).toBeInTheDocument()
    expect(screen.getByText('Projekt zlecenia: Wealth Blueprint')).toBeInTheDocument()
    expect(screen.queryByText('Tylko lokalnie. Publikacja wyłączona dla tej rolki.')).not.toBeInTheDocument()
  })

  it('explains a stopped preflight and exposes its reserved budget and output folder', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status: 'blocked', current_step: 'preflight',
      request: { delivery_mode: 'local_only' }, reserved_cost_units: 125000, budget_limit_usd: 2,
      artifact_dir: 'E:\\Atnos\\production\\prod-review', can_resume: true, worker_live: false })
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({ ...review,
      blocking_failures: ['Preflight: FFmpeg or independent Whisper speech validation is unavailable'] })
    renderStudio()
    expect(await screen.findAllByText('Sprawdzanie połączeń i materiałów')).not.toHaveLength(0)
    expect(screen.getByText(/Sprawdź FFmpeg i Whisper/)).toBeInTheDocument()
    expect(screen.getByText('$0.13')).toBeInTheDocument()
    expect(screen.getByText('E:\\Atnos\\production\\prod-review')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Twoje produkcje' })).toHaveAttribute('href', '/create')
  })

  const limitedJob = { ...job, status: 'blocked', worker_live: false, can_resume: false,
    provider_calls: 30, max_provider_calls: 30, can_extend_call_limit: true,
    request: { delivery_mode: 'local_only' }, reserved_cost_units: 125000, budget_limit_usd: 2 }

  it('shows an unknown dispatched result without offering another production run', async () => {
    const message = 'Worker został przerwany po wysłaniu generacji. Wynik wymaga sprawdzenia; automatyczne ponowienie jest zablokowane.'
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...job, status: 'blocked',
      worker_live: false, can_resume: false, current_step: 'assets/image_local/flux2-trial/s01/test',
      error: JSON.stringify({ category: 'execution_outcome_unknown', message }) })
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({ ...review, blocking_failures: [message] })
    renderStudio()
    expect(await screen.findAllByText(message)).not.toHaveLength(0)
    expect(screen.queryByRole('button', { name: 'Wznów produkcję' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sprawdź wynik przerwanej generacji' })).toBeInTheDocument()
  })

  it('checks the interrupted local outcome separately from starting production', async () => {
    const stopped={...job,status:'blocked',worker_live:false,can_resume:false,
      current_step:'assets/image_local/flux2-trial/s01/test',
      error:JSON.stringify({category:'execution_outcome_unknown',message:'Wynik wymaga sprawdzenia.'})}
    const reconciled={...stopped,can_resume:true,error:JSON.stringify({category:'local_operation_abandoned',message:'Przerwano bez produktu.'})}
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(stopped)
    vi.mocked(api.fetchProductionStudioReviewPackage).mockResolvedValue({...review,blocking_failures:['Wynik wymaga sprawdzenia.']})
    vi.mocked(api.reconcileProductionLocalOperation).mockResolvedValue(reconciled)
    renderStudio()
    const button=await screen.findByRole('button',{name:'Sprawdź wynik przerwanej generacji'})
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(reconciled)
    fireEvent.click(button)
    await waitFor(()=>expect(api.reconcileProductionLocalOperation).toHaveBeenCalledTimes(1))
    expect(api.resumeProductionOrder).not.toHaveBeenCalled()
  })

  it('requires an explicit limit change and leaves starting the worker to a separate action', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(limitedJob)
    renderStudio()
    expect(await screen.findByText('30 z 30')).toBeInTheDocument()
    expect(api.authorizeProductionCallLimit).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Wznów produkcję' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Nowy łączny limit wywołań' }), { target: { value: '45' } })
    const approvedJob = { ...limitedJob, status: 'queued', can_resume: true, max_provider_calls: 45, can_extend_call_limit: false }
    vi.mocked(api.authorizeProductionCallLimit).mockResolvedValue(approvedJob)
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(approvedJob)
    fireEvent.click(screen.getByRole('button', { name: 'Zwiększ limit wywołań' }))
    expect(await screen.findByText('30 z 45')).toBeInTheDocument()
    expect(api.authorizeProductionCallLimit).toHaveBeenCalledTimes(1)
    expect(api.authorizeProductionCallLimit).toHaveBeenCalledWith('prod-review', 45)
    expect(api.resumeProductionOrder).not.toHaveBeenCalled()
    expect(screen.getByText('Oczekuje na uruchomienie')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Wznów produkcję' })).toBeInTheDocument()
    expect(screen.queryByText('Atnos pracuje nad rolką')).not.toBeInTheDocument()
    expect(screen.getByText('$0.13')).toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
  })

  it.each(['', '0', '30', '30.5', '100001'])('prevents invalid call-limit approval for %s', async value => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(limitedJob)
    renderStudio()
    const input = await screen.findByRole('spinbutton')
    fireEvent.change(input, { target: { value } })
    expect(screen.getByRole('button', { name: 'Zwiększ limit wywołań' })).toBeDisabled()
    expect(api.authorizeProductionCallLimit).not.toHaveBeenCalled()
  })

  it('shows a failed limit change and allows a retry without starting production', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(limitedJob)
    vi.mocked(api.authorizeProductionCallLimit).mockRejectedValue(new Error('Proces produkcji już działa.'))
    renderStudio()
    fireEvent.click(await screen.findByRole('button', { name: 'Zwiększ limit wywołań' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Proces produkcji już działa.')
    expect(screen.getByText('30 z 30')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Zwiększ limit wywołań' })).toBeEnabled()
    expect(api.resumeProductionOrder).not.toHaveBeenCalled()
  })

  it('disables editing and duplicate submission while the limit is saving', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue(limitedJob)
    vi.mocked(api.authorizeProductionCallLimit).mockImplementation(() => new Promise(() => {}))
    renderStudio()
    fireEvent.click(await screen.findByRole('button', { name: 'Zwiększ limit wywołań' }))
    const button = screen.getByRole('button', { name: 'Zapisywanie limitu…' })
    expect(button).toBeDisabled()
    expect(screen.getByRole('spinbutton')).toBeDisabled()
    fireEvent.click(button)
    expect(api.authorizeProductionCallLimit).toHaveBeenCalledTimes(1)
  })

  it('does not offer a limit change when another blocker needs attention', async () => {
    vi.mocked(api.fetchProductionStudioJob).mockResolvedValue({ ...limitedJob, can_extend_call_limit: false })
    renderStudio()
    expect(await screen.findByText('30 z 30')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Zwiększ limit wywołań' })).not.toBeInTheDocument()
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { ProductionAssetCandidate } from '../lib/api'
import { ProductionAssetReviewPanel } from './ProductionAssetReviewPanel'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchProductionAssetCandidates: vi.fn(),
    fetchProductionAssetRejectReasons: vi.fn(),
    approveProductionAssetCandidate: vi.fn(),
    rejectProductionAssetCandidate: vi.fn(),
  }
})

const pending: ProductionAssetCandidate = {
  scene_id: 's01', asset_id: 'candidate-1', acquisition_status: 'awaiting_approval',
  quality_status: 'AWAITING_REVIEW', qc_verdict: 'TECHNICALLY_VALID', qc_failed: [],
  reject_reason: null, reject_notes: '', reviewed_by: null, pre_gate: { passed: true },
  image_url: '/api/production/orders/prod-1/asset-candidates/candidate-1/image',
  can_approve: true, can_reject: true, approval_blocker: null,
}

const qcRejected: ProductionAssetCandidate = {
  ...pending, scene_id: 's02', asset_id: 'candidate-2', quality_status: 'REJECTED',
  qc_verdict: 'REJECTED', qc_failed: ['alpha_qc'], reject_reason: 'ALPHA_QC_FAIL',
  image_url: '/api/production/orders/prod-1/asset-candidates/candidate-2/image',
  can_approve: false, can_reject: false,
}

describe('ProductionAssetReviewPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchProductionAssetCandidates).mockResolvedValue({ job_id: 'prod-1', candidates: [pending, qcRejected] })
    vi.mocked(api.fetchProductionAssetRejectReasons).mockResolvedValue({ reasons: ['STYLE_MISMATCH'] })
  })

  it('shows job candidates and QC rejection, then rejects only after the operator selects a reason', async () => {
    const user = userEvent.setup()
    render(<ProductionAssetReviewPanel jobId="prod-1" />)

    expect(await screen.findByRole('heading', { name: 'Grafiki do oceny' })).toBeInTheDocument()
    expect(screen.getByAltText('Grafika do sceny s01')).toHaveAttribute('src', expect.stringContaining('/prod-1/asset-candidates/candidate-1/image'))
    expect(screen.getByAltText('Grafika do sceny s02')).toBeInTheDocument()
    expect(screen.getByText('Odrzucona przez kontrolę jakości')).toBeInTheDocument()
    expect(screen.getByText('Powód odrzucenia: Błąd przezroczystości')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Zatwierdź grafikę' })).toHaveLength(1)
    expect(api.approveProductionAssetCandidate).not.toHaveBeenCalled()
    expect(api.rejectProductionAssetCandidate).not.toHaveBeenCalled()

    const reject = screen.getByRole('button', { name: 'Odrzuć' })
    expect(reject).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Powód odrzucenia sceny s01'), 'STYLE_MISMATCH')
    await user.click(reject)
    await waitFor(() => expect(api.rejectProductionAssetCandidate).toHaveBeenCalledWith('candidate-1', 'STYLE_MISMATCH'))
  })

  it('approves only the clicked reviewable candidate and refreshes its status', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchProductionAssetCandidates)
      .mockResolvedValueOnce({ job_id: 'prod-1', candidates: [pending] })
      .mockResolvedValueOnce({ job_id: 'prod-1', candidates: [{ ...pending, quality_status: 'APPROVED', can_approve: false, can_reject: false }] })
    render(<ProductionAssetReviewPanel jobId="prod-1" />)

    const approve = await screen.findByRole('button', { name: 'Zatwierdź grafikę' })
    expect(api.approveProductionAssetCandidate).not.toHaveBeenCalled()
    await user.click(approve)
    await waitFor(() => expect(api.approveProductionAssetCandidate).toHaveBeenCalledOnce())
    expect(api.approveProductionAssetCandidate).toHaveBeenCalledWith('candidate-1')
    expect(await screen.findByText('Zatwierdzona')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Zatwierdź grafikę' })).not.toBeInTheDocument()
  })

  it('distinguishes automatic qualification from user acceptance and follows new candidates from order updates', async () => {
    vi.mocked(api.fetchProductionAssetCandidates).mockResolvedValueOnce({ job_id: 'prod-1', candidates: [] })
      .mockResolvedValue({ job_id: 'prod-1', candidates: [{ ...pending, quality_status: 'APPROVED',
        reviewed_by: 'atnos_automatic_visual_qc:0123456789', can_approve: false, can_reject: false }] })
    const view = render(<ProductionAssetReviewPanel jobId="prod-1" refreshVersion="first" />)
    await waitFor(() => expect(screen.queryByText('Wczytywanie grafik do oceny…')).not.toBeInTheDocument())
    view.rerender(<ProductionAssetReviewPanel jobId="prod-1" refreshVersion="second" />)
    expect(await screen.findByText('Zakwalifikowana automatycznie do tej produkcji')).toBeInTheDocument()
    expect(screen.getByText('Akceptacja wyglądu przez użytkownika: niewykonana')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Zatwierdź grafikę' })).not.toBeInTheDocument()
    expect(api.approveProductionAssetCandidate).not.toHaveBeenCalled()
  })

  it('shows an honest empty state when a stopped order has no image candidates', async () => {
    vi.mocked(api.fetchProductionAssetCandidates).mockResolvedValue({ job_id: 'prod-1', candidates: [] })
    render(<ProductionAssetReviewPanel jobId="prod-1" emptyMessage="Nie ma grafiki do oceny; plan zatrzymał się przed generowaniem." />)

    expect(await screen.findByRole('heading', { name: 'Stan grafik' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Nie ma grafiki do oceny')
    expect(screen.queryByRole('heading', { name: 'Grafiki do oceny' })).not.toBeInTheDocument()
  })
})

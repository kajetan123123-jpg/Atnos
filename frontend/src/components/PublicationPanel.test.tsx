import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import PublicationPanel from './PublicationPanel'

vi.mock('../lib/api', () => ({ fetchPublicationStatus: vi.fn(), savePublicationPolicy: vi.fn(), verifyPublicationChannel: vi.fn(), retryPublication: vi.fn() }))
const policy = { enabled: false, channel_id: 'UCxxxxxxxxxxxxxxxxxxxxxx', times_utc: ['14:00', '20:00'] }
const snapshot = { policy, jobs: [], write_blocked: false }
function show() { return render(<MemoryRouter><PublicationPanel jobId="prod-1" /></MemoryRouter>) }

describe('publication panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchPublicationStatus).mockResolvedValue(snapshot)
    vi.mocked(api.savePublicationPolicy).mockResolvedValue(policy)
  })

  it('saves public publication only after user enables it and saves', async () => {
    show()
    const checkbox = await screen.findByRole('checkbox')
    await waitFor(() => expect(checkbox).not.toBeDisabled())
    expect(checkbox).not.toBeChecked()
    fireEvent.click(checkbox)
    expect(api.savePublicationPolicy).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Zapisz ustawienia publikacji' }))
    expect(await screen.findByText(/Zapisano zgodę/)).toBeInTheDocument()
    expect(api.savePublicationPolicy).toHaveBeenCalledWith({ ...policy, enabled: true })
  })

  it('keeps failed OAuth visible without claiming publication success', async () => {
    vi.mocked(api.verifyPublicationChannel).mockResolvedValue({ status: 'blocked_user_action', message: 'Połącz ponownie YouTube: invalid_grant' })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Sprawdź połączenie z kanałem' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid_grant')
    expect(screen.queryByText('Opublikowano publicznie')).not.toBeInTheDocument()
  })

  it('shows schedule as scheduled, not published, and preserves edits on save failure', async () => {
    vi.mocked(api.fetchPublicationStatus).mockResolvedValue({ ...snapshot, jobs: [{ job_id: 'prod-1', status: 'scheduled', publish_at: '2026-09-07T14:00:00+00:00', error: null, youtube_url: 'https://www.youtube.com/watch?v=testvideo01' }] })
    vi.mocked(api.savePublicationPolicy).mockRejectedValue(new Error('Nie udało się zapisać'))
    show()
    expect(await screen.findByText('Termin potwierdzony przez YouTube')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Godziny publikacji UTC'), { target: { value: '15:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Zapisz ustawienia publikacji' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Nie udało się zapisać')
    expect(screen.getByLabelText('Godziny publikacji UTC')).toHaveValue('15:00')
    expect(screen.queryByText('Opublikowano publicznie')).not.toBeInTheDocument()
  })

  it('does not offer blind retry when upload result is unknown', async () => {
    vi.mocked(api.fetchPublicationStatus).mockResolvedValue({ ...snapshot, jobs: [{ job_id: 'prod-1', status: 'insufficient_evidence', publish_at: '2026-09-07T14:00:00Z', error: 'Sprawdź Studio', youtube_url: null }] })
    show()
    expect(await screen.findByText('Niepewny wynik wysyłki. Sprawdź YouTube Studio')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sprawdź ponownie po naprawie' })).not.toBeInTheDocument()
  })
})

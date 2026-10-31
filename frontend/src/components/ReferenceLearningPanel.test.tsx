import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import { ReferenceLearningPanel } from './ReferenceLearningPanel'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchReferenceLearningStatus: vi.fn(),
    startReferenceLearning: vi.fn(),
  }
})

describe('ReferenceLearningPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchReferenceLearningStatus).mockResolvedValue({
      status: 'not_started',
      phase: 'idle',
      target_count: 40,
      selected_count: 0,
      transcript_count: 0,
      visual_count: 0,
      failure_count: 0,
      policy: 'aggregate_patterns_only_no_source_copy',
    })
  })

  it('starts an exact 40-video Atnos learning batch', async () => {
    vi.mocked(api.startReferenceLearning).mockResolvedValue({
      status: 'running',
      phase: 'collecting',
      target_count: 40,
      selected_count: 7,
      transcript_count: 6,
      visual_count: 5,
      failure_count: 1,
      message: 'Atnos przeanalizował 7 z 40 wybranych materiałów.',
      policy: 'aggregate_patterns_only_no_source_copy',
    })
    render(<ReferenceLearningPanel />)

    fireEvent.click(await screen.findByRole('button', { name: 'Analizuj 40 filmów' }))

    await waitFor(() => expect(api.startReferenceLearning).toHaveBeenCalledTimes(1))
    expect(screen.getByText('7 / 40 · 18%')).toBeInTheDocument()
    expect(screen.getByText('Atnos analizuje')).toBeInTheDocument()
  })

  it('shows the active production integration after completion', async () => {
    vi.mocked(api.fetchReferenceLearningStatus).mockResolvedValue({
      status: 'completed',
      phase: 'completed',
      target_count: 40,
      selected_count: 40,
      transcript_count: 40,
      visual_count: 38,
      failure_count: 2,
      channel_distribution: { '@humphrey': 9, '@ThePlainBagel': 4 },
      policy: 'aggregate_patterns_only_no_source_copy',
    })
    render(<ReferenceLearningPanel />)

    expect(await screen.findByText('Profil gotowy')).toBeInTheDocument()
    expect(screen.getByText(/Profil jest aktywny w generatorze skryptu/)).toBeInTheDocument()
    expect(screen.getByText('Faktyczne pokrycie źródeł')).toBeInTheDocument()
    expect(screen.getByText('@humphrey')).toBeInTheDocument()
  })
})

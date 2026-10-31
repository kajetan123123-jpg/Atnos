import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProductionOrderStatusCard } from './ProductionOrderStatusCard'

const fetchProductionStudioJob = vi.fn()

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchProductionStudioJob: (...args: unknown[]) => fetchProductionStudioJob(...args),
  }
})

describe('ProductionOrderStatusCard', () => {
  beforeEach(() => {
    fetchProductionStudioJob.mockReset()
    fetchProductionStudioJob.mockResolvedValue({
      id: 'prod-1',
      status: 'running',
      current_step: 'script',
      worker_live: true,
      topic_title: 'Test topic',
      error: null,
    })
  })

  it('renders job status, studio link and refreshes on demand', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <ProductionOrderStatusCard
          jobId="prod-1"
          initialTopic="Test topic"
          initialStatus="queued"
          initialStep="queued"
          initialWorkerLive={false}
        />
      </MemoryRouter>,
    )

    expect(screen.getByTestId('production-order-status-card')).toBeInTheDocument()
    expect(screen.getByText('Test topic')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Otwórz studio/i })).toHaveAttribute(
      'href',
      '/studio/prod-1',
    )

    await waitFor(() => {
      expect(screen.getByText('Produkcja trwa')).toBeInTheDocument()
      expect(screen.getByText('żywy')).toBeInTheDocument()
      expect(screen.getByText('Atnos pracuje teraz')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /Odśwież/i }))
    await waitFor(() => {
      expect(fetchProductionStudioJob.mock.calls.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('renders nothing without job id', () => {
    const { container } = render(
      <MemoryRouter>
        <ProductionOrderStatusCard jobId="   " />
      </MemoryRouter>,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows a review hold from the backend error JSON without JSON syntax', async () => {
    const path = 'E:\\Atnos\\App\\data\\production\\scene_production_assets.json'
    fetchProductionStudioJob.mockResolvedValue({
      id: 'prod-1',
      status: 'blocked',
      current_step: 'asset_review',
      worker_live: false,
      topic_title: 'Test topic',
      error: JSON.stringify({
        category: 'awaiting_asset_review',
        message: `2 required scene assets await review. Details: ${path}`,
      }),
    })

    render(
      <MemoryRouter>
        <ProductionOrderStatusCard jobId="prod-1" />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Wygenerowane grafiki czekają na ocenę i zatwierdzenie/)).toBeInTheDocument()
    })
    expect(screen.getByTestId('production-order-status-card')).toHaveTextContent(path)
    expect(screen.getByTestId('production-order-status-card')).not.toHaveTextContent('"category"')
  })
})

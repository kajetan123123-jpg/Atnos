import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import PublicDataIntegrationsPanel from './PublicDataIntegrationsPanel'

const catalog = [
  {
    id: 'frankfurter',
    name: 'Frankfurter',
    category: 'Data',
    capabilities: ['FX_RATES'],
    freshness: 'delayed',
    cost: 'FREE',
    auth_type: 'none',
    permissions: ['read web'],
    docs_url: 'https://frankfurter.dev/',
    summary: 'Dzienne kursy walut.',
    limits: 'INTERNAL_THROTTLE',
    enabled: true,
    throttle_kind: 'INTERNAL_THROTTLE',
  },
]

describe('PublicDataIntegrationsPanel', () => {
  it('shows cost, capability and test action without an api key field', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => catalog,
      })),
    )
    render(<PublicDataIntegrationsPanel />)
    expect(await screen.findByText('Frankfurter')).toBeTruthy()
    expect(screen.getByText(/FX_RATES/)).toBeTruthy()
    expect(screen.getByText(/FREE/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeTruthy()
    expect(screen.queryByLabelText(/api key/i)).toBeNull()
  })

  it('shows Wikimedia as requiring contact setup and explains why', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => [
          {
            ...catalog[0],
            id: 'wikimedia',
            name: 'Wikipedia / Wikimedia',
            capabilities: ['ENCYCLOPEDIC_KNOWLEDGE'],
            setup_status: 'REQUIRES_SETUP',
            contact: '',
            app_name: 'Atnos',
          },
        ],
      })),
    )
    render(<PublicDataIntegrationsPanel />)
    expect(await screen.findByText(/REQUIRES_SETUP/)).toBeTruthy()
    expect(screen.getByText(/Wikimedia requires API clients to provide contact information/)).toBeTruthy()
    expect(screen.getByLabelText('Contact')).toBeTruthy()
  })

  it('keeps the panel mounted when the catalog request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline')
      }),
    )
    render(<PublicDataIntegrationsPanel />)
    expect(await screen.findByText('offline')).toBeTruthy()
    expect(screen.getByTestId('public-data-integrations')).toBeTruthy()
  })
})

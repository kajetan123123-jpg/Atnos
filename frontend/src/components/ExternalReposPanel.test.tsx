import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ExternalReposPanel from './ExternalReposPanel'

describe('ExternalReposPanel', () => {
  it('shows pinned repos, social cards and disabled skills', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo) => {
        const url = String(input)
        if (url.endsWith('/integrations/external-repos')) {
          return {
            ok: true,
            json: async () => [
              {
                id: 'awesome',
                name: 'Awesome',
                type: 'DISCOVERY_CATALOG',
                license: 'CC0-1.0',
                trust: 'TRUSTED',
                status: 'HEALTHY',
                commit: 'bc98e517ddca',
                docs_url: 'https://github.com/sindresorhus/awesome',
                summary: 'Katalog linków.',
                items_discovered: 674,
              },
            ],
          }
        }
        if (url.endsWith('/integrations/external-repos/social')) {
          return {
            ok: true,
            json: async () => [
              {
                id: 'youtube',
                name: 'YouTube',
                status: 'REQUIRES_SETUP',
                connected: false,
                capabilities: [{ id: 'SOCIAL_MEDIA_UPLOAD' }],
                password_accepted: false,
                publish: 'READY_FOR_APPROVAL',
                auth: 'oauth',
                account_count: 0,
                accounts: [],
              },
            ],
          }
        }
        if (url.endsWith('/integrations/external-repos/projects')) {
          return {
            ok: true,
            json: async () => ({
              projects: [
                { project_id: 'wealth_blueprint', platform_or_account: null },
                { project_id: 'wonder_crew', platform_or_account: null },
                { project_id: 'dark_chronicles', platform_or_account: null },
              ],
            }),
          }
        }
        if (url.endsWith('/integrations/external-repos/opentoonz')) {
          return {
            ok: true,
            json: async () => ({
              status: 'NOT_INSTALLED',
              path: null,
              detail: 'OpenToonz nie jest zainstalowany.',
            }),
          }
        }
        return {
          ok: true,
          json: async () => [
            {
              id: 'yt-retention',
              classification: 'NEW_CAPABILITY',
              enabled: false,
              trust: 'REVIEW_REQUIRED',
              commit: '6314d3c17dfa',
            },
          ],
        }
      }),
    )
    render(<ExternalReposPanel />)
    expect(await screen.findByText('Awesome')).toBeTruthy()
    expect(screen.getByText(/Odkryte linki: 674/)).toBeTruthy()
    expect(screen.getByText('YouTube')).toBeTruthy()
    expect(screen.getByText(/Hasło platformy nie jest przyjmowane/)).toBeTruthy()
    expect(screen.getByText('yt-retention')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Enable' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy()
    expect(screen.getByText('Wealth Blueprint')).toBeTruthy()
    expect(screen.getByText(/NOT_INSTALLED/)).toBeTruthy()
  })

  it('keeps the panel mounted when the catalog request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('offline')
      }),
    )
    render(<ExternalReposPanel />)
    expect(await screen.findByText('offline')).toBeTruthy()
    expect(screen.getByTestId('external-repos')).toBeTruthy()
  })
})

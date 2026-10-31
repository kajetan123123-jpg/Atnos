import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  ProjectPipelineSettings,
  experimentResultCopy,
  scopedYouTubeConnectionState,
  youtubeIntegrationPresentation,
} from './ContentMachineSettings'
import { DEMO_ACCOUNTS } from '../stores/accountStore'
import type { SettingsControlCenterPayload } from '../types/settings'
import type { ContentReadinessItem } from '../types/system'

function readiness(contentAccountId: string, connected = true): ContentReadinessItem {
  return {
    platformKey: 'youtube',
    displayName: 'YouTube Studio',
    status: connected ? 'supervised_ready' : 'blocked',
    availableActions: [],
    blockingReasons: connected ? [] : ['OAuth missing'],
    accountLabel: contentAccountId,
    connectorKey: 'youtube-studio',
    requiresApproval: true,
    lastCheckedAt: '2026-07-26T20:30:00Z',
    connected,
    needsAuth: !connected,
    missingGrant: false,
    connectionId: connected ? 61 : null,
    accountId: connected ? 67 : null,
    contentAccountId,
    grantedScopes: [],
    linkedProjects: [],
  }
}

function controlCenter(items: ContentReadinessItem[]): SettingsControlCenterPayload {
  return {
    oauthConnectors: [
      {
        connectorKey: 'youtube-studio',
        displayName: 'YouTube Studio',
        status: 'connected',
        enabled: true,
        configured: true,
        connectionId: 61,
        connectedAt: '2026-07-26T20:00:00Z',
        setupWarnings: [],
      },
    ],
    trustedAuto: { contentReadiness: { items } },
  } as unknown as SettingsControlCenterPayload
}

describe('ContentMachineSettings truthfulness', () => {
  const youtubeAccount = DEMO_ACCOUNTS.find((account) => account.id === 'youtube:account-c5034bf4@example.invalid')!
  const investmentAccount = DEMO_ACCOUNTS.find((account) => account.type === 'investment')!

  it('does not assign a global YouTube connection to a different active account', () => {
    const state = scopedYouTubeConnectionState(
      controlCenter([readiness('youtube:someone-else@example.com')]),
      youtubeAccount,
    )

    expect(state.connected).toBe(false)
    expect(state.verified).toBe(false)
    expect(state.observedAt).toBeNull()
  })

  it('verifies YouTube only from the exact account mapping and exposes evidence time', () => {
    const state = scopedYouTubeConnectionState(
      controlCenter([readiness('youtube:account-c5034bf4@example.invalid')]),
      youtubeAccount,
    )

    expect(state.connected).toBe(true)
    expect(state.verified).toBe(true)
    expect(state.observedAt).toBe('2026-07-26T20:30:00Z')
    expect(state.source).toContain('contentReadiness')
  })

  it('maps YouTube oauth facts to setup, live, degraded, error and running', () => {
    expect(youtubeIntegrationPresentation({ oauth: 'MISSING' }).status).toBe('REQUIRES_SETUP')
    expect(youtubeIntegrationPresentation({ oauth: 'EXPIRED' }).detail).toMatch(/Reconnect to restore analytics/i)
    expect(youtubeIntegrationPresentation({ oauth: 'TOKEN_REVOKED' }).status).toBe('REQUIRES_SETUP')
    expect(youtubeIntegrationPresentation({ oauth: 'AWAITING_USER_AUTHORIZATION' }).detail).toMatch(/Waiting for YouTube authorization/)
    expect(youtubeIntegrationPresentation({ oauth: 'CONNECTED', analytics: 'available', comments: 'available' }).status).toBe('LIVE')
    expect(youtubeIntegrationPresentation({ oauth: 'CONNECTED', analytics: 'available', comments: 'unavailable' }).status).toBe('DEGRADED')
    expect(youtubeIntegrationPresentation({ oauth: 'ERROR' }).status).toBe('ERROR')
    expect(youtubeIntegrationPresentation({ oauth: 'CONNECTED', syncing: true }).status).toBe('RUNNING')
    expect(experimentResultCopy({ winner: null, status: 'COLLECTING_DATA' })).toMatch(/insufficient observed metrics/i)
  })

  it('shows only investment pipeline controls for an investment project', () => {
    render(<ProjectPipelineSettings account={investmentAccount} />)

    expect(screen.getByText('Pipeline inwestycyjny')).toBeInTheDocument()
    expect(screen.queryByText('Model LLM')).not.toBeInTheDocument()
    expect(screen.queryByText('YouTube OAuth')).not.toBeInTheDocument()
  })

  it('does not invent production defaults when the profile has not been loaded', () => {
    render(<ProjectPipelineSettings account={youtubeAccount} />)

    expect(screen.getByText('Model LLM')).toBeInTheDocument()
    expect(screen.getAllByText(/Brak odczytu profilu produkcji/i)).toHaveLength(6)
    expect(screen.getByText('Nie wczytano wyboru autora i recenzenta')).toBeInTheDocument()
    expect(screen.getByText('Nie wczytano wyboru głosu')).toBeInTheDocument()
    expect(screen.queryByText('qwen3:14b')).not.toBeInTheDocument()
    expect(screen.queryByText(/F5-TTS/)).not.toBeInTheDocument()
    expect(screen.queryByText('1080x1920')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Otwórz produkcje i wybierz zlecenie' })).toHaveAttribute('href', '/create')
    expect(screen.getByRole('link', { name: 'Sprawdź Runtime produkcji' })).toHaveAttribute('href', '/settings?area=general&tab=production-runtime')
    expect(screen.queryByRole('textbox', { name: 'Model LLM' })).not.toBeInTheDocument()
    expect(screen.queryByText('YouTube OAuth')).not.toBeInTheDocument()
  })
})

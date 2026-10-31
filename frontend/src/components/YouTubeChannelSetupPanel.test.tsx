import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { ProjectYouTubeChannelIdentitySnapshot } from '../lib/api'
import { YouTubeChannelSetupPanel } from './YouTubeChannelSetupPanel'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchOAuthConnections: vi.fn(),
    saveYouTubeChannelIdentity: vi.fn(),
    confirmYouTubeChannelIdentity: vi.fn(),
    saveYouTubeChannelManifest: vi.fn(),
    approveYouTubeChannelManifest: vi.fn(),
    saveYouTubePublishingPolicy: vi.fn(),
    approveYouTubePublishingPolicy: vi.fn(),
  }
})

function snapshot(): ProjectYouTubeChannelIdentitySnapshot {
  return {
    status: 'not_started',
    identity: null,
    manifest: { status: 'not_started', missing_fields: ['mission'] },
    publishing_policy: { status: 'not_started', missing_fields: ['allowed_channel_ids'] },
    oauth: { status: 'not_linked', connection_id: null, ready: false },
    publish_eligibility: {
      status: 'blocked',
      eligible_by_local_policy: false,
      external_upload_checked: false,
      reasons: [],
      note: 'local only',
    },
  }
}

describe('YouTubeChannelSetupPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchOAuthConnections).mockResolvedValue([
      {
        id: 59,
        connectorKey: 'youtube-studio',
        displayName: 'YouTube Studio',
        status: 'connected',
        accountId: 65,
        connectedAt: '2026-07-16T12:00:00Z',
        metadata: { content_account_id: 'youtube:owner@example.com' },
      },
    ])
    vi.mocked(api.saveYouTubeChannelIdentity).mockResolvedValue(snapshot())
  })

  it('uses an existing OAuth connection and never asks the operator to type a UC id', async () => {
    const onUpdated = vi.fn().mockResolvedValue(undefined)
    render(<YouTubeChannelSetupPanel projectId={21} snapshot={snapshot()} onUpdated={onUpdated} />)

    await waitFor(() => expect(api.fetchOAuthConnections).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Aktywne połączenie YouTube OAuth'), { target: { value: '59' } })

    expect(screen.getByLabelText('Trwałe powiązanie konta')).toHaveValue('youtube:owner@example.com')
    expect(screen.queryByLabelText(/youtube_channel_id/i)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Zapisz powiązanie' }))

    await waitFor(() => {
      expect(api.saveYouTubeChannelIdentity).toHaveBeenCalledWith(21, expect.objectContaining({
        accountBindingKey: 'youtube:owner@example.com',
        oauthConnectionId: 59,
        youtubeChannelId: null,
      }))
    })
    expect(onUpdated).toHaveBeenCalled()
  })
})

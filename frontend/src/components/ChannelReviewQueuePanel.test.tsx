import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchChannelReviewQueue } from '../lib/api'
import { ChannelReviewQueuePanel } from './ChannelReviewQueuePanel'

vi.mock('../lib/api', () => ({
  fetchChannelReviewQueue: vi.fn(),
}))

const fetchChannelReviewQueueMock = vi.mocked(fetchChannelReviewQueue)

describe('ChannelReviewQueuePanel', () => {
  beforeEach(() => {
    fetchChannelReviewQueueMock.mockReset()
  })

  it('pokazuje gotowe shorty i elementy do naprawy', async () => {
    fetchChannelReviewQueueMock.mockResolvedValue({
      itemCount: 2,
      readyCount: 1,
      repairCount: 1,
      jsonPath: 'F:\\AtnosSystem\\data\\channel_ops\\review_queue.json',
      markdownPath: 'F:\\AtnosSystem\\data\\channel_ops\\review_queue.md',
      items: [
        {
          channelId: 'faceless-ai-pl',
          itemId: 'item-ready',
          topic: 'Gotowy short o lokalnym AI',
          status: 'manual_upload_ready',
          finalMp4: 'F:\\AtnosSystem\\shorts\\final.mp4',
          outputDir: 'F:\\AtnosSystem\\shorts',
          reviewPath: 'F:\\AtnosSystem\\shorts\\review.md',
          qualityScore: 92,
          visualQualityScore: 9.3,
          scriptQualityScore: 8.4,
          renderOk: true,
          youtubeShortsReady: true,
          uploadApprovalId: null,
          youtubeVideoId: null,
          updatedAt: '2026-05-21T12:00:00Z',
        },
        {
          channelId: 'faceless-ai-pl',
          itemId: 'item-repair',
          topic: 'Short wymagający naprawy',
          status: 'qa_failed',
          finalMp4: null,
          outputDir: null,
          reviewPath: null,
          qualityScore: null,
          visualQualityScore: null,
          scriptQualityScore: null,
          renderOk: false,
          youtubeShortsReady: false,
          uploadApprovalId: null,
          youtubeVideoId: null,
          updatedAt: '2026-05-21T12:05:00Z',
        },
      ],
    })

    render(<ChannelReviewQueuePanel />)

    expect(await screen.findByText('Shorty gotowe do oceny')).toBeInTheDocument()
    expect(screen.getByText('Gotowy short o lokalnym AI')).toBeInTheDocument()
    expect(screen.getByText('Short wymagający naprawy')).toBeInTheDocument()
    expect(screen.getByText('gotowe do review')).toBeInTheDocument()
    expect(screen.getByText('QA do naprawy')).toBeInTheDocument()
    expect(screen.getByText('F:\\AtnosSystem\\shorts\\final.mp4')).toBeInTheDocument()
  })

  it('pozwala odświeżyć kolejkę', async () => {
    const user = userEvent.setup()
    fetchChannelReviewQueueMock.mockResolvedValue({
      itemCount: 0,
      readyCount: 0,
      repairCount: 0,
      jsonPath: 'queue.json',
      markdownPath: 'queue.md',
      items: [],
    })

    render(<ChannelReviewQueuePanel />)

    await screen.findByText(/Brak shortów w kolejce review/i)
    await user.click(screen.getByRole('button', { name: /Odśwież/i }))

    await waitFor(() => {
      expect(fetchChannelReviewQueueMock).toHaveBeenCalledTimes(2)
    })
  })

  it('pokazuje błąd pobierania kolejki', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    fetchChannelReviewQueueMock.mockRejectedValue(new Error('backend unavailable'))

    render(<ChannelReviewQueuePanel />)

    expect(await screen.findByText('Nie można wczytać kolejki')).toBeInTheDocument()
    expect(screen.getByText('backend unavailable')).toBeInTheDocument()
    expect(errorSpy).toHaveBeenCalled()
    errorSpy.mockRestore()
  })
})

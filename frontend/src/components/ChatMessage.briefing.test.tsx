import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ChatMessage } from './ChatMessage'

describe('ChatMessage briefing', () => {
  it('renders structured briefing metadata through BriefingCard and resolves decisions', async () => {
    const user = userEvent.setup()
    const onResolveDecision = vi.fn().mockResolvedValue(undefined)
    const onRefreshBriefing = vi.fn().mockResolvedValue(undefined)

    render(
      <ChatMessage
        message={{
          id: 88,
          role: 'assistant',
          status: 'completed',
          content: 'Briefing fallback text',
          createdAt: '2026-05-17T08:00:00Z',
          metadataJson: {
            kind: 'briefing',
            payload: {
              generatedAt: '2026-05-17T08:00:00Z',
              projectId: 1,
              sections: [
                {
                  kind: 'decisions',
                  title: 'Decyzje i commitments',
                  summary: '1 aktywny commitment.',
                  items: [
                    {
                      label: 'Commitment',
                      detail: 'Dowiesc review PR do piatku.',
                      urgency: 'high',
                      actionUrl: null,
                      metadata: { decision_id: 12 },
                    },
                  ],
                },
              ],
            },
          },
        }}
        onResolveDecision={onResolveDecision}
        onRefreshBriefing={onRefreshBriefing}
      />,
    )

    expect(screen.getAllByText('BRIEFING')).toHaveLength(2)
    expect(screen.getByText('Dowiesc review PR do piatku.')).toBeInTheDocument()
    expect(screen.queryByText('Briefing fallback text')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Odswiez' }))
    expect(onRefreshBriefing).toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Oznacz jako zamkniete' }))
    await waitFor(() => {
      expect(onResolveDecision).toHaveBeenCalledWith(12)
    })
  })

  it('falls back to BriefingCard when a structured briefing message lost metadata', () => {
    render(
      <ChatMessage
        message={{
          id: 89,
          role: 'assistant',
          status: 'completed',
          createdAt: '2026-05-17T08:15:00Z',
          content: [
            'Decyzje i commitments: 2 zaleglych commitments, 4 aktywne wpisy lacznie.',
            '- [HIGH] Commitment: Dowiesc review PR-123 do 2026-05-16.',
            '- [LOW] Decyzja: Ustalamy plan dnia.',
          ].join('\n'),
          metadataJson: null,
        }}
      />,
    )

    expect(screen.getAllByText('BRIEFING')).toHaveLength(2)
    expect(
      screen.getByText('2 zaleglych commitments, 4 aktywne wpisy lacznie.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Dowiesc review PR-123 do 2026-05-16.')).toBeInTheDocument()
    expect(screen.getByText('Odswiez')).toBeInTheDocument()
  })
})

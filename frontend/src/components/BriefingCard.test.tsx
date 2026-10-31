import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { BriefingCard } from './BriefingCard'
import type { BriefingPayload } from '../types/briefing'

function buildPayload(overrides: Partial<BriefingPayload> = {}): BriefingPayload {
  return {
    generatedAt: '2026-05-17T08:00:00Z',
    projectId: 1,
    sections: [],
    ...overrides,
  }
}

describe('BriefingCard', () => {
  it('renders empty payload as a no-items state', () => {
    render(<BriefingCard payload={buildPayload()} />)

    expect(screen.getByText('BRIEFING')).toBeInTheDocument()
    expect(screen.getByText('Brak pozycji.')).toBeInTheDocument()
  })

  it('renders sections, actions and decision resolve affordance', async () => {
    const user = userEvent.setup()
    const onResolveDecision = vi.fn().mockResolvedValue(undefined)
    const onRefresh = vi.fn().mockResolvedValue(undefined)

    render(
      <BriefingCard
        payload={buildPayload({
          sections: [
            {
              kind: 'github',
              title: 'GitHub',
              summary: '1 PR czeka na review.',
              items: [
                {
                  label: 'Requested review - atnos/core',
                  detail: 'Naprawic fallback briefingu.',
                  urgency: 'high',
                  actionUrl: 'https://github.com/example/repo/pull/12',
                  metadata: {},
                },
              ],
            },
            {
              kind: 'decisions',
              title: 'Decyzje i commitments',
              summary: '1 commitment na dzis.',
              items: [
                {
                  label: 'Commitment',
                  detail: 'Dowiesc review PR do piatku.',
                  urgency: 'medium',
                  actionUrl: null,
                  metadata: { decision_id: 42 },
                },
              ],
            },
          ],
        })}
        onRefresh={onRefresh}
        onResolveDecision={onResolveDecision}
      />,
    )

    expect(screen.getByText('GitHub')).toBeInTheDocument()
    expect(screen.getByText('Requested review - atnos/core')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Otworz' })).toHaveAttribute(
      'href',
      'https://github.com/example/repo/pull/12',
    )

    await user.click(screen.getByRole('button', { name: 'Odswiez' }))
    expect(onRefresh).toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Oznacz jako zamkniete' }))

    await waitFor(() => {
      expect(onResolveDecision).toHaveBeenCalledWith(42)
    })
  })

  it('dismisses the card when requested', async () => {
    const user = userEvent.setup()
    const onDismiss = vi.fn()

    render(<BriefingCard payload={buildPayload()} onDismiss={onDismiss} />)

    await user.click(screen.getByRole('button', { name: 'Zamknij' }))

    expect(onDismiss).toHaveBeenCalled()
    expect(screen.queryByText('BRIEFING')).not.toBeInTheDocument()
  })

  it('renders repeated decision items without duplicate key warnings', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    try {
      render(
        <BriefingCard
          payload={buildPayload({
            sections: [
              {
                kind: 'decisions',
                title: 'Decyzje i commitments',
                summary: 'Powtarzalne wpisy z decision log.',
                items: [
                  {
                    label: 'Otwarte pytanie',
                    detail: 'Powiedz jakie karty sa otwarte w przegladarce chrome?.',
                    urgency: 'medium',
                    actionUrl: null,
                    metadata: {},
                  },
                  {
                    label: 'Otwarte pytanie',
                    detail: 'Powiedz jakie karty sa otwarte w przegladarce chrome?.',
                    urgency: 'medium',
                    actionUrl: null,
                    metadata: {},
                  },
                ],
              },
            ],
          })}
        />,
      )

      expect(screen.getAllByText('Otwarte pytanie')).toHaveLength(2)
      expect(
        consoleError.mock.calls.some((call) =>
          call.some((entry) => String(entry).includes('same key')),
        ),
      ).toBe(false)
    } finally {
      consoleError.mockRestore()
    }
  })
})

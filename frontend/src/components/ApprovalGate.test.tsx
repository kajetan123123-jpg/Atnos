import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApprovalGate } from './ApprovalGate'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'

describe('ApprovalGate', () => {
  beforeEach(() => {
    usePipelineStore.setState({
      currentJob: {
        id: 'job-1',
        title: 'Short o nowych workflow',
        createdAt: Date.now(),
        platform: 'youtube',
        awaitingApproval: true,
        thumbnailUrl: 'https://example.com/thumb.jpg',
        steps: [
          { id: 'research', state: 'done' },
          { id: 'script', state: 'done' },
          { id: 'tts', state: 'done' },
          { id: 'video', state: 'done' },
          { id: 'thumbnail', state: 'done' },
          { id: 'upload', state: 'pending', workItemId: 77 },
        ],
      },
      snapshot: {
        previewOnly: false,
        job: null,
        steps: [],
        awaitingApproval: true,
        status: 'awaiting_human',
        statusReason: '',
        approvalPackage: {
          workItem: { id: 77, title: 'Publikacja shorta' },
          project: {},
          risk: 'low',
          safetyFlags: [],
          reviewNotes: null,
          proposedNextAction: 'publish',
          actionBrokerPayloadPreview: {},
        },
      },
    })
    useSystemStore.setState({ agentStage: 'awaiting_human' })
    useToastStore.setState({ queue: [], toasts: [] })
  })

  it('renders the pipeline approval gate when upload is pending and stage awaits human', () => {
    render(
      <ApprovalGate snapshot={usePipelineStore.getState().snapshot} onApprove={vi.fn()} onReject={vi.fn()} />,
    )

    expect(screen.getByText('Publikacja shorta')).toBeInTheDocument()
    expect(screen.getByText('YOUTUBE')).toBeInTheDocument()
    expect(screen.getByText('30:00')).toBeInTheDocument()
  })

  it('opens a confirmation toast before rejecting the pipeline package', async () => {
    const user = userEvent.setup()
    const onReject = vi.fn()

    render(
      <ApprovalGate snapshot={usePipelineStore.getState().snapshot} onApprove={vi.fn()} onReject={onReject} />,
    )

    await user.click(screen.getByRole('button', { name: 'ODRZUĆ' }))

    expect(useToastStore.getState().toasts.at(-1)?.title).toBe('Odrzucić pakiet publikacji?')
    expect(onReject).not.toHaveBeenCalled()
  })

  it('still supports generic preview approvals', async () => {
    const onApproveGeneric = vi.fn()
    const onRejectGeneric = vi.fn()

    render(
      <ApprovalGate
        snapshot={null}
        genericPreview={{
          executionRunId: 41,
          projectId: 1,
          kind: 'file_write',
          title: 'Zapis pliku wymaga approval',
          summary: 'Atnos chce zapisać lokalny plik.',
          impactSummary: 'Zmiana zapisze nową wersję planu kampanii.',
          requiredApprovalLevel: 'single',
          previewPayload: {
            path: 'campaigns/plan.md',
          },
          status: 'waiting_for_input',
          createdAt: '2026-05-13T10:00:00Z',
          updatedAt: '2026-05-13T10:00:00Z',
        }}
        onApprove={() => undefined}
        onReject={() => undefined}
        onApproveGeneric={onApproveGeneric}
        onRejectGeneric={onRejectGeneric}
      />,
    )

    expect(screen.getByText(/Zapis pliku wymaga approval/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'ZATWIERDŹ' }))

    await waitFor(() => {
      expect(onApproveGeneric).toHaveBeenCalledWith(41)
    })
    expect(onRejectGeneric).not.toHaveBeenCalled()
  })
})

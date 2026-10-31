import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import { usePipelineStore } from '../stores/pipelineStore'
import { useToastStore } from '../stores/toastStore'
import { resetAllStores } from '../test/resetStores'
import type { PipelineSnapshot } from '../types/pipeline'
import PipelineRoute from './Pipeline'

vi.mock('../components/ContentPipeline', () => ({
  PipelineBody: () => <div data-testid="pipeline-body">pipeline-body</div>,
}))

vi.mock('../components/ApprovalGate', () => ({
  ApprovalGate: ({
    onApprove,
    onReject,
  }: {
    onApprove: (workItemId: number) => void | Promise<void>
    onReject: (workItemId: number) => void | Promise<void>
  }) => (
    <div>
      <button
        type="button"
        onClick={() => {
          void Promise.resolve(onApprove(77)).catch(() => undefined)
        }}
      >
        approve-test
      </button>
      <button
        type="button"
        onClick={() => {
          void Promise.resolve(onReject(77)).catch(() => undefined)
        }}
      >
        reject-test
      </button>
    </div>
  ),
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    updatePipelineApproval: vi.fn(),
  }
})

function primePipelineStore() {
  usePipelineStore.setState({
    currentJob: {
      id: 'job-1',
      title: 'Short AI',
      createdAt: Date.now(),
      platform: 'youtube',
      awaitingApproval: true,
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
    history: [],
  })
}

describe('PipelineRoute', () => {
  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
    primePipelineStore()
  })

  it('zamyka approval gate optymistycznie przy approve zanim backend odpowie', async () => {
    const user = userEvent.setup()
    let resolveApproval: ((value: PipelineSnapshot) => void) | null = null
    const approvalPromise = new Promise<PipelineSnapshot>((resolve) => {
      resolveApproval = resolve
    })
    vi.mocked(api.updatePipelineApproval).mockReturnValue(approvalPromise)

    render(<PipelineRoute />)

    expect(screen.getByTestId('pipeline-body')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'approve-test' }))

    expect(usePipelineStore.getState().currentJob?.awaitingApproval).toBe(false)

    await act(async () => {
      resolveApproval?.({
        previewOnly: false,
        job: null,
        steps: [],
        awaitingApproval: false,
        status: 'executing',
        statusReason: '',
        approvalPackage: null,
      })
      await approvalPromise
    })

    await waitFor(() => {
      expect(useToastStore.getState().toasts.at(-1)?.title).toBe('Preview zatwierdzony')
    })
  })

  it('przy bledzie reject przywraca approval gate i pokazuje toast bledu', async () => {
    const user = userEvent.setup()
    let rejectApproval: ((reason?: unknown) => void) | null = null
    const approvalPromise = new Promise<never>((_, reject) => {
      rejectApproval = reject
    })
    vi.mocked(api.updatePipelineApproval).mockReturnValue(approvalPromise)

    render(<PipelineRoute />)

    await user.click(screen.getByRole('button', { name: 'reject-test' }))

    expect(usePipelineStore.getState().currentJob?.awaitingApproval).toBe(false)

    await act(async () => {
      rejectApproval?.(new Error('Backend approval failed'))
      try {
        await approvalPromise
      } catch {
        // expected
      }
    })

    await waitFor(() => {
      expect(usePipelineStore.getState().currentJob?.awaitingApproval).toBe(true)
      expect(useToastStore.getState().toasts.at(-1)?.title).toBe(
        'Nie udało się odrzucić publikacji',
      )
    })
  })
})

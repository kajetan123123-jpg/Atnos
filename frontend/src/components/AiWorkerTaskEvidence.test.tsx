import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AiWorkerTask } from '../lib/api'
import { AiWorkerTaskEvidence } from './AiWorkerTaskEvidence'

function task(overrides: Partial<AiWorkerTask> = {}): AiWorkerTask {
  return {
    id: 82,
    taskType: 'safe_code_fix',
    status: 'rolled_back',
    priority: 'normal',
    requestedBy: 'operator',
    workerKey: 'codex',
    privacyMode: 'strict_local',
    parentTaskId: null,
    taskGroup: null,
    taskRole: 'executor',
    sequenceIndex: 0,
    requiresReview: true,
    verificationStatus: 'rolled_back',
    inputSummary: 'Napraw redakcję nagłówka.',
    redactedPrompt: '[REDACTED]',
    payloadJson: {},
    resultSummary: 'Propozycja została zweryfikowana i wycofana.',
    resultJson: {
      changed_paths: [
        'runtime-recovered/api/src/atnos/ai_workers/redaction.py',
        'runtime-recovered/api/tests/test_ai_workers_delegation.py',
      ],
      diff_path: 'F:\\AtnosSystem\\proposal.diff',
      diff_sha256: '7826357993d93e857102c34d0561b271083446970022dc0aebd38c46c7033fd9',
      proposal_state: 'rolled_back',
      verification: {
        status: 'passed',
        reason: 'Independent verification passed.',
        command: ['python.exe', '-m', 'pytest', 'test_ai_workers_delegation.py', '-q'],
        stdout_tail: '......... [100%]\n9 passed in 28.02s',
      },
      rollback: {
        status: 'rolled_back',
        main_worktree_modified: false,
        isolated_worktree_removed: true,
        apply_authorized: false,
      },
    },
    errorMessage: null,
    blockedReason: null,
    approvalRequired: true,
    traceId: 144,
    createdAt: '2026-07-17T03:13:00Z',
    updatedAt: '2026-07-17T03:34:17Z',
    startedAt: '2026-07-17T03:13:59Z',
    completedAt: '2026-07-17T03:15:50Z',
    runs: [{
      id: 91,
      workerKey: 'codex',
      status: 'completed',
      durationMs: 110929,
      resultSummary: 'Patch prepared.',
      errorMessage: null,
      startedAt: '2026-07-17T03:13:59Z',
      completedAt: '2026-07-17T03:15:50Z',
    }],
    children: [],
    ...overrides,
  }
}

describe('AiWorkerTaskEvidence', () => {
  it('pokazuje wyłącznie strukturalne dowody wykonania, testu i rollbacku', () => {
    render(<AiWorkerTaskEvidence task={task()} />)

    expect(screen.getByText('Evidence Log')).toBeInTheDocument()
    expect(screen.getByText('runtime-recovered/api/src/atnos/ai_workers/redaction.py')).toBeInTheDocument()
    expect(screen.getByText(/9 passed in 28.02s/)).toBeInTheDocument()
    expect(screen.getByText('7826357993d93e857102c34d0561b271083446970022dc0aebd38c46c7033fd9')).toBeInTheDocument()
    expect(screen.getByText('rolled_back')).toBeInTheDocument()
    expect(screen.getAllByText('nie')).toHaveLength(2)
    expect(screen.queryByText(/stderr_tail/)).not.toBeInTheDocument()
  })

    it('jawnie oznacza brak dowodów zamiast sugerować sukces', () => {
    render(<AiWorkerTaskEvidence task={task({ id: 83, status: 'queued', verificationStatus: 'pending', resultJson: null, runs: [] })} />)

    expect(screen.getByText('Worker nie zapisał jeszcze strukturalnych dowodów wykonania.')).toBeInTheDocument()
  })

  it('pokazuje Capabilities used z resultJson', () => {
    render(
      <AiWorkerTaskEvidence
        task={task({
          resultJson: {
            capabilities_used: [
              { capability: 'YOUTUBE_SCRIPT_QA', status: 'SUCCESS' },
              { capability: 'YOUTUBE_PACKAGING', status: 'SUCCESS' },
              { capability: 'SOCIAL_READ', status: 'REQUIRES_SETUP' },
              { capability: 'YOUTUBE_RETENTION_ANALYSIS', status: 'REQUIRES_DATA' },
            ],
          },
          runs: [],
        })}
      />,
    )

    expect(screen.getByText('Capabilities used')).toBeInTheDocument()
    expect(screen.getByText('YOUTUBE_SCRIPT_QA — SUCCESS')).toBeInTheDocument()
    expect(screen.getByText('SOCIAL_READ — REQUIRES_SETUP')).toBeInTheDocument()
    expect(screen.getByText('YOUTUBE_RETENTION_ANALYSIS — REQUIRES_DATA')).toBeInTheDocument()
  })
})

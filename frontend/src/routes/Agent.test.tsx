import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import { resetAllStores } from '../test/resetStores'
import type { AgentActionPreview, AgentStatusPayload } from '../types/system'
import AgentRoute from './Agent'

vi.mock('../hooks/useBreakpoint', () => ({
  useBreakpoint: () => ({ isWide: true, isMobile: false, isTablet: false, isDesktop: true, breakpoint: 'wide' }),
}))

vi.mock('../components/AgentActivityWindow', () => ({
  AgentActivityWindow: () => <div data-testid="agent-activity-window">activity</div>,
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    approveAgentAction: vi.fn(),
    emergencyStopAgent: vi.fn(),
    fetchAgentPendingApprovals: vi.fn(),
    fetchAgentRecentLogs: vi.fn(),
    fetchAgentStatus: vi.fn(),
    fetchAgentTools: vi.fn(),
    rejectAgentAction: vi.fn(),
  }
})

const statusPayload = {
  agent: {
    state: 'idle',
    activeTasks: 0,
    pendingApprovals: 1,
    errors: 0,
    lastDecisionAt: '2026-05-17T10:00:00Z',
    lastToolCallAt: '2026-05-17T10:01:00Z',
  },
  activeTasks: [],
  ubuntu: {
    ready: true,
    user: 'atnos',
    host: '198.51.100.216',
    workdir: '/home/atnos/atnos',
  },
  browser: {
    ready: true,
    status_reason: 'Browser runtime ready.',
  },
  shortPipeline: {
    ready: false,
    reason: 'Piper missing.',
  },
  youtubeAutomation: {
    ready: true,
    pending_approvals: 1,
    approved_approvals: 0,
    status_reason: 'n8n channel ops ready.',
  },
  monetizationFactory: {
    status: 'initialized',
    ready: true,
    revenueVector: 'automated_shorts_factory',
    stateMachineReady: true,
    channelOpsReady: true,
    shortsContractReady: true,
    approvalGateReady: true,
    youtubeReadinessStatus: 'missing_client_secret',
    privateUploadOnly: true,
    manualUploadReadySupported: true,
    missingRequirements: [],
    requiredArtifacts: ['hook_variants.json', 'short_asset_plan.json', 'visual_direction.md', 'quality_report.md'],
    statusPath: 'F:/Atnos/data/monetization/initialization_status.json',
    logPath: 'F:/Atnos/data/logs/monetization_init.jsonl',
    createdAt: '2026-05-26T18:11:48Z',
    statusReason: 'Factory can create local packages. YouTube OAuth is not ready.',
  },
  qualityRuntime: {
    ready: true,
    runsDir: 'F:/Atnos/data/quality-runs',
    statusReason: 'Quality runtime ready.',
    latestRun: {
      runId: '20260518T214000Z_agent',
      createdAt: '2026-05-18T21:40:00Z',
      task: 'Verify agent panel',
      area: 'agent-panel',
      status: 'DONE_VERIFIED',
      score: 9,
      reportPath: 'F:/Atnos/data/quality-runs/20260518T214000Z_agent.md',
      jsonPath: 'F:/Atnos/data/quality-runs/20260518T214000Z_agent.json',
      evidenceCount: 2,
    },
  },
  recentArtifacts: [
    {
      path: 'F:/Atnos/data/shorts/demo/final.mp4',
      storagePath: 'shorts/demo/final.mp4',
      filename: 'final.mp4',
      extension: '.mp4',
      sizeBytes: 2048,
      updatedAt: '2026-05-17T10:02:00Z',
    },
  ],
  pendingApprovals: [],
  errors: [],
  logs: {
    agent_decisions: 'F:/Atnos/data/logs/agent_decisions.jsonl',
    tool_calls: 'F:/Atnos/data/logs/tool_calls.jsonl',
  },
} satisfies AgentStatusPayload

const approval = {
  executionRunId: 77,
  projectId: 1,
  kind: 'file_write',
  title: 'Write report',
  summary: 'Save a report.',
  impactSummary: 'Local file write.',
  requiredApprovalLevel: 'single',
  previewPayload: {},
  status: 'waiting_for_input',
  createdAt: '2026-05-17T10:00:00Z',
  updatedAt: '2026-05-17T10:00:00Z',
} satisfies AgentActionPreview

describe('AgentRoute control panel', () => {
  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
    vi.mocked(api.fetchAgentStatus).mockResolvedValue(statusPayload)
    vi.mocked(api.fetchAgentRecentLogs).mockResolvedValue({
      decisions: [
        {
          timestamp: '2026-05-17T10:00:00Z',
          event: 'agent_route',
          raw: {},
        },
      ],
      toolCalls: [
        {
          timestamp: '2026-05-17T10:01:00Z',
          toolName: 'ubuntu_check',
          status: 'completed',
          success: true,
          raw: {},
        },
      ],
    })
    vi.mocked(api.fetchAgentTools).mockResolvedValue([
      {
        key: 'ubuntu_control',
        label: 'Ubuntu SSH sandbox',
        status: 'configured',
        riskLevel: 'high',
        logPath: 'F:/Atnos/data/logs/tool_calls.jsonl',
        actions: ['check_ubuntu_connection'],
      },
    ])
    vi.mocked(api.fetchAgentPendingApprovals).mockResolvedValue([approval])
    vi.mocked(api.approveAgentAction).mockResolvedValue({ ...approval, status: 'approved' })
    vi.mocked(api.rejectAgentAction).mockResolvedValue({ ...approval, status: 'blocked' })
    vi.mocked(api.emergencyStopAgent).mockResolvedValue({
      ok: true,
      event: 'agent_emergency_stop',
      interruptedRuns: [],
      ubuntuStop: { ok: true, result: { exit_code: 0 } },
      message: 'Emergency stop requested. No data deletion was performed.',
    })
  })

  it('renders agent status, tools, logs, approvals and artifacts', async () => {
    render(<AgentRoute />)

    expect(await screen.findByTestId('agent-status-panel')).toBeInTheDocument()
    expect(screen.getByText('Automated Shorts Factory')).toBeInTheDocument()
    expect(screen.getByText('Ubuntu SSH sandbox')).toBeInTheDocument()
    expect(screen.getByText('ubuntu_check')).toBeInTheDocument()
    expect(screen.getByText('Write report')).toBeInTheDocument()
    expect(screen.getByText('final.mp4')).toBeInTheDocument()
  })

  it('approves pending actions and triggers emergency stop', async () => {
    const user = userEvent.setup()
    render(<AgentRoute />)

    await screen.findByText('Write report')
    await user.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(api.approveAgentAction).toHaveBeenCalledWith(77))

    await user.click(screen.getByRole('button', { name: 'Emergency stop' }))
    await waitFor(() => expect(api.emergencyStopAgent).toHaveBeenCalled())
    expect(await screen.findByText(/No data deletion/i)).toBeInTheDocument()
  })
})

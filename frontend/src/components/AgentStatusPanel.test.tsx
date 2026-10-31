import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AgentStatusPanel } from './AgentStatusPanel'
import type { AgentStatusPayload } from '../types/system'

function buildStatus(): AgentStatusPayload {
  return {
    agent: {
      state: 'idle',
      activeTasks: 0,
      pendingApprovals: 0,
      errors: 0,
      lastDecisionAt: null,
      lastToolCallAt: null,
    },
    activeTasks: [],
    ubuntu: { ready: true, user: 'atnos', host: '198.51.100.216', workdir: '/home/atnos/atnos' },
    browser: { ready: true, status_reason: 'Browser ready.' },
    shortPipeline: { ready: true, reason: 'Runtime ready.' },
    youtubeAutomation: {
      ready: true,
      pending_approvals: 2,
      approved_approvals: 1,
      n8n_docker_status: 'docker_unavailable_virtualization_disabled',
      fallback_runner_status: 'available',
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
      runsDir: 'F:/Apps/Atnos/data/quality-runs',
      statusReason: 'Quality runtime ready.',
      latestRun: {
        runId: '20260518T212000Z_chat',
        createdAt: '2026-05-18T21:20:00Z',
        task: 'Verify chat route after navigation',
        area: 'frontend-chat',
        status: 'DONE_VERIFIED',
        score: 8.5,
        reportPath: 'F:/Apps/Atnos/data/quality-runs/20260518T212000Z_chat.md',
        jsonPath: 'F:/Apps/Atnos/data/quality-runs/20260518T212000Z_chat.json',
        evidenceCount: 2,
      },
    },
    recentArtifacts: [],
    pendingApprovals: [],
    errors: [],
    logs: {
      quality_runs: 'F:/Apps/Atnos/data/quality-runs',
    },
  }
}

describe('AgentStatusPanel', () => {
  it('surfaces the latest quality gate verdict', () => {
    render(<AgentStatusPanel status={buildStatus()} tools={[]} />)

    expect(screen.getByText('QUALITY GATE')).toBeInTheDocument()
    expect(screen.getByText('YOUTUBE OPS')).toBeInTheDocument()
    expect(screen.getByText('2 pending / 1 approved · fallback available')).toBeInTheDocument()
    expect(screen.getByText('SHORTS FACTORY')).toBeInTheDocument()
    expect(screen.getByText('MONETIZATION CORE')).toBeInTheDocument()
    expect(screen.getByText('hook_variants.json')).toBeInTheDocument()
    expect(screen.getByText('short_asset_plan.json')).toBeInTheDocument()
    expect(screen.getByText('docker_unavailable_virtualization_disabled')).toBeInTheDocument()
    expect(screen.getAllByText('DONE_VERIFIED').length).toBeGreaterThan(0)
    expect(screen.getByText('Verify chat route after navigation')).toBeInTheDocument()
    expect(screen.getByText(/8.5\/10/)).toBeInTheDocument()
  })
})

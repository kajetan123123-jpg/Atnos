import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { ProjectCommandOverview } from '../lib/api'
import { ProjectCommandPanel } from './ProjectCommandPanel'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchProjectCommandOverview: vi.fn(),
    fetchProjectPlatformBindings: vi.fn(),
  }
})

function buildOverview(): ProjectCommandOverview {
  return {
    project: {
      id: 17,
      name: 'Wealth Blueprint',
      slug: 'wealth-blueprint',
      description: 'Personal finance content project.',
      summary: null,
      status: 'active',
      provider_posture: 'prefer-local',
      persona_profile: 'strategist',
    },
    status: 'partially_verified',
    checked_at: '2026-07-16T12:00:00+00:00',
    runtime: {
      status: 'degraded',
      summary: { ready: 8, degraded: 1, missing: 0, blocked: 1 },
    } as ProjectCommandOverview['runtime'],
    readiness: null,
    blockers: [
      {
        source: 'runtime_health_service',
        key: 'ollama',
        title: 'Ollama',
        detail: 'Model nie odpowiada.',
        next_action: 'Uruchom health-check Ollamy.',
        verified: true,
      },
    ],
    next_step: {
      source: 'runtime_health_service',
      action: 'Uruchom health-check Ollamy.',
      reason: 'Ollama',
      requires_confirmation: false,
    },
    work_items: [
      {
        id: 3,
        title: 'Napraw błąd 404',
        summary: 'Napraw trasę API i uruchom testy.',
        next_step: 'Uruchom test API.',
        status: 'active',
        priority: 'P1',
        review_state: 'unreviewed',
        blocked_reason: null,
        updated_at: '2026-07-16T12:00:00+00:00',
      },
    ],
    delegations: [
      {
        id: 8,
        task_type: 'engineering_project_change',
        status: 'planned',
        worker_key: 'ollama',
        verification_status: 'planned',
        result_summary: 'Plan inżynierski został zapisany.',
        error_message: null,
        blocked_reason: null,
        created_at: '2026-07-16T12:00:00+00:00',
        updated_at: '2026-07-16T12:00:00+00:00',
      },
    ],
    knowledge: {
      query: 'wealth',
      library_matches: [
        {
          source: 'knowledge_library',
          title: 'Risk framework',
          path: 'finance/risk-framework.md',
          topic: 'investing',
          score: 12,
          excerpt: 'Define downside before upside.',
          verified: false,
          citation: 'knowledge/finance/risk-framework.md:L18-L20 (Operator note)',
          source_updated_at: '2026-07-16',
          indexed_at: '2026-07-16T12:00:00+00:00',
        },
      ],
      library_error: null,
      obsidian: { source: 'obsidian', configured: true, readable: true, note_count: 4, warnings: [], matches: [] },
    },
    recent_evidence: [
      {
        key: 'runtime_live_check',
        source: 'runtime_health_service',
        status: 'degraded',
        verified: true,
        checked_at: '2026-07-16T12:00:00+00:00',
        summary: 'Live runtime health snapshot executed for this command.',
      },
    ],
    brain: {
      state: {
        phase: 'safe_local_verification',
        readiness_status: 'ready',
        summary: 'Runtime=degraded; readiness=ready; verified_blockers=1; verified_evidence=2.',
        working: [],
        failing: [],
        risks: [],
        open_questions: [],
        next_step: {},
        observed_at: '2026-07-16T12:00:00+00:00',
        source_hash: 'state-hash',
        confidence_score: 1,
        verified: true,
      },
      goals: [
        {
          id: 1,
          title: 'Cel: Wealth Blueprint',
          description: 'Personal finance content project.',
          status: 'active',
          source: 'project_metadata',
          source_ref: 'project:17:metadata',
          observed_at: '2026-07-16T12:00:00+00:00',
          confidence_score: 0.8,
          verified: false,
        },
      ],
      blockers: [],
      facts: [
        {
          key: 'runtime:backend',
          content: 'FastAPI backend | status=ready',
          source: 'runtime_health_service',
          source_ref: 'backend',
          observed_at: '2026-07-16T12:00:00+00:00',
          expires_at: null,
          confidence_score: 1,
          verification_status: 'verified',
        },
      ],
      evidence: [],
      recent_traces: [],
    },
  }
}

describe('ProjectCommandPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchProjectCommandOverview).mockResolvedValue(buildOverview())
    vi.mocked(api.fetchProjectPlatformBindings).mockResolvedValue([])
  })

  it('pokazuje stan oparty na runtime i zweryfikowany bloker', async () => {
    render(<ProjectCommandPanel projectId={17} projectName="Wealth Blueprint" onCommand={vi.fn()} />)

    await waitFor(() => {
      expect(api.fetchProjectCommandOverview).toHaveBeenCalledWith(17)
    })

    expect(screen.getByText('CZĘŚCIOWO POTWIERDZONY')).toBeInTheDocument()
    expect(screen.getByText('Ollama')).toBeInTheDocument()
    expect(screen.getByText('Live runtime health snapshot executed for this command.')).toBeInTheDocument()
    expect(screen.getByText('Napraw błąd 404')).toBeInTheDocument()
    expect(screen.getByText(/#8 · ollama/i)).toBeInTheDocument()
    expect(screen.getByText('PROJECT BRAIN')).toBeInTheDocument()
    expect(screen.getByText('Cel: Wealth Blueprint')).toBeInTheDocument()
    expect(screen.getByText('Risk framework')).toBeInTheDocument()
    expect(screen.getByText('knowledge/finance/risk-framework.md:L18-L20 (Operator note)')).toBeInTheDocument()
    expect(screen.getByTestId('project-command-panel')).toHaveClass('shell-panel-strong')
    expect(screen.getByTestId('project-command-panel')).not.toHaveClass('shell-module')
  })

  it('deleguje szybkie polecenie do Project Command zamiast do ogólnego czatu', async () => {
    const onCommand = vi.fn()
    render(<ProjectCommandPanel projectId={17} projectName="Wealth Blueprint" onCommand={onCommand} />)

    await screen.findByText('Wealth Blueprint')
    fireEvent.click(screen.getByRole('button', { name: 'Co obecnie blokuje projekt?' }))

    expect(onCommand).toHaveBeenCalledWith('Co obecnie blokuje projekt?')
  })

  it('udostępnia deterministyczną weryfikację backendu jako akcję Project Command', async () => {
    const onCommand = vi.fn()
    render(<ProjectCommandPanel projectId={17} projectName="Wealth Blueprint" onCommand={onCommand} />)

    await screen.findByText('Wealth Blueprint')
    fireEvent.click(screen.getByRole('button', { name: 'Uruchom testy backendu i pokaż dowody.' }))

    expect(onCommand).toHaveBeenCalledWith('Uruchom testy backendu i pokaż dowody.')
  })

  it('nie pokazuje fałszywego stanu bez wybranego projektu', () => {
    render(<ProjectCommandPanel projectId={null} projectName="" onCommand={vi.fn()} />)

    expect(screen.getByText(/Wybierz projekt/i)).toBeInTheDocument()
    expect(screen.getByTestId('project-command-panel')).toHaveClass('shell-panel-strong')
    expect(api.fetchProjectCommandOverview).not.toHaveBeenCalled()
  })
})

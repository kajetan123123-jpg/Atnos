import { describe, expect, it, beforeEach, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import App from './App'
import * as api from './lib/api'
import { resetAllStores } from './test/resetStores'

vi.mock('./lib/api', async () => {
  const actual = await vi.importActual<typeof import('./lib/api')>('./lib/api')
  return {
    ...actual,
    fetchDistributionCockpit: vi.fn(),
    fetchMoneyMachineSummary: vi.fn(),
    fetchProductionJobs: vi.fn(),
    fetchProductionOperatingStandards: vi.fn(),
    fetchProductionPublicQualityStatus: vi.fn(),
    fetchProductionQualityRemediationStatus: vi.fn(),
    fetchProductionQualityReviewStatus: vi.fn(),
    fetchProjectReadiness: vi.fn(),
    fetchTopicQueue: vi.fn(),
  }
})

describe('Project Control tooling plan', () => {
  beforeEach(() => {
    window.localStorage.clear()
    resetAllStores()
    vi.mocked(api.fetchProjectReadiness).mockResolvedValue({
      generated_at: '2026-07-03T10:00:00Z',
      source: 'test',
      summary: {
        total: 1,
        ready: 0,
        limited: 1,
        blocked: 0,
        safe_mode: 1,
        youtube_private_upload_ready: 0,
        youtube_quality_review_blocked: 1,
        public_publish_enabled: false,
      },
      money_machine: {
        autopilot_enabled: false,
        human_approval_before_upload: true,
        public_upload_enabled: false,
        global_blockers: [],
        next_actions: ['Run quality auto-remediation.'],
      },
      projects: [
        {
          project_id: 'youtube:account-c5034bf4@example.invalid',
          project_type: 'youtube',
          name: 'Wealth Blueprint',
          description: 'Personal finance Shorts channel.',
          status: 'limited',
          safe_mode: true,
          capabilities: {
            can_research: true,
            can_generate_private: true,
            can_upload_private: false,
            can_publish_public: false,
            can_trade_or_purchase: false,
          },
          requirements: [],
          blockers: ['quality_review_blocked:1', 'quality_review:thumbnail_quality:local_template_thumbnail'],
          next_action: 'Run quality auto-remediation and regenerate blocked Shorts before review/upload.',
          operator_decision: {
            state: 'blocked_public_quality',
            label: 'Quality review is blocking review/upload',
            primary_action: 'Regenerate blocked Shorts.',
            safe_to_run_now: false,
            requires_owner_approval: true,
            risk_level: 'medium',
            reason: 'One job is blocked by visual quality review.',
            estimated_cost_pln: 0,
            available_balance_pln: 50,
            budget_allowed: true,
            budget_blockers: [],
            manual_checkout_only: true,
            card_data_storage: 'forbidden',
            public_quality_ready: false,
            public_upload_enabled: false,
            forbidden_actions: [],
          },
          evidence: {
            quality_review: {
              status: 'blocked',
              blocked_count: 1,
              passed_count: 2,
              total_count: 3,
              top_blockers: ['thumbnail_quality:local_template_thumbnail', 'semantic_first_frame_flag_generic_blur'],
              latest_blocked: [
                {
                  job_id: 'prod-visual-blocked',
                  topic_id: 'topic-finance',
                  quality_review_checked_at: '2026-07-03T09:55:00Z',
                  blockers: ['thumbnail_quality:local_template_thumbnail', 'semantic_first_frame_flag_generic_blur'],
                  next_action: 'Regenerate visual package with topic-specific finance scene.',
                },
              ],
              remediation_endpoint: 'POST /api/production/quality-remediation/run-once',
              scheduler_endpoint: 'POST /api/production/quality-review/run-once',
            },
          },
        },
      ],
    })
    vi.mocked(api.fetchProductionOperatingStandards).mockResolvedValue({
      status: 'ready',
      project_id: 'youtube:account-c5034bf4@example.invalid',
      project_type: 'youtube',
      generated_at: '2026-07-03T10:00:00Z',
      source: { exists: true, path: 'data/standards.json' },
      summary: 'Operational standards loaded.',
      sections: [],
      forbidden: [],
      metrics: {
        required_count: 0,
        blocked_until_configured_count: 0,
      },
      next_actions: [],
    })
    vi.mocked(api.fetchMoneyMachineSummary).mockResolvedValue({
      next_operator_action: 'blocked_public_quality',
      global_blockers: [],
      notes: [],
      accounts: [],
      active_jobs: [],
      private_upload_ready: [],
      recovery_ready: [],
      next_actions: [],
      totals: {
        accounts: 1,
        ready_private_upload_jobs: 0,
        public_ready_private_upload_jobs: 0,
        blocked_private_upload_jobs: 1,
        public_promotion_jobs: 0,
        active_jobs: 0,
        topic_buffer: 0,
        approved_topics: 0,
        due_analytics_followups: 0,
      },
    } as Awaited<ReturnType<typeof api.fetchMoneyMachineSummary>>)
    vi.mocked(api.fetchTopicQueue).mockResolvedValue([])
    vi.mocked(api.fetchProductionJobs).mockResolvedValue({ jobs: [] })
    vi.mocked(api.fetchProductionPublicQualityStatus).mockResolvedValue({} as Awaited<ReturnType<typeof api.fetchProductionPublicQualityStatus>>)
    vi.mocked(api.fetchDistributionCockpit).mockResolvedValue({} as Awaited<ReturnType<typeof api.fetchDistributionCockpit>>)
    vi.mocked(api.fetchProductionQualityReviewStatus).mockResolvedValue({} as Awaited<ReturnType<typeof api.fetchProductionQualityReviewStatus>>)
    vi.mocked(api.fetchProductionQualityRemediationStatus).mockResolvedValue({} as Awaited<ReturnType<typeof api.fetchProductionQualityRemediationStatus>>)
  })

  it('renders project tooling readiness from backend readiness payload', () => {
    render(
      <MemoryRouter initialEntries={['/projects/youtube%3Aatnos%40int.pl']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByText('Public quality gate')).toBeInTheDocument()
    expect(screen.getByText('Standardy operacyjne')).toBeInTheDocument()
    expect(screen.getByText('Stack narzedzi i zakupow')).toBeInTheDocument()
    expect(screen.getByText('Must-have teraz')).toBeInTheDocument()
    expect(screen.getAllByText('Premium voice').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('ComfyUI LTX/Wan or image-motion')).toBeInTheDocument()
  })

  it('surfaces blocked quality review backlog for YouTube projects', async () => {
    render(
      <MemoryRouter initialEntries={['/projects/youtube%3Aatnos%40int.pl']}>
        <App />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('Backlog jakości')).toBeInTheDocument()
    })
    expect(screen.getByText('Blokuje review')).toBeInTheDocument()
    expect(screen.getByText('prod-visual-blocked / topic-finance')).toBeInTheDocument()
    expect(screen.getByText(/POST \/api\/production\/quality-remediation\/run-once/)).toBeInTheDocument()
  })
})

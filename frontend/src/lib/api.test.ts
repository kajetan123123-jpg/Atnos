import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchBootstrap,
  fetchBootstrapShell,
  fetchMoneyMachineSummary,
  fetchProductionOperatingStandards,
  fetchProductionJob,
  fetchProductionStudioJob,
  fetchProductionStudioReviewPackage,
  fetchProductionPublicQualityStatus,
  fetchProductionQualityRemediationStatus,
  fetchProductionQualityReviewStatus,
  regenerateProductionJob,
  runMoneyMachineNext,
  runMoneyMachinePublication,
  runProductionContentLoopOnce,
  runProductionQualityRemediationOnce,
  runProductionQualityReviewOnce,
  sendChatTurn,
  startProduction,
  uploadProductionJobPrivate,
  updateSettingsRuntime,
} from './api'

describe('Studio production source', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
  afterEach(() => vi.unstubAllGlobals())

  it('reads a legacy production only when the durable order does not exist', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response('{"detail":"not found"}', { status: 404 }))
      .mockResolvedValueOnce(new Response('{"id":"legacy"}', { status: 200 }))
    expect(await fetchProductionStudioJob('legacy')).toEqual({ id: 'legacy' })
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(vi.mocked(fetch).mock.calls[1][0]).toEqual(expect.stringContaining('/jobs/legacy'))
  })

  it.each([409, 500])('does not hide a durable package error (%s) behind legacy data', async (status) => {
    vi.mocked(fetch).mockResolvedValue(new Response('{"detail":"QA artifact rejected"}', { status }))
    await expect(fetchProductionStudioReviewPackage('production')).rejects.toThrow('QA artifact rejected')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})

describe('api.sendChatTurn', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('maps runtime_error from backend payload', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        conversation_id: 101,
        conversation_title: 'Plan shortow AI',
        conversation_project_id: 1,
        user_message_id: 501,
        assistant_message_id: 502,
        user_message: 'Hej Atnos',
        user_message_attachment_only: false,
        assistant_message: '',
        selected_provider_key: 'ollama-local',
        selected_model_key: 'ollama-local/chat-default',
        routing_reason: 'prefer-local',
        runtime_error: {
          failure_type: 'request_timeout',
          provider_key: 'ollama-local',
          model_key: 'ollama-local/chat-default',
          runtime_model_name: 'atnos-core',
          user_title: 'Lokalny model nie odpowiedzial na czas',
          user_detail: 'Model lokalny przekroczyl limit czasu.',
          retryable: true,
          can_fallback: true,
          suggested_actions: ['Sprobuj ponownie'],
          diagnostics: {
            timeout_ms: 45000,
          },
        },
        skill_decision: {
          selected_skills: ['atnos:deep-research'],
          reasoning_summary: 'Wymaga bieżących źródeł.',
          rejected_skills: [],
          required_inputs: [],
          risks: [],
          next_action: 'Wyszukaj źródła.',
          confidence: 0.9,
        },
        traces: [
          {
            kind: 'skill',
            status: 'completed',
            metadata_json: {
              instruction_context: {
                instruction_status: 'loaded',
                loaded_instructions: [{ skill_id: 'atnos:deep-research' }],
                missing_instruction_skills: [],
              },
            },
          },
        ],
      }),
      text: async () => '',
    } as Response)

    const result = await sendChatTurn({
      content: 'Hej Atnos',
    })

    expect(result.runtimeError).toEqual({
      failureType: 'request_timeout',
      providerKey: 'ollama-local',
      modelKey: 'ollama-local/chat-default',
      runtimeModelName: 'atnos-core',
      userTitle: 'Lokalny model nie odpowiedzial na czas',
      userDetail: 'Model lokalny przekroczyl limit czasu.',
      retryable: true,
      canFallback: true,
      suggestedActions: ['Sprobuj ponownie'],
      diagnostics: {
        timeout_ms: 45000,
      },
    })
    expect(result.skillExecution).toEqual({
      selectedSkills: ['atnos:deep-research'],
      reasoningSummary: 'Wymaga bieżących źródeł.',
      instructionStatus: 'loaded',
      loadedInstructionSkills: ['atnos:deep-research'],
      missingInstructionSkills: [],
    })
  })
})

describe('api local runtime fetch retry', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('retries transient local fetch failures before succeeding', async () => {
    vi.mocked(fetch)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          ui_version: 'test',
          generated_at: '2026-05-15T00:00:00Z',
          projects: [],
          recent_conversations: [],
          runtime_summary: {
            voice_stt: {},
          },
          provider_cards: [],
          oauth_connectors: [],
          capabilities: {},
          pipeline: {},
          trusted_auto_summary: {},
          access_reality: null,
          ambient_state: null,
          remote_workers: [],
        }),
        text: async () => '',
      } as Response)

    const pending = fetchBootstrap()
    await vi.advanceTimersByTimeAsync(1050)
    await pending

    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('surfaces a stable local runtime error after retry budget is exhausted', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))

    const pending = fetchBootstrap().catch((error) => error)
    await vi.advanceTimersByTimeAsync(1050)
    const result = await pending

    expect(result).toBeInstanceOf(Error)
    expect((result as Error).message).toBe('Nie udało się połączyć z lokalnym runtime Atnosa.')
    expect(fetch).toHaveBeenCalledTimes(3)
  })
})

describe('api.startProduction', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('posts approved topic to backend production orchestrator', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        job_id: 'prod-123',
        account_id: 'acct-1',
        topic_id: 'topic-1',
        status: 'queued',
      }),
      text: async () => '',
    } as Response)

    const result = await startProduction({ accountId: 'acct-1', topicId: 'topic-1' })

    expect(result.job_id).toBe('prod-123')
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/start'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ account_id: 'acct-1', topic_id: 'topic-1' }),
      }),
    )
  })

  it('fetches production job status for dashboard polling', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 'prod-123',
        account_id: 'acct-1',
        topic_id: 'topic-1',
        topic_title: 'Test topic',
        status: 'qa_passed',
        current_step: 'upload_package',
        ready_for_review: true,
        ready_for_private_upload: true,
        steps: [{ step: 'qa', status: 'done', progress: 0.91, attempt: 1 }],
      }),
      text: async () => '',
    } as Response)

    const result = await fetchProductionJob('prod-123')

    expect(result.ready_for_private_upload).toBe(true)
    expect(result.steps[0].step).toBe('qa')
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/jobs/prod-123'),
      expect.any(Object),
    )
  })

  it('starts private upload for a QA-approved production job', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'uploaded_private',
        job_id: 'prod-123',
        youtube_video_id: 'yt-123',
        youtube_url: 'https://www.youtube.com/watch?v=yt-123',
        visibility: 'private',
      }),
      text: async () => '',
    } as Response)

    const result = await uploadProductionJobPrivate('prod-123')

    expect(result.status).toBe('uploaded_private')
    expect(result.visibility).toBe('private')
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/jobs/prod-123/upload-private'),
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('fetches compact money-machine operator summary', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'producing',
        public_upload_enabled: false,
        human_approval_before_upload: true,
        voice_provider_ready: true,
        cost_guard: {
          enabled: true,
          allowed: true,
          blocked: false,
          warning: false,
          currency: 'PLN',
          estimated_action_cost_pln: 4,
          monthly_spent_pln: 8,
          monthly_limit_pln: 200,
          monthly_remaining_pln: 192,
          daily_spent_pln: 4,
          daily_limit_pln: 25,
          daily_remaining_pln: 21,
        },
        totals: {
          accounts: 3,
          ready_private_upload_jobs: 22,
          public_ready_private_upload_jobs: 2,
          blocked_private_upload_jobs: 20,
          public_promotion_jobs: 6,
          active_jobs: 1,
          topic_buffer: 39,
          approved_topics: 2,
          due_analytics_followups: 0,
        },
        next_operator_action: 'review_and_execute_private_upload',
        active_jobs: [
          {
            account_id: 'acct-1',
            job_id: 'prod-active',
            topic_title: 'Active render',
            status: 'running',
            current_step: 'thumbnail',
            heartbeat_age_seconds: 42,
            stale_after_seconds: 3600,
            stale_candidate: false,
          },
        ],
        private_upload_ready: [{ job_id: 'prod-ready', topic_title: 'Ready', public_quality_blockers: [] }],
        recovery_ready: [],
        accounts: [
          {
            account_id: 'acct-1',
            channel_title: 'Wealth Blueprint',
            next_action: 'wait_for_active_production_job',
            ready_private_upload_count: 3,
            blockers: ['public_quality_gate_failed'],
            topic_counts: {
              buffer_count: 5,
              approved: 2,
            },
            job_counts: {
              active: 1,
              ready_private: 3,
            },
            signals: {
              youtube_trend_videos: 42,
              google_trends_keywords: 12,
            },
            analytics: {
              own_performance_rows: 0,
            },
            youtube_oauth: {
              ready: true,
              connected: true,
            },
            upload_capabilities: {
              captions_api: { ready: false, status: 'missing_optional_scope' },
              custom_thumbnail: { ready: false, status: 'blocked_by_youtube_channel_permission' },
            },
          },
        ],
        next_actions: [],
        global_blockers: [],
        notes: ['Private upload execution still requires approved_private_upload=true.'],
      }),
      text: async () => '',
    } as Response)

    const result = await fetchMoneyMachineSummary({ accountId: 'acct-1', includeOauth: true, maxUploads: 3 })

    expect(result.next_operator_action).toBe('review_and_execute_private_upload')
    expect(result.cost_guard?.monthly_remaining_pln).toBe(192)
    expect(result.cost_guard?.estimated_action_cost_pln).toBe(4)
    expect(result.active_jobs?.[0].current_step).toBe('thumbnail')
    expect(result.accounts[0].signals?.youtube_trend_videos).toBe(42)
    expect(result.accounts[0].upload_capabilities?.captions_api?.ready).toBe(false)
    expect(result.private_upload_ready[0].job_id).toBe('prod-ready')
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/money-machine/summary?account_id=acct-1&include_oauth=true&max_uploads=3'),
      expect.any(Object),
    )
  })

  it('queues production regeneration for blocked quality job', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'queued',
        source_job_id: 'prod-old',
        job_id: 'prod-new',
        account_id: 'acct-1',
        topic_id: 'topic-1',
        reason: 'dashboard_quality_recovery',
      }),
      text: async () => '',
    } as Response)

    const result = await regenerateProductionJob('prod-old', 'dashboard_quality_recovery')

    expect(result.job_id).toBe('prod-new')
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/jobs/prod-old/regenerate'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ reason: 'dashboard_quality_recovery', run_in_background: true }),
      }),
    )
  })

  it('fetches public quality readiness for a content account', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        ready: true,
        blockers: [],
        voice_provider: {
          checked: true,
          public_voice_ready: true,
          selected_tts_backend: 'openai_tts',
          next_action: 'premium_voice_ready_generate_private_public_quality_short',
        },
        visual_backend: {
          checked: true,
          public_visual_ready: true,
          selected_video_backend: 'auto',
          effective_video_backend: 'wan',
          next_action: 'visual_backend_ready_generate_private_public_quality_short',
        },
        style_guide: {
          ready: true,
          status: 'obsidian_channel_style',
          source: 'F:/obsydian/atnos/Atnos/Shorts/acct-1/style_guide.md',
          used_for_account: 'acct-1',
          next_action: null,
        },
        next_actions: [],
      }),
      text: async () => '',
    } as Response)

    const result = await fetchProductionPublicQualityStatus('acct-1')

    expect(result.ready).toBe(true)
    expect(result.visual_backend.effective_video_backend).toBe('wan')
    expect(result.style_guide.ready).toBe(true)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/public-quality/status?account_id=acct-1'),
      expect.any(Object),
    )
  })

  it('fetches quality review scheduler status', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        enabled: true,
        require_public_quality: true,
        interval_minutes: 15,
        max_jobs_per_tick: 5,
        pending_count: 2,
      }),
      text: async () => '',
    } as Response)

    const result = await fetchProductionQualityReviewStatus()

    expect(result.pending_count).toBe(2)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/quality-review/status'),
      expect.objectContaining({ headers: expect.any(Object) }),
    )
  })

  it('runs quality review once with a limit', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'completed',
        checked_count: 1,
        blocked_count: 1,
        passed_count: 0,
        jobs: [
          {
            status: 'blocked',
            job_id: 'prod-blur',
            blockers: ['semantic_visual_thumbnail_flag_generic_blur'],
          },
        ],
      }),
      text: async () => '',
    } as Response)

    const result = await runProductionQualityReviewOnce({ limit: 5 })

    expect(result.jobs[0].blockers).toContain('semantic_visual_thumbnail_flag_generic_blur')
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/quality-review/run-once'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ limit: 5 }),
      }),
    )
  })

  it('fetches quality remediation scheduler status', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        enabled: true,
        run_jobs: false,
        interval_minutes: 20,
        max_jobs_per_tick: 3,
        candidate_count: 4,
      }),
      text: async () => '',
    } as Response)

    const result = await fetchProductionQualityRemediationStatus()

    expect(result.candidate_count).toBe(4)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/quality-remediation/status'),
      expect.objectContaining({ headers: expect.any(Object) }),
    )
  })

  it('runs quality remediation once with a limit', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'completed',
        planned_count: 1,
        started_count: 1,
        results: [{ status: 'queued', job_id: 'prod-fixed' }],
      }),
      text: async () => '',
    } as Response)

    const result = await runProductionQualityRemediationOnce({ limit: 3 })

    expect(result.started_count).toBe(1)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/quality-remediation/run-once'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ limit: 3 }),
      }),
    )
  })

  it('fetches operating standards for a project', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'ready',
        project_id: 'yt-finanse-pl',
        project_type: 'shorts_channel',
        generated_at: '2026-06-08T10:00:00+00:00',
        source: {
          path: 'F:/AtnosSystem/AppData/Roaming/Atnos/data/learning/side_research/report.md',
          exists: true,
          last_modified: '2026-06-08T09:00:00+00:00',
        },
        summary: 'Research report is available and mapped into operational quality standards.',
        sections: [
          {
            key: 'shorts_quality',
            title: 'Shorts quality system',
            items: [
              {
                key: 'private_qa_before_public',
                label: 'Private QA before public',
                purpose: 'Public upload stays blocked until QA passes.',
                status: 'active',
                gate: 'publication',
                required: true,
              },
            ],
          },
        ],
        forbidden: ['No public autopost without QA evidence.'],
        metrics: {
          required_count: 1,
          blocked_until_configured_count: 0,
        },
        next_actions: ['Use these standards as pre-render and pre-publication gates.'],
      }),
      text: async () => '',
    } as Response)

    const result = await fetchProductionOperatingStandards('yt-finanse-pl')

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/operating-standards?project_id=yt-finanse-pl'),
      expect.objectContaining({ headers: expect.any(Object) }),
    )
    expect(result.sections[0].items[0].key).toBe('private_qa_before_public')
  })

  it('executes the next approved private money-machine action', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'completed',
        execute: true,
        approved_private_upload: true,
        executed_count: 1,
        blocked_count: 0,
        results: [{ job_id: 'prod-ready', status: 'uploaded_private' }],
      }),
      text: async () => '',
    } as Response)

    const result = await runMoneyMachineNext({
      accountId: 'acct-1',
      execute: true,
      approvedPrivateUpload: true,
      maxUploads: 1,
      perAccountLimit: 1,
    })

    expect(result.executed_count).toBe(1)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/money-machine/run-next'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          account_id: 'acct-1',
          max_uploads: 1,
          per_account_limit: 1,
          execute: true,
          approved_private_upload: true,
          execute_recovery: false,
        }),
      }),
    )
  })

  it('schedules public promotion only through the explicit publication endpoint', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'completed',
        execute: true,
        approved_public_promotion: true,
        publish_at: '2026-06-08T14:00:00Z',
        executed_count: 1,
        blocked_count: 0,
        results: [{ job_id: 'prod-public', result: { status: 'scheduled_public' } }],
      }),
      text: async () => '',
    } as Response)

    const result = await runMoneyMachinePublication({
      accountId: 'acct-1',
      execute: true,
      approvedPublicPromotion: true,
      publishAt: '2026-06-08T14:00:00Z',
      maxPublications: 1,
      perAccountLimit: 1,
    })

    expect(result.executed_count).toBe(1)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/money-machine/run-publication'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          account_id: 'acct-1',
          max_publications: 1,
          per_account_limit: 1,
          execute: true,
          approved_public_promotion: true,
          publish_at: '2026-06-08T14:00:00Z',
        }),
      }),
    )
  })

  it('saves the runtime public upload switch through the settings endpoint', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        profile: {},
        style: {},
        policy: {},
        runtime: {
          browser_control_enabled: true,
          stealth_headless_enabled: false,
          browser_allow_any_url: true,
          external_actions_enabled: true,
          public_upload_enabled: true,
          scheduler_enabled: true,
          oauth_enabled: true,
          autonomy_mode: 'trusted_auto',
          emergency_stop_enabled: false,
          pause_all_jobs: false,
          login_automation_enabled: true,
          two_factor_automation_enabled: true,
          publish_automation_enabled: true,
          default_retry_budget: 3,
          default_vram_floor_gb: 6,
          voice_stt_engine: 'browser-speech',
          voice_stt_model_path: null,
          voice_stt_device: 'cpu',
          ambient_listening_enabled: false,
          ambient_wake_word: 'Atnos',
          ambient_mic_device: null,
          ambient_muted: false,
          ambient_context_enabled: false,
          trend_watch_enabled: false,
          trend_watch_query: 'short video hooks',
          trend_watch_region: 'PL',
          trend_watch_interval_minutes: 180,
          trend_watch_last_run_at: null,
          voice_replies_enabled: false,
          voice_reply_volume: 0.8,
          voice_reply_voice_id: null,
        },
        runtime_summary: {},
        provider_cards: [],
        oauth_connectors: [],
        capabilities: {},
        projects: [],
        obsidian: {},
        trusted_auto: null,
        access_reality: null,
        remote_workers: [],
      }),
      text: async () => '',
    } as Response)

    const result = await updateSettingsRuntime({
      browserControlEnabled: true,
      externalActionsEnabled: true,
      publicUploadEnabled: true,
      schedulerEnabled: true,
      oauthEnabled: true,
    })

    expect(result.runtime.publicUploadEnabled).toBe(true)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/settings/control-center/runtime'),
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          browser_control_enabled: true,
          browser_allow_any_url: undefined,
          external_actions_enabled: true,
          scheduler_enabled: true,
          oauth_enabled: true,
          public_upload_enabled: true,
          ambient_listening_enabled: undefined,
          ambient_mic_device: undefined,
          ambient_muted: undefined,
          ambient_context_enabled: undefined,
          trend_watch_enabled: undefined,
        }),
      }),
    )
  })

  it('runs the autonomous content loop once without public publication', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        status: 'completed',
        generated_count: 4,
        approved_count: 1,
        rejected_count: 2,
        started_count: 1,
        jobs: [{ job_id: 'prod-1' }],
        errors: [],
        accounts: [],
      }),
      text: async () => '',
    } as Response)

    const result = await runProductionContentLoopOnce({ accountId: 'acct-1', maxJobs: 1 })

    expect(result.started_count).toBe(1)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/production/content-loop/run-once'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ account_id: 'acct-1', max_jobs: 1 }),
      }),
    )
  })
})

describe('api.fetchBootstrapShell', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('maps the lightweight chat shell payload', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        ui_version: 'shell-v1',
        generated_at: '2026-05-31T20:00:00Z',
        projects: [
          {
            id: 7,
            name: 'YT Finanse Short',
            slug: 'yt-finanse-short',
            description: null,
            summary: 'Shorts finance workspace',
            status: 'active',
            is_pinned: true,
            provider_posture: 'prefer-local',
            persona_profile: 'strategist',
          },
        ],
        recent_conversations: [
          {
            id: 11,
            title: 'Plan shorta',
            project_id: 7,
            summary: null,
            last_message_at: null,
            updated_at: '2026-05-31T20:00:01Z',
          },
        ],
      }),
      text: async () => '',
    } as Response)

    const result = await fetchBootstrapShell()

    expect(result.uiVersion).toBe('shell-v1')
    expect(result.projects).toHaveLength(1)
    expect(result.projects[0].name).toBe('YT Finanse Short')
    expect(result.recentConversations).toHaveLength(1)
    expect(result.recentConversations[0].id).toBe(11)
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/ui/shell'),
      expect.any(Object),
    )
  })
})

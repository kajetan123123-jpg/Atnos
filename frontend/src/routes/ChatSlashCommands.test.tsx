import { act, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import { useChatStore } from '../stores/chatStore'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import { buildBootstrapPayload, buildConversationDetailPayload } from '../test/factories'
import { resetAllStores } from '../test/resetStores'
import ChatRoute from './Chat'

const { mockNavigate } = vi.hoisted(() => ({ mockNavigate: vi.fn() }))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('../hooks/useMediaQuery', () => ({
  useMediaQuery: () => true,
}))

vi.mock('../components/ContentPipeline', () => ({
  ContentPipeline: () => <div data-testid="content-pipeline">pipeline</div>,
}))

vi.mock('../components/ProjectCommandPanel', () => ({
  ProjectCommandPanel: () => <div data-testid="project-command-panel">project-command</div>,
}))

vi.mock('../components/ApprovalGate', () => ({
  ApprovalGate: () => <div data-testid="approval-gate">approval-gate</div>,
}))

vi.mock('../components/ChatStream', () => ({
  ChatStream: ({
    messages,
    onSubmit,
  }: {
    messages: Array<{ id: number | string; content: string }>
    inputHeader?: ReactNode
    onSubmit: (payload: { text: string; attachments: Array<unknown> }) => boolean | Promise<boolean>
  }) => (
    <div>
      <button type="button" onClick={() => void onSubmit({ text: '/decisions', attachments: [] })}>
        Decisions test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: '/help', attachments: [] })}>
        Help test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: '/clear', attachments: [] })}>
        Clear test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: '/model qwen2.5-coder', attachments: [] })}>
        Model test
      </button>
      <button
        type="button"
        onClick={() =>
          void onSubmit({
            text: '/atnos popraw runtime i sprawdz realne statusy',
            attachments: [],
          })
        }
      >
        Atnos delegation test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: '/ai-tasks', attachments: [] })}>
        AI tasks test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: '/run-task 43', attachments: [] })}>
        Run AI task test
      </button>
      <button
        type="button"
        onClick={() =>
          void onSubmit({
            text: '/rolka First ten thousand | direct practical savings plan | no publication',
            attachments: [],
          })
        }
      >
        Short order test
      </button>
      <button
        type="button"
        onClick={() =>
          void onSubmit({
            text: 'zrób rolkę o odkładaniu pierwszych pieniędzy bez obiecywania zysków',
            attachments: [],
          })
        }
      >
        Natural short order test
      </button>
      <div data-testid="message-list">
        {messages.map((message) => (
          <div key={message.id}>{message.content}</div>
        ))}
      </div>
    </div>
  ),
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    delegateAiWorkerPlan: vi.fn(),
    fetchAiWorkersStatus: vi.fn(),
    fetchAiWorkerTasks: vi.fn(),
    fetchBootstrap: vi.fn(),
    fetchChatDecisions: vi.fn(),
    fetchConversationDetail: vi.fn(),
    fetchScheduledTasks: vi.fn(),
    createProductionOrder: vi.fn(),
    executeProjectCommand: vi.fn(),
    runAiWorkerSafePlan: vi.fn(),
    runAiWorkerTask: vi.fn(),
    sendChatTurn: vi.fn(),
    switchLocalChatModel: vi.fn(),
    updateConversationSettings: vi.fn(),
  }
})

function primeChatStores() {
  const bootstrap = buildBootstrapPayload()

  act(() => {
    useChatStore.getState().setBootstrap({
      uiVersion: bootstrap.uiVersion,
      projects: bootstrap.projects,
      conversations: bootstrap.recentConversations,
    })
    usePipelineStore.getState().setSnapshot(bootstrap.pipeline)
    useSystemStore.getState().setRuntimeSummary(bootstrap.runtimeSummary)
    useSystemStore.getState().setCapabilities(bootstrap.capabilities)
    useSystemStore.getState().setStage('idle')
  })
}

describe('ChatRoute slash commands', () => {
  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
    vi.mocked(api.fetchConversationDetail).mockResolvedValue(buildConversationDetailPayload())
    vi.mocked(api.fetchScheduledTasks).mockResolvedValue([])
    vi.mocked(api.fetchAiWorkersStatus).mockResolvedValue({
      mode: 'supervised_high_trust',
      workers: [
        {
          workerKey: 'ollama',
          label: 'Ollama',
          status: 'ready',
          ready: true,
          reason: 'mocked Ollama ready',
          capabilities: ['text'],
          allowedTaskTypes: ['review'],
          requiresBrowser: false,
          safetyMode: 'supervised_high_trust',
          metadata: {},
        },
        {
          workerKey: 'browser_claude',
          label: 'Claude Browser',
          status: 'blocked_by_policy',
          ready: false,
          reason: 'Browser control is disabled in runtime settings.',
          capabilities: ['browser_ai'],
          allowedTaskTypes: ['review'],
          requiresBrowser: true,
          safetyMode: 'supervised_high_trust',
          metadata: { browser_ready: false },
        },
      ],
      guardrails: {
        high_risk_actions_blocked: true,
        browser_ai_submit_policy: 'supervised_handoff_only',
      },
    })
    vi.mocked(api.fetchChatDecisions).mockResolvedValue({
      entries: [],
      overdueCount: 0,
    })
    vi.mocked(api.switchLocalChatModel).mockResolvedValue({
      modelId: 1,
      modelKey: 'ollama-local/chat-default',
      runtimeModelName: 'qwen2.5-coder',
    })
    vi.mocked(api.updateConversationSettings).mockResolvedValue(
      buildConversationDetailPayload().settings,
    )
    vi.mocked(api.createProductionOrder).mockResolvedValue({
      id: 'prod-one-command',
      account_id: 'yt-wealth-blueprint',
      topic_id: 'topic-one-command',
      topic_title: 'First ten thousand',
      topic_description: 'direct practical savings plan',
      status: 'queued',
      current_step: 'queued',
      ready_for_review: false,
      ready_for_private_upload: false,
      steps: [],
      worker_live: true,
      can_resume: false,
      profile_hash: 'hash',
      reserved_cost_units: 0,
      provider_calls: 0,
      request: { project: 'wealth_blueprint' },
    } as unknown as api.ProductionJobPayload)
    vi.mocked(api.delegateAiWorkerPlan).mockResolvedValue({
      id: 42,
      taskType: 'engineering_runtime_delegation',
      status: 'planned',
      priority: 'normal',
      requestedBy: 'atnos',
      workerKey: 'planner',
      privacyMode: 'strict_local',
      parentTaskId: null,
      taskGroup: 'aiw-test',
      taskRole: 'planner',
      sequenceIndex: 0,
      requiresReview: false,
      verificationStatus: 'planned',
      inputSummary: 'popraw runtime',
      redactedPrompt: 'popraw runtime i sprawdz realne statusy',
      payloadJson: {},
      resultSummary: 'Plan dla diagnozy, implementacji, testow i review technicznego.',
      resultJson: null,
      errorMessage: null,
      blockedReason: null,
      approvalRequired: true,
      traceId: null,
      createdAt: '2026-07-09T20:00:00Z',
      updatedAt: '2026-07-09T20:00:00Z',
      startedAt: null,
      completedAt: null,
      runs: [],
      children: [
        {
          id: 43,
          taskType: 'engineering_diagnosis',
          status: 'queued',
          workerKey: 'ollama',
          taskRole: 'diagnosis',
          sequenceIndex: 1,
          requiresReview: false,
          verificationStatus: 'pending',
          inputSummary: 'Identify impacted files',
          blockedReason: null,
          resultSummary: null,
        },
      ],
    })
    vi.mocked(api.runAiWorkerSafePlan).mockResolvedValue({
      id: 42,
      taskType: 'engineering_runtime_delegation',
      status: 'awaiting_review',
      priority: 'normal',
      requestedBy: 'atnos',
      workerKey: 'planner',
      privacyMode: 'strict_local',
      parentTaskId: null,
      taskGroup: 'aiw-test',
      taskRole: 'planner',
      sequenceIndex: 0,
      requiresReview: false,
      verificationStatus: 'awaiting_human_review',
      inputSummary: 'popraw runtime',
      redactedPrompt: 'popraw runtime i sprawdz realne statusy',
      payloadJson: {},
      resultSummary: 'Wykonano bezpieczne kroki lokalne. Dalszy review wymaga decyzji człowieka.',
      resultJson: {
        execution_policy: 'safe_local_only',
        executed_child_ids: [43],
        stopped_before_child_id: 44,
      },
      errorMessage: null,
      blockedReason: 'Krok review wymaga decyzji człowieka.',
      approvalRequired: true,
      traceId: null,
      createdAt: '2026-07-09T20:00:00Z',
      updatedAt: '2026-07-09T20:01:10Z',
      startedAt: '2026-07-09T20:01:00Z',
      completedAt: '2026-07-09T20:01:10Z',
      runs: [],
      children: [
        {
          id: 43,
          taskType: 'engineering_diagnosis',
          status: 'completed',
          workerKey: 'ollama',
          taskRole: 'diagnosis',
          sequenceIndex: 1,
          requiresReview: false,
          verificationStatus: 'completed',
          inputSummary: 'Identify impacted files',
          blockedReason: null,
          resultSummary: 'Diagnoza runtime wykonana.',
        },
        {
          id: 44,
          taskType: 'technical_review',
          status: 'awaiting_review',
          workerKey: 'ollama',
          taskRole: 'review',
          sequenceIndex: 2,
          requiresReview: true,
          verificationStatus: 'awaiting_human_review',
          inputSummary: 'Review wynikow diagnozy',
          blockedReason: 'Krok review wymaga decyzji człowieka.',
          resultSummary: null,
        },
      ],
    })
    vi.mocked(api.fetchAiWorkerTasks).mockResolvedValue([
      {
        id: 42,
        taskType: 'engineering_runtime_delegation',
        status: 'planned',
        priority: 'normal',
        requestedBy: 'atnos',
        workerKey: 'planner',
        privacyMode: 'strict_local',
        parentTaskId: null,
        taskGroup: 'aiw-test',
        taskRole: 'planner',
        sequenceIndex: 0,
        requiresReview: false,
        verificationStatus: 'planned',
        inputSummary: 'popraw runtime',
        redactedPrompt: 'popraw runtime',
        payloadJson: {},
        resultSummary: 'Plan techniczny gotowy.',
        resultJson: null,
        errorMessage: null,
        blockedReason: null,
        approvalRequired: true,
        traceId: null,
        createdAt: '2026-07-09T20:00:00Z',
        updatedAt: '2026-07-09T20:00:00Z',
        startedAt: null,
        completedAt: null,
        runs: [],
        children: [
          {
            id: 43,
            taskType: 'engineering_diagnosis',
            status: 'queued',
            workerKey: 'ollama',
            taskRole: 'diagnosis',
            sequenceIndex: 1,
            requiresReview: false,
            verificationStatus: 'pending',
            inputSummary: 'diagnoza',
            blockedReason: null,
            resultSummary: null,
          },
        ],
      },
    ])
    vi.mocked(api.runAiWorkerTask).mockResolvedValue({
      id: 43,
      taskType: 'engineering_diagnosis',
      status: 'completed',
      priority: 'normal',
      requestedBy: 'atnos',
      workerKey: 'ollama',
      privacyMode: 'strict_local',
      parentTaskId: 42,
      taskGroup: 'aiw-test',
      taskRole: 'diagnosis',
      sequenceIndex: 1,
      requiresReview: false,
      verificationStatus: 'completed',
      inputSummary: 'diagnoza',
      redactedPrompt: 'Identify impacted files',
      payloadJson: {},
      resultSummary: 'Diagnoza runtime wykonana.',
      resultJson: { content: 'Diagnoza runtime wykonana.' },
      errorMessage: null,
      blockedReason: null,
      approvalRequired: false,
      traceId: null,
      createdAt: '2026-07-09T20:00:00Z',
      updatedAt: '2026-07-09T20:00:00Z',
      startedAt: '2026-07-09T20:01:00Z',
      completedAt: '2026-07-09T20:01:10Z',
      runs: [],
      children: [],
    })
  })

  it('renders /decisions result inline without sending a chat turn', async () => {
    const user = userEvent.setup()
    primeChatStores()
    vi.mocked(api.fetchChatDecisions).mockResolvedValue({
      entries: [
        {
          decisionId: 7,
          conversationId: 101,
          projectId: 1,
          kind: 'commitment',
          summary: 'Dowiesc review PR do piatku',
          madeAt: '2026-05-17T08:00:00Z',
          status: 'active',
          dueAt: '2026-05-17T12:00:00Z',
          resolvedAt: null,
          resolutionSummary: null,
          evidenceMessageIds: [501],
        },
      ],
      overdueCount: 1,
    })

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Decisions test' }))

    await waitFor(() => {
      expect(api.fetchChatDecisions).toHaveBeenCalledWith({ projectId: 1 })
    })
    expect(screen.getByText(/AKTYWNE DECYZJE/)).toBeInTheDocument()
    expect(screen.getByText(/Dowiesc review PR do piatku/)).toBeInTheDocument()
  })

  it('renders /help and /model results inline', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Help test' }))
    expect(screen.getByText(/DOSTEPNE KOMENDY/)).toBeInTheDocument()
    expect(screen.getByText(/\/model <name>/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Model test' }))
    await waitFor(() => {
      expect(api.switchLocalChatModel).toHaveBeenCalledWith('qwen2.5-coder')
    })
    expect(screen.getByText(/MODEL PRZELACZONY/)).toBeInTheDocument()
  })

  it('clears the workspace after /clear confirmation', async () => {
    const user = userEvent.setup()
    primeChatStores()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Clear test' }))

    expect(confirmSpy).toHaveBeenCalled()
    expect(useChatStore.getState().selectedConversationId).toBeNull()
    expect(useChatStore.getState().selectedConversationTitle).toBe('Nowa rozmowa')
    expect(useToastStore.getState().toasts.at(-1)?.title).toBe('Rozmowa wyczyszczona')
  })

  it('delegates /atnos commands to AI Workers instead of normal chat', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Atnos delegation test' }))

    await waitFor(() => {
      expect(api.delegateAiWorkerPlan).toHaveBeenCalledWith(
        expect.objectContaining({
          taskType: 'engineering_runtime_delegation',
          prompt: 'popraw runtime i sprawdz realne statusy',
          projectId: 1,
          preferredWorker: 'ollama',
          allowedWorkers: ['ollama'],
          privacyMode: 'strict_local',
          requiresApprovalForResult: true,
        }),
      )
    })
    expect(api.sendChatTurn).not.toHaveBeenCalled()
    expect(api.runAiWorkerSafePlan).toHaveBeenCalledWith(42)
    expect(screen.getByText(/PLAN DELEGACJI #42/)).toBeInTheDocument()
    expect(screen.getByText(/Podzadania/)).toBeInTheDocument()
  })

  it('lists and runs AI worker tasks from chat commands', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'AI tasks test' }))
    await waitFor(() => {
      expect(api.fetchAiWorkerTasks).toHaveBeenCalledWith({ rootsOnly: true, limit: 8 })
    })
    expect(screen.getByText(/AI WORKERS - OSTATNIE ZADANIA/)).toBeInTheDocument()
    expect(screen.getByText(/#42/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Run AI task test' }))
    await waitFor(() => {
      expect(api.runAiWorkerTask).toHaveBeenCalledWith(43)
    })
    expect(screen.getByText(/AI WORKER TASK #43/)).toBeInTheDocument()
    expect(screen.getByText(/Diagnoza runtime wykonana/)).toBeInTheDocument()
  })

  it('hands /rolka off to the production form with its fields preserved', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Short order test' }))

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/create', {
      state: { productionPrefill: {
        project: 'wealth_blueprint', topic: 'First ten thousand',
        direction: 'direct practical savings plan', optionalNotes: 'no publication',
      } },
    }))
    expect(api.sendChatTurn).not.toHaveBeenCalled()
    expect(api.createProductionOrder).not.toHaveBeenCalled()
    expect(screen.getByText(/Wybierz lokalną grafikę i jej styl/)).toBeInTheDocument()
    expect(screen.getByText(/sposób realizacji lektora zależy od konfiguracji/)).toBeInTheDocument()
  })

  it('hands a natural create-short command to the same configured production form', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Natural short order test' }))

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/create', {
      state: { productionPrefill: {
        project: 'wealth_blueprint',
        topic: 'odkładaniu pierwszych pieniędzy bez obiecywania zysków',
        direction: 'zrób rolkę o odkładaniu pierwszych pieniędzy bez obiecywania zysków',
        optionalNotes: 'Utworzone z naturalnej komendy w czacie.',
      } },
    }))
    expect(api.executeProjectCommand).not.toHaveBeenCalled()
    expect(api.sendChatTurn).not.toHaveBeenCalled()
    expect(api.createProductionOrder).not.toHaveBeenCalled()
    expect(screen.getByText(/Wybierz lokalną grafikę i jej styl/)).toBeInTheDocument()
  })
})

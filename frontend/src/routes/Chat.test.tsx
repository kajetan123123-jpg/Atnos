import { act, fireEvent, render as renderInTest, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import { useChatStore } from '../stores/chatStore'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import {
  buildBootstrapPayload,
  buildChatTurnResponse,
  buildConversationDetailPayload,
} from '../test/factories'
import { resetAllStores } from '../test/resetStores'
import ChatRoute from './Chat'

// Match the production router context used by ChatRoute's useNavigate hook.
function render(ui: ReactNode) {
  return renderInTest(<MemoryRouter>{ui}</MemoryRouter>)
}

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
    inputHeader,
    inputValue,
    activeWorkStatus,
    queuedMessages,
    onInputValueChange,
    onSubmit,
    onImprovePrompt,
  }: {
    messages: Array<{ id: number | string; content: string }>
    inputHeader?: ReactNode
    inputValue?: string
    activeWorkStatus?: { title: string; detail: string; statusLabel?: string; stepLabel?: string | null } | null
    queuedMessages?: Array<{ id: string; text: string; targetLabel: string }>
    onInputValueChange?: (value: string) => void
    onSubmit: (payload: {
      text: string
      attachments: Array<unknown>
      promptImproved?: boolean
    }) => boolean | Promise<boolean>
    onImprovePrompt?: (draft: string) => Promise<{ text: string; changed?: boolean } | null>
  }) => (
    <div data-testid="mock-chat-stream">
      {inputHeader}
      {activeWorkStatus ? (
        <div data-testid="mock-active-work-status">
          <span>{activeWorkStatus.title}</span>
          <span>{activeWorkStatus.detail}</span>
          {activeWorkStatus.statusLabel ? <span>{activeWorkStatus.statusLabel}</span> : null}
          {activeWorkStatus.stepLabel ? <span>{activeWorkStatus.stepLabel}</span> : null}
        </div>
      ) : null}
      <div data-testid="mock-queue-count">{queuedMessages?.length ?? 0}</div>
      <input
        aria-label="Mock chat draft"
        value={inputValue ?? ''}
        onChange={(event) => onInputValueChange?.(event.target.value)}
      />
      <button
        type="button"
        onClick={() => void onImprovePrompt?.('powiedz jakie karty sa otwarte w chrome?')}
      >
        popraw-prompt-test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: '/ask Hej Atnos', attachments: [] })}>
        Wyslij test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: 'Następne polecenie z kolejki', attachments: [] })}>
        Kolejkuj test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: inputValue ?? '', attachments: [] })}>
        Project command test
      </button>
      <button type="button" onClick={() => void onSubmit({ text: '/briefing', attachments: [] })}>
        Briefing test
      </button>
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
  const sendChatTurn = vi.fn()
  const sendChatTurnStream = vi.fn(
    async (
      payload: Parameters<typeof actual.sendChatTurn>[0],
      options?: {
        signal?: AbortSignal
        onEvent?: (event: { type: string; stage?: string; title?: string; detail?: string }) => void
      },
    ) => {
      options?.onEvent?.({
        type: 'status',
        stage: 'thinking',
        title: 'Atnos odpowiada…',
        detail: 'Generuję odpowiedź.',
      })
      return sendChatTurn(payload, { signal: options?.signal })
    },
  )
  return {
    ...actual,
    archiveConversation: vi.fn(),
    createChatBriefing: vi.fn(),
    createProject: vi.fn(),
    deleteScheduledTask: vi.fn(),
    deleteProject: vi.fn(),
    fetchChatDecisions: vi.fn(),
    fetchAiWorkersStatus: vi.fn(),
    fetchBootstrap: vi.fn(),
    fetchConversationDetail: vi.fn(),
    fetchScheduledTasks: vi.fn(),
    improveChatPrompt: vi.fn(),
    executeProjectCommand: vi.fn(),
    resolveDecisionLogEntry: vi.fn(),
    runTrustedAutoJob: vi.fn(),
    sendChatTurn,
    sendChatTurnStream,
    switchLocalChatModel: vi.fn(),
    toggleScheduledTask: vi.fn(),
    updateConversationSettings: vi.fn(),
    updatePipelineApproval: vi.fn(),
    updateProject: vi.fn(),
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
    useSystemStore.getState().setNetworkSignals({ backendReachable: true, browserOnline: true })
    useSystemStore.getState().setWebSocketStatus({ status: 'connected' })
    useSystemStore.getState().setStage('idle')
  })

  return bootstrap
}

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value: width,
  })
  window.dispatchEvent(new Event('resize'))
}

describe('ChatRoute', () => {
  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
    window.localStorage.clear()
    setViewportWidth(1440)
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
    vi.mocked(api.executeProjectCommand).mockResolvedValue({
      status: 'completed',
      intent: 'project_status',
      title: 'Stan projektu',
      answer: 'Stan jest oparty na aktualnych dowodach.',
      data: {},
      evidence: [],
      requires_confirmation: false,
      verified: false,
      verification_status: 'insufficient_evidence',
      trace_id: 91,
      trace_status: 'completed',
      conversation_id: 101,
      conversation_title: 'Plan shortow AI',
      user_message_id: 501,
      assistant_message_id: 502,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('waits for the first health result before reporting API unavailable', async () => {
    primeChatStores()
    useSystemStore.setState({ backendCheckedAt: null, networkSignals: { browserOnline: true, backendReachable: false } })
    render(<ChatRoute />)
    const banner = screen.getByTestId('chat-connection-banner')
    expect(banner).not.toHaveTextContent('API niedostępne')
    expect(within(banner).queryByRole('button')).not.toBeInTheDocument()
    act(() => useSystemStore.getState().setNetworkSignals({ backendReachable: false }))
    expect(within(banner).getByRole('button')).toBeInTheDocument()
  })

  it('domyslnie maksymalizuje rozmowe i pozwala przywrocic panele', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    expect(screen.getByTestId('chat-route-shell')).toHaveAttribute('data-layout-mode', 'reading')
    expect(screen.getByTestId('chat-route-shell')).toHaveStyle({
      height: 'calc(100dvh - 56px)',
    })
    expect(screen.getByTestId('restore-chat-panels')).toHaveAccessibleName(
      'Przywróć panele rozmowy',
    )
    expect(screen.queryByTestId('conversation-sidebar')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chat-pipeline-rail')).not.toBeInTheDocument()
    expect(screen.queryByText('USTAWIENIA ROZMOWY')).not.toBeInTheDocument()
    expect(screen.queryByText('TRYB ATNOSA')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Mock chat draft')).toBeInTheDocument()

    await user.click(screen.getByTestId('restore-chat-panels'))

    expect(screen.getByTestId('chat-route-shell')).toHaveAttribute('data-layout-mode', 'workspace')
    expect(screen.getByTestId('conversation-sidebar')).toBeInTheDocument()
    expect(screen.getByTestId('chat-pipeline-rail')).toBeInTheDocument()
    expect(screen.getByLabelText('Mock chat draft')).toBeInTheDocument()
  })

  it('uzywa mobilnego offsetu 100dvh i zachowuje composer w trybie czytania', async () => {
    setViewportWidth(390)
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    expect(screen.getByTestId('chat-route-shell')).toHaveStyle({
      height: 'calc(100dvh - 131px)',
    })
    expect(screen.queryByTestId('conversation-sidebar')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chat-pipeline-rail')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Mock chat draft')).toBeVisible()
  })

  it('udostepnia projekty, wyszukiwanie i historie rozmow na mobile', async () => {
    const user = userEvent.setup()
    setViewportWidth(390)
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })
    await user.click(screen.getByRole('button', { name: 'Otworz projekty i rozmowy' }))

    expect(screen.getByTestId('mobile-conversation-panel')).toBeInTheDocument()
    expect(screen.getByLabelText('Szukaj rozmów mobilnie')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'PROJEKT' })).toBeInTheDocument()
    expect(screen.getAllByText('Plan shortów AI').length).toBeGreaterThan(0)

    await user.type(screen.getByLabelText('Szukaj rozmów mobilnie'), 'brak-wyniku')
    expect(screen.getByText('Brak rozmow dla wybranego filtra.')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Zamknij panel rozmów' })[1])
    expect(screen.queryByTestId('mobile-conversation-panel')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Mock chat draft')).toBeVisible()
  })

  it.each([
    ['desktop', 1440],
    ['tablet', 900],
    ['mobile', 390],
  ])(
    'utrzymuje rozmowe przed zwijanymi szczegolami projektu na widoku %s',
    async (_label, width) => {
      const user = userEvent.setup()
      setViewportWidth(width)
      primeChatStores()

      render(<ChatRoute />)

      await waitFor(() => {
        expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
      })

      await user.click(screen.getByTestId('restore-chat-panels'))

      const conversation = screen.getByTestId('chat-conversation-surface')
      const details = screen.getByTestId('chat-workspace-details')
      const toggle = screen.getByRole('button', {
        name: 'Szczegóły projektu i Project Command',
      })

      expect(toggle).toHaveAttribute('aria-expanded', 'false')
      expect(screen.queryByTestId('chat-workspace-details-content')).not.toBeInTheDocument()
      expect(
        conversation.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy()
      expect(screen.getByLabelText('Mock chat draft')).toBeVisible()

      await user.click(toggle)

      expect(toggle).toHaveAttribute('aria-expanded', 'true')
      expect(screen.getByTestId('chat-workspace-details-content')).toHaveClass(
        'overflow-y-auto',
        'max-h-[30dvh]',
      )
      expect(screen.getByTestId('project-command-panel')).toBeInTheDocument()
      expect(screen.getByLabelText('Mock chat draft')).toBeVisible()
    },
  )

  it('pokazuje jawny przycisk nowej rozmowy i resetuje workspace', async () => {
    const user = userEvent.setup()
    primeChatStores()

    const { container } = render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    expect(container.querySelector('.shell-route')).toHaveStyle({
      height: 'calc(100dvh - 56px)',
    })
    expect(screen.getAllByRole('button', { name: /Nowa rozmowa/i }).length).toBeGreaterThan(0)
    await user.type(screen.getByLabelText('Mock chat draft'), 'roboczy draft')
    expect(useChatStore.getState().selectedConversationId).toBe(101)

    await user.click(screen.getByTestId('new-conversation-primary-button'))

    expect(useChatStore.getState().selectedConversationId).toBeNull()
    expect(useChatStore.getState().selectedConversationTitle).toBe('Nowa rozmowa')
    expect(screen.getByLabelText('Mock chat draft')).toHaveValue('')
  })

  it('pozwala utworzyc nowy chat bez podpinania do projektu', async () => {
    const user = userEvent.setup()
    primeChatStores()
    const projectlessTurn = {
      ...buildChatTurnResponse(),
      conversationId: 202,
      conversationTitle: 'Luźna rozmowa',
      conversationProjectId: null,
    }
    vi.mocked(api.sendChatTurn).mockResolvedValue(projectlessTurn)

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })
    expect(useChatStore.getState().selectedProjectId).toBe(1)

    await user.click(screen.getByTestId('new-projectless-conversation-primary-button'))

    expect(useChatStore.getState().selectedProjectId).toBeNull()
    expect(useChatStore.getState().selectedConversationId).toBeNull()
    expect(screen.getByText('Projekt: Bez projektu')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))

    await waitFor(() => {
      expect(api.sendChatTurn).toHaveBeenCalledWith(
        expect.not.objectContaining({
          project_id: expect.any(Number),
        }),
        expect.objectContaining({
          signal: expect.any(AbortSignal),
        }),
      )
      expect(useChatStore.getState().selectedConversationId).toBe(202)
      expect(useChatStore.getState().conversations[0]?.projectId).toBeNull()
    })
  })

  it('wysyła wiadomości z kolejki sekwencyjnie do przypisanej rozmowy', async () => {
    const user = userEvent.setup()
    const bootstrap = primeChatStores()
    vi.mocked(api.fetchBootstrap).mockResolvedValue(bootstrap)
    let finishFirstTurn!: (result: ReturnType<typeof buildChatTurnResponse>) => void
    const firstTurn = new Promise<ReturnType<typeof buildChatTurnResponse>>((resolve) => {
      finishFirstTurn = resolve
    })
    vi.mocked(api.sendChatTurn)
      .mockReturnValueOnce(firstTurn)
      .mockResolvedValueOnce(buildChatTurnResponse())

    render(<ChatRoute />)
    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))
    await waitFor(() => expect(api.sendChatTurn).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole('button', { name: 'Kolejkuj test' }))
    expect(useChatStore.getState().queuedTurns).toHaveLength(1)
    expect(screen.getByTestId('mock-queue-count')).toHaveTextContent('1')

    await act(async () => finishFirstTurn(buildChatTurnResponse()))
    await waitFor(() => expect(api.sendChatTurn).toHaveBeenCalledTimes(2))
    expect(api.sendChatTurn).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ content: 'Następne polecenie z kolejki', conversation_id: 101 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    await waitFor(() => expect(useChatStore.getState().queuedTurns).toHaveLength(0))
  })

  it('tworzy projekt z opisem, persona i postawa providera oraz aktywuje go w workspace', async () => {
    const user = userEvent.setup()
    const bootstrap = primeChatStores()
    const createdProject = {
      projectId: 2,
      name: 'Shorty AI Polska',
      slug: 'shorty-ai-polska',
      description: 'Kanal shortow AI na rynek PL',
      summary: null,
      status: 'active',
      isPinned: false,
      providerPosture: 'strict-local' as const,
      personaProfile: 'mentor' as const,
    }

    vi.mocked(api.createProject).mockResolvedValue(createdProject)
    vi.mocked(api.fetchBootstrap).mockResolvedValue({
      ...bootstrap,
      projects: [
        ...bootstrap.projects,
        {
          id: createdProject.projectId,
          name: createdProject.name,
          slug: createdProject.slug,
          description: createdProject.description,
          summary: createdProject.summary,
          status: createdProject.status,
          isPinned: createdProject.isPinned,
          providerPosture: createdProject.providerPosture,
          personaProfile: createdProject.personaProfile,
        },
      ],
      recentConversations: [],
    })

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByTestId('restore-chat-panels'))
    await user.click(screen.getAllByRole('button', { name: /Nowy/i })[0])
    await user.type(screen.getAllByPlaceholderText('Np. Shorts AI Polska')[0], 'Shorty AI Polska')
    await user.type(screen.getAllByPlaceholderText(/Kana/i)[0], 'Kanal shortow AI na rynek PL')
    await user.selectOptions(screen.getAllByRole('combobox', { name: /PERSONA/i })[0], 'mentor')
    await user.selectOptions(
      screen.getAllByRole('combobox', { name: /POSTAWA PROVIDERA/i })[0],
      'strict-local',
    )
    await user.click(screen.getAllByRole('button', { name: /Utw.*projekt/i })[0])

    await waitFor(() => {
      expect(api.createProject).toHaveBeenCalledWith({
        name: 'Shorty AI Polska',
        description: 'Kanal shortow AI na rynek PL',
        personaProfile: 'mentor',
        providerPosture: 'strict-local',
      })
    })

    await waitFor(() => {
      expect(useChatStore.getState().selectedProjectId).toBe(2)
      expect(useChatStore.getState().selectedConversationId).toBeNull()
      expect(useChatStore.getState().selectedConversationTitle).toBe('Nowa rozmowa')
    })

    expect(useToastStore.getState().toasts.at(-1)?.title).toBe('Projekt utworzony')
    expect(screen.getAllByText('Shorty AI Polska')[0]).toBeInTheDocument()
  })

  it('wysyla wiadomosc optymistycznie i zapisuje odpowiedz po sukcesie', async () => {
    const user = userEvent.setup()
    primeChatStores()
    const turnResult = buildChatTurnResponse()

    let resolveTurn: ((value: ReturnType<typeof buildChatTurnResponse>) => void) | null = null
    const turnPromise = new Promise<ReturnType<typeof buildChatTurnResponse>>((resolve) => {
      resolveTurn = resolve
    })

    vi.mocked(api.sendChatTurn).mockReturnValue(turnPromise)

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))

    expect(await screen.findByText('Hej Atnos')).toBeInTheDocument()
    expect(useSystemStore.getState().agentStage).toBe('thinking')
    expect(api.sendChatTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        content: 'Hej Atnos',
        conversation_id: 101,
        execution_preferences: expect.objectContaining({
          quality_profile: 'standard',
        }),
      }),
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    )

    await act(async () => {
      resolveTurn?.(turnResult)
      await turnPromise
    })

    await waitFor(() => {
      expect(
        within(screen.getByTestId('message-list')).getByText(turnResult.assistantMessage),
      ).toBeInTheDocument()
      expect(useSystemStore.getState().agentStage).toBe('idle')
      expect(useChatStore.getState().selectedConversationTitle).toBe(turnResult.conversationTitle)
    })
  })

  it('traktuje zwykla wiadomosc w aktywnym projekcie jako Project Command', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByTestId('restore-chat-panels'))
    await user.click(screen.getByRole('button', { name: 'Polecenia projektu' }))
    fireEvent.change(screen.getByLabelText('Mock chat draft'), {
      target: { value: 'Co obecnie blokuje projekt?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Project command test' }))

    await waitFor(() => {
      expect(api.executeProjectCommand).toHaveBeenCalledWith(
        expect.objectContaining({
          projectId: 1,
          content: 'Co obecnie blokuje projekt?',
        }),
      )
    })
    expect(api.sendChatTurn).not.toHaveBeenCalled()
    expect(await screen.findByText('Stan jest oparty na aktualnych dowodach.')).toBeInTheDocument()
  })

  it('przywraca draft per rozmowa po przełączaniu kontekstu', async () => {
    const user = userEvent.setup()
    const bootstrap = buildBootstrapPayload()
    bootstrap.recentConversations = [
      bootstrap.recentConversations[0],
      {
        id: 102,
        title: 'Notatki sprintu',
        projectId: 1,
        summary: 'Lista follow-upow po review',
        lastMessageAt: '2026-05-08T12:10:00Z',
        updatedAt: '2026-05-08T12:10:00Z',
      },
    ]

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

    vi.mocked(api.fetchConversationDetail)
      .mockResolvedValueOnce(buildConversationDetailPayload())
      .mockResolvedValueOnce({
        ...buildConversationDetailPayload(),
        conversation: {
          ...buildConversationDetailPayload().conversation,
          id: 102,
          title: 'Notatki sprintu',
          summary: 'Lista follow-upow po review',
        },
      })
      .mockResolvedValue(buildConversationDetailPayload())

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByTestId('restore-chat-panels'))
    await user.type(screen.getByLabelText('Mock chat draft'), 'roboczy plan dla rozmowy 101')
    await user.click(screen.getByRole('button', { name: /Notatki sprintu/i }))
    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(102)
    })
    expect(screen.getByLabelText('Mock chat draft')).toHaveValue('')

    await user.type(screen.getByLabelText('Mock chat draft'), 'drugi draft')
    await user.click(screen.getByRole('button', { name: /Plan short/i }))

    await waitFor(() => {
      expect(screen.getByLabelText('Mock chat draft')).toHaveValue('roboczy plan dla rozmowy 101')
    })
  })

  it('po 8 sekundach ostrzega, ale nie zabija jeszcze requestu', async () => {
    vi.useFakeTimers()
    primeChatStores()

    vi.mocked(api.sendChatTurn).mockReturnValue(new Promise(() => undefined))

    render(<ChatRoute />)

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Wyslij test' }))

    await act(async () => {
      vi.advanceTimersByTime(90000)
      await Promise.resolve()
      await Promise.resolve()
    })

    const pendingMessage = useChatStore
      .getState()
      .messages.find((message) => message.content === 'Hej Atnos')

    expect(useToastStore.getState().toasts.at(-1)?.title).toBe('ATNOS nadal pracuje')
    expect(pendingMessage?.status).not.toBe('failed')
    expect(pendingMessage?.metadataJson?.optimisticState).toBe('pending')
    expect(useSystemStore.getState().agentStage).toBe('executing')
    expect(useChatStore.getState().activeTurn?.summary).toBe('Hej Atnos')
  })

  it('pozwala przelaczyc profil jakosci rozmowy przed wyslaniem', async () => {
    const user = userEvent.setup()
    primeChatStores()
    vi.mocked(api.sendChatTurn).mockResolvedValue(buildChatTurnResponse())

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Przywróć panele rozmowy' }))
    await user.click(screen.getByRole('button', { name: 'Premium' }))
    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))

    await waitFor(() => {
      expect(api.sendChatTurn).toHaveBeenCalledWith(
        expect.objectContaining({
          execution_preferences: expect.objectContaining({
            quality_profile: 'premium',
          }),
        }),
        expect.objectContaining({
          signal: expect.any(AbortSignal),
        }),
      )
    })
  })

  it('stosuje szybkie toggles rozmowy przy wysylce', async () => {
    const user = userEvent.setup()
    primeChatStores()
    vi.mocked(api.sendChatTurn).mockResolvedValue(buildChatTurnResponse())

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Przywróć panele rozmowy' }))
    await user.click(screen.getByRole('button', { name: 'Krotka' }))
    await user.click(screen.getByRole('button', { name: 'Wspierajacy' }))
    await user.click(screen.getByRole('button', { name: 'Pamiec: ON' }))
    await user.click(screen.getByRole('button', { name: 'Internet: OFF' }))
    await user.click(screen.getByRole('button', { name: 'Deep analysis: OFF' }))
    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))

    await waitFor(() => {
      expect(api.sendChatTurn).toHaveBeenCalledWith(
        expect.objectContaining({
          execution_preferences: expect.objectContaining({
            response_style: 'supportive-guide',
            memory_enabled: false,
            internet_enabled: true,
            active_work_mode: 'deep',
          }),
        }),
        expect.objectContaining({
          signal: expect.any(AbortSignal),
        }),
      )
    })
  })

  it('filtruje rozmowy po tytule i podsumowaniu', async () => {
    const user = userEvent.setup()
    const bootstrap = buildBootstrapPayload()
    bootstrap.recentConversations = [
      bootstrap.recentConversations[0],
      {
        id: 102,
        title: 'Nowa rozmowa',
        projectId: 1,
        summary: 'Checklista wdrozenia Gemini i Linear',
        lastMessageAt: '2026-05-08T12:10:00Z',
        updatedAt: '2026-05-08T12:10:00Z',
      },
    ]

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

    render(<ChatRoute />)

    await user.click(screen.getByTestId('restore-chat-panels'))
    expect(screen.getAllByText('Checklista wdrozenia Gemini i Linear').length).toBeGreaterThan(0)

    await user.type(screen.getByPlaceholderText(/Szukaj rozm/i), 'gemini')

    expect(screen.getAllByText('Checklista wdrozenia Gemini i Linear').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /Plan short/i })).not.toBeInTheDocument()
  })

  it('odswieza aktywna rozmowe, gdy short workflow zmienia stan w tle', async () => {
    vi.useFakeTimers()
    primeChatStores()

    const queuedDetail = buildConversationDetailPayload()
    queuedDetail.messages = [
      {
        id: 501,
        role: 'user',
        status: 'completed',
        content: 'Zrob mi shorta o lokalnym AI',
        createdAt: '2026-05-10T12:00:00Z',
      },
      {
        id: 502,
        role: 'assistant',
        status: 'completed',
        content: 'Workflow shorta wystartowal. Zbieram assety i odpalam render w tle.',
        createdAt: '2026-05-10T12:00:02Z',
        metadataJson: {
          creative_video_operator: {
            workflow_key: 'content_campaign',
            approval_required: true,
            workflow_state: {
              status: 'queued',
              current_node: 'scheduler_dispatch',
            },
            quality_profile: 'premium',
            platform_hint: 'youtube-short',
            scheduler_job_id: 9,
          },
        },
      },
    ]

    const failedDetail = buildConversationDetailPayload()
    failedDetail.conversation.updatedAt = '2026-05-10T12:00:08Z'
    failedDetail.conversation.lastMessageAt = '2026-05-10T12:00:08Z'
    failedDetail.messages = [
      ...queuedDetail.messages,
      {
        id: 503,
        role: 'assistant',
        status: 'completed',
        content:
          'Workflow shorta zatrzymal sie w tle.\nBlokada: Free VRAM below 6 GB floor.\nNastepny krok: Zwolnij GPU albo przelacz rozmowe na `Szybciej`, potem uruchom short ponownie.',
        createdAt: '2026-05-10T12:00:08Z',
        metadataJson: {
          creative_video_operator: {
            workflow_key: 'content_campaign',
            blocked: true,
            blocker: 'Free VRAM below 6 GB floor.',
            next_step:
              'Zwolnij GPU albo przelacz rozmowe na `Szybciej`, potem uruchom short ponownie.',
            workflow_state: {
              status: 'failed',
              current_node: 'hardware_guard',
            },
            quality_profile: 'premium',
            platform_hint: 'youtube-short',
            scheduler_job_id: 9,
          },
        },
      },
    ]

    vi.mocked(api.fetchConversationDetail)
      .mockResolvedValueOnce(buildConversationDetailPayload())
      .mockResolvedValueOnce(queuedDetail)
      .mockResolvedValueOnce(failedDetail)
      .mockResolvedValue(failedDetail)
    vi.mocked(api.sendChatTurn).mockResolvedValue({
      ...buildChatTurnResponse(),
      assistantMessage: 'Workflow shorta wystartowal.',
      selectedProviderKey: 'creative-video-operator',
      selectedModelKey: 'campaign-youtube-short',
    })

    render(<ChatRoute />)

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)

    fireEvent.click(screen.getByRole('button', { name: 'Wyslij test' }))

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(api.fetchConversationDetail).toHaveBeenCalledTimes(2)
    expect(
      screen.getByText('Workflow shorta wystartowal. Zbieram assety i odpalam render w tle.'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('creative-video-workflow-refresh-indicator'),
    ).toHaveTextContent('Short odswieza sie w tle')
    expect(screen.getByText('YouTube Short')).toBeInTheDocument()
    expect(screen.getByText('etap: Uruchamia workflow')).toBeInTheDocument()
    expect(screen.getByText('ATNOS pracuje nad shortem')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(3500)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(api.fetchConversationDetail).toHaveBeenCalledTimes(3)
    expect(screen.getByText(/Workflow shorta zatrzymal sie w tle\./)).toBeInTheDocument()
    expect(
      screen.queryByTestId('creative-video-workflow-refresh-indicator'),
    ).not.toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(7000)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(api.fetchConversationDetail).toHaveBeenCalledTimes(3)
    expect(useChatStore.getState().conversations[0]?.updatedAt).toBe('2026-05-10T12:00:08Z')
  })

  it('pokazuje selektor jakosci bez wymuszonego wyboru projektu', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Przywróć panele rozmowy' }))
    expect(screen.getByText('JAKOSC')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Standard' }).length).toBeGreaterThan(0)
  })

  it('pokazuje realny status czatu lokalnego zamiast mylącego Browser AI Workers', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchAiWorkersStatus).toHaveBeenCalled()
    })

    await user.click(screen.getByRole('button', { name: 'Przywróć panele rozmowy' }))
    expect(screen.getByText(/Agent czatu: pętla narzędzi/)).toBeInTheDocument()
    expect(screen.getByText(/Ollama: gotowy/)).toBeInTheDocument()
    expect(
      screen.getByText((content) => content.includes('czatu:') && /\bON\b|\bOFF\b/.test(content)),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Browser AI: zablokowane/)).not.toBeInTheDocument()
  })

  it('w trybie delegacji pokazuje status Browser AI Workers', async () => {
    const user = userEvent.setup()
    primeChatStores()

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchAiWorkersStatus).toHaveBeenCalled()
    })

    await user.click(screen.getByRole('button', { name: 'Przywróć panele rozmowy' }))
    await user.click(screen.getByRole('button', { name: 'Delegacja lokalna' }))
    expect(screen.getByText(/AI Workers:/)).toHaveTextContent('1/2 gotowe')
    expect(screen.getByText(/Browser AI: zablokowane/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Browser AI po redakcji' })).toBeDisabled()
    expect(screen.getByText(/Browser AI jest ukryte przed delegacja/)).toHaveTextContent(
      'Browser control is disabled in runtime settings.',
    )
  })

  it('po 3 minutach nie zabija requestu automatycznie (tylko soft-timeout ostrzega)', async () => {
    vi.useFakeTimers()
    primeChatStores()

    vi.mocked(api.sendChatTurn).mockReturnValue(new Promise(() => undefined))

    render(<ChatRoute />)

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Wyslij test' }))

    await act(async () => {
      vi.advanceTimersByTime(180000)
      await Promise.resolve()
      await Promise.resolve()
    })

    const pendingMessage = useChatStore
      .getState()
      .messages.find((message) => message.content === 'Hej Atnos')

    expect(useToastStore.getState().toasts.some((toast) => toast.title === 'ATNOS nadal pracuje')).toBe(
      true,
    )
    expect(pendingMessage?.status).not.toBe('failed')
    expect(pendingMessage?.metadataJson?.optimisticState).toBe('pending')
    expect(useChatStore.getState().activeTurn?.summary).toBe('Hej Atnos')
  })

  it('uzywa lokalnego endpointu poprawy promptu', async () => {
    primeChatStores()
    vi.mocked(api.improveChatPrompt).mockResolvedValue({
      originalContent: 'powiedz jakie karty sa otwarte w chrome?',
      improvedContent:
        'Sprawdz, jakie karty sa teraz otwarte w Google Chrome. Zwroc tytul i URL kazdej karty.',
      strategyNote: 'Doprecyzowano cel i format odpowiedzi.',
      changed: true,
    })

    render(<ChatRoute />)

    fireEvent.click(screen.getByRole('button', { name: 'popraw-prompt-test' }))

    await waitFor(() => {
      expect(api.improveChatPrompt).toHaveBeenCalledWith(
        'powiedz jakie karty sa otwarte w chrome?',
      )
    })
  })

  it('nie gubi aktywnego turnu po odmontowaniu zakladki chat', async () => {
    const user = userEvent.setup()
    primeChatStores()
    const turnResult = buildChatTurnResponse()

    let resolveTurn: ((value: ReturnType<typeof buildChatTurnResponse>) => void) | null = null
    const turnPromise = new Promise<ReturnType<typeof buildChatTurnResponse>>((resolve) => {
      resolveTurn = resolve
    })

    vi.mocked(api.sendChatTurn).mockReturnValue(turnPromise)

    const view = render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))

    await waitFor(() => {
      expect(useChatStore.getState().activeTurn?.summary).toBe('Hej Atnos')
    })

    view.unmount()

    expect(useChatStore.getState().activeTurn?.summary).toBe('Hej Atnos')

    await act(async () => {
      resolveTurn?.(turnResult)
      await turnPromise
    })

    await waitFor(() => {
      expect(useChatStore.getState().activeTurn).toBeNull()
      expect(useChatStore.getState().selectedConversationTitle).toBe(turnResult.conversationTitle)
    })
  })

  it('hydrateuje rozmowe po structured runtime_error, zeby karta bledu byla widoczna od razu', async () => {
    const user = userEvent.setup()
    primeChatStores()
    const turnResult = {
      ...buildChatTurnResponse(),
      assistantMessage: '',
      runtimeError: {
        failureType: 'request_timeout' as const,
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
      },
    }

    vi.mocked(api.sendChatTurn).mockResolvedValue(turnResult)

    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledTimes(2)
    })
  })

  it('uruchamia lokalny /briefing bez zwyklego chat turnu', async () => {
    const bootstrap = primeChatStores()
    vi.mocked(api.fetchBootstrap).mockResolvedValue(bootstrap)
    vi.mocked(api.createChatBriefing).mockResolvedValue({
      conversationId: 101,
      conversationTitle: 'Plan shortów AI',
      assistantMessageId: 777,
      content: '- Active project: Projekt bazowy',
      metadataJson: {
        kind: 'briefing_card',
        briefing_card: {
          title: 'BRIEFING',
          generated_at: '2026-05-10T08:00:00Z',
          items: [],
          project_id: 1,
          project_name: 'Projekt bazowy',
          calendar_connected: false,
        },
      },
    })

    const user = userEvent.setup()
    render(<ChatRoute />)

    await waitFor(() => {
      expect(api.fetchConversationDetail).toHaveBeenCalledWith(101)
    })

    await user.click(screen.getByRole('button', { name: 'Briefing test' }))

    await waitFor(() => {
      expect(api.createChatBriefing).toHaveBeenCalledWith({
        conversationId: 101,
        projectId: 1,
      })
    })
    expect(api.sendChatTurn).not.toHaveBeenCalled()
  })

  it('renderuje zwinieta karte zaplanowanych zadan i pokazuje wpisy po rozwinieciu', async () => {
    const user = userEvent.setup()
    primeChatStores()
    act(() => {
      useChatStore.getState().setSelectedProjectId(1)
    })
    vi.mocked(api.fetchScheduledTasks).mockResolvedValue([
      {
        scheduledTaskId: 41,
        conversationId: 101,
        projectId: 1,
        projectName: 'Projekt bazowy',
        userId: 1,
        promptTemplate: 'zrob przeglad projektu',
        cronSpec: '0 9 * * 1',
        nextRunAt: '2026-05-12T09:00:00Z',
        lastRunAt: null,
        lastRunStatus: null,
        isActive: true,
        createdAt: '2026-05-08T10:00:00Z',
        updatedAt: '2026-05-08T10:00:00Z',
      },
    ])

    render(<ChatRoute />)

    await user.click(screen.getByTestId('restore-chat-panels'))
    expect(
      (await screen.findAllByRole('button', { name: /ZAPLANOWANE ZADANIA/i }))[0],
    ).toBeInTheDocument()
    expect(screen.queryByText('zrob przeglad projektu')).not.toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: /ZAPLANOWANE ZADANIA/i })[0])

    expect((await screen.findAllByText('zrob przeglad projektu'))[0]).toBeInTheDocument()
    expect(screen.getAllByText('0 9 * * 1')[0]).toBeInTheDocument()
  })

  it('zachowuje identyfikator nowej rozmowy po przerwaniu przed odpowiedzia', async () => {
    const bootstrap = primeChatStores()
    vi.mocked(api.fetchBootstrap).mockResolvedValue(bootstrap)
    const detail = buildConversationDetailPayload()
    detail.conversation.id = 909
    vi.mocked(api.fetchConversationDetail).mockResolvedValue(detail)
    act(() => useChatStore.getState().setSelectedConversation(null))
    vi.mocked(api.sendChatTurnStream).mockImplementationOnce(async (_payload, options) => {
      options.onEvent({ type: 'conversation', conversation_id: 909,
        conversation_title: 'Audit accepted', user_message_id: 1234 })
      throw new DOMException('Audit cancel', 'AbortError')
    })
    const user = userEvent.setup()
    render(<ChatRoute />)
    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))
    await waitFor(() => expect(useChatStore.getState().selectedConversationId).toBe(909))
    await user.click(screen.getByRole('button', { name: 'Wyslij test' }))
    await waitFor(() => expect(api.sendChatTurnStream).toHaveBeenLastCalledWith(
      expect.objectContaining({ conversation_id: 909 }), expect.anything()))
  })

  it('nie pokazuje juz panelu trusted auto w zakladce chat', async () => {
    primeChatStores()

    render(<ChatRoute />)

    expect(screen.queryByText('TRUSTED AUTO')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Run campaign/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Pokaz panel wykonania/i })).not.toBeInTheDocument()
  })
})

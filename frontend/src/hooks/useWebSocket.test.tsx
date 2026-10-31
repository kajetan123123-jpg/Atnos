import { render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWebSocket } from './useWebSocket'
import { useChatStore } from '../stores/chatStore'
import { useContentMachineStore } from '../stores/contentMachineStore'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import { buildAccessRealitySnapshot } from '../test/factories'
import { resetAllStores } from '../test/resetStores'

class MockWebSocket {
  static instances: MockWebSocket[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  url: string
  readyState = MockWebSocket.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send() {}

  close() {
    this.readyState = MockWebSocket.CLOSED
  }

  emitOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  emitMessage(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent)
  }

  emitClose() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close'))
  }
}

function Harness() {
  useWebSocket()
  return null
}

function ForceHarness() {
  useWebSocket({ forceConnect: true, silentBackgroundRetry: true })
  return null
}

function hydrateBootstrap() {
  useChatStore.getState().setBootstrap({
    uiVersion: 'axiom-prod-v1',
    projects: [],
    conversations: [],
  })
}

describe('useWebSocket', () => {
  beforeEach(() => {
    resetAllStores()
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('waits for bootstrap hydration before opening websocket', async () => {
    render(<Harness />)

    expect(MockWebSocket.instances).toHaveLength(0)

    hydrateBootstrap()

    await waitFor(() => {
      expect(MockWebSocket.instances).toHaveLength(1)
    })
  })

  it('stores access reality snapshots and shows toasts for visible access changes', async () => {
    hydrateBootstrap()
    render(<Harness />)

    const socket = MockWebSocket.instances[0]
    expect(socket).toBeDefined()

    socket.emitOpen()
    socket.emitMessage({
      type: 'access.reality',
      payload: {
        snapshot_key: 'access-1',
        checked_at: '2026-05-10T15:00:00Z',
        summary: {
          total_subjects: 1,
          none: 0,
          identity_only: 0,
          metadata_only: 0,
          configured: 0,
          live_read: 1,
          live_write: 0,
          blocked: 0,
          unavailable: 0,
        },
        subjects: [
          {
            subject_key: 'provider.ollama-local',
            subject_type: 'provider',
            display_name: 'Ollama Local',
            access_level: 'live_read',
            summary: 'Provider is reachable and can enter routing.',
            reasons: [],
            available_actions: [],
            account_label: null,
            connector_key: null,
            metadata: {},
            last_checked_at: '2026-05-10T15:00:00Z',
          },
        ],
        operational_context: buildAccessRealitySnapshot().operationalContext,
      },
    })

    await waitFor(() => {
      expect(useSystemStore.getState().accessReality?.snapshotKey).toBe('access-1')
    })

    socket.emitMessage({
      type: 'access.reality.change',
      payload: {
        snapshot_key: 'access-2',
        changes: [
          {
            subject_key: 'provider.ollama-local',
            subject_type: 'provider',
            display_name: 'Ollama Local',
            from_access_level: 'live_read',
            to_access_level: 'unavailable',
            change_kind: 'access_level',
            title: 'Ollama runtime przestal odpowiadac',
            detail: 'Runtime health check failed.',
            user_visible: true,
          },
        ],
      },
    })

    await waitFor(() => {
      expect(useSystemStore.getState().accessRealityChanges).toHaveLength(1)
    })

    expect(useToastStore.getState().toasts.at(-1)?.title).toBe(
      'Ollama runtime przestal odpowiadac',
    )
  })

  it('keeps NETWORK online when websocket disconnects but backend health signal is alive', async () => {
    hydrateBootstrap()
    render(<Harness />)

    const socket = MockWebSocket.instances[0]
    expect(socket).toBeDefined()

    socket.emitOpen()
    useSystemStore.getState().setMetrics({
      chroma: 'connected',
      network: 'online',
      rttMs: 24,
    })
    useSystemStore.getState().setNetworkSignals({
      browserOnline: true,
      backendReachable: true,
      rttMs: 24,
    })

    socket.emitClose()

    await waitFor(() => {
      expect(useSystemStore.getState().network).toBe('online')
      expect(useSystemStore.getState().chroma).toBe('reconnecting')
      expect(useSystemStore.getState().rttMs).toBeNull()
      expect(useSystemStore.getState().agentStage).toBe('idle')
      expect(useSystemStore.getState().websocketStatus).toBe('reconnecting')
    })
  })

  it('moves websocket into background retry after ten failed reconnect attempts', async () => {
    vi.useFakeTimers()
    hydrateBootstrap()
    render(<Harness />)

    expect(MockWebSocket.instances).toHaveLength(1)
    MockWebSocket.instances[0].emitClose()

    for (let index = 0; index < 10; index += 1) {
      await vi.runOnlyPendingTimersAsync()
      MockWebSocket.instances.at(-1)?.emitClose()
    }

    expect(useSystemStore.getState().websocketStatus).toBe('background_retry')
    expect(useSystemStore.getState().websocketNextRetryMs).toBe(30000)
    expect(useToastStore.getState().toasts.at(-1)?.title).toBe('Live sync odzyskuje polaczenie')
    vi.useRealTimers()
  })

  it('resyncs active conversation after reconnect using the last known message id as guard', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({
        conversation: {
          id: 101,
          title: 'Recovered chat',
          project_id: null,
          summary: 'Recovered summary',
          last_message_at: '2026-05-25T12:00:02Z',
          updated_at: '2026-05-25T12:00:02Z',
          active_work_mode: 'auto',
        },
        messages: [
          {
            id: 1,
            role: 'user',
            status: 'completed',
            content: 'before reconnect',
            created_at: '2026-05-25T12:00:00Z',
          },
          {
            id: 2,
            role: 'assistant',
            status: 'completed',
            content: 'after reconnect',
            created_at: '2026-05-25T12:00:02Z',
          },
        ],
        settings: {
          memory_enabled: true,
          file_enabled: true,
          internet_enabled: false,
          persona_profile: 'strategist',
          provider_posture: 'prefer-local',
          sources: {},
        },
        memory_cards: null,
      }), { status: 200 })),
    )
    hydrateBootstrap()
    useChatStore.getState().setConversationDetail({
      conversationId: 101,
      title: 'Recovered chat',
      messages: [
        {
          id: 1,
          role: 'user',
          status: 'completed',
          content: 'before reconnect',
          createdAt: '2026-05-25T12:00:00Z',
        },
      ],
      settings: {
        activeWorkMode: 'auto',
        responseStyle: null,
        memoryEnabled: true,
        fileEnabled: true,
        internetEnabled: false,
        personaProfile: 'strategist',
        providerPosture: 'prefer-local',
        qualityProfile: 'standard',
        sources: {},
      },
      memoryCards: null,
    })
    render(<Harness />)

    MockWebSocket.instances[0].emitOpen()
    MockWebSocket.instances[0].emitClose()
    await vi.runOnlyPendingTimersAsync()
    MockWebSocket.instances[1].emitOpen()
    await vi.advanceTimersByTimeAsync(0)
    await Promise.resolve()
    await Promise.resolve()

    expect(useChatStore.getState().messages).toHaveLength(2)
    expect(useChatStore.getState().messages.at(-1)?.content).toBe('after reconnect')
    expect(useChatStore.getState().conversations[0].summary).toBe('Recovered summary')
    vi.useRealTimers()
  })

  it('hydrates canonical message frames into the chat store', async () => {
    hydrateBootstrap()
    render(<Harness />)

    const socket = MockWebSocket.instances[0]
    expect(socket).toBeDefined()

    socket.emitOpen()
    socket.emitMessage({
      type: 'message',
      payload: {
        id: 'msg-1',
        speaker: 'atnos',
        text: 'Canonical websocket payload',
        timestamp: 1_715_000_000_000,
        meta: {
          model: 'llama3:8b',
          latencyMs: 420,
        },
      },
    })

    await waitFor(() => {
      expect(useChatStore.getState().messages).toHaveLength(1)
    })

    const message = useChatStore.getState().messages[0]
    expect(message.role).toBe('assistant')
    expect(message.content).toBe('Canonical websocket payload')
    expect(message.metadataJson).toEqual({
      messageMeta: {
        model: 'llama3:8b',
        latencyMs: 420,
      },
    })
  })

  it('routes canonical pipeline step and asset frames into the pipeline store', async () => {
    hydrateBootstrap()
    usePipelineStore.getState().hydratePipeline({
      currentJob: {
        id: 'job-1',
        title: 'WS pipeline job',
        createdAt: 1,
        platform: 'youtube',
        awaitingApproval: false,
        steps: [
          { id: 'research', state: 'pending', progress: 0 },
          { id: 'thumbnail', state: 'pending', progress: 0 },
        ],
      },
      history: [],
    })

    render(<Harness />)

    const socket = MockWebSocket.instances[0]
    expect(socket).toBeDefined()

    socket.emitOpen()
    socket.emitMessage({
      type: 'pipeline.step',
      payload: {
        jobId: 'job-1',
        stepId: 'research',
        state: 'active',
        progress: 0.4,
        durationMs: 1200,
      },
    })
    socket.emitMessage({
      type: 'pipeline.asset',
      payload: {
        jobId: 'job-1',
        kind: 'thumbnail',
        url: 'https://example.com/thumb.png',
      },
    })

    await waitFor(() => {
      expect(usePipelineStore.getState().currentJob?.steps[0].state).toBe('active')
    })

    const currentJob = usePipelineStore.getState().currentJob
    expect(currentJob?.steps[0].progress).toBe(0.4)
    expect(currentJob?.steps[0].durationMs).toBe(1200)
    expect(currentJob?.thumbnailUrl).toBe('https://example.com/thumb.png')
  })

  it('routes content machine pipeline_step and job_log frames without chat bootstrap', async () => {
    render(<ForceHarness />)

    const socket = MockWebSocket.instances[0]
    expect(socket).toBeDefined()

    socket.emitOpen()
    socket.emitMessage({
      type: 'pipeline_step',
      accountId: 'yt-finanse-pl',
      job_id: 'job-live-1',
      title: 'Live backend short',
      step: 'UPLOAD',
      status: 'done',
      progress: 100,
      videoId: 'abc123',
      videoUrl: 'https://youtube.com/watch?v=abc123',
    })
    socket.emitMessage({
      type: 'job_log',
      account_id: 'yt-finanse-pl',
      job_id: 'job-live-1',
      step: 'UPLOAD',
      level: 'info',
      message: 'Upload completed by backend',
    })

    await waitFor(() => {
      expect(useContentMachineStore.getState().jobs['job-live-1']?.videoUrl).toBe(
        'https://youtube.com/watch?v=abc123',
      )
    })

    const job = useContentMachineStore.getState().jobs['job-live-1']
    expect(job.steps.find((step) => step.id === 'upload')?.status).toBe('done')
    expect(useContentMachineStore.getState().logs['yt-finanse-pl'][0].message).toBe(
      'Upload completed by backend',
    )
  })

  it('keeps content machine websocket retry silent in background mode', async () => {
    vi.useFakeTimers()
    render(<ForceHarness />)

    expect(MockWebSocket.instances).toHaveLength(1)
    MockWebSocket.instances[0].emitClose()

    for (let index = 0; index < 10; index += 1) {
      await vi.runOnlyPendingTimersAsync()
      MockWebSocket.instances.at(-1)?.emitClose()
    }

    expect(useSystemStore.getState().websocketStatus).toBe('background_retry')
    expect(useToastStore.getState().toasts).toHaveLength(0)
    vi.useRealTimers()
  })

  it('maps agent graph progress into readable activity labels', async () => {
    hydrateBootstrap()
    render(<Harness />)

    const socket = MockWebSocket.instances[0]
    expect(socket).toBeDefined()

    socket.emitOpen()
    socket.emitMessage({
      type: 'graph.progress',
      payload: {
        current_step: 'execute_tool',
        completed_steps: 6,
        total_steps: 10,
        status: 'running',
      },
    })

    await waitFor(() => {
      expect(useSystemStore.getState().agentActivity?.title).toBe('Wykonuje narzedzie')
    })

    expect(useSystemStore.getState().agentActivity?.stage).toBe('executing')
    expect(useSystemStore.getState().agentActivity?.detail).toBe('Krok 6/10 workflow.')
  })

  it('hydrates hardware snapshots into the system store metrics', async () => {
    hydrateBootstrap()
    render(<Harness />)

    const socket = MockWebSocket.instances[0]
    expect(socket).toBeDefined()

    socket.emitOpen()
    socket.emitMessage({
      type: 'hardware.snapshot',
      payload: {
        used_vram_mb: 4096,
        total_vram_mb: 32768,
        free_ram_mb: 22000,
        total_ram_mb: 32768,
        status: 'ready',
        notes: null,
      },
    })

    await waitFor(() => {
      expect(useSystemStore.getState().hardwareSnapshot?.totalVramMb).toBe(32768)
    })

    expect(useSystemStore.getState().vramUsedMB).toBe(4096)
    expect(useSystemStore.getState().vramTotalMB).toBe(32768)
  })
})

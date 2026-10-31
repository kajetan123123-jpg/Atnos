import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { StatusHUD, StatusHUDView } from './StatusHUD'
import { useChatStore } from '../stores/chatStore'
import { useSystemStore } from '../stores/systemStore'
import { resetAllStores } from '../test/resetStores'

const BASE_PROPS = {
  agentStage: 'idle',
  model: { name: 'llama3:8b', provider: 'ollama' },
  vramUsedMB: 8192,
  vramTotalMB: 16384,
  chroma: 'connected',
  network: 'online',
  rttMs: 42,
  accessStatus: 'active',
} as const

describe('StatusHUDView', () => {
  beforeEach(() => {
    resetAllStores()
    window.innerWidth = 1280
    window.dispatchEvent(new Event('resize'))
  })

  it('renders the canonical stage pill, access badge, and model block', () => {
    render(<StatusHUDView {...BASE_PROPS} />)

    expect(screen.getByText('BEZCZYNNY')).toBeInTheDocument()
    expect(screen.getByText('FULL ACCESS ACTIVE')).toBeInTheDocument()
    expect(screen.getByText('MODEL')).toBeInTheDocument()
    expect(screen.getByText('llama3:8b')).toBeInTheDocument()
    expect(screen.getByText('CHROMA')).toBeInTheDocument()
    expect(screen.getByText('NETWORK')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /pipeline/i })).not.toBeInTheDocument()
  })

  it('announces the critical VRAM threshold after crossing 14 GB', async () => {
    const { rerender } = render(<StatusHUDView {...BASE_PROPS} />)

    rerender(
      <StatusHUDView
        {...BASE_PROPS}
        vramUsedMB={15360}
      />,
    )

    expect(await screen.findByText(/Krytyczny poziom pami.*ci VRAM/i)).toBeInTheDocument()
    expect(screen.getAllByText('15,0 / 16,0 GB')).toHaveLength(1)
  })

  it('shows access limited when critical runtime permissions are missing', () => {
    render(
      <StatusHUDView
        {...BASE_PROPS}
        accessStatus="limited"
        accessDetail="Brakuje: browser control jest wylaczone."
      />,
    )

    expect(screen.getByText('ACCESS LIMITED')).toBeInTheDocument()
  })

  it('uses the agent stage from the system store without chat-side overrides', () => {
    useSystemStore.setState({
      agentStage: 'offline',
      capabilities: {
        browserControlEnabled: true,
        externalActionsEnabled: true,
        schedulerEnabled: true,
        playwrightAdapterAvailable: true,
        oauthEnabled: true,
        obsidianExportEnabled: true,
        autonomyMode: 'trusted_auto',
        executionEnabled: true,
      },
      trustedAutoSummary: {
        runtimeControls: {
          autonomyMode: 'trusted_auto',
          schedulerEnabled: true,
          emergencyStopEnabled: true,
          pauseAllJobs: false,
          loginAutomationEnabled: true,
          twoFactorAutomationEnabled: true,
          publishAutomationEnabled: true,
          defaultRetryBudget: 2,
          defaultVramFloorGb: 4,
        },
        accounts: [],
        trustGrants: [],
        schedulerJobs: [],
        executionRuns: [],
        mediaPipelinePresets: [],
        hardwareSnapshot: null,
        browserRuntime: null,
        youtubeConnector: null,
        contentReadiness: null,
      },
    })
    useChatStore.setState({
      activeTurn: {
        kind: 'chat',
        conversationId: null,
        projectId: null,
        summary: 'aktywny turn',
        startedAt: new Date().toISOString(),
      },
    })

    render(<StatusHUD />)

    expect(screen.getByText('OFFLINE')).toBeInTheDocument()
    expect(screen.getByText('FULL ACCESS ACTIVE')).toBeInTheDocument()
    expect(screen.queryByText(/MY/i)).not.toBeInTheDocument()
  })
})

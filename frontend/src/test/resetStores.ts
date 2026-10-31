import { DEMO_ACCOUNTS, useAccountStore } from '../stores/accountStore'
import { useChatStore } from '../stores/chatStore'
import { useContentMachineStore } from '../stores/contentMachineStore'
import { usePipelineStore } from '../stores/pipelineStore'
import { useRevenueStore } from '../stores/revenueStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import { useUIStore } from '../stores/uiStore'

export function resetAllStores() {
  useChatStore.setState({
    uiVersion: 'axiom-prod-v1',
    projects: [],
    conversations: [],
    selectedProjectId: null,
    selectedConversationId: null,
    selectedConversationTitle: null,
    hydratedConversationId: null,
    messages: [],
    searchQuery: '',
    settings: null,
    memoryCards: null,
    activeTurn: null,
    queuedTurns: [],
    loadingConversation: false,
    bootstrapHydrated: false,
  })

  usePipelineStore.setState({
    snapshot: null,
    currentJob: null,
    history: [],
  })

  useSystemStore.setState({
    agentStage: 'idle',
    agentSnapshot: {
      stage: 'idle',
      lastActionAt: null,
      currentTask: null,
      errorMessage: null,
    },
    agentActivity: null,
    vramUsedMB: 0,
    vramTotalMB: 16384,
    model: { name: 'Nieustalony model', provider: 'ollama' },
    backendCheckedAt: null,
    chroma: 'down',
    network: 'offline',
    rttMs: null,
    updatedAt: 0,
    networkSignals: {
      browserOnline: true,
      backendReachable: false,
    },
    websocketStatus: 'idle',
    websocketRetryCount: 0,
    websocketNextRetryMs: null,
    runtimeSummary: null,
    providerCards: [],
    capabilities: null,
    accessReality: null,
    accessRealityChanges: [],
    trustedAutoSummary: null,
    schedulerJobs: [],
    executionRuns: [],
    remoteWorkers: [],
    remoteExecutionRuns: [],
    hardwareSnapshot: null,
    ambientState: null,
  })

  useToastStore.setState({
    queue: [],
    toasts: [],
  })

  useUIStore.setState({
    sidebarExpanded: true,
    sidebarPreferenceSet: false,
    sidebarDrawerOpen: false,
    hudDetailsOpen: false,
    paletteOpen: false,
    lowPerf: false,
    pipelineSheetOpen: false,
    desktopPipelineOpen: false,
    okropekModeEnabled: false,
    okropekPanelOpen: false,
    okropekDockSide: 'right',
    briefingNonce: 0,
    decisionsNonce: 0,
    composerFocusNonce: 0,
    chatCommandSurface: null,
  })

  useAccountStore.setState({
    accounts: DEMO_ACCOUNTS,
    activeAccountId: DEMO_ACCOUNTS[0].id,
  })

  useContentMachineStore.setState({
    jobs: {},
    queue: {},
    logs: {},
    activeJobs: {},
  })

  useRevenueStore.setState({
    pointsByAccount: {},
    statsByAccount: {},
  })
}

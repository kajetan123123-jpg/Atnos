import { create } from 'zustand'
import type { AgentSnapshot, AgentStage } from '../types/agent'
import type {
  AccessChange,
  AccessRealitySnapshot,
  AmbientState,
  AgentActivityState,
  CapabilityFlags,
  ProviderCard,
  RemoteExecutionRun,
  RemoteWorkerSummary,
  RuntimeSummary,
  TrustedAutoExecutionRun,
  TrustedAutoHardwareSnapshot,
  TrustedAutoSchedulerJob,
  TrustedAutoSummary,
  SystemMetrics as CanonicalSystemMetrics,
} from '../types/system'

export type SystemProvider = 'ollama' | 'openai' | 'anthropic' | 'gemini'

export interface SystemModel {
  name: string
  provider: SystemProvider
}

export interface SystemMetrics {
  vramUsedMB: number
  vramTotalMB: number
  model: SystemModel
  chroma: 'connected' | 'reconnecting' | 'down'
  network: 'online' | 'degraded' | 'offline'
  rttMs: number | null
  updatedAt: number
}

export type WebSocketStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'background_retry'
  | 'disconnected'

interface NetworkSignals {
  browserOnline: boolean
  backendReachable: boolean
}

interface SystemState extends SystemMetrics {
  agentStage: AgentStage
  agentSnapshot: AgentSnapshot
  agentActivity: AgentActivityState | null
  networkSignals: NetworkSignals
  backendCheckedAt: number | null
  websocketStatus: WebSocketStatus
  websocketRetryCount: number
  websocketNextRetryMs: number | null
  runtimeSummary: RuntimeSummary | null
  providerCards: ProviderCard[]
  capabilities: CapabilityFlags | null
  accessReality: AccessRealitySnapshot | null
  accessRealityChanges: AccessChange[]
  trustedAutoSummary: TrustedAutoSummary | null
  schedulerJobs: TrustedAutoSchedulerJob[]
  executionRuns: TrustedAutoExecutionRun[]
  remoteWorkers: RemoteWorkerSummary[]
  remoteExecutionRuns: RemoteExecutionRun[]
  hardwareSnapshot: TrustedAutoHardwareSnapshot | null
  ambientState: AmbientState | null
  setAgentActivity: (activity: AgentActivityState | null) => void
  setStage: (stage: AgentStage) => void
  setMetrics: (metrics: Partial<SystemMetrics>) => void
  setNetworkSignals: (signals: Partial<NetworkSignals> & { rttMs?: number | null }) => void
  setWebSocketStatus: (payload: {
    status: WebSocketStatus
    retryCount?: number
    nextRetryMs?: number | null
  }) => void
  setAgentSnapshot: (snapshot: Partial<AgentSnapshot>) => void
  setRuntimeSummary: (summary: RuntimeSummary) => void
  setProviderCards: (cards: ProviderCard[]) => void
  updateProviderCard: (card: ProviderCard) => void
  setCapabilities: (capabilities: CapabilityFlags) => void
  setAccessReality: (snapshot: AccessRealitySnapshot | null) => void
  setAccessRealityChanges: (changes: AccessChange[]) => void
  setTrustedAutoSummary: (summary: TrustedAutoSummary) => void
  upsertSchedulerJob: (job: TrustedAutoSchedulerJob) => void
  upsertExecutionRun: (run: TrustedAutoExecutionRun) => void
  setRemoteWorkers: (workers: RemoteWorkerSummary[]) => void
  setRemoteExecutionRuns: (runs: RemoteExecutionRun[]) => void
  upsertRemoteWorker: (worker: RemoteWorkerSummary) => void
  upsertRemoteExecutionRun: (run: RemoteExecutionRun) => void
  setHardwareSnapshot: (snapshot: TrustedAutoHardwareSnapshot | null) => void
  setAmbientState: (state: AmbientState | null) => void
}

export const useSystemStore = create<SystemState>((set) => ({
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
  chroma: 'down',
  network: 'offline',
  rttMs: null,
  updatedAt: 0,
  networkSignals: {
    browserOnline:
      typeof navigator === 'undefined' ? true : navigator.onLine !== false,
    backendReachable: false,
  },
  websocketStatus: 'idle',
  backendCheckedAt: null,
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
  setAgentActivity: (agentActivity) =>
    set((state) => ({
      agentActivity,
      agentSnapshot: {
        ...state.agentSnapshot,
        currentTask: agentActivity?.title ?? null,
        errorMessage:
          agentActivity?.stage === 'error' ? agentActivity.detail ?? null : state.agentSnapshot.errorMessage,
      },
    })),
  setStage: (stage) =>
    set((state) => ({
      agentStage: stage,
      agentSnapshot: {
        ...state.agentSnapshot,
        stage,
        lastActionAt: Date.now(),
        errorMessage: stage === 'error' ? state.agentSnapshot.errorMessage : null,
      },
      agentActivity: stage === 'idle' || stage === 'offline' ? null : state.agentActivity,
    })),
  setMetrics: (metrics) =>
    set((state) => ({
      ...state,
      ...metrics,
      updatedAt: Date.now(),
    })),
  setNetworkSignals: ({ rttMs, ...signals }) =>
    set((state) => {
      const networkSignals = {
        ...state.networkSignals,
        ...signals,
      }
      const network = networkSignals.backendReachable
        ? 'online'
        : networkSignals.browserOnline
          ? 'degraded'
          : 'offline'
      return {
        networkSignals,
        backendCheckedAt: typeof signals.backendReachable === 'boolean' ? Date.now() : state.backendCheckedAt,
        network,
        rttMs:
          rttMs === undefined
            ? state.rttMs
            : networkSignals.backendReachable
              ? rttMs
              : null,
        updatedAt: Date.now(),
      }
    }),
  setWebSocketStatus: ({ status, retryCount = 0, nextRetryMs = null }) =>
    set({
      websocketStatus: status,
      websocketRetryCount: retryCount,
      websocketNextRetryMs: nextRetryMs,
    }),
  setAgentSnapshot: (snapshot) =>
    set((state) => ({
      agentSnapshot: {
        ...state.agentSnapshot,
        ...snapshot,
      },
    })),
  setRuntimeSummary: (runtimeSummary) => set({ runtimeSummary }),
  setProviderCards: (providerCards) => set({ providerCards }),
  updateProviderCard: (card) =>
    set((state) => ({
      providerCards: state.providerCards.map((item) =>
        item.providerKey === card.providerKey ? card : item,
      ),
    })),
  setCapabilities: (capabilities) => set({ capabilities }),
  setAccessReality: (accessReality) => set({ accessReality }),
  setAccessRealityChanges: (accessRealityChanges) => set({ accessRealityChanges }),
  setTrustedAutoSummary: (trustedAutoSummary) =>
    set({
      trustedAutoSummary,
      schedulerJobs: trustedAutoSummary.schedulerJobs,
      executionRuns: trustedAutoSummary.executionRuns,
      hardwareSnapshot: trustedAutoSummary.hardwareSnapshot,
    }),
  upsertSchedulerJob: (job) =>
    set((state) => ({
      schedulerJobs: [job, ...state.schedulerJobs.filter((item) => item.jobId !== job.jobId)].slice(0, 24),
    })),
  upsertExecutionRun: (run) =>
    set((state) => ({
      executionRuns: [run, ...state.executionRuns.filter((item) => item.executionRunId !== run.executionRunId)].slice(0, 24),
    })),
  setRemoteWorkers: (remoteWorkers) => set({ remoteWorkers }),
  setRemoteExecutionRuns: (remoteExecutionRuns) => set({ remoteExecutionRuns }),
  upsertRemoteWorker: (worker) =>
    set((state) => ({
      remoteWorkers: [worker, ...state.remoteWorkers.filter((item) => item.nodeId !== worker.nodeId)].slice(0, 16),
    })),
  upsertRemoteExecutionRun: (run) =>
    set((state) => ({
      remoteExecutionRuns: [run, ...state.remoteExecutionRuns.filter((item) => item.runId !== run.runId)].slice(0, 24),
    })),
  setHardwareSnapshot: (hardwareSnapshot) => set({ hardwareSnapshot }),
  setAmbientState: (ambientState) => set({ ambientState }),
}))

export const useSystem = useSystemStore
export type { CanonicalSystemMetrics }

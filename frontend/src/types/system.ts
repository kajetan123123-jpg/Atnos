import type { AgentStage } from './agent'

export interface SystemMetrics {
  vramUsedMB: number
  vramTotalMB: number
  model: { name: string; provider: 'ollama' | 'openai' | 'anthropic' | 'gemini' }
  chroma: 'connected' | 'reconnecting' | 'down'
  network: 'online' | 'degraded' | 'offline'
  rttMs: number | null
  updatedAt: number
}

export type ConnectionState =
  | { kind: 'disconnected' }
  | { kind: 'configured'; message?: string }
  | { kind: 'testing' }
  | { kind: 'connected' }
  | { kind: 'error'; message: string }
  | { kind: 'rate_limited'; resetInSec: number | null }

export interface ProviderCard {
  providerKey: 'openai' | 'anthropic' | 'ollama' | 'gemini'
  displayName: string
  status: string
  statusReason: string
  supportsApiKey: boolean
  supportsBaseUrl: boolean
  providerEnabled: boolean
  baseUrl: string | null
  accountId: number | null
  hasSecret: boolean
  maskedSecretState: string
  runtimeAdapterAvailable: boolean
  runtimeHealthStatus: string
  runtimeHealthReason: string
  sanityStatus: string
  sanityReason: string
  readinessStatus: string
  readinessReason: string
  oauthConnectorKey: string | null
}

export interface ProviderCardTestResult {
  providerKey: string
  status: string
  connectionState: 'connected' | 'error' | 'rate_limited'
  detail: string
  message: string
  healthStatus: string
  latencyMs: number | null
  installedModels: string[]
  resetInSec: number | null
  testedAt: string
}

export interface RuntimeSummary {
  providersReady: number
  enabledModels: number
  tasksReady: number
  hasLocalChat: boolean
  hasExternalFallback: boolean
  voiceStt: {
    configuredEngine: string
    effectiveEngine: string
    status: string
    statusReason: string
    configuredModelPath?: string | null
    configuredDevice?: string
  }
}

export interface CapabilityFlags {
  browserControlEnabled: boolean
  externalActionsEnabled: boolean
  schedulerEnabled: boolean
  playwrightAdapterAvailable: boolean
  oauthEnabled: boolean
  obsidianExportEnabled: boolean
  autonomyMode?: string
  executionEnabled?: boolean
}

export interface AmbientContextSnapshot {
  foregroundWindowTitle: string | null
  activeAppName: string | null
  capturedAt: string
  timeOfDayLabel: string
  dayOfWeekLabel: string
  cpuPercent: number | null
  ramUsedMb: number | null
  ramTotalMb: number | null
  networkOnline: boolean | null
  projectId: number | null
  projectName: string | null
  lastUserInteractionAt: string | null
  hardware: {
    totalVramMb: number | null
    freeVramMb: number | null
    usedVramMb: number | null
    freeRamMb: number | null
    totalRamMb: number | null
    freeDiskGb: number | null
    diskPath: string | null
    status: string
    notes: string | null
  } | null
}

export interface AmbientState {
  ambientListeningEnabled: boolean
  ambientWakeWord: string
  ambientMuted: boolean
  ambientContextEnabled: boolean
  voiceRepliesEnabled: boolean
  voiceReplyVoiceId: number | null
  listeningAvailable: boolean
  listeningReason: string
  contextSnapshot: AmbientContextSnapshot | null
}

export interface LocalHostControlSummary {
  hostLabel: string
  platform: string
  currentUser: string
  currentWorkdir: string
  powershellAvailable: boolean
  browserOpenAvailable: boolean
  playwrightAdapterAvailable: boolean
  desktopInputAvailable: boolean
  browserRecipeAvailable: boolean
  elevated: boolean
  executionEnabled: boolean
  trustedAutoEnabled: boolean
  commandLogPath: string
  lastCheckedAt: string
  desktopApps: LocalHostDesktopApp[]
  chromeDiagnostics: LocalHostChromeDiagnostics | null
}

export interface LocalHostDesktopApp {
  appKey: string
  label: string
  category: string
  installed: boolean
  running: boolean
  windowVisible: boolean
  launchSupported: boolean
  focusSupported: boolean
  preferredEntryPath: string | null
  preferredEntryKind: string | null
  runningProcesses: string[]
  visibleWindowTitles: string[]
  notes: string[]
}

export interface LocalHostChromeTab {
  title: string | null
  url: string
  source: 'devtools' | 'session'
}

export interface LocalHostChromeDiagnostics {
  browserRunning: boolean
  processCount: number
  mode: 'devtools_live' | 'session_fallback' | 'window_titles_only' | 'unavailable'
  summary: string
  reasons: string[]
  remoteDebuggingPort: number | null
  visibleWindows: Array<Record<string, unknown>>
  tabs: LocalHostChromeTab[]
  detectedApps: string[]
  sessionSourceFile: string | null
  checkedAt: string
}

export interface LocalHostChromeBridgeResult {
  ok: boolean
  status: 'already_live' | 'launched' | 'restarted_existing'
  port: number
  browserExecutable: string
  usingDefaultProfile: boolean
  restartedExisting: boolean
  command: string[]
  notes: string[]
  startedAt: string
  diagnostics: LocalHostChromeDiagnostics
}

export interface JarvisBrowserSessionSummary {
  service: string
  displayName: string
  loginUrl: string
  validationUrl: string
  storageStatePath: string
  ready: boolean
  expired: boolean
  pendingManualLogin: boolean
  hasStorageState: boolean
  loginWindowOpen: boolean
  statusReason: string
  semaphoreFree: number | null
}

export interface AgentActivityState {
  title: string
  detail: string | null
  stage: AgentStage
}

export interface RemoteWorkerCapabilitySnapshot {
  snapshotId: number
  cpuPercent: number | null
  loadAvg: number[]
  ramTotalMb: number | null
  ramFreeMb: number | null
  diskFreeGb: number | null
  networkOnline: boolean | null
  sudoAvailable: boolean
  ollamaReady: boolean
  browserReady: boolean
  desktopBridgeReady: boolean
  ffmpegReady: boolean
  dockerReady: boolean
  displayMode: 'x11' | 'wayland' | 'unknown'
  browserMode: string
  pythonVersion: string | null
  ffmpegVersion: string | null
  capturedAt: string
  rawPayloadJson: Record<string, unknown>
}

export interface RemoteExecutionStep {
  stepId: number
  stepIndex: number
  executor: 'worker_core' | 'desktop_bridge' | 'local_executor'
  actionKind: string
  command: string | null
  browserTarget: string | null
  sudo: boolean
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  stdout: string | null
  stderr: string | null
  exitCode: number | null
  resultJson: Record<string, unknown>
  startedAt: string | null
  finishedAt: string | null
}

export interface RemoteExecutionRun {
  runId: number
  origin: 'manual' | 'scheduler' | 'trusted_auto' | 'system'
  requestedBy: string | null
  taskKind: string
  routingTarget: 'local' | 'remote'
  assignedNodeId: number | null
  assignedNodeLabel: string | null
  status:
    | 'queued'
    | 'dispatching'
    | 'running'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | 'local_required'
  label: string | null
  requestPayloadJson: Record<string, unknown>
  resultJson: Record<string, unknown>
  errorJson: Record<string, unknown>
  fallbackReason: string | null
  startedAt: string | null
  finishedAt: string | null
  createdAt: string
  updatedAt: string
  steps: RemoteExecutionStep[]
}

export interface RemoteWorkerSummary {
  nodeId: number
  label: string
  nodeType: string
  platform: string
  hostname: string | null
  host: string
  sshPort: number
  sshUser: string
  status:
    | 'provisioning'
    | 'enrolling'
    | 'connected'
    | 'disconnected'
    | 'paused'
    | 'revoked'
    | 'error'
  trustMode: 'trusted_auto' | 'supervised' | 'manual'
  transport: 'websocket' | 'ssh'
  displayMode: 'x11' | 'wayland' | 'unknown'
  browserDefaultTarget: string
  publicCapabilities: Record<string, unknown>
  metadataJson: Record<string, unknown>
  pauseRequested: boolean
  revokedAt: string | null
  lastSeenAt: string | null
  lastError: string | null
  capabilitySnapshot: RemoteWorkerCapabilitySnapshot | null
  recentRuns: RemoteExecutionRun[]
}

export interface TrustedAutoHardwareSnapshot {
  freeVramMb: number | null
  usedVramMb: number | null
  totalVramMb: number | null
  freeRamMb: number | null
  totalRamMb: number | null
  usedRamMb: number | null
  cpuPercent: number | null
  gpuName: string | null
  freeDiskGb: number | null
  diskPath: string | null
  status: string
  notes: string | null
}

export interface TrustedAutoBrowserRuntime {
  profileDir: string
  downloadsDir: string
  traceDir: string
  browsersPath: string
  browserExecutable: string | null
  desktopShell: string | null
  ready: boolean
  statusReason: string
}

export interface TrustedAutoCredentialProfile {
  credentialProfileId: number
  loginIdentifier: string | null
  hasPassword: boolean
  hasTotp: boolean
  hasBackupCodes: boolean
  recoveryEmail: string | null
  hasRecoveryEmailSecret: boolean
  connectorMappingJson: Record<string, unknown>
  status: string
  notes: string | null
}

export interface TrustedAutoTrustGrant {
  trustGrantId: number
  projectId: number
  projectName: string | null
  accountId: number
  accountLabel: string | null
  connectorKey: string
  mode: string
  domainAllowlist: string[]
  actionFamilies: string[]
  fileWriteScope: string[]
  loginAllowed: boolean
  twoFactorAllowed: boolean
  publishAllowed: boolean
  browserAutomationAllowed: boolean
  connectorWriteAllowed: boolean
  localRenderAllowed: boolean
  maxRetries: number
  cooldownMinutes: number
  isEnabled: boolean
  notes: string | null
  updatedAt: string
}

export interface TrustedAutoAccount {
  accountId: number
  label: string
  providerKey: string
  accountIdentifier: string
  secretType: string
  status: string
  hasSecret: boolean
  maskedSecretState: string
  linkedProjectId: number | null
  linkedIntegrationKey: string | null
  allowedCapabilities: string[]
  credentialProfile: TrustedAutoCredentialProfile | null
  trustGrants: TrustedAutoTrustGrant[]
}

export interface TrustedAutoAccountFeedback {
  sequence: number
  accountId: number
  label: string
  providerKey: string
  accountIdentifier: string
  url: string | null
  hasSecret: boolean
  grantCount: number
  checkedAt: string
  status:
    | 'ok'
    | 'http_restricted'
    | 'http_error'
    | 'timeout'
    | 'connection_error'
    | 'config_missing'
  httpStatus: number | null
  reason: string
}

export interface TrustedAutoSchedulerJob {
  jobId: number
  projectId: number
  projectName: string | null
  jobType: string
  triggerMode: string
  status: string
  connectorKey: string | null
  platformKey: string | null
  scheduledFor: string | null
  cronExpression: string | null
  payloadJson: Record<string, unknown>
  retryCount: number
  retryBudget: number
  requiresTrustedAuto: boolean
  cooldownKey: string | null
  lastError: string | null
  lastRunAt: string | null
  nextRunAt: string | null
  lockedAt: string | null
  cancelledAt: string | null
}

export interface TrustedAutoExecutionStep {
  executionStepId: number
  stepIndex: number
  stepType: string
  stepName: string
  status: string
  selectorHint: string | null
  errorMessage: string | null
  screenshotPath: string | null
  startedAt: string | null
  finishedAt: string | null
}

export interface AgentActionPreview {
  executionRunId: number
  projectId: number
  kind:
    | 'browser_navigate'
    | 'browser_form_submit'
    | 'file_write'
    | 'file_create'
    | 'file_delete'
    | 'publication'
    | 'api_billing'
    | 'secret_access'
    | 'main_pc_action'
  title: string
  summary: string
  impactSummary: string
  requiredApprovalLevel: 'single' | 'double'
  previewPayload: Record<string, unknown>
  status: string
  createdAt: string
  updatedAt: string
}

export interface AgentLogEntry {
  timestamp: string | null
  event?: string
  toolName?: string | null
  step?: string | null
  status?: string | null
  success?: boolean | null
  error?: string | null
  message?: string | null
  command?: string | null
  riskLevel?: string | null
  raw: Record<string, unknown>
}

export interface AgentRecentLogsPayload {
  decisions: AgentLogEntry[]
  toolCalls: AgentLogEntry[]
}

export interface AgentToolDescriptor {
  key: string
  label: string
  status: string
  riskLevel: string
  logPath: string | null
  remoteLogPath?: string | null
  actions: string[]
}

export interface AgentArtifactSummary {
  path: string
  storagePath: string
  filename: string
  extension: string
  sizeBytes: number
  updatedAt: string
}

export interface AgentQualityRunSummary {
  runId: string
  createdAt: string | null
  task: string
  area: string
  status: 'DONE_VERIFIED' | 'DONE_NEEDS_REVIEW' | 'FAILED_WITH_REASON' | string
  score: number | null
  reportPath: string | null
  jsonPath: string | null
  evidenceCount: number
}

export interface AgentStatusPayload {
  agent: {
    state: string
    activeTasks: number
    pendingApprovals: number
    errors: number
    lastDecisionAt: string | null
    lastToolCallAt: string | null
  }
  activeTasks: Array<Record<string, unknown>>
  ubuntu: Record<string, unknown>
  browser: Record<string, unknown>
  shortPipeline: Record<string, unknown>
  youtubeAutomation: Record<string, unknown>
  monetizationFactory: MonetizationFactoryStatus
  qualityRuntime: {
    ready: boolean
    runsDir: string | null
    statusReason: string | null
    latestRun: AgentQualityRunSummary | null
  }
  recentArtifacts: AgentArtifactSummary[]
  pendingApprovals: AgentActionPreview[]
  errors: Array<Record<string, unknown>>
  logs: Record<string, string>
}

export interface MonetizationFactoryStatus {
  status: string
  ready: boolean
  revenueVector: string
  stateMachineReady: boolean
  channelOpsReady: boolean
  shortsContractReady: boolean
  approvalGateReady: boolean
  youtubeReadinessStatus: string
  privateUploadOnly: boolean
  manualUploadReadySupported: boolean
  missingRequirements: string[]
  requiredArtifacts: string[]
  statusPath: string | null
  logPath: string | null
  createdAt: string | null
  statusReason: string | null
}

export interface AgentEmergencyStopResult {
  ok: boolean
  event: string
  interruptedRuns: Array<Record<string, unknown>>
  ubuntuStop: {
    ok: boolean
    result: Record<string, unknown>
  }
  message: string
}

export interface TrustedAutoExecutionRun {
  executionRunId: number
  projectId: number
  projectName: string | null
  schedulerJobId: number | null
  accountId: number | null
  trustGrantId: number | null
  runType: string
  status: string
  goal: string
  connectorKey: string | null
  platformKey: string | null
  riskLevel: string
  inputJson: Record<string, unknown>
  outputJson: Record<string, unknown>
  artifactsJson: Record<string, unknown>
  errorMessage: string | null
  startedAt: string | null
  finishedAt: string | null
  steps: TrustedAutoExecutionStep[]
  preview: AgentActionPreview | null
}

export interface TrustedAutoMediaPipelinePreset {
  presetId: number
  projectId: number | null
  name: string
  targetPlatform: string
  ttsEngine: string
  imageProvider: string
  videoRenderer: string
  subtitleEngine: string
  promptStyle: string | null
  targetDurationSec: number
  vramFloorGb: number
  publishMode: string
  cooldownHours: number
  stepsJson: Record<string, unknown>
  isDefault: boolean
}

export interface ContentReadinessItem {
  platformKey: string
  displayName: string
  status: 'blocked' | 'metadata_only' | 'read_ready' | 'draft_ready' | 'supervised_ready'
  availableActions: string[]
  blockingReasons: string[]
  accountLabel: string | null
  connectorKey: string | null
  requiresApproval: boolean
  lastCheckedAt: string
  connected: boolean
  needsAuth: boolean
  missingGrant: boolean
  connectionId: number | null
  accountId: number | null
  contentAccountId: string | null
  grantedScopes: string[]
  linkedProjects: string[]
}

export type AccessLevel =
  | 'none'
  | 'identity_only'
  | 'metadata_only'
  | 'configured'
  | 'live_read'
  | 'live_write'
  | 'blocked'
  | 'unavailable'

export interface AccessSubjectStatus {
  subjectKey: string
  subjectType: string
  displayName: string
  accessLevel: AccessLevel
  summary: string
  reasons: string[]
  availableActions: string[]
  accountLabel: string | null
  connectorKey: string | null
  metadata: Record<string, unknown>
  lastCheckedAt: string
}

export interface AccessChange {
  subjectKey: string
  subjectType: string
  displayName: string
  fromAccessLevel: AccessLevel
  toAccessLevel: AccessLevel
  changeKind: string
  title: string
  detail: string
  userVisible: boolean
}

export interface AccessRealitySnapshot {
  snapshotKey: string
  checkedAt: string
  summary: {
    totalSubjects: number
    none: number
    identityOnly: number
    metadataOnly: number
    configured: number
    liveRead: number
    liveWrite: number
    blocked: number
    unavailable: number
  }
  subjects: AccessSubjectStatus[]
  operationalContext: Record<string, unknown>
}

export interface ContentReadinessSnapshot {
  snapshotKey: string
  checkedAt: string
  summary: {
    totalItems: number
    blocked: number
    metadataOnly: number
    readReady: number
    draftReady: number
    supervisedReady: number
    publishMode: string
    primaryYoutubeStatus: string | null
  }
  items: ContentReadinessItem[]
}

export interface TrustedAutoSummary {
  runtimeControls: {
    autonomyMode: 'blocked' | 'supervised' | 'trusted_auto'
    schedulerEnabled: boolean
    emergencyStopEnabled: boolean
    pauseAllJobs: boolean
    loginAutomationEnabled: boolean
    twoFactorAutomationEnabled: boolean
    publishAutomationEnabled: boolean
    defaultRetryBudget: number
    defaultVramFloorGb: number
  }
  accounts: TrustedAutoAccount[]
  trustGrants: TrustedAutoTrustGrant[]
  schedulerJobs: TrustedAutoSchedulerJob[]
  executionRuns: TrustedAutoExecutionRun[]
  mediaPipelinePresets: TrustedAutoMediaPipelinePreset[]
  hardwareSnapshot: TrustedAutoHardwareSnapshot | null
  browserRuntime: TrustedAutoBrowserRuntime | null
  youtubeConnector: {
    connectorKey: string
    displayName: string
    configured: boolean
    connected: boolean
    ready: boolean
    warnings: string[]
    accountId: number | null
    channelTitle: string | null
    grantedScopes: string[]
  } | null
  contentReadiness: ContentReadinessSnapshot | null
}

export interface SystemStatusSnapshot {
  agentStage: AgentStage
  agentActivity: AgentActivityState | null
  vramUsedMB: number
  vramTotalMB: number
  model: { name: string; provider: 'ollama' | 'openai' | 'anthropic' | 'gemini' }
  chroma: 'connected' | 'reconnecting' | 'down'
  network: 'online' | 'degraded' | 'offline'
  rttMs: number | null
}

import type {
  AccessRealitySnapshot,
  CapabilityFlags,
  LocalHostControlSummary,
  ProviderCard,
  RemoteWorkerSummary,
  RuntimeSummary,
  TrustedAutoSummary,
} from './system'

export interface SettingsProfile {
  userId: number
  displayName: string
  handle: string
  email: string | null
  bio: string | null
  locale: string
  timezone: string
}

export interface SettingsStyle {
  responseLength: 'short' | 'medium' | 'long'
  tone: 'professional' | 'natural' | 'direct' | 'supportive'
  structure: 'paragraphs' | 'bullets' | 'mixed'
  criticalityLevel: number
  supportivenessLevel: number
  professionalDefault: boolean
}

export interface SettingsPolicy {
  automationLevel: 'manual' | 'assisted' | 'auto'
  verificationMode: 'off' | 'smart' | 'strict'
  allowLocalFileRead: boolean
  allowLocalFileWrite: boolean
  allowWebRead: boolean
  allowExternalFallback: boolean
  allowExternalAttachmentMetadata: boolean
  allowExternalAttachmentContent: boolean
  preferLocalLlm: boolean
  defaultProviderPosture:
    | 'prefer-local'
    | 'improve-when-needed'
    | 'ask-before-external'
    | 'strict-local'
  defaultPersonaProfile: 'strategist' | 'critic' | 'executor' | 'mentor' | 'jarvis-mode'
  jarvisAddressAs: 'none' | 'first_name' | 'sir_madam'
  maxExternalAttachmentTextChars: number | null
  maxDailyCost: number | null
  maxMonthlyCost: number | null
}

export interface SettingsRuntime {
  browserControlEnabled: boolean
  stealthHeadlessEnabled: boolean
  browserAllowAnyUrl: boolean
  externalActionsEnabled: boolean
  schedulerEnabled: boolean
  oauthEnabled: boolean
  autonomyMode: 'blocked' | 'supervised' | 'trusted_auto'
  emergencyStopEnabled: boolean
  pauseAllJobs: boolean
  loginAutomationEnabled: boolean
  twoFactorAutomationEnabled: boolean
  publishAutomationEnabled: boolean
  publicUploadEnabled: boolean
  defaultRetryBudget: number
  defaultVramFloorGb: number
  voiceSttEngine: 'browser-speech' | 'whisper-local' | 'faster-whisper-local'
  voiceSttModelPath: string | null
  voiceSttDevice: string
  ambientListeningEnabled: boolean
  ambientWakeWord: string
  ambientMicDevice: string | null
  ambientMuted: boolean
  ambientContextEnabled: boolean
  trendWatchEnabled: boolean
  trendWatchQuery: string
  trendWatchRegion: string
  trendWatchIntervalMinutes: number
  trendWatchLastRunAt: string | null
  voiceRepliesEnabled: boolean
  voiceReplyVolume: number
  voiceReplyVoiceId: number | null
  ambientListeningAvailable: boolean
  ambientListeningReason: string
  lastUserInteractionAt: string | null
  lastBriefingAt: string | null
  voiceRepliesAvailable: boolean
  voiceRepliesReason: string
  localShortsEnabled: boolean
  localShortsReady: boolean
  localShortsReason: string
  localShortsRuntime: Array<{
    key: string
    label: string
    ready: boolean
    path: string | null
    reason: string
  }>
  executionEnabled: boolean
  pcOperatorMode: string
  actionBrokerMode: string
  playwrightAdapterAvailable: boolean
}

export interface SettingsObsidian {
  vaultPath: string | null
  exportEnabled: boolean
  pathExists: boolean
  source: string
  warnings: string[]
  vaultName: string
  readable: boolean
  noteCount: number
}

export interface SettingsVoice {
  voiceId: number
  providerId: number | null
  name: string
  voiceKey: string
  providerMode: 'local' | 'remote'
  style: string | null
  language: string
  speakingRate: string | null
  supportsRealtime: boolean
  autoReadResponses: boolean
  isDefault: boolean
  notes: string | null
}

export interface SettingsRole {
  roleId: number
  name: string
  slug: string
  description: string | null
  systemPrompt: string | null
  tone: string | null
  responseStyle: string | null
  criticalityLevel: number
  empathyLevel: number
  firmnessLevel: number
  scope: 'global' | 'project'
  isDefault: boolean
  isBuiltin: boolean
  isArchived: boolean
}

export interface SettingsDecisionLogEntry {
  decisionId: number
  conversationId: number | null
  projectId: number | null
  kind: 'decision' | 'commitment' | 'open_question' | 'pending_action'
  summary: string
  madeAt: string
  status: 'active' | 'resolved' | 'expired'
  dueAt: string | null
  resolvedAt: string | null
  resolutionSummary: string | null
  evidenceMessageIds: number[]
}

export interface SettingsPlugin {
  pluginId: number
  key: string
  name: string
  summary: string
  source: string
  permissionPolicy: 'auto' | 'ask' | 'disabled'
  requiresConfirmation: boolean
  isEnabled: boolean
  configJson: Record<string, unknown> | null
}

export interface SettingsProviderOption {
  providerId: number
  providerKey: string
  label: string
  kind: string
  isEnabled: boolean
}

export interface SettingsOmegaProtocol {
  profileKey: string
  mode: string
  activated: boolean
  readyForBrowserWorkflows: boolean
  readyForAccountLogin: boolean
  readyForTwoFactor: boolean
  readyForPublish: boolean
  webAdvisorFallbackEnabled: boolean
  trustedAccountsCount: number
  trustGrantsCount: number
  runtimeControls: Record<string, unknown>
  browserRuntime: {
    profileDir: string
    downloadsDir: string
    traceDir: string
    browsersPath: string
    browserExecutable: string | null
    desktopShell: string | null
    ready: boolean
    statusReason: string
  }
  trustedFilesystemRoots: string[]
  runtimeProbes: Array<{
    key: string
    label: string
    status: string
    ready: boolean
    reason: string
  }>
  allowedCapabilities: string[]
  blockedCapabilities: string[]
  recommendedNextSteps: string[]
  personaProtocol: string[]
  notePath: string | null
  noteWrittenAt: string | null
}

export interface SettingsSkillToolContract {
  toolName: string
  description: string
  schema: Record<string, unknown>
}

export interface SettingsSkillDashboardItem {
  key: string
  label: string
  category: 'creation' | 'system' | 'research' | 'dev'
  readiness: 'ready' | 'partial' | 'blueprint' | 'future'
  summary: string
  repoName: string
  repoUrl: string
  whyItMatters: string
  amdProfile: string
  nextIntegrationStep: string
  relatedModules: string[]
  blockers: string[]
  toolContracts: SettingsSkillToolContract[]
  learningNotePath: string | null
  statusReason: string
}

export interface SettingsTrendWatchSnapshot {
  snapshotId: number
  projectId: number | null
  topicQuery: string
  regionCode: string
  status: string
  sourceMix: Array<Record<string, unknown>>
  topSignals: Array<Record<string, unknown>>
  summary: {
    query: string
    regionCode: string
    topKeywords: string[]
    topHashtags: string[]
    recommendedReelTypes: string[]
    dominantSources: string[]
    signalsCount: number
    intervalMinutes: number
    collectedAt: string | null
  }
  evaluationNotes: string[]
  notePath: string | null
  failureReason: string | null
  collectedAt: string
  expiresAt: string | null
}

export interface SettingsTrendWatch {
  enabled: boolean
  query: string
  region: string
  intervalMinutes: number
  lastRunAt: string | null
  latestSnapshot: SettingsTrendWatchSnapshot | null
}

export interface SettingsCreativeAssetLane {
  laneKey: string
  displayName: string
  ready: boolean
  status: 'ready' | 'blocked' | 'degraded' | 'unknown'
  statusReason: string
  lastRecordedStatus: string
  lastRecordedReason: string
  lastRecordedAt: string | null
}

export interface SettingsCreativeAssetLaneSummary {
  lastRetestAt: string | null
  lastOutputLaneKey: string | null
  lastOutputDisplayName: string | null
  lastOutputFilename: string | null
  lastOutputStoragePath: string | null
  lastOutputAbsolutePath: string | null
  lastOutputWidth: number | null
  lastOutputHeight: number | null
  lastOutputSizeBytes: number | null
  lastOutputAt: string | null
  lastBlockerLaneKey: string | null
  lastBlockerDisplayName: string | null
  lastBlockerSummary: string | null
  lastBlockerReason: string | null
  lastBlockerScreenshotPath: string | null
  lastBlockerAt: string | null
}

export interface SettingsN8nYoutubeAutomation {
  enabled: boolean
  baseUrl: string
  mode: string
  dataDir: string
  dockerStatus: string
  dockerReady: boolean
  dockerAvailable: boolean
  dockerMessage: string
  virtualizationFirmwareEnabled: boolean | null
  fallbackRunnerStatus: string
  fallbackRunnerAvailable: boolean
  fallbackRunnerPath: string
  workflowTemplatesStatus: string
  workflowTemplatesCount: number
  workflowTemplatesDir: string
  workflowTemplateErrors: string[]
}

export interface SettingsSkillDashboard {
  summary: {
    totalSkills: number
    ready: number
    partial: number
    blueprint: number
    future: number
  }
  categories: Array<{
    key: 'creation' | 'system' | 'research' | 'dev'
    label: string
    count: number
  }>
  items: SettingsSkillDashboardItem[]
}

export interface SettingsControlCenterPayload {
  profile: SettingsProfile
  style: SettingsStyle
  policy: SettingsPolicy
  runtime: SettingsRuntime
  capabilities: CapabilityFlags
  obsidian: SettingsObsidian
  voices: SettingsVoice[]
  roles: SettingsRole[]
  plugins: SettingsPlugin[]
  providerOptions: SettingsProviderOption[]
  providerCards: ProviderCard[]
  oauthConnectors: Array<{
    connectorKey: string
    displayName: string
    status: string
    enabled: boolean
    configured: boolean
    connectionId: number | null
    connectedAt: string | null
    setupWarnings: string[]
  }>
  runtimeSummary: RuntimeSummary
  trustedAuto: TrustedAutoSummary
  accessReality: AccessRealitySnapshot | null
  omegaProtocol: SettingsOmegaProtocol
  skillsDashboard: SettingsSkillDashboard
  decisionLog: SettingsDecisionLogEntry[]
  localHost: LocalHostControlSummary
  remoteWorkers: RemoteWorkerSummary[]
  trendWatch: SettingsTrendWatch
  n8nYoutubeAutomation: SettingsN8nYoutubeAutomation | null
  creativeAssetLanes: SettingsCreativeAssetLane[]
  creativeAssetLaneSummary: SettingsCreativeAssetLaneSummary
}

import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Archive,
  AlertTriangle,
  ChevronsDown,
  ChevronsUp,
  FolderOpenDot,
  LoaderCircle,
  Pin,
  PinOff,
  PanelLeft,
  Plus,
  Save,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import {
  type ChatAttachmentPayload,
  archiveConversation,
  createChatBriefing,
  createProject,
  deleteUpload,
  deleteScheduledTask,
  deleteProject,
  delegateAiWorkerPlan,
  fetchChatDecisions,
  fetchAiWorkersStatus,
  fetchAiWorkerTasks,
  fetchBootstrap,
  fetchConversationDetail,
  fetchScheduledTasks,
  executeProjectCommand,
  improveChatPrompt,
  resolveDecisionLogEntry,
  runAiWorkerTask,
  runAiWorkerSafePlan,
  sendChatTurnStream,
  switchLocalChatModel,
  toggleScheduledTask,
  updateConversationSettings,
  updatePipelineApproval,
  updateProject,
  type AiWorkersStatusPayload,
  type DecisionLogEntryView,
  type ScheduledTaskDescriptor,
  type ProjectPersonaProfile,
  type ProjectProviderPosture,
} from '../lib/api'
import { formatDateTime, formatRelativeDateTime } from '../lib/format'
import { getAgentToolLabel, getAgentToolStateLabel } from '../lib/agentToolLabels'
import {
  CHAT_WORK_MODE_OPTIONS,
  CHAT_WORK_MODE_STORAGE_KEY,
  readChatWorkMode,
  type ChatWorkMode,
} from '../lib/chatPreferences'
import { CHAT_STATUS, humanizeChatError } from '../lib/chatStatusCopy'
import { ApprovalGate } from '../components/ApprovalGate'
import {
  ChatStream,
  type ChatActiveWorkStatus,
  type ChatQueuedMessagePreview,
} from '../components/ChatStream'
import { Kbd } from '../components/kbd'
import type { ChatInputSubmitPayload } from '../components/ChatInput'
import { ContentPipeline } from '../components/ContentPipeline'
import { ProjectCommandPanel } from '../components/ProjectCommandPanel'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { useHotkey } from '../hooks/useHotkey'
import { useChatStore } from '../stores/chatStore'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import { useUIStore } from '../stores/uiStore'
import type { ChatQuickToggleKey } from '../types/command'
import {
  bindQueuedTurnsToConversation,
  enqueueChatTurn,
  MAX_QUEUED_CHAT_TURNS,
  takeRunnableChatTurn,
  type QueuedChatTurn,
} from '../lib/chatQueue'

const DEFAULT_PROVIDER_POSTURE: ProjectProviderPosture = 'strict-local'
const DEFAULT_PERSONA_PROFILE: ProjectPersonaProfile = 'strategist'
const CHAT_SOFT_TIMEOUT_MS = 90000
const CREATIVE_VIDEO_WORKFLOW_POLL_MS = 3500
const CHAT_WORKSPACE_STORAGE_KEY = 'atnos.chat.workspace.v1'
const CHAT_CONTROLS_COLLAPSED_STORAGE_KEY = 'atnos.chat.controlsCollapsed.v2'

const PROVIDER_POSTURE_OPTIONS: Array<{
  value: ProjectProviderPosture
  label: string
  hint: string
}> = [
  {
    value: 'prefer-local',
    label: 'Preferuj lokalne',
    hint: 'Domyślnie trzymaj pracę na lokalnych modelach.',
  },
  {
    value: 'improve-when-needed',
    label: 'Poprawiaj, gdy trzeba',
    hint: 'Sięgaj po cloud tylko wtedy, gdy realnie poprawia wynik.',
  },
  {
    value: 'ask-before-external',
    label: 'Pytaj przed chmurą',
    hint: 'Zawsze proś o zgodę przed wyjściem poza lokalny runtime.',
  },
  {
    value: 'strict-local',
    label: 'Tylko lokalne',
    hint: 'Zero fallbacków cloud dla tego projektu.',
  },
]

const PERSONA_OPTIONS: Array<{
  value: ProjectPersonaProfile
  label: string
  hint: string
}> = [
  {
    value: 'strategist',
    label: 'Strateg',
    hint: 'Szeroki ogląd, priorytety i decyzje produktowe.',
  },
  {
    value: 'critic',
    label: 'Krytyk',
    hint: 'Twarda weryfikacja jakości i słabych punktów.',
  },
  {
    value: 'executor',
    label: 'Wykonawca',
    hint: 'Skupienie na dowożeniu i konkretnych akcjach.',
  },
  {
    value: 'mentor',
    label: 'Mentor',
    hint: 'Wyjaśnianie i prowadzenie krok po kroku.',
  },
]

interface ProjectFormState {
  name: string
  description: string
  personaProfile: ProjectPersonaProfile
  providerPosture: ProjectProviderPosture
}

type ConversationQualityProfile = 'fast-turnaround' | 'standard' | 'premium'
type ConversationResponseLength = 'short' | 'medium' | 'long'
type ConversationResponseTone = 'direct' | 'professional' | 'supportive' | 'critical'
type DelegationPrivacyMode = 'strict_local' | 'allow_browser_ai'

interface ConversationQuickSettings {
  responseLength: ConversationResponseLength
  responseTone: ConversationResponseTone
  memoryEnabled: boolean
  fileEnabled: boolean
  internetEnabled: boolean
  deepAnalysisEnabled: boolean
}

const DELEGATION_COMMANDS = new Set(['/atnos', '/delegate', '/deleguj'])
const GENERAL_CHAT_COMMAND = '/ask'

// Rollback switch for the incremental Project Command migration.
const PROJECT_COMMAND_ENABLED = import.meta.env.VITE_PROJECT_COMMAND_ENABLED !== 'false'

function isProjectCommand(text: string, projectId: number | null, attachmentCount: number): boolean {
  const trimmed = text.trim()
  const [command] = trimmed.split(/\s+/, 1)
  return (
    PROJECT_COMMAND_ENABLED &&
    projectId != null &&
    attachmentCount === 0 &&
    trimmed.length > 0 &&
    !DELEGATION_COMMANDS.has(command.toLowerCase()) &&
    command.toLowerCase() !== GENERAL_CHAT_COMMAND &&
    // Natural-language input in an active project is Project Command by default.
    // Slash commands keep their explicit routing semantics.
    !command.startsWith('/')
  )
}

function parseGeneralChatPrompt(text: string): string | null {
  const trimmed = text.trim()
  if (!trimmed.toLowerCase().startsWith(GENERAL_CHAT_COMMAND)) {
    return null
  }

  return trimmed.slice(GENERAL_CHAT_COMMAND.length).trim()
}

function normalizeCommandText(text: string) {
  return text.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
}

function parseProductionOrderPrompt(text: string): { topic: string; direction: string; optionalNotes: string } | null {
  const trimmed = text.trim()
  if (!trimmed || trimmed.startsWith('/') || /^jak\s+/i.test(normalizeCommandText(trimmed))) {
    return null
  }
  const normalized = normalizeCommandText(trimmed)
  const match = normalized.match(
    /^(zrob|zrobic|stworz|utworz|wygeneruj|przygotuj|nagraj|zlec|make|create)\s+(mi\s+)?((jedna|1)\s+)?((testowa|probna)\s+)?(rolke|rolka|shorta|short|reel|filmik)\b/,
  )
  if (!match) return null
  const rest = trimmed.slice(match[0].length).trim().replace(/^(o|na temat|about)\s+/i, '').trim()
  if (rest.length < 3) return null
  return {
    topic: rest.slice(0, 300),
    direction: trimmed.slice(0, 2000),
    optionalNotes: 'Utworzone z naturalnej komendy w czacie.',
  }
}

function parseDelegationPrompt(text: string, delegationModeEnabled: boolean): string | null {
  const trimmed = text.trim()
  if (!trimmed) {
    return null
  }

  const [firstToken, ...rest] = trimmed.split(/\s+/)
  const command = firstToken.toLowerCase()
  if (DELEGATION_COMMANDS.has(command)) {
    return rest.join(' ').trim()
  }

  return delegationModeEnabled ? trimmed : null
}

function inferDelegationTaskType(prompt: string): string {
  const normalized = prompt.toLowerCase()
  if (/(short|reel|youtube|thumbnail|miniatur|script|content|film|video)/.test(normalized)) {
    return 'content_operator_command'
  }
  if (/(xtb|portfolio|inwest|trading|market|paper|broker)/.test(normalized)) {
    return 'investment_operator_command'
  }
  if (/(runtime|repo|test|bug|interface|interfejs|aplikac|frontend|backend|kod|code)/.test(normalized)) {
    return 'engineering_runtime_delegation'
  }
  if (/(research|szukaj|sprawd|analiz|kurs|narz[eę]dzi)/.test(normalized)) {
    return 'research_delegation'
  }
  return 'operator_command'
}

function formatDelegationMessage(task: {
  id: number
  status: string
  taskType: string
  workerKey: string | null
  privacyMode: string
  resultSummary: string | null
  blockedReason: string | null
  errorMessage: string | null
  children: Array<{
    id: number
    taskType: string
    status: string
    workerKey: string | null
    taskRole: string
    sequenceIndex: number
    blockedReason: string | null
    resultSummary: string | null
  }>
}) {
  const lines = [
    `PLAN DELEGACJI #${task.id}`,
    '',
    `Status: ${task.status}`,
    `Typ: ${task.taskType}`,
    `Worker: ${task.workerKey ?? 'planner'}`,
    `Prywatnosc: ${task.privacyMode}`,
  ]

  const summary = task.resultSummary ?? task.blockedReason ?? task.errorMessage
  if (summary) {
    lines.push('', `Wynik: ${summary}`)
  }

  if (task.children.length > 0) {
    lines.push('', 'Podzadania:')
    task.children
      .slice()
      .sort((left, right) => left.sequenceIndex - right.sequenceIndex)
      .forEach((child) => {
        lines.push(
          `- #${child.id} [${child.status}] ${child.taskRole || child.taskType} -> ${child.workerKey ?? 'auto'}`,
        )
      })
  }

  lines.push('', 'Bezpieczenstwo: OAuth, publikacja, zakupy, hasla, karta i live trading pozostaja zablokowane przez guardrails.')
  if (task.status === 'awaiting_review') {
    lines.push('Wykonanie zatrzymano przed krokiem wymagajacym review lub workera browser.')
  }

  return lines.join('\n')
}

function formatAiWorkerTaskList(
  tasks: Array<{
    id: number
    taskType: string
    status: string
    workerKey: string | null
    privacyMode: string
    resultSummary: string | null
    blockedReason: string | null
    errorMessage: string | null
    children: Array<{
      id: number
      taskType: string
      status: string
      workerKey: string | null
      taskRole: string
      sequenceIndex: number
    }>
  }>,
) {
  if (tasks.length === 0) {
    return 'AI WORKERS\n\nBrak zapisanych zadan w kolejce.'
  }

  const lines = ['AI WORKERS - OSTATNIE ZADANIA', '']
  tasks.slice(0, 8).forEach((task) => {
    const note = task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? ''
    lines.push(
      `- #${task.id} [${task.status}] ${task.taskType} -> ${task.workerKey ?? 'planner'} (${task.privacyMode})`,
    )
    if (note) {
      lines.push(`  ${note.slice(0, 180)}`)
    }
    if (task.children.length > 0) {
      lines.push(
        `  podzadania: ${task.children
          .slice()
          .sort((left, right) => left.sequenceIndex - right.sequenceIndex)
          .map((child) => `#${child.id}:${child.status}`)
          .join(', ')}`,
      )
    }
  })

  lines.push('', 'Nowe plany uruchamiaja bezpieczne lokalne kroki automatycznie. Szczegoly: /ai-tasks')
  return lines.join('\n')
}

function formatAiWorkerTaskResult(task: {
  id: number
  taskType: string
  status: string
  workerKey: string | null
  privacyMode: string
  resultSummary: string | null
  blockedReason: string | null
  errorMessage: string | null
  verificationStatus: string
}) {
  return [
    `AI WORKER TASK #${task.id}`,
    '',
    `Status: ${task.status}`,
    `Typ: ${task.taskType}`,
    `Worker: ${task.workerKey ?? 'auto'}`,
    `Prywatnosc: ${task.privacyMode}`,
    `Weryfikacja: ${task.verificationStatus}`,
    '',
    task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? 'Task zaktualizowany bez dodatkowego opisu.',
  ].join('\n')
}

function formatWorkerStateLabel(status: string, ready: boolean) {
  if (ready) return 'gotowy'
  if (status === 'blocked_by_policy') return 'zablokowany'
  if (status === 'runtime_unavailable') return 'brak runtime'
  if (status === 'missing') return 'brak'
  if (!status) return 'nieznany'
  return status.replace(/_/g, ' ')
}

interface PersistedChatWorkspaceState {
  draftsByConversationKey: Record<string, string>
  pinnedConversationIds: number[]
  quickSettingsByConversationKey: Record<string, ConversationQuickSettings>
}

const QUALITY_PROFILE_OPTIONS: Array<{
  value: ConversationQualityProfile
  label: string
  hint: string
}> = [
  {
    value: 'fast-turnaround',
    label: 'Szybciej',
    hint: 'Lzejszy tor wykonania i szybszy dowoz, gdy jakosc nie musi byc maksymalna.',
  },
  {
    value: 'standard',
    label: 'Standard',
    hint: 'Domyslny balans jakosci, kosztu i czasu wykonania.',
  },
  {
    value: 'premium',
    label: 'Premium',
    hint: 'Mocniejszy tor dla grafiki, wideo i innych dopracowanych outputow.',
  },
]

const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  'ollama-local': 'Ollama',
  ollama: 'Ollama',
  'openai-cloud': 'OpenAI',
  openai: 'OpenAI',
  'anthropic-cloud': 'Claude',
  anthropic: 'Claude',
  'gemini-cloud': 'Gemini',
  gemini: 'Gemini',
  'ubuntu-sandbox-agent': 'Ubuntu sandbox',
  'local-browser-operator': 'Browser operator',
  'external-browser-operator': 'Browser AI bridge',
  'local-capability': 'Local tools',
}

const PROVIDER_POSTURE_LABELS: Record<ProjectProviderPosture, string> = {
  'prefer-local': 'Prefer local',
  'improve-when-needed': 'Allow cloud',
  'ask-before-external': 'Ask before cloud',
  'strict-local': 'Strict local',
}

function readMetadataString(metadata: Record<string, unknown> | null | undefined, key: string) {
  const value = metadata?.[key]
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function formatProviderName(providerKey: string | null | undefined) {
  if (!providerKey) {
    return 'Nie wybrano'
  }
  return PROVIDER_DISPLAY_NAMES[providerKey] ?? providerKey
}

function createEmptyProjectForm(): ProjectFormState {
  return {
    name: '',
    description: '',
    personaProfile: DEFAULT_PERSONA_PROFILE,
    providerPosture: DEFAULT_PROVIDER_POSTURE,
  }
}

function readPersistedChatWorkspaceState(): PersistedChatWorkspaceState {
  if (typeof window === 'undefined') {
    return {
      draftsByConversationKey: {},
      pinnedConversationIds: [],
      quickSettingsByConversationKey: {},
    }
  }

  try {
    const raw = window.localStorage.getItem(CHAT_WORKSPACE_STORAGE_KEY)
    if (!raw) {
      return {
        draftsByConversationKey: {},
        pinnedConversationIds: [],
        quickSettingsByConversationKey: {},
      }
    }

    const parsed = JSON.parse(raw) as Partial<PersistedChatWorkspaceState>
    return {
      draftsByConversationKey:
        parsed.draftsByConversationKey && typeof parsed.draftsByConversationKey === 'object'
          ? (parsed.draftsByConversationKey as Record<string, string>)
          : {},
      pinnedConversationIds: Array.isArray(parsed.pinnedConversationIds)
        ? parsed.pinnedConversationIds
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value))
        : [],
      quickSettingsByConversationKey:
        parsed.quickSettingsByConversationKey && typeof parsed.quickSettingsByConversationKey === 'object'
          ? (parsed.quickSettingsByConversationKey as Record<string, ConversationQuickSettings>)
          : {},
    }
  } catch {
    return {
      draftsByConversationKey: {},
      pinnedConversationIds: [],
      quickSettingsByConversationKey: {},
    }
  }
}

function writePersistedChatWorkspaceState(payload: PersistedChatWorkspaceState) {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.setItem(CHAT_WORKSPACE_STORAGE_KEY, JSON.stringify(payload))
}

function readChatControlsCollapsedPreference(): boolean {
  if (typeof window === 'undefined') {
    return true
  }
  return window.localStorage.getItem(CHAT_CONTROLS_COLLAPSED_STORAGE_KEY) !== 'false'
}

function formatProjectStatus(status: string) {
  switch (status) {
    case 'active':
      return 'Aktywny'
    case 'archived':
      return 'Archiwalny'
    case 'on_hold':
      return 'Wstrzymany'
    default:
      return status
  }
}

function normalizeProjectPayload(form: ProjectFormState) {
  return {
    name: form.name.trim(),
    description: form.description.trim() || null,
    personaProfile: form.personaProfile,
    providerPosture: form.providerPosture,
  }
}

function inferResponseLength(responseStyle: string | null | undefined): ConversationResponseLength {
  if (responseStyle === 'concise-direct') return 'short'
  if (responseStyle === 'critical-deep') return 'long'
  return 'medium'
}

function inferResponseTone(responseStyle: string | null | undefined): ConversationResponseTone {
  if (responseStyle === 'supportive-guide') return 'supportive'
  if (responseStyle === 'critical-deep') return 'critical'
  if (responseStyle === 'professional-clear') return 'professional'
  return 'direct'
}

function buildResponseStyleOverride(
  length: ConversationResponseLength,
  tone: ConversationResponseTone,
): 'concise-direct' | 'professional-clear' | 'supportive-guide' | 'critical-deep' {
  if (tone === 'supportive') return 'supportive-guide'
  if (tone === 'critical' || length === 'long') return 'critical-deep'
  if (tone === 'professional' || length === 'medium') return 'professional-clear'
  return 'concise-direct'
}

function inferQuickSettings(
  settings:
    | {
        activeWorkMode?: string
        responseStyle?: string | null
        memoryEnabled?: boolean
        fileEnabled?: boolean
        internetEnabled?: boolean
      }
    | null
    | undefined,
): ConversationQuickSettings {
  return {
    responseLength: inferResponseLength(settings?.responseStyle),
    responseTone: inferResponseTone(settings?.responseStyle),
    memoryEnabled: settings?.memoryEnabled ?? true,
    fileEnabled: settings?.fileEnabled ?? true,
    internetEnabled: settings?.internetEnabled ?? true,
    deepAnalysisEnabled: settings?.activeWorkMode === 'deep',
  }
}

function resolveConversationTitle(title: string, summary: string | null) {
  const normalizedTitle = title.trim()
  if (/^nowa rozmowa$/i.test(normalizedTitle) && summary?.trim()) {
    return summary.trim()
  }
  return normalizedTitle || 'Nowa rozmowa'
}

function buildConversationSearchHaystack(
  title: string,
  summary: string | null,
  projectName: string | null,
) {
  return [title, summary ?? '', projectName ?? ''].join(' ').toLowerCase()
}

type CreativeVideoWorkflowSnapshot = {
  workflowStatus: string | null
  blocked: boolean
  approvalRequired: boolean
  currentNode: string | null
  platformHint: string | null
}

function extractCreativeVideoWorkflowSnapshot(
  metadata: Record<string, unknown> | null | undefined,
): CreativeVideoWorkflowSnapshot | null {
  if (!metadata || typeof metadata !== 'object') {
    return null
  }
  const operator = (metadata as { creative_video_operator?: unknown }).creative_video_operator
  if (!operator || typeof operator !== 'object') {
    return null
  }
  const workflowState = (operator as { workflow_state?: unknown }).workflow_state
  const workflowStatus =
    workflowState &&
    typeof workflowState === 'object' &&
    typeof (workflowState as { status?: unknown }).status === 'string'
      ? String((workflowState as { status?: unknown }).status)
      : null
  return {
    workflowStatus,
    blocked: Boolean((operator as { blocked?: unknown }).blocked),
    approvalRequired: Boolean((operator as { approval_required?: unknown }).approval_required),
    currentNode:
      workflowState &&
      typeof workflowState === 'object' &&
      typeof (workflowState as { current_node?: unknown }).current_node === 'string'
        ? String((workflowState as { current_node?: unknown }).current_node)
        : null,
    platformHint:
      typeof (operator as { platform_hint?: unknown }).platform_hint === 'string'
        ? String((operator as { platform_hint?: unknown }).platform_hint)
        : null,
  }
}

function compareConversationMessageOrder(
  left: { sequenceNumber?: number; createdAt?: string },
  right: { sequenceNumber?: number; createdAt?: string },
): number {
  if (
    left.sequenceNumber != null &&
    right.sequenceNumber != null &&
    left.sequenceNumber !== right.sequenceNumber
  ) {
    return left.sequenceNumber - right.sequenceNumber
  }

  const leftCreatedAt = Date.parse(left.createdAt ?? '')
  const rightCreatedAt = Date.parse(right.createdAt ?? '')
  if (
    Number.isFinite(leftCreatedAt) &&
    Number.isFinite(rightCreatedAt) &&
    leftCreatedAt !== rightCreatedAt
  ) {
    return leftCreatedAt - rightCreatedAt
  }

  return 0
}

function getLatestCreativeVideoWorkflowSnapshot(
  messages: Array<{
    sequenceNumber?: number
    createdAt?: string
    metadataJson?: Record<string, unknown> | null
  }>,
): CreativeVideoWorkflowSnapshot | null {
  let latestMessage: (typeof messages)[number] | null = null
  let latestSnapshot: CreativeVideoWorkflowSnapshot | null = null

  for (const message of messages) {
    const snapshot = extractCreativeVideoWorkflowSnapshot(message.metadataJson)
    if (!snapshot) {
      continue
    }
    if (latestMessage == null || compareConversationMessageOrder(latestMessage, message) <= 0) {
      latestMessage = message
      latestSnapshot = snapshot
    }
  }

  return latestSnapshot
}

function isCreativeVideoWorkflowPollingState(
  snapshot: CreativeVideoWorkflowSnapshot | null,
): boolean {
  if (!snapshot || snapshot.blocked) {
    return false
  }

  return (
    snapshot.workflowStatus === 'queued' ||
    snapshot.workflowStatus === 'running' ||
    snapshot.workflowStatus === 'retrying' ||
    snapshot.workflowStatus === 'planned'
  )
}

function formatCreativeVideoWorkflowStatusLabel(
  workflowStatus: string | null | undefined,
): string {
  if (workflowStatus === 'queued' || workflowStatus === 'planned') {
    return 'Zaplanowane'
  }
  if (workflowStatus === 'running' || workflowStatus === 'retrying') {
    return 'W toku'
  }
  if (workflowStatus === 'awaiting_human') {
    return 'Review'
  }
  if (workflowStatus === 'done' || workflowStatus === 'completed') {
    return 'Gotowe'
  }
  if (workflowStatus === 'manual_upload_ready' || workflowStatus === 'local_render_complete') {
    return 'Gotowe lokalnie'
  }
  return 'Workflow'
}

const CREATIVE_VIDEO_NODE_LABELS: Record<string, string> = {
  approval_gate: 'Czeka na review',
  hardware_guard: 'Sprawdza zasoby PC',
  local_delivery: 'Pakuje finalne pliki',
  memory_update: 'Zapisuje raport i pamiec',
  runtime_probe: 'Sprawdza Piper, whisper.cpp i FFmpeg',
  scheduler_dispatch: 'Uruchamia workflow',
  research: 'Uklada brief i research',
  script_generation: 'Pisze skrypt',
  script_quality_gate: 'Sprawdza jakosc skryptu',
  script_repair: 'Naprawia skrypt',
  tts: 'Generuje lektora',
  voice: 'Generuje lektora',
  subtitles: 'Tworzy napisy',
  thumbnail: 'Tworzy miniaturke',
  render: 'Renderuje MP4',
  render_qa: 'Sprawdza finalny film',
  qa: 'Sprawdza finalny film',
}

function formatCreativeVideoWorkflowNodeLabel(currentNode: string | null | undefined): string | null {
  if (!currentNode) {
    return null
  }
  const compact = currentNode.split('.').at(-1) ?? currentNode
  return CREATIVE_VIDEO_NODE_LABELS[compact] ?? compact.replace(/_/g, ' ')
}

function formatCreativeVideoWorkflowPlatformLabel(
  platformHint: string | null | undefined,
): string | null {
  if (!platformHint) {
    return null
  }
  if (platformHint === 'youtube-short') {
    return 'YouTube Short'
  }
  return platformHint.replace(/_/g, ' ')
}

function ProjectFieldShell(props: {
  label: string
  description?: string
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="label-eyebrow">{props.label}</span>
      {props.description ? (
        <div className="mt-1 text-xs leading-5 text-text-tertiary">{props.description}</div>
      ) : null}
      <div className="mt-2">{props.children}</div>
    </label>
  )
}

export default function ChatRoute() {
  const navigate = useNavigate()
  const {
    projects,
    conversations,
    selectedProjectId,
    selectedConversationId,
    selectedConversationTitle,
    hydratedConversationId,
    messages,
    searchQuery,
    settings,
    activeTurn,
    queuedTurns,
    loadingConversation,
    setSelectedProjectId,
    setSelectedConversation,
    setConversationDetail,
    setConversationSettings,
    setLoadingConversation,
    setSearchQuery,
    setBootstrap,
    startActiveTurn,
    finishActiveTurn,
    setQueuedTurns,
    appendOptimisticUserMessage,
    setOptimisticMessageState,
    replaceAfterTurn,
    appendMessage,
    upsertStreamingAssistant,
    appendStreamingAssistantToken,
    resetConversationWorkspace,
    upsertConversation,
    removeConversation,
  } = useChatStore()
  const pipeline = usePipelineStore((state) => state.snapshot)
  const setPipelineSnapshot = usePipelineStore((state) => state.setSnapshot)
  const currentJob = usePipelineStore((state) => state.currentJob)
  const setAwaitingApproval = usePipelineStore((state) => state.setAwaitingApproval)
  const agentStage = useSystemStore((state) => state.agentStage)
  const agentActivity = useSystemStore((state) => state.agentActivity)
  const setStage = useSystemStore((state) => state.setStage)
  const setAgentActivity = useSystemStore((state) => state.setAgentActivity)
  const remoteWorkers = useSystemStore((state) => state.remoteWorkers)
  const remoteExecutionRuns = useSystemStore((state) => state.remoteExecutionRuns)
  const activeModel = useSystemStore((state) => state.model)
  const backendReachable = useSystemStore((state) => state.networkSignals.backendReachable)
  const backendCheckedAt = useSystemStore((state) => state.backendCheckedAt)
  const websocketStatus = useSystemStore((state) => state.websocketStatus)
  const pushToast = useToastStore((state) => state.push)
  const briefingNonce = useUIStore((state) => state.briefingNonce)
  const decisionsNonce = useUIStore((state) => state.decisionsNonce)
  const composerFocusNonce = useUIStore((state) => state.composerFocusNonce)
  const setChatCommandSurface = useUIStore((state) => state.setChatCommandSurface)
  const { isDesktopUp, isWide } = useBreakpoint()

  const [projectComposerOpen, setProjectComposerOpen] = useState(false)
  const [creatingProject, setCreatingProject] = useState(false)
  const [savingProject, setSavingProject] = useState(false)
  const [deletingProject, setDeletingProject] = useState(false)
  const [deleteArmed, setDeleteArmed] = useState(false)
  const [newProject, setNewProject] = useState<ProjectFormState>(createEmptyProjectForm)
  const [projectDraft, setProjectDraft] = useState<ProjectFormState>(createEmptyProjectForm)
  const [scheduledTasks, setScheduledTasks] = useState<ScheduledTaskDescriptor[]>([])
  const [scheduledTasksOpen, setScheduledTasksOpen] = useState(false)
  const [scheduledTasksLoading, setScheduledTasksLoading] = useState(false)
  const [qualityProfilesByConversation, setQualityProfilesByConversation] = useState<
    Record<string, ConversationQualityProfile>
  >({})
  const persistedWorkspaceState = useMemo(() => readPersistedChatWorkspaceState(), [])
  const [draftsByConversationKey, setDraftsByConversationKey] = useState<Record<string, string>>(
    persistedWorkspaceState.draftsByConversationKey,
  )
  const [pinnedConversationIds, setPinnedConversationIds] = useState<number[]>(
    persistedWorkspaceState.pinnedConversationIds,
  )
  const [quickSettingsByConversationKey, setQuickSettingsByConversationKey] = useState<
    Record<string, ConversationQuickSettings>
  >(persistedWorkspaceState.quickSettingsByConversationKey)
  const [chatControlsCollapsed, setChatControlsCollapsed] = useState(
    readChatControlsCollapsedPreference,
  )
  const [workspaceDetailsOpen, setWorkspaceDetailsOpen] = useState(false)
  const [mobileConversationPanelOpen, setMobileConversationPanelOpen] = useState(false)
  const [chatWorkMode, setChatWorkMode] = useState<ChatWorkMode>(readChatWorkMode)
  const delegationModeEnabled = chatWorkMode === 'delegation'
  const [delegationPrivacyMode, setDelegationPrivacyMode] =
    useState<DelegationPrivacyMode>('strict_local')
  const [switchingLocalModel, setSwitchingLocalModel] = useState(false)
  const [queueDispatching, setQueueDispatching] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    window.localStorage.setItem(CHAT_WORK_MODE_STORAGE_KEY, chatWorkMode)
  }, [chatWorkMode])

  const [aiWorkersStatus, setAiWorkersStatus] = useState<AiWorkersStatusPayload | null>(null)
  const [aiWorkersStatusError, setAiWorkersStatusError] = useState<string | null>(null)
  const [aiWorkersStatusLoading, setAiWorkersStatusLoading] = useState(false)
  const [projectCommandRefreshKey, setProjectCommandRefreshKey] = useState(0)
  const conversationSearchInputRef = useRef<HTMLInputElement | null>(null)
  const creativeVideoWorkflowRefreshInFlightRef = useRef(false)
  const pendingTurnControllersRef = useRef(new Map<string, AbortController>())
  const pendingTurnTimeoutsRef = useRef(
    new Map<string, { softTimeoutId?: number; hardTimeoutId?: number }>(),
  )

  const chatSurfaceMaxWidth = isWide ? 'shell-content-width-wide' : 'shell-content-width'
  const deferredSearchQuery = useDeferredValue(searchQuery)

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  )
  const projectNameById = useMemo(
    () => new Map(projects.map((project) => [project.id, project.name])),
    [projects],
  )
  const queuedMessagePreviews = useMemo<ChatQueuedMessagePreview[]>(
    () =>
      queuedTurns.map((turn) => ({
        id: turn.id,
        text:
          turn.text.trim() ||
          `Załączniki: ${turn.attachments.map((attachment) => attachment.filename).join(', ')}`,
        targetLabel:
          turn.targetConversationId != null
            ? `Rozmowa: ${conversations.find((item) => item.id === turn.targetConversationId)?.title ?? turn.targetConversationId}`
            : turn.targetProjectId != null
              ? `Projekt: ${projectNameById.get(turn.targetProjectId) ?? turn.targetProjectId}`
              : 'Nowa rozmowa',
      })),
    [conversations, projectNameById, queuedTurns],
  )
  const selectedConversationProjectId = useMemo(
    () =>
      conversations.find((conversation) => conversation.id === selectedConversationId)
        ?.projectId ?? null,
    [conversations, selectedConversationId],
  )
  const selectedConversationProject = useMemo(
    () =>
      selectedConversationProjectId == null
        ? null
        : projects.find((project) => project.id === selectedConversationProjectId) ?? null,
    [projects, selectedConversationProjectId],
  )
  const activeProjectContext = selectedProject ?? selectedConversationProject
  const activeProjectContextLabel = activeProjectContext?.name ?? 'Bez projektu'
  const activeProjectContextAccent = activeProjectContext != null

  const selectedProjectConversationCount = useMemo(() => {
    if (!selectedProject) return 0
    return conversations.filter((conversation) => conversation.projectId === selectedProject.id).length
  }, [conversations, selectedProject])

  const qualityProfileKey = selectedConversationId == null ? '__draft__' : String(selectedConversationId)
  const conversationWorkspaceKey = selectedConversationId == null ? '__draft__' : String(selectedConversationId)
  const hydratedQualityProfile =
    selectedConversationId != null && hydratedConversationId === selectedConversationId
      ? settings?.qualityProfile ?? 'fast-turnaround'
      : 'fast-turnaround'
  const activeQualityProfile =
    qualityProfilesByConversation[qualityProfileKey] ?? hydratedQualityProfile
  const activeQualityProfileHint =
    QUALITY_PROFILE_OPTIONS.find((option) => option.value === activeQualityProfile)?.hint ??
    QUALITY_PROFILE_OPTIONS[1].hint
  const inferredQuickSettings = useMemo(() => inferQuickSettings(settings), [settings])
  const activeQuickSettings =
    quickSettingsByConversationKey[conversationWorkspaceKey] ?? inferredQuickSettings
  const activeDraft = draftsByConversationKey[conversationWorkspaceKey] ?? ''
  const activePersonaLabel =
    PERSONA_OPTIONS.find(
      (option) =>
        option.value ===
        ((settings?.personaProfile as ProjectPersonaProfile | undefined) ??
          activeProjectContext?.personaProfile ??
          DEFAULT_PERSONA_PROFILE),
    )?.label ?? 'Strateg'
  const aiWorkers = aiWorkersStatus?.workers ?? []
  const readyAiWorkerCount = aiWorkers.filter((worker) => worker.ready).length
  const ollamaWorker = aiWorkers.find((worker) => worker.workerKey === 'ollama') ?? null
  const ollamaInstalledModels = useMemo(() => {
    const raw = ollamaWorker?.metadata?.models
    if (!Array.isArray(raw)) {
      return []
    }
    return raw.map((item) => String(item).trim()).filter((item) => item.length > 0)
  }, [ollamaWorker?.metadata?.models])
  const browserAiWorkers = aiWorkers.filter((worker) => worker.workerKey.startsWith('browser_'))
  const readyBrowserAiWorkers = browserAiWorkers.filter((worker) => worker.ready)
  const readyBrowserWorkerKeys = useMemo(
    () => readyBrowserAiWorkers.map((worker) => worker.workerKey),
    [readyBrowserAiWorkers],
  )
  const browserAiAvailable = readyBrowserWorkerKeys.length > 0
  const browserAiBlockReason =
    browserAiWorkers.find((worker) => !worker.ready)?.reason ??
    (aiWorkersStatusError
      ? aiWorkersStatusError
      : aiWorkersStatusLoading
        ? 'Sprawdzam /api/ai-workers/status.'
        : 'Backend nie potwierdzil gotowego workera browser AI.')
  const effectiveDelegationPrivacyMode: DelegationPrivacyMode =
    delegationPrivacyMode === 'allow_browser_ai' && browserAiAvailable
      ? 'allow_browser_ai'
      : 'strict_local'
  const effectiveAllowedDelegationWorkers =
    effectiveDelegationPrivacyMode === 'allow_browser_ai'
      ? ['ollama', ...readyBrowserWorkerKeys]
      : ['ollama']
  const activeCreativeVideoWorkflowSnapshot = useMemo(
    () => getLatestCreativeVideoWorkflowSnapshot(messages),
    [messages],
  )
  const latestAssistantRouting = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (message.role !== 'assistant') {
        continue
      }
      const providerKey = readMetadataString(message.metadataJson, 'selected_provider_key')
      const modelKey = readMetadataString(message.metadataJson, 'selected_model_key')
      if (providerKey || modelKey) {
        return {
          providerKey,
          modelKey,
          runtimeModelName: readMetadataString(message.metadataJson, 'actual_runtime_model_name'),
          routingReason: readMetadataString(message.metadataJson, 'routing_reason'),
          taskType: readMetadataString(message.metadataJson, 'task_type'),
          providerPosture: readMetadataString(message.metadataJson, 'provider_posture'),
        }
      }
    }
    return null
  }, [messages])
  const activeProviderPosture =
    (settings?.providerPosture ??
      activeProjectContext?.providerPosture ??
      DEFAULT_PROVIDER_POSTURE) as ProjectProviderPosture
  const activeProviderLabel =
    latestAssistantRouting?.providerKey != null
      ? formatProviderName(latestAssistantRouting.providerKey)
      : formatProviderName(activeModel.provider)
  const activeModelLabel = latestAssistantRouting?.runtimeModelName ?? latestAssistantRouting?.modelKey ?? activeModel.name
  const activePrivacyLabel = PROVIDER_POSTURE_LABELS[activeProviderPosture] ?? activeProviderPosture
  const strictLocalCloudBlocked = activeProviderPosture === 'strict-local'
  const shouldPollActiveCreativeVideoWorkflow =
    selectedConversationId != null &&
    hydratedConversationId === selectedConversationId &&
    isCreativeVideoWorkflowPollingState(activeCreativeVideoWorkflowSnapshot)
  const activeCreativeVideoWorkflowStatusLabel = formatCreativeVideoWorkflowStatusLabel(
    activeCreativeVideoWorkflowSnapshot?.workflowStatus,
  )
  const activeCreativeVideoWorkflowNodeLabel = formatCreativeVideoWorkflowNodeLabel(
    activeCreativeVideoWorkflowSnapshot?.currentNode,
  )
  const activeCreativeVideoWorkflowPlatformLabel = formatCreativeVideoWorkflowPlatformLabel(
    activeCreativeVideoWorkflowSnapshot?.platformHint,
  )
  const activeCreativeVideoWorkStatus: ChatActiveWorkStatus | null =
    shouldPollActiveCreativeVideoWorkflow
      ? {
          title: 'ATNOS pracuje nad shortem',
          detail: activeCreativeVideoWorkflowNodeLabel
            ? `Aktualny etap: ${activeCreativeVideoWorkflowNodeLabel}. Status odswieza sie automatycznie co kilka sekund.`
            : 'Workflow dziala lokalnie w tle. Status rozmowy odswieza sie automatycznie co kilka sekund.',
          statusLabel: activeCreativeVideoWorkflowStatusLabel,
          stepLabel: activeCreativeVideoWorkflowNodeLabel,
        }
      : null

  const applyConversationDetail = useCallback(
    (detail: Awaited<ReturnType<typeof fetchConversationDetail>>) => {
      setConversationDetail({
        conversationId: detail.conversation.id,
        title: detail.conversation.title,
        messages: detail.messages,
        settings: detail.settings,
        memoryCards: detail.memoryCards,
      })
      upsertConversation({
        id: detail.conversation.id,
        title: detail.conversation.title,
        projectId: detail.conversation.projectId,
        summary: detail.conversation.summary,
        lastMessageAt: detail.conversation.lastMessageAt,
        updatedAt: detail.conversation.updatedAt,
        archived: false,
        matchedSource: null,
      })
      return detail
    },
    [setConversationDetail, upsertConversation],
  )

  const refreshConversationDetail = useCallback(
    async (conversationId: number) => {
      const detail = await fetchConversationDetail(conversationId)
      applyConversationDetail(detail)
    },
    [applyConversationDetail],
  )

  useEffect(() => {
    writePersistedChatWorkspaceState({
      draftsByConversationKey,
      pinnedConversationIds,
      quickSettingsByConversationKey,
    })
  }, [draftsByConversationKey, pinnedConversationIds, quickSettingsByConversationKey])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    window.localStorage.setItem(
      CHAT_CONTROLS_COLLAPSED_STORAGE_KEY,
      chatControlsCollapsed ? 'true' : 'false',
    )
  }, [chatControlsCollapsed])

  useEffect(() => {
    let active = true
    setAiWorkersStatusLoading(true)
    setAiWorkersStatusError(null)
    fetchAiWorkersStatus()
      .then((payload) => {
        if (!active) return
        setAiWorkersStatus(payload)
      })
      .catch((error: Error) => {
        if (!active) return
        setAiWorkersStatus(null)
        setAiWorkersStatusError(error.message)
      })
      .finally(() => {
        if (active) setAiWorkersStatusLoading(false)
      })

    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (delegationPrivacyMode === 'allow_browser_ai' && aiWorkersStatus != null && !browserAiAvailable) {
      setDelegationPrivacyMode('strict_local')
    }
  }, [aiWorkersStatus, browserAiAvailable, delegationPrivacyMode])

  useEffect(() => {
    if (!selectedConversationId || hydratedConversationId === selectedConversationId) {
      setLoadingConversation(false)
      return
    }
    let active = true

    setLoadingConversation(true)
    fetchConversationDetail(selectedConversationId)
      .then((payload) => {
        if (!active) return
        applyConversationDetail(payload)
        setLoadingConversation(false)
      })
      .catch((error: Error) => {
        if (!active) return
        pushToast({
          type: 'error',
          title: 'Nie udało się wczytać rozmowy',
          message: error.message,
        })
        setLoadingConversation(false)
      })

    return () => {
      active = false
      setLoadingConversation(false)
    }
  }, [
    applyConversationDetail,
    hydratedConversationId,
    pushToast,
    selectedConversationId,
    setLoadingConversation,
  ])

  useEffect(() => {
    if (!shouldPollActiveCreativeVideoWorkflow || selectedConversationId == null) {
      return
    }

    let active = true
    const intervalId = window.setInterval(() => {
      if (!active || creativeVideoWorkflowRefreshInFlightRef.current) {
        return
      }

      creativeVideoWorkflowRefreshInFlightRef.current = true
      fetchConversationDetail(selectedConversationId)
        .then((payload) => {
          if (!active) {
            return
          }
          applyConversationDetail(payload)
        })
        .catch(() => {
          // Background workflow refresh should stay quiet unless the user explicitly retries.
        })
        .finally(() => {
          creativeVideoWorkflowRefreshInFlightRef.current = false
        })
    }, CREATIVE_VIDEO_WORKFLOW_POLL_MS)

    return () => {
      active = false
      window.clearInterval(intervalId)
    }
  }, [applyConversationDetail, selectedConversationId, shouldPollActiveCreativeVideoWorkflow])

  useEffect(() => {
    if (!selectedProject) {
      setProjectDraft(createEmptyProjectForm())
      setDeleteArmed(false)
      setScheduledTasks([])
      return
    }

    setProjectDraft({
      name: selectedProject.name,
      description: selectedProject.description ?? '',
      personaProfile: selectedProject.personaProfile ?? DEFAULT_PERSONA_PROFILE,
      providerPosture: selectedProject.providerPosture ?? DEFAULT_PROVIDER_POSTURE,
    })
    setDeleteArmed(false)
  }, [selectedProject])

  useEffect(() => {
    if (!selectedProject) {
      return
    }
    setScheduledTasksLoading(true)
    fetchScheduledTasks(selectedProject.id)
      .then(setScheduledTasks)
      .catch((error: Error) => {
        pushToast({
          type: 'error',
          title: 'Nie udało się wczytać zadań cyklicznych',
          message: error.message,
        })
      })
      .finally(() => setScheduledTasksLoading(false))
  }, [pushToast, selectedProject])

  const filteredConversations = useMemo(() => {
    const normalizedQuery = deferredSearchQuery.trim().toLowerCase()
    const queryTokens = normalizedQuery.split(/\s+/).filter(Boolean)
    return conversations
      .filter((conversation) => {
        if (conversation.archived) {
          return false
        }
        const matchesProject =
          selectedProjectId == null || conversation.projectId === selectedProjectId
        if (!matchesProject) {
          return false
        }
        if (!queryTokens.length) {
          return true
        }
        const displayTitle = resolveConversationTitle(conversation.title, conversation.summary)
        const projectName =
          conversation.projectId == null ? null : (projectNameById.get(conversation.projectId) ?? null)
        const haystack = buildConversationSearchHaystack(
          displayTitle,
          conversation.summary,
          projectName,
        )
        return queryTokens.every((token) => haystack.includes(token))
      })
      .sort((left, right) => {
        const leftPinned = pinnedConversationIds.includes(left.id) ? 1 : 0
        const rightPinned = pinnedConversationIds.includes(right.id) ? 1 : 0
        if (leftPinned !== rightPinned) {
          return rightPinned - leftPinned
        }

        const leftMatchedSource = left.matchedSource === 'title' ? 1 : 0
        const rightMatchedSource = right.matchedSource === 'title' ? 1 : 0
        if (leftMatchedSource !== rightMatchedSource) {
          return rightMatchedSource - leftMatchedSource
        }

        const leftTimestamp = Date.parse(left.lastMessageAt ?? left.updatedAt ?? '')
        const rightTimestamp = Date.parse(right.lastMessageAt ?? right.updatedAt ?? '')
        return rightTimestamp - leftTimestamp
      })
  }, [conversations, deferredSearchQuery, pinnedConversationIds, projectNameById, selectedProjectId])

  const activeRemoteRun = useMemo(() => {
    return (
      remoteExecutionRuns.find((run) => run.status === 'running' || run.status === 'dispatching') ??
      remoteExecutionRuns[0] ??
      null
    )
  }, [remoteExecutionRuns])

  const activeRemoteWorker = useMemo(() => {
    if (!activeRemoteRun?.assignedNodeId) {
      return null
    }
    return remoteWorkers.find((worker) => worker.nodeId === activeRemoteRun.assignedNodeId) ?? null
  }, [activeRemoteRun, remoteWorkers])

  const refreshBootstrap = async () => {
    const bootstrap = await fetchBootstrap()
    setBootstrap({
      uiVersion: bootstrap.uiVersion,
      projects: bootstrap.projects,
      conversations: bootstrap.recentConversations,
    })
    return bootstrap
  }

  const hydrateConversation = async (conversationId: number) => {
    const detail = await fetchConversationDetail(conversationId)
    return applyConversationDetail(detail)
  }

  const setActiveDraft = useCallback(
    (nextValue: string) => {
      setDraftsByConversationKey((current) => {
        if (!nextValue.trim()) {
          const next = { ...current }
          delete next[conversationWorkspaceKey]
          return next
        }
        return {
          ...current,
          [conversationWorkspaceKey]: nextValue,
        }
      })
    },
    [conversationWorkspaceKey],
  )

  const applyQuickSettingsPatch = useCallback(
    async (patch: Partial<ConversationQuickSettings>) => {
      const previousSettings = activeQuickSettings
      const nextSettings = {
        ...activeQuickSettings,
        ...patch,
      }

      setQuickSettingsByConversationKey((current) => ({
        ...current,
        [conversationWorkspaceKey]: nextSettings,
      }))

      if (selectedConversationId == null) {
        return
      }

      try {
        const updatedSettings = await updateConversationSettings(selectedConversationId, {
          activeWorkMode: nextSettings.deepAnalysisEnabled ? 'deep' : 'auto',
          responseStyleOverride: buildResponseStyleOverride(
            nextSettings.responseLength,
            nextSettings.responseTone,
          ),
          memoryEnabledOverride: nextSettings.memoryEnabled,
          fileEnabledOverride: nextSettings.fileEnabled,
          internetEnabledOverride: nextSettings.internetEnabled,
        })

        setConversationSettings({
          ...updatedSettings,
          qualityProfile: settings?.qualityProfile ?? activeQualityProfile,
        })
      } catch (error) {
        setQuickSettingsByConversationKey((current) => ({
          ...current,
          [conversationWorkspaceKey]: previousSettings,
        }))
        pushToast({
          type: 'error',
          title: 'Nie udalo sie zapisac ustawien rozmowy',
          message: error instanceof Error ? error.message : 'Nieznany blad podczas zapisu.',
        })
      }
    },
    [
      activeQualityProfile,
      activeQuickSettings,
      conversationWorkspaceKey,
      pushToast,
      selectedConversationId,
      setConversationSettings,
      settings?.qualityProfile,
    ],
  )

  const handleTogglePinnedConversation = useCallback((conversationId: number) => {
    setPinnedConversationIds((current) =>
      current.includes(conversationId)
        ? current.filter((item) => item !== conversationId)
        : [conversationId, ...current],
    )
  }, [])

  const handleArchiveConversation = useCallback(
    async (conversationId: number) => {
      const targetConversation =
        conversations.find((conversation) => conversation.id === conversationId) ?? null
      if (!targetConversation) {
        return
      }

      try {
        await archiveConversation(conversationId, {
          projectId: targetConversation.projectId,
        })
        removeConversation(conversationId)
        setPinnedConversationIds((current) => current.filter((item) => item !== conversationId))
        setDraftsByConversationKey((current) => {
          const next = { ...current }
          delete next[String(conversationId)]
          return next
        })
        setQuickSettingsByConversationKey((current) => {
          const next = { ...current }
          delete next[String(conversationId)]
          return next
        })

        if (selectedConversationId === conversationId) {
          const nextConversation =
            conversations.find(
              (conversation) =>
                conversation.id !== conversationId &&
                !conversation.archived &&
                (selectedProjectId == null || conversation.projectId === selectedProjectId),
            ) ?? null
          if (nextConversation) {
            setSelectedConversation(nextConversation.id, nextConversation.title)
          } else {
            resetConversationWorkspace('Nowa rozmowa')
          }
        }

        pushToast({
          type: 'success',
          title: 'Rozmowa zarchiwizowana',
          message: `Usunieto z glownej listy: ${resolveConversationTitle(targetConversation.title, targetConversation.summary)}.`,
        })
      } catch (error) {
        pushToast({
          type: 'error',
          title: 'Nie udalo sie zarchiwizowac rozmowy',
          message: error instanceof Error ? error.message : 'Nieznany blad podczas archiwizacji.',
        })
      }
    },
    [
      conversations,
      pushToast,
      removeConversation,
      resetConversationWorkspace,
      selectedConversationId,
      selectedProjectId,
      setSelectedConversation,
    ],
  )

  const triggerBriefing = async () => {
    const conversationProjectId =
      conversations.find((conversation) => conversation.id === selectedConversationId)?.projectId ?? null
    const effectiveProjectId = selectedProjectId ?? conversationProjectId

    startActiveTurn({
      kind: 'briefing',
      conversationId: selectedConversationId,
      projectId: effectiveProjectId,
      summary: '/briefing',
    })
    setStage('thinking')
    setAgentActivity({
      stage: 'thinking',
      title: 'ATNOS buduje briefing',
      detail: 'Zbieram decyzje, GitHub, Linear i najwazniejszy kontekst projektu.',
    })
    try {
      const result = await createChatBriefing({
        conversationId: selectedConversationId,
        projectId: effectiveProjectId,
      })
      setSelectedConversation(result.conversationId, result.conversationTitle)
      await hydrateConversation(result.conversationId)
      await refreshBootstrap()
      setStage('idle')
      finishActiveTurn()
      return true
    } catch (error) {
      setStage('error')
      setAgentActivity({
        stage: 'error',
        title: 'Briefing nie powstal',
        detail: error instanceof Error ? error.message : 'Nieznany blad przy generowaniu briefingu.',
      })
      pushToast({
        type: 'error',
        title: 'Briefing nieudany',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
      finishActiveTurn()
      window.setTimeout(() => setStage('idle'), 1400)
      return false
    }
  }

  const handleSelectProject = (projectId: number | null) => {
    setSelectedProjectId(projectId)

    const currentConversation = conversations.find(
      (conversation) => conversation.id === selectedConversationId,
    )
    if (currentConversation && (projectId == null || currentConversation.projectId === projectId)) {
      return
    }

    const nextConversation = conversations.find(
      (conversation) => projectId == null || conversation.projectId === projectId,
    )

    if (nextConversation) {
      setSelectedConversation(nextConversation.id, nextConversation.title)
      return
    }

    resetConversationWorkspace('Nowa rozmowa')
  }

  const handleNewConversation = useCallback(() => {
    resetConversationWorkspace('Nowa rozmowa')
    useUIStore.getState().requestComposerFocus()
  }, [resetConversationWorkspace])

  const handleNewProjectlessConversation = useCallback(() => {
    setSelectedProjectId(null)
    resetConversationWorkspace('Nowa rozmowa bez projektu')
    useUIStore.getState().requestComposerFocus()
  }, [resetConversationWorkspace, setSelectedProjectId])

  const buildRetryAttachments = (assistantMessageId: number | string): ChatAttachmentPayload[] => {
    const assistantIndex = messages.findIndex((message) => message.id === assistantMessageId)
    if (assistantIndex <= 0) {
      return []
    }
    const previousUserMessage = [...messages.slice(0, assistantIndex)]
      .reverse()
      .find((message) => message.role === 'user')
    if (!previousUserMessage?.attachments?.length) {
      return []
    }
    return previousUserMessage.attachments.map((attachment) => ({
      filename: attachment.filename,
      storage_path: attachment.storagePath,
      kind: attachment.kind,
      media_type: attachment.mediaType ?? null,
      file_extension: attachment.fileExtension ?? null,
      size_bytes: attachment.sizeBytes ?? null,
      extracted_text: attachment.extractedText ?? null,
    }))
  }

  const clearPendingTurn = (messageId: string, executionPhaseTimer?: number) => {
    const timeoutIds = pendingTurnTimeoutsRef.current.get(messageId)
    if (timeoutIds?.softTimeoutId != null) {
      window.clearTimeout(timeoutIds.softTimeoutId)
    }
    if (timeoutIds?.hardTimeoutId != null) {
      window.clearTimeout(timeoutIds.hardTimeoutId)
    }
    if (timeoutIds != null) {
      pendingTurnTimeoutsRef.current.delete(messageId)
    }
    if (executionPhaseTimer != null) {
      window.clearTimeout(executionPhaseTimer)
    }
    pendingTurnControllersRef.current.delete(messageId)
  }

  const cancelActiveGeneration = useCallback(() => {
    const controllers = [...pendingTurnControllersRef.current.values()]
    if (controllers.length === 0) {
      return
    }
    for (const controller of controllers) {
      if (!controller.signal.aborted) {
        controller.abort('user-cancel')
      }
    }
    setAgentActivity({
      stage: 'idle',
      title: CHAT_STATUS.cancelled,
      detail: 'Generowanie zostało przerwane na Twoje żądanie.',
    })
    setStage('idle')
    pushToast({
      type: 'warning',
      title: CHAT_STATUS.cancelled,
      message: 'Możesz wysłać kolejną wiadomość.',
    })
  }, [pushToast, setAgentActivity, setStage])

  const extractOptimisticRequest = (messageId: number | string) => {
    const message = messages.find((item) => item.id === messageId)
    const optimisticRequest =
      message?.metadataJson &&
      typeof message.metadataJson === 'object' &&
      message.metadataJson.optimisticRequest &&
      typeof message.metadataJson.optimisticRequest === 'object'
        ? (message.metadataJson.optimisticRequest as {
            text?: unknown
            attachments?: unknown
          })
        : null

    if (!optimisticRequest) {
      return null
    }

    return {
      text: typeof optimisticRequest.text === 'string' ? optimisticRequest.text : '',
      attachments: Array.isArray(optimisticRequest.attachments)
        ? (optimisticRequest.attachments as ChatAttachmentPayload[])
        : [],
    }
  }

  useEffect(() => {
    return () => {
      for (const controller of pendingTurnControllersRef.current.values()) {
        controller.abort('chat-route-unmount')
      }
      for (const timeoutIds of pendingTurnTimeoutsRef.current.values()) {
        if (timeoutIds.softTimeoutId != null) {
          window.clearTimeout(timeoutIds.softTimeoutId)
        }
        if (timeoutIds.hardTimeoutId != null) {
          window.clearTimeout(timeoutIds.hardTimeoutId)
        }
      }
      pendingTurnControllersRef.current.clear()
      pendingTurnTimeoutsRef.current.clear()
    }
  }, [])

  const submitProjectCommand = async (command: string, originalText: string) => {
    const projectId = activeProjectContext?.id ?? null
    if (projectId == null) {
      return false
    }

    appendMessage({
      id: `project-command-user-${crypto.randomUUID()}`,
      role: 'user',
      status: 'completed',
      content: originalText.trim(),
      createdAt: new Date().toISOString(),
      metadataJson: { kind: 'project_command_request', project_id: projectId },
    })
    startActiveTurn({
      kind: 'chat',
      conversationId: selectedConversationId,
      projectId,
      summary: `project command: ${command.slice(0, 120)}`,
    })
    setStage('thinking')
    setAgentActivity({
      stage: 'thinking',
      title: 'ATNOS sprawdza stan projektu',
      detail: 'Czytam health-checki, zadania, dowody i lokalną wiedzę projektu bez zgadywania przez LLM.',
    })

    try {
      const result = await executeProjectCommand({
        projectId,
        content: command,
        conversationId: selectedConversationId,
        privacyMode: effectiveDelegationPrivacyMode,
      })
      appendMessage({
        id: `project-command-result-${result.trace_id}`,
        role: 'assistant',
        status: 'completed',
        content: result.answer,
        createdAt: new Date().toISOString(),
        metadataJson: {
          kind: 'project_command_result',
          project_id: projectId,
          command_intent: result.intent,
          command_status: result.status,
          trace_id: result.trace_id,
          trace_status: result.trace_status,
          evidence_count: result.evidence.length,
          requires_confirmation: result.requires_confirmation,
          verified: result.verified,
          verification_status: result.verification_status,
        },
      })
      setSelectedConversation(result.conversation_id, result.conversation_title)
      await refreshBootstrap()
      await hydrateConversation(result.conversation_id)
      setProjectCommandRefreshKey((current) => current + 1)
      setStage(result.requires_confirmation ? 'awaiting_human' : 'idle')
      setAgentActivity(
        result.requires_confirmation
          ? {
              stage: 'awaiting_human',
              title: 'ATNOS czeka na decyzję',
              detail: 'Działanie zostało zatrzymane przed operacją wymagającą jawnego potwierdzenia.',
            }
          : null,
      )
      pushToast({
        type: result.status === 'completed' ? 'success' : 'warning',
        title: result.title,
        message: `Dowody: ${result.evidence.length} · trace #${result.trace_id}`,
      })
      finishActiveTurn()
      return true
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Nieznany błąd Project Command.'
      appendMessage({
        id: `project-command-error-${crypto.randomUUID()}`,
        role: 'system',
        status: 'failed',
        content: `PROJECT COMMAND NIEUDANY\n\n${detail}`,
        createdAt: new Date().toISOString(),
        metadataJson: { kind: 'project_command_error', project_id: projectId },
      })
      setStage('error')
      setAgentActivity({ stage: 'error', title: 'Project Command zatrzymał się na błędzie', detail })
      pushToast({ type: 'error', title: 'Nie udało się wykonać polecenia projektu', message: detail })
      finishActiveTurn()
      window.setTimeout(() => setStage('idle'), 1400)
      return false
    }
  }

  const submitChatTurn = async (
    payload: ChatInputSubmitPayload & { forceExternal?: boolean },
    options?: { optimisticMessageId?: string | number },
  ) => {
    const rawText = payload.text.trim()
    const attachments = payload.attachments
    const forceExternal = payload.forceExternal ?? false
    const generalChatPrompt = parseGeneralChatPrompt(rawText)
    if (rawText.startsWith('/') && generalChatPrompt === null && attachments.length === 0) {
      const slashResult = await handleSlashCommand(rawText)
      if (slashResult !== null) {
        return slashResult
      }
    }
    if (generalChatPrompt !== null && !generalChatPrompt) {
      appendSystemMessage(
        'Uzyj: /ask <pytanie>. W trybie „Rozmowa lokalna” zwykle wiadomosci i tak ida do Ollamy, bez /ask i bez Codexu.',
        { kind: 'slash_result', command: GENERAL_CHAT_COMMAND },
      )
      return false
    }
    const text = generalChatPrompt ?? rawText
    if (!text && attachments.length === 0) {
      return false
    }
    const effectiveProjectId =
      selectedConversationId == null ? selectedProjectId : selectedConversationProjectId
    const productionPrompt = generalChatPrompt === null && attachments.length === 0 ? parseProductionOrderPrompt(text) : null
    if (productionPrompt !== null) {
      return submitProductionOrderCommand(
        [productionPrompt.topic, productionPrompt.direction, productionPrompt.optionalNotes].join(' | '),
      )
    }
    if (generalChatPrompt === null && chatWorkMode === 'project_command' && isProjectCommand(text, effectiveProjectId, attachments.length)) {
      return submitProjectCommand(text, text)
    }
    const delegationPrompt = parseDelegationPrompt(text, delegationModeEnabled)
    if (delegationPrompt !== null) {
      return submitDelegationCommand(delegationPrompt, text, attachments)
    }
    const optimisticContent =
      text ||
      (attachments.length === 1
        ? `Dodano plik: ${attachments[0].filename}`
        : `Dodano ${attachments.length} pliki`)
    const optimisticMessageId = String(options?.optimisticMessageId ?? crypto.randomUUID())
    if (options?.optimisticMessageId != null) {
      setOptimisticMessageState(optimisticMessageId, 'pending', null)
    } else {
      appendOptimisticUserMessage({
        id: optimisticMessageId,
        content: optimisticContent,
        requestText: text,
        requestAttachments: attachments as unknown as Record<string, unknown>[],
      })
    }
    const effectiveTurnProjectId = effectiveProjectId
    let turnConversationId = selectedConversationId
    const isTurnWorkspaceActive = () =>
      useChatStore.getState().selectedConversationId === turnConversationId
    startActiveTurn({
      kind: 'chat',
      conversationId: turnConversationId,
      projectId: effectiveTurnProjectId,
      summary: text || attachments.map((attachment) => attachment.filename).join(', '),
    })
    const queueOriginTurnStartedAt = useChatStore.getState().activeTurn?.startedAt ?? null
    setStage('thinking')
    setAgentActivity({
      stage: 'thinking',
      title: CHAT_STATUS.analyzing,
      detail: 'Dobieram sposób obsługi polecenia. Czynności pokażę po otrzymaniu zdarzeń z narzędzi.',
    })
    const requestController = new AbortController()
    pendingTurnControllersRef.current.set(optimisticMessageId, requestController)
    const softTimeoutId = window.setTimeout(() => {
      if (!isTurnWorkspaceActive()) {
        return
      }
      useSystemStore.getState().setStage('executing')
      useSystemStore.getState().setAgentActivity({
        stage: 'executing',
        title: CHAT_STATUS.awaitingResponse,
        detail:
          'Nie otrzymałem jeszcze wyniku. Brak odpowiedzi nie potwierdza wykonania operacji.',
      })
      pushToast({
        type: 'warning',
        title: 'ATNOS nadal pracuje',
        message:
          'Zadanie trwa dłużej niż zwykle. Lokalny model może potrzebować więcej czasu; Atnos nie przerywa rozmowy sam z siebie.',
      })
    }, CHAT_SOFT_TIMEOUT_MS)
    pendingTurnTimeoutsRef.current.set(optimisticMessageId, {
      softTimeoutId,
    })

    try {
      const streamingAssistantId = `stream-${optimisticMessageId}`
      const liveTools: Array<{ name: string; status: string; summary: string }> = []
      if (isTurnWorkspaceActive()) {
        upsertStreamingAssistant(streamingAssistantId, {
          content: '',
          status: 'pending',
          metadataJson: {
            streaming: true,
            agentic_tool_loop: { status: 'running', live_tools: liveTools, tools_used: [] },
          },
        })
      }
      const result = await sendChatTurnStream(
        {
          content: text,
          conversation_id: turnConversationId ?? undefined,
          project_id: effectiveTurnProjectId ?? undefined,
          prompt_improved: payload.promptImproved ?? false,
          force_external: forceExternal,
          attachments,
          execution_preferences: {
            response_style: buildResponseStyleOverride(
              activeQuickSettings.responseLength,
              activeQuickSettings.responseTone,
            ),
            persona_profile:
              settings?.personaProfile ??
              activeProjectContext?.personaProfile ??
              DEFAULT_PERSONA_PROFILE,
            provider_posture:
              chatWorkMode === 'local_chat'
                ? 'strict-local'
                : settings?.providerPosture ??
                  activeProjectContext?.providerPosture ??
                  DEFAULT_PROVIDER_POSTURE,
            quality_profile: activeQualityProfile,
            memory_enabled: activeQuickSettings.memoryEnabled,
            file_enabled: activeQuickSettings.fileEnabled,
            internet_enabled: activeQuickSettings.internetEnabled,
            active_work_mode: activeQuickSettings.deepAnalysisEnabled ? 'deep' : 'quick',
          },
        },
        {
          signal: requestController.signal,
          onEvent: (event) => {
            if (!isTurnWorkspaceActive()) {
              return
            }
            if (event.type === 'conversation') {
              turnConversationId = event.conversation_id
              setSelectedConversation(event.conversation_id, event.conversation_title)
              return
            }
            if (event.type === 'status') {
              const stage =
                event.stage === 'executing' || event.stage === 'answering'
                  ? 'executing'
                  : 'thinking'
              useSystemStore.getState().setStage(stage)
              useSystemStore.getState().setAgentActivity({
                stage,
                title: event.title || 'Atnos pracuje',
                detail: event.detail || '',
              })
              return
            }
            if (event.type === 'tool_start') {
              const runningLabel = getAgentToolLabel(event.name, 'running')
              useSystemStore.getState().setStage('executing')
              useSystemStore.getState().setAgentActivity({
                stage: 'executing',
                title: runningLabel,
                detail: 'Wykonuję kolejny krok…',
              })
              liveTools.push({ name: event.name, status: 'running', summary: '' })
              upsertStreamingAssistant(streamingAssistantId, {
                content: '',
                metadataJson: {
                  streaming: true,
                  liveTool: event.name,
                  agentic_tool_loop: {
                    status: 'running',
                    live_tools: [...liveTools],
                    tools_used: liveTools.map((item) => item.name),
                  },
                },
              })
              return
            }
            if (event.type === 'tool_result') {
              const doneLabel = getAgentToolStateLabel(event.name, event.status)
              useSystemStore.getState().setAgentActivity({
                stage: 'executing',
                title: doneLabel,
                detail: event.summary?.trim()
                  ? event.summary
                  : doneLabel,
              })
              const lastIndex = [...liveTools]
                .map((item, index) => ({ item, index }))
                .reverse()
                .find((entry) => entry.item.name === event.name && entry.item.status === 'running')
                ?.index
              if (lastIndex != null) {
                liveTools[lastIndex] = {
                  name: event.name,
                  status: event.status || 'ok',
                  summary: event.summary || '',
                }
              } else {
                liveTools.push({
                  name: event.name,
                  status: event.status || 'ok',
                  summary: event.summary || '',
                })
              }
              upsertStreamingAssistant(streamingAssistantId, {
                content: '',
                metadataJson: {
                  streaming: true,
                  liveTool: event.name,
                  agentic_tool_loop: {
                    status: 'running',
                    live_tools: [...liveTools],
                    tools_used: liveTools.map((item) => item.name),
                  },
                },
              })
              return
            }
            if (event.type === 'token' && event.text) {
              useSystemStore.getState().setStage('executing')
              useSystemStore.getState().setAgentActivity({
                stage: 'executing',
                title: 'Piszę odpowiedź…',
                detail: 'Składam finalną wiadomość.',
              })
              appendStreamingAssistantToken(streamingAssistantId, event.text)
            }
          },
        },
      )

      const applyWorkspace = isTurnWorkspaceActive()

      // Drop temporary streaming bubble before committing final turn.
      if (applyWorkspace) {
        useChatStore.setState((state) => ({
          messages: state.messages.filter((message) => message.id !== streamingAssistantId),
        }))
      }

      replaceAfterTurn({
        tempId: optimisticMessageId,
        userMessageId: result.userMessageId,
        assistantMessageId: result.assistantMessageId,
        userMessage: result.userMessage || optimisticContent,
        assistantMessage: result.assistantMessage || result.runtimeError?.userTitle || '',
        assistantMessageMetadataJson: {
          ...result.assistantMessageMetadataJson,
          ...(result.skillExecution ? { atnos_skill_execution: result.skillExecution } : {}),
        },
        conversationId: result.conversationId,
        conversationTitle: result.conversationTitle,
        projectId: result.conversationProjectId,
        applyWorkspace,
      })
      setQualityProfilesByConversation((current) => {
        const next = { ...current }
        const selected = current[qualityProfileKey] ?? activeQualityProfile
        next[String(result.conversationId)] = selected
        if (qualityProfileKey === '__draft__') {
          delete next.__draft__
        }
        return next
      })
      if (applyWorkspace) {
        setSelectedConversation(result.conversationId, result.conversationTitle)
        if (
          attachments.length > 0 ||
          result.selectedProviderKey === 'local-capability' ||
          result.selectedProviderKey === 'creative-video-operator' ||
          useSystemStore.getState().ambientState?.voiceRepliesEnabled ||
          result.runtimeError != null
        ) {
          await hydrateConversation(result.conversationId)
        }
      }
      if (result.selectedProviderKey === 'local-capability' && selectedProjectId != null) {
        const tasks = await fetchScheduledTasks(selectedProjectId)
        setScheduledTasks(tasks)
      }
      clearPendingTurn(optimisticMessageId)
      const agenticMeta =
        result.assistantMessageMetadataJson &&
        typeof result.assistantMessageMetadataJson === 'object'
          ? (result.assistantMessageMetadataJson.agentic_tool_loop as
              | Record<string, unknown>
              | undefined)
          : undefined
      const needsConfirmation =
        agenticMeta?.requires_confirmation === true ||
        agenticMeta?.status === 'needs_confirmation'
      if (applyWorkspace) {
        setAgentActivity(
          needsConfirmation
            ? {
                stage: 'awaiting_human',
                title: 'ATNOS czeka na potwierdzenie',
                detail:
                  'Inwazyjna akcja została zatrzymana. Odpowiedz „potwierdzam”, aby kontynuować.',
              }
            : null,
        )
        setStage(needsConfirmation ? 'awaiting_human' : 'idle')
      }
      setQueuedTurns(
        bindQueuedTurnsToConversation(
          useChatStore.getState().queuedTurns,
          queueOriginTurnStartedAt,
          result.conversationId,
        ),
      )
      finishActiveTurn()
      return true
    } catch (error) {
      clearPendingTurn(optimisticMessageId)
      const streamingId = `stream-${optimisticMessageId}`
      const applyWorkspace = isTurnWorkspaceActive()
      if (requestController.signal.aborted) {
        if (applyWorkspace) {
          useChatStore.setState((state) => ({
            messages: state.messages
              .map((message) => {
                if (message.id === optimisticMessageId) {
                  return {
                    ...message,
                    status: 'completed',
                    metadataJson: {
                      ...(message.metadataJson && typeof message.metadataJson === 'object'
                        ? message.metadataJson
                        : {}),
                      optimistic: false,
                      cancelled_generation: true,
                    },
                  }
                }
                if (message.id !== streamingId) {
                  return message
                }
                const partial = (message.content ?? '').trim()
                if (!partial) {
                  return message
                }
                return {
                  ...message,
                  status: 'interrupted',
                  metadataJson: {
                    ...(message.metadataJson && typeof message.metadataJson === 'object'
                      ? message.metadataJson
                      : {}),
                    streaming: false,
                    interrupted: true,
                    interrupt_reason: 'user-cancel',
                    agentic_tool_loop: {
                      ...((message.metadataJson?.agentic_tool_loop as Record<string, unknown>) ?? {}),
                      status: 'interrupted',
                    },
                  },
                }
              })
              .filter((message) => {
                if (message.id !== streamingId) {
                  return true
                }
                return Boolean((message.content ?? '').trim())
              }),
          }))
          setAgentActivity(null)
          setStage('idle')
        }
        finishActiveTurn()
        return false
      }
      if (applyWorkspace) {
        useChatStore.setState((state) => ({
          messages: state.messages.filter((message) => message.id !== streamingId),
        }))
        setOptimisticMessageState(
          optimisticMessageId,
          'failed',
          humanizeChatError(error),
        )
        setStage('error')
        setAgentActivity({
          stage: 'error',
          title: CHAT_STATUS.sendFailed,
          detail: humanizeChatError(error),
        })
        pushToast({
          type: 'error',
          title: CHAT_STATUS.sendFailed,
          message: humanizeChatError(error),
        })
        window.setTimeout(() => setStage('idle'), 1400)
      }
      finishActiveTurn()
      return false
    }
  }

  const submitChatTurnRef = useRef(submitChatTurn)
  submitChatTurnRef.current = submitChatTurn

  useEffect(() => {
    const projectId =
      selectedConversationId == null ? selectedProjectId : selectedConversationProjectId
    const next = takeRunnableChatTurn(
      queuedTurns,
      { conversationId: selectedConversationId, projectId },
      activeTurn != null || queueDispatching,
    )
    if (!next) return

    setQueuedTurns(next.remaining)
    setQueueDispatching(true)
    void submitChatTurnRef.current({
      text: next.turn.text,
      attachments: next.turn.attachments,
      promptImproved: next.turn.promptImproved,
    }).finally(() => setQueueDispatching(false))
  }, [
    activeTurn,
    queueDispatching,
    queuedTurns,
    selectedConversationId,
    selectedConversationProjectId,
    selectedProjectId,
    setQueuedTurns,
  ])

  const appendSystemMessage = useCallback(
    (content: string, metadataJson?: Record<string, unknown>) => {
      appendMessage({
        id: `system-${crypto.randomUUID()}`,
        role: 'system',
        status: 'completed',
        content,
        createdAt: new Date().toISOString(),
        metadataJson: metadataJson ?? null,
      })
    },
    [appendMessage],
  )

  const submitDelegationCommand = useCallback(
    async (prompt: string, originalText: string, attachments: ChatAttachmentPayload[]) => {
      if (!prompt.trim()) {
        appendSystemMessage('Uzyj: /atnos <zadanie> albo wlacz Tryb Atnosa i wpisz polecenie.', {
          kind: 'ai_delegation_help',
        })
        return false
      }

      appendMessage({
        id: `delegation-user-${crypto.randomUUID()}`,
        role: 'user',
        status: 'completed',
        content: originalText.trim(),
        createdAt: new Date().toISOString(),
        metadataJson: {
          kind: 'ai_delegation_request',
          delegation_mode: delegationModeEnabled,
        },
      })

      const effectiveProjectId =
        selectedConversationId == null ? selectedProjectId : selectedConversationProjectId
      startActiveTurn({
        kind: 'chat',
        conversationId: selectedConversationId,
        projectId: effectiveProjectId,
        summary: `delegacja: ${prompt.slice(0, 120)}`,
      })
      setStage('thinking')
      setAgentActivity({
        stage: 'thinking',
        title: 'ATNOS planuje delegacje',
        detail: 'Tworze audytowalny plan zadan dla Ollamy lub kontrolowanego browser AI.',
      })

      try {
        const task = await delegateAiWorkerPlan({
          taskType: inferDelegationTaskType(prompt),
          prompt,
          projectId: effectiveProjectId,
          context: {
            source: 'chat_route',
            project_id: effectiveProjectId,
            project: activeProjectContext
              ? {
                  id: activeProjectContext.id,
                  name: activeProjectContext.name,
                  slug: activeProjectContext.slug,
                  personaProfile: activeProjectContext.personaProfile,
                  providerPosture: activeProjectContext.providerPosture,
                }
              : null,
            conversationId: selectedConversationId,
            attachments: attachments.map((attachment) => ({
              filename: attachment.filename,
              kind: attachment.kind,
              mediaType: attachment.media_type ?? null,
              sizeBytes: attachment.size_bytes ?? null,
            })),
          },
          preferredWorker: 'ollama',
          allowedWorkers: effectiveAllowedDelegationWorkers,
          privacyMode: effectiveDelegationPrivacyMode,
          priority: activeQualityProfile === 'premium' ? 'high' : 'normal',
          requiresApprovalForResult: true,
          maxSteps: 50,
        })

        let executedPlan = task
        if (task.status === 'planned') {
          setStage('executing')
          setAgentActivity({
            stage: 'executing',
            title: 'ATNOS wykonuje bezpieczne kroki lokalne',
            detail: 'Uruchamiam wyłącznie Ollamę w trybie strict local; zatrzymam plan przed review lub browser AI.',
          })
          executedPlan = await runAiWorkerSafePlan(task.id)
        }

        appendSystemMessage(formatDelegationMessage(executedPlan), {
          kind: 'ai_delegation_plan',
          task_id: executedPlan.id,
          task_status: executedPlan.status,
          task_type: executedPlan.taskType,
          privacy_mode: executedPlan.privacyMode,
          child_count: executedPlan.children.length,
        })
        pushToast({
          type: executedPlan.status === 'blocked' ? 'warning' : 'success',
          title: `Delegacja #${executedPlan.id}: ${executedPlan.status}`,
          message: executedPlan.resultSummary ?? executedPlan.blockedReason ?? 'Plan zapisany w kolejce AI Delegation.',
        })
        setStage('idle')
        setAgentActivity(null)
        finishActiveTurn()
        return true
      } catch (error) {
        appendSystemMessage(
          `DELEGACJA NIEUDANA\n\n${error instanceof Error ? error.message : 'Nieznany blad delegacji.'}`,
          { kind: 'ai_delegation_error' },
        )
        setStage('error')
        setAgentActivity({
          stage: 'error',
          title: 'Delegacja AI nie powiodla sie',
          detail: error instanceof Error ? error.message : 'Nieznany blad delegacji.',
        })
        pushToast({
          type: 'error',
          title: 'Delegacja AI nie powiodla sie',
          message: error instanceof Error ? error.message : 'Nieznany blad',
        })
        finishActiveTurn()
        window.setTimeout(() => setStage('idle'), 1400)
        return false
      }
    },
    [
      activeProjectContext,
      activeQualityProfile,
      appendMessage,
      appendSystemMessage,
      delegationModeEnabled,
      delegationPrivacyMode,
      effectiveAllowedDelegationWorkers,
      effectiveDelegationPrivacyMode,
      finishActiveTurn,
      pushToast,
      runAiWorkerSafePlan,
      selectedConversationId,
      selectedConversationProjectId,
      selectedProjectId,
      setAgentActivity,
      setStage,
      startActiveTurn,
    ],
  )

  const formatDecisionCommandSummary = useCallback(
    (entries: DecisionLogEntryView[], overdueCount: number) => {
      if (entries.length === 0) {
        return 'AKTYWNE DECYZJE\n\nBrak aktywnych commitments i decyzji.'
      }
      const lines = ['AKTYWNE DECYZJE', '']
      if (overdueCount > 0) {
        lines.push(`Zalegle: ${overdueCount}`)
        lines.push('')
      }
      entries.slice(0, 8).forEach((entry) => {
        const dueSuffix = entry.dueAt ? ` · termin ${entry.dueAt.slice(0, 10)}` : ''
        lines.push(`- [${entry.kind}] ${entry.summary}${dueSuffix}`)
      })
      return lines.join('\n')
    },
    [],
  )

  const showDecisionsSummary = useCallback(async () => {
    const conversationProjectId =
      conversations.find((conversation) => conversation.id === selectedConversationId)?.projectId ?? null
    const effectiveProjectId = selectedProjectId ?? conversationProjectId
    const payload = await fetchChatDecisions({ projectId: effectiveProjectId })
    appendSystemMessage(formatDecisionCommandSummary(payload.entries, payload.overdueCount), {
      kind: 'slash_result',
      command: '/decisions',
      overdue_count: payload.overdueCount,
      entries: payload.entries,
    })
    return true
  }, [
    appendSystemMessage,
    conversations,
    formatDecisionCommandSummary,
    selectedConversationId,
    selectedProjectId,
  ])

  const showAiWorkerTasks = useCallback(async () => {
    try {
      const tasks = await fetchAiWorkerTasks({ rootsOnly: true, limit: 8 })
      appendSystemMessage(formatAiWorkerTaskList(tasks), {
        kind: 'slash_result',
        command: '/ai-tasks',
        task_count: tasks.length,
      })
      return true
    } catch (error) {
      appendSystemMessage(
        `AI WORKERS NIEDOSTEPNE\n\n${error instanceof Error ? error.message : 'Nieznany blad pobierania kolejki.'}`,
        { kind: 'slash_error', command: '/ai-tasks' },
      )
      return false
    }
  }, [appendSystemMessage])

  const runAiWorkerTaskFromChat = useCallback(
    async (argument: string) => {
      const taskId = Number.parseInt(argument.trim(), 10)
      if (!Number.isFinite(taskId) || taskId <= 0) {
        appendSystemMessage('Uzyj: /run-task <id>', {
          kind: 'slash_result',
          command: '/run-task',
        })
        return false
      }

      startActiveTurn({
        kind: 'chat',
        conversationId: selectedConversationId,
        projectId: selectedProjectId ?? selectedConversationProjectId,
        summary: `run AI worker task #${taskId}`,
      })
      setStage('executing')
      setAgentActivity({
        stage: 'executing',
        title: `Uruchamiam AI worker task #${taskId}`,
        detail: 'Backend wykona podzadanie przez dozwolonego workera i zapisze wynik w kolejce.',
      })
      try {
        const task = await runAiWorkerTask(taskId)
        appendSystemMessage(formatAiWorkerTaskResult(task), {
          kind: 'slash_result',
          command: '/run-task',
          task_id: task.id,
          task_status: task.status,
        })
        pushToast({
          type: task.status === 'completed' ? 'success' : 'warning',
          title: `Task #${task.id}: ${task.status}`,
          message: task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? 'Task zaktualizowany.',
        })
        setStage('idle')
        setAgentActivity(null)
        finishActiveTurn()
        return true
      } catch (error) {
        appendSystemMessage(
          `AI WORKER TASK NIEUDANY\n\n${error instanceof Error ? error.message : 'Nieznany blad uruchamiania taska.'}`,
          { kind: 'slash_error', command: '/run-task', task_id: taskId },
        )
        setStage('error')
        setAgentActivity({
          stage: 'error',
          title: `AI worker task #${taskId} nie powiodl sie`,
          detail: error instanceof Error ? error.message : 'Nieznany blad uruchamiania taska.',
        })
        pushToast({
          type: 'error',
          title: 'AI worker task nie powiodl sie',
          message: error instanceof Error ? error.message : 'Nieznany blad',
        })
        finishActiveTurn()
        window.setTimeout(() => setStage('idle'), 1400)
        return false
      }
    },
    [
      appendSystemMessage,
      finishActiveTurn,
      pushToast,
      selectedConversationId,
      selectedConversationProjectId,
      selectedProjectId,
      setAgentActivity,
      setStage,
      startActiveTurn,
    ],
  )

  const handleResolveBriefingDecision = useCallback(
    async (decisionId: number) => {
      const resolved = await resolveDecisionLogEntry(decisionId, 'Resolved from BriefingCard.')
      pushToast({
        type: 'success',
        title: 'Commitment zamkniety',
        message: resolved.summary,
      })
    },
    [pushToast],
  )

  const submitProductionOrderCommand = useCallback(
    async (argument: string) => {
      const parts = argument.split('|').map((part) => part.trim())
      const [topic, direction = '', optionalNotes = ''] = parts
      if (!topic) {
        appendSystemMessage('Uzyj: /rolka <temat> | <kierunek> | <opcjonalne uwagi>', {
          kind: 'slash_result',
          command: '/rolka',
        })
        return false
      }
      appendMessage({
        id: `production-order-user-${crypto.randomUUID()}`,
        role: 'user',
        status: 'completed',
        content: `/rolka ${argument}`,
        createdAt: new Date().toISOString(),
        metadataJson: { kind: 'production_order_request', project: 'wealth_blueprint' },
      })
      startActiveTurn({
        kind: 'chat',
        conversationId: selectedConversationId,
        projectId: selectedProjectId,
        summary: `production order: ${topic}`,
      })
      setStage('executing')
      setAgentActivity({
        stage: 'executing',
        title: 'ATNOS przygotowuje zlecenie rolki',
        detail: 'Przekazuję temat do formularza z wyborem lokalnej grafiki Wealth Blueprint.',
      })
      appendMessage({
        id: `production-order-handoff-${crypto.randomUUID()}`,
        role: 'assistant',
        status: 'completed',
        content: [
          '## Przygotowanie rolki Wealth Blueprint',
          '',
          'Przenoszę temat do formularza produkcji. Wybierz lokalną grafikę i jej styl, a potem utwórz zlecenie.',
          'Lokalny wybór dotyczy grafiki; sposób realizacji lektora zależy od konfiguracji produkcji.',
        ].join('\n'),
        createdAt: new Date().toISOString(),
        metadataJson: { kind: 'production_order_handoff', project: 'wealth_blueprint' },
      })
      pushToast({
        type: 'success',
        title: 'Temat gotowy do konfiguracji',
        message: 'Wybierz lokalną grafikę i styl w formularzu produkcji.',
      })
      setStage('idle')
      setAgentActivity(null)
      finishActiveTurn()
      navigate('/create', {
        state: {
          productionPrefill: { project: 'wealth_blueprint', topic, direction, optionalNotes },
        },
      })
      return true
    },
    [
      appendMessage,
      appendSystemMessage,
      finishActiveTurn,
      navigate,
      pushToast,
      selectedConversationId,
      selectedProjectId,
      setAgentActivity,
      setStage,
      startActiveTurn,
    ],
  )

  const handleSelectLocalModel = useCallback(
    async (runtimeModelName: string) => {
      const target = runtimeModelName.trim()
      if (!target || switchingLocalModel) {
        return
      }
      setSwitchingLocalModel(true)
      try {
        const result = await switchLocalChatModel(target)
        useSystemStore.getState().setMetrics({
          model: {
            name: result.runtimeModelName,
            provider: 'ollama',
          },
        })
        appendSystemMessage(
          `MODEL PRZELACZONY\n\nAktywny lokalny model czatu: ${result.runtimeModelName}.`,
          {
            kind: 'slash_result',
            command: '/model',
            runtime_model_name: result.runtimeModelName,
            model_key: result.modelKey,
          },
        )
        pushToast({
          type: 'success',
          title: 'Lokalny model czatu',
          message: `${result.runtimeModelName} odpowiada teraz w rozmowie.`,
        })
      } catch (error) {
        pushToast({
          type: 'error',
          title: 'Nie udalo sie przelaczyc modelu',
          message:
            error instanceof Error
              ? error.message
              : 'Sprawdz, czy Ollama dziala i czy model jest zainstalowany.',
        })
      } finally {
        setSwitchingLocalModel(false)
      }
    },
    [appendSystemMessage, pushToast, switchingLocalModel],
  )

  const handleSlashCommand = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      const [command, ...rest] = trimmed.split(/\s+/)
      const argument = rest.join(' ').trim()

      if (command === '/briefing') {
        return triggerBriefing()
      }

      if (command === '/decisions') {
        return showDecisionsSummary()
      }

      if (command === '/ai-tasks') {
        return showAiWorkerTasks()
      }

      if (command === '/run-task') {
        return runAiWorkerTaskFromChat(argument)
      }

      if (command === '/rolka' || command === '/short') {
        return submitProductionOrderCommand(argument)
      }

      if (command === '/help') {
        appendSystemMessage(
          [
            'DOSTEPNE KOMENDY',
            '',
            '- /briefing · zbuduj poranny briefing',
            '- /decisions · pokaz aktywne commitments i decyzje',
            '- /atnos <zadanie> · zaplanuj kontrolowana delegacje przez AI Workers',
            '- /delegate <zadanie> · alias dla /atnos',
            '- /ask <pytanie> · to samo co zwykle pole w trybie rozmowy lokalnej',
            '- /rolka <temat> | <kierunek> | <uwagi> · utworz zlecenie Wealth Blueprint',
            '- /ai-tasks · pokaz ostatnie plany i taski AI Delegation',
            '- /run-task <id> · uruchom zapisane podzadanie AI Workera',
            '- /clear · wyczysc biezaca powierzchnie rozmowy',
            '- /model <name> · przelacz lokalny model czatu',
            '- /help · pokaz te liste',
          ].join('\n'),
          { kind: 'slash_result', command: '/help' },
        )
        return true
      }

      if (command === '/clear') {
        const confirmed = window.confirm('Wyczysc biezaca powierzchnie rozmowy?')
        if (!confirmed) {
          return false
        }
        resetConversationWorkspace('Nowa rozmowa')
        pushToast({
          type: 'success',
          title: 'Rozmowa wyczyszczona',
          message: 'Biezacy workspace rozmowy zostal zresetowany.',
        })
        return true
      }

      if (command === '/model') {
        if (!argument) {
          const available =
            ollamaInstalledModels.length > 0
              ? `\nDostepne lokalnie: ${ollamaInstalledModels.join(', ')}`
              : ''
          appendSystemMessage(`Uzyj: /model <nazwa-modelu>${available}`, {
            kind: 'slash_result',
            command: '/model',
          })
          return false
        }
        await handleSelectLocalModel(argument)
        return true
      }

      return null
    },
    [
      appendSystemMessage,
      handleSelectLocalModel,
      ollamaInstalledModels,
      pushToast,
      resetConversationWorkspace,
      runAiWorkerTaskFromChat,
      showAiWorkerTasks,
      showDecisionsSummary,
      submitProductionOrderCommand,
      triggerBriefing,
    ],
  )

  const handleSend = async (payload: ChatInputSubmitPayload) => {
    const currentTurn = useChatStore.getState().activeTurn
    if (!currentTurn) return submitChatTurn(payload)

    const targetConversationId = selectedConversationId ?? currentTurn.conversationId
    const targetConversation =
      targetConversationId == null
        ? null
        : conversations.find((conversation) => conversation.id === targetConversationId) ?? null
    const targetProjectId =
      targetConversationId == null
        ? selectedProjectId ?? currentTurn.projectId
        : targetConversation?.projectId ??
          (targetConversationId === selectedConversationId ? selectedConversationProjectId : null) ??
          currentTurn.projectId
    const queuedTurn: QueuedChatTurn = {
      id: crypto.randomUUID(),
      text: payload.text,
      attachments: payload.attachments,
      promptImproved: payload.promptImproved ?? false,
      targetConversationId,
      targetProjectId,
      afterTurnStartedAt: currentTurn.startedAt,
      createdAt: new Date().toISOString(),
    }
    const result = enqueueChatTurn(useChatStore.getState().queuedTurns, queuedTurn)
    if (!result.accepted) {
      pushToast({
        type: 'warning',
        title: 'Kolejka jest pełna',
        message: `Możesz dodać maksymalnie ${MAX_QUEUED_CHAT_TURNS} wiadomości.`,
      })
      return false
    }
    setQueuedTurns(result.queue)
    pushToast({
      type: 'success',
      title: 'Dodano do kolejki',
      message: 'Atnos wyśle tę wiadomość po zakończeniu bieżącej tury.',
    })
    return true
  }

  const handleRemoveQueuedMessage = (id: string) => {
    const turn = useChatStore.getState().queuedTurns.find((item) => item.id === id)
    if (!turn) return
    setQueuedTurns(useChatStore.getState().queuedTurns.filter((item) => item.id !== id))
    for (const attachment of turn.attachments) {
      void deleteUpload(attachment.storage_path).catch(() => undefined)
    }
  }

  const toggleQuickSettingFromPalette = useCallback(
    (key: ChatQuickToggleKey) => {
      void applyQuickSettingsPatch({
        [key]: !activeQuickSettings[key],
      } as Partial<ConversationQuickSettings>)
    },
    [activeQuickSettings, applyQuickSettingsPatch],
  )

  useEffect(() => {
    setChatCommandSurface({
      activeConversationId: selectedConversationId,
      activeConversationTitle: selectedConversationTitle,
      activeProjectId: activeProjectContext?.id ?? null,
      activeProjectName: activeProjectContext?.name ?? null,
      quickToggles: {
        memoryEnabled: activeQuickSettings.memoryEnabled,
        fileEnabled: activeQuickSettings.fileEnabled,
        internetEnabled: activeQuickSettings.internetEnabled,
      },
      actions: {
        archiveConversation: () => {
          if (selectedConversationId == null) {
            return
          }
          return handleArchiveConversation(selectedConversationId)
        },
        selectConversation: (conversationId: number) => {
          const conversation =
            conversations.find((item) => item.id === conversationId && !item.archived) ?? null
          if (!conversation) {
            return
          }
          setSelectedConversation(conversation.id, conversation.title)
        },
        selectProject: handleSelectProject,
        showDecisions: showDecisionsSummary,
        toggleQuickSetting: toggleQuickSettingFromPalette,
        triggerBriefing,
      },
    })

    return () => {
      setChatCommandSurface(null)
    }
  }, [
    activeQuickSettings.fileEnabled,
    activeQuickSettings.internetEnabled,
    activeQuickSettings.memoryEnabled,
    activeProjectContext,
    conversations,
    handleArchiveConversation,
    handleSelectProject,
    selectedConversationId,
    selectedConversationTitle,
    setChatCommandSurface,
    setSelectedConversation,
    showDecisionsSummary,
    toggleQuickSettingFromPalette,
    triggerBriefing,
  ])

  useHotkey(['mod+/'], (event) => {
    if (useUIStore.getState().paletteOpen) {
      return
    }
    event.preventDefault()
    useUIStore.getState().requestComposerFocus()
  })

  useHotkey(['mod+shift+n'], (event) => {
    if (useUIStore.getState().paletteOpen) {
      return
    }
    event.preventDefault()
    handleNewConversation()
  })

  useHotkey(['mod+shift+b'], (event) => {
    if (useUIStore.getState().paletteOpen) {
      return
    }
    event.preventDefault()
    void triggerBriefing()
  })

  useHotkey(['mod+shift+a'], (event) => {
    if (useUIStore.getState().paletteOpen || selectedConversationId == null) {
      return
    }
    event.preventDefault()
    void handleArchiveConversation(selectedConversationId)
  })

  useHotkey(['mod+shift+f'], (event) => {
    if (useUIStore.getState().paletteOpen) {
      return
    }
    event.preventDefault()
    conversationSearchInputRef.current?.focus()
    conversationSearchInputRef.current?.select()
  })

  const handleImprovePrompt = async (draft: string) => {
    const result = await improveChatPrompt(draft)
    return {
      text: result.improvedContent,
      strategyNote: result.strategyNote,
      changed: result.changed,
    }
  }

  const handleRetryOptimisticMessage = async (messageId: number | string) => {
    const retryPayload = extractOptimisticRequest(messageId)
    if (!retryPayload) {
      pushToast({
        type: 'warning',
        title: 'Brak danych do ponowienia',
        message: 'Nie udało się odzyskać oryginalnej treści wiadomości.',
      })
      return
    }

    await submitChatTurn(
      {
        text: retryPayload.text,
        attachments: retryPayload.attachments,
      },
      { optimisticMessageId: messageId },
    )
  }

  const handleRetryRuntimeError = async (
    assistantMessageId: number | string,
    options?: { forceExternal?: boolean },
  ) => {
    const assistantIndex = messages.findIndex((message) => message.id === assistantMessageId)
    if (assistantIndex <= 0) {
      pushToast({
        type: 'warning',
        title: 'Brak poprzedniego promptu',
        message: 'Nie znalazlem wiadomosci uzytkownika do ponowienia.',
      })
      return
    }
    const previousUserMessage = [...messages.slice(0, assistantIndex)]
      .reverse()
      .find((message) => message.role === 'user')
    if (!previousUserMessage) {
      pushToast({
        type: 'warning',
        title: 'Brak poprzedniego promptu',
        message: 'Nie znalazlem wiadomosci uzytkownika do ponowienia.',
      })
      return
    }
    await submitChatTurn({
      text: previousUserMessage.content,
      attachments: buildRetryAttachments(assistantMessageId),
      forceExternal: options?.forceExternal ?? false,
    })
  }

  useEffect(() => {
    if (briefingNonce <= 0) return
    void triggerBriefing()
  }, [briefingNonce])

  useEffect(() => {
    if (decisionsNonce <= 0) return
    void showDecisionsSummary()
  }, [decisionsNonce, showDecisionsSummary])

  const handleCreateProject = async () => {
    const payload = normalizeProjectPayload(newProject)
    if (!payload.name) {
      pushToast({
        type: 'warning',
        title: 'Brakuje nazwy projektu',
        message: 'Podaj nazwę projektu przed zapisaniem.',
      })
      return
    }

    setCreatingProject(true)
    try {
      const created = await createProject(payload)
      await refreshBootstrap()
      setSelectedProjectId(created.projectId)
      resetConversationWorkspace('Nowa rozmowa')
      setProjectComposerOpen(false)
      setNewProject(createEmptyProjectForm())
      pushToast({
        type: 'success',
        title: 'Projekt utworzony',
        message: `Projekt „${created.name}” jest gotowy do pracy.`,
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udało się utworzyć projektu',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
    } finally {
      setCreatingProject(false)
    }
  }

  const handleSaveProject = async () => {
    if (!selectedProject) return

    const payload = normalizeProjectPayload(projectDraft)
    if (!payload.name) {
      pushToast({
        type: 'warning',
        title: 'Brakuje nazwy projektu',
        message: 'Projekt musi mieć nazwę, zanim zapiszesz zmiany.',
      })
      return
    }

    setSavingProject(true)
    try {
      const updated = await updateProject(selectedProject.id, payload)
      await refreshBootstrap()
      setSelectedProjectId(updated.projectId)
      pushToast({
        type: 'success',
        title: 'Projekt zapisany',
        message: `Zapisano ustawienia projektu „${updated.name}”.`,
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udało się zapisać projektu',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
    } finally {
      setSavingProject(false)
    }
  }

  const handleDeleteProject = async () => {
    if (!selectedProject) return

    if (!deleteArmed) {
      setDeleteArmed(true)
      pushToast({
        type: 'warning',
        title: 'Potwierdź usunięcie projektu',
        message: 'Kliknij „Usuń projekt” jeszcze raz, aby usunąć projekt i odpiąć rozmowy.',
      })
      return
    }

    setDeletingProject(true)
    try {
      const deleted = await deleteProject(selectedProject.id)
      await refreshBootstrap()
      setSelectedProjectId(null)
      resetConversationWorkspace('Nowa rozmowa')
      setDeleteArmed(false)
      pushToast({
        type: 'success',
        title: 'Projekt usunięty',
        message: `Projekt „${deleted.name}” został usunięty.`,
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udało się usunąć projektu',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
    } finally {
      setDeletingProject(false)
    }
  }

  const handleToggleScheduledTask = async (taskId: number, isActive: boolean) => {
    try {
      const next = await toggleScheduledTask(taskId, { isActive })
      setScheduledTasks((state) =>
        state.map((task) => (task.scheduledTaskId === taskId ? next : task)),
      )
      pushToast({
        type: 'success',
        title: isActive ? 'Zadanie aktywne' : 'Zadanie wstrzymane',
        message: isActive
          ? 'Zadanie cykliczne zostało ponownie aktywowane.'
          : 'Zadanie cykliczne zostało wyłączone.',
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udało się zmienić stanu zadania',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
    }
  }

  const handleDeleteScheduledTask = async (taskId: number) => {
    try {
      await deleteScheduledTask(taskId)
      setScheduledTasks((state) => state.filter((task) => task.scheduledTaskId !== taskId))
      pushToast({
        type: 'success',
        title: 'Zadanie usunięte',
        message: 'Zaplanowane zadanie zostało usunięte z projektu.',
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udało się usunąć zadania',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
    }
  }

  const projectComposer = (
    <div id="new-project-composer" className="rounded-xl border border-border-default bg-panel p-4">
      <div className="mb-4">
        <div className="label-eyebrow">NOWY PROJEKT</div>
        <div className="mt-2 text-sm leading-6 text-text-secondary">
          Projekt zapisuje opis, personę, routing providera i porządkuje rozmowy jako jeden
          kontekst pracy.
        </div>
      </div>

      <div className="space-y-4">
        <ProjectFieldShell label="NAZWA PROJEKTU">
          <input
            value={newProject.name}
            onChange={(event) =>
              setNewProject((state) => ({ ...state, name: event.target.value }))
            }
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void handleCreateProject()
              }
            }}
            placeholder="Np. Shorts AI Polska"
            className="h-11 w-full rounded-md border border-border-default bg-elevated px-3 text-sm text-text-primary outline-none transition focus:border-accent-primary"
          />
        </ProjectFieldShell>

        <ProjectFieldShell
          label="OPIS"
          description="Krótki cel projektu. To trafia do pamięci i pomaga agentowi dobierać styl pracy."
        >
          <textarea
            value={newProject.description}
            onChange={(event) =>
              setNewProject((state) => ({ ...state, description: event.target.value }))
            }
            rows={3}
            placeholder="Np. Kanał shortów o AI na rynek PL, z naciskiem na prosty język i mocny hook."
            className="w-full rounded-md border border-border-default bg-elevated px-3 py-3 text-sm text-text-primary outline-none transition placeholder:text-text-tertiary focus:border-accent-primary"
          />
        </ProjectFieldShell>

        <div className="grid gap-4 md:grid-cols-2">
          <ProjectFieldShell label="PERSONA">
            <select
              value={newProject.personaProfile}
              onChange={(event) =>
                setNewProject((state) => ({
                  ...state,
                  personaProfile: event.target.value as ProjectPersonaProfile,
                }))
              }
              className="h-11 w-full rounded-md border border-border-default bg-elevated px-3 text-sm text-text-primary outline-none transition focus:border-accent-primary"
            >
              {PERSONA_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <div className="mt-2 text-xs leading-5 text-text-tertiary">
              {PERSONA_OPTIONS.find((option) => option.value === newProject.personaProfile)?.hint}
            </div>
          </ProjectFieldShell>

          <ProjectFieldShell label="POSTAWA PROVIDERA">
            <select
              value={newProject.providerPosture}
              onChange={(event) =>
                setNewProject((state) => ({
                  ...state,
                  providerPosture: event.target.value as ProjectProviderPosture,
                }))
              }
              className="h-11 w-full rounded-md border border-border-default bg-elevated px-3 text-sm text-text-primary outline-none transition focus:border-accent-primary"
            >
              {PROVIDER_POSTURE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <div className="mt-2 text-xs leading-5 text-text-tertiary">
              {
                PROVIDER_POSTURE_OPTIONS.find(
                  (option) => option.value === newProject.providerPosture,
                )?.hint
              }
            </div>
          </ProjectFieldShell>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="text-xs leading-5 text-text-tertiary">
          Po utworzeniu projekt zostanie od razu aktywowany w czacie.
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setProjectComposerOpen(false)
              setNewProject(createEmptyProjectForm())
            }}
            className="rounded-md border border-border-default px-3 py-2 text-xs text-text-secondary transition hover:text-text-primary"
          >
            Anuluj
          </button>
          <button
            type="button"
            disabled={creatingProject}
            onClick={() => void handleCreateProject()}
            className={`rounded-md border px-3 py-2 text-xs transition ${
              creatingProject
                ? 'cursor-not-allowed border-border-default text-text-disabled'
                : 'border-accent-primary text-accent-primary hover:bg-accent-primary/10'
            }`}
          >
            {creatingProject ? 'Tworzenie…' : 'Utwórz projekt'}
          </button>
        </div>
      </div>
    </div>
  )

  const projectSettingsPanel = (
    <div id="project-settings-panel" className="rounded-xl border border-border-default bg-panel p-4">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="label-eyebrow">USTAWIENIA PROJEKTU</div>
          <div className="mt-2 text-sm leading-6 text-text-secondary">
            Projekt kontroluje kontekst rozmów, personę i routing modeli dla tego obszaru pracy.
          </div>
        </div>
        {selectedProject ? (
          <span className="rounded-pill border border-border-default px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-text-tertiary">
            {formatProjectStatus(selectedProject.status)}
          </span>
        ) : null}
      </div>

      {selectedProject ? (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-border-default bg-elevated px-3 py-3">
              <div className="label-eyebrow">SLUG</div>
              <div className="mt-2 text-sm text-text-primary">{selectedProject.slug}</div>
            </div>
            <div className="rounded-lg border border-border-default bg-elevated px-3 py-3">
              <div className="label-eyebrow">ROZMOWY</div>
              <div className="mt-2 text-sm text-text-primary">{selectedProjectConversationCount}</div>
            </div>
            <div className="rounded-lg border border-border-default bg-elevated px-3 py-3">
              <div className="label-eyebrow">PAMIĘĆ</div>
              <div className="mt-2 text-sm text-text-primary">
                {selectedProject.summary ? 'Jest podsumowanie' : 'Jeszcze pusto'}
              </div>
            </div>
          </div>

          <div className="mt-4 space-y-4">
            <ProjectFieldShell label="NAZWA PROJEKTU">
              <input
                value={projectDraft.name}
                onChange={(event) =>
                  setProjectDraft((state) => ({ ...state, name: event.target.value }))
                }
                placeholder="Nazwa projektu"
                className="h-11 w-full rounded-md border border-border-default bg-elevated px-3 text-sm text-text-primary outline-none transition focus:border-accent-primary"
              />
            </ProjectFieldShell>

            <ProjectFieldShell
              label="OPIS"
              description="To jest robocza instrukcja kontekstu projektu, nie marketingowy opis."
            >
              <textarea
                value={projectDraft.description}
                onChange={(event) =>
                  setProjectDraft((state) => ({ ...state, description: event.target.value }))
                }
                rows={4}
                placeholder="Opisz cel projektu, ograniczenia i preferowany efekt."
                className="w-full rounded-md border border-border-default bg-elevated px-3 py-3 text-sm text-text-primary outline-none transition placeholder:text-text-tertiary focus:border-accent-primary"
              />
            </ProjectFieldShell>

            <div className="grid gap-4 md:grid-cols-2">
              <ProjectFieldShell label="PERSONA">
                <select
                  value={projectDraft.personaProfile}
                  onChange={(event) =>
                    setProjectDraft((state) => ({
                      ...state,
                      personaProfile: event.target.value as ProjectPersonaProfile,
                    }))
                  }
                  className="h-11 w-full rounded-md border border-border-default bg-elevated px-3 text-sm text-text-primary outline-none transition focus:border-accent-primary"
                >
                  {PERSONA_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <div className="mt-2 text-xs leading-5 text-text-tertiary">
                  {PERSONA_OPTIONS.find((option) => option.value === projectDraft.personaProfile)?.hint}
                </div>
              </ProjectFieldShell>

              <ProjectFieldShell label="POSTAWA PROVIDERA">
                <select
                  value={projectDraft.providerPosture}
                  onChange={(event) =>
                    setProjectDraft((state) => ({
                      ...state,
                      providerPosture: event.target.value as ProjectProviderPosture,
                    }))
                  }
                  className="h-11 w-full rounded-md border border-border-default bg-elevated px-3 text-sm text-text-primary outline-none transition focus:border-accent-primary"
                >
                  {PROVIDER_POSTURE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <div className="mt-2 text-xs leading-5 text-text-tertiary">
                  {
                    PROVIDER_POSTURE_OPTIONS.find(
                      (option) => option.value === projectDraft.providerPosture,
                    )?.hint
                  }
                </div>
              </ProjectFieldShell>
            </div>
          </div>

          <div className="mt-4 rounded-lg border border-border-default bg-elevated/60">
            <button
              type="button"
              onClick={() => setScheduledTasksOpen((value) => !value)}
              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
            >
              <div>
                <div className="label-eyebrow">ZAPLANOWANE ZADANIA</div>
                <div className="mt-2 text-xs leading-5 text-text-tertiary">
                  Projektowe zadania cykliczne uruchamiane przez APScheduler.
                </div>
              </div>
              <span className="rounded-pill border border-border-default px-3 py-1 font-mono text-[11px] text-text-secondary">
                {scheduledTasks.length}
              </span>
            </button>
            {scheduledTasksOpen ? (
              <div className="border-t border-border-default px-4 py-4">
                {scheduledTasksLoading ? (
                  <div className="text-xs text-text-tertiary">Ładowanie zadań…</div>
                ) : scheduledTasks.length ? (
                  <div className="space-y-3">
                    {scheduledTasks.map((task) => (
                      <div
                        key={task.scheduledTaskId}
                        className="rounded-lg border border-border-default bg-panel px-3 py-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <div className="text-sm text-text-primary">{task.promptTemplate}</div>
                            <div className="mt-1 font-mono text-[11px] text-text-tertiary">
                              {task.cronSpec}
                            </div>
                            <div className="mt-2 text-xs text-text-tertiary">
                              Następny bieg:{' '}
                              {task.nextRunAt ? formatDateTime(task.nextRunAt) : 'scheduler wyliczy przy następnym ticku'}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => void handleToggleScheduledTask(task.scheduledTaskId, !task.isActive)}
                              className={`rounded-pill border px-3 py-1 text-[11px] transition ${
                                task.isActive
                                  ? 'border-accent-primary text-accent-primary hover:bg-accent-primary/10'
                                  : 'border-border-default text-text-secondary'
                              }`}
                            >
                              {task.isActive ? 'Aktywne' : 'Nieaktywne'}
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleDeleteScheduledTask(task.scheduledTaskId)}
                              className="rounded-pill border border-border-default px-3 py-1 text-[11px] text-text-secondary transition hover:text-sem-error"
                            >
                              Usuń
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-text-tertiary">
                    Brak zaplanowanych zadań dla tego projektu. Utwórz je z poziomu rozmowy.
                  </div>
                )}
              </div>
            ) : null}
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border-default pt-4">
            <div className="flex items-center gap-2 text-xs text-text-tertiary">
              <AlertTriangle size={14} className="text-sem-warning" />
              Usunięcie projektu odpina rozmowy, ale ich nie kasuje.
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={deletingProject}
                onClick={() => void handleDeleteProject()}
                className={`inline-flex items-center gap-2 rounded-md border px-3 py-2 text-xs transition ${
                  deleteArmed
                    ? 'border-sem-error text-sem-error hover:bg-sem-error/10'
                    : 'border-border-default text-text-secondary hover:text-sem-error'
                } ${
                  deletingProject ? 'cursor-not-allowed border-border-default text-text-disabled' : ''
                }`}
              >
                <Trash2 size={14} />
                {deletingProject ? 'Usuwanie…' : deleteArmed ? 'Potwierdź usunięcie' : 'Usuń projekt'}
              </button>
              <button
                type="button"
                disabled={savingProject}
                onClick={() => void handleSaveProject()}
                className={`inline-flex items-center gap-2 rounded-md border px-3 py-2 text-xs transition ${
                  savingProject
                    ? 'cursor-not-allowed border-border-default text-text-disabled'
                    : 'border-accent-primary text-accent-primary hover:bg-accent-primary/10'
                }`}
              >
                <Save size={14} />
                {savingProject ? 'Zapisywanie…' : 'Zapisz projekt'}
              </button>
            </div>
          </div>
        </>
      ) : (
        <div className="rounded-lg border border-dashed border-border-default bg-elevated/50 px-4 py-5 text-sm leading-6 text-text-secondary">
          Wybierz projekt z listy albo utwórz nowy, aby zarządzać jego opisem, personą,
          routingiem providera i powiązanym kontekstem rozmów.
        </div>
      )}
    </div>
  )

  const projectWorkspacePanel = (
    <div className="min-w-0 max-w-full space-y-4">
      <div className="shell-bento min-w-0 max-w-full rounded-lg p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <FolderOpenDot size={16} className="text-text-tertiary" />
            <span className="label-eyebrow">PROJEKTY</span>
          </div>
          <button
            type="button"
            onClick={() => {
              setProjectComposerOpen((value) => !value)
              if (projectComposerOpen) {
                setNewProject(createEmptyProjectForm())
              }
            }}
            className="inline-flex items-center gap-2 rounded-pill border border-border-default px-3 py-1.5 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
          >
            <Plus size={12} />
            Nowy
          </button>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => handleSelectProject(null)}
            className={`max-w-full truncate rounded-pill border px-3 py-1.5 text-xs transition ${
              selectedProjectId == null
                ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                : 'border-border-default text-text-secondary'
            }`}
          >
            Wszystkie
          </button>
          {projects.map((project) => (
            <button
              key={project.id}
              type="button"
              onClick={() => handleSelectProject(project.id)}
              className={`max-w-full truncate rounded-pill border px-3 py-1.5 text-xs transition ${
                selectedProjectId === project.id
                  ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                  : 'border-border-default text-text-secondary'
              }`}
            >
              {project.name}
            </button>
          ))}
        </div>
      </div>

      {projectComposerOpen ? projectComposer : null}
      {projectSettingsPanel}
    </div>
  )

  const composerHeader = chatControlsCollapsed ? null : (
    <div className="space-y-3">
      <div className="rounded-lg border border-border-default bg-panel/80 px-3 py-3 backdrop-blur-md">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <span className="label-eyebrow">USTAWIENIA ROZMOWY</span>
            <button
              type="button"
              onClick={() => setChatControlsCollapsed(true)}
              className="inline-flex items-center gap-2 rounded-pill border border-border-default px-3 py-1 text-[11px] text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
            >
              <ChevronsDown size={13} />
              Zwiń do trybu czytania
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {([
              { value: 'short', label: 'Krotka' },
              { value: 'medium', label: 'Standard' },
              { value: 'long', label: 'Rozbudowana' },
            ] as const).map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => void applyQuickSettingsPatch({ responseLength: option.value })}
                className={`rounded-pill border px-3 py-1 text-[11px] transition ${
                  activeQuickSettings.responseLength === option.value
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary hover:text-text-primary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {([
              { value: 'direct', label: 'Bezposredni' },
              { value: 'professional', label: 'Profesjonalny' },
              { value: 'supportive', label: 'Wspierajacy' },
              { value: 'critical', label: 'Krytyczny' },
            ] as const).map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => void applyQuickSettingsPatch({ responseTone: option.value })}
                className={`rounded-pill border px-3 py-1 text-[11px] transition ${
                  activeQuickSettings.responseTone === option.value
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary hover:text-text-primary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {([
              {
                key: 'memoryEnabled',
                label: 'Pamiec',
                active: activeQuickSettings.memoryEnabled,
                title: 'ON = Obsidian search/read/write w pętli narzędzi',
              },
              {
                key: 'fileEnabled',
                label: 'Pliki',
                active: activeQuickSettings.fileEnabled,
                title: 'ON = fs_* + PowerShell w pętli narzędzi',
              },
              {
                key: 'internetEnabled',
                label: 'Internet',
                active: activeQuickSettings.internetEnabled,
                title: 'ON = browser_open / tabs / learn w czacie',
              },
              {
                key: 'deepAnalysisEnabled',
                label: 'Deep analysis',
                active: activeQuickSettings.deepAnalysisEnabled,
                title: 'ON = więcej tur narzędzi i dokładniejsze sprawdzanie stanu',
              },
            ] as const).map((toggle) => (
              <button
                key={toggle.key}
                type="button"
                title={toggle.title}
                onClick={() =>
                  void applyQuickSettingsPatch({
                    [toggle.key]: !toggle.active,
                  } as Partial<ConversationQuickSettings>)
                }
                className={`rounded-pill border px-3 py-1 text-[11px] transition ${
                  toggle.active
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary hover:text-text-primary'
                }`}
              >
                {toggle.label}: {toggle.active ? 'ON' : 'OFF'}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="rounded-lg border border-border-default bg-panel/70 px-3 py-3 backdrop-blur-md">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="label-eyebrow">TRYB ATNOSA</div>
            <div className="mt-1 text-xs leading-5 text-text-tertiary">
              {chatWorkMode === 'local_chat' ? (
                <>
                  Rozmowa lokalna = Ollama + pętla narzędzi Atnosa. Przełączniki Pamięć / Pliki /
                  Internet / Deep analysis realnie włączają lub wyłączają narzędzia w tej turze.
                  Domyślne czatu ustawisz w{' '}
                  <a href="/settings?area=general&tab=chat" className="text-accent-primary underline">
                    Ustawienia → Ogólne → Czat
                  </a>
                  .
                </>
              ) : (
                <>
                  Domyslnie wybierz „Rozmowa lokalna”: Ollama odpowiada lokalnie, bez Codexu i bez chmury.
                  Polecenia projektu i delegacja lokalna tez nie wymagaja Codexu. Uzyj{' '}
                  <span className="type-mono-sm text-text-secondary">/ask</span> tylko do luznej rozmowy;
                  <span className="type-mono-sm text-text-secondary"> /atnos</span> lub{' '}
                  <span className="type-mono-sm text-text-secondary">/delegate</span> tworza audytowalny plan
                  w AI Delegation.
                </>
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
              {chatWorkMode === 'local_chat' ? (
                <>
                  <span className="rounded-pill border border-sem-success/50 bg-sem-success/10 px-2 py-0.5 text-sem-success">
                    Agent czatu: pętla narzędzi
                  </span>
                  <span
                    className={`rounded-pill border px-2 py-0.5 ${
                      ollamaWorker?.ready
                        ? 'border-sem-success/50 bg-sem-success/10 text-sem-success'
                        : 'border-sem-warning/50 bg-sem-warning/10 text-sem-warning'
                    }`}
                    title={ollamaWorker?.reason ?? 'Brak snapshotu Ollamy z backendu.'}
                  >
                    Ollama: {formatWorkerStateLabel(ollamaWorker?.status ?? '', Boolean(ollamaWorker?.ready))}
                  </span>
                  <span
                    className={`rounded-pill border px-2 py-0.5 ${
                      activeQuickSettings.memoryEnabled
                        ? 'border-sem-success/50 bg-sem-success/10 text-sem-success'
                        : 'border-border-default text-text-secondary'
                    }`}
                  >
                    Obsidian: {activeQuickSettings.memoryEnabled ? 'ON' : 'OFF'}
                  </span>
                  <span
                    className={`rounded-pill border px-2 py-0.5 ${
                      activeQuickSettings.fileEnabled
                        ? 'border-sem-success/50 bg-sem-success/10 text-sem-success'
                        : 'border-border-default text-text-secondary'
                    }`}
                  >
                    Pliki/PC: {activeQuickSettings.fileEnabled ? 'ON' : 'OFF'}
                  </span>
                  <span
                    className={`rounded-pill border px-2 py-0.5 ${
                      activeQuickSettings.internetEnabled
                        ? 'border-sem-success/50 bg-sem-success/10 text-sem-success'
                        : 'border-border-default text-text-secondary'
                    }`}
                    title="Przeglądarka w czacie (tabs/open/learn), niezależna od Browser AI Workers"
                  >
                    Przeglądarka czatu: {activeQuickSettings.internetEnabled ? 'ON' : 'OFF'}
                  </span>
                  <span
                    className={`rounded-pill border px-2 py-0.5 ${
                      activeQuickSettings.deepAnalysisEnabled
                        ? 'border-accent-primary/50 bg-accent-primary/10 text-accent-primary'
                        : 'border-border-default text-text-secondary'
                    }`}
                  >
                    Deep: {activeQuickSettings.deepAnalysisEnabled ? 'ON' : 'OFF'}
                  </span>
                </>
              ) : (
                <>
              <span
                className={`rounded-pill border px-2 py-0.5 ${
                  aiWorkersStatusError
                    ? 'border-sem-danger/50 bg-sem-danger/10 text-sem-danger'
                    : readyAiWorkerCount > 0
                      ? 'border-sem-success/50 bg-sem-success/10 text-sem-success'
                      : 'border-border-default text-text-secondary'
                }`}
              >
                AI Workers:{' '}
                {aiWorkersStatusError
                  ? 'blad statusu'
                  : aiWorkersStatusLoading && !aiWorkersStatus
                    ? 'sprawdzam'
                    : `${readyAiWorkerCount}/${aiWorkers.length || '-'} gotowe`}
              </span>
              <span
                className={`rounded-pill border px-2 py-0.5 ${
                  ollamaWorker?.ready
                    ? 'border-sem-success/50 bg-sem-success/10 text-sem-success'
                    : 'border-sem-warning/50 bg-sem-warning/10 text-sem-warning'
                }`}
                title={ollamaWorker?.reason ?? 'Brak snapshotu Ollamy z backendu.'}
              >
                Ollama: {formatWorkerStateLabel(ollamaWorker?.status ?? '', Boolean(ollamaWorker?.ready))}
              </span>
              <span
                className={`rounded-pill border px-2 py-0.5 ${
                  browserAiAvailable
                    ? 'border-sem-success/50 bg-sem-success/10 text-sem-success'
                    : 'border-sem-warning/50 bg-sem-warning/10 text-sem-warning'
                }`}
                title={
                  browserAiAvailable
                    ? readyBrowserAiWorkers.map((worker) => worker.label).join(', ')
                    : browserAiBlockReason
                }
              >
                Browser AI: {browserAiAvailable ? `${readyBrowserAiWorkers.length} gotowe` : 'zablokowane'}
              </span>
                </>
              )}
              <span className="rounded-pill border border-border-default px-2 py-0.5 text-text-tertiary">
                Guardrails: OAuth, publikacja, zakupy i live trading wymagaja zgody
              </span>
            </div>
            {chatWorkMode === 'local_chat' ? (
              <div className="mt-2 max-w-2xl text-[11px] leading-5 text-text-tertiary">
                Po każdej odpowiedzi zobaczysz listę użytych narzędzi. Wyłącz Pliki / Internet / Pamięć,
                jeśli chcesz zawęzić agenta tylko do rozmowy lub wybranej warstwy tworzenia.
              </div>
            ) : !browserAiAvailable ? (
              <div className="mt-2 max-w-2xl text-[11px] leading-5 text-text-tertiary">
                Browser AI jest ukryte przed delegacja, dopoki backend nie potwierdzi gotowego
                workera. Powod: {browserAiBlockReason}
              </div>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {CHAT_WORK_MODE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={chatWorkMode === option.value}
                title={option.hint}
                onClick={() => setChatWorkMode(option.value)}
                className={`rounded-pill border px-3 py-1 text-[11px] transition ${
                  chatWorkMode === option.value
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary hover:text-text-primary'
                }`}
              >
                {option.label}
              </button>
            ))}
            <button
              type="button"
              aria-pressed={delegationModeEnabled}
              onClick={() =>
                setChatWorkMode((current) =>
                  current === 'delegation' ? 'local_chat' : 'delegation',
                )
              }
              className={`rounded-pill border px-3 py-1 text-[11px] transition ${
                delegationModeEnabled
                  ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                  : 'border-border-default text-text-secondary hover:text-text-primary'
              }`}
            >
              Deleguj wszystko: {delegationModeEnabled ? 'ON' : 'OFF'}
            </button>
            {([
              { value: 'strict_local', label: 'Tylko lokalnie' },
              { value: 'allow_browser_ai', label: 'Browser AI po redakcji' },
            ] as const).map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={delegationPrivacyMode === option.value}
                disabled={option.value === 'allow_browser_ai' && !browserAiAvailable}
                title={
                  option.value === 'allow_browser_ai' && !browserAiAvailable
                    ? browserAiBlockReason
                    : undefined
                }
                onClick={() => setDelegationPrivacyMode(option.value)}
                className={`rounded-pill border px-3 py-1 text-[11px] transition ${
                  delegationPrivacyMode === option.value
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : option.value === 'allow_browser_ai' && !browserAiAvailable
                      ? 'cursor-not-allowed border-border-default text-text-disabled opacity-60'
                      : 'border-border-default text-text-secondary hover:text-text-primary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )

  return (
    <div
      data-testid="chat-route-shell"
      data-layout-mode={chatControlsCollapsed ? 'reading' : 'workspace'}
      className="shell-route flex min-h-0 min-w-0 max-w-full overflow-x-hidden"
      style={{
        height: isDesktopUp ? 'calc(100dvh - 56px)' : 'calc(100dvh - 131px)',
      }}
    >
      {!isWide && mobileConversationPanelOpen ? (
        <div className="fixed inset-0 z-modal flex" data-testid="mobile-conversation-panel">
          <button
            type="button"
            aria-label="Zamknij panel rozmów"
            className="absolute inset-0 bg-void/90"
            onClick={() => setMobileConversationPanelOpen(false)}
          />
          <aside className="relative z-sidebar-drawer flex h-full w-[min(92vw,380px)] flex-col border-r border-border-default bg-shell">
            <div className="flex items-center justify-between border-b border-border-default px-4 py-4">
              <div>
                <div className="label-eyebrow">CENTRUM ROZMÓW</div>
                <div className="mt-1 text-sm text-text-secondary">Projekty, historia i wyszukiwanie</div>
              </div>
              <button
                type="button"
                aria-label="Zamknij panel rozmów"
                onClick={() => setMobileConversationPanelOpen(false)}
                className="rounded-lg border border-border-default p-2 text-text-secondary hover:border-accent-primary hover:text-accent-primary"
              >
                <X size={17} />
              </button>
            </div>
            <div className="space-y-3 border-b border-border-default p-4">
              <button
                type="button"
                onClick={() => {
                  handleNewConversation()
                  setMobileConversationPanelOpen(false)
                }}
                className="flex w-full items-center justify-center gap-2 rounded-md border border-accent-primary bg-accent-primary px-3 py-3 text-sm font-semibold text-void shadow-glow-soft"
              >
                <Plus size={16} /> Nowa rozmowa
              </button>
              <label className="block">
                <span className="label-eyebrow">PROJEKT</span>
                <select
                  value={selectedProjectId ?? ''}
                  onChange={(event) => {
                    const value = event.target.value
                    handleSelectProject(value ? Number(value) : null)
                  }}
                  className="mt-2 h-11 w-full rounded-lg border border-border-default bg-elevated px-3 text-sm text-text-primary outline-none focus:border-accent-primary"
                >
                  <option value="">Wszystkie rozmowy</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>{project.name}</option>
                  ))}
                </select>
              </label>
              <div className="flex items-center gap-2 rounded-lg border border-border-default bg-elevated px-3">
                <Search size={14} className="text-text-tertiary" />
                <input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Szukaj rozmów..."
                  aria-label="Szukaj rozmów mobilnie"
                  className="h-11 min-w-0 flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-tertiary"
                />
              </div>
            </div>
            <div className="scroll-chrome flex-1 overflow-y-auto p-3">
              <div className="mb-2 flex items-center justify-between px-2">
                <span className="label-eyebrow">ROZMOWY</span>
                <span className="type-mono-sm text-text-tertiary">{filteredConversations.length}</span>
              </div>
              {filteredConversations.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border-default p-5 text-center text-sm text-text-secondary">
                  Brak rozmow dla wybranego filtra.
                </div>
              ) : (
                <div className="space-y-2">
                  {filteredConversations.map((conversation) => (
                    <button
                      key={conversation.id}
                      type="button"
                      onClick={() => {
                        setSelectedConversation(conversation.id, conversation.title)
                        setMobileConversationPanelOpen(false)
                      }}
                      className={`w-full rounded-md border px-3 py-3 text-left transition ${
                        selectedConversationId === conversation.id
                          ? 'border-accent-primary/50 bg-accent-primary/10'
                          : 'border-border-default bg-panel/70 hover:border-accent-primary/30'
                      }`}
                    >
                      <span className="block line-clamp-2 text-sm text-text-primary">
                        {resolveConversationTitle(conversation.title, conversation.summary)}
                      </span>
                      <span className="mt-2 block text-xs text-text-tertiary">
                        {formatRelativeDateTime(conversation.lastMessageAt ?? conversation.updatedAt)}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </aside>
        </div>
      ) : null}
      {!chatControlsCollapsed ? (
        <aside
          data-testid="conversation-sidebar"
          className="hidden w-[340px] shrink-0 border-r border-border-subtle bg-shell/90 backdrop-blur-xl xl:flex xl:flex-col"
        >
        <div className="border-b border-border-default px-4 py-4">
          <button
            type="button"
            onClick={handleNewConversation}
            data-testid="new-conversation-sidebar-button"
            className="shell-magnetic group flex w-full items-center justify-center gap-3 rounded-md border border-accent-primary bg-accent-primary px-4 py-3 text-sm font-medium text-void shadow-glow-soft transition hover:brightness-110"
            aria-label="Nowa rozmowa"
          >
            <Plus size={18} />
            <span>Nowa rozmowa</span>
            <Kbd>Ctrl+Shift+N</Kbd>
          </button>
          <button
            type="button"
            onClick={handleNewProjectlessConversation}
            data-testid="new-projectless-conversation-sidebar-button"
            className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-border-default bg-panel/80 px-3 py-2 text-xs font-medium text-text-secondary transition hover:border-accent-primary/40 hover:text-text-primary"
          >
            <Plus size={14} />
            Bez projektu
          </button>
          <div className="mt-3 shell-module flex items-center gap-2 rounded-lg px-3">
            <Search size={14} className="text-text-tertiary" />
            <input
              ref={conversationSearchInputRef}
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Szukaj rozmów…"
              className="h-11 flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-tertiary"
              aria-label="Szukaj rozmów"
            />
            <Kbd>Ctrl+Shift+F</Kbd>
          </div>
        </div>

        <div className="border-b border-border-default px-4 py-4">{projectWorkspacePanel}</div>

        <div className="scroll-chrome flex-1 overflow-y-auto px-3 py-3">
          <div className="mb-3 flex items-center justify-between px-2">
            <span className="label-eyebrow">ROZMOWY</span>
            <span className="type-mono-sm text-text-tertiary">
              {filteredConversations.length}
            </span>
          </div>
          {filteredConversations.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border-default bg-panel/60 px-4 py-6 text-center">
              <div className="type-body-sm text-text-secondary">
                {searchQuery.trim()
                  ? 'Brak rozmów pasujących do wyszukiwania.'
                  : 'Jeszcze nie masz rozmów.'}
              </div>
              <div className="mt-2 text-xs leading-5 text-text-tertiary">
                Kliknij „Nowa rozmowa” powyżej, żeby zacząć.
              </div>
            </div>
          ) : null}
          <div className="space-y-2">
            {filteredConversations.map((conversation) => (
              <div
                key={conversation.id}
                className={`shell-magnetic w-full rounded-md border px-3 py-3 text-left transition ${
                  selectedConversationId === conversation.id
                    ? 'border-accent-primary/50 bg-accent-primary/10'
                    : 'border-border-default bg-panel/70 hover:border-accent-primary/30 hover:bg-elevated/70'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <button
                    type="button"
                    onClick={() => setSelectedConversation(conversation.id, conversation.title)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <div className="line-clamp-2 text-sm text-text-primary">
                      {resolveConversationTitle(conversation.title, conversation.summary)}
                    </div>
                    {conversation.summary ? (
                      <div className="mt-1 line-clamp-2 text-xs leading-5 text-text-secondary">
                        {conversation.summary}
                      </div>
                    ) : null}
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-tertiary">
                      {conversation.projectId != null && projectNameById.get(conversation.projectId) ? (
                        <span className="rounded-pill border border-border-default px-2 py-0.5 text-[11px]">
                          {projectNameById.get(conversation.projectId)}
                        </span>
                      ) : null}
                      <span title={formatDateTime(conversation.lastMessageAt ?? conversation.updatedAt)}>
                        {formatRelativeDateTime(conversation.lastMessageAt ?? conversation.updatedAt)}
                      </span>
                    </div>
                  </button>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => handleTogglePinnedConversation(conversation.id)}
                      aria-label={
                        pinnedConversationIds.includes(conversation.id)
                          ? 'Odepnij rozmowe'
                          : 'Przypnij rozmowe'
                      }
                      className="rounded-sm border border-border-default p-1 text-text-secondary transition hover:border-accent-primary hover:text-text-primary"
                    >
                      {pinnedConversationIds.includes(conversation.id) ? (
                        <PinOff size={14} />
                      ) : (
                        <Pin size={14} />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleArchiveConversation(conversation.id)}
                      aria-label="Archiwizuj rozmowe"
                      className="rounded-sm border border-border-default p-1 text-text-secondary transition hover:border-sem-warning hover:text-sem-warning"
                    >
                      <Archive size={14} />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
        </aside>
      ) : null}

      <div className="flex min-w-0 max-w-full flex-1 flex-col overflow-x-hidden">
        {chatControlsCollapsed ? (
          <div className="border-b border-border-subtle bg-shell/90 px-4 py-3 backdrop-blur-md">
            <div className={`mx-auto flex w-full ${chatSurfaceMaxWidth} flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between`}>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-text-primary">
                  {selectedConversationTitle ?? 'Nowa rozmowa'}
                </div>
                <div className="mt-1 flex min-w-0 items-center gap-2 text-[11px] text-text-tertiary">
                  <span className="truncate">Projekt: {activeProjectContextLabel}</span>
                  <span aria-hidden>·</span>
                  <span className="truncate">{latestAssistantRouting ? 'Ostatnie wykonanie' : 'Model czatu'}: {activeModelLabel}</span>
                </div>
              </div>
              <div className="flex min-w-0 w-full flex-wrap items-center justify-start gap-2 sm:w-auto sm:shrink-0 sm:justify-end">
                {!isWide ? (
                  <button
                    type="button"
                    onClick={() => setMobileConversationPanelOpen(true)}
                    aria-label="Otworz projekty i rozmowy"
                    className="inline-flex items-center gap-2 rounded-pill border border-border-default px-3 py-1.5 text-xs text-text-secondary hover:border-accent-primary hover:text-accent-primary"
                  >
                    <PanelLeft size={14} /> Rozmowy
                  </button>
                ) : null}
                {shouldPollActiveCreativeVideoWorkflow ? (
                  <div
                    className="inline-flex items-center gap-2 rounded-pill border border-accent-primary/30 bg-accent-primary/8 px-3 py-1.5 text-[11px] text-text-secondary"
                    data-testid="creative-video-workflow-refresh-indicator"
                  >
                    <LoaderCircle size={13} className="animate-spin text-accent-primary" />
                    <span className="font-medium text-text-primary">
                      Short odswieza sie w tle
                    </span>
                    <span className="text-accent-primary">
                      {activeCreativeVideoWorkflowStatusLabel}
                    </span>
                    {activeCreativeVideoWorkflowPlatformLabel ? (
                      <span>{activeCreativeVideoWorkflowPlatformLabel}</span>
                    ) : null}
                    {activeCreativeVideoWorkflowNodeLabel ? (
                      <span>etap: {activeCreativeVideoWorkflowNodeLabel}</span>
                    ) : null}
                  </div>
                ) : null}
                <button
                  type="button"
                  onClick={handleNewConversation}
                  data-testid="new-conversation-primary-button"
                  aria-label="Nowa rozmowa"
                  className="inline-flex items-center gap-2 rounded-pill border border-accent-primary/45 bg-accent-primary/10 px-3 py-1.5 text-xs font-medium text-accent-primary transition hover:border-accent-primary hover:bg-accent-primary/15"
                >
                  <Plus size={14} />
                  Nowa
                </button>
                <button
                  type="button"
                  onClick={handleNewProjectlessConversation}
                  data-testid="new-projectless-conversation-primary-button"
                  className="inline-flex items-center gap-2 rounded-pill border border-border-default px-3 py-1.5 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
                >
                  <Plus size={14} />
                  Bez projektu
                </button>
                <button
                  type="button"
                  onClick={() => setChatControlsCollapsed(false)}
                  aria-label="Przywróć panele rozmowy"
                  aria-expanded={false}
                  data-testid="restore-chat-panels"
                  className="inline-flex items-center gap-2 rounded-pill border border-border-default px-3 py-1.5 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
                >
                  <ChevronsUp size={14} />
                  Panele
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="px-4 pt-4">
            <div className={`mx-auto w-full ${chatSurfaceMaxWidth}`}>
              <div className="shell-hero relative w-full p-4 sm:p-6">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="label-eyebrow">{activeProjectContext ? 'PROJECT COMMAND CHAT' : 'AKTYWNA ROZMOWA'}</div>
                  <h1 className="mt-2 type-h2 truncate text-text-primary">
                    {selectedConversationTitle ?? 'Nowa rozmowa'}
                  </h1>
                  <div className="mt-3 flex flex-wrap items-center gap-2 type-body-sm text-text-tertiary">
                    <span
                      className={`rounded-pill border px-3 py-1 type-body-sm ${
                        activeProjectContextAccent
                          ? 'border-accent-primary/40 bg-accent-primary/10 text-accent-primary'
                          : 'border-border-default text-text-secondary'
                      }`}
                    >
                      Projekt: {activeProjectContextLabel}
                    </span>
                    <span className="rounded-pill border border-border-default px-3 py-1 type-body-sm">
                      Persona: {activePersonaLabel}
                    </span>
                    <span className="rounded-pill border border-border-default px-3 py-1 type-body-sm">
                      Provider: {activeProviderLabel}
                    </span>
                    <span className="rounded-pill border border-border-default px-3 py-1 type-body-sm">
                      {latestAssistantRouting ? 'Ostatnie wykonanie' : 'Model czatu'}: {activeModelLabel}
                    </span>
                    <span
                      className={`rounded-pill border px-3 py-1 type-body-sm ${
                        strictLocalCloudBlocked
                          ? 'border-sem-warning/50 bg-sem-warning/10 text-sem-warning'
                          : 'border-border-default'
                      }`}
                      title={strictLocalCloudBlocked ? 'Cloud blocked by project privacy.' : undefined}
                    >
                      Privacy: {activePrivacyLabel}
                    </span>
                    {strictLocalCloudBlocked ? (
                      <span className="rounded-pill border border-sem-warning/50 bg-sem-warning/10 px-3 py-1 type-body-sm text-sem-warning">
                        Cloud blocked by project privacy
                      </span>
                    ) : null}
                    {activeRemoteRun ? (
                      <span className="rounded-pill border border-border-default px-3 py-1 type-body-sm">
                        Executor: {activeRemoteRun.routingTarget}
                        {activeRemoteWorker ? ` · ${activeRemoteWorker.label}` : ''}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="grid w-full min-w-0 max-w-full grid-cols-1 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center sm:justify-end">
                  {!isWide ? (
                    <button
                      type="button"
                      onClick={() => setMobileConversationPanelOpen(true)}
                      aria-label="Otworz projekty i rozmowy"
                      className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-border-default bg-panel/80 px-3 py-2.5 text-sm font-medium text-text-secondary hover:border-accent-primary/40 hover:text-text-primary sm:w-auto"
                    >
                      <PanelLeft size={15} /> Rozmowy
                    </button>
                  ) : null}
                  {loadingConversation ? (
                    <span className="inline-flex items-center gap-2 rounded-pill border border-accent-primary/30 bg-accent-primary/10 px-3 py-1.5 text-xs text-accent-primary">
                      <LoaderCircle size={14} className="animate-spin" aria-hidden />
                      {CHAT_STATUS.loadingConversation}
                    </span>
                  ) : null}
                  <button
                    type="button"
                    onClick={handleNewConversation}
                    data-testid="new-conversation-primary-button"
                    aria-label="Nowa rozmowa"
                    className="shell-magnetic inline-flex w-full items-center justify-center gap-2 rounded-md border border-accent-primary bg-accent-primary px-4 py-2.5 text-sm font-medium text-void shadow-glow-soft transition hover:brightness-110 sm:w-auto"
                  >
                    <Plus size={16} />
                    <span>Nowa rozmowa</span>
                    <Kbd>Ctrl+Shift+N</Kbd>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setWorkspaceDetailsOpen(false)
                      setChatControlsCollapsed(true)
                    }}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-border-default bg-panel/80 px-3 py-2.5 text-sm font-medium text-text-secondary transition hover:border-accent-primary/40 hover:text-text-primary sm:w-auto"
                  >
                    <ChevronsDown size={15} />
                    <span>Tryb czytania</span>
                  </button>
                  <button
                    type="button"
                    onClick={handleNewProjectlessConversation}
                    data-testid="new-projectless-conversation-primary-button"
                    className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-border-default bg-panel/80 px-3 py-2.5 text-sm font-medium text-text-secondary transition hover:border-accent-primary/40 hover:text-text-primary sm:w-auto"
                  >
                    <Plus size={15} />
                    <span>Bez projektu</span>
                  </button>
                </div>
              </div>

              {activeRemoteRun?.fallbackReason ? (
                <div className="mt-2 text-xs text-text-tertiary">
                  fallback: {activeRemoteRun.fallbackReason}
                </div>
              ) : null}

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <span className="label-eyebrow">JAKOSC</span>
                {QUALITY_PROFILE_OPTIONS.map((option) => {
                  const isActive = option.value === activeQualityProfile
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() =>
                        setQualityProfilesByConversation((current) => ({
                          ...current,
                          [qualityProfileKey]: option.value,
                        }))
                      }
                      className={`rounded-pill border px-3 py-1 text-xs transition ${
                        isActive
                          ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                          : 'border-border-default text-text-secondary hover:border-accent-primary/40 hover:text-text-primary'
                      }`}
                    >
                      {option.label}
                    </button>
                  )
                })}
                <span className="hidden md:inline text-xs leading-5 text-text-tertiary">
                  {activeQualityProfileHint}
                </span>
              </div>

              {shouldPollActiveCreativeVideoWorkflow ? (
                <div
                  className="mt-4 flex flex-wrap items-center gap-2 rounded-md border border-accent-primary/35 bg-accent-primary/8 px-4 py-3 text-xs text-text-secondary"
                  data-testid="creative-video-workflow-refresh-indicator"
                >
                  <LoaderCircle size={14} className="animate-spin text-accent-primary" />
                  <span className="font-medium text-text-primary">
                    Short odswieza sie w tle
                  </span>
                  <span className="rounded-pill border border-accent-primary/30 px-2 py-0.5 text-[11px] text-accent-primary">
                    {activeCreativeVideoWorkflowStatusLabel}
                  </span>
                  {activeCreativeVideoWorkflowPlatformLabel ? (
                    <span className="rounded-pill border border-border-default px-2 py-0.5 text-[11px]">
                      {activeCreativeVideoWorkflowPlatformLabel}
                    </span>
                  ) : null}
                  {activeCreativeVideoWorkflowNodeLabel ? (
                    <span className="text-text-tertiary">
                      etap: {activeCreativeVideoWorkflowNodeLabel}
                    </span>
                  ) : null}
                  <span className="text-text-tertiary">
                    Status rozmowy odswieza sie automatycznie co kilka sekund.
                  </span>
                </div>
              ) : null}
            </div>
          </div>
          </div>
        )}

        <div
          data-testid="chat-conversation-surface"
          className="flex min-h-0 flex-1 flex-col"
        >
          {!backendReachable ||
          websocketStatus === 'connecting' ||
          websocketStatus === 'reconnecting' ||
          (chatWorkMode === 'local_chat' && ollamaWorker != null && !ollamaWorker.ready) ? (
            <div
              data-testid="chat-connection-banner"
              className="border-b border-border-subtle px-4 py-2"
              role="status"
            >
              <div className="mx-auto flex w-full max-w-full flex-wrap items-center gap-2 type-body-sm">
                {!backendReachable && backendCheckedAt == null ? (
                  <span className="rounded-pill border border-accent-primary/35 bg-accent-primary/10 px-2.5 py-1 text-accent-primary">
                    {CHAT_STATUS.connecting}
                  </span>
                ) : !backendReachable ? (
                  <>
                    <span className="rounded-pill border border-sem-warning/40 bg-sem-warning/10 px-2.5 py-1 text-sem-warning">
                      {CHAT_STATUS.apiUnavailable}
                    </span>
                    <button
                      type="button"
                      className="rounded-md border border-border-default px-2.5 py-1 text-text-secondary hover:border-accent-primary hover:text-accent-primary"
                      onClick={() => {
                        void refreshBootstrap().catch(() => undefined)
                      }}
                    >
                      {CHAT_STATUS.retry}
                    </button>
                  </>
                ) : websocketStatus === 'connecting' || websocketStatus === 'reconnecting' ? (
                  <span className="rounded-pill border border-accent-primary/35 bg-accent-primary/10 px-2.5 py-1 text-accent-primary">
                    {CHAT_STATUS.connecting}
                  </span>
                ) : chatWorkMode === 'local_chat' && ollamaWorker != null && !ollamaWorker.ready ? (
                  <span
                    className="rounded-pill border border-sem-warning/40 bg-sem-warning/10 px-2.5 py-1 text-sem-warning"
                    title={ollamaWorker.reason ?? undefined}
                  >
                    {CHAT_STATUS.ollamaUnavailable}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}
          <ChatStream
            messages={messages}
            queuedMessages={queuedMessagePreviews}
            onRemoveQueuedMessage={handleRemoveQueuedMessage}
            agentStage={agentStage}
            agentActivity={agentActivity}
            activeWorkStatus={activeCreativeVideoWorkStatus}
            showTypingIndicator={
              activeTurn != null && activeTurn.conversationId === selectedConversationId
            }
            generating={activeTurn != null}
            loadingConversation={loadingConversation}
            hasActiveConversation={selectedConversationId != null}
            inputFocusSignal={composerFocusNonce}
            inputValue={activeDraft}
            onInputValueChange={setActiveDraft}
            inputFilesEnabled={activeQuickSettings.fileEnabled}
            inputHeader={composerHeader}
            onSubmit={handleSend}
            onCancelGeneration={cancelActiveGeneration}
            onRetryRuntimeError={handleRetryRuntimeError}
            onRetryUserMessage={handleRetryOptimisticMessage}
            onQuestionAnswered={refreshConversationDetail}
            onResolveDecision={handleResolveBriefingDecision}
            onRefreshBriefing={async () => {
              await triggerBriefing()
            }}
            onImprovePrompt={handleImprovePrompt}
            onNewConversation={handleNewConversation}
            maxWidthClass={chatSurfaceMaxWidth}
          />
        </div>

        {!chatControlsCollapsed ? (
          <section
            data-testid="chat-workspace-details"
            className="shrink-0 border-t border-border-default bg-shell px-4 py-2"
          >
            <div className={`mx-auto w-full ${chatSurfaceMaxWidth}`}>
              <button
                type="button"
                onClick={() => setWorkspaceDetailsOpen((current) => !current)}
                aria-expanded={workspaceDetailsOpen}
                aria-controls="chat-workspace-details-content"
                className="flex w-full items-center justify-between gap-3 rounded-lg border border-border-default bg-panel/75 px-3 py-2 text-left text-xs font-medium text-text-secondary transition hover:border-accent-primary/50 hover:text-accent-primary"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <FolderOpenDot size={14} className="shrink-0" />
                  <span className="truncate">Szczegóły projektu i Project Command</span>
                </span>
                {workspaceDetailsOpen ? (
                  <ChevronsDown size={14} className="shrink-0" aria-hidden />
                ) : (
                  <ChevronsUp size={14} className="shrink-0" aria-hidden />
                )}
              </button>

              {workspaceDetailsOpen ? (
                <div
                  id="chat-workspace-details-content"
                  data-testid="chat-workspace-details-content"
                  className="scroll-chrome mt-2 max-h-[30dvh] space-y-4 overflow-y-auto overscroll-contain pb-2 sm:max-h-[34dvh] xl:max-h-[40dvh]"
                >
                  {!isWide ? projectWorkspacePanel : null}
                  <ProjectCommandPanel
                    projectId={activeProjectContext?.id ?? null}
                    projectName={activeProjectContextLabel}
                    refreshKey={projectCommandRefreshKey}
                    onCommand={(command) => {
                      void submitProjectCommand(command, command)
                    }}
                  />
                </div>
              ) : null}
            </div>
          </section>
        ) : null}
      </div>

      {!chatControlsCollapsed ? (
        <div data-testid="chat-pipeline-rail">
          <ContentPipeline snapshot={pipeline} showPreviewOnly={false} />
        </div>
      ) : null}

      <ApprovalGate
        snapshot={pipeline}
        onApprove={async (workItemId) => {
          const activeJobId = currentJob?.id
          if (activeJobId) {
            setAwaitingApproval(activeJobId, false)
          }
          try {
            const next = await updatePipelineApproval(workItemId, { action: 'approve_review' })
            setPipelineSnapshot(next)
            pushToast({
              type: 'success',
              title: 'Preview zatwierdzony',
              message: 'Pakiet publikacji został oznaczony jako approved.',
            })
          } catch (error) {
            if (activeJobId) {
              setAwaitingApproval(activeJobId, true)
            }
            pushToast({
              type: 'error',
              title: 'Nie udało się zatwierdzić publikacji',
              message: error instanceof Error ? error.message : 'Spróbuj ponownie.',
            })
            throw error
          }
        }}
        onReject={async (workItemId) => {
          const activeJobId = currentJob?.id
          if (activeJobId) {
            setAwaitingApproval(activeJobId, false)
          }
          try {
            const next = await updatePipelineApproval(workItemId, { action: 'request_changes' })
            setPipelineSnapshot(next)
            pushToast({
              type: 'warning',
              title: 'Preview cofnięty',
              message: 'Pakiet wrócił do statusu wymagającego zmian.',
            })
          } catch (error) {
            if (activeJobId) {
              setAwaitingApproval(activeJobId, true)
            }
            pushToast({
              type: 'error',
              title: 'Nie udało się odrzucić publikacji',
              message: error instanceof Error ? error.message : 'Spróbuj ponownie.',
            })
            throw error
          }
        }}
      />
    </div>
  )
}

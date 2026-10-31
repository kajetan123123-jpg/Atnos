import { Copy } from 'lucide-react'
import { motion } from 'framer-motion'
import { LoaderCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { ConversationMessage } from '../types/message'
import type { BriefingPayload } from '../types/briefing'
import { formatClock } from '../lib/format'
import {
  formatAgenticLoopStatusLabel,
  formatToolCallStatusLabel,
  getAgentToolStateLabel,
  getAgentToolNoun,
  isToolCallInProgress,
} from '../lib/agentToolLabels'
import { CHAT_STATUS } from '../lib/chatStatusCopy'
import { DUR, EASE, VARIANTS } from '../lib/motion'
import { ChatMarkdownBody } from '../lib/renderChatMarkdown'
import { getShikiHighlighter, getShikiLanguage } from '../lib/shiki'
import {
  answerQuestion,
  approveActionDraft,
  buildAgentArtifactDownloadUrl,
  executeActionDraft,
  rejectActionDraft,
} from '../lib/api'
import {
  AssistantQuestionCard,
  type AssistantQuestion,
} from './AssistantQuestionCard'
import { BriefingCard } from './BriefingCard'
import { ProductionOrderStatusCard } from './ProductionOrderStatusCard'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { useChatStore } from '../stores/chatStore'
import { useToastStore } from '../stores/toastStore'

type ParsedSegment =
  | { type: 'text'; value: string }
  | { type: 'code'; value: string; language: string }

interface ProviderUsagePreview {
  providerKey: string
  providerLabel: string
  modelKey: string | null
  routingReason: string | null
  providerPosture: string | null
  taskType: string | null
  routerTaskType: string | null
  routerModel: string | null
  actualRuntimeModel: string | null
}

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

function readMetadataString(metadata: Record<string, unknown> | null | undefined, key: string) {
  const value = metadata?.[key]
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function extractProviderUsagePreview(
  metadata: Record<string, unknown> | null | undefined,
): ProviderUsagePreview | null {
  const providerKey = readMetadataString(metadata, 'selected_provider_key')
  if (!providerKey) {
    return null
  }
  return {
    providerKey,
    providerLabel: PROVIDER_DISPLAY_NAMES[providerKey] ?? providerKey,
    modelKey: readMetadataString(metadata, 'selected_model_key'),
    routingReason: readMetadataString(metadata, 'routing_reason'),
    providerPosture: readMetadataString(metadata, 'provider_posture'),
    taskType: readMetadataString(metadata, 'task_type'),
    routerTaskType: readMetadataString(metadata, 'llm_router_task_type'),
    routerModel: readMetadataString(metadata, 'llm_router_model'),
    actualRuntimeModel: readMetadataString(metadata, 'actual_runtime_model_name'),
  }
}

function extractSkillExecution(metadata: Record<string, unknown> | null | undefined) {
  const payload = metadata?.atnos_skill_execution
  if (!payload || typeof payload !== 'object') return null
  const row = payload as Record<string, unknown>
  const selectedSkills = Array.isArray(row.selectedSkills)
    ? row.selectedSkills.filter((item): item is string => typeof item === 'string')
    : []
  if (selectedSkills.length === 0) return null
  const loaded = Array.isArray(row.loadedInstructionSkills)
    ? row.loadedInstructionSkills.filter((item): item is string => typeof item === 'string')
    : []
  const missing = Array.isArray(row.missingInstructionSkills)
    ? row.missingInstructionSkills.filter((item): item is string => typeof item === 'string')
    : []
  return {
    selectedSkills,
    loaded,
    missing,
    status: typeof row.instructionStatus === 'string' ? row.instructionStatus : 'unknown',
  }
}

const BRIEFING_SECTION_KIND_BY_TITLE: Record<string, BriefingPayload['sections'][number]['kind']> = {
  'decyzje i commitments': 'decisions',
  github: 'github',
  linear: 'linear',
  'pamiec projektu': 'project_memory',
  'project memory': 'project_memory',
}

function inferBriefingSectionKind(title: string): BriefingPayload['sections'][number]['kind'] {
  return BRIEFING_SECTION_KIND_BY_TITLE[title.trim().toLowerCase()] ?? 'overview'
}

function parseBriefingContentFallback(
  content: string,
  createdAt?: string,
): BriefingPayload | null {
  const lines = content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

  if (lines.length < 2) {
    return null
  }

  const sections: BriefingPayload['sections'] = []
  let currentSection: BriefingPayload['sections'][number] | null = null
  let matchedStructuredItem = false

  for (const line of lines) {
    if (!line.startsWith('- ')) {
      const separatorIndex = line.indexOf(':')
      if (separatorIndex <= 0) {
        currentSection = null
        continue
      }
      const title = line.slice(0, separatorIndex).trim()
      const summary = line.slice(separatorIndex + 1).trim()
      if (!summary) {
        currentSection = null
        continue
      }
      currentSection = {
        kind: inferBriefingSectionKind(title),
        title,
        summary,
        items: [],
      }
      sections.push(currentSection)
      continue
    }

    if (!currentSection) {
      continue
    }

    const match = line.match(/^- \[(HIGH|MEDIUM|LOW)\]\s+([^:]+):\s+(.+)$/i)
    if (!match) {
      continue
    }

    matchedStructuredItem = true
    currentSection.items.push({
      label: match[2].trim(),
      detail: match[3].trim(),
      urgency:
        match[1].toUpperCase() === 'HIGH'
          ? 'high'
          : match[1].toUpperCase() === 'MEDIUM'
            ? 'medium'
            : 'low',
      actionUrl: null,
      metadata: {},
    })
  }

  if (!matchedStructuredItem || sections.length === 0) {
    return null
  }

  const visibleSections = sections.filter((section) => section.items.length > 0)
  if (visibleSections.length === 0) {
    return null
  }

  return {
    generatedAt: createdAt ?? new Date().toISOString(),
    projectId: null,
    sections: visibleSections,
  }
}

function getSpeakerLabel(message: ConversationMessage, isBriefing = false) {
  if (
    isBriefing ||
    message.metadataJson?.kind === 'briefing' ||
    message.metadataJson?.kind === 'briefing_card'
  ) {
    return 'BRIEFING'
  }
  if (message.role === 'assistant') {
    return 'ATNOS'
  }
  if (message.role === 'user') {
    return 'TY'
  }
  if (message.role === 'tool') {
    return 'NARZEDZIE'
  }
  return 'SYSTEM'
}

function extractBriefingPayload(
  metadata: Record<string, unknown> | null | undefined,
  content?: string,
  createdAt?: string,
): BriefingPayload | null {
  if (!metadata || typeof metadata !== 'object') {
    return content ? parseBriefingContentFallback(content, createdAt) : null
  }
  const kind = (metadata as { kind?: unknown }).kind
  if (kind === 'briefing') {
    const payload = (metadata as { payload?: unknown }).payload
    if (!payload || typeof payload !== 'object') {
      return null
    }
    return normalizeBriefingPayload(payload as Record<string, unknown>)
  }
  if (kind === 'briefing_card') {
    const legacy = (metadata as { briefing_card?: unknown }).briefing_card
    if (!legacy || typeof legacy !== 'object') {
      return null
    }
    return {
      generatedAt: String((legacy as { generated_at?: unknown }).generated_at ?? ''),
      projectId:
        (legacy as { project_id?: unknown }).project_id == null
          ? null
          : Number((legacy as { project_id?: unknown }).project_id),
      sections: [
        {
          kind: 'overview',
          title: String((legacy as { title?: unknown }).title ?? 'Briefing'),
          summary: 'Legacy briefing payload.',
          items: Array.isArray((legacy as { items?: unknown }).items)
            ? ((legacy as { items?: unknown }).items as Array<Record<string, unknown>>).map((item) => ({
                label: String(item.label ?? 'Pozycja'),
                detail: String(item.detail ?? item.value ?? ''),
                urgency: 'low',
                actionUrl: null,
                metadata: {
                  legacy_kind: item.kind ?? 'overview',
                },
              }))
            : [],
        },
      ],
    }
  }
  return content ? parseBriefingContentFallback(content, createdAt) : null
}

function normalizeBriefingPayload(payload: Record<string, unknown>): BriefingPayload {
  return {
    generatedAt: String(payload.generated_at ?? payload.generatedAt ?? ''),
    projectId:
      payload.project_id == null && payload.projectId == null
        ? null
        : Number(payload.project_id ?? payload.projectId),
    sections: Array.isArray(payload.sections)
      ? payload.sections
          .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
          .map((section) => ({
            kind: String(section.kind ?? 'overview'),
            title: String(section.title ?? 'Briefing'),
            summary: String(section.summary ?? ''),
            items: Array.isArray(section.items)
              ? section.items
                  .filter(
                    (item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object',
                  )
                  .map((item) => ({
                    label: String(item.label ?? ''),
                    detail: String(item.detail ?? ''),
                    urgency:
                      item.urgency === 'high' || item.urgency === 'medium'
                        ? (item.urgency as 'high' | 'medium')
                        : 'low',
                    actionUrl:
                      item.action_url == null && item.actionUrl == null
                        ? null
                        : String(item.action_url ?? item.actionUrl),
                    metadata:
                      item.metadata && typeof item.metadata === 'object'
                        ? (item.metadata as Record<string, unknown>)
                        : {},
                  }))
              : [],
          }))
      : [],
  }
}

function extractAssistantQuestion(
  metadata: Record<string, unknown> | null | undefined,
): AssistantQuestion | null {
  if (!metadata || typeof metadata !== 'object') {
    return null
  }
  const kind = (metadata as { kind?: unknown }).kind
  if (kind !== 'question_choice') {
    return null
  }
  const prompt = (metadata as { prompt?: unknown }).prompt
  const optionsRaw = (metadata as { options?: unknown }).options
  const questionId = (metadata as { question_id?: unknown }).question_id
  if (typeof prompt !== 'string' || !Array.isArray(optionsRaw) || typeof questionId !== 'string') {
    return null
  }
  const options: AssistantQuestion['options'] = []
  for (const entry of optionsRaw) {
    if (!entry || typeof entry !== 'object') {
      continue
    }
    const id = (entry as { id?: unknown }).id
    const label = (entry as { label?: unknown }).label
    if (typeof id !== 'string' || typeof label !== 'string') {
      continue
    }
    const description = (entry as { description?: unknown }).description
    options.push({
      id,
      label,
      description: typeof description === 'string' ? description : undefined,
    })
  }
  if (options.length === 0) {
    return null
  }
  const multiSelect = Boolean((metadata as { multi_select?: unknown }).multi_select)
  const required = (metadata as { required?: unknown }).required
  const helperText = (metadata as { helper_text?: unknown }).helper_text
  return {
    questionId,
    prompt,
    options,
    multiSelect,
    required: typeof required === 'boolean' ? required : true,
    helperText: typeof helperText === 'string' ? helperText : undefined,
  }
}

function extractResolvedSelections(
  metadata: Record<string, unknown> | null | undefined,
): string[] | undefined {
  if (!metadata || typeof metadata !== 'object') return undefined
  const resolved = (metadata as { resolved_selected_ids?: unknown }).resolved_selected_ids
  if (!Array.isArray(resolved)) return undefined
  const ids = resolved.filter((value): value is string => typeof value === 'string')
  return ids.length > 0 ? ids : undefined
}

interface CodeDiffFilePreview {
  path: string
  diff: string
  originalContent?: string
  proposedContent?: string
}

interface CodeDiffPreview {
  title: string
  summary: string
  files: CodeDiffFilePreview[]
  actionDraftId?: number
  draftStatus?: string
}

interface RuntimeErrorPreview {
  failureType: string
  providerKey: string
  modelKey: string
  runtimeModelName?: string
  userTitle: string
  userDetail: string
  retryable: boolean
  canFallback: boolean
  suggestedActions: string[]
  diagnostics?: Record<string, unknown>
}

interface CreativeVideoStatusPreview {
  workflowKey: string
  platformHint?: string
  qualityProfile?: string
  workflowStatus?: string
  currentNode?: string
  approvalRequired: boolean
  blocked: boolean
  blocker?: string
  nextStep?: string
  artifactCount: number
  qualityRun?: CreativeVideoQualityPreview
}

interface CreativeVideoQualityPreview {
  status: string
  score?: number
  reportPath?: string
  evidenceCount?: number
}

interface OptimisticMessageState {
  optimistic: boolean
  state: 'pending' | 'failed'
  errorMessage?: string
}

interface QuestionSubmitState {
  status: 'idle' | 'submitting' | 'submitted' | 'error'
  selectedIds: string[]
  errorMessage?: string
  optimisticMessageId?: string
}

function extractCodeDiffPreview(
  metadata: Record<string, unknown> | null | undefined,
): CodeDiffPreview | null {
  if (!metadata || typeof metadata !== 'object' || metadata.kind !== 'code_diff') {
    return null
  }
  const filesRaw = Array.isArray(metadata.files) ? metadata.files : []
  const files: CodeDiffFilePreview[] = []
  for (const entry of filesRaw) {
    if (!entry || typeof entry !== 'object') {
      continue
    }
    const path = (entry as { path?: unknown }).path
    const diff = (entry as { diff?: unknown }).diff
    if (typeof path !== 'string' || typeof diff !== 'string') {
      continue
    }
    files.push({
      path,
      diff,
      originalContent:
        typeof (entry as { original_content?: unknown }).original_content === 'string'
          ? ((entry as { original_content?: string }).original_content as string)
          : undefined,
      proposedContent:
        typeof (entry as { proposed_content?: unknown }).proposed_content === 'string'
          ? ((entry as { proposed_content?: string }).proposed_content as string)
          : undefined,
    })
  }
  return {
    title:
      typeof metadata.title === 'string' && metadata.title.trim()
        ? metadata.title
        : 'Code change preview',
    summary:
      typeof metadata.summary === 'string' && metadata.summary.trim()
        ? metadata.summary
        : 'ATNOS przygotowal diff do zatwierdzenia.',
    files,
    actionDraftId:
      typeof metadata.action_draft_id === 'number'
        ? metadata.action_draft_id
        : typeof metadata.action_draft_id === 'string'
          ? Number(metadata.action_draft_id)
          : undefined,
    draftStatus:
      typeof metadata.draft_status === 'string' ? metadata.draft_status : undefined,
  }
}

function extractRuntimeErrorPreview(
  metadata: Record<string, unknown> | null | undefined,
): RuntimeErrorPreview | null {
  if (!metadata || typeof metadata !== 'object') {
    return null
  }
  const runtimeError = (metadata as { runtime_error?: unknown }).runtime_error
  if (!runtimeError || typeof runtimeError !== 'object') {
    return null
  }
  const failureType = (runtimeError as { failure_type?: unknown }).failure_type
  const providerKey = (runtimeError as { provider_key?: unknown }).provider_key
  const modelKey = (runtimeError as { model_key?: unknown }).model_key
  const userTitle = (runtimeError as { user_title?: unknown }).user_title
  const userDetail = (runtimeError as { user_detail?: unknown }).user_detail
  if (
    typeof failureType !== 'string' ||
    typeof providerKey !== 'string' ||
    typeof modelKey !== 'string' ||
    typeof userTitle !== 'string' ||
    typeof userDetail !== 'string'
  ) {
    return null
  }
  const runtimeModelName = (runtimeError as { runtime_model_name?: unknown }).runtime_model_name
  const suggestedActionsRaw = (runtimeError as { suggested_actions?: unknown }).suggested_actions
  const diagnostics = (runtimeError as { diagnostics?: unknown }).diagnostics
  return {
    failureType,
    providerKey,
    modelKey,
    runtimeModelName: typeof runtimeModelName === 'string' ? runtimeModelName : undefined,
    userTitle,
    userDetail,
    retryable: Boolean((runtimeError as { retryable?: unknown }).retryable),
    canFallback: Boolean((runtimeError as { can_fallback?: unknown }).can_fallback),
    suggestedActions: Array.isArray(suggestedActionsRaw)
      ? suggestedActionsRaw.filter((value): value is string => typeof value === 'string')
      : [],
    diagnostics:
      diagnostics && typeof diagnostics === 'object'
        ? (diagnostics as Record<string, unknown>)
        : undefined,
  }
}

function extractCreativeVideoStatusPreview(
  metadata: Record<string, unknown> | null | undefined,
): CreativeVideoStatusPreview | null {
  if (!metadata || typeof metadata !== 'object') {
    return null
  }
  const raw = (metadata as { creative_video_operator?: unknown }).creative_video_operator
  if (!raw || typeof raw !== 'object') {
    return null
  }
  const workflowKey = (raw as { workflow_key?: unknown }).workflow_key
  if (typeof workflowKey !== 'string' || workflowKey.trim().length === 0) {
    return null
  }
  const workflowState = (raw as { workflow_state?: unknown }).workflow_state
  const artifactCount = (raw as { artifact_count?: unknown }).artifact_count
  const shortAgent = (raw as { short_agent?: unknown }).short_agent
  return {
    workflowKey,
    platformHint:
      typeof (raw as { platform_hint?: unknown }).platform_hint === 'string'
        ? ((raw as { platform_hint?: string }).platform_hint as string)
        : undefined,
    qualityProfile:
      typeof (raw as { quality_profile?: unknown }).quality_profile === 'string'
        ? ((raw as { quality_profile?: string }).quality_profile as string)
        : undefined,
    workflowStatus:
      workflowState && typeof workflowState === 'object' && typeof (workflowState as { status?: unknown }).status === 'string'
        ? ((workflowState as { status?: string }).status as string)
        : undefined,
    currentNode:
      workflowState && typeof workflowState === 'object' && typeof (workflowState as { current_node?: unknown }).current_node === 'string'
        ? ((workflowState as { current_node?: string }).current_node as string)
        : undefined,
    approvalRequired: Boolean((raw as { approval_required?: unknown }).approval_required),
    blocked: Boolean((raw as { blocked?: unknown }).blocked),
    blocker:
      typeof (raw as { blocker?: unknown }).blocker === 'string'
        ? ((raw as { blocker?: string }).blocker as string)
        : undefined,
    nextStep:
      typeof (raw as { next_step?: unknown }).next_step === 'string'
        ? ((raw as { next_step?: string }).next_step as string)
        : undefined,
    artifactCount: typeof artifactCount === 'number' ? artifactCount : 0,
    qualityRun: extractCreativeVideoQualityRun(
      (raw as { quality_run?: unknown }).quality_run ??
        (shortAgent && typeof shortAgent === 'object'
          ? (shortAgent as { quality_run?: unknown }).quality_run
          : undefined),
    ),
  }
}

function extractCreativeVideoQualityRun(raw: unknown): CreativeVideoQualityPreview | undefined {
  if (!raw || typeof raw !== 'object') {
    return undefined
  }
  const status = (raw as { status?: unknown }).status
  if (typeof status !== 'string' || !status.trim()) {
    return undefined
  }
  const score = (raw as { score?: unknown }).score
  const reportPath = (raw as { report_path?: unknown; reportPath?: unknown }).report_path ??
    (raw as { reportPath?: unknown }).reportPath
  const evidenceCount = (raw as { evidence_count?: unknown; evidenceCount?: unknown }).evidence_count ??
    (raw as { evidenceCount?: unknown }).evidenceCount
  return {
    status,
    score:
      typeof score === 'number'
        ? score
        : typeof score === 'string' && Number.isFinite(Number(score))
          ? Number(score)
          : undefined,
    reportPath: typeof reportPath === 'string' ? reportPath : undefined,
    evidenceCount:
      typeof evidenceCount === 'number'
        ? evidenceCount
        : typeof evidenceCount === 'string' && Number.isFinite(Number(evidenceCount))
          ? Number(evidenceCount)
          : undefined,
  }
}

function extractOptimisticMessageState(
  metadata: Record<string, unknown> | null | undefined,
): OptimisticMessageState | null {
  if (!metadata || typeof metadata !== 'object' || metadata.optimistic !== true) {
    return null
  }

  const optimisticState = metadata.optimisticState
  if (optimisticState !== 'pending' && optimisticState !== 'failed') {
    return null
  }

  return {
    optimistic: true,
    state: optimisticState,
    errorMessage:
      typeof metadata.optimisticErrorMessage === 'string'
        ? metadata.optimisticErrorMessage
        : undefined,
  }
}

function formatBytes(sizeBytes: number | null | undefined): string {
  if (sizeBytes == null || Number.isNaN(sizeBytes) || sizeBytes < 0) {
    return 'local file'
  }
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`
  }
  if (sizeBytes < 1024 * 1024) {
    return `${Math.round(sizeBytes / 1024)} KB`
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatCreativeVideoStatus(preview: CreativeVideoStatusPreview): string {
  if (preview.blocked) {
    return 'Zatrzymane'
  }
  if (preview.workflowStatus === 'queued' || preview.workflowStatus === 'planned') {
    return 'Zaplanowane'
  }
  if (preview.workflowStatus === 'running' || preview.workflowStatus === 'retrying') {
    return 'W toku'
  }
  if (preview.approvalRequired || preview.workflowStatus === 'awaiting_human') {
    return 'Gotowe do review'
  }
  if (preview.workflowStatus === 'done' || preview.workflowStatus === 'completed') {
    return 'Gotowe'
  }
  if (
    preview.workflowStatus === 'manual_upload_ready' ||
    preview.workflowStatus === 'local_render_complete'
  ) {
    return 'Gotowe lokalnie'
  }
  return 'W toku'
}

function extractProjectCommandVerification(
  metadata: Record<string, unknown> | null | undefined,
): { verified: boolean; status: string; verificationStatus: string; evidenceCount: number } | null {
  if (!metadata || metadata.kind !== 'project_command_result') {
    return null
  }
  const rawCount = metadata.evidence_count
  return {
    verified: metadata.verified === true,
    status: typeof metadata.command_status === 'string' ? metadata.command_status : 'unknown',
    verificationStatus:
      typeof metadata.verification_status === 'string'
        ? metadata.verification_status
        : metadata.verified === true
          ? 'verified'
          : 'insufficient_evidence',
    evidenceCount:
      typeof rawCount === 'number'
        ? rawCount
        : typeof rawCount === 'string' && Number.isFinite(Number(rawCount))
          ? Number(rawCount)
          : 0,
  }
}

function extractAgenticHardStop(
  metadata: Record<string, unknown> | null | undefined,
): { summary: string; reason: string } | null {
  if (!metadata || typeof metadata !== 'object') {
    return null
  }
  const loop = metadata.agentic_tool_loop
  if (!loop || typeof loop !== 'object') {
    return null
  }
  const payload = loop as Record<string, unknown>
  if (payload.requires_confirmation !== true && payload.status !== 'needs_confirmation') {
    return null
  }
  const pending =
    payload.pending_approval && typeof payload.pending_approval === 'object'
      ? (payload.pending_approval as Record<string, unknown>)
      : null
  const summary =
    typeof pending?.summary === 'string' && pending.summary.trim()
      ? pending.summary
      : 'Inwazyjna akcja wymaga potwierdzenia.'
  const reason =
    typeof pending?.reason === 'string' && pending.reason.trim()
      ? pending.reason
      : 'hard_stop'
  return { summary, reason }
}

function formatAgenticLoopStatus(status: string): string {
  return formatAgenticLoopStatusLabel(status)
}

function formatToolCallStatus(status: string): string {
  return formatToolCallStatusLabel(status)
}

function shortenToolArgs(args: Record<string, unknown> | null | undefined): string | null {
  if (!args || typeof args !== 'object') {
    return null
  }
  const preferredKeys = ['query', 'path', 'url', 'command', 'app_key', 'list_only', 'name']
  const parts: string[] = []
  for (const key of preferredKeys) {
    const value = args[key]
    if (value === undefined || value === null || value === '') {
      continue
    }
    const text = typeof value === 'string' ? value : JSON.stringify(value)
    parts.push(`${key}=${text.length > 48 ? `${text.slice(0, 45)}…` : text}`)
    if (parts.length >= 2) {
      break
    }
  }
  if (parts.length === 0) {
    const entries = Object.entries(args).slice(0, 2)
    for (const [key, value] of entries) {
      const text = typeof value === 'string' ? value : JSON.stringify(value)
      parts.push(`${key}=${text.length > 40 ? `${text.slice(0, 37)}…` : text}`)
    }
  }
  return parts.length > 0 ? parts.join(' · ') : null
}

function extractAgenticToolActivity(
  metadata: Record<string, unknown> | null | undefined,
): {
  toolsUsed: string[]
  calls: Array<{
    name: string
    status: string | null
    summary: string | null
    argsPreview: string | null
  }>
  turnsUsed: number | null
  status: string
  statusLabel: string
  settings: { files: boolean; memory: boolean; internet: boolean; workMode: string } | null
} | null {
  if (!metadata || typeof metadata !== 'object') {
    return null
  }
  const loop = metadata.agentic_tool_loop
  if (loop && typeof loop === 'object') {
    const payload = loop as Record<string, unknown>
    const fromList = Array.isArray(payload.tools_used)
      ? payload.tools_used.filter((item): item is string => typeof item === 'string')
      : []
    const calls = Array.isArray(payload.tool_calls)
      ? payload.tool_calls
          .map((item) => {
            if (!item || typeof item !== 'object') {
              return null
            }
            const row = item as Record<string, unknown>
            const name = typeof row.name === 'string' ? row.name : null
            if (!name) {
              return null
            }
            const args =
              row.arguments && typeof row.arguments === 'object'
                ? (row.arguments as Record<string, unknown>)
                : null
            return {
              name,
              status: typeof row.status === 'string' ? row.status : null,
              summary: typeof row.summary === 'string' && row.summary.trim() ? row.summary.trim() : null,
              argsPreview: shortenToolArgs(args),
            }
          })
          .filter((item): item is NonNullable<typeof item> => Boolean(item))
      : []
    const liveCalls = Array.isArray(payload.live_tools)
      ? payload.live_tools
          .map((item) => {
            if (!item || typeof item !== 'object') {
              return null
            }
            const row = item as Record<string, unknown>
            const name = typeof row.name === 'string' ? row.name : null
            if (!name) {
              return null
            }
            return {
              name,
              status: typeof row.status === 'string' ? row.status : 'running',
              summary: typeof row.summary === 'string' && row.summary.trim() ? row.summary.trim() : null,
              argsPreview: null,
            }
          })
          .filter((item): item is NonNullable<typeof item> => Boolean(item))
      : []
    const effectiveCalls = calls.length > 0 ? calls : liveCalls
    const toolsUsed =
      fromList.length > 0 ? fromList : effectiveCalls.map((item) => item.name)
    const settingsRaw =
      payload.settings && typeof payload.settings === 'object'
        ? (payload.settings as Record<string, unknown>)
        : null
    const status = typeof payload.status === 'string' ? payload.status : 'completed'
    return {
      toolsUsed,
      calls: effectiveCalls,
      turnsUsed: typeof payload.turns_used === 'number' ? payload.turns_used : null,
      status,
      statusLabel: formatAgenticLoopStatus(status),
      settings: settingsRaw
        ? {
            files: settingsRaw.files_enabled !== false,
            memory: settingsRaw.memory_enabled !== false,
            internet: settingsRaw.internet_enabled !== false,
            workMode: typeof settingsRaw.work_mode === 'string' ? settingsRaw.work_mode : 'auto',
          }
        : null,
    }
  }

  const desktop = metadata.desktop_operator
  if (desktop && typeof desktop === 'object') {
    const row = desktop as Record<string, unknown>
    const action = typeof row.action === 'string' ? row.action : 'desktop_operator'
    const status =
      typeof row.error === 'string' && row.error
        ? 'error'
        : typeof row.status === 'string' ? row.status : 'ok'
    const failed = ['error', 'failed', 'not_found', 'blocked', 'ambiguous'].includes(status)
      || (typeof row.command_exit_code === 'number' && row.command_exit_code !== 0)
    const loopStatus = failed ? 'failed' : status === 'incomplete' ? 'incomplete' : 'completed'
    const summaryParts = [
      typeof row.label === 'string' ? row.label : null,
      typeof row.app_key === 'string' ? `key=${row.app_key}` : null,
      typeof row.query === 'string' ? `query=${row.query}` : null,
      typeof row.error === 'string' ? row.error : null,
    ].filter((item): item is string => Boolean(item))
    return {
      toolsUsed: [action],
      calls: [
        {
          name: action,
          status,
          summary: summaryParts.join(' · ') || null,
          argsPreview: null,
        },
      ],
      turnsUsed: null,
      status: loopStatus,
      statusLabel: formatAgenticLoopStatus(loopStatus),
      settings: null,
    }
  }

  const browser = metadata.browser_operator
  if (browser && typeof browser === 'object') {
    const row = browser as Record<string, unknown>
    const action = typeof row.action === 'string' ? row.action : 'browser_operator'
    const status = typeof row.error === 'string' ? 'error' : typeof row.status === 'string' ? row.status : 'ok'
    const loopStatus = status === 'incomplete' ? 'incomplete' : ['error', 'learn_failed', 'not_found'].includes(status) ? 'failed' : 'completed'
    const summaryParts = [
      typeof row.mode === 'string' ? `mode=${row.mode}` : null,
      typeof row.tab_count === 'number' ? `karty=${row.tab_count}` : null,
      typeof row.target_url === 'string' ? row.target_url : null,
      typeof row.vault_note_path === 'string' ? row.vault_note_path : null,
      typeof row.error === 'string' ? row.error : null,
    ].filter((item): item is string => Boolean(item))
    return {
      toolsUsed: [action],
      calls: [
        {
          name: action,
          status,
          summary: summaryParts.join(' · ') || null,
          argsPreview: null,
        },
      ],
      turnsUsed: null,
      status: loopStatus,
      statusLabel: formatAgenticLoopStatus(loopStatus),
      settings: null,
    }
  }

  return null
}

function isCreativeVideoWorkflowActive(preview: CreativeVideoStatusPreview): boolean {
  return (
    preview.workflowStatus === 'queued' ||
    preview.workflowStatus === 'running' ||
    preview.workflowStatus === 'retrying' ||
    preview.workflowStatus === 'planned'
  )
}

function formatWorkflowLabel(value: string): string {
  if (value === 'content_campaign') {
    return 'Short pipeline'
  }
  return value.replace(/_/g, ' ')
}

function formatQualityLabel(value: string | undefined): string {
  switch (value) {
    case 'premium':
      return 'Premium'
    case 'fast-turnaround':
      return 'Szybciej'
    case 'standard':
      return 'Standard'
    default:
      return 'Standard'
  }
}

function formatCurrentNode(value: string | undefined): string | null {
  if (!value) return null
  const compact = value.split('.').at(-1) ?? value
  const labels: Record<string, string> = {
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
  return labels[compact] ?? compact.replace(/_/g, ' ')
}

function formatQualityRunStatus(value: string): string {
  switch (value) {
    case 'DONE_VERIFIED':
      return 'Zweryfikowane'
    case 'DONE_NEEDS_REVIEW':
      return 'Wymaga review'
    case 'FAILED_WITH_REASON':
      return 'Nie przeszlo'
    default:
      return value.replace(/_/g, ' ')
  }
}

function qualityRunToneClass(value: string): string {
  switch (value) {
    case 'DONE_VERIFIED':
      return 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200'
    case 'DONE_NEEDS_REVIEW':
      return 'border-sem-warning/40 bg-sem-warning/10 text-sem-warning'
    case 'FAILED_WITH_REASON':
      return 'border-error/40 bg-error/10 text-error'
    default:
      return 'border-border-default bg-elevated text-text-secondary'
  }
}

function renderSegments(content: string): ParsedSegment[] {
  const matches = [...content.matchAll(/```([\w-]+)?\r?\n([\s\S]*?)```/g)]
  if (matches.length === 0) {
    return [{ type: 'text', value: content }]
  }

  const segments: ParsedSegment[] = []
  let cursor = 0
  matches.forEach((match) => {
    if ((match.index ?? 0) > cursor) {
      segments.push({
        type: 'text',
        value: content.slice(cursor, match.index),
      })
    }
    segments.push({
      type: 'code',
      language: match[1] || 'text',
      value: match[2].trimEnd(),
    })
    cursor = (match.index ?? 0) + match[0].length
  })

  if (cursor < content.length) {
    segments.push({ type: 'text', value: content.slice(cursor) })
  }
  return segments
}

function CodeBlock({
  code,
  language,
  onCopy,
}: {
  code: string
  language: string
  onCopy: (value: string) => void | Promise<void>
}) {
  const [html, setHtml] = useState<string | null>(null)
  const normalizedLanguage = getShikiLanguage(language)

  useEffect(() => {
    let cancelled = false

    if (!normalizedLanguage) {
      setHtml(null)
      return () => {
        cancelled = true
      }
    }

    void getShikiHighlighter()
      .then((highlighter) =>
        highlighter.codeToHtml(code, {
          lang: normalizedLanguage,
          theme: 'atnos-shell',
        }),
      )
      .then((nextHtml) => {
        if (!cancelled) {
          setHtml(nextHtml)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setHtml(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [code, normalizedLanguage])

  return (
    <div className="overflow-hidden rounded-md border border-border-default bg-elevated">
      <div className="flex items-center justify-between border-b border-border-default px-4 py-3">
        <span className="type-mono-sm text-text-tertiary">
          {(normalizedLanguage ?? language).toUpperCase()}
        </span>
        <button
          type="button"
          onClick={() => void onCopy(code)}
          className="inline-flex items-center gap-1.5 type-body-sm text-text-secondary transition hover:text-text-primary"
          aria-label="Kopiuj kod"
        >
          <Copy size={14} />
          <span className="max-[480px]:sr-only">kopiuj</span>
        </button>
      </div>
      <div
        className="overflow-x-auto p-4 text-[13px] leading-5 [&_.shiki]:!m-0 [&_.shiki]:overflow-x-auto [&_.shiki]:!bg-transparent [&_pre]:!m-0 [&_pre]:overflow-x-auto [&_code]:font-mono"
        dangerouslySetInnerHTML={
          html
            ? { __html: html }
            : undefined
        }
      >
        {html ? null : (
          <pre className="m-0 overflow-x-auto font-mono text-[13px] leading-5 text-text-primary">
            <code>{code}</code>
          </pre>
        )}
      </div>
    </div>
  )
}

function CodeDiffCard({ preview }: { preview: CodeDiffPreview }) {
  const [expanded, setExpanded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState(preview.draftStatus ?? 'draft')
  const pushToast = useToastStore((state) => state.push)
  const firstDiff = preview.files[0]?.diff ?? ''
  const hasDraftAction = typeof preview.actionDraftId === 'number'
  const canApprove = hasDraftAction && status === 'draft'
  const canExecute = hasDraftAction && status === 'approved'
  const canReject = hasDraftAction && !['executed_later', 'rejected'].includes(status)
  const statusLabel =
    status === 'draft'
      ? 'review only'
      : status === 'approved'
        ? 'approved for apply'
        : status === 'executed_later'
          ? 'applied'
          : status === 'rejected'
            ? 'rejected'
            : status
  const reviewHelperText =
    status === 'approved'
      ? 'Preview jest zatwierdzony. Zastosowanie zmian wymaga osobnego klikniecia.'
      : status === 'executed_later'
        ? 'Ten draft zostal juz zastosowany. Karta pozostaje jako slad review.'
        : status === 'rejected'
          ? 'Ten draft zostal odrzucony i nie wykona zadnej zmiany na dysku.'
          : 'To jest tylko preview zmian. Atnos niczego nie zapisze bez osobnego zatwierdzenia i osobnego zastosowania.'

  async function handleApprove() {
    if (!preview.actionDraftId || saving) return
    setSaving(true)
    try {
      const approved = await approveActionDraft(preview.actionDraftId)
      setStatus(approved.status)
      pushToast({
        type: 'success',
        title: 'Preview zatwierdzony',
        message: 'Draft jest gotowy do osobnego zastosowania.',
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udalo sie zatwierdzic preview',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSaving(false)
    }
  }

  async function handleExecute() {
    if (!preview.actionDraftId || saving || status !== 'approved') return
    setSaving(true)
    try {
      const execution = await executeActionDraft(preview.actionDraftId, { confirm: true })
      setStatus(execution.actionDraft.status)
      pushToast({
        type: 'success',
        title: 'Zmiany zastosowane',
        message: 'Zatwierdzony diff zostal zapisany na dysku.',
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udalo sie zastosowac zmian',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSaving(false)
    }
  }

  async function handleReject() {
    if (!preview.actionDraftId || saving) return
    setSaving(true)
    try {
      const rejected = await rejectActionDraft(
        preview.actionDraftId,
        'Rejected from chat code diff card.',
      )
      setStatus(rejected.status)
      pushToast({
        type: 'warning',
        title: 'Diff odrzucony',
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udalo sie odrzucic diffu',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-lg border border-border-default bg-panel px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="label-eyebrow">CODE DIFF</div>
          <div className="mt-2 type-body-sm font-medium text-text-primary">{preview.title}</div>
          <div className="mt-1 type-body-sm text-text-secondary">{preview.summary}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
            {preview.files.length} files
          </span>
          <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
            {statusLabel}
          </span>
        </div>
      </div>

      <div className="mt-4 rounded-md border border-accent-primary/20 bg-accent-primary/5 px-4 py-3">
        <div className="label-eyebrow text-accent-primary">REVIEW FLOW</div>
        <div className="mt-2 type-body-sm text-text-secondary">{reviewHelperText}</div>
      </div>

      {firstDiff ? (
        <div className="mt-4 overflow-hidden rounded-md border border-border-default bg-elevated">
          <div className="flex items-center justify-between border-b border-border-default px-4 py-3">
            <span className="type-mono-sm text-text-tertiary">
              {preview.files[0]?.path ?? 'diff'}
            </span>
          </div>
          <pre className="max-h-72 overflow-auto p-4 font-mono text-[12px] leading-5 text-text-primary">
            <code>{expanded ? preview.files.map((file) => file.diff).join('\n\n') : firstDiff}</code>
          </pre>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        {hasDraftAction ? (
          <>
            <button
              type="button"
              disabled={!canApprove || saving}
              onClick={() => void handleApprove()}
              className="rounded-md border border-accent-primary bg-accent-primary/10 px-4 py-2 type-body-sm text-accent-primary transition hover:bg-accent-primary/15 disabled:cursor-not-allowed disabled:border-border-default disabled:text-text-disabled"
            >
              Zatwierdz preview
            </button>
            <button
              type="button"
              disabled={!canExecute || saving}
              onClick={() => void handleExecute()}
              className="rounded-md border border-accent-primary/60 px-4 py-2 type-body-sm text-accent-primary transition hover:bg-accent-primary/10 disabled:cursor-not-allowed disabled:border-border-default disabled:text-text-disabled"
            >
              Zastosuj zatwierdzone zmiany
            </button>
            <button
              type="button"
              disabled={!canReject || saving}
              onClick={() => void handleReject()}
              className="rounded-md border border-border-default px-4 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:text-text-disabled"
            >
              Odrzuc
            </button>
          </>
        ) : null}
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="rounded-md border border-border-default px-4 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary"
        >
          {expanded ? 'Ukryj diff' : 'Pokaż pełny diff'}
        </button>
      </div>
    </div>
  )
}

function RuntimeErrorCard({
  preview,
  onRetry,
  onUseFallback,
}: {
  preview: RuntimeErrorPreview
  onRetry?: () => void
  onUseFallback?: () => void
}) {
  const timeoutMs =
    typeof preview.diagnostics?.timeout_ms === 'number'
      ? preview.diagnostics.timeout_ms
      : typeof preview.diagnostics?.timeout_ms === 'string'
        ? Number(preview.diagnostics.timeout_ms)
        : null
  const fallbackRequiresConfirmation =
    preview.diagnostics?.fallback_requires_confirmation === true
  const healthReason =
    typeof preview.diagnostics?.health_reason === 'string'
      ? preview.diagnostics.health_reason
      : null
  const healthStatus =
    typeof preview.diagnostics?.health_status === 'string'
      ? preview.diagnostics.health_status
      : null
  const modelPresent =
    typeof preview.diagnostics?.model_present === 'boolean'
      ? preview.diagnostics.model_present
      : null
  const installedModelsSample = Array.isArray(preview.diagnostics?.installed_models_sample)
    ? preview.diagnostics.installed_models_sample.filter(
        (value): value is string => typeof value === 'string',
      )
    : []

  return (
    <div className="rounded-lg border border-border-default bg-panel px-4 py-4">
      <div className="label-eyebrow">RUNTIME ERROR</div>
      <div className="mt-2 type-body-sm font-medium text-text-primary">{preview.userTitle}</div>
      <div className="mt-2 type-body-sm text-text-secondary">{preview.userDetail}</div>
      <div className="mt-4 flex flex-wrap gap-2">
        <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
          {preview.failureType}
        </span>
        <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
          fallback:{preview.canFallback ? 'yes' : 'no'}
        </span>
        {timeoutMs != null ? (
          <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
            timeout:{timeoutMs}ms
          </span>
        ) : null}
      </div>
      <div className="mt-4 grid gap-2 md:grid-cols-2">
        <div className="rounded-md border border-border-default bg-elevated px-3 py-3">
          <div className="label-eyebrow">MODEL</div>
          <div className="mt-2 type-mono-sm text-text-primary">
            {preview.runtimeModelName ?? preview.modelKey}
          </div>
          <div className="mt-1 type-body-sm text-text-tertiary">{preview.providerKey}</div>
        </div>
        <div className="rounded-md border border-border-default bg-elevated px-3 py-3">
          <div className="label-eyebrow">DIAGNOSTYKA</div>
          <div className="mt-2 type-body-sm text-text-primary">
            {healthReason ??
              (healthStatus
                ? `Status runtime: ${healthStatus}`
                : 'Brak dodatkowej diagnozy od runtime.')}
          </div>
          {modelPresent === false ? (
            <div className="mt-1 type-body-sm text-error">
              Wybrany model nie jest teraz widoczny w runtime.
            </div>
          ) : null}
          {installedModelsSample.length ? (
            <div className="mt-2 type-mono-sm text-text-tertiary">
              Modele widoczne: {installedModelsSample.join(', ')}
            </div>
          ) : null}
        </div>
      </div>
      {preview.suggestedActions.length ? (
        <div className="mt-4 rounded-md border border-border-default bg-elevated px-3 py-3">
          <div className="label-eyebrow">CO ZROBIC TERAZ</div>
          <ul className="mt-2 space-y-1">
            {preview.suggestedActions.map((action) => (
              <li key={action} className="type-body-sm text-text-secondary">
                - {action}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="mt-4 flex flex-wrap gap-2">
        {preview.retryable ? (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md border border-border-default px-4 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary"
          >
            {CHAT_STATUS.retry}
          </button>
        ) : null}
        <Link
          to="/settings#diagnostics"
          className="rounded-md border border-border-default px-4 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary"
        >
          Sprawdź runtime
        </Link>
        <Link
          to="/settings#models"
          className="rounded-md border border-border-default px-4 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary"
        >
          Zmień model
        </Link>
        {preview.canFallback && fallbackRequiresConfirmation ? (
          <button
            type="button"
            onClick={onUseFallback}
            className="rounded-md border border-border-default px-4 py-2 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary"
          >
            Użyj fallbacku
          </button>
        ) : null}
      </div>
    </div>
  )
}

function ProviderUsageCard({ preview }: { preview: ProviderUsagePreview }) {
  return (
    <details className="rounded-lg border border-border-default bg-panel/55 px-3 py-2 text-xs text-text-secondary">
      <summary className="cursor-pointer list-none">
        <span className="label-eyebrow mr-2">AI ROUTING</span>
        <span className="text-text-primary">Użyto {preview.providerLabel}</span>
        {preview.modelKey ? <span className="ml-2 text-text-tertiary">· {preview.modelKey}</span> : null}
      </summary>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        {preview.providerPosture ? (
          <div>
            <div className="label-eyebrow">Privacy</div>
            <div className="mt-1 text-text-primary">{preview.providerPosture}</div>
          </div>
        ) : null}
        {preview.taskType ? (
          <div>
            <div className="label-eyebrow">Task</div>
            <div className="mt-1 text-text-primary">{preview.taskType}</div>
          </div>
        ) : null}
        {preview.routerTaskType ? (
          <div>
            <div className="label-eyebrow">Router</div>
            <div className="mt-1 text-text-primary">{preview.routerTaskType}</div>
          </div>
        ) : null}
        {preview.routerModel ? (
          <div>
            <div className="label-eyebrow">Recommended</div>
            <div className="mt-1 text-text-primary">{preview.routerModel}</div>
          </div>
        ) : null}
        {preview.actualRuntimeModel ? (
          <div>
            <div className="label-eyebrow">Runtime</div>
            <div className="mt-1 text-text-primary">{preview.actualRuntimeModel}</div>
          </div>
        ) : null}
        {preview.routingReason ? (
          <div className="sm:col-span-3">
            <div className="label-eyebrow">Reason</div>
            <div className="mt-1 text-text-primary">{preview.routingReason}</div>
          </div>
        ) : null}
      </div>
    </details>
  )
}

function CreativeVideoStatusCard({
  preview,
  attachments,
}: {
  preview: CreativeVideoStatusPreview
  attachments: ConversationMessage['attachments']
}) {
  const statusLabel = formatCreativeVideoStatus(preview)
  const videoCount = attachments?.filter((attachment) => attachment.kind === 'video').length ?? 0
  const subtitleCount =
    attachments?.filter((attachment) => attachment.fileExtension === '.srt' || attachment.kind === 'text').length ?? 0
  const thumbnailCount = attachments?.filter((attachment) => attachment.kind === 'image').length ?? 0
  const currentNode = formatCurrentNode(preview.currentNode)
  const isActiveWorkflow = isCreativeVideoWorkflowActive(preview)

  return (
    <div className="rounded-lg border border-border-default bg-panel px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="label-eyebrow">VIDEO WORKFLOW</div>
          <div className="mt-2 flex items-center gap-2 type-body-sm font-medium text-text-primary">
            {isActiveWorkflow ? (
              <LoaderCircle size={14} className="animate-spin text-accent-primary" aria-hidden />
            ) : null}
            {statusLabel}
          </div>
          <div className="mt-1 type-body-sm text-text-secondary">
            {preview.blocked
              ? preview.blocker ?? 'Workflow zatrzymal sie przed wygenerowaniem materialu.'
              : isActiveWorkflow
                ? 'Workflow jest aktywny. Aktualny etap i artefakty odswiezaja sie z metadanych zadania.'
              : preview.approvalRequired
                ? 'Material jest gotowy do review i czeka na jawna decyzje o publikacji.'
                : 'Workflow dowiozl komplet artefaktow dla tej rozmowy.'}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="rounded-pill border border-accent-primary/30 bg-accent-primary/10 px-3 py-1 type-mono-sm text-accent-primary">
            {formatWorkflowLabel(preview.workflowKey)}
          </span>
          <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
            {formatQualityLabel(preview.qualityProfile)}
          </span>
          {preview.platformHint ? (
            <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-tertiary">
              {preview.platformHint}
            </span>
          ) : null}
        </div>
      </div>

      {isActiveWorkflow ? (
        <div className="mt-4 flex items-start gap-3 rounded-md border border-accent-primary/30 bg-accent-primary/8 px-3 py-3">
          <LoaderCircle size={16} className="mt-0.5 shrink-0 animate-spin text-accent-primary" aria-hidden />
          <div>
            <div className="type-body-sm font-medium text-text-primary">
              ATNOS realnie pracuje teraz: {currentNode ?? 'workflow lokalny w tle'}
            </div>
            <div className="mt-1 type-body-sm text-text-secondary">
              Ten status jest brany z metadanych workflow. Chat odswieza rozmowe w tle, ale nie
              powinien juz wyrywac Cie na dol, gdy czytasz starsze wiadomosci.
            </div>
          </div>
        </div>
      ) : null}

      <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-md border border-border-default bg-elevated px-3 py-3">
          <div className="label-eyebrow">WIDEO</div>
          <div className="mt-2 type-body-sm text-text-primary">{videoCount || preview.artifactCount} artefakt</div>
        </div>
        <div className="rounded-md border border-border-default bg-elevated px-3 py-3">
          <div className="label-eyebrow">MINIATURY</div>
          <div className="mt-2 type-body-sm text-text-primary">{thumbnailCount}</div>
        </div>
        <div className="rounded-md border border-border-default bg-elevated px-3 py-3">
          <div className="label-eyebrow">NAPISY</div>
          <div className="mt-2 type-body-sm text-text-primary">{subtitleCount}</div>
        </div>
        <div className="rounded-md border border-border-default bg-elevated px-3 py-3">
          <div className="label-eyebrow">STAN</div>
          <div className="mt-2 type-body-sm text-text-primary">{statusLabel}</div>
          {currentNode ? (
            <div className="mt-1 type-mono-sm text-text-tertiary">{currentNode}</div>
          ) : null}
        </div>
      </div>

      {preview.nextStep ? (
        <div className="mt-4 rounded-md border border-border-default bg-elevated px-3 py-3">
          <div className="label-eyebrow">NASTEPNY KROK</div>
          <div className="mt-2 type-body-sm text-text-secondary">{preview.nextStep}</div>
        </div>
      ) : null}

      {preview.qualityRun ? (
        <div
          className={`mt-4 rounded-md border px-3 py-3 ${qualityRunToneClass(preview.qualityRun.status)}`}
          data-testid="chat-quality-run-card"
        >
          <div className="label-eyebrow">QUALITY GATE</div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="type-body-sm font-medium">
              {formatQualityRunStatus(preview.qualityRun.status)}
            </span>
            {preview.qualityRun.score != null ? (
              <span className="rounded-pill border border-current/25 px-2 py-0.5 type-mono-sm">
                score {preview.qualityRun.score}/10
              </span>
            ) : null}
            {preview.qualityRun.evidenceCount != null ? (
              <span className="rounded-pill border border-current/25 px-2 py-0.5 type-mono-sm">
                dowody {preview.qualityRun.evidenceCount}
              </span>
            ) : null}
          </div>
          {preview.qualityRun.reportPath ? (
            <div className="mt-2 break-all type-mono-sm opacity-85">
              raport: {preview.qualityRun.reportPath}
            </div>
          ) : (
            <div className="mt-2 type-body-sm opacity-85">
              Atnos zapisal werdykt jakosci, ale raport nie ma jawnej sciezki w metadanych.
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}

export function ChatMessage({
  message,
  animationIndex = 0,
  onRetryRuntimeError,
  onRetryUserMessage,
  onQuestionAnswered,
  onResolveDecision,
  onRefreshBriefing,
}: {
  message: ConversationMessage
  animationIndex?: number
  onRetryRuntimeError?: (assistantMessageId: number | string, options?: { forceExternal?: boolean }) => void
  onRetryUserMessage?: (userMessageId: number | string) => void
  onQuestionAnswered?: (conversationId: number) => void | Promise<void>
  onResolveDecision?: (decisionId: number) => void | Promise<void>
  onRefreshBriefing?: () => void | Promise<void>
}) {
  const reducedMotion = useReducedMotionStrict()
  const isAssistant = message.role === 'assistant'
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'
  const briefingPayload = extractBriefingPayload(
    message.metadataJson,
    message.content,
    message.createdAt,
  )
  const isBriefing = briefingPayload !== null
  const segments = renderSegments(message.content)
  const assistantQuestion = isAssistant ? extractAssistantQuestion(message.metadataJson) : null
  const codeDiffPreview = isAssistant ? extractCodeDiffPreview(message.metadataJson) : null
  const runtimeErrorPreview = isAssistant ? extractRuntimeErrorPreview(message.metadataJson) : null
  const creativeVideoPreview = isAssistant ? extractCreativeVideoStatusPreview(message.metadataJson) : null
  const providerUsagePreview = isAssistant ? extractProviderUsagePreview(message.metadataJson) : null
  const skillExecution = isAssistant ? extractSkillExecution(message.metadataJson) : null
  const productionOrderMeta =
    message.metadataJson &&
    typeof message.metadataJson === 'object' &&
    message.metadataJson.kind === 'production_order_result'
      ? message.metadataJson
      : null
  const projectCommandVerification = isAssistant
    ? extractProjectCommandVerification(message.metadataJson)
    : null
  const agenticHardStop = isAssistant ? extractAgenticHardStop(message.metadataJson) : null
  const agenticToolActivity = isAssistant ? extractAgenticToolActivity(message.metadataJson) : null
  const optimisticMessageState = isUser
    ? extractOptimisticMessageState(message.metadataJson)
    : null
  const resolvedSelections = extractResolvedSelections(message.metadataJson)
  const [questionSubmitState, setQuestionSubmitState] = useState<QuestionSubmitState>({
    status: 'idle',
    selectedIds: [],
  })
  const selectedConversationId = useChatStore((state) => state.selectedConversationId)
  const appendOptimisticUserMessage = useChatStore((state) => state.appendOptimisticUserMessage)
  const setOptimisticMessageState = useChatStore((state) => state.setOptimisticMessageState)
  const pushToast = useToastStore((state) => state.push)
  const speakerLabel = getSpeakerLabel(message, isBriefing)
  const containerJustifyClass = isUser ? 'justify-end' : 'justify-start'
  const headerJustifyClass = isUser ? 'justify-end' : 'justify-start'
  const messageSurfaceClass = isBriefing
    ? ''
    : isUser
      ? 'factory-user-dispatch rounded-lg border border-accent-primary/80 bg-accent-primary/15 px-3.5 py-2.5 text-text-primary shadow-glow-soft'
      : isSystem
        ? 'rounded-lg border border-sem-warning/50 bg-sem-warning/8 px-3.5 py-2.5'
        : 'factory-agent-dispatch rounded-lg border border-border-default bg-panel/90 px-3.5 py-2.5'
  const barClass = isBriefing
    ? 'bg-accent-primary'
    : isAssistant
      ? 'bg-accent-primary'
      : isSystem
        ? 'bg-sem-warning'
        : 'bg-accent-primary/80'
  const messageEnter = reducedMotion
    ? {
        initial: { opacity: 0, y: 8 },
        animate: { opacity: 1, y: 0, transition: { duration: 0 } },
        exit: { opacity: 0, y: -4, transition: { duration: 0 } },
      }
    : {
        initial: VARIANTS.enter.initial,
        animate: {
          ...VARIANTS.enter.animate,
          transition: {
            ...VARIANTS.enter.animate.transition,
            delay: Math.min(animationIndex * 0.06, 0.24),
          },
        },
        exit: VARIANTS.enter.exit,
      }

  useEffect(() => {
    setQuestionSubmitState({
      status: 'idle',
      selectedIds: [],
    })
  }, [message.id, assistantQuestion?.questionId])

  function buildQuestionAnswerSummary(question: AssistantQuestion, selectedIds: string[]) {
    const labels = selectedIds.map((id) => {
      const option = question.options.find((opt) => opt.id === id)
      return option ? option.label : id
    })
    return labels.length === 1 ? `Wybralem: ${labels[0]}` : `Wybralem: ${labels.join(', ')}`
  }

  async function handleQuestionAnswer(
    question: AssistantQuestion,
    selectedIds: string[],
    existingOptimisticMessageId?: string,
  ) {
    const conversationId = selectedConversationId
    const assistantMessageId = Number(message.id)
    const summary = buildQuestionAnswerSummary(question, selectedIds)
    const optimisticMessageId =
      existingOptimisticMessageId ??
      appendOptimisticUserMessage({
        content: summary,
        requestText: summary,
        requestAttachments: [],
      })

    setQuestionSubmitState({
      status: 'submitting',
      selectedIds,
      optimisticMessageId,
    })
    setOptimisticMessageState(optimisticMessageId, 'pending')

    if (conversationId == null || !Number.isFinite(assistantMessageId)) {
      const errorMessage = 'Nie moge zapisac wyboru: brak aktywnej rozmowy albo ID pytania.'
      console.error('[AssistantQuestionCard] Invalid answer-question context', {
        conversationId,
        assistantMessageId: message.id,
        questionId: question.questionId,
      })
      setOptimisticMessageState(optimisticMessageId, 'failed', errorMessage)
      setQuestionSubmitState({
        status: 'error',
        selectedIds,
        errorMessage,
        optimisticMessageId,
      })
      return
    }

    try {
      await answerQuestion({
        conversationId,
        assistantMessageId,
        questionId: question.questionId,
        selectedIds,
      })
      await onQuestionAnswered?.(conversationId)
      setQuestionSubmitState({
        status: 'submitted',
        selectedIds,
        optimisticMessageId,
      })
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : 'Nie udalo sie zapisac wyboru.'
      console.error('[AssistantQuestionCard] Failed to answer question', error)
      setOptimisticMessageState(optimisticMessageId, 'failed', errorMessage)
      setQuestionSubmitState({
        status: 'error',
        selectedIds,
        errorMessage,
        optimisticMessageId,
      })
    }
  }


  async function handleCopyCodeBlock(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      pushToast({
        type: 'success',
        title: 'Kod skopiowany',
      })
    } catch {
      pushToast({
        type: 'warning',
        title: 'Nie udalo sie skopiowac kodu',
      })
    }
  }

  return (
    <motion.article
      data-message-role={message.role}
      initial={messageEnter.initial}
      animate={messageEnter.animate}
      exit={messageEnter.exit}
      className={`flex w-full ${containerJustifyClass}`}
    >
      <div className={`w-full ${isUser ? 'max-w-[92%] md:max-w-[88%]' : 'max-w-full'}`}>
        <div className={`mb-2 flex items-center gap-2 ${headerJustifyClass}`}>
          <span className="label-eyebrow text-text-secondary">{speakerLabel}</span>
          <span className="label-eyebrow text-text-tertiary" aria-hidden>
            ·
          </span>
          <span className="type-mono-sm text-text-tertiary">{formatClock(message.createdAt)}</span>
        </div>

        <div className={`flex items-start gap-4 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
          <motion.div
            initial={{ scaleY: 0 }}
            animate={{ scaleY: 1 }}
            transition={
              reducedMotion
                ? { duration: 0 }
                : { duration: DUR.base, delay: 0.08, ease: EASE.standard }
            }
            style={{ transformOrigin: 'top' }}
            className={`min-h-[24px] w-0.5 shrink-0 ${barClass}`}
          />

          <div className={`min-w-0 flex-1 space-y-3 ${messageSurfaceClass}`}>
            {briefingPayload ? (
              <BriefingCard
                payload={briefingPayload}
                onResolveDecision={onResolveDecision}
                onRefresh={onRefreshBriefing}
              />
            ) : null}

            {!briefingPayload
              ? segments.map((segment, index) =>
                  segment.type === 'text' ? (
                    segment.value.trim().length === 0 ? null : (
                      <ChatMarkdownBody
                        key={`${message.id}-text-${index}`}
                        text={segment.value}
                      />
                    )
                  ) : (
                    <CodeBlock
                      key={`${message.id}-code-${index}`}
                      code={segment.value}
                      language={segment.language}
                      onCopy={handleCopyCodeBlock}
                    />
                  ),
                )
              : null}

            {productionOrderMeta && String(productionOrderMeta.job_id ?? '').trim() ? (
              <ProductionOrderStatusCard
                jobId={String(productionOrderMeta.job_id ?? '')}
                initialTopic={
                  typeof productionOrderMeta.topic_title === 'string'
                    ? productionOrderMeta.topic_title
                    : null
                }
                initialStatus={
                  typeof productionOrderMeta.status === 'string'
                    ? productionOrderMeta.status
                    : null
                }
                initialStep={
                  typeof productionOrderMeta.current_step === 'string'
                    ? productionOrderMeta.current_step
                    : null
                }
                initialWorkerLive={Boolean(productionOrderMeta.worker_live)}
                initialError={
                  typeof productionOrderMeta.error === 'string'
                    ? productionOrderMeta.error
                    : null
                }
              />
            ) : null}

            {agenticToolActivity ? (
              <div
                data-testid="agentic-tool-activity"
                className="rounded-md border border-border-default/80 bg-panel/50 px-3 py-2 type-body-sm text-text-secondary backdrop-blur-md"
              >
                <div className="flex flex-wrap items-center gap-2 type-body-sm">
                  <span className="font-medium text-text-primary">
                    {agenticToolActivity.status === 'running' ||
                    agenticToolActivity.status === 'pending'
                      ? 'Pracuję nad zadaniem'
                      : agenticToolActivity.status === 'completed'
                        ? 'Zrobione'
                        : 'Praca z narzędziami'}
                  </span>
                  <span aria-hidden className="text-text-tertiary">
                    ·
                  </span>
                  <span
                    className={
                      agenticToolActivity.status === 'needs_confirmation' ||
                      agenticToolActivity.status === 'loop_detected' ||
                      agenticToolActivity.status === 'error' ||
                      agenticToolActivity.status === 'failed'
                        ? 'text-sem-warning'
                        : agenticToolActivity.status === 'completed'
                          ? 'text-sem-success'
                          : 'text-accent-primary'
                    }
                  >
                    {agenticToolActivity.statusLabel}
                  </span>
                  {agenticToolActivity.turnsUsed != null &&
                  agenticToolActivity.status !== 'completed' ? (
                    <>
                      <span aria-hidden className="text-text-tertiary">
                        ·
                      </span>
                      <span className="text-text-tertiary">
                        {agenticToolActivity.turnsUsed}{' '}
                        {agenticToolActivity.turnsUsed === 1 ? 'krok' : 'kroki'}
                      </span>
                    </>
                  ) : null}
                </div>
                {agenticToolActivity.calls.length > 0 ? (
                  <ul className="mt-2 space-y-1.5" data-testid="agentic-tool-timeline">
                    {agenticToolActivity.calls.map((call, index) => {
                      const inProgress = isToolCallInProgress(call.status)
                      const statusTone =
                        call.status === 'error' ||
                        call.status === 'failed' ||
                        call.status === 'not_found'
                          ? 'border-sem-warning/35 text-sem-warning'
                          : call.status === 'needs_confirmation'
                            ? 'border-sem-warning/35 text-sem-warning'
                            : inProgress
                              ? 'border-accent-primary/40 text-accent-primary'
                              : 'border-border-default text-text-secondary'
                      const label = getAgentToolStateLabel(call.name, call.status)
                      return (
                        <li
                          key={`${call.name}-${index}`}
                          className="rounded-md border border-border-subtle bg-void/40 px-2.5 py-1.5"
                        >
                          <div className="flex flex-wrap items-center gap-2 type-body-sm">
                            <span className={`rounded-pill border px-2 py-0.5 ${statusTone}`}>
                              {label}
                            </span>
                            {call.status && !inProgress ? (
                              <span className="text-text-tertiary">
                                {formatToolCallStatus(call.status)}
                              </span>
                            ) : null}
                          </div>
                          {call.summary ? (
                            <div className="mt-1 type-body-sm text-text-secondary">{call.summary}</div>
                          ) : null}
                        </li>
                      )
                    })}
                  </ul>
                ) : agenticToolActivity.toolsUsed.length > 0 ? (
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {agenticToolActivity.toolsUsed.map((toolName, index) => (
                      <span
                        key={`${toolName}-${index}`}
                        className="rounded-pill border border-accent-primary/30 bg-accent-primary/8 px-2 py-0.5 type-body-sm text-accent-primary"
                      >
                        {getAgentToolNoun(toolName)}
                      </span>
                    ))}
                  </div>
                ) : agenticToolActivity.status === 'running' ||
                  agenticToolActivity.status === 'pending' ? (
                  <div className="mt-1 type-body-sm text-text-tertiary">Przygotowuję kolejny krok…</div>
                ) : null}
              </div>
            ) : null}
            {skillExecution ? (
              <details
                data-testid="atnos-skill-execution"
                className="rounded-md border border-border-default/80 bg-panel/50 px-3 py-2 type-body-sm text-text-secondary"
              >
                <summary className="cursor-pointer font-medium text-text-primary">
                  Skille: {skillExecution.selectedSkills.join(', ')}
                </summary>
                <div className="mt-2 space-y-1 text-text-secondary">
                  <p>Instrukcje: {skillExecution.status}</p>
                  <p>Wczytane: {skillExecution.loaded.length ? skillExecution.loaded.join(', ') : 'brak potwierdzonych pakietów'}</p>
                  {skillExecution.missing.length ? (
                    <p className="text-sem-warning">Brak instrukcji: {skillExecution.missing.join(', ')}</p>
                  ) : null}
                  <p className="text-text-tertiary">Wybór skilla nie oznacza wykonania narzędzia; jego wynik pokazuje osobna oś działań.</p>
                </div>
              </details>
            ) : null}

            {agenticHardStop ? (
              <div
                data-testid="agentic-hard-stop"
                className="rounded-md border border-sem-warning/40 bg-sem-warning/10 px-3 py-2 type-body-sm text-sem-warning"
              >
                <div className="font-medium">Potrzebuję potwierdzenia</div>
                <div className="mt-1 opacity-90">{agenticHardStop.summary}</div>
                <div className="mt-1 type-body-sm opacity-80">
                  Napisz w czacie: potwierdzam
                </div>
              </div>
            ) : null}

            {projectCommandVerification ? (
              <div
                data-testid="project-command-verification"
                className={`flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 type-mono-sm ${
                  projectCommandVerification.verified
                    ? 'border-sem-success/35 bg-sem-success/8 text-sem-success'
                    : 'border-sem-warning/35 bg-sem-warning/8 text-sem-warning'
                }`}
              >
                <span>
                  {projectCommandVerification.verified
                    ? 'ZWERYFIKOWANE'
                    : projectCommandVerification.verificationStatus === 'partially_verified'
                      ? 'CZĘŚCIOWO ZWERYFIKOWANE'
                      : projectCommandVerification.status === 'completed'
                        ? 'ZAKOŃCZONE, BRAK WERYFIKACJI'
                      : 'NIEZWERYFIKOWANE'}
                </span>
                <span aria-hidden>·</span>
                <span>{projectCommandVerification.evidenceCount} dowodów</span>
              </div>
            ) : null}

            {assistantQuestion ? (
              <AssistantQuestionCard
                question={assistantQuestion}
                onSubmit={(ids) => handleQuestionAnswer(assistantQuestion, ids)}
                pending={questionSubmitState.status === 'submitting'}
                errorMessage={
                  questionSubmitState.status === 'error'
                    ? questionSubmitState.errorMessage
                    : null
                }
                submittedSelectedIds={
                  questionSubmitState.selectedIds.length > 0
                    ? questionSubmitState.selectedIds
                    : undefined
                }
                onRetry={
                  questionSubmitState.status === 'error'
                    ? () =>
                        handleQuestionAnswer(
                          assistantQuestion,
                          questionSubmitState.selectedIds,
                          questionSubmitState.optimisticMessageId,
                        )
                    : undefined
                }
                resolvedSelectedIds={resolvedSelections}
              />
            ) : null}

            {runtimeErrorPreview ? (
              <RuntimeErrorCard
                preview={runtimeErrorPreview}
                onRetry={() => onRetryRuntimeError?.(message.id)}
                onUseFallback={() => onRetryRuntimeError?.(message.id, { forceExternal: true })}
              />
            ) : null}

            {providerUsagePreview ? <ProviderUsageCard preview={providerUsagePreview} /> : null}

            {creativeVideoPreview ? (
              <CreativeVideoStatusCard preview={creativeVideoPreview} attachments={message.attachments} />
            ) : null}

            {optimisticMessageState ? (
              <div className="flex flex-wrap items-center gap-3 type-body-sm text-text-tertiary">
                <span>
                  {optimisticMessageState.state === 'pending'
                    ? CHAT_STATUS.sending
                    : optimisticMessageState.errorMessage || CHAT_STATUS.sendFailed}
                </span>
                {optimisticMessageState.state === 'failed' ? (
                  <button
                    type="button"
                    onClick={() => onRetryUserMessage?.(message.id)}
                    className="rounded-md border border-border-default px-3 py-1.5 type-body-sm text-text-secondary transition hover:bg-elevated hover:text-text-primary"
                  >
                    {CHAT_STATUS.retry}
                  </button>
                ) : null}
              </div>
            ) : null}

            {codeDiffPreview ? <CodeDiffCard preview={codeDiffPreview} /> : null}

            {message.attachments?.length ? (
              <div className="space-y-3">
                {message.attachments.map((attachment) => {
                  const href = buildAgentArtifactDownloadUrl(attachment.storagePath)
                  const isAudio =
                    attachment.kind === 'audio' ||
                    attachment.mediaType?.toLowerCase().startsWith('audio/') === true
                  const isImage =
                    attachment.kind === 'image' ||
                    attachment.mediaType?.toLowerCase().startsWith('image/') === true
                  const isVideo =
                    attachment.kind === 'video' ||
                    attachment.mediaType?.toLowerCase().startsWith('video/') === true

                  if (isAudio) {
                    return (
                      <div
                        key={attachment.attachmentId}
                        className="rounded-lg border border-border-default bg-elevated px-4 py-3"
                      >
                        <div className="label-eyebrow">VOICE REPLY</div>
                        <div className="mt-2 type-body-sm text-text-primary">{attachment.filename}</div>
                        <audio className="mt-3 w-full" controls autoPlay preload="none">
                          <source src={href} type={attachment.mediaType ?? 'audio/mpeg'} />
                        </audio>
                      </div>
                    )
                  }

                  if (isImage) {
                    return (
                      <a
                        key={attachment.attachmentId}
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className="block overflow-hidden rounded-lg border border-border-default bg-elevated transition hover:border-accent-primary"
                      >
                        <div className="px-4 py-3">
                          <div className="label-eyebrow">OBRAZ</div>
                          <div className="mt-2 type-body-sm text-text-primary">{attachment.filename}</div>
                        </div>
                        <img
                          src={href}
                          alt={attachment.filename}
                          className="max-h-[420px] w-full border-t border-border-default object-contain bg-shell"
                        />
                      </a>
                    )
                  }

                  if (isVideo) {
                    return (
                      <div
                        key={attachment.attachmentId}
                        className="overflow-hidden rounded-lg border border-border-default bg-elevated"
                      >
                        <div className="px-4 py-3">
                          <div className="label-eyebrow">WIDEO</div>
                          <div className="mt-2 type-body-sm text-text-primary">
                            {attachment.filename}
                          </div>
                          <div className="mt-1 type-mono-sm text-text-tertiary">
                            {(attachment.fileExtension ?? attachment.kind).toUpperCase()} /{' '}
                            {formatBytes(attachment.sizeBytes)}
                          </div>
                        </div>
                        <video
                          controls
                          preload="metadata"
                          className="aspect-video w-full border-t border-border-default bg-shell"
                        >
                          <source src={href} type={attachment.mediaType ?? 'video/mp4'} />
                        </video>
                      </div>
                    )
                  }

                  return (
                    <a
                      key={attachment.attachmentId}
                      href={href}
                      target="_blank"
                      rel="noreferrer"
                      className="block rounded-lg border border-border-default bg-elevated px-4 py-3 transition hover:border-accent-primary"
                    >
                      <div className="label-eyebrow">ZALACZNIK</div>
                      <div className="mt-2 type-body-sm text-text-primary">{attachment.filename}</div>
                      <div className="mt-1 type-mono-sm text-text-tertiary">
                        {(attachment.fileExtension ?? attachment.kind).toUpperCase()} ·{' '}
                        {formatBytes(attachment.sizeBytes)}
                      </div>
                    </a>
                  )
                })}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </motion.article>
  )
}

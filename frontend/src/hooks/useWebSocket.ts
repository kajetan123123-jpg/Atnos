import { useEffect, useRef } from 'react'
import {
  fetchConversationDetail,
  normalizeAccessChange,
  normalizeAccessRealitySnapshot,
  normalizeAmbientState,
  normalizeContentReadinessSnapshot,
  normalizePipelineSnapshot,
  normalizeRemoteExecutionRun,
  normalizeRemoteWorkerSummary,
  normalizeTrustedAutoExecutionRun,
  normalizeTrustedAutoHardwareSnapshot,
  normalizeTrustedAutoSchedulerJob,
} from '../lib/api'
import { WSClient, type WSConnectionEvent, type WSIncomingFrame } from '../lib/ws'
import { useChatStore } from '../stores/chatStore'
import { useContentMachineStore } from '../stores/contentMachineStore'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import {
  messageToConversationMessage,
  type Message as CanonicalMessage,
} from '../types/message'
import type { ContentJob, PipelineStep, PipelineStepId } from '../types/pipeline'
import type { LogEntry, PipelineStepId as ContentMachineStepId } from '../types/contentMachine'

const AGENT_WEBSOCKET_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/agent'

type SocketPayload = Record<string, unknown>
type SocketFrame = {
  type?: unknown
  payload?: unknown
}

function isSocketPayload(value: unknown): value is SocketPayload {
  return typeof value === 'object' && value != null
}

function framePayload(frame: WSIncomingFrame | SocketFrame): SocketPayload {
  if (isSocketPayload(frame.payload)) {
    return frame.payload
  }
  if (!isSocketPayload(frame)) {
    return {}
  }
  return Object.fromEntries(Object.entries(frame).filter(([key]) => key !== 'type'))
}

function resolveGraphStage(stepType: string | null | undefined) {
  const normalized = String(stepType ?? '')
  if (
    normalized === 'graph.research' ||
    normalized === 'graph.script' ||
    normalized === 'graph.metadata' ||
    normalized === 'graph.analytics_sync' ||
    normalized === 'graph.memory_update'
  ) {
    return 'thinking' as const
  }
  if (!normalized) {
    return 'executing' as const
  }
  return 'executing' as const
}

function isTerminalRunStatus(status: string | null | undefined) {
  const normalized = String(status ?? '')
  return (
    normalized === 'done' ||
    normalized === 'failed' ||
    normalized === 'blocked' ||
    normalized === 'interrupted' ||
    normalized === 'cancelled'
  )
}

function localizeStepName(stepName: string | null | undefined) {
  const normalized = String(stepName ?? '').trim()
  const compact = normalized.split('.').at(-1) ?? normalized
  const labels: Record<string, string> = {
    classify_task: 'Klasyfikuje zadanie',
    route_to_skill: 'Wybiera skill',
    retrieve_memory: 'Czyta pamiec projektu',
    plan_steps: 'Uklada plan',
    risk_check: 'Sprawdza ryzyko',
    approval_gate: 'Czeka na decyzje',
    execute_tool: 'Wykonuje narzedzie',
    verify_result: 'Weryfikuje wynik',
    write_memory: 'Zapisuje pamiec',
    respond_to_user: 'Sklada odpowiedz',
    research: 'Zbiera kontekst',
    script: 'Pisze skrypt',
    script_generation: 'Pisze skrypt',
    script_quality_gate: 'Sprawdza jakosc skryptu',
    script_repair: 'Poprawia skrypt',
    tts: 'Generuje lektora',
    subtitles: 'Tworzy napisy',
    thumbnail: 'Tworzy miniaturke',
    render: 'Renderuje finalny plik',
    render_qa: 'Sprawdza finalny wynik',
    memory_update: 'Zapisuje pamiec',
    analytics_sync: 'Synchronizuje analityke',
  }
  return labels[compact] ?? (compact.replace(/_/g, ' ') || 'ATNOS realizuje workflow')
}

function normalizeBrowserRuntime(value: SocketPayload) {
  return {
    profileDir: String(value.profile_dir ?? ''),
    downloadsDir: String(value.downloads_dir ?? ''),
    traceDir: String(value.trace_dir ?? ''),
    browsersPath: String(value.browsers_path ?? ''),
    browserExecutable:
      value.browser_executable == null ? null : String(value.browser_executable),
    desktopShell: value.desktop_shell == null ? null : String(value.desktop_shell),
    ready: Boolean(value.ready),
    statusReason: String(value.status_reason ?? ''),
  }
}

function normalizeMessageRole(value: unknown) {
  if (value === 'user' || value === 'assistant' || value === 'system' || value === 'tool') {
    return value
  }
  return 'assistant'
}

function normalizeCanonicalMessage(payload: SocketPayload): CanonicalMessage | null {
  const speaker =
    payload.speaker === 'atnos' || payload.speaker === 'user'
      ? payload.speaker
      : null

  if (speaker == null) {
    return null
  }

  if (payload.text == null) {
    return null
  }

  const rawTimestamp = payload.timestamp
  const parsedTimestamp =
    typeof rawTimestamp === 'number' ? rawTimestamp : Date.parse(String(rawTimestamp ?? ''))

  return {
    id: payload.id == null ? `ws-${crypto.randomUUID()}` : String(payload.id),
    speaker,
    text: String(payload.text),
    timestamp: Number.isFinite(parsedTimestamp) ? parsedTimestamp : Date.now(),
    attachments: Array.isArray(payload.attachments)
      ? payload.attachments
          .filter(isSocketPayload)
          .map((attachment) => ({
            name: String(attachment.name ?? 'attachment'),
            url: String(attachment.url ?? ''),
            mime: String(attachment.mime ?? 'application/octet-stream'),
          }))
          .filter((attachment) => attachment.url.length > 0)
      : undefined,
    meta: isSocketPayload(payload.meta)
      ? {
          tokensIn:
            payload.meta.tokensIn == null ? undefined : Number(payload.meta.tokensIn),
          tokensOut:
            payload.meta.tokensOut == null ? undefined : Number(payload.meta.tokensOut),
          latencyMs:
            payload.meta.latencyMs == null ? undefined : Number(payload.meta.latencyMs),
          model:
            payload.meta.model == null ? undefined : String(payload.meta.model),
        }
      : undefined,
  }
}

function normalizePipelineFrameStep(value: SocketPayload): PipelineStep {
  return {
    id: String(value.id ?? value.step_id ?? 'research') as PipelineStepId,
    label: value.label == null ? undefined : String(value.label),
    state: String(value.state ?? 'pending') as PipelineStep['state'],
    progress: Number(value.progress ?? 0),
    durationMs: value.duration_ms == null ? null : Number(value.duration_ms),
    errorMessage:
      value.error_message == null ? null : String(value.error_message),
  }
}

function normalizePipelineFrameJob(value: SocketPayload): ContentJob | null {
  const rawId = value.id ?? value.job_id ?? value.creator_project_id
  if (rawId == null) {
    return null
  }

  const createdAt =
    value.created_at == null
      ? Date.now()
      : Date.parse(String(value.created_at))

  return {
    id: String(rawId),
    title: String(value.title ?? 'ATNOS job'),
    createdAt: Number.isNaN(createdAt) ? Date.now() : createdAt,
    steps: Array.isArray(value.steps)
      ? value.steps.map((step) => normalizePipelineFrameStep(step as SocketPayload))
      : [],
    thumbnailUrl:
      value.thumbnail_url == null ? undefined : String(value.thumbnail_url),
    videoUrl: value.video_url == null ? undefined : String(value.video_url),
    platform: 'youtube',
    awaitingApproval: Boolean(value.awaitingApproval ?? value.awaiting_approval ?? false),
    approvalDeadline:
      value.approvalDeadline == null && value.approval_deadline == null
        ? undefined
        : Number(
            value.approvalDeadline ?? Date.parse(String(value.approval_deadline)),
          ),
  }
}

function resolveLastKnownMessageId(messages: ReturnType<typeof useChatStore.getState>['messages']) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const id = messages[index]?.id
    if (typeof id === 'number' && Number.isFinite(id)) {
      return id
    }
    if (typeof id === 'string' && /^\d+$/.test(id)) {
      return Number(id)
    }
  }
  return null
}

async function resyncActiveConversationAfterReconnect(lastKnownMessageId: number | null) {
  const chatStore = useChatStore.getState()
  if (chatStore.selectedConversationId == null) {
    return
  }

  const detail = await fetchConversationDetail(chatStore.selectedConversationId)
  const latestMessageId = resolveLastKnownMessageId(detail.messages)
  if (
    lastKnownMessageId != null &&
    latestMessageId != null &&
    latestMessageId <= lastKnownMessageId &&
    chatStore.hydratedConversationId === detail.conversation.id
  ) {
    return
  }

  useChatStore.getState().resyncConversationDetail({
    conversationId: detail.conversation.id,
    title: detail.conversation.title,
    projectId: detail.conversation.projectId,
    summary: detail.conversation.summary,
    lastMessageAt: detail.conversation.lastMessageAt,
    updatedAt: detail.conversation.updatedAt,
    messages: detail.messages,
    settings: detail.settings,
    memoryCards: detail.memoryCards,
  })
}

function updateWebSocketStatus(event: WSConnectionEvent) {
  const systemStore = useSystemStore.getState()
  if (event.state === 'open') {
    systemStore.setWebSocketStatus({ status: 'connected' })
    systemStore.setMetrics({ chroma: 'connected' })
    if (systemStore.agentStage === 'offline') {
      systemStore.setStage('idle')
    }
    return
  }

  if (event.state === 'connecting') {
    systemStore.setWebSocketStatus({ status: 'connecting' })
    systemStore.setMetrics({ chroma: 'reconnecting' })
    return
  }

  if (event.state === 'reconnecting') {
    systemStore.setWebSocketStatus({
      status: 'reconnecting',
      retryCount: event.retryCount,
      nextRetryMs: event.nextDelayMs,
    })
    systemStore.setMetrics({ chroma: 'reconnecting', rttMs: null })
    return
  }

  if (event.state === 'background_retry') {
    systemStore.setWebSocketStatus({
      status: 'background_retry',
      retryCount: event.retryCount,
      nextRetryMs: event.nextDelayMs,
    })
    systemStore.setMetrics({ chroma: 'down', rttMs: null })
    return
  }

  systemStore.setWebSocketStatus({
    status: 'disconnected',
    retryCount: event.retryCount,
    nextRetryMs: event.nextDelayMs,
  })
}

function normalizeContentMachineLogStep(value: unknown): LogEntry['step'] {
  const normalized = String(value ?? 'system').trim().toLowerCase().replace(/[_\s.-]+/g, '')
  const aliases: Record<string, ContentMachineStepId | 'fleet' | 'system'> = {
    topic: 'topics',
    topics: 'topics',
    tematy: 'topics',
    script: 'script',
    skrypt: 'script',
    voice: 'voice',
    glos: 'voice',
    głos: 'voice',
    tts: 'voice',
    video: 'video',
    wideo: 'video',
    render: 'video',
    assembly: 'assembly',
    montaz: 'assembly',
    qa: 'qa',
    quality: 'qa',
    qualitygate: 'qa',
    upload: 'upload',
    fleet: 'fleet',
    system: 'system',
  }
  return aliases[normalized] ?? 'system'
}

export function useWebSocket(options: { forceConnect?: boolean; silentBackgroundRetry?: boolean } = {}) {
  const bootstrapHydrated = useChatStore((state) => state.bootstrapHydrated)
  const backgroundRetryToastShownRef = useRef(false)
  const forceConnect = options.forceConnect ?? false
  const silentBackgroundRetry = options.silentBackgroundRetry ?? false

  useEffect(() => {
    if (!bootstrapHydrated && !forceConnect) {
      return
    }

    const client = new WSClient({
      url: AGENT_WEBSOCKET_URL,
      backoffBaseMs: 3000,
      backoffMaxMs: 30000,
      backgroundRetryMs: 30000,
      maxFastReconnectAttempts: 10,
      onStatusChange: (event) => {
        updateWebSocketStatus(event)
        if (
          event.state === 'background_retry' &&
          !backgroundRetryToastShownRef.current &&
          !silentBackgroundRetry
        ) {
          backgroundRetryToastShownRef.current = true
          useToastStore.getState().push({
            type: 'warning',
            title: 'Live sync odzyskuje polaczenie',
            message:
              'WebSocket nie wrocil po 10 probach. UI zostaje aktywne, a Atnos probuje dalej w tle co 30 sekund.',
          })
        }
      },
      onOpen: (event) => {
        backgroundRetryToastShownRef.current = false
        if (event.retryCount > 0) {
          const lastKnownMessageId = resolveLastKnownMessageId(useChatStore.getState().messages)
          void resyncActiveConversationAfterReconnect(lastKnownMessageId).catch(() => {
            useToastStore.getState().push({
              type: 'warning',
              title: 'Resync rozmowy nieudany',
              message:
                'WebSocket wrocil, ale nie udalo sie odswiezyc aktywnej rozmowy. UI nadal dziala i sprobuje przy kolejnym reconnect.',
            })
          })
        }
      },
      onClose: () => {
        const systemStore = useSystemStore.getState()
        if (systemStore.network === 'offline') {
          systemStore.setStage('offline')
        }
      },
      onMessage: (rawFrame) => {
        let frame: WSIncomingFrame | SocketFrame

        try {
          frame = JSON.parse(rawFrame) as WSIncomingFrame | SocketFrame
        } catch {
          return
        }

        const frameType = typeof frame.type === 'string' ? frame.type : ''
        const payload = framePayload(frame)

        if (frameType === 'pipeline_step') {
          const accountId = payload.accountId ?? payload.account_id
          const step = payload.step
          const status = payload.status
          if (accountId != null && step != null && status != null) {
            useContentMachineStore.getState().ingestPipelineStep({
              accountId: String(accountId),
              jobId: payload.jobId == null && payload.job_id == null ? undefined : String(payload.jobId ?? payload.job_id),
              title: payload.title == null ? undefined : String(payload.title),
              step: String(step),
              status: String(status),
              progress: payload.progress == null ? undefined : Number(payload.progress),
              etaSeconds: payload.eta == null && payload.etaSeconds == null && payload.eta_seconds == null
                ? undefined
                : Number(payload.eta ?? payload.etaSeconds ?? payload.eta_seconds),
              durationMs: payload.durationMs == null && payload.duration_ms == null
                ? undefined
                : Number(payload.durationMs ?? payload.duration_ms),
              error: payload.error == null ? undefined : String(payload.error),
              videoId: payload.videoId == null && payload.video_id == null ? undefined : String(payload.videoId ?? payload.video_id),
              videoUrl: payload.videoUrl == null && payload.video_url == null ? undefined : String(payload.videoUrl ?? payload.video_url),
            })
          }
          return
        }

        if (frameType === 'job_log') {
          const accountId = payload.accountId ?? payload.account_id
          const message = payload.message
          if (accountId != null && message != null) {
            useContentMachineStore.getState().appendLog({
              accountId: String(accountId),
              jobId: payload.jobId == null && payload.job_id == null ? undefined : String(payload.jobId ?? payload.job_id),
              step: normalizeContentMachineLogStep(payload.step),
              level: String(payload.level ?? 'info') as LogEntry['level'],
              message: String(message),
            })
          }
          return
        }

        if (frameType === 'topics_generated') {
          const accountId = payload.accountId ?? payload.account_id
          if (accountId != null) {
            useContentMachineStore.getState().appendLog({
              accountId: String(accountId),
              step: 'topics',
              level: 'info',
              message: `Wygenerowano ${String(payload.count ?? 0)} tematów. Top: ${String(payload.top_topic ?? '-')}`,
            })
            window.dispatchEvent(
              new CustomEvent('atnos:topics-generated', {
                detail: { accountId: String(accountId) },
              }),
            )
          }
          return
        }

        if (frameType === 'metrics') {
          const currentMetrics = useSystemStore.getState()
          useSystemStore.getState().setMetrics({
            vramUsedMB: Number(payload.vramUsedMB ?? currentMetrics.vramUsedMB ?? 0),
            vramTotalMB: Number(payload.vramTotalMB ?? currentMetrics.vramTotalMB ?? 16384),
            model: {
              name: String((payload.model as SocketPayload | undefined)?.name ?? currentMetrics.model.name),
              provider: String(
                (payload.model as SocketPayload | undefined)?.provider ?? currentMetrics.model.provider,
              ) as 'ollama' | 'openai' | 'anthropic' | 'gemini',
            },
            chroma: String(payload.chroma ?? 'down') as 'connected' | 'reconnecting' | 'down',
            rttMs: payload.rttMs == null ? null : Number(payload.rttMs),
          })
          return
        }

        if (frameType === 'stage') {
          useSystemStore
            .getState()
            .setStage(
              String(payload.stage ?? 'idle') as ReturnType<
                typeof useSystemStore.getState
              >['agentStage'],
            )
          return
        }

        if (frameType === 'message') {
          const canonicalMessage = normalizeCanonicalMessage(payload)
          if (canonicalMessage) {
            useChatStore
              .getState()
              .appendMessage(messageToConversationMessage(canonicalMessage))
            return
          }

          const content =
            payload.content == null
              ? payload.message == null
                ? null
                : String(payload.message)
              : String(payload.content)

          if (!content) {
            return
          }

          useChatStore.getState().appendMessage({
            id: payload.id == null ? `ws-${crypto.randomUUID()}` : String(payload.id),
            role: normalizeMessageRole(payload.role),
            status: String(payload.status ?? 'completed'),
            content,
            createdAt:
              payload.createdAt == null
                ? new Date().toISOString()
                : String(payload.createdAt),
          })
          return
        }

        if (frameType === 'pipeline.step') {
          const stepId = payload.stepId ?? payload.step_id
          if (stepId == null) {
            return
          }

          const patch: Partial<PipelineStep> = {
            state: String(payload.state ?? 'pending') as PipelineStep['state'],
          }

          if (payload.progress != null) {
            patch.progress = Number(payload.progress)
          }
          if (payload.durationMs != null || payload.duration_ms != null) {
            patch.durationMs = Number(payload.durationMs ?? payload.duration_ms)
          }
          if (payload.errorMessage != null || payload.error_message != null) {
            patch.errorMessage = String(payload.errorMessage ?? payload.error_message)
          }

          const jobId = payload.jobId ?? payload.job_id
          if (jobId != null) {
            usePipelineStore.getState().setStepStateForJob(
              String(jobId),
              String(stepId) as PipelineStepId,
              patch as Partial<{
                state: PipelineStep['state']
                progress: number
                errorMessage: string
                durationMs: number
              }>,
            )
          } else {
            usePipelineStore.getState().updateStep(String(stepId), patch)
          }
          return
        }

        if (frameType === 'pipeline.asset') {
          const kind = payload.kind ?? payload.asset_kind
          const url = payload.url ?? payload.asset_url
          if ((kind !== 'thumbnail' && kind !== 'video') || url == null) {
            return
          }

          const jobId = payload.jobId ?? payload.job_id
          if (jobId != null) {
            usePipelineStore
              .getState()
              .attachAssetToJob(String(jobId), kind, String(url))
          } else {
            usePipelineStore.getState().attachAsset(String(url), kind)
          }
          return
        }

        if (frameType === 'pipeline') {
          const currentJobSource =
            payload.currentJob && typeof payload.currentJob === 'object'
              ? (payload.currentJob as SocketPayload)
              : payload.current_job && typeof payload.current_job === 'object'
                ? (payload.current_job as SocketPayload)
                : payload.job && typeof payload.job === 'object'
                  ? (payload.job as SocketPayload)
                  : null

          const currentJob = currentJobSource
            ? normalizePipelineFrameJob(currentJobSource)
            : null
          const history = Array.isArray(payload.history)
            ? payload.history
                .map((job) =>
                  job && typeof job === 'object'
                    ? normalizePipelineFrameJob(job as SocketPayload)
                    : null,
                )
                .filter((job): job is ContentJob => job != null)
            : []

          if (currentJob || history.length > 0) {
            usePipelineStore.getState().hydratePipeline({ currentJob, history })
          }

          if (payload.asset_url && payload.asset_kind) {
            usePipelineStore.getState().attachAsset(
              String(payload.asset_url),
              String(payload.asset_kind) as 'thumbnail' | 'video',
            )
          }

          if (payload.step_id && payload.state) {
            usePipelineStore.getState().setStepState(
              String(payload.step_id) as PipelineStepId,
              String(payload.state) as PipelineStep['state'],
              {
                errorMessage:
                  payload.error_message == null
                    ? null
                    : String(payload.error_message),
              },
            )
          }

          if (payload.step_id && payload.progress != null) {
            usePipelineStore.getState().setStepProgress(
              String(payload.step_id) as PipelineStepId,
              Number(payload.progress),
            )
          }
          return
        }

        if (frameType === 'pipeline.snapshot') {
          usePipelineStore
            .getState()
            .setSnapshot(normalizePipelineSnapshot(payload))
          return
        }

        if (frameType === 'hardware.snapshot') {
          const hardwareSnapshot = normalizeTrustedAutoHardwareSnapshot(payload)
          const systemStore = useSystemStore.getState()
          systemStore.setHardwareSnapshot(hardwareSnapshot)
          if (hardwareSnapshot) {
            const nextMetrics: { vramUsedMB?: number; vramTotalMB?: number } = {}
            if (hardwareSnapshot.usedVramMb != null) {
              nextMetrics.vramUsedMB = hardwareSnapshot.usedVramMb
            }
            if (hardwareSnapshot.totalVramMb != null) {
              nextMetrics.vramTotalMB = hardwareSnapshot.totalVramMb
            }
            if (Object.keys(nextMetrics).length > 0) {
              systemStore.setMetrics(nextMetrics)
            }
          }
          return
        }

        if (frameType === 'ambient.state') {
          useSystemStore.getState().setAmbientState(normalizeAmbientState(payload))
          return
        }

        if (frameType === 'scheduler.job') {
          useSystemStore
            .getState()
            .upsertSchedulerJob(normalizeTrustedAutoSchedulerJob(payload))
          return
        }

        if (frameType === 'execution.run') {
          useSystemStore
            .getState()
            .upsertExecutionRun(normalizeTrustedAutoExecutionRun(payload))
          return
        }

        if (frameType === 'remote.node') {
          useSystemStore
            .getState()
            .upsertRemoteWorker(normalizeRemoteWorkerSummary(payload))
          return
        }

        if (frameType === 'remote.execution.run') {
          useSystemStore
            .getState()
            .upsertRemoteExecutionRun(normalizeRemoteExecutionRun(payload))
          return
        }

        if (frameType === 'graph.node') {
          const runStatus = payload.status == null ? null : String(payload.status)
          if (isTerminalRunStatus(runStatus)) {
            useSystemStore.getState().setStage('idle')
            return
          }

          const stage = resolveGraphStage(
            payload.step_type == null ? null : String(payload.step_type),
          )
          useSystemStore.getState().setStage(stage)
          useSystemStore.getState().setAgentActivity({
            stage,
            title: localizeStepName(
              payload.step_name == null ? null : String(payload.step_name),
            ),
            detail:
              payload.run_type == null
                ? 'ATNOS wykonuje kolejny etap workflow.'
                : `Workflow: ${String(payload.run_type)}`,
          })
          return
        }

        if (frameType === 'graph.progress') {
          const runStatus = payload.status == null ? null : String(payload.status)
          if (isTerminalRunStatus(runStatus)) {
            useSystemStore.getState().setStage('idle')
            return
          }

          const stage = resolveGraphStage(
            payload.current_step == null ? null : String(payload.current_step),
          )
          useSystemStore.getState().setStage(stage)
          useSystemStore.getState().setAgentActivity({
            stage,
            title:
              payload.current_step == null
                ? 'ATNOS realizuje workflow'
                : localizeStepName(String(payload.current_step)),
            detail:
              payload.total_steps == null
                ? 'ATNOS pracuje nad kampanią.'
                : `Krok ${Number(payload.completed_steps ?? 0)}/${Number(payload.total_steps)} workflow.`,
          })
          return
        }

        if (frameType === 'approval.required') {
          useSystemStore.getState().setStage('awaiting_human')
          useSystemStore.getState().setAgentActivity({
            stage: 'awaiting_human',
            title: 'ATNOS czeka na akceptację publikacji',
            detail:
              'Short jest gotowy do review. Kolejny krok to jawna decyzja o publikacji.',
          })
          return
        }

        if (frameType === 'publish.result') {
          const status = String(payload.status ?? 'unknown')
          if (status === 'uploaded') {
            useSystemStore.getState().setAgentActivity({
              stage: 'executing',
              title: 'Publikacja zakończona',
              detail:
                payload.video_id == null
                  ? 'Short został wysłany.'
                  : `Video ID: ${String(payload.video_id)}`,
            })
            return
          }

          if (status === 'awaiting_approval') {
            useSystemStore.getState().setStage('awaiting_human')
            useSystemStore.getState().setAgentActivity({
              stage: 'awaiting_human',
              title: 'ATNOS czeka na akceptację publikacji',
              detail: 'Pakiet publikacji jest gotowy. Czekam na Twoją decyzję.',
            })
          }
          return
        }

        if (frameType === 'analytics.summary') {
          if (String(payload.status ?? '') === 'ready') {
            useSystemStore.getState().setAgentActivity({
              stage: 'executing',
              title: 'ATNOS synchronizuje analitykę',
              detail:
                'Sprawdzam status publikacji i pierwsze sygnały z platformy.',
            })
          }
          return
        }

        if (frameType === 'fallback.used') {
          const providerKey = String(payload.provider_key ?? 'external')
          useSystemStore.getState().setAgentActivity({
            stage: 'thinking',
            title: 'ATNOS używa fallbacku',
            detail: `Workflow przełączył się na ${providerKey}.`,
          })
          return
        }

        if (frameType === 'connector.state') {
          const store = useSystemStore.getState()
          if (store.capabilities) {
            store.setCapabilities({
              ...store.capabilities,
              autonomyMode:
                payload.autonomy_mode == null
                  ? store.capabilities.autonomyMode
                  : String(payload.autonomy_mode),
            })
          }

          if (store.trustedAutoSummary && payload.browser_runtime) {
            store.setTrustedAutoSummary({
              ...store.trustedAutoSummary,
              browserRuntime: normalizeBrowserRuntime(
                payload.browser_runtime as SocketPayload,
              ),
            })
          }
          return
        }

        if (frameType === 'content.readiness') {
          const store = useSystemStore.getState()
          const snapshot = normalizeContentReadinessSnapshot(payload)
          if (!snapshot) {
            return
          }

          const previousKey = store.trustedAutoSummary?.contentReadiness?.snapshotKey
          if (store.trustedAutoSummary) {
            store.setTrustedAutoSummary({
              ...store.trustedAutoSummary,
              contentReadiness: snapshot,
            })
          }

          if (previousKey && previousKey !== snapshot.snapshotKey) {
            useToastStore.getState().push({
              type: 'success',
              title: 'Zmienił się stan kanałów contentowych',
              message: `Publish mode: ${snapshot.summary.publishMode}. YouTube: ${snapshot.summary.primaryYoutubeStatus ?? 'unknown'}.`,
            })
          }
          return
        }

        if (frameType === 'access.reality') {
          useSystemStore
            .getState()
            .setAccessReality(normalizeAccessRealitySnapshot(payload))
          return
        }

        if (frameType === 'access.reality.change') {
          const changes = Array.isArray(payload.changes)
            ? payload.changes.map((change) =>
                normalizeAccessChange(change as SocketPayload),
              )
            : []

          useSystemStore.getState().setAccessRealityChanges(changes)

          for (const change of changes) {
            if (!change.userVisible) {
              continue
            }

            useToastStore.getState().push({
              type:
                change.toAccessLevel === 'blocked' ||
                change.toAccessLevel === 'unavailable'
                  ? 'warning'
                  : 'success',
              title: change.title,
              message: change.detail || undefined,
            })
          }
          return
        }

        if (frameType === 'toast') {
          useToastStore.getState().push({
            type: String(payload.type ?? 'warning') as never,
            title: String(payload.title ?? 'ATNOS'),
            message: payload.message == null ? undefined : String(payload.message),
          })
          return
        }

        if (frameType === 'alert') {
          useToastStore.getState().push({
            type: String(payload.severity ?? 'warning') as never,
            title: String(payload.title ?? 'ATNOS'),
            message: payload.message == null ? undefined : String(payload.message),
          })
        }
      },
    })

    client.connect()

    return () => {
      client.disconnect()
    }
  }, [bootstrapHydrated, forceConnect, silentBackgroundRetry])
}

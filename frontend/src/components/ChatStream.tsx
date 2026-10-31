import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ArrowDown, LoaderCircle, X } from 'lucide-react'
import type { ConversationMessage } from '../types/message'
import type { AgentStage } from '../types/agent'
import type { AgentActivityState } from '../types/system'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { CHAT_STATUS } from '../lib/chatStatusCopy'
import { transcribeVoice } from '../lib/api'
import { useToastStore } from '../stores/toastStore'
import { ChatInput, type ChatInputSubmitPayload } from './ChatInput'
import { ChatMessage } from './ChatMessage'
import { TypingIndicator } from './TypingIndicator'

const AUTO_SCROLL_BOTTOM_THRESHOLD_PX = 50

export interface ChatActiveWorkStatus {
  title: string
  detail: string
  statusLabel?: string
  stepLabel?: string | null
}

export interface ChatQueuedMessagePreview {
  id: string
  text: string
  targetLabel: string
}

function getAgentStageLabel(stage: AgentStage) {
  switch (stage) {
    case 'thinking':
      return 'Analizuję'
    case 'executing':
      return 'Wykonuję'
    case 'awaiting_human':
      return 'Czekam na Ciebie'
    case 'error':
      return 'Błąd'
    case 'offline':
      return 'Offline'
    default:
      return 'Gotowy'
  }
}

function resolveLiveWorkStatus(
  agentStage: AgentStage,
  agentActivity: AgentActivityState | null | undefined,
  activeWorkStatus: ChatActiveWorkStatus | null | undefined,
  showTypingIndicator: boolean,
): ChatActiveWorkStatus | null {
  if (activeWorkStatus) {
    return activeWorkStatus
  }
  if (
    agentStage !== 'thinking' &&
    agentStage !== 'executing' &&
    agentStage !== 'awaiting_human' &&
    !showTypingIndicator
  ) {
    return null
  }
  return {
    title: agentActivity?.title ?? CHAT_STATUS.answering,
    detail:
      agentActivity?.detail ??
      (agentStage === 'thinking'
        ? 'Porządkuję kontekst i wybieram najlepszy tor.'
        : agentStage === 'awaiting_human'
          ? 'Potrzebuję Twojej decyzji, żeby iść dalej.'
          : 'Wykonuję kolejne kroki i zbieram wyniki.'),
    statusLabel: getAgentStageLabel(agentActivity?.stage ?? agentStage),
  }
}

export function ChatStream({
  messages,
  agentStage,
  agentActivity,
  activeWorkStatus,
  queuedMessages = [],
  showTypingIndicator = false,
  generating = false,
  loadingConversation = false,
  hasActiveConversation = false,
  onSubmit,
  onCancelGeneration,
  onRemoveQueuedMessage,
  onRetryRuntimeError,
  onRetryUserMessage,
  onQuestionAnswered,
  onResolveDecision,
  onRefreshBriefing,
  onImprovePrompt,
  onNewConversation,
  inputFocusSignal = 0,
  inputValue,
  onInputValueChange,
  inputFilesEnabled = true,
  inputHeader,
  maxWidthClass = 'shell-content-width',
}: {
  messages: ConversationMessage[]
  agentStage: AgentStage
  agentActivity?: AgentActivityState | null
  activeWorkStatus?: ChatActiveWorkStatus | null
  queuedMessages?: ChatQueuedMessagePreview[]
  showTypingIndicator?: boolean
  /** True while any generation is in flight (may be for another conversation). */
  generating?: boolean
  loadingConversation?: boolean
  hasActiveConversation?: boolean
  onSubmit: (payload: ChatInputSubmitPayload) => boolean | Promise<boolean>
  onCancelGeneration?: () => void
  onRemoveQueuedMessage?: (id: string) => void
  onRetryRuntimeError?: (
    assistantMessageId: number | string,
    options?: { forceExternal?: boolean },
  ) => void
  onRetryUserMessage?: (userMessageId: number | string) => void
  onQuestionAnswered?: (conversationId: number) => void | Promise<void>
  onResolveDecision?: (decisionId: number) => void | Promise<void>
  onRefreshBriefing?: () => void | Promise<void>
  onImprovePrompt?: (
    draft: string,
  ) => Promise<{ text: string; strategyNote?: string; changed?: boolean } | null>
  onNewConversation?: () => void
  inputFocusSignal?: number
  inputValue?: string
  onInputValueChange?: (value: string) => void
  inputFilesEnabled?: boolean
  inputHeader?: ReactNode
  maxWidthClass?: string
}) {
  const reducedMotion = useReducedMotionStrict()
  const scrollContainerRef = useRef<HTMLDivElement | null>(null)
  const pinnedToBottomRef = useRef(true)
  const previousMessageCountRef = useRef(-1)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const voiceChunksRef = useRef<BlobPart[]>([])
  const pushToTalkActiveRef = useRef(false)
  const [voiceRecording, setVoiceRecording] = useState(false)
  const [screenAwareBusy, setScreenAwareBusy] = useState(false)
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true)
  const [hasUnreadActivity, setHasUnreadActivity] = useState(false)
  const pushToast = useToastStore((state) => state.push)
  const orderedMessages = useMemo(
    () =>
      messages
        .map((message, index) => ({ message, index }))
        .sort((left, right) => {
          if (
            left.message.sequenceNumber != null &&
            right.message.sequenceNumber != null &&
            left.message.sequenceNumber !== right.message.sequenceNumber
          ) {
            return left.message.sequenceNumber - right.message.sequenceNumber
          }

          const leftCreatedAt = Date.parse(left.message.createdAt ?? '')
          const rightCreatedAt = Date.parse(right.message.createdAt ?? '')
          if (
            Number.isFinite(leftCreatedAt) &&
            Number.isFinite(rightCreatedAt) &&
            leftCreatedAt !== rightCreatedAt
          ) {
            return leftCreatedAt - rightCreatedAt
          }

          return left.index - right.index
        })
        .map(({ message }) => message),
    [messages],
  )
  const liveWorkStatus = resolveLiveWorkStatus(
    agentStage,
    agentActivity,
    activeWorkStatus,
    showTypingIndicator,
  )

  const isNodePinnedToBottom = useCallback((node: HTMLDivElement) => {
    const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight
    return distanceFromBottom <= AUTO_SCROLL_BOTTOM_THRESHOLD_PX
  }, [])

  const syncPinnedToBottom = useCallback(() => {
    const node = scrollContainerRef.current
    if (!node) return true
    const nextPinned = isNodePinnedToBottom(node)
    pinnedToBottomRef.current = nextPinned
    setIsPinnedToBottom(nextPinned)
    if (nextPinned) {
      setHasUnreadActivity(false)
    }
    return nextPinned
  }, [isNodePinnedToBottom])

  const markReaderMovedAwayFromBottom = useCallback(() => {
    pinnedToBottomRef.current = false
    setIsPinnedToBottom(false)
    setHasUnreadActivity(true)
  }, [])

  const scrollToBottom = useCallback(
    (behavior: ScrollBehavior = reducedMotion ? 'auto' : 'smooth') => {
      const node = scrollContainerRef.current
      if (!node) return
      pinnedToBottomRef.current = true
      setIsPinnedToBottom(true)
      setHasUnreadActivity(false)

      const nextScrollTop = Math.max(0, node.scrollHeight - node.clientHeight)
      if (typeof node.scrollTo === 'function') {
        node.scrollTo({
          top: nextScrollTop,
          left: 0,
          behavior,
        })
        return
      }

      node.scrollTop = nextScrollTop
    },
    [reducedMotion],
  )

  useEffect(() => {
    const node = scrollContainerRef.current
    const messageCountChanged = previousMessageCountRef.current !== orderedMessages.length
    previousMessageCountRef.current = orderedMessages.length

    if (!messageCountChanged && node && !isNodePinnedToBottom(node)) {
      markReaderMovedAwayFromBottom()
      return
    }

    if (!pinnedToBottomRef.current) {
      setHasUnreadActivity(true)
      return
    }
    scrollToBottom()
  }, [
    agentStage,
    liveWorkStatus?.detail,
    liveWorkStatus?.statusLabel,
    liveWorkStatus?.stepLabel,
    liveWorkStatus?.title,
    isNodePinnedToBottom,
    markReaderMovedAwayFromBottom,
    orderedMessages.length,
    scrollToBottom,
    showTypingIndicator,
  ])

  const stopMediaStream = useCallback(() => {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
    mediaStreamRef.current = null
  }, [])

  const appendTranscriptToComposer = useCallback(
    (transcript: string) => {
      const cleaned = transcript.trim()
      if (!cleaned) return
      const current = inputValue ?? ''
      const separator = current.trim() && !current.endsWith(' ') ? ' ' : ''
      onInputValueChange?.(`${current}${separator}${cleaned}`)
    },
    [inputValue, onInputValueChange],
  )

  const transcribeRecording = useCallback(
    async (audio: Blob) => {
      if (audio.size === 0) {
        pushToast({
          type: 'warning',
          title: 'Nagranie jest puste',
          message: 'Przytrzymaj Ctrl+Shift+Space dluzej i sprobuj ponownie.',
        })
        return
      }

      try {
        const result = await transcribeVoice(audio, {
          filename: 'push-to-talk.webm',
          language: 'pl',
          transcriptHint: inputValue,
        })
        appendTranscriptToComposer(result.transcript)
        pushToast({
          type: 'success',
          title: 'Dyktowanie gotowe',
          message: `Transkrypt trafil do composera (${result.engine}).`,
        })
      } catch (error) {
        pushToast({
          type: 'error',
          title: 'Transkrypcja nieudana',
          message: error instanceof Error ? error.message : 'Nieznany blad transkrypcji.',
        })
      }
    },
    [appendTranscriptToComposer, inputValue, pushToast],
  )

  const stopVoiceRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
      return
    }
    setVoiceRecording(false)
    stopMediaStream()
  }, [stopMediaStream])

  const startVoiceRecording = useCallback(async () => {
    if (voiceRecording) return
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      pushToast({
        type: 'error',
        title: 'Mikrofon niedostepny',
        message: 'Przegladarka nie udostepnia MediaDevices API.',
      })
      return
    }
    if (typeof MediaRecorder === 'undefined') {
      pushToast({
        type: 'error',
        title: 'Nagrywanie niedostepne',
        message: 'Ta przegladarka nie obsluguje MediaRecorder.',
      })
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType =
        typeof MediaRecorder.isTypeSupported === 'function' &&
        MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
          ? 'audio/webm;codecs=opus'
          : undefined
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      voiceChunksRef.current = []
      mediaStreamRef.current = stream
      mediaRecorderRef.current = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          voiceChunksRef.current.push(event.data)
        }
      }
      recorder.onerror = () => {
        setVoiceRecording(false)
        stopMediaStream()
        pushToast({
          type: 'error',
          title: 'Nagrywanie przerwane',
          message: 'Przegladarka zwrocila blad podczas nagrywania.',
        })
      }
      recorder.onstop = () => {
        const audio = new Blob(voiceChunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        })
        voiceChunksRef.current = []
        mediaRecorderRef.current = null
        setVoiceRecording(false)
        stopMediaStream()
        void transcribeRecording(audio)
      }
      recorder.start()
      setVoiceRecording(true)
    } catch (error) {
      setVoiceRecording(false)
      stopMediaStream()
      pushToast({
        type: 'error',
        title: 'Nie udalo sie uruchomic mikrofonu',
        message: error instanceof Error ? error.message : 'Sprawdz uprawnienia mikrofonu.',
      })
    }
  }, [pushToast, stopMediaStream, transcribeRecording, voiceRecording])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.code !== 'Space' || !event.ctrlKey || !event.shiftKey || event.repeat) {
        return
      }
      event.preventDefault()
      pushToTalkActiveRef.current = true
      void startVoiceRecording()
    }

    function handleKeyUp(event: KeyboardEvent) {
      if (event.code !== 'Space' || !pushToTalkActiveRef.current) {
        return
      }
      event.preventDefault()
      pushToTalkActiveRef.current = false
      stopVoiceRecording()
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
    }
  }, [startVoiceRecording, stopVoiceRecording])

  useEffect(() => {
    return () => {
      stopVoiceRecording()
      stopMediaStream()
    }
  }, [stopMediaStream, stopVoiceRecording])

  const handleSubmit = useCallback(
    (payload: ChatInputSubmitPayload) => {
      scrollToBottom('auto')
      return onSubmit(payload)
    },
    [onSubmit, scrollToBottom],
  )

  const handleScreenAwareTurn = useCallback(async () => {
    if (screenAwareBusy) return
    setScreenAwareBusy(true)
    try {
      const focus = (inputValue ?? '').trim()
      const text = focus
        ? `Spójrz na mój ekran (screen_capture_understand) i działaj: ${focus}`
        : 'Spójrz na mój ekran (użyj screen_capture_understand), zrozum co widzisz i wykonaj najbardziej sensowne następne działanie.'
      await Promise.resolve(
        onSubmit({
          text,
          attachments: [],
        }),
      )
      onInputValueChange?.('')
    } finally {
      setScreenAwareBusy(false)
    }
  }, [inputValue, onInputValueChange, onSubmit, screenAwareBusy])

  const shouldShowTypingIndicator = agentStage === 'thinking' && showTypingIndicator

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollContainerRef}
        data-testid="chat-scroll-container"
        onScroll={syncPinnedToBottom}
        onWheel={(event) => {
          if (event.deltaY < 0) {
            markReaderMovedAwayFromBottom()
          }
        }}
        className="scroll-chrome flex-1 overflow-y-auto overflow-x-hidden px-3 pb-6 pt-4 sm:px-4"
      >
        <div className={`mx-auto flex w-full ${maxWidthClass} flex-col gap-2.5 sm:gap-3`}>
          {orderedMessages.length === 0 && loadingConversation ? (
            <div
              data-testid="chat-loading-state"
              className="shell-panel flex items-center justify-center gap-3 px-5 py-8 text-text-secondary"
            >
              <LoaderCircle size={20} className="animate-spin text-accent-primary" aria-hidden />
              <div>
                <div className="type-body text-text-primary">{CHAT_STATUS.loadingConversation}</div>
                <div className="mt-1 type-body-sm text-text-tertiary">
                  Pobieram historię, ustawienia i pamięć projektu.
                </div>
              </div>
            </div>
          ) : null}
          {orderedMessages.length === 0 && !loadingConversation && hasActiveConversation ? (
            <div
              data-testid="chat-empty-active"
              className="shell-panel px-5 py-8 text-center"
            >
              <div className="label-eyebrow">PUSTA ROZMOWA</div>
              <div className="mt-3 type-h3 text-text-primary">
                Ta rozmowa jeszcze nie ma wiadomości.
              </div>
              <p className="mx-auto mt-3 max-w-xl type-body-sm text-text-secondary">
                Napisz pierwsze polecenie w composerze poniżej. Atnos sam dobierze model, narzędzia
                i tryb wykonania.
              </p>
            </div>
          ) : null}
          {orderedMessages.length === 0 && !loadingConversation && !hasActiveConversation ? (
            <div
              data-testid="chat-empty-new"
              className="shell-hero px-6 py-12"
            >
              <div className="label-eyebrow">CHAT</div>
              <h1 className="mt-2 type-h1 text-text-primary">
                Zacznij nową rozmowę z Atnosem
              </h1>
              <p className="mt-4 max-w-2xl type-body text-text-secondary">
                Powiedz, co chcesz zrobić: napisać kod, zaplanować shorta, przejrzeć materiał
                albo zorganizować pracę. Atnos dobierze odpowiedni model i narzędzia.
              </p>
              {onNewConversation ? (
                <button
                  type="button"
                  onClick={onNewConversation}
                  className="shell-magnetic mt-6 inline-flex items-center gap-2 rounded-2xl border border-accent-primary/45 bg-accent-primary/15 px-4 py-2.5 text-sm font-semibold text-accent-primary shadow-glow-soft transition hover:border-accent-primary hover:bg-accent-primary/20 hover:shadow-glow-medium"
                >
                  Nowa rozmowa
                </button>
              ) : null}
            </div>
          ) : null}
          {orderedMessages.map((message, index) => (
            <ChatMessage
              key={String(message.id)}
              message={message}
              animationIndex={index}
              onRetryRuntimeError={onRetryRuntimeError}
              onRetryUserMessage={onRetryUserMessage}
              onQuestionAnswered={onQuestionAnswered}
              onResolveDecision={onResolveDecision}
              onRefreshBriefing={onRefreshBriefing}
            />
          ))}
          {shouldShowTypingIndicator ? <TypingIndicator /> : null}
        </div>
      </div>
      {liveWorkStatus || (hasUnreadActivity && !isPinnedToBottom) ? (
        <div className="px-4 pb-3">
          <div className={`mx-auto flex w-full ${maxWidthClass} flex-wrap items-center justify-between gap-3 rounded-lg border border-accent-primary/30 bg-shell/95 px-3 py-3 shadow-glow-soft backdrop-blur`}>
            {liveWorkStatus ? (
              <div className="flex min-w-0 items-center gap-3">
                <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-accent-primary/35 bg-accent-primary/10 text-accent-primary">
                  <LoaderCircle size={16} className="animate-spin" aria-hidden />
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-text-primary">
                    <span>{liveWorkStatus.title}</span>
                    {liveWorkStatus.statusLabel ? (
                      <span className="rounded-pill border border-accent-primary/30 px-2 py-0.5 text-[11px] text-accent-primary">
                        {liveWorkStatus.statusLabel}
                      </span>
                    ) : null}
                    {liveWorkStatus.stepLabel ? (
                      <span className="rounded-pill border border-border-default px-2 py-0.5 text-[11px] text-text-secondary">
                        {liveWorkStatus.stepLabel}
                      </span>
                    ) : null}
                  </div>
                <div className="mt-1 text-xs leading-5 text-text-secondary">
                  {liveWorkStatus.detail}
                  {hasUnreadActivity && !isPinnedToBottom ? (
                    <span className="ml-2 text-text-tertiary">
                      Nie przewijam automatycznie — czytasz wyżej.
                    </span>
                  ) : null}
                </div>
                </div>
              </div>
            ) : (
              <div className="text-sm text-text-secondary">Są nowe aktywności niżej.</div>
            )}
            {hasUnreadActivity && !isPinnedToBottom ? (
              <button
                type="button"
                onClick={() => scrollToBottom()}
                className="inline-flex shrink-0 items-center gap-2 rounded-pill border border-border-default px-3 py-2 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
              >
                <ArrowDown size={14} />
                ↓ Nowe wiadomości
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
      {queuedMessages.length > 0 ? (
        <div className="px-4 pb-3" data-testid="chat-queued-messages">
          <div className={`mx-auto w-full ${maxWidthClass} rounded-lg border border-border-default bg-panel/90 px-3 py-2.5`}>
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="label-eyebrow">KOLEJKA · {queuedMessages.length}</div>
              <div className="type-body-sm text-text-tertiary">Wysyłam po zakończeniu bieżącej odpowiedzi</div>
            </div>
            <ol className="max-h-28 space-y-1 overflow-y-auto">
              {queuedMessages.map((item, index) => (
                <li key={item.id} className="flex min-w-0 items-center gap-2 rounded-md bg-elevated/70 px-2 py-1.5">
                  <span className="type-mono-sm text-text-tertiary">{index + 1}.</span>
                  <span className="min-w-0 flex-1 truncate type-body-sm text-text-secondary" title={item.text}>
                    {item.text}
                  </span>
                  <span className="max-w-36 truncate type-mono-sm text-text-tertiary" title={item.targetLabel}>
                    {item.targetLabel}
                  </span>
                  {onRemoveQueuedMessage ? (
                    <button
                      type="button"
                      aria-label={`Usuń z kolejki: ${item.text}`}
                      title="Usuń z kolejki"
                      onClick={() => onRemoveQueuedMessage(item.id)}
                      className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-text-tertiary transition hover:bg-panel hover:text-text-primary"
                    >
                      <X size={14} />
                    </button>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
        </div>
      ) : null}
      <ChatInput
        focusSignal={inputFocusSignal}
        value={inputValue}
        onValueChange={onInputValueChange}
        filesEnabled={inputFilesEnabled}
        header={inputHeader}
        generating={generating || showTypingIndicator}
        queueCount={queuedMessages.length}
        onSubmit={handleSubmit}
        onCancel={onCancelGeneration}
        onImprovePrompt={onImprovePrompt}
        onVoiceToggle={() => {
          if (voiceRecording) {
            stopVoiceRecording()
          } else {
            void startVoiceRecording()
          }
        }}
        voiceRecording={voiceRecording}
        onScreenAwareTurn={handleScreenAwareTurn}
        screenAwareBusy={screenAwareBusy}
      />
    </section>
  )
}

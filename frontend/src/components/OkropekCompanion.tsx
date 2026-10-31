import { AnimatePresence, motion, type PanInfo, useReducedMotion } from 'framer-motion'
import { LoaderCircle, Mic, MicOff, SendHorizonal, Volume2, VolumeX, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import TextareaAutosize from 'react-textarea-autosize'
import { fetchConversationDetail, sendChatTurnStream } from '../lib/api'
import { useChatStore } from '../stores/chatStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import { useUIStore } from '../stores/uiStore'
import { OkropekAvatar, type OkropekSpecialMove } from './okropek/OkropekAvatar'

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean
  readonly 0: { readonly transcript: string }
  readonly length: number
}

interface SpeechRecognitionEventLike {
  readonly resultIndex: number
  readonly results: ArrayLike<SpeechRecognitionResultLike>
}

interface SpeechRecognitionLike {
  lang: string
  interimResults: boolean
  continuous: boolean
  maxAlternatives: number
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

interface SpeechRecognitionConstructorLike {
  new (): SpeechRecognitionLike
}

const ATNOS_WAKE_PREFIXES = [
  'atnos',
  'atnosie',
  'hej atnos',
  'hej atnosie',
  'okropek',
  'okropku',
  'hej okropek',
  'hej okropku',
] as const

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructorLike | null {
  const scope = window as Window & {
    SpeechRecognition?: SpeechRecognitionConstructorLike
    webkitSpeechRecognition?: SpeechRecognitionConstructorLike
  }
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null
}

function pickSpeechVoice() {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return null
  const voices = window.speechSynthesis.getVoices()
  if (!voices.length) return null
  const ranked = [...voices].sort((left, right) => scoreSpeechVoice(right) - scoreSpeechVoice(left))
  return ranked[0] ?? null
}

function scoreSpeechVoice(voice: SpeechSynthesisVoice) {
  const lang = voice.lang.toLowerCase()
  const fingerprint = `${voice.name} ${voice.voiceURI}`.toLowerCase()
  let score = 0
  if (lang.startsWith('pl')) score += 120
  else if (lang.startsWith('en')) score += 20
  if (/zofia|maja|paulina|agnieszka/.test(fingerprint)) score += 90
  if (/marek|piotr/.test(fingerprint)) score += 40
  if (/microsoft|natural|neural|online/.test(fingerprint)) score += 35
  if (/polish|polski/.test(fingerprint)) score += 25
  if (voice.localService) score += 6
  return score
}

function trimReplyForSpeech(text: string) {
  return text.replace(/\s+/g, ' ').trim().slice(0, 900)
}

function normalizeWakeAddress(text: string) {
  return text
    .toLowerCase()
    .replace(/[.,!?;:]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function escapeRegExp(text: string) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function stripWakeAddress(text: string) {
  const trimmed = text.trim()
  if (!trimmed) {
    return ''
  }
  const normalized = normalizeWakeAddress(text)
  for (const prefix of ATNOS_WAKE_PREFIXES) {
    if (normalized === prefix) {
      return ''
    }
    if (normalized.startsWith(`${prefix} `)) {
      const prefixPattern = prefix
        .split(' ')
        .map((part) => escapeRegExp(part))
        .join('\\s+')
      return trimmed.replace(new RegExp(`^${prefixPattern}[\\s,.:;-]*`, 'i'), '').trim()
    }
  }
  return trimmed
}

const OKROPEK_IDLE_MINIMIZE_MS = 12000
const OKROPEK_PANEL_AUTOCLOSE_MS = 20000
const OKROPEK_SLEEP_MS = 32000
/** ChatGPT-style end-of-turn: pause after last speech before sending. */
const OKROPEK_END_OF_TURN_MS = 1200
const OKROPEK_MAX_UTTERANCE_MS = 28_000
const OKROPEK_SPECIAL_MOVES: Exclude<OkropekSpecialMove, 'none'>[] = ['wave', 'hop', 'peek']
const OKROPEK_SPECIAL_MOVE_EVERY_MS = 5 * 60 * 1000
const OKROPEK_SPECIAL_MOVE_DURATION_MS = 4200

export function OkropekCompanion() {
  const reducedMotion = useReducedMotion()
  const okropekModeEnabled = useUIStore((state) => state.okropekModeEnabled)
  const okropekPanelOpen = useUIStore((state) => state.okropekPanelOpen)
  const okropekDockSide = useUIStore((state) => state.okropekDockSide)
  const setOkropekPanelOpen = useUIStore((state) => state.setOkropekPanelOpen)
  const setOkropekModeEnabled = useUIStore((state) => state.setOkropekModeEnabled)
  const setOkropekDockSide = useUIStore((state) => state.setOkropekDockSide)
  const pushToast = useToastStore((state) => state.push)
  const {
    selectedConversationId,
    selectedProjectId,
    settings,
    activeTurn,
    startActiveTurn,
    finishActiveTurn,
    appendOptimisticUserMessage,
    replaceAfterTurn,
    setSelectedConversation,
    setConversationDetail,
  } = useChatStore()
  const { agentStage, setStage, setAgentActivity } = useSystemStore()

  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [listening, setListening] = useState(false)
  const [conversationMode, setConversationMode] = useState(false)
  const [interimTranscript, setInterimTranscript] = useState('')
  const [speechRepliesEnabled, setSpeechRepliesEnabled] = useState(true)
  const [lastReply, setLastReply] = useState<string | null>(null)
  const [idleMinimized, setIdleMinimized] = useState(false)
  const [sleeping, setSleeping] = useState(false)
  const [specialMove, setSpecialMove] = useState<OkropekSpecialMove>('none')
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const finalTranscriptRef = useRef('')
  const lastSpeechAtRef = useRef(0)
  const utteranceStartedAtRef = useRef(0)
  const endOfTurnTimerRef = useRef<number | null>(null)
  const maxUtteranceTimerRef = useRef<number | null>(null)
  const conversationModeRef = useRef(false)
  const speakingRef = useRef(false)
  const restartAfterSpeakRef = useRef(false)
  const ignoreToggleUntilRef = useRef(0)
  const lastInteractionAtRef = useRef(Date.now())
  const specialMoveIndexRef = useRef(0)
  const startListeningRef = useRef<() => void>(() => undefined)

  const recognitionSupported = typeof window !== 'undefined' && getSpeechRecognitionConstructor() != null
  const effectiveStage =
    activeTurn && (agentStage === 'idle' || agentStage === 'offline') ? 'thinking' : agentStage === 'offline' ? 'idle' : agentStage

  useEffect(() => {
    if (!okropekModeEnabled && typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel()
    }
  }, [okropekModeEnabled])

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort()
      if (endOfTurnTimerRef.current != null) window.clearTimeout(endOfTurnTimerRef.current)
      if (maxUtteranceTimerRef.current != null) window.clearTimeout(maxUtteranceTimerRef.current)
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
        window.speechSynthesis.cancel()
      }
    }
  }, [])

  const markInteraction = () => {
    lastInteractionAtRef.current = Date.now()
    setIdleMinimized(false)
    setSleeping(false)
  }

  useEffect(() => {
    // Centrum and other entry points can open the panel through the shared store.
    // Give that explicit opening a fresh idle period, just like the avatar button.
    if (okropekPanelOpen) {
      lastInteractionAtRef.current = Date.now()
      setIdleMinimized(false)
      setSleeping(false)
    }
  }, [okropekPanelOpen])

  useEffect(() => {
    if (!okropekModeEnabled) {
      setIdleMinimized(false)
      setSleeping(false)
      return
    }

    const timer = window.setInterval(() => {
      const idleFor = Date.now() - lastInteractionAtRef.current
      const canAutoClose = okropekPanelOpen && !sending && !listening && !conversationMode && !draft.trim()
      const canIdleMinimize =
        !okropekPanelOpen && !sending && !listening && !conversationMode && effectiveStage === 'idle'

      if (canAutoClose && idleFor >= OKROPEK_PANEL_AUTOCLOSE_MS) {
        setOkropekPanelOpen(false)
        setIdleMinimized(true)
        return
      }

      if (canIdleMinimize && idleFor >= OKROPEK_IDLE_MINIMIZE_MS) {
        setIdleMinimized(true)
      }

      if (canIdleMinimize && idleFor >= OKROPEK_SLEEP_MS) {
        setSleeping(true)
      } else if (!canIdleMinimize && sleeping) {
        setSleeping(false)
      }
    }, 1000)

    return () => window.clearInterval(timer)
  }, [
    conversationMode,
    draft,
    effectiveStage,
    listening,
    okropekModeEnabled,
    okropekPanelOpen,
    sending,
    setOkropekPanelOpen,
    sleeping,
  ])

  const displayStage = sleeping ? 'idle' : effectiveStage

  const strollAnimation = useMemo(() => {
    if (reducedMotion || okropekPanelOpen || sleeping) {
      return { x: 0, y: 0 }
    }
    if (effectiveStage === 'executing') {
      return { x: [0, -8, -14, -20, -24, -20, -14, -8, 0], y: [0, -0.08, -0.14, -0.08, 0] }
    }
    if (effectiveStage === 'thinking' || listening) {
      return { x: 0, y: [0, -0.05, -0.08, -0.05, 0] }
    }
    return { x: 0, y: [0, -0.03, -0.05, -0.03, 0] }
  }, [effectiveStage, listening, okropekPanelOpen, reducedMotion, sleeping])

  const strollTransition = useMemo(() => {
    if (reducedMotion || okropekPanelOpen || sleeping) {
      return { duration: 0 }
    }
    return {
      duration: effectiveStage === 'executing' ? 6.4 : effectiveStage === 'thinking' || listening ? 4.8 : 7.8,
      repeat: Infinity,
      ease: 'easeInOut' as const,
    }
  }, [effectiveStage, listening, okropekPanelOpen, reducedMotion, sleeping])

  const companionScale = useMemo(() => {
    if (okropekPanelOpen) return 1
    if (sleeping) return 0.58
    if (idleMinimized) return 0.78
    if (effectiveStage === 'executing') return 0.98
    if (effectiveStage === 'thinking' || listening) return 0.95
    return 0.9
  }, [effectiveStage, idleMinimized, listening, okropekPanelOpen, sleeping])
  const canDrag = !reducedMotion && !okropekPanelOpen

  useEffect(() => {
    conversationModeRef.current = conversationMode
  }, [conversationMode])

  useEffect(() => {
    if (!okropekModeEnabled || reducedMotion) {
      setSpecialMove('none')
      return
    }
    const tick = () => {
      const move = OKROPEK_SPECIAL_MOVES[specialMoveIndexRef.current % OKROPEK_SPECIAL_MOVES.length]
      specialMoveIndexRef.current += 1
      setSpecialMove(move)
      window.setTimeout(() => {
        setSpecialMove((current) => (current === move ? 'none' : current))
      }, OKROPEK_SPECIAL_MOVE_DURATION_MS)
    }
    // First playful move shortly after enable, then every 5 minutes.
    const first = window.setTimeout(tick, 18_000)
    const interval = window.setInterval(tick, OKROPEK_SPECIAL_MOVE_EVERY_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(interval)
    }
  }, [okropekModeEnabled, reducedMotion])

  const clearVoiceTimers = () => {
    if (endOfTurnTimerRef.current != null) {
      window.clearTimeout(endOfTurnTimerRef.current)
      endOfTurnTimerRef.current = null
    }
    if (maxUtteranceTimerRef.current != null) {
      window.clearTimeout(maxUtteranceTimerRef.current)
      maxUtteranceTimerRef.current = null
    }
  }

  const speakReply = (text: string, resumeListening = false) => {
    if (
      !speechRepliesEnabled ||
      typeof window === 'undefined' ||
      !('speechSynthesis' in window) ||
      typeof SpeechSynthesisUtterance === 'undefined'
    ) {
      if (resumeListening && conversationModeRef.current) {
        window.setTimeout(() => startListeningRef.current(), 350)
      }
      return
    }
    const utterance = new SpeechSynthesisUtterance(trimReplyForSpeech(text))
    const voice = pickSpeechVoice()
    if (voice) {
      utterance.voice = voice
      utterance.lang = voice.lang
    } else {
      utterance.lang = 'pl-PL'
    }
    // Softer, more human cadence for Okropek.
    utterance.rate = 0.92
    utterance.pitch = 1.05
    utterance.volume = 1
    speakingRef.current = true
    restartAfterSpeakRef.current = resumeListening
    utterance.onend = () => {
      speakingRef.current = false
      if (restartAfterSpeakRef.current && conversationModeRef.current) {
        restartAfterSpeakRef.current = false
        window.setTimeout(() => startListeningRef.current(), 280)
      }
    }
    utterance.onerror = () => {
      speakingRef.current = false
      if (restartAfterSpeakRef.current && conversationModeRef.current) {
        restartAfterSpeakRef.current = false
        window.setTimeout(() => startListeningRef.current(), 280)
      }
    }
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
  }

  const hydrateConversation = async (conversationId: number) => {
    const detail = await fetchConversationDetail(conversationId)
    setConversationDetail({
      conversationId: detail.conversation.id,
      title: detail.conversation.title,
      messages: detail.messages,
      settings: detail.settings,
      memoryCards: detail.memoryCards,
    })
  }

  const submitDraft = async (textOverride?: string, fromVoice = false) => {
    const nextText = stripWakeAddress(textOverride ?? draft)
    if (!nextText || sending) return
    const tempId = appendOptimisticUserMessage(nextText)
    markInteraction()
    setSending(true)
    startActiveTurn({
      kind: 'chat',
      conversationId: selectedConversationId,
      projectId: selectedProjectId,
      summary: nextText,
    })
    setStage('thinking')
    setAgentActivity({
      stage: 'thinking',
      title: 'Okropek słucha i planuje',
      detail: 'Zamienia polecenie głosowe lub tekstowe w konkretny bieg.',
    })

    try {
      const result = await sendChatTurnStream(
        {
          content: nextText,
          conversation_id: selectedConversationId ?? undefined,
          project_id: selectedProjectId ?? undefined,
          prompt_improved: false,
          attachments: [],
          message_metadata_json: fromVoice
            ? { source: 'voice_input', voice_reply_requested: true, companion: 'okropek' }
            : { source: 'okropek_panel', companion: 'okropek' },
          assistant_metadata_json: { companion: 'okropek' },
          execution_preferences: settings
            ? {
                response_style: settings.responseStyle,
                persona_profile: settings.personaProfile,
                provider_posture: settings.providerPosture,
              }
            : undefined,
        },
        {
          onEvent: (event) => {
            if (event.type === 'status') {
              setAgentActivity({
                stage: 'thinking',
                title: event.title || 'Okropek pracuje',
                detail: event.detail || 'Streaming odpowiedzi...',
              })
            }
          },
        },
      )

      replaceAfterTurn({
        tempId,
        userMessageId: result.userMessageId,
        assistantMessageId: result.assistantMessageId,
        userMessage: result.userMessage || nextText,
        assistantMessage: result.assistantMessage,
        conversationId: result.conversationId,
        conversationTitle: result.conversationTitle,
        projectId: result.conversationProjectId,
      })
      setSelectedConversation(result.conversationId, result.conversationTitle)
      await hydrateConversation(result.conversationId)
      setLastReply(result.assistantMessage)
      speakReply(result.assistantMessage, fromVoice && conversationModeRef.current)
      setDraft('')
      setInterimTranscript('')
      setStage('idle')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nieznany blad podczas rozmowy z Okropkiem.'
      setStage('error')
      setAgentActivity({
        stage: 'error',
        title: 'Okropek zatrzymal sie na bledzie',
        detail: message,
      })
      pushToast({
        type: 'error',
        title: 'Okropek nie odpowiedzial',
        message,
      })
      if (fromVoice && conversationModeRef.current) {
        window.setTimeout(() => startListeningRef.current(), 600)
      }
    } finally {
      finishActiveTurn()
      setSending(false)
      window.setTimeout(() => {
        useSystemStore.getState().setStage('idle')
      }, 800)
    }
  }

  const finishUtteranceAndSend = () => {
    clearVoiceTimers()
    const nextText = finalTranscriptRef.current.trim()
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop()
      } catch {
        // ignore
      }
    }
    setListening(false)
    setInterimTranscript('')
    if (nextText) {
      void submitDraft(nextText, true)
    } else if (conversationModeRef.current) {
      window.setTimeout(() => startListeningRef.current(), 400)
    }
  }

  const scheduleEndOfTurn = () => {
    if (endOfTurnTimerRef.current != null) {
      window.clearTimeout(endOfTurnTimerRef.current)
    }
    endOfTurnTimerRef.current = window.setTimeout(() => {
      if (!finalTranscriptRef.current.trim()) {
        return
      }
      finishUtteranceAndSend()
    }, OKROPEK_END_OF_TURN_MS)
  }

  const stopListening = (opts?: { send?: boolean; keepConversation?: boolean }) => {
    clearVoiceTimers()
    const shouldSend = opts?.send ?? true
    if (!opts?.keepConversation) {
      setConversationMode(false)
      conversationModeRef.current = false
    }
    if (!recognitionRef.current) {
      if (shouldSend && finalTranscriptRef.current.trim()) {
        void submitDraft(finalTranscriptRef.current, true)
      }
      setListening(false)
      return
    }
    try {
      recognitionRef.current.stop()
    } catch {
      // ignore
    }
    setListening(false)
    if (shouldSend && finalTranscriptRef.current.trim()) {
      void submitDraft(finalTranscriptRef.current, true)
    }
  }

  const startListening = () => {
    const RecognitionCtor = getSpeechRecognitionConstructor()
    if (!RecognitionCtor) {
      pushToast({
        type: 'warning',
        title: 'Brak browser speech',
        message: 'Ten runtime nie udostępnia rozpoznawania mowy w przeglądarce (Chrome/Edge).',
      })
      return
    }
    if (listening) {
      stopListening({ send: true, keepConversation: conversationModeRef.current })
      return
    }
    if (sending) {
      return
    }

    // Barge-in: interrupt Okropek speaking like ChatGPT Advanced Voice.
    if (speakingRef.current && typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel()
      speakingRef.current = false
    }

    markInteraction()
    setOkropekPanelOpen(true)
    setConversationMode(true)
    conversationModeRef.current = true
    finalTranscriptRef.current = ''
    setInterimTranscript('')
    lastSpeechAtRef.current = 0
    utteranceStartedAtRef.current = Date.now()
    clearVoiceTimers()

    const recognition = new RecognitionCtor()
    recognition.lang = 'pl-PL'
    recognition.interimResults = true
    // Continuous + silence-gated end-of-turn ≈ realistic GPT voice turn-taking.
    recognition.continuous = true
    recognition.maxAlternatives = 1
    recognition.onresult = (event) => {
      let finalText = ''
      let interimText = ''
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index]
        const transcript = result[0]?.transcript ?? ''
        if (result.isFinal) {
          finalText += transcript
        } else {
          interimText += transcript
        }
      }
      lastSpeechAtRef.current = Date.now()
      if (finalText.trim()) {
        finalTranscriptRef.current = `${finalTranscriptRef.current} ${finalText}`.trim()
        setDraft(finalTranscriptRef.current)
        scheduleEndOfTurn()
      }
      setInterimTranscript(interimText.trim())
      if (interimText.trim()) {
        // Keep extending turn while user is still speaking.
        if (endOfTurnTimerRef.current != null) {
          window.clearTimeout(endOfTurnTimerRef.current)
          endOfTurnTimerRef.current = null
        }
      }
    }
    recognition.onerror = (event) => {
      setListening(false)
      clearVoiceTimers()
      const code = event.error || ''
      if (code === 'aborted' || code === 'no-speech') {
        if (conversationModeRef.current && !sending) {
          window.setTimeout(() => startListeningRef.current(), 450)
        }
        return
      }
      pushToast({
        type: 'warning',
        title: 'Mikrofon przerwany',
        message: code ? `Browser speech: ${code}` : 'Nie udało się przechwycić głosu.',
      })
    }
    recognition.onend = () => {
      setListening(false)
      recognitionRef.current = null
      clearVoiceTimers()
      // If browser ended the session mid-conversation without our stop, resume.
      if (
        conversationModeRef.current &&
        !sending &&
        !speakingRef.current &&
        !finalTranscriptRef.current.trim()
      ) {
        window.setTimeout(() => startListeningRef.current(), 320)
      }
    }
    recognitionRef.current = recognition
    setListening(true)
    try {
      recognition.start()
    } catch {
      setListening(false)
      pushToast({
        type: 'warning',
        title: 'Mikrofon zajęty',
        message: 'Nie udało się uruchomić rozpoznawania mowy. Spróbuj ponownie.',
      })
      return
    }
    maxUtteranceTimerRef.current = window.setTimeout(() => {
      if (finalTranscriptRef.current.trim()) {
        finishUtteranceAndSend()
      } else {
        stopListening({ send: false, keepConversation: true })
        window.setTimeout(() => startListeningRef.current(), 300)
      }
    }, OKROPEK_MAX_UTTERANCE_MS)
  }

  startListeningRef.current = startListening

  if (!okropekModeEnabled) {
    return null
  }

  const handleDragEnd = (_event: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) => {
    markInteraction()
    ignoreToggleUntilRef.current = Date.now() + 180
    if (typeof window !== 'undefined') {
      setOkropekDockSide(info.point.x < window.innerWidth / 2 ? 'left' : 'right')
    }
  }

  const handleCompanionClick = () => {
    markInteraction()
    if (Date.now() < ignoreToggleUntilRef.current) {
      return
    }
    setOkropekPanelOpen(!okropekPanelOpen)
  }

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-3 z-30 px-3 md:bottom-4 md:px-4">
      <div className="relative flex min-h-[120px] items-end">
        <motion.div
          className={`pointer-events-auto absolute bottom-0 ${okropekDockSide === 'right' ? 'right-0' : 'left-0'}`}
          drag={canDrag ? 'x' : undefined}
          dragMomentum={canDrag ? false : undefined}
          dragElastic={canDrag ? 0.08 : undefined}
          dragTransition={canDrag ? { bounceStiffness: 420, bounceDamping: 28 } : undefined}
          onDragEnd={canDrag ? handleDragEnd : undefined}
          animate={{
            scale: companionScale,
            opacity: sleeping && !okropekPanelOpen ? 0.46 : idleMinimized && !okropekPanelOpen ? 0.72 : 1,
            x: sleeping && !okropekPanelOpen ? (okropekDockSide === 'right' ? 18 : -18) : 0,
          }}
          transition={{
            scale: { duration: reducedMotion ? 0 : 0.22, ease: 'easeOut' },
            opacity: { duration: reducedMotion ? 0 : 0.28, ease: 'easeOut' },
            x: { duration: reducedMotion ? 0 : 0.34, ease: 'easeOut' },
          }}
          onMouseEnter={markInteraction}
          onFocusCapture={markInteraction}
        >
          <motion.div
            className={`relative flex flex-col ${okropekDockSide === 'right' ? 'items-end' : 'items-start'}`}
            animate={strollAnimation}
            transition={strollTransition}
          >
            <AnimatePresence>
              {okropekPanelOpen ? (
                <motion.div
                  initial={{ opacity: 0, y: 12, scale: 0.96 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 8, scale: 0.98 }}
                  transition={{ duration: reducedMotion ? 0 : 0.2, ease: 'easeOut' }}
                  className="mb-2 w-[min(300px,calc(100vw-1.5rem))] rounded-[22px] border border-border-default bg-shell/90 p-3 shadow-[var(--shadow-floating)] backdrop-blur-xl"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="label-eyebrow">OKROPEK</div>
                      <div className="mt-1 text-sm text-text-secondary">
                        {sending
                          ? 'Domykam odpowiedź…'
                          : listening
                            ? 'Słucham jak w normalnej rozmowie — mów spokojnie, wyślę po krótkiej pauzie.'
                            : conversationMode
                              ? 'Tryb rozmowy włączony. Kliknij mikrofon, żeby wyjść.'
                              : effectiveStage === 'executing'
                                ? 'Robię zadanie w tle.'
                                : sleeping
                                  ? 'Cicho czekam przy krawędzi ekranu.'
                                  : 'Jestem gotowy. Mikrofon = ciągła rozmowa.'}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          markInteraction()
                          setSpeechRepliesEnabled((value) => !value)
                        }}
                        className={`rounded-full border p-2 transition ${
                          speechRepliesEnabled
                            ? 'border-accent-primary/50 bg-accent-primary/10 text-accent-primary'
                            : 'border-border-default text-text-secondary hover:border-accent-primary hover:text-text-primary'
                        }`}
                        aria-label={speechRepliesEnabled ? 'Wyłącz głos Okropka' : 'Włącz głos Okropka'}
                      >
                        {speechRepliesEnabled ? <Volume2 size={13} /> : <VolumeX size={13} />}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          markInteraction()
                          setConversationMode(false)
                          conversationModeRef.current = false
                          stopListening({ send: false, keepConversation: false })
                          setOkropekPanelOpen(false)
                        }}
                        className="rounded-full border border-border-default p-2 text-text-secondary transition hover:border-accent-primary hover:text-text-primary"
                        aria-label="Zwiń Okropka"
                      >
                        <X size={13} />
                      </button>
                    </div>
                  </div>

                  <div className="mb-2 flex items-center gap-2">
                    <span className="rounded-pill border border-border-default px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-text-secondary">
                      {displayStage}
                    </span>
                    <span className="rounded-pill border border-border-default px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-text-secondary">
                      {conversationMode ? 'live talk' : recognitionSupported ? 'voice ready' : 'voice off'}
                    </span>
                  </div>

                  {interimTranscript ? (
                    <div className="mb-2 rounded-2xl border border-border-default bg-panel px-3 py-2 text-sm text-text-secondary">
                      <span className="text-text-tertiary">Słyszę:</span> {interimTranscript}
                    </div>
                  ) : null}

                  {lastReply ? (
                    <div className="mb-2 rounded-2xl border border-accent-primary/20 bg-accent-primary/8 px-3 py-2 text-sm text-text-primary">
                      <span className="text-text-tertiary">Ostatnia odpowiedź:</span> {lastReply.slice(0, 160)}
                      {lastReply.length > 160 ? '...' : ''}
                    </div>
                  ) : null}

                  <div className="flex items-end gap-2">
                    <div className="flex-1 rounded-2xl border border-border-default bg-panel px-3 py-2">
                      <TextareaAutosize
                        minRows={1}
                        maxRows={3}
                        value={draft}
                        onChange={(event) => {
                          markInteraction()
                          setDraft(event.target.value)
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' && !event.shiftKey) {
                            event.preventDefault()
                            void submitDraft()
                          }
                        }}
                        placeholder="Powiedz Okropkowi, co ma zrobić..."
                        className="w-full resize-none border-0 bg-transparent text-sm leading-6 text-text-primary outline-none placeholder:text-text-tertiary"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={startListening}
                      className={`flex h-10 w-10 items-center justify-center rounded-2xl border transition ${
                        listening || conversationMode
                          ? 'border-accent-secondary bg-accent-secondary/12 text-accent-secondary'
                          : 'border-border-default bg-panel text-text-secondary hover:border-accent-primary hover:text-text-primary'
                      }`}
                      aria-label={listening ? 'Zakończ wypowiedź i wyślij' : 'Rozmawiaj z Okropkiem'}
                    >
                      {listening ? <MicOff size={15} /> : <Mic size={15} />}
                    </button>
                    <button
                      type="button"
                      onClick={() => void submitDraft()}
                      disabled={sending || !draft.trim()}
                      className="flex h-10 w-10 items-center justify-center rounded-2xl border border-accent-primary bg-accent-primary/10 text-accent-primary transition disabled:cursor-not-allowed disabled:border-border-default disabled:text-text-disabled"
                      aria-label="Wyślij do Okropka"
                    >
                      {sending ? <LoaderCircle size={15} className="animate-spin" /> : <SendHorizonal size={15} />}
                    </button>
                  </div>
                </motion.div>
              ) : null}
            </AnimatePresence>

            <div className="relative w-[84px]">
              <button
                type="button"
                onClick={handleCompanionClick}
                data-sleeping={sleeping ? 'true' : 'false'}
                className={`group relative flex w-full items-end bg-transparent ${
                  okropekDockSide === 'right' ? 'justify-end' : 'justify-start'
                } ${okropekPanelOpen ? '' : 'cursor-grab active:cursor-grabbing'}`}
                aria-label="Otwórz Okropka"
              >
                <div className="relative">
                  <OkropekAvatar
                    state={displayStage}
                    listening={listening && !sleeping}
                    size={64}
                    specialMove={sleeping ? 'none' : specialMove}
                  />
                  <motion.span
                    className={`absolute right-0 top-1 h-2.5 w-2.5 rounded-full border border-shell ${
                      sleeping
                        ? 'bg-text-disabled'
                        : listening
                        ? 'bg-accent-secondary'
                        : sending || effectiveStage === 'executing'
                          ? 'bg-accent-primary'
                          : 'bg-text-tertiary'
                    }`}
                    animate={
                      reducedMotion || sleeping
                        ? undefined
                        : { scale: [1, 1.35, 1], opacity: [0.8, 1, 0.8] }
                    }
                    transition={{ duration: 0.9, repeat: Infinity, ease: 'easeInOut' }}
                  />
                </div>
              </button>
            </div>

            <button
              type="button"
              onClick={() => {
                setConversationMode(false)
                conversationModeRef.current = false
                stopListening({ send: false, keepConversation: false })
                setOkropekModeEnabled(false)
              }}
              className={`pointer-events-auto absolute -top-1 rounded-full border border-border-default bg-shell/90 p-1.5 text-text-tertiary shadow-[0_8px_20px_rgba(0,0,0,0.22)] transition hover:border-accent-primary hover:text-text-primary ${
                okropekDockSide === 'right' ? '-right-2' : '-left-2'
              }`}
              aria-label="Wyłącz tryb Okropek"
            >
              <X size={11} />
            </button>
          </motion.div>
        </motion.div>
      </div>
    </div>
  )
}

import { useEffect, useRef, useState, type ReactNode } from 'react'
import TextareaAutosize from 'react-textarea-autosize'
import {
  LoaderCircle,
  Mic,
  MicOff,
  Monitor,
  Paperclip,
  SendHorizontal,
  Square,
  Upload,
  Wand2,
  X,
} from 'lucide-react'
import { deleteUpload, uploadChatFile, type ChatAttachmentPayload } from '../lib/api'
import { CHAT_STATUS } from '../lib/chatStatusCopy'
import { useToastStore } from '../stores/toastStore'

export interface ChatInputSubmitPayload {
  text: string
  attachments: ChatAttachmentPayload[]
  promptImproved?: boolean
}

interface StagedAttachment {
  id: string
  payload: ChatAttachmentPayload
  previewUrl: string | null
  isImage: boolean
}

export function ChatInput({
  disabled,
  generating = false,
  queueCount = 0,
  focusSignal = 0,
  value,
  onValueChange,
  filesEnabled = true,
  header,
  onSubmit,
  onCancel,
  onImprovePrompt,
  onVoiceToggle,
  voiceRecording = false,
  onScreenAwareTurn,
  screenAwareBusy = false,
}: {
  disabled?: boolean
  /** When true, new messages are queued while the active turn continues. */
  generating?: boolean
  queueCount?: number
  focusSignal?: number
  value?: string
  onValueChange?: (value: string) => void
  filesEnabled?: boolean
  header?: ReactNode
  onSubmit: (payload: ChatInputSubmitPayload) => boolean | Promise<boolean>
  onCancel?: () => void
  onImprovePrompt?: (draft: string) => Promise<{ text: string; strategyNote?: string; changed?: boolean } | null>
  onVoiceToggle?: () => void
  voiceRecording?: boolean
  onScreenAwareTurn?: () => void | Promise<void>
  screenAwareBusy?: boolean
}) {
  const [internalValue, setInternalValue] = useState('')
  const [uploading, setUploading] = useState(false)
  const [improvingPrompt, setImprovingPrompt] = useState(false)
  const [promptWasImproved, setPromptWasImproved] = useState(false)
  const [stagedAttachments, setStagedAttachments] = useState<StagedAttachment[]>([])
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const dragDepthRef = useRef(0)
  const stagedAttachmentsRef = useRef<StagedAttachment[]>([])
  const pushToast = useToastStore((state) => state.push)
  const draftValue = value ?? internalValue

  useEffect(() => {
    stagedAttachmentsRef.current = stagedAttachments
  }, [stagedAttachments])

  useEffect(() => {
    return () => {
      stagedAttachmentsRef.current.forEach((attachment) => {
        if (attachment.previewUrl) {
          URL.revokeObjectURL(attachment.previewUrl)
        }
      })
    }
  }, [])

  useEffect(() => {
    if (focusSignal <= 0) {
      return
    }
    const textarea = textareaRef.current
    if (!textarea) {
      return
    }
    textarea.focus()
    const length = textarea.value.length
    textarea.setSelectionRange(length, length)
  }, [focusSignal])

  function setDraftValue(nextValue: string) {
    setInternalValue(nextValue)
    onValueChange?.(nextValue)
  }

  function revokePreview(previewUrl: string | null) {
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl)
    }
  }

  async function stageFiles(files: File[]) {
    if (!files.length || disabled) return
    if (!filesEnabled) {
      pushToast({
        type: 'warning',
        title: 'Pliki sa wylaczone',
        message: 'Włącz pliki w ustawieniach rozmowy, aby dodawać załączniki.',
      })
      return
    }
    setUploading(true)
    try {
      const uploaded = await Promise.all(
        files.map(async (file) => {
          const payload = await uploadChatFile(file)
          const mediaType = (payload.media_type ?? file.type ?? '').toLowerCase()
          const isImage = payload.kind === 'image' || mediaType.startsWith('image/')
          return {
            id: crypto.randomUUID(),
            payload,
            previewUrl: isImage ? URL.createObjectURL(file) : null,
            isImage,
          } satisfies StagedAttachment
        }),
      )
      setStagedAttachments((current) => [...current, ...uploaded])
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udalo sie dodac zalacznika',
        message: error instanceof Error ? error.message : 'Nieznany błąd podczas przesyłania pliku.',
      })
    } finally {
      setUploading(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  async function removeAttachment(attachmentId: string) {
    const attachment = stagedAttachmentsRef.current.find((item) => item.id === attachmentId)
    if (!attachment) return
    setStagedAttachments((current) => current.filter((item) => item.id !== attachmentId))
    revokePreview(attachment.previewUrl)
    try {
      await deleteUpload(attachment.payload.storage_path)
    } catch {
      // best effort cleanup
    }
  }

  async function submit() {
    const next = draftValue.trim()
    if ((!next && stagedAttachments.length === 0) || disabled || uploading) return
    const accepted = await onSubmit({
      text: next,
      attachments: stagedAttachments.map((attachment) => attachment.payload),
      promptImproved: promptWasImproved,
    })
    if (accepted === false) return
    stagedAttachments.forEach((attachment) => revokePreview(attachment.previewUrl))
    setStagedAttachments([])
    setDraftValue('')
    setPromptWasImproved(false)
    setDragActive(false)
    dragDepthRef.current = 0
  }

  function cancelGeneration() {
    if (!generating || !onCancel) return
    onCancel()
  }

  async function improvePrompt() {
    const draft = draftValue.trim()
    if (!draft) {
      pushToast({
        type: 'warning',
        title: 'Brak promptu do poprawy',
        message: 'Najpierw wpisz treść, którą Atnos ma doprecyzować.',
      })
      return
    }
    if (!onImprovePrompt || improvingPrompt) return
    setImprovingPrompt(true)
    try {
      const result = await onImprovePrompt(draft)
      if (!result) return
      setDraftValue(result.text)
      setPromptWasImproved(Boolean(result.changed))
      pushToast({
        type: 'success',
        title: result.changed ? 'Prompt doprecyzowany' : 'Prompt już jest czytelny',
        message:
          result.strategyNote ||
          (result.changed
            ? 'Atnos przepisal prompt do bardziej wykonawczej wersji.'
            : 'Nie było potrzeby dalszej poprawy.'),
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udalo sie poprawic promptu',
        message: error instanceof Error ? error.message : 'Nieznany błąd podczas poprawy promptu.',
      })
    } finally {
      setImprovingPrompt(false)
    }
  }

  return (
    <div className="sticky bottom-0 z-hud border-t border-border-subtle bg-shell/90 px-4 py-4 backdrop-blur-xl">
      <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-3">
        {header ? <div>{header}</div> : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <button
            type="button"
            disabled={disabled || improvingPrompt}
            onClick={() => void improvePrompt()}
            className={`inline-flex items-center gap-2 rounded-pill border px-3 py-1.5 type-body-sm transition ${
              promptWasImproved
                ? 'border-accent-primary text-accent-primary'
                : 'border-border-default text-text-secondary hover:border-accent-primary/50 hover:text-text-primary'
            } disabled:cursor-not-allowed disabled:text-text-disabled`}
          >
            {improvingPrompt ? <LoaderCircle size={14} className="animate-spin" /> : <Wand2 size={14} />}
            Popraw prompt
          </button>
          <span className="hidden type-mono-sm text-text-tertiary sm:inline">
            Enter wysyła, Shift+Enter łamie linię, Ctrl+Shift+Space dyktuje
          </span>
        </div>

        {stagedAttachments.length ? (
          <div className="flex flex-wrap gap-2" data-testid="chat-staged-attachments">
            {stagedAttachments.map((attachment) => (
              <div
                key={attachment.id}
                className="flex items-center gap-3 rounded-md border border-border-default bg-panel/80 px-3 py-2 backdrop-blur-md"
              >
                {attachment.isImage && attachment.previewUrl ? (
                  <img
                    src={attachment.previewUrl}
                    alt={attachment.payload.filename}
                    className="h-12 w-12 rounded-sm border border-border-default object-cover"
                  />
                ) : (
                  <div className="flex h-12 w-12 items-center justify-center rounded-sm border border-border-default bg-elevated type-mono-sm text-text-tertiary">
                    FILE
                  </div>
                )}
                <div className="min-w-0">
                  <div className="truncate type-body-sm text-text-primary">
                    {attachment.payload.filename}
                  </div>
                  <div className="type-mono-sm text-text-tertiary">
                    {(attachment.payload.file_extension ?? attachment.payload.kind).toUpperCase()}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void removeAttachment(attachment.id)}
                  className="rounded-pill border border-border-default p-1.5 text-text-secondary transition hover:border-accent-primary hover:text-text-primary"
                  aria-label={`Usuń ${attachment.payload.filename}`}
                >
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
        ) : null}

        <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-end">
          <div
            className={`chat-composer-shell relative min-w-0 flex-1 ${
              dragActive ? 'border-accent-primary' : ''
            }`}
            onDragEnter={(event) => {
              if (!filesEnabled || disabled || !event.dataTransfer?.types.includes('Files')) return
              event.preventDefault()
              dragDepthRef.current += 1
              setDragActive(true)
            }}
            onDragOver={(event) => {
              if (!filesEnabled || disabled) return
              event.preventDefault()
            }}
            onDragLeave={(event) => {
              if (!filesEnabled || disabled || !event.dataTransfer?.types.includes('Files')) return
              event.preventDefault()
              dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
              if (dragDepthRef.current === 0) {
                setDragActive(false)
              }
            }}
            onDrop={(event) => {
              if (!filesEnabled || disabled) return
              event.preventDefault()
              dragDepthRef.current = 0
              setDragActive(false)
              const files = Array.from(event.dataTransfer?.files ?? [])
              void stageFiles(files)
            }}
          >
            {dragActive ? (
              <div className="pointer-events-none absolute inset-0 z-page flex items-center justify-center rounded-[inherit] border border-dashed border-accent-primary bg-shell/92">
                <div className="flex items-center gap-2 text-sm text-accent-primary">
                  <Upload size={16} />
                  Upuść pliki, aby dodać je do rozmowy
                </div>
              </div>
            ) : null}
            <TextareaAutosize
              ref={textareaRef}
              minRows={1}
              maxRows={6}
              value={draftValue}
              disabled={disabled}
              onChange={(event) => {
                setDraftValue(event.target.value)
                if (promptWasImproved) {
                  setPromptWasImproved(false)
                }
              }}
              onPaste={(event) => {
                const items = Array.from(event.clipboardData?.items ?? [])
                const files = items
                  .map((item) => (item.kind === 'file' ? item.getAsFile() : null))
                  .filter((file): file is File => file instanceof File)
                if (!files.length) return
                const hasPlainText = items.some((item) => item.kind === 'string' && item.type === 'text/plain')
                if (!hasPlainText) {
                  event.preventDefault()
                }
                void stageFiles(files)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void submit()
                }
              }}
              placeholder="Powiedz Atnosowi, co ma zrobić..."
              className="w-full resize-none border-0 bg-transparent px-4 py-3.5 type-body text-text-primary outline-none placeholder:text-text-tertiary"
            />
          </div>

          <div className="flex shrink-0 items-center justify-end gap-2">
            <button
              type="button"
              disabled={disabled || screenAwareBusy || !onScreenAwareTurn}
              aria-label="Spójrz na ekran"
              title="Screen-aware: zrzut → zrozum → działaj"
              onClick={() => {
                void onScreenAwareTurn?.()
              }}
              className="flex h-11 w-11 items-center justify-center rounded-pill border border-border-default bg-panel/80 text-text-secondary backdrop-blur-md transition hover:border-accent-primary hover:text-accent-primary disabled:cursor-not-allowed disabled:text-text-disabled"
            >
              {screenAwareBusy ? <LoaderCircle size={16} className="animate-spin" /> : <Monitor size={16} />}
            </button>
            <button
              type="button"
              disabled={disabled}
              aria-label={voiceRecording ? 'Wyłącz dyktafon' : 'Włącz dyktafon'}
              onClick={() => onVoiceToggle?.()}
              className="flex h-11 w-11 items-center justify-center rounded-pill border border-border-default bg-panel/80 text-text-secondary backdrop-blur-md transition hover:border-accent-primary hover:text-accent-primary disabled:cursor-not-allowed disabled:text-text-disabled"
            >
              {voiceRecording ? <MicOff size={16} className="text-error" /> : <Mic size={16} />}
            </button>
            <button
              type="button"
              disabled={disabled || uploading || !filesEnabled}
              aria-label="Dodaj załącznik"
              onClick={() => fileInputRef.current?.click()}
              className="flex h-11 w-11 items-center justify-center rounded-pill border border-border-default bg-panel/80 text-text-secondary backdrop-blur-md transition hover:border-accent-primary hover:text-accent-primary disabled:cursor-not-allowed disabled:text-text-disabled"
            >
              {uploading ? (
                <LoaderCircle size={16} className="animate-spin" />
              ) : (
                <Paperclip size={16} />
              )}
            </button>
            {generating ? (
              <>
                <button
                  type="button"
                  disabled={disabled || uploading || (!draftValue.trim() && stagedAttachments.length === 0)}
                  aria-label="Dodaj do kolejki"
                  title={`Dodaj wiadomość do kolejki${queueCount ? ` (${queueCount})` : ''}. Zostanie wysłana po bieżącej odpowiedzi.`}
                  data-testid="chat-queue-turn"
                  onClick={() => void submit()}
                  className="chat-send-btn flex h-11 w-11 items-center justify-center disabled:cursor-not-allowed"
                >
                  <SendHorizontal size={16} />
                </button>
                <button
                  type="button"
                  disabled={!onCancel}
                  aria-label={CHAT_STATUS.cancel}
                  title={CHAT_STATUS.cancel}
                  data-testid="chat-cancel-generation"
                  onClick={cancelGeneration}
                  className="chat-send-btn flex h-11 w-11 items-center justify-center border border-sem-warning/50 bg-sem-warning/15 text-sem-warning hover:bg-sem-warning/25 disabled:cursor-not-allowed"
                >
                  <Square size={14} fill="currentColor" />
                </button>
              </>
            ) : (
              <button
                type="button"
                disabled={disabled || uploading || (!draftValue.trim() && stagedAttachments.length === 0)}
                aria-label="Wyślij"
                onClick={() => void submit()}
                className="chat-send-btn flex h-11 w-11 items-center justify-center disabled:cursor-not-allowed"
              >
                <SendHorizontal size={16} />
              </button>
            )}
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? [])
              void stageFiles(files)
            }}
          />
        </div>
      </div>
    </div>
  )
}

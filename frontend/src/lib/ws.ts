export interface WSClientOptions {
  url: string
  onOpen?: (event: WSConnectionEvent) => void
  onClose?: (event: WSConnectionEvent) => void
  onMessage?: (data: string) => void
  onStatusChange?: (event: WSConnectionEvent) => void
  backoffBaseMs?: number
  backoffMaxMs?: number
  backgroundRetryMs?: number
  maxFastReconnectAttempts?: number
  webSocketFactory?: (url: string) => WebSocket
}

export type WSConnectionState =
  | 'connecting'
  | 'open'
  | 'reconnecting'
  | 'background_retry'
  | 'closed'

export interface WSConnectionEvent {
  state: WSConnectionState
  retryCount: number
  nextDelayMs: number | null
}

export interface WSFrame<TType extends string = string, TPayload = unknown> {
  type: TType
  payload: TPayload
}

export type WSIncomingFrame =
  | WSFrame<
      'metrics',
      {
        vramUsedMB: number
        vramTotalMB?: number
        model: { name: string; provider: 'ollama' | 'openai' | 'anthropic' | 'gemini' }
        chroma: 'connected' | 'reconnecting' | 'down'
        network: 'online' | 'degraded' | 'offline'
        rttMs: number | null
      }
    >
  | WSFrame<'stage', { stage: string }>
  | WSFrame<'message', Record<string, unknown>>
  | WSFrame<
      'pipeline.step',
      {
        jobId?: string
        stepId: string
        state: string
        progress?: number
        durationMs?: number | null
        errorMessage?: string | null
      }
    >
  | WSFrame<
      'pipeline.asset',
      {
        jobId?: string
        kind: 'thumbnail' | 'video'
        url: string
      }
    >
  | WSFrame<'toast', Record<string, unknown>>
  | WSFrame<string, Record<string, unknown>>

export type WSOutgoingFrame =
  | WSFrame<'user.message', Record<string, unknown>>
  | WSFrame<'user.command', Record<string, unknown>>
  | WSFrame<'pipeline.approve', { jobId: string }>
  | WSFrame<'pipeline.reject', { jobId: string }>
  | WSFrame<'pipeline.retry', { jobId: string; stepId: string }>

let activeWSClient: WSClient | null = null

function setActiveWSClient(client: WSClient | null) {
  activeWSClient = client
}

export class WSClient {
  private readonly url: string
  private readonly onOpen?: (event: WSConnectionEvent) => void
  private readonly onClose?: (event: WSConnectionEvent) => void
  private readonly onMessage?: (data: string) => void
  private readonly onStatusChange?: (event: WSConnectionEvent) => void
  private readonly backoffBaseMs: number
  private readonly backoffMaxMs: number
  private readonly backgroundRetryMs: number
  private readonly maxFastReconnectAttempts: number
  private readonly webSocketFactory: (url: string) => WebSocket
  private retryCount = 0
  private reconnectTimer: number | null = null
  private socket: WebSocket | null = null
  private disposed = false

  constructor({
    url,
    onOpen,
    onClose,
    onMessage,
    onStatusChange,
    backoffBaseMs = 3000,
    backoffMaxMs = 30000,
    backgroundRetryMs = 30000,
    maxFastReconnectAttempts = 10,
    webSocketFactory,
  }: WSClientOptions) {
    this.url = url
    this.onOpen = onOpen
    this.onClose = onClose
    this.onMessage = onMessage
    this.onStatusChange = onStatusChange
    this.backoffBaseMs = backoffBaseMs
    this.backoffMaxMs = backoffMaxMs
    this.backgroundRetryMs = backgroundRetryMs
    this.maxFastReconnectAttempts = maxFastReconnectAttempts
    this.webSocketFactory = webSocketFactory ?? ((nextUrl) => new WebSocket(nextUrl))
  }

  connect() {
    if (this.disposed) {
      return
    }

    setActiveWSClient(this)
    this.emitStatus({ state: this.retryCount > 0 ? 'reconnecting' : 'connecting', nextDelayMs: null })
    try {
      this.socket = this.webSocketFactory(this.url)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.socket.onopen = () => {
      const retryCount = this.retryCount
      this.retryCount = 0
      this.emitStatus({ state: 'open', retryCount, nextDelayMs: null })
      this.onOpen?.({ state: 'open', retryCount, nextDelayMs: null })
    }
    this.socket.onclose = () => {
      this.socket = null
      if (this.disposed) {
        return
      }

      this.onClose?.({ state: 'closed', retryCount: this.retryCount, nextDelayMs: null })
      this.scheduleReconnect()
    }
    this.socket.onerror = () => {
      if (this.socket && this.socket.readyState !== WebSocket.CLOSED) {
        this.socket.close()
      }
    }
    this.socket.onmessage = (event) => {
      this.onMessage?.(String(event.data))
    }
  }

  disconnect() {
    this.disposed = true
    if (this.reconnectTimer != null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.socket?.close()
    this.socket = null
    if (activeWSClient === this) {
      setActiveWSClient(null)
    }
    this.emitStatus({ state: 'closed', nextDelayMs: null })
  }

  send(frame: WSOutgoingFrame) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return false
    }

    this.socket.send(JSON.stringify(frame))
    return true
  }

  private scheduleReconnect() {
    if (this.disposed) {
      return
    }

    const inBackgroundRetry = this.retryCount >= this.maxFastReconnectAttempts
    const delay = inBackgroundRetry
      ? this.backgroundRetryMs
      : Math.min(this.backoffBaseMs * 2 ** this.retryCount, this.backoffMaxMs)
    this.retryCount += 1
    this.emitStatus({
      state: inBackgroundRetry ? 'background_retry' : 'reconnecting',
      nextDelayMs: delay,
    })
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private emitStatus(
    event: Omit<WSConnectionEvent, 'retryCount'> & { retryCount?: number },
  ) {
    this.onStatusChange?.({
      state: event.state,
      retryCount: event.retryCount ?? this.retryCount,
      nextDelayMs: event.nextDelayMs,
    })
  }
}

export function sendWSFrame(frame: WSOutgoingFrame) {
  return activeWSClient?.send(frame) ?? false
}

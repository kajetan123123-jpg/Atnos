export interface MessageMeta {
  tokensIn?: number
  tokensOut?: number
  latencyMs?: number
  model?: string
}

export interface MessageAttachment {
  name: string
  url: string
  mime: string
}

export interface Message {
  id: string
  speaker: 'atnos' | 'user'
  text: string
  timestamp: number
  attachments?: MessageAttachment[]
  meta?: MessageMeta
}

export interface ConversationAttachment {
  attachmentId: number
  filename: string
  storagePath: string
  kind: string
  source: string
  status: string
  mediaType?: string | null
  fileExtension?: string | null
  sizeBytes?: number | null
  extractedText?: string | null
  metadataJson?: Record<string, unknown> | null
}

export interface ConversationMessage {
  id: number | string
  role: 'user' | 'assistant' | 'system' | 'tool'
  status: string
  sequenceNumber?: number
  content: string
  contentFormat?: string
  promptImproved?: boolean
  attachmentOnly?: boolean
  metadataJson?: Record<string, unknown> | null
  attachments?: ConversationAttachment[]
  createdAt?: string
  updatedAt?: string
}

export interface ConversationSummary {
  id: number
  title: string
  projectId: number | null
  summary: string | null
  lastMessageAt: string | null
  updatedAt: string
  archived?: boolean
  matchedSource?: string | null
}

export function messageToConversationMessage(message: Message): ConversationMessage {
  return {
    id: message.id,
    role: message.speaker === 'atnos' ? 'assistant' : 'user',
    status: 'completed',
    content: message.text,
    createdAt: new Date(message.timestamp).toISOString(),
    attachments: message.attachments?.map((attachment, index) => ({
      attachmentId: index,
      filename: attachment.name,
      storagePath: attachment.url,
      kind: attachment.mime,
      source: 'websocket',
      status: 'available',
      mediaType: attachment.mime,
    })),
    metadataJson: message.meta ? { messageMeta: message.meta } : null,
  }
}

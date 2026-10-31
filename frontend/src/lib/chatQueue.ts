import type { ChatAttachmentPayload } from './api'

export const MAX_QUEUED_CHAT_TURNS = 10

export interface QueuedChatTurn {
  id: string
  text: string
  attachments: ChatAttachmentPayload[]
  promptImproved: boolean
  targetConversationId: number | null
  targetProjectId: number | null
  afterTurnStartedAt: string | null
  createdAt: string
}

export interface ChatQueueWorkspace {
  conversationId: number | null
  projectId: number | null
}

export function enqueueChatTurn(
  queue: QueuedChatTurn[],
  turn: QueuedChatTurn,
  maxSize = MAX_QUEUED_CHAT_TURNS,
): { queue: QueuedChatTurn[]; accepted: boolean } {
  if (queue.length >= maxSize) return { queue, accepted: false }
  return { queue: [...queue, turn], accepted: true }
}

export function bindQueuedTurnsToConversation(
  queue: QueuedChatTurn[],
  turnStartedAt: string | null,
  conversationId: number,
): QueuedChatTurn[] {
  if (!turnStartedAt) return queue
  return queue.map((turn) =>
    turn.afterTurnStartedAt === turnStartedAt && turn.targetConversationId == null
      ? { ...turn, targetConversationId: conversationId }
      : turn,
  )
}

export function takeRunnableChatTurn(
  queue: QueuedChatTurn[],
  workspace: ChatQueueWorkspace,
  activeTurn: boolean,
): { turn: QueuedChatTurn; remaining: QueuedChatTurn[] } | null {
  if (activeTurn || queue.length === 0) return null
  const next = queue[0]
  if (
    next.targetConversationId !== workspace.conversationId ||
    next.targetProjectId !== workspace.projectId
  ) {
    return null
  }
  return { turn: next, remaining: queue.slice(1) }
}

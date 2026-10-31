import { describe, expect, it } from 'vitest'
import {
  bindQueuedTurnsToConversation,
  enqueueChatTurn,
  MAX_QUEUED_CHAT_TURNS,
  takeRunnableChatTurn,
  type QueuedChatTurn,
} from './chatQueue'

function queuedTurn(overrides: Partial<QueuedChatTurn> = {}): QueuedChatTurn {
  return {
    id: 'queued-1',
    text: 'Następne polecenie',
    attachments: [],
    promptImproved: false,
    targetConversationId: 42,
    targetProjectId: 7,
    afterTurnStartedAt: 'turn-1',
    createdAt: '2026-10-26T12:00:00.000Z',
    ...overrides,
  }
}

describe('chat queue', () => {
  it('keeps a FIFO queue bounded and leaves it unchanged when full', () => {
    const full = Array.from({ length: MAX_QUEUED_CHAT_TURNS }, (_, index) =>
      queuedTurn({ id: `queued-${index}` }),
    )

    const result = enqueueChatTurn(full, queuedTurn({ id: 'overflow' }))

    expect(result.accepted).toBe(false)
    expect(result.queue).toBe(full)
  })

  it('binds early queued messages to the conversation created by the active turn', () => {
    const queue = [
      queuedTurn({ targetConversationId: null, afterTurnStartedAt: 'turn-1' }),
      queuedTurn({ id: 'other', targetConversationId: null, afterTurnStartedAt: 'turn-2' }),
    ]

    expect(bindQueuedTurnsToConversation(queue, 'turn-1', 91).map((item) => item.targetConversationId))
      .toEqual([91, null])
  })

  it('dispatches only the head item after the active turn and only in its target workspace', () => {
    const queue = [queuedTurn(), queuedTurn({ id: 'queued-2' })]

    expect(takeRunnableChatTurn(queue, { conversationId: 42, projectId: 7 }, true)).toBeNull()
    expect(takeRunnableChatTurn(queue, { conversationId: 42, projectId: 8 }, false)).toBeNull()

    const result = takeRunnableChatTurn(queue, { conversationId: 42, projectId: 7 }, false)
    expect(result?.turn.id).toBe('queued-1')
    expect(result?.remaining.map((item) => item.id)).toEqual(['queued-2'])
  })
})

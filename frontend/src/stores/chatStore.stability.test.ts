import { beforeEach, describe, expect, it } from 'vitest'
import { useChatStore } from './chatStore'

describe('chatStore replaceAfterTurn isolation', () => {
  beforeEach(() => {
    useChatStore.setState({
      conversations: [
        {
          id: 10,
          title: 'Chat A',
          projectId: null,
          summary: null,
          lastMessageAt: null,
          updatedAt: new Date().toISOString(),
          archived: false,
          matchedSource: null,
        },
        {
          id: 20,
          title: 'Chat B',
          projectId: null,
          summary: null,
          lastMessageAt: null,
          updatedAt: new Date().toISOString(),
          archived: false,
          matchedSource: null,
        },
      ],
      selectedConversationId: 20,
      selectedConversationTitle: 'Chat B',
      hydratedConversationId: 20,
      messages: [
        {
          id: 2001,
          role: 'user',
          status: 'completed',
          content: 'wiadomość z B',
          createdAt: new Date().toISOString(),
        },
      ],
      activeTurn: null,
    })
  })

  it('nie nadpisuje workspace Chat B wynikami turnu Chat A gdy applyWorkspace=false', () => {
    useChatStore.getState().replaceAfterTurn({
      tempId: 'temp-a',
      userMessageId: 1001,
      assistantMessageId: 1002,
      userMessage: 'pytanie A',
      assistantMessage: 'odpowiedź A',
      conversationId: 10,
      conversationTitle: 'Chat A',
      projectId: null,
      applyWorkspace: false,
    })

    const state = useChatStore.getState()
    expect(state.selectedConversationId).toBe(20)
    expect(state.selectedConversationTitle).toBe('Chat B')
    expect(state.messages).toHaveLength(1)
    expect(state.messages[0]?.content).toBe('wiadomość z B')
    expect(state.conversations[0]?.id).toBe(10)
    expect(state.conversations[0]?.summary).toContain('odpowiedź A')
  })

  it('aktualizuje workspace gdy applyWorkspace=true', () => {
    useChatStore.setState({
      messages: [
        {
          id: 'temp-b',
          role: 'user',
          status: 'pending',
          content: 'pytanie B pending',
          createdAt: new Date().toISOString(),
        },
      ],
    })
    useChatStore.getState().replaceAfterTurn({
      tempId: 'temp-b',
      userMessageId: 2002,
      assistantMessageId: 2003,
      userMessage: 'pytanie B',
      assistantMessage: 'odpowiedź B',
      conversationId: 20,
      conversationTitle: 'Chat B updated',
      projectId: null,
      applyWorkspace: true,
    })

    const state = useChatStore.getState()
    expect(state.selectedConversationId).toBe(20)
    expect(state.selectedConversationTitle).toBe('Chat B updated')
    expect(state.messages.map((item) => item.content)).toEqual(['pytanie B', 'odpowiedź B'])
  })
})

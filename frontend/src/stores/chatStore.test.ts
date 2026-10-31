import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import { useChatStore } from './chatStore'
import { resetAllStores } from '../test/resetStores'

describe('chatStore', () => {
  afterEach(() => vi.useRealTimers())

  it('preserves the submission time when a slow response completes and is reconciled again', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-29T20:06:00Z'))
    const tempId = useChatStore.getState().appendOptimisticUserMessage('Test')
    vi.setSystemTime(new Date('2026-10-29T20:08:00Z'))
    const turn = { tempId, userMessageId: 20, assistantMessageId: 21,
      userMessage: 'Test', assistantMessage: 'Wynik', conversationId: 1,
      conversationTitle: 'Test', projectId: null }
    useChatStore.getState().replaceAfterTurn(turn)
    expect(useChatStore.getState().messages[0].createdAt).toBe('2026-10-29T20:06:00.000Z')
    expect(useChatStore.getState().messages[1].createdAt).toBe('2026-10-29T20:08:00.000Z')
    vi.setSystemTime(new Date('2026-10-29T20:09:00Z'))
    useChatStore.getState().replaceAfterTurn(turn)
    expect(useChatStore.getState().messages.map(message => message.createdAt)).toEqual([
      '2026-10-29T20:06:00.000Z', '2026-10-29T20:08:00.000Z',
    ])
  })

  it('uzgadnia final turn z historią wczytaną podczas streamu bez duplikatów', () => {
    useChatStore.setState({ messages: [
      { id: 10, role: 'user', status: 'completed', content: 'To samo pytanie', createdAt: '2026-10-20T10:00:00Z' },
      { id: '20', role: 'user', status: 'completed', content: 'To samo pytanie', createdAt: '2026-10-20T11:00:00Z' },
      { id: 21, role: 'assistant', status: 'pending', content: '', createdAt: '2026-10-20T11:00:00Z' },
    ] })
    const turn = { tempId: 'already-replaced-temp', userMessageId: 20, assistantMessageId: 21,
      userMessage: 'To samo pytanie', assistantMessage: 'Rzeczywisty wynik', conversationId: 1,
      conversationTitle: 'Test', projectId: null }
    useChatStore.getState().replaceAfterTurn(turn)
    useChatStore.getState().replaceAfterTurn(turn)
    const messages = useChatStore.getState().messages
    expect(messages.map((message) => Number(message.id))).toEqual([10, 20, 21])
    expect(messages.at(-1)?.content).toBe('Rzeczywisty wynik')
  })

  beforeEach(() => {
    resetAllStores()
  })

  it('automatycznie wybiera jedyny projekt po bootstrapie', () => {
    useChatStore.getState().setBootstrap({
      uiVersion: 'axiom-prod-v1',
      projects: [
        {
          id: 7,
          name: 'Trusted Auto Demo',
          slug: 'trusted-auto-demo',
          description: null,
          summary: null,
          status: 'active',
          isPinned: false,
          providerPosture: 'prefer-local',
          personaProfile: 'strategist',
        },
      ],
      conversations: [],
    })

    expect(useChatStore.getState().selectedProjectId).toBe(7)
  })

  it('oznacza optimistic message jako failed i zastępuje ją wiadomością o tym samym id', () => {
    const optimisticId = useChatStore.getState().appendOptimisticUserMessage({
      id: 'client-msg-1',
      content: 'Hej Atnos',
      requestText: 'Hej Atnos',
      requestAttachments: [],
    })

    useChatStore
      .getState()
      .setOptimisticMessageState(optimisticId, 'failed', 'Timeout')

    expect(useChatStore.getState().messages[0].status).toBe('failed')
    expect(useChatStore.getState().messages[0].metadataJson).toMatchObject({
      optimistic: true,
      optimisticState: 'failed',
      optimisticErrorMessage: 'Timeout',
    })

    useChatStore.getState().appendMessage({
      id: 'client-msg-1',
      role: 'user',
      status: 'completed',
      content: 'Hej Atnos',
      createdAt: '2026-05-15T00:00:00Z',
      metadataJson: null,
    })

    expect(useChatStore.getState().messages).toHaveLength(1)
    expect(useChatStore.getState().messages[0].status).toBe('completed')
    expect(useChatStore.getState().messages[0].metadataJson).toBeNull()
  })
  it('utrzymuje jedna stabilna pozycje rozmowy po 100 kolejnych odpowiedziach', () => {
    for (let index = 1; index <= 100; index += 1) {
      const tempId = `temp-${index}`
      useChatStore.getState().appendOptimisticUserMessage({
        id: tempId,
        content: `Pytanie ${index}`,
        requestText: `Pytanie ${index}`,
        requestAttachments: [],
      })
      useChatStore.getState().replaceAfterTurn({
        tempId,
        userMessageId: index * 2 - 1,
        assistantMessageId: index * 2,
        userMessage: `Pytanie ${index}`,
        assistantMessage: `Odpowiedz ${index}`,
        assistantMessageMetadataJson: null,
        conversationId: 101,
        conversationTitle: 'Stabilna rozmowa',
        projectId: null,
      })
    }

    const state = useChatStore.getState()
    expect(state.selectedConversationId).toBe(101)
    expect(state.messages).toHaveLength(200)
    expect(state.conversations.filter((conversation) => conversation.id === 101)).toHaveLength(1)
    expect(state.conversations[0].title).toBe('Stabilna rozmowa')
  })
})

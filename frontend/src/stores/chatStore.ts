import { create } from 'zustand'
import type { ProjectPersonaProfile, ProjectProviderPosture } from '../lib/api'
import type { ConversationMessage, ConversationSummary } from '../types/message'
import type { QueuedChatTurn } from '../lib/chatQueue'

export interface ProjectSummary {
  id: number
  name: string
  slug: string
  description: string | null
  summary: string | null
  status: string
  isPinned: boolean
  providerPosture: ProjectProviderPosture | null
  personaProfile: ProjectPersonaProfile | null
}

export interface ConversationSettingsView {
  activeWorkMode: 'auto' | 'quick' | 'deep' | 'execute'
  responseStyle: string | null
  memoryEnabled: boolean
  fileEnabled: boolean
  internetEnabled: boolean
  personaProfile: string
  providerPosture: string
  qualityProfile: 'fast-turnaround' | 'standard' | 'premium'
  sources: Record<string, string>
}

export type ActiveTurnKind = 'chat' | 'briefing'

export interface ActiveTurnState {
  kind: ActiveTurnKind
  conversationId: number | null
  projectId: number | null
  summary: string
  startedAt: string
}

interface ChatState {
  uiVersion: string
  projects: ProjectSummary[]
  conversations: ConversationSummary[]
  selectedProjectId: number | null
  selectedConversationId: number | null
  selectedConversationTitle: string | null
  hydratedConversationId: number | null
  messages: ConversationMessage[]
  searchQuery: string
  settings: ConversationSettingsView | null
  memoryCards: Record<string, unknown> | null
  activeTurn: ActiveTurnState | null
  queuedTurns: QueuedChatTurn[]
  loadingConversation: boolean
  bootstrapHydrated: boolean
  setBootstrap: (payload: {
    uiVersion: string
    projects: ProjectSummary[]
    conversations: ConversationSummary[]
  }) => void
  setSelectedProjectId: (projectId: number | null) => void
  setSelectedConversation: (conversationId: number | null, title?: string | null) => void
  setConversationDetail: (payload: {
    conversationId: number
    title: string
    messages: ConversationMessage[]
    settings: ConversationSettingsView
    memoryCards: Record<string, unknown> | null
  }) => void
  resyncConversationDetail: (payload: {
    conversationId: number
    title: string
    projectId: number | null
    summary: string | null
    lastMessageAt: string | null
    updatedAt: string
    messages: ConversationMessage[]
    settings: ConversationSettingsView
    memoryCards: Record<string, unknown> | null
  }) => void
  setConversationSettings: (settings: ConversationSettingsView) => void
  setLoadingConversation: (value: boolean) => void
  setConversations: (conversations: ConversationSummary[]) => void
  setSearchQuery: (value: string) => void
  startActiveTurn: (payload: {
    kind: ActiveTurnKind
    conversationId: number | null
    projectId: number | null
    summary: string
  }) => void
  finishActiveTurn: () => void
  setQueuedTurns: (queuedTurns: QueuedChatTurn[]) => void
  appendOptimisticUserMessage: (
    payload:
      | string
      | {
          id?: string
          content: string
          requestText?: string
          requestAttachments?: Record<string, unknown>[]
        },
  ) => string
  setOptimisticMessageState: (
    messageId: string | number,
    state: 'pending' | 'failed',
    errorMessage?: string | null,
  ) => void
  replaceAfterTurn: (payload: {
    tempId: string
    userMessageId: number
    assistantMessageId: number
    userMessage: string
    assistantMessage: string
    assistantMessageMetadataJson?: Record<string, unknown> | null
    conversationId: number
    conversationTitle: string
    projectId: number | null
    /** When false, only refresh conversation list — do not hijack workspace/messages. */
    applyWorkspace?: boolean
  }) => void
  appendMessage: (message: ConversationMessage) => void
  upsertStreamingAssistant: (
    messageId: string | number,
    patch: {
      content?: string
      status?: ConversationMessage['status']
      metadataJson?: Record<string, unknown> | null
    },
  ) => void
  appendStreamingAssistantToken: (messageId: string | number, token: string) => void
  clear: () => void
  clearMessages: () => void
  resetConversationWorkspace: (title?: string | null) => void
  upsertConversation: (conversation: ConversationSummary) => void
  removeConversation: (conversationId: number) => void
}

export const useChatStore = create<ChatState>((set) => ({
  uiVersion: 'axiom-prod-v1',
  projects: [],
  conversations: [],
  selectedProjectId: null,
  selectedConversationId: null,
  selectedConversationTitle: null,
  hydratedConversationId: null,
  messages: [],
  searchQuery: '',
  settings: null,
  memoryCards: null,
  activeTurn: null,
  queuedTurns: [],
  loadingConversation: false,
  bootstrapHydrated: false,
  setBootstrap: ({ uiVersion, projects, conversations }) =>
    set((state) => ({
      selectedProjectId:
        state.selectedProjectId != null && projects.some((project) => project.id === state.selectedProjectId)
          ? state.selectedProjectId
          : projects.length === 1
            ? projects[0].id
            : null,
      uiVersion,
      projects,
      conversations,
      bootstrapHydrated: true,
      selectedConversationId:
        state.selectedConversationId != null &&
        conversations.some((conversation) => conversation.id === state.selectedConversationId)
          ? state.selectedConversationId
          : conversations[0]?.id ?? null,
      selectedConversationTitle:
        state.selectedConversationId != null &&
        conversations.some((conversation) => conversation.id === state.selectedConversationId)
          ? state.selectedConversationTitle
          : conversations[0]?.title ?? null,
    })),
  setSelectedProjectId: (selectedProjectId) => set({ selectedProjectId }),
  setSelectedConversation: (selectedConversationId, selectedConversationTitle = null) =>
    set({ selectedConversationId, selectedConversationTitle }),
  setConversationDetail: ({ conversationId, title, messages, settings, memoryCards }) =>
    set({
      selectedConversationId: conversationId,
      selectedConversationTitle: title,
      hydratedConversationId: conversationId,
      messages,
      settings,
      memoryCards,
      loadingConversation: false,
    }),
  resyncConversationDetail: ({
    conversationId,
    title,
    projectId,
    summary,
    lastMessageAt,
    updatedAt,
    messages,
    settings,
    memoryCards,
  }) =>
    set((state) => ({
      selectedConversationId: conversationId,
      selectedConversationTitle: title,
      hydratedConversationId: conversationId,
      messages,
      settings,
      memoryCards,
      loadingConversation: false,
      conversations: [
        {
          id: conversationId,
          title,
          projectId,
          summary,
          lastMessageAt,
          updatedAt,
          archived: false,
          matchedSource: null,
        },
        ...state.conversations.filter((item) => item.id !== conversationId),
      ],
    })),
  setConversationSettings: (settings) => set({ settings }),
  setLoadingConversation: (loadingConversation) => set({ loadingConversation }),
  setConversations: (conversations) => set({ conversations }),
  setSearchQuery: (searchQuery) => set({ searchQuery }),
  startActiveTurn: ({ kind, conversationId, projectId, summary }) =>
    set({
      activeTurn: {
        kind,
        conversationId,
        projectId,
        summary,
        startedAt: new Date().toISOString(),
      },
    }),
  finishActiveTurn: () => set({ activeTurn: null }),
  setQueuedTurns: (queuedTurns) => set({ queuedTurns }),
  appendOptimisticUserMessage: (payload) => {
    const next =
      typeof payload === 'string'
        ? {
            id: crypto.randomUUID(),
            content: payload,
            requestText: payload,
            requestAttachments: [] as Record<string, unknown>[],
          }
        : {
            id: payload.id ?? crypto.randomUUID(),
            content: payload.content,
            requestText: payload.requestText ?? payload.content,
            requestAttachments: payload.requestAttachments ?? [],
          }

    set((state) => ({
      messages: [
        ...state.messages,
        {
          id: next.id,
          role: 'user',
          status: 'pending',
          content: next.content,
          createdAt: new Date().toISOString(),
          metadataJson: {
            optimistic: true,
            optimisticState: 'pending',
            optimisticErrorMessage: null,
            optimisticRequest: {
              text: next.requestText,
              attachments: next.requestAttachments,
            },
          },
        },
      ],
    }))
    return next.id
  },
  setOptimisticMessageState: (messageId, optimisticState, errorMessage = null) =>
    set((state) => ({
      messages: state.messages.map((message) => {
        if (message.id !== messageId) {
          return message
        }

        return {
          ...message,
          status: optimisticState === 'failed' ? 'failed' : 'pending',
          metadataJson: {
            ...(message.metadataJson ?? {}),
            optimistic: true,
            optimisticState,
            optimisticErrorMessage: errorMessage,
          },
        }
      }),
    })),
  replaceAfterTurn: ({
    tempId,
    userMessageId,
    assistantMessageId,
    userMessage,
    assistantMessage,
    assistantMessageMetadataJson,
    conversationId,
    conversationTitle,
    projectId,
    applyWorkspace = true,
    }) =>
    set((state) => {
      const conversationEntry = {
        id: conversationId,
        title: conversationTitle,
        projectId,
        summary: assistantMessage.slice(0, 140),
        lastMessageAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        archived: false,
        matchedSource: null,
      }
      const conversations = [
        conversationEntry,
        ...state.conversations.filter((item) => item.id !== conversationId),
      ]
      if (!applyWorkspace) {
        return { conversations }
      }
      const submittedMessage = state.messages.find(message => String(message.id) === String(userMessageId))
        ?? state.messages.find(message => message.id === tempId)
      const completedReply = state.messages.find(message =>
        String(message.id) === String(assistantMessageId) && message.status === 'completed')
      return {
        selectedConversationId: conversationId,
        selectedConversationTitle: conversationTitle,
        hydratedConversationId: conversationId,
        messages: [
          ...state.messages.filter((message) =>
            message.id !== tempId &&
            String(message.id) !== String(userMessageId) &&
            String(message.id) !== String(assistantMessageId),
          ),
          {
            id: userMessageId,
            role: 'user' as const,
            status: 'completed',
            content: userMessage,
            createdAt: submittedMessage?.createdAt ?? new Date().toISOString(),
          },
          {
            id: assistantMessageId,
            role: 'assistant' as const,
            status: 'completed',
            content: assistantMessage,
            createdAt: completedReply?.createdAt ?? new Date().toISOString(),
            metadataJson: assistantMessageMetadataJson ?? null,
          },
        ],
        conversations,
      }
    }),
  appendMessage: (message) =>
    set((state) => ({
      messages: state.messages.some((item) => item.id === message.id)
        ? state.messages.map((item) => (item.id === message.id ? message : item))
        : [...state.messages, message],
    })),
  upsertStreamingAssistant: (messageId, patch) =>
    set((state) => {
      const existing = state.messages.find((item) => item.id === messageId)
      if (existing == null) {
        return {
          messages: [
            ...state.messages,
            {
              id: messageId,
              role: 'assistant',
              status: 'pending',
              content: patch.content ?? '',
              createdAt: new Date().toISOString(),
              metadataJson: patch.metadataJson ?? { streaming: true },
            },
          ],
        }
      }
      return {
        messages: state.messages.map((item) => {
          if (item.id !== messageId) {
            return item
          }
          return {
            ...item,
            content: patch.content !== undefined ? patch.content : item.content,
            status: patch.status ?? item.status,
            metadataJson:
              patch.metadataJson !== undefined
                ? { ...(item.metadataJson ?? {}), ...patch.metadataJson }
                : item.metadataJson,
          }
        }),
      }
    }),
  appendStreamingAssistantToken: (messageId, token) =>
    set((state) => ({
      messages: state.messages.map((item) => {
        if (item.id !== messageId) {
          return item
        }
        return {
          ...item,
          status: 'pending',
          content: `${item.content || ''}${token}`,
          metadataJson: { ...(item.metadataJson ?? {}), streaming: true },
        }
      }),
    })),
  clear: () => set({ messages: [], queuedTurns: [] }),
  clearMessages: () => set({ messages: [] }),
  resetConversationWorkspace: (selectedConversationTitle = 'Nowa rozmowa') =>
    set({
      selectedConversationId: null,
      selectedConversationTitle,
      hydratedConversationId: null,
      messages: [],
      settings: null,
      memoryCards: null,
      loadingConversation: false,
    }),
  upsertConversation: (conversation) =>
    set((state) => ({
      conversations: [
        conversation,
        ...state.conversations.filter((item) => item.id !== conversation.id),
      ],
    })),
  removeConversation: (conversationId) =>
    set((state) => ({
      conversations: state.conversations.filter((item) => item.id !== conversationId),
    })),
}))

export const useChat = useChatStore

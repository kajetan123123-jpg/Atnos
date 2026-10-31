import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getRootCommands, getTopRowCommand } from './commands'
import type { CommandContext } from '../types/command'

function createContext(overrides: Partial<CommandContext> = {}): CommandContext {
  return {
    chatCommandSurface: null,
    closeModelPicker: vi.fn(),
    closeScope: vi.fn(),
    conversations: [],
    executionRuns: [],
    messages: [],
    navigate: vi.fn(),
    openConversationPicker: vi.fn(),
    openModelPicker: vi.fn(),
    openProjectPicker: vi.fn(),
    pathname: '/chat',
    pipelineSnapshot: null,
    projects: [],
    pushToast: vi.fn(),
    requestBriefing: vi.fn(),
    requestComposerFocus: vi.fn(),
    requestDecisions: vi.fn(),
    resetConversationWorkspace: vi.fn(),
    selectConversationWorkspace: vi.fn(),
    selectedConversationId: 101,
    selectedConversationTitle: null,
    selectedProjectId: null,
    selectProjectWorkspace: vi.fn(),
    upsertExecutionRun: vi.fn(),
    ...overrides,
  }
}

describe('commands registry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('exposes the primary chat actions in the root palette', () => {
    const labels = getRootCommands(createContext()).map((command) => command.label)

    expect(labels).toEqual(
      expect.arrayContaining([
        'Skup composer',
        'Nowa rozmowa',
        'Uruchom briefing',
        'Pokaz decyzje i commitments',
        'Szukaj rozmow',
        'Przelacz projekt',
        'Przelacz model',
        'Otworz ustawienia',
      ]),
    )
  })

  it('returns "Skup composer" as the top-row action on chat', () => {
    const command = getTopRowCommand(createContext())

    expect(command.label).toBe('Skup composer')
  })

  it('returns "Wroc do rozmowy" as the top-row action outside chat', () => {
    const command = getTopRowCommand(
      createContext({
        pathname: '/settings',
      }),
    )

    expect(command.label).toBe('Wroc do rozmowy')
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CommandPalette } from './CommandPalette'
import { useChatStore } from '../stores/chatStore'
import { useUIStore } from '../stores/uiStore'
import { resetAllStores } from '../test/resetStores'

vi.mock('focus-trap-react', () => ({
  default: ({ children }: { children: ReactNode }) => children,
}))

describe('CommandPalette', () => {
  beforeEach(() => {
    resetAllStores()
    window.localStorage.clear()
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 1280,
    })
  })

  it('opens with Ctrl+K, filters to Ustawienia, navigates on Enter, and closes with ESC', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route path="/chat" element={<div>Chat page</div>} />
          <Route path="/settings" element={<div>Settings page</div>} />
        </Routes>
        <CommandPalette />
      </MemoryRouter>,
    )

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })

    const input = await screen.findByRole('textbox', { name: 'Wpisz polecenie albo szukaj' })
    const dialog = screen.getByRole('dialog', { name: 'Paleta polecen' })
    expect(dialog).toBeInTheDocument()

    await user.type(input, 'ust')

    const settingsOption = await screen.findByRole('option', { name: /otworz ustawienia/i })
    expect(settingsOption).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('option', { name: /przelacz projekt/i })).not.toBeInTheDocument()

    await user.keyboard('{Enter}')

    await screen.findByText('Settings page')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Paleta polecen' })).not.toBeInTheDocument()
    })

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    await screen.findByRole('dialog', { name: 'Paleta polecen' })

    await user.keyboard('{Escape}')

    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Paleta polecen' })).not.toBeInTheDocument()
    })
  })

  it('opens conversation picker from the palette and selects a conversation', async () => {
    const user = userEvent.setup()

    useChatStore.setState({
      conversations: [
        {
          id: 101,
          title: 'Plan short',
          projectId: 1,
          summary: 'brief AI',
          lastMessageAt: '2026-05-16T10:00:00Z',
          updatedAt: '2026-05-16T10:00:00Z',
          archived: false,
          matchedSource: null,
        },
        {
          id: 102,
          title: 'Review Linear',
          projectId: 1,
          summary: 'dzienny follow-up',
          lastMessageAt: '2026-05-17T10:00:00Z',
          updatedAt: '2026-05-17T10:00:00Z',
          archived: false,
          matchedSource: null,
        },
      ],
    })

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route path="/chat" element={<div>Chat page</div>} />
        </Routes>
        <CommandPalette />
      </MemoryRouter>,
    )

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    const input = await screen.findByRole('textbox', { name: 'Wpisz polecenie albo szukaj' })
    await user.type(input, 'szukaj rozmow')
    await user.keyboard('{Enter}')

    await waitFor(() => {
      expect(screen.getByText('SZUKAJ ROZMOW')).toBeInTheDocument()
    })

    const scopedInput = screen.getByRole('textbox', { name: 'Wpisz polecenie albo szukaj' })
    await user.clear(scopedInput)
    await user.type(scopedInput, 'review')

    const conversationOption = await screen.findByRole('option', { name: /review linear/i })
    expect(conversationOption).toBeInTheDocument()

    await user.keyboard('{Enter}')

    expect(useChatStore.getState().selectedConversationId).toBe(102)
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Paleta polecen' })).not.toBeInTheDocument()
    })
  })

  it('toggles memory from the palette when chat actions are available', async () => {
    const user = userEvent.setup()
    const toggleQuickSetting = vi.fn()

    useUIStore.setState({
      chatCommandSurface: {
        activeConversationId: 101,
        activeConversationTitle: 'Plan short',
        activeProjectId: 1,
        activeProjectName: 'Projekt bazowy',
        quickToggles: {
          memoryEnabled: true,
          fileEnabled: true,
          internetEnabled: true,
        },
        actions: {
          archiveConversation: vi.fn(),
          selectConversation: vi.fn(),
          selectProject: vi.fn(),
          showDecisions: vi.fn(),
          toggleQuickSetting,
          triggerBriefing: vi.fn(),
        },
      },
    })

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route path="/chat" element={<div>Chat page</div>} />
        </Routes>
        <CommandPalette />
      </MemoryRouter>,
    )

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    const input = await screen.findByRole('textbox', { name: 'Wpisz polecenie albo szukaj' })
    await user.type(input, 'pamiec')

    expect(await screen.findByRole('option', { name: /wylacz pamiec rozmowy/i })).toBeInTheDocument()

    await user.keyboard('{Enter}')

    expect(toggleQuickSetting).toHaveBeenCalledWith('memoryEnabled')
  })
})

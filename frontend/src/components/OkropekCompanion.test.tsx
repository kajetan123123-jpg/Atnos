import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OkropekCompanion } from './OkropekCompanion'
import * as api from '../lib/api'
import { useChatStore } from '../stores/chatStore'
import { useSystemStore } from '../stores/systemStore'
import { useUIStore } from '../stores/uiStore'
import { buildBootstrapPayload, buildChatTurnResponse, buildConversationDetailPayload } from '../test/factories'
import { resetAllStores } from '../test/resetStores'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchConversationDetail: vi.fn(),
    sendChatTurnStream: vi.fn(),
  }
})

describe('OkropekCompanion', () => {
  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
    Object.defineProperty(window, 'speechSynthesis', {
      writable: true,
      value: {
        cancel: vi.fn(),
        speak: vi.fn(),
        getVoices: vi.fn(() => []),
      },
    })

    const bootstrap = buildBootstrapPayload()
    act(() => {
      useChatStore.getState().setBootstrap({
        uiVersion: bootstrap.uiVersion,
        projects: bootstrap.projects,
        conversations: bootstrap.recentConversations,
      })
      useUIStore.getState().setOkropekModeEnabled(true)
      useSystemStore.getState().setStage('idle')
    })

    vi.mocked(api.fetchConversationDetail).mockResolvedValue(buildConversationDetailPayload())
  })

  it('wysyla polecenie przez zwykly chat backend z poziomu Okropka', async () => {
    const user = userEvent.setup()
    const turn = buildChatTurnResponse()
    vi.mocked(api.sendChatTurnStream).mockResolvedValue(turn)

    render(<OkropekCompanion />)

    await user.click(screen.getByRole('button', { name: 'Otwórz Okropka' }))

    const textarea = await screen.findByPlaceholderText('Powiedz Okropkowi, co ma zrobić...')
    fireEvent.change(textarea, { target: { value: 'Atnos, Zrob plan dnia' } })
    await user.click(screen.getByRole('button', { name: 'Wyślij do Okropka' }))

    await waitFor(() => {
      expect(api.sendChatTurnStream).toHaveBeenCalledWith(
        expect.objectContaining({
          content: 'Zrob plan dnia',
          conversation_id: 101,
          message_metadata_json: expect.objectContaining({ companion: 'okropek' }),
        }),
        expect.objectContaining({
          onEvent: expect.any(Function),
        }),
      )
    })

    await waitFor(() => {
      expect(useChatStore.getState().selectedConversationTitle).toBe(turn.conversationTitle)
      expect(screen.getByText(/Ostatnia odpowiedź:/i)).toBeInTheDocument()
    })
  })

  it('zwija panel po bezczynnosci', async () => {
    vi.useFakeTimers()
    try {
      render(<OkropekCompanion />)

      fireEvent.click(screen.getByRole('button', { name: 'Otwórz Okropka' }))
      expect(screen.getByPlaceholderText('Powiedz Okropkowi, co ma zrobić...')).toBeInTheDocument()

      act(() => {
        vi.advanceTimersByTime(21000)
      })

      expect(screen.queryByPlaceholderText('Powiedz Okropkowi, co ma zrobić...')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps a panel opened from Centrum visible after the companion has been idle', () => {
    vi.useFakeTimers()
    try {
      render(<OkropekCompanion />)
      act(() => { vi.advanceTimersByTime(33_000) })
      expect(screen.getByRole('button', { name: 'Otwórz Okropka' })).toHaveAttribute('data-sleeping', 'true')

      act(() => { useUIStore.getState().setOkropekPanelOpen(true) })
      act(() => { vi.advanceTimersByTime(1000) })
      expect(screen.getByPlaceholderText('Powiedz Okropkowi, co ma zrobić...')).toBeInTheDocument()
      expect(api.sendChatTurnStream).not.toHaveBeenCalled()

      // External opening begins a new idle period, not a permanently pinned panel.
      act(() => { vi.advanceTimersByTime(20_000) })
      expect(screen.queryByPlaceholderText('Powiedz Okropkowi, co ma zrobić...')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('przechodzi w tryb uspienia po dluzszej bezczynnosci', () => {
    vi.useFakeTimers()
    try {
      render(<OkropekCompanion />)

      const trigger = screen.getByRole('button', { name: 'Otwórz Okropka' })
      expect(trigger).toHaveAttribute('data-sleeping', 'false')

      act(() => {
        vi.advanceTimersByTime(33000)
      })

      expect(screen.getByRole('button', { name: 'Otwórz Okropka' })).toHaveAttribute('data-sleeping', 'true')
    } finally {
      vi.useRealTimers()
    }
  })

  it('co 5 minut odpala jeden z trzech ruchow avatara', () => {
    vi.useFakeTimers()
    try {
      render(<OkropekCompanion />)
      const avatar = screen.getByRole('img', { name: /Okropek/i })
      expect(avatar.getAttribute('aria-label')).toMatch(/idle|thinking|executing|listening|error/i)

      act(() => {
        vi.advanceTimersByTime(18_000)
      })
      expect(screen.getByRole('img', { name: /Okropek: (wave|hop|peek)/i })).toBeInTheDocument()

      act(() => {
        vi.advanceTimersByTime(5_000)
      })
      // Move ends after duration, then next cycle at 5 minutes from prior schedule is separate.
      expect(screen.getByRole('img', { name: /Okropek/i })).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect, useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import { useToastStore } from '../stores/toastStore'
import { ChatInput } from './ChatInput'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    uploadChatFile: vi.fn(),
    deleteUpload: vi.fn(),
  }
})

describe('ChatInput', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useToastStore.setState({ queue: [], toasts: [] })
  })

  it('wkleja screenshot z clipboarda, pokazuje podglad i wysyla attachment do chatu', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(true)

    vi.mocked(api.uploadChatFile).mockResolvedValue({
      filename: 'screenshot.png',
      storage_path: 'uploads/screenshot.png',
      kind: 'image',
      media_type: 'image/png',
      file_extension: '.png',
      size_bytes: 128,
      detected_kind: 'image',
      understanding_status: 'ready',
      understanding_reason: null,
      preview_text: null,
      truncated: false,
      extracted_text: null,
    })

    render(<ChatInput onSubmit={onSubmit} onImprovePrompt={async () => null} />)

    const textarea = screen.getByRole('textbox')
    const file = new File(['binary'], 'screenshot.png', { type: 'image/png' })

    fireEvent.paste(textarea, {
      clipboardData: {
        items: [
          {
            kind: 'file',
            type: 'image/png',
            getAsFile: () => file,
          },
        ],
      },
    })

    expect(api.uploadChatFile).toHaveBeenCalledWith(file)
    expect(await screen.findByTestId('chat-staged-attachments')).toBeInTheDocument()
    expect(screen.getByText('screenshot.png')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /wyślij/i }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        text: '',
        attachments: [
          expect.objectContaining({
            filename: 'screenshot.png',
            storage_path: 'uploads/screenshot.png',
            kind: 'image',
          }),
        ],
        promptImproved: false,
      })
    })
  })

  it('obsluguje drag and drop plikow do composera', async () => {
    vi.mocked(api.uploadChatFile).mockResolvedValue({
      filename: 'brief.pdf',
      storage_path: 'uploads/brief.pdf',
      kind: 'document',
      media_type: 'application/pdf',
      file_extension: '.pdf',
      size_bytes: 512,
      detected_kind: 'document',
      understanding_status: 'ready',
      understanding_reason: null,
      preview_text: null,
      truncated: false,
      extracted_text: null,
    })

    render(<ChatInput onSubmit={vi.fn().mockResolvedValue(true)} onImprovePrompt={async () => null} />)

    const textarea = screen.getByRole('textbox')
    const dropZone = textarea.parentElement
    expect(dropZone).not.toBeNull()
    const file = new File(['pdf'], 'brief.pdf', { type: 'application/pdf' })

    fireEvent.dragEnter(dropZone as HTMLElement, {
      dataTransfer: {
        types: ['Files'],
      },
    })
    fireEvent.drop(dropZone as HTMLElement, {
      dataTransfer: {
        files: [file],
      },
    })

    expect(api.uploadChatFile).toHaveBeenCalledWith(file)
    expect(await screen.findByText('brief.pdf')).toBeInTheDocument()
  })

  it('przepisuje draft po kliknieciu popraw prompt', async () => {
    const user = userEvent.setup()
    const onImprovePrompt = vi.fn().mockResolvedValue({
      text: 'Sprawdz, jakie karty sa teraz otwarte w Google Chrome i zwroc tytul plus URL.',
      strategyNote: 'Doprecyzowano cel i format odpowiedzi.',
      changed: true,
    })

    render(<ChatInput onSubmit={vi.fn().mockResolvedValue(true)} onImprovePrompt={onImprovePrompt} />)

    const textarea = screen.getByRole('textbox')
    await user.type(textarea, 'powiedz jakie karty sa otwarte w chrome?')
    await user.click(screen.getByRole('button', { name: /popraw prompt/i }))

    await waitFor(() => {
      expect(onImprovePrompt).toHaveBeenCalledWith('powiedz jakie karty sa otwarte w chrome?')
      expect(screen.getByDisplayValue(/Sprawdz, jakie karty sa teraz otwarte/i)).toBeInTheDocument()
    })
  })

  it('pokazuje Przerwij podczas generowania i wywoluje onCancel', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    const onSubmit = vi.fn().mockResolvedValue(true)

    render(
      <ChatInput
        generating
        onCancel={onCancel}
        onSubmit={onSubmit}
        onImprovePrompt={async () => null}
      />,
    )

    expect(screen.getByRole('button', { name: /dodaj do kolejki/i })).toBeInTheDocument()
    const cancelButton = screen.getByTestId('chat-cancel-generation')
    expect(cancelButton).toHaveAttribute('aria-label', 'Przerwij')

    await user.click(cancelButton)
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('kolejkuje Enter podczas generowania zamiast blokować kolejną wiadomość', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(true)

    render(
      <ChatInput
        generating
        value="wiadomość podczas streamu"
        onSubmit={onSubmit}
        onImprovePrompt={async () => null}
      />,
    )

    await user.type(screen.getByRole('textbox'), '{Enter}')
    expect(onSubmit).toHaveBeenCalledWith({
      text: 'wiadomość podczas streamu',
      attachments: [],
      promptImproved: false,
    })
  })

  it('pozwala sterowac draftem z zewnatrz', async () => {
    const user = userEvent.setup()
    const onValueChange = vi.fn()

    function ControlledHarness({ value }: { value: string }) {
      const [draft, setDraft] = useState(value)
      useEffect(() => {
        setDraft(value)
      }, [value])
      return (
        <ChatInput
          value={draft}
          onValueChange={(nextValue) => {
            onValueChange(nextValue)
            setDraft(nextValue)
          }}
          onSubmit={vi.fn().mockResolvedValue(true)}
          onImprovePrompt={async () => null}
        />
      )
    }

    const view = render(
      <ControlledHarness value="roboczy draft" />,
    )

    const textarea = screen.getByRole('textbox')
    expect(textarea).toHaveValue('roboczy draft')

    await user.type(textarea, ' 2')
    expect(onValueChange).toHaveBeenLastCalledWith('roboczy draft 2')

    view.rerender(<ControlledHarness value="przywrocony draft" />)

    expect(screen.getByRole('textbox')).toHaveValue('przywrocony draft')
  })
})

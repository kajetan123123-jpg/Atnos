import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChatStream } from './ChatStream'

vi.mock('framer-motion', async () => {
  const actual = await vi.importActual<typeof import('framer-motion')>('framer-motion')
  return {
    ...actual,
    useReducedMotion: () => false,
  }
})

vi.mock('./ChatInput', () => ({
  ChatInput: () => <div data-testid="chat-input">chat-input</div>,
}))

vi.mock('./ChatMessage', () => ({
  ChatMessage: ({ message }: { message: { content: string } }) => (
    <div data-testid="chat-message">{message.content}</div>
  ),
}))

vi.mock('./TypingIndicator', () => ({
  TypingIndicator: () => <div data-testid="typing-indicator">Atnos odpowiada…</div>,
}))

describe('ChatStream', () => {
  const baseMessages = [
    {
      id: 41,
      role: 'assistant' as const,
      status: 'completed' as const,
      sequenceNumber: 41,
      content: 'Ostatnia odpowiedz Atnosa',
      createdAt: '2026-05-08T10:00:00.000Z',
    },
    {
      id: 42,
      role: 'user' as const,
      status: 'completed' as const,
      sequenceNumber: 42,
      content: 'Nowa wiadomosc usera',
      createdAt: '2026-05-08T10:00:02.000Z',
    },
  ]

  function setScrollMetrics(node: HTMLElement, options: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
    Object.defineProperty(node, 'scrollHeight', {
      configurable: true,
      value: options.scrollHeight,
    })
    Object.defineProperty(node, 'clientHeight', {
      configurable: true,
      value: options.clientHeight,
    })
    node.scrollTop = options.scrollTop
  }

  function mockContainerScrollTo(node: HTMLElement) {
    const scrollTo = vi.fn((options?: ScrollToOptions) => {
      if (typeof options?.top === 'number') {
        node.scrollTop = options.top
      }
    })
    Object.defineProperty(node, 'scrollTo', {
      configurable: true,
      value: scrollTo,
    })
    return scrollTo
  }

  it('trzyma optimistic message pod ostatnia odpowiedzia zamiast wyrzucac ja na gore', () => {
    render(
      <ChatStream
        messages={[
          {
            id: 41,
            role: 'assistant',
            status: 'completed',
            sequenceNumber: 41,
            content: 'Ostatnia odpowiedz Atnosa',
            createdAt: '2026-05-08T10:00:00.000Z',
          },
          {
            id: 'temp-1',
            role: 'user',
            status: 'created',
            content: 'Nowa wiadomosc usera',
            createdAt: '2026-05-08T10:00:02.000Z',
          },
        ]}
        agentStage="thinking"
        showTypingIndicator={false}
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )

    const contents = screen.getAllByTestId('chat-message').map((node) => node.textContent)
    expect(contents).toEqual(['Ostatnia odpowiedz Atnosa', 'Nowa wiadomosc usera'])
  })

  it('pokazuje typing indicator tylko dla stanu thinking', () => {
    render(
      <ChatStream
        messages={[
          {
            id: 41,
            role: 'user',
            status: 'completed',
            content: 'Przygotuj prezentacje',
            createdAt: '2026-05-08T10:00:00.000Z',
          },
        ]}
        agentStage="thinking"
        showTypingIndicator
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )

    expect(screen.getByTestId('typing-indicator')).toHaveTextContent('Atnos odpowiada…')
  })

  it('nie pokazuje typing indicatora dla stanu executing', () => {
    render(
      <ChatStream
        messages={[
          {
            id: 41,
            role: 'assistant',
            status: 'completed',
            content: 'Gotowa odpowiedz',
            createdAt: '2026-05-08T10:00:00.000Z',
          },
        ]}
        agentStage="executing"
        showTypingIndicator
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )

    expect(screen.queryByTestId('typing-indicator')).not.toBeInTheDocument()
  })

  it('nie przewija na dol, gdy user celowo czyta starsze wiadomosci', () => {
    const scrollIntoViewMock = vi.mocked(HTMLElement.prototype.scrollIntoView)
    const { rerender } = render(
      <ChatStream
        messages={baseMessages}
        agentStage="idle"
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )
    const scrollContainer = screen.getByTestId('chat-scroll-container')
    setScrollMetrics(scrollContainer, {
      scrollHeight: 1200,
      clientHeight: 400,
      scrollTop: 240,
    })
    const scrollToMock = mockContainerScrollTo(scrollContainer)
    fireEvent.scroll(scrollContainer)
    scrollIntoViewMock.mockClear()
    scrollToMock.mockClear()

    rerender(
      <ChatStream
        messages={[
          ...baseMessages,
          {
            id: 43,
            role: 'assistant',
            status: 'completed',
            sequenceNumber: 43,
            content: 'Status workflow odswiezony',
            createdAt: '2026-05-08T10:00:05.000Z',
          },
        ]}
        agentStage="executing"
        activeWorkStatus={{
          title: 'ATNOS pracuje nad shortem',
          detail: 'Aktualny etap: Renderuje MP4.',
          statusLabel: 'Generowanie',
          stepLabel: 'Renderuje MP4',
        }}
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )

    expect(scrollIntoViewMock).not.toHaveBeenCalled()
    expect(scrollToMock).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /Nowe wiadomości/i })).toBeInTheDocument()
    expect(screen.getByText('ATNOS pracuje nad shortem')).toBeInTheDocument()
    expect(screen.getByText('Renderuje MP4')).toBeInTheDocument()
  })

  it('nie zjezdza na dol przy samym odswiezeniu statusu, nawet gdy event scrolla nie dotarl', () => {
    const { rerender } = render(
      <ChatStream
        messages={baseMessages}
        agentStage="idle"
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )
    const scrollContainer = screen.getByTestId('chat-scroll-container')
    setScrollMetrics(scrollContainer, {
      scrollHeight: 1200,
      clientHeight: 400,
      scrollTop: 240,
    })
    const scrollToMock = mockContainerScrollTo(scrollContainer)
    scrollToMock.mockClear()

    rerender(
      <ChatStream
        messages={baseMessages}
        agentStage="executing"
        activeWorkStatus={{
          title: 'ATNOS pracuje nad shortem',
          detail: 'Aktualny etap: Renderuje MP4.',
          statusLabel: 'Generowanie',
          stepLabel: 'Renderuje MP4',
        }}
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )

    expect(scrollToMock).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /Nowe wiadomości/i })).toBeInTheDocument()
    expect(screen.getByText(/Nie przewijam automatycznie/i)).toBeInTheDocument()
  })

  it('przewija na dol tylko wewnatrz chatu, kiedy user zostal przy najnowszych wiadomosciach', () => {
    const scrollIntoViewMock = vi.mocked(HTMLElement.prototype.scrollIntoView)
    const windowScrollToMock = vi.mocked(window.scrollTo)
    const { rerender } = render(
      <ChatStream
        messages={baseMessages}
        agentStage="idle"
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )
    const scrollContainer = screen.getByTestId('chat-scroll-container')
    setScrollMetrics(scrollContainer, {
      scrollHeight: 1200,
      clientHeight: 400,
      scrollTop: 800,
    })
    const scrollToMock = mockContainerScrollTo(scrollContainer)
    fireEvent.scroll(scrollContainer)
    scrollIntoViewMock.mockClear()
    windowScrollToMock.mockClear()
    scrollToMock.mockClear()

    rerender(
      <ChatStream
        messages={[
          ...baseMessages,
          {
            id: 44,
            role: 'assistant',
            status: 'completed',
            sequenceNumber: 44,
            content: 'Gotowe',
            createdAt: '2026-05-08T10:00:06.000Z',
          },
        ]}
        agentStage="idle"
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )

    expect(scrollToMock).toHaveBeenCalledWith({
      top: 800,
      left: 0,
      behavior: 'smooth',
    })
    expect(scrollContainer.scrollTop).toBe(800)
    expect(scrollIntoViewMock).not.toHaveBeenCalled()
    expect(windowScrollToMock).not.toHaveBeenCalled()
  })

  it('pokazuje oczekujące wiadomości i pozwala usunąć je przed wysłaniem', () => {
    const onRemoveQueuedMessage = vi.fn()

    render(
      <ChatStream
        messages={baseMessages}
        agentStage="executing"
        generating
        queuedMessages={[
          { id: 'queued-1', text: 'Sprawdź raport i podsumuj wnioski', targetLabel: 'Rozmowa: Raport' },
        ]}
        onRemoveQueuedMessage={onRemoveQueuedMessage}
        onSubmit={() => true}
        onImprovePrompt={async () => null}
      />,
    )

    expect(screen.getByTestId('chat-queued-messages')).toHaveTextContent('KOLEJKA · 1')
    expect(screen.getByText('Sprawdź raport i podsumuj wnioski')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /usuń z kolejki/i }))
    expect(onRemoveQueuedMessage).toHaveBeenCalledWith('queued-1')
  })
})

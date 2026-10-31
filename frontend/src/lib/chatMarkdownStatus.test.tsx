import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChatMarkdownBody } from './renderChatMarkdown'
import { CHAT_STATUS, humanizeChatError } from './chatStatusCopy'

describe('chatStatusCopy', () => {
  it('maps connection failures to Polish API status', () => {
    expect(humanizeChatError(new Error('Failed to fetch'))).toBe(CHAT_STATUS.apiUnavailable)
    expect(humanizeChatError(new Error('Ollama unavailable on :11434'))).toBe(
      CHAT_STATUS.ollamaUnavailable,
    )
    expect(humanizeChatError(new Error('Traceback (most recent call last):\n  File "x"'))).toBe(
      CHAT_STATUS.sendFailed,
    )
  })
})

describe('ChatMarkdownBody', () => {
  it('renders lists, links, headings and emphasis', () => {
    render(
      <ChatMarkdownBody
        text={
          '## Nagłówek\n\nLista:\n- jeden\n- dwa\n\nZobacz [Atnos](https://example.com) i *ważne*.'
        }
      />,
    )
    expect(screen.getByRole('heading', { name: 'Nagłówek' })).toBeInTheDocument()
    expect(screen.getByText('jeden')).toBeInTheDocument()
    expect(screen.getByText('dwa')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Atnos' })).toHaveAttribute(
      'href',
      'https://example.com',
    )
    expect(screen.getByText('ważne').tagName).toBe('EM')
  })
})

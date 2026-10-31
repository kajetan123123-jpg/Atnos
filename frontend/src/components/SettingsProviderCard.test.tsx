import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsProviderCard } from './SettingsProviderCard'

describe('SettingsProviderCard', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('zapisuje draft baseUrl dla Ollamy', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)

    render(
      <SettingsProviderCard
        provider="ollama"
        state={{ kind: 'connected' }}
        baseUrl="http://localhost:11434"
        lastSync={null}
        onSave={onSave}
        onTest={vi.fn().mockResolvedValue({
          providerKey: 'ollama',
          status: 'connected',
          connectionState: 'connected',
          detail: 'OK',
          message: 'OK',
          healthStatus: 'healthy',
          latencyMs: 25,
          installedModels: ['qwen3:8b'],
          resetInSec: null,
          testedAt: '2026-05-08T12:00:00Z',
        })}
      />,
    )

    await user.clear(screen.getByLabelText('Ścieżka serwera Ollama'))
    await user.type(screen.getByLabelText('Ścieżka serwera Ollama'), 'http://127.0.0.1:11434')
    await user.click(screen.getByRole('button', { name: 'Zapisz' }))

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith({
        apiKey: undefined,
        baseUrl: 'http://127.0.0.1:11434',
      })
    })
  })

  it('uruchamia test providera z draft API key i pokazuje czytelny status', async () => {
    const user = userEvent.setup()
    const onTest = vi.fn().mockResolvedValue({
      providerKey: 'openai',
      status: 'connected',
      connectionState: 'connected',
      detail: 'Runtime odpowiedział poprawnie.',
      message: 'Runtime odpowiedział poprawnie.',
      healthStatus: 'healthy',
      latencyMs: 21,
      installedModels: [],
      resetInSec: null,
      testedAt: '2026-05-08T12:00:00Z',
    })

    render(
      <SettingsProviderCard
        provider="openai"
        state={{ kind: 'disconnected' }}
        apiKey="sk-************************WXYZ"
        baseUrl="https://api.openai.com"
        lastSync={null}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onTest={onTest}
      />,
    )

    await user.type(screen.getByLabelText('Klucz API OpenAI'), 'sk-test-secret-value')
    await user.click(screen.getByRole('button', { name: 'Test connection' }))

    await waitFor(() => {
      expect(onTest).toHaveBeenCalledWith({
        apiKey: 'sk-test-secret-value',
        baseUrl: 'https://api.openai.com',
      })
      expect(screen.getByText('ready')).toBeInTheDocument()
      expect(screen.getByText('Połączony')).toBeInTheDocument()
    })
  })

  it('pokazuje skonfigurowany provider bez live probe jako configured', () => {
    render(
      <SettingsProviderCard
        provider="anthropic"
        state={{ kind: 'configured', message: 'Sekret jest zapisany, ale provider nie przeszedl jeszcze live probe.' }}
        apiKey="sk-ant-************************WXYZ"
        baseUrl="https://api.anthropic.com"
        lastSync={null}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onTest={vi.fn()}
      />,
    )

    expect(screen.getByText('configured')).toBeInTheDocument()
    expect(screen.getByText('Sekret jest zapisany, ale provider nie przeszedl jeszcze live probe.')).toBeInTheDocument()
  })
})

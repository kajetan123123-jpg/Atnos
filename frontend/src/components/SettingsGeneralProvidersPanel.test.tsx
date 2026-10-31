import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import { resetAllStores } from '../test/resetStores'
import { SettingsGeneralProvidersPanel } from './SettingsGeneralProvidersPanel'

vi.mock('./SettingsProviderCard', () => ({
  SettingsProviderCard: ({ provider }: { provider: string }) => <div data-testid="provider-card">{provider}</div>,
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchProviderCards: vi.fn(),
    fetchRuntimeInventory: vi.fn(),
  }
})

describe('SettingsGeneralProvidersPanel', () => {
  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
    vi.mocked(api.fetchProviderCards).mockResolvedValue([])
    vi.mocked(api.fetchRuntimeInventory).mockResolvedValue({
      providers: [
        {
          provider_key: 'ollama-local', label: 'Ollama Local', kind: 'local_llm', local_first: true,
          is_enabled: true, has_runtime_adapter: true, status: 'ready', status_reason: 'Adapter ready.',
          health_status: 'healthy', health_reason: 'Ollama answered live probe.', installed_models: ['qwen3.5:9b'],
        },
        {
          provider_key: 'openai-cloud', label: 'OpenAI Cloud', kind: 'remote_llm', local_first: false,
          is_enabled: true, has_runtime_adapter: true, status: 'ready', status_reason: 'Adapter ready.',
          health_status: 'healthy', health_reason: 'Provider API answered.', installed_models: ['gpt-model'],
        },
      ],
      models: [
        {
          model_key: 'ollama-local/chat-default', provider_key: 'ollama-local', task: 'general_chat',
          is_enabled: true, is_default: true, status: 'ready', runtime_available: true,
          runtime_model_present: true, resolved_runtime_model_name: 'qwen3.5:9b', runtime_status_reason: 'Model present.',
        },
        {
          model_key: 'openai-cloud/chat-default', provider_key: 'openai-cloud', task: 'general_chat',
          is_enabled: true, is_default: true, status: 'ready', runtime_available: true,
          runtime_model_present: true, resolved_runtime_model_name: 'gpt-model', runtime_status_reason: 'API configured.',
        },
      ],
      tasks: [
        {
          task: 'general_chat', has_local_default: true, has_external_default: true, ready: true,
          default_local_model_key: 'ollama-local/chat-default', default_external_model_key: 'openai-cloud/chat-default',
        },
        {
          task: 'image_generation', has_local_default: false, has_external_default: false, ready: false,
          default_local_model_key: null, default_external_model_key: null,
        },
      ],
      summary: { ready_providers: 2, enabled_models: 2, tasks_ready: 1, has_local_chat: true, has_external_fallback: true },
      voice_stt: { effective_engine: 'browser-speech', status: 'ready', status_reason: 'Browser speech fallback.' },
    })
  })

  it('shows verified local capability, gated external fallback and a missing capability', async () => {
    render(<SettingsGeneralProvidersPanel />)

    await screen.findByText('Lokalnie: model lokalny wykryty')
    const panel = screen.getByTestId('runtime-capability-status')
    expect(panel).toHaveTextContent('Lokalnie: model lokalny wykryty')
    expect(panel).toHaveTextContent('serwer lokalny odpowiada')
    expect(panel).toHaveTextContent('Zewnętrzny fallback: API odpowiada; wybór trybu zewnętrznego może kosztować')
    expect(panel).toHaveTextContent('Lokalnie: brak przypisanego modelu')
    expect(panel).toHaveTextContent('tryb przeglądarkowy; lokalność niepotwierdzona')
    expect(panel).toHaveTextContent('Ollama Local · lokalny')
    expect(panel).toHaveTextContent('OpenAI Cloud · zewnętrzny')
  })
})

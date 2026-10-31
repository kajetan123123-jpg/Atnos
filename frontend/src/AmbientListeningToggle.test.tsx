import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsRoute from './routes/Settings'
import { StatusHUD } from './components/StatusHUD'
import * as api from './lib/api'
import { useSystemStore } from './stores/systemStore'
import { buildJarvisBrowserSessions, buildSettingsControlCenterPayload } from './test/factories'
import { resetAllStores } from './test/resetStores'

vi.mock('./components/SettingsProviderCard', () => ({
  SettingsProviderCard: ({ provider }: { provider: string }) => (
    <div data-testid="provider-card">
      {provider === 'ollama'
        ? 'Ollama'
        : provider === 'openai'
          ? 'OpenAI'
          : provider === 'anthropic'
            ? 'Anthropic'
          : provider === 'gemini'
            ? 'Gemini'
              : provider}
    </div>
  ),
}))

vi.mock('./lib/api', async () => {
  const actual = await vi.importActual<typeof import('./lib/api')>('./lib/api')
  return {
    ...actual,
    activateOmegaProtocol: vi.fn(),
    createSettingsRole: vi.fn(),
    createSettingsVoice: vi.fn(),
    fetchJarvisBrowserSessions: vi.fn(),
    fetchSettingsControlCenter: vi.fn(),
    testProviderCard: vi.fn(),
    updateProviderCard: vi.fn(),
    updateSettingsObsidian: vi.fn(),
    updateSettingsPlugin: vi.fn(),
    updateSettingsPolicy: vi.fn(),
    updateSettingsProfile: vi.fn(),
    updateSettingsRole: vi.fn(),
    updateSettingsRuntime: vi.fn(),
    updateSettingsStyle: vi.fn(),
    updateSettingsVoice: vi.fn(),
    updateTrustedAutoSchedulerSettings: vi.fn(),
    upsertTrustedAutoAccount: vi.fn(),
  }
})

describe('AmbientListeningToggle', () => {
  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
    vi.mocked(api.fetchJarvisBrowserSessions).mockResolvedValue(buildJarvisBrowserSessions())
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: vi.fn().mockResolvedValue([
          { kind: 'audioinput', deviceId: 'mic-1', label: 'Studio Mic' },
        ]),
      },
    })
  })

  it('renders ambient controls and saves the selected mic device', async () => {
    const payload = buildSettingsControlCenterPayload()
    vi.mocked(api.fetchSettingsControlCenter).mockResolvedValue(payload)
    vi.mocked(api.updateSettingsRuntime).mockResolvedValue(payload)
    vi.mocked(api.updateTrustedAutoSchedulerSettings).mockResolvedValue(payload.trustedAuto.runtimeControls)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    expect(await screen.findByText(/^Ambient listening$/i)).toBeInTheDocument()
    expect(
      screen.getByText(/Ambient listening wymaga lokalnego STT i poprawnej sciezki modelu/i),
    ).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Studio Mic' })).toBeInTheDocument()

    const ambientCard = screen
      .getByText(/^Ambient listening$/i)
      .closest('div')
      ?.parentElement?.parentElement
    expect(ambientCard).not.toBeNull()
    const ambientCheckboxes = within(ambientCard as HTMLElement).getAllByRole('checkbox')
    await user.click(ambientCheckboxes[0])
    await user.selectOptions(screen.getByLabelText(/Mikrofon \/ device/i), 'label:Studio Mic')
    await user.click(screen.getByLabelText(/Ambient context aware/i))
    await user.click(screen.getByRole('button', { name: /Zapisz runtime/i }))

    await waitFor(() => {
      expect(api.updateSettingsRuntime).toHaveBeenCalledWith(
        expect.objectContaining({
          ambientListeningEnabled: true,
          ambientMicDevice: 'label:Studio Mic',
          ambientContextEnabled: true,
        }),
      )
    })
  })

  it('nie pokazuje juz ambient indicatorow w HUD po uproszczeniu shellu', () => {
    act(() => {
      useSystemStore.getState().setStage('idle')
      useSystemStore.getState().setAmbientState({
        ambientListeningEnabled: true,
        ambientWakeWord: 'Atnos',
        ambientMuted: false,
        ambientContextEnabled: true,
        voiceRepliesEnabled: false,
        voiceReplyVoiceId: null,
        listeningAvailable: true,
        listeningReason: '',
        contextSnapshot: null,
      })
    })

    render(<StatusHUD />)

    expect(screen.getByText('BEZCZYNNY')).toBeInTheDocument()
    expect(screen.queryByText('LISTENING')).not.toBeInTheDocument()
    expect(screen.queryByText('Aware')).not.toBeInTheDocument()
  })
})

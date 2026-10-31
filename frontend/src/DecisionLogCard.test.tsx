import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsRoute from './routes/Settings'
import * as api from './lib/api'
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
    upsertTrustedAutoAccount: vi.fn(),
  }
})

describe('DecisionLogCard', () => {
  beforeEach(() => {
    resetAllStores()
    vi.clearAllMocks()
    vi.mocked(api.fetchJarvisBrowserSessions).mockResolvedValue(buildJarvisBrowserSessions())
  })

  it('filters active, resolved and overdue decision log entries', async () => {
    const payload = buildSettingsControlCenterPayload()
    payload.decisionLog = [
      {
        decisionId: 1,
        conversationId: 101,
        projectId: 1,
        kind: 'commitment',
        summary: 'Dowiezc raport projektu.',
        madeAt: '2026-05-08T10:00:00Z',
        status: 'active',
        dueAt: '2000-01-01T00:00:00Z',
        resolvedAt: null,
        resolutionSummary: null,
        evidenceMessageIds: [10],
      },
      {
        decisionId: 2,
        conversationId: 101,
        projectId: 1,
        kind: 'decision',
        summary: 'Produkt zostaje w modelu Chat + Settings.',
        madeAt: '2026-05-07T09:00:00Z',
        status: 'resolved',
        dueAt: null,
        resolvedAt: '2026-05-07T11:00:00Z',
        resolutionSummary: 'Wdrozone.',
        evidenceMessageIds: [20],
      },
    ]
    vi.mocked(api.fetchSettingsControlCenter).mockResolvedValue(payload)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    expect(await screen.findByText(/Dziennik decyzji/i)).toBeInTheDocument()
    expect(screen.getByText(/Dowiezc raport projektu/i)).toBeInTheDocument()
    expect(screen.queryByText(/Produkt zostaje w modelu Chat \+ Settings/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Resolved$/i }))
    expect(screen.getByText(/Produkt zostaje w modelu Chat \+ Settings/i)).toBeInTheDocument()
    expect(screen.queryByText(/Dowiezc raport projektu/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Overdue$/i }))
    expect(screen.getByText(/Dowiezc raport projektu/i)).toBeInTheDocument()
    expect(screen.queryByText(/Produkt zostaje w modelu Chat \+ Settings/i)).not.toBeInTheDocument()
  })
})

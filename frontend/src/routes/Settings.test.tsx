import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsRoute from './Settings'
import * as api from '../lib/api'
import { useChatStore } from '../stores/chatStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import {
  buildJarvisBrowserSessions,
  buildOperationalMemoryPromotionPreview,
  buildOperationalMemorySnapshot,
  buildSettingsControlCenterPayload,
} from '../test/factories'
import { resetAllStores } from '../test/resetStores'

vi.mock('../components/SettingsProviderCard', () => ({
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

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    confirmJarvisBrowserSessionManualLogin: vi.fn(),
    disconnectOAuthConnection: vi.fn(),
    fetchJarvisBrowserSessions: vi.fn(),
    fetchRuntimeInventory: vi.fn(),
    fetchOperationalMemorySnapshot: vi.fn(),
    previewOperationalMemoryPromotion: vi.fn(),
    promoteOperationalMemorySelectedNote: vi.fn(),
    fetchSettingsControlCenter: vi.fn(),
    focusLocalHostDesktopApp: vi.fn(),
    openLocalHostBrowserUrl: vi.fn(),
    openLocalHostDesktopApp: vi.fn(),
    probeGmailConnector: vi.fn(),
    probeGitHubConnector: vi.fn(),
    probeGoogleCalendarConnector: vi.fn(),
    probeTrustedAutoAccounts: vi.fn(),
    probeYouTubeConnector: vi.fn(),
    startJarvisBrowserSessionManualLogin: vi.fn(),
    startJarvisBrowserSessionManualLoginForUrl: vi.fn(),
    startOAuthConnection: vi.fn(),
    testProviderCard: vi.fn(),
    updateProviderCard: vi.fn(),
    updateSettingsRuntime: vi.fn(),
    upsertTrustedAutoAccount: vi.fn(),
  }
})

function findCardByTitle(
  title: string,
  predicate: (card: HTMLElement) => boolean = () => true,
): HTMLElement {
  const heading = screen
    .getAllByText(title, { selector: 'div.font-display, div.type-body, h2' })
    .find((node) => {
      const card = node.closest('.rounded-xl, .rounded-lg, .shell-panel')
      return card instanceof HTMLElement && predicate(card)
    })
  expect(heading).not.toBeUndefined()
  if (!heading) {
    throw new Error(`Card "${title}" not found`)
  }
  const card = heading.closest('.rounded-xl, .rounded-lg, .shell-panel')
  expect(card).not.toBeNull()
  return card as HTMLElement
}

function findNewTrustedAccountCard(): HTMLElement {
  return screen.getByTestId('trusted-account-editor-new')
}

function getInputByPlaceholder(card: HTMLElement, placeholder: string): HTMLInputElement {
  const input = card.querySelector(`input[placeholder="${placeholder}"]`)
  expect(input).not.toBeNull()
  return input as HTMLInputElement
}

function getSelectAt(card: HTMLElement, index: number): HTMLSelectElement {
  const select = card.querySelectorAll('select').item(index)
  expect(select).not.toBeNull()
  return select as HTMLSelectElement
}

describe('SettingsRoute', () => {
  beforeEach(() => {
    resetAllStores()
    window.localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(api.fetchSettingsControlCenter).mockResolvedValue(buildSettingsControlCenterPayload())
    vi.mocked(api.fetchJarvisBrowserSessions).mockResolvedValue(buildJarvisBrowserSessions())
    vi.mocked(api.fetchRuntimeInventory).mockResolvedValue({
      providers: [
        {
          provider_key: 'ollama-local', label: 'Ollama Local', kind: 'local_llm', local_first: true,
          is_enabled: true, has_runtime_adapter: true, status: 'ready', status_reason: 'Adapter ready.',
          health_status: 'healthy', health_reason: 'Ollama responded.', installed_models: ['qwen3.5:9b'],
        },
        {
          provider_key: 'openai-cloud', label: 'OpenAI Cloud', kind: 'remote_llm', local_first: false,
          is_enabled: true, has_runtime_adapter: true, status: 'ready', status_reason: 'Adapter ready.',
          health_status: 'healthy', health_reason: 'API responded.', installed_models: ['gpt-model'],
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
    vi.mocked(api.openLocalHostDesktopApp).mockResolvedValue({
      actionKind: 'desktop_app_open',
      command: 'start Kimi',
      exitCode: 0,
      stdout: 'opened',
      stderr: '',
      workdir: 'F:\\Apps\\Atnos',
      elevated: false,
      startedAt: '2026-05-10T15:00:00Z',
      finishedAt: '2026-05-10T15:00:01Z',
    })
    vi.mocked(api.focusLocalHostDesktopApp).mockResolvedValue({
      actionKind: 'desktop_app_focus',
      command: 'focus Kimi',
      exitCode: 0,
      stdout: 'focused',
      stderr: '',
      workdir: 'F:\\Apps\\Atnos',
      elevated: false,
      startedAt: '2026-05-10T15:00:00Z',
      finishedAt: '2026-05-10T15:00:01Z',
    })
    vi.mocked(api.fetchOperationalMemorySnapshot).mockResolvedValue(buildOperationalMemorySnapshot())
    vi.mocked(api.previewOperationalMemoryPromotion).mockResolvedValue(
      buildOperationalMemoryPromotionPreview(),
    )
  })

  it('loads the simplified settings surface and hydrates runtime state', async () => {
    render(<SettingsRoute />)

    expect(await screen.findByText('Konta i loginy dla Atnosa')).toBeInTheDocument()
    expect(screen.getByText('Dodaj przez URL aplikacji')).toBeInTheDocument()
    expect(screen.getByText('Kanaly i narzedzia contentowe')).toBeInTheDocument()
    expect(screen.getByText('Browserowe sesje AI')).toBeInTheDocument()
    expect(screen.getAllByText('Runtime').length).toBeGreaterThan(0)
    expect(screen.getByText('Operational Memory')).toBeInTheDocument()
    expect(screen.getByText('AI & Tools Center')).toBeInTheDocument()
    expect(screen.getByText('Modele AI')).toBeInTheDocument()
    expect(screen.getByText('OAuth connectory')).toBeInTheDocument()
    expect(
      screen.getByText(/Wybierz aktywny projekt w Chacie/i),
    ).toBeInTheDocument()
    expect(screen.getAllByTestId('provider-card').map((node) => node.textContent)).toContain('Ollama')
    expect(useSystemStore.getState().runtimeSummary?.providersReady).toBe(1)
    expect(useSystemStore.getState().capabilities?.obsidianExportEnabled).toBe(true)
  })

  it('separates local model readiness from external fallback and missing capabilities', async () => {
    render(<SettingsRoute />)

    await waitFor(() => expect(api.fetchRuntimeInventory).toHaveBeenCalled())
    expect(await screen.findByText('Rzeczywisty status możliwości')).toBeInTheDocument()
    expect(await screen.findByText('qwen3.5:9b')).toBeInTheDocument()
    const panel = screen.getByTestId('runtime-capability-status')
    expect(panel).toHaveTextContent('Lokalnie: działa lokalnie')
    expect(panel).toHaveTextContent(/Zewnętrzny fallback: API odpowiada/)
    expect(panel).toHaveTextContent('Lokalnie: brak modelu')
    expect(document.body.textContent).toContain('Usługi zewnętrzne wymagają jawnego trybu i mogą generować koszt.')
  })

  it('renders detected local desktop apps and routes open actions through local-host API', async () => {
    const payload = buildSettingsControlCenterPayload()
    payload.localHost.desktopApps = [
      {
        appKey: 'kimi',
        label: 'Kimi',
        category: 'browser_ai',
        installed: true,
        running: false,
        windowVisible: false,
        launchSupported: true,
        focusSupported: true,
        preferredEntryPath: 'C:\\Users\\localuser\\AppData\\Local\\Programs\\kimi-desktop\\Kimi.exe',
        preferredEntryKind: 'path',
        runningProcesses: [],
        visibleWindowTitles: [],
        notes: ['Detected from desktop app catalog.'],
      },
      {
        appKey: 'comfy-desktop',
        label: 'Comfy Desktop',
        category: 'creative_runtime',
        installed: true,
        running: true,
        windowVisible: true,
        launchSupported: true,
        focusSupported: true,
        preferredEntryPath: 'F:\\Apps\\ComfyUI-Desktop\\Comfy Desktop\\Comfy Desktop.exe',
        preferredEntryKind: 'path',
        runningProcesses: ['Comfy Desktop'],
        visibleWindowTitles: ['Comfy Desktop'],
        notes: [],
      },
    ]
    vi.mocked(api.fetchSettingsControlCenter).mockResolvedValue(payload)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    await screen.findByText('Konta i loginy dla Atnosa')
    const appsPanel = screen.getByText('Aplikacje PC dla Atnosa')
    const panel = appsPanel.closest('.rounded-lg')
    expect(panel).not.toBeNull()
    const localAppsPanel = panel as HTMLElement
    await waitFor(() => {
      expect(within(localAppsPanel).getByText('Kimi')).toBeInTheDocument()
      expect(within(localAppsPanel).getAllByText('Comfy Desktop').length).toBeGreaterThan(0)
      expect(within(localAppsPanel).getByText(/Kimi\.exe/)).toBeInTheDocument()
    })

    const kimiCard = within(localAppsPanel).getByText('kimi').closest('.rounded-lg')
    expect(kimiCard).not.toBeNull()
    await user.click(within(kimiCard as HTMLElement).getByRole('button', { name: 'Otworz' }))

    expect(api.openLocalHostDesktopApp).toHaveBeenCalledWith('kimi')
  })

  it('saves the public upload runtime switch explicitly', async () => {
    const updatedPayload = buildSettingsControlCenterPayload()
    updatedPayload.runtime.publicUploadEnabled = true
    vi.mocked(api.updateSettingsRuntime).mockResolvedValue(updatedPayload)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    const runtimeCard = await waitFor(() => findCardByTitle('Runtime'))
    await user.click(within(runtimeCard).getByLabelText('Publiczny upload'))
    await user.click(within(runtimeCard).getByRole('button', { name: /Zapisz podstawowe/i }))

    await waitFor(() => {
      expect(api.updateSettingsRuntime).toHaveBeenCalledWith(
        expect.objectContaining({
          publicUploadEnabled: true,
        }),
      )
    })
  })

  it('renders the operational memory snapshot for the active project context', async () => {
    useChatStore.setState({
      projects: [
        {
          id: 1,
          name: 'Projekt bazowy',
          slug: 'projekt-bazowy',
          description: 'Główny projekt testowy',
          summary: 'Opis projektu testowego',
          status: 'active',
          isPinned: false,
          providerPosture: 'prefer-local',
          personaProfile: 'strategist',
        },
      ],
      selectedProjectId: 1,
    })

    render(<SettingsRoute />)

    expect(await screen.findByText('Operational Memory')).toBeInTheDocument()
    expect(await screen.findByText('Domknac selected-note triage')).toBeInTheDocument()
    expect(api.fetchOperationalMemorySnapshot).toHaveBeenCalledWith({ projectId: 1 })
  })

  it('detects a pasted app URL and starts the same browser login flow as AI sessions', async () => {
    vi.mocked(api.startJarvisBrowserSessionManualLoginForUrl).mockResolvedValue({
      ok: true,
      session: {
        ...buildJarvisBrowserSessions()[0],
        service: 'claude',
        displayName: 'Claude',
        loginUrl: 'https://claude.ai/login',
        validationUrl: 'https://claude.ai/new',
        pendingManualLogin: true,
        loginWindowOpen: true,
        statusReason: 'Okno logowania jest otwarte.',
      },
    })

    const user = userEvent.setup()
    render(<SettingsRoute />)

    const newAccountCard = await waitFor(() => findNewTrustedAccountCard())
    await user.type(getInputByPlaceholder(newAccountCard, 'https://claude.ai'), 'https://claude.ai')
    await user.click(within(newAccountCard).getByRole('button', { name: /Wykryj i ustaw/i }))

    expect(getInputByPlaceholder(newAccountCard, 'Np. Claude Pro').value).toBe('Claude')
    expect(getSelectAt(newAccountCard, 0).value).toBe('claude')
    expect(within(newAccountCard).getByDisplayValue('claude_ui_bridge')).toBeInTheDocument()

    const browserSessionBox = within(newAccountCard).getByText('Browser session').closest('.rounded-lg')
    expect(browserSessionBox).not.toBeNull()
    await user.click(within(browserSessionBox as HTMLElement).getByRole('button', { name: /Zaloguj browserowo/i }))

    await waitFor(() => {
      expect(api.startJarvisBrowserSessionManualLoginForUrl).toHaveBeenCalledWith({
        service: 'claude',
        displayName: 'Claude',
        loginUrl: 'https://claude.ai/login',
        validationUrl: 'https://claude.ai/new',
      })
    })
  })

  it('prefills the Atnos int.pl mailbox without storing a password from chat', async () => {
    const user = userEvent.setup()
    render(<SettingsRoute />)

    const newAccountCard = await waitFor(() => findNewTrustedAccountCard())
    await user.type(getInputByPlaceholder(newAccountCard, 'https://claude.ai'), 'https://int.pl')
    await user.click(within(newAccountCard).getByRole('button', { name: /Wykryj i ustaw/i }))

    expect(getInputByPlaceholder(newAccountCard, 'Np. Claude Pro').value).toBe('INT mail')
    expect(getSelectAt(newAccountCard, 0).value).toBe('int-mail')
    expect(screen.getByDisplayValue('int_mail_browser_session')).toBeInTheDocument()

    await user.click(within(newAccountCard).getByRole('button', { name: /Ustaw account-c5034bf4@example\.invalid/i }))

    expect(getInputByPlaceholder(newAccountCard, 'Np. Claude Pro').value).toBe('Atnos mailbox (int.pl)')
    expect(getInputByPlaceholder(newAccountCard, 'account-77a076eb@example.invalid').value).toBe('account-c5034bf4@example.invalid')
    expect(screen.getAllByDisplayValue('account-c5034bf4@example.invalid')).toHaveLength(2)
    expect(getInputByPlaceholder(newAccountCard, 'Opcjonalnie, Vault').value).toBe('')
    expect(getInputByPlaceholder(newAccountCard, 'token / API key / cookie').value).toBe('')
  })

  it('saves a URL-derived trusted account with browser mapping and optional vault credentials', async () => {
    const payload = buildSettingsControlCenterPayload()
    vi.mocked(api.upsertTrustedAutoAccount).mockResolvedValue(payload.trustedAuto.accounts)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    const newAccountCard = await waitFor(() => findNewTrustedAccountCard())
    await user.type(getInputByPlaceholder(newAccountCard, 'https://claude.ai'), 'https://zebracat.ai')
    await user.click(within(newAccountCard).getByRole('button', { name: /Wykryj i ustaw/i }))
    await user.type(getInputByPlaceholder(newAccountCard, 'account-77a076eb@example.invalid'), 'owner@example.com')
    await user.type(getInputByPlaceholder(newAccountCard, 'Opcjonalnie, Vault'), 'tajnehaslo123')
    await user.click(within(newAccountCard).getByRole('button', { name: /^Dodaj konto$/i }))

    await waitFor(() => {
      expect(api.upsertTrustedAutoAccount).toHaveBeenCalledWith(
        expect.objectContaining({
          label: 'Zebracat',
          providerKey: 'zebracat',
          accountIdentifier: 'owner@example.com',
          secretType: 'session_cookie_later',
          linkedIntegrationKey: 'zebracat_api',
          passwordValue: 'tajnehaslo123',
          connectorMappingJson: expect.objectContaining({
            browser_session_service: 'zebracat',
            login_url: 'https://www.zebracat.ai/',
            auto_send: false,
          }),
        }),
      )
    })
  })

  it('saves a real account from the simple form with purpose, email, password and login URL', async () => {
    const payload = buildSettingsControlCenterPayload()
    vi.mocked(api.upsertTrustedAutoAccount).mockResolvedValue(payload.trustedAuto.accounts)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    const newAccountCard = await waitFor(() => findNewTrustedAccountCard())
    fireEvent.change(getInputByPlaceholder(newAccountCard, 'Np. Claude Pro'), { target: { value: 'YT Finanse' } })
    fireEvent.change(getInputByPlaceholder(newAccountCard, 'Np. upload shortow / poczta / narzedzie AI'), {
      target: { value: 'upload shortow na kanal finansowy' },
    })
    fireEvent.change(getInputByPlaceholder(newAccountCard, 'account-77a076eb@example.invalid'), { target: { value: 'owner@example.com' } })
    fireEvent.change(getInputByPlaceholder(newAccountCard, 'Opcjonalnie, Vault'), { target: { value: 'tajnehaslo123' } })
    fireEvent.change(getInputByPlaceholder(newAccountCard, 'https://studio.youtube.com/'), {
      target: { value: 'https://studio.youtube.com/' },
    })
    await user.click(within(newAccountCard).getByRole('button', { name: /^Dodaj konto$/i }))

    await waitFor(() => {
      expect(api.upsertTrustedAutoAccount).toHaveBeenCalledWith(
        expect.objectContaining({
          label: 'YT Finanse',
          providerKey: 'youtube',
          accountIdentifier: 'owner@example.com',
          loginIdentifier: 'owner@example.com',
          passwordValue: 'tajnehaslo123',
          linkedIntegrationKey: 'youtube',
          connectorMappingJson: expect.objectContaining({
            purpose: 'upload shortow na kanal finansowy',
            browser_session_service: 'youtube',
            login_url: 'https://studio.youtube.com/',
            auto_send: false,
          }),
          notes: expect.stringContaining('Do czego: upload shortow na kanal finansowy'),
        }),
      )
    })
  })

  it('wires content-tool cards to browser login, OAuth disconnect and connector probes', async () => {
    vi.mocked(api.startJarvisBrowserSessionManualLoginForUrl).mockResolvedValue({
      ok: true,
      session: {
        ...buildJarvisBrowserSessions()[0],
        service: 'youtube',
        displayName: 'YouTube Studio',
        loginWindowOpen: true,
        pendingManualLogin: true,
      },
    })
    vi.mocked(api.disconnectOAuthConnection).mockResolvedValue({
      ok: true,
      connectionId: 101,
      status: 'disconnected',
    })
    vi.mocked(api.probeGitHubConnector).mockResolvedValue({ probe_status: 'ok' })

    const user = userEvent.setup()
    render(<SettingsRoute />)

    const youtubeCard = await waitFor(() => findCardByTitle('YouTube'))
    await user.click(within(youtubeCard).getByRole('button', { name: /Zaloguj browserowo/i }))
    await waitFor(() => {
      expect(api.startJarvisBrowserSessionManualLoginForUrl).toHaveBeenCalledWith(
        expect.objectContaining({
          service: 'youtube',
          displayName: 'YouTube Studio',
          loginUrl: 'https://studio.youtube.com/',
        }),
      )
    })

    await user.click(within(youtubeCard).getByRole('button', { name: /Odlacz/i }))
    await waitFor(() => {
      expect(api.disconnectOAuthConnection).toHaveBeenCalledWith(101)
    })

    const githubCard = findCardByTitle('GitHub', (card) => within(card).queryByRole('button', { name: /Test connection/i }) != null)
    await user.click(within(githubCard).getByRole('button', { name: /Test connection/i }))
    await waitFor(() => {
      expect(api.probeGitHubConnector).toHaveBeenCalled()
    })
  })

  it('opens OAuth only through the official connector path when it is configured', async () => {
    const payload = buildSettingsControlCenterPayload()
    payload.oauthConnectors = payload.oauthConnectors.map((connector) =>
      connector.connectorKey === 'github'
        ? { ...connector, enabled: true, configured: true, connectionId: null, status: 'available' }
        : connector,
    )
    vi.mocked(api.fetchSettingsControlCenter).mockResolvedValue(payload)
    vi.mocked(api.startOAuthConnection).mockResolvedValue({
      authorizationUrl: 'http://127.0.0.1:8000/oauth/github/callback?code=fake&state=fake',
      connectorKey: 'github',
      status: 'pending',
      scopes: ['read:user'],
      connectionId: 501,
    })
    vi.mocked(api.openLocalHostBrowserUrl).mockResolvedValue({
      actionKind: 'browser_open',
      command: "Start-Process 'http://127.0.0.1:8000/oauth/github/callback?code=fake&state=fake'",
      exitCode: 0,
      stdout: '',
      stderr: '',
      workdir: 'F:\\Apps\\Atnos',
      elevated: true,
      startedAt: '2026-05-22T08:00:00Z',
      finishedAt: '2026-05-22T08:00:01Z',
    })
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    const githubCards = await screen.findAllByText('GitHub', { selector: 'div.font-display' })
    const oauthCard =
      githubCards
        .map((node) => node.closest('.rounded-xl'))
        .find(
          (card): card is HTMLElement =>
            card instanceof HTMLElement &&
            within(card).queryByRole('button', { name: /Polacz OAuth/i }) != null,
        ) ?? null
    expect(oauthCard).not.toBeNull()

    await user.click(within(oauthCard as HTMLElement).getByRole('button', { name: /Polacz OAuth/i }))
    await waitFor(() => {
      expect(api.startOAuthConnection).toHaveBeenCalledWith('github')
      expect(api.openLocalHostBrowserUrl).toHaveBeenCalledWith(
        'http://127.0.0.1:8000/oauth/github/callback?code=fake&state=fake',
      )
      expect(openSpy).not.toHaveBeenCalled()
    })

    openSpy.mockRestore()
  })

  it('falls back to a browser popup when local host browser opening is unavailable', async () => {
    const payload = buildSettingsControlCenterPayload()
    payload.localHost = {
      ...payload.localHost,
      browserOpenAvailable: false,
      executionEnabled: false,
    }
    payload.oauthConnectors = payload.oauthConnectors.map((connector) =>
      connector.connectorKey === 'github'
        ? { ...connector, enabled: true, configured: true, connectionId: null, status: 'available' }
        : connector,
    )
    vi.mocked(api.fetchSettingsControlCenter).mockResolvedValue(payload)
    vi.mocked(api.startOAuthConnection).mockResolvedValue({
      authorizationUrl: 'http://127.0.0.1:8000/oauth/github/callback?code=fake&state=fake',
      connectorKey: 'github',
      status: 'pending',
      scopes: ['read:user'],
      connectionId: 501,
    })
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    const githubCards = await screen.findAllByText('GitHub', { selector: 'div.font-display' })
    const oauthCard =
      githubCards
        .map((node) => node.closest('.rounded-xl'))
        .find(
          (card): card is HTMLElement =>
            card instanceof HTMLElement &&
            within(card).queryByRole('button', { name: /Polacz OAuth/i }) != null,
        ) ?? null
    expect(oauthCard).not.toBeNull()

    await user.click(within(oauthCard as HTMLElement).getByRole('button', { name: /Polacz OAuth/i }))
    await waitFor(() => {
      expect(api.startOAuthConnection).toHaveBeenCalledWith('github')
      expect(api.openLocalHostBrowserUrl).not.toHaveBeenCalled()
      expect(openSpy).toHaveBeenCalledWith(
        'http://127.0.0.1:8000/oauth/github/callback?code=fake&state=fake',
        '_blank',
        'popup,width=720,height=820',
      )
    })

    openSpy.mockRestore()
  })


  it('resyncs URL and mapping fields when the connector is changed manually', async () => {
    const payload = buildSettingsControlCenterPayload()
    payload.trustedAuto.accounts = [
      {
        accountId: 303,
        label: 'ATNOS OnlyFans',
        providerKey: 'claude',
        accountIdentifier: 'account-c5034bf4@example.invalid',
        secretType: 'password',
        status: 'configured',
        hasSecret: false,
        maskedSecretState: 'vault_optional',
        linkedProjectId: null,
        linkedIntegrationKey: 'claude_ui_bridge',
        allowedCapabilities: ['trusted_login', 'browser_workflow'],
        trustGrants: [],
        credentialProfile: {
          credentialProfileId: 403,
          loginIdentifier: 'account-c5034bf4@example.invalid',
          hasPassword: false,
          hasTotp: false,
          hasBackupCodes: false,
          recoveryEmail: null,
          hasRecoveryEmailSecret: false,
          connectorMappingJson: {
            browser_session_service: 'claude',
            login_url: 'https://claude.ai/login',
            validation_url: 'https://claude.ai/new',
          },
          status: 'configured',
          notes: null,
        },
      },
    ]
    vi.mocked(api.fetchSettingsControlCenter).mockResolvedValue(payload)

    const user = userEvent.setup()
    render(<SettingsRoute />)

    const accountCard = await waitFor(() => findCardByTitle('ATNOS OnlyFans'))
    const connectorSelect = within(accountCard).getAllByRole('combobox')[0]
    expect(within(accountCard).getByLabelText(/URL aplikacji/i)).toHaveValue('https://claude.ai/login')
    expect(within(accountCard).getByDisplayValue('claude_ui_bridge')).toBeInTheDocument()

    await user.selectOptions(connectorSelect, 'youtube')

    expect(within(accountCard).getByLabelText(/URL aplikacji/i)).toHaveValue('https://studio.youtube.com/')
    expect(within(accountCard).getByLabelText(/Integration key/i)).toHaveValue('youtube')
    expect(within(accountCard).queryByDisplayValue('claude_ui_bridge')).not.toBeInTheDocument()
  })

  it('shows the saved login URL per trusted account instead of reusing a stale helper URL', async () => {
    const payload = buildSettingsControlCenterPayload()
    payload.trustedAuto.accounts = [
      {
        accountId: 301,
        label: 'RunwayML [account-c5034bf4@example.invalid]',
        providerKey: 'runway',
        accountIdentifier: 'account-c5034bf4@example.invalid',
        secretType: 'password',
        status: 'metadata_only',
        hasSecret: false,
        maskedSecretState: 'vault_optional',
        linkedProjectId: null,
        linkedIntegrationKey: 'runway_browser_recipe',
        allowedCapabilities: ['trusted_login', 'browser_workflow'],
        trustGrants: [],
        credentialProfile: {
          credentialProfileId: 401,
          loginIdentifier: 'account-c5034bf4@example.invalid',
          hasPassword: false,
          hasTotp: false,
          hasBackupCodes: false,
          recoveryEmail: null,
          hasRecoveryEmailSecret: false,
          connectorMappingJson: {
            browser_session_service: 'runway',
            login_url: 'https://app.runwayml.com/login',
            validation_url: 'https://app.runwayml.com/',
          },
          status: 'configured',
          notes: null,
        },
      },
      {
        accountId: 302,
        label: 'Submagic [account-c5034bf4@example.invalid]',
        providerKey: 'submagic',
        accountIdentifier: 'account-c5034bf4@example.invalid',
        secretType: 'password',
        status: 'metadata_only',
        hasSecret: false,
        maskedSecretState: 'vault_optional',
        linkedProjectId: null,
        linkedIntegrationKey: 'submagic_browser_recipe',
        allowedCapabilities: ['trusted_login', 'browser_workflow'],
        trustGrants: [],
        credentialProfile: {
          credentialProfileId: 402,
          loginIdentifier: 'account-c5034bf4@example.invalid',
          hasPassword: false,
          hasTotp: false,
          hasBackupCodes: false,
          recoveryEmail: null,
          hasRecoveryEmailSecret: false,
          connectorMappingJson: {
            browser_session_service: 'submagic',
            login_url: 'https://app.submagic.co/',
            validation_url: 'https://app.submagic.co/',
          },
          status: 'configured',
          notes: null,
        },
      },
    ]
    vi.mocked(api.fetchSettingsControlCenter).mockResolvedValue(payload)

    render(<SettingsRoute />)

    const runwayCard = await waitFor(() => findCardByTitle('RunwayML [account-c5034bf4@example.invalid]'))
    const submagicCard = await waitFor(() => findCardByTitle('Submagic [account-c5034bf4@example.invalid]'))

    expect(within(runwayCard).getByLabelText(/URL aplikacji/i)).toHaveValue('https://app.runwayml.com/login')
    expect(within(submagicCard).getByLabelText(/URL aplikacji/i)).toHaveValue('https://app.submagic.co/')
  })

  it('keeps direct browser session controls for saved AI sessions', async () => {
    const sessions = buildJarvisBrowserSessions()
    const pending = [{ ...sessions[0], pendingManualLogin: true, loginWindowOpen: true }, sessions[1]]
    const confirmed = [{ ...sessions[0], ready: true, hasStorageState: true, pendingManualLogin: false }, sessions[1]]
    vi.mocked(api.fetchJarvisBrowserSessions)
      .mockResolvedValueOnce(sessions)
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(confirmed)
    vi.mocked(api.startJarvisBrowserSessionManualLogin).mockResolvedValue({ ok: true, session: pending[0] })
    vi.mocked(api.confirmJarvisBrowserSessionManualLogin).mockResolvedValue({ ok: true, session: confirmed[0] })

    const user = userEvent.setup()
    render(<SettingsRoute />)

    expect(await screen.findByText('Browserowe sesje AI')).toBeInTheDocument()
    const ideogramCard = findCardByTitle('Ideogram')
    await user.click(within(ideogramCard).getByRole('button', { name: /^Zaloguj$/i }))
    await waitFor(() => {
      expect(api.startJarvisBrowserSessionManualLogin).toHaveBeenCalledWith('ideogram')
    })

    await user.click(within(ideogramCard).getByRole('button', { name: /Potwierdz zapis/i }))
    await waitFor(() => {
      expect(api.confirmJarvisBrowserSessionManualLogin).toHaveBeenCalledWith('ideogram')
    })
  })

  it('runs trusted account probes and renders sequential feedback', async () => {
    vi.mocked(api.probeTrustedAutoAccounts).mockResolvedValue([
      {
        sequence: 1,
        accountId: 11,
        label: 'Claude Pro',
        providerKey: 'claude',
        accountIdentifier: 'owner@example.com',
        url: 'https://claude.ai/login',
        hasSecret: true,
        grantCount: 1,
        checkedAt: '2026-05-18T10:00:00Z',
        status: 'ok',
        httpStatus: 200,
        reason: 'Strona odpowiada HTTP 200.',
      },
    ])

    const user = userEvent.setup()
    render(<SettingsRoute />)

    await user.click(await screen.findByRole('button', { name: /Sprawdz zapisane strony/i }))

    await waitFor(() => {
      expect(api.probeTrustedAutoAccounts).toHaveBeenCalled()
    })
    expect(await screen.findByText(/1\. Claude Pro/i)).toBeInTheDocument()
    expect(useToastStore.getState().toasts.at(-1)?.title).toBe('Sprawdzilem zapisane strony')
  })
})

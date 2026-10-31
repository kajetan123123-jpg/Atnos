import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import type { ProjectPlatformBinding } from '../lib/api'
import { ProjectPlatformBindingsPanel } from './ProjectPlatformBindingsPanel'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchProjectPlatformBindings: vi.fn(),
    fetchAccountCredentials: vi.fn(),
    createProjectPlatformBinding: vi.fn(),
    updateProjectPlatformBinding: vi.fn(),
    disableProjectPlatformBinding: vi.fn(),
    verifyProjectPlatformBinding: vi.fn(),
  }
})

function binding(overrides: Partial<ProjectPlatformBinding> = {}): ProjectPlatformBinding {
  return {
    id: 41,
    projectId: 17,
    integrationKey: 'youtube-studio',
    customIntegrationId: null,
    accountId: 65,
    accountSummary: {
      id: 65,
      label: 'Wealth Blueprint',
      providerKey: 'youtube',
      accountIdentifier: 'account-c5034bf4@example.invalid',
      status: 'active',
      riskLevel: 'high',
      linkedIntegrationKey: 'youtube',
    },
    resourceType: 'channel',
    resourceExternalId: 'UC123456789',
    resourceLabel: 'Wealth Blueprint',
    bindingRole: 'primary',
    environment: 'production',
    confirmationPolicy: 'supervised',
    delegationPolicy: 'project_only',
    allowSharedAccount: false,
    permissionScopes: ['youtube.readonly', 'youtube.upload'],
    connectionStatus: 'connected',
    connectionDetail: 'OAuth token reference is healthy.',
    lastVerifiedAt: '2026-07-17T10:00:00Z',
    verificationEvidence: { secret_exposed: false },
    label: 'Kanał główny',
    purpose: 'Publikacja prywatnych Shorts po QA.',
    status: 'configured',
    permissionProfile: 'supervised_write_later',
    allowedCapabilities: [],
    safetyNotes: null,
    notes: null,
    integrationLabel: 'YouTube Studio',
    createdAt: '2026-07-17T09:00:00Z',
    updatedAt: '2026-07-17T10:00:00Z',
    ...overrides,
  }
}

function openPanel() {
  const details = screen.getByTestId('project-platform-bindings') as HTMLDetailsElement
  details.open = true
  return details
}

describe('ProjectPlatformBindingsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchProjectPlatformBindings).mockResolvedValue([binding()])
    vi.mocked(api.fetchAccountCredentials).mockResolvedValue([
      {
        id: 64,
        label: 'Skrzynka właściciela',
        providerKey: 'gmail',
        accountIdentifier: 'account-b2096dbc@example.invalid',
        secretType: 'oauth',
        status: 'active',
        hasSecret: true,
        riskLevel: 'high',
        allowedCapabilities: ['gmail.readonly'],
        linkedIntegrationKey: 'gmail',
        linkedProjectId: null,
        notes: null,
        maskedSecretState: 'Stored securely',
      },
    ])
  })

  it('pokazuje realne konto, zasób, środowisko, zakresy i stan połączenia', async () => {
    render(<ProjectPlatformBindingsPanel projectId={17} />)

    await waitFor(() => expect(api.fetchProjectPlatformBindings).toHaveBeenCalledWith(17))
    const panel = openPanel()

    expect(within(panel).getByText('Kanał główny')).toBeInTheDocument()
    expect(within(panel).getByText(/UC123456789/)).toBeInTheDocument()
    expect(within(panel).getByText(/Wealth Blueprint \(account-c5034bf4@example.invalid\)/)).toBeInTheDocument()
    expect(within(panel).getByText('production')).toBeInTheDocument()
    expect(within(panel).getByText('youtube.readonly')).toBeInTheDocument()
    expect(within(panel).getByText('Połączono')).toBeInTheDocument()
  })

  it('tworzy trwałe powiązanie bez przesyłania sekretów', async () => {
    vi.mocked(api.fetchProjectPlatformBindings).mockResolvedValue([])
    vi.mocked(api.createProjectPlatformBinding).mockResolvedValue(
      binding({ id: 52, resourceExternalId: 'mailbox:owner', integrationKey: 'gmail', integrationLabel: 'Gmail' }),
    )
    render(<ProjectPlatformBindingsPanel projectId={17} />)

    await waitFor(() => expect(api.fetchProjectPlatformBindings).toHaveBeenCalled())
    openPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Dodaj powiązanie' }))
    fireEvent.change(screen.getByLabelText('Nazwa powiązania'), { target: { value: 'Skrzynka raportowa' } })
    fireEvent.change(screen.getByLabelText('Klucz integracji'), { target: { value: 'gmail' } })
    fireEvent.change(screen.getByLabelText('Konto platformowe'), { target: { value: '64' } })
    fireEvent.change(screen.getByLabelText('Typ zasobu'), { target: { value: 'mailbox' } })
    fireEvent.change(screen.getByLabelText('Trwały identyfikator zasobu'), { target: { value: 'mailbox:owner' } })
    fireEvent.change(screen.getByLabelText('Środowisko'), { target: { value: 'test' } })
    fireEvent.change(screen.getByLabelText('Zakresy uprawnień'), { target: { value: 'gmail.readonly\ngmail.send' } })
    fireEvent.click(screen.getByRole('button', { name: 'Zapisz powiązanie' }))

    await waitFor(() => {
      expect(api.createProjectPlatformBinding).toHaveBeenCalledWith(
        17,
        expect.objectContaining({
          integrationKey: 'gmail',
          accountId: 64,
          label: 'Skrzynka raportowa',
          resourceType: 'mailbox',
          resourceExternalId: 'mailbox:owner',
          environment: 'test',
          permissionScopes: ['gmail.readonly', 'gmail.send'],
          bindingRole: 'primary',
          confirmationPolicy: 'supervised',
          delegationPolicy: 'project_only',
          allowSharedAccount: false,
        }),
      )
    })
    const payload = vi.mocked(api.createProjectPlatformBinding).mock.calls[0][1]
    expect(payload).not.toHaveProperty('secret')
    expect(payload).not.toHaveProperty('token')
  })

  it('edytuje, weryfikuje live i wyłącza istniejące powiązanie', async () => {
    const initial = binding()
    vi.mocked(api.updateProjectPlatformBinding).mockResolvedValue(binding({ label: 'Kanał produkcyjny' }))
    vi.mocked(api.verifyProjectPlatformBinding).mockResolvedValue(
      binding({ connectionStatus: 'not_connected', connectionDetail: 'No OAuth connection.' }),
    )
    vi.mocked(api.disableProjectPlatformBinding).mockResolvedValue(
      binding({ status: 'disabled', connectionStatus: 'disabled' }),
    )
    render(<ProjectPlatformBindingsPanel projectId={17} />)

    await screen.findByText(/1 trwałych powiązań/)
    openPanel()
    fireEvent.click(screen.getByRole('button', { name: `Edytuj ${initial.label}` }))
    fireEvent.change(screen.getByLabelText('Nazwa powiązania'), { target: { value: 'Kanał produkcyjny' } })
    fireEvent.click(screen.getByRole('button', { name: 'Zapisz powiązanie' }))

    await waitFor(() => expect(api.updateProjectPlatformBinding).toHaveBeenCalledWith(17, 41, expect.objectContaining({ label: 'Kanał produkcyjny' })))
    fireEvent.click(screen.getByRole('button', { name: 'Weryfikuj Kanał produkcyjny' }))
    await waitFor(() => expect(api.verifyProjectPlatformBinding).toHaveBeenCalledWith(17, 41))
    expect(screen.getByText('Niepołączone')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Wyłącz Kanał główny' }))
    await waitFor(() => expect(api.disableProjectPlatformBinding).toHaveBeenCalledWith(17, 41))
    expect(screen.getByText('Wyłączone')).toBeInTheDocument()
  })
})

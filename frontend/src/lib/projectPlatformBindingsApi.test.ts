import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createProjectPlatformBinding,
  disableProjectPlatformBinding,
  fetchAccountCredentials,
  fetchProjectPlatformBindings,
  updateProjectPlatformBinding,
  verifyProjectPlatformBinding,
} from './api'

function backendBinding(overrides: Record<string, unknown> = {}) {
  return {
    id: 41,
    project_id: 17,
    integration_key: 'youtube-studio',
    custom_integration_id: null,
    account_id: 65,
    resource_type: 'channel',
    resource_external_id: 'UC123456789',
    resource_label: 'Wealth Blueprint',
    binding_role: 'primary',
    environment: 'production',
    confirmation_policy: 'supervised',
    delegation_policy: 'project_only',
    allow_shared_account: false,
    permission_scopes: ['youtube.readonly'],
    connection_status: 'connected',
    connection_detail: 'OAuth reference is healthy.',
    last_verified_at: '2026-07-17T10:00:00Z',
    verification_evidence: { secret_exposed: false },
    label: 'Kanał główny',
    purpose: 'Prywatne uploady po QA.',
    status: 'configured',
    permission_profile: 'supervised_write_later',
    allowed_capabilities: [],
    safety_notes: null,
    is_default_for_project: false,
    is_default_for_content: false,
    is_default_for_analytics: false,
    is_default_for_storage: false,
    is_default_for_publish_preview: false,
    notes: null,
    integration_label: 'YouTube Studio',
    account_summary: {
      id: 65,
      label: 'Wealth Blueprint',
      provider_key: 'youtube',
      account_identifier: 'account-c5034bf4@example.invalid',
      status: 'active',
      risk_level: 'high',
      linked_integration_key: 'youtube',
    },
    created_at: '2026-07-17T09:00:00Z',
    updated_at: '2026-07-17T10:00:00Z',
    ...overrides,
  }
}

describe('project platform bindings API', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
  afterEach(() => vi.unstubAllGlobals())

  it('pobiera i normalizuje trwałą tożsamość konta oraz zasobu', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => [backendBinding()],
      text: async () => '',
    } as Response)

    const result = await fetchProjectPlatformBindings(17)

    expect(result[0]).toMatchObject({
      projectId: 17,
      accountId: 65,
      resourceExternalId: 'UC123456789',
      environment: 'production',
      permissionScopes: ['youtube.readonly'],
      connectionStatus: 'connected',
    })
    expect(result[0].accountSummary?.accountIdentifier).toBe('account-c5034bf4@example.invalid')
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/projects/17/app-links'),
      expect.objectContaining({ headers: expect.any(Object) }),
    )
  })

  it('używa właściwych metod dla create, update, disable i live verify', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => backendBinding(),
      text: async () => '',
    } as Response)

    await createProjectPlatformBinding(17, {
      integrationKey: 'youtube-studio',
      accountId: 65,
      label: 'Kanał główny',
      permissionProfile: 'supervised_write_later',
      resourceType: 'channel',
      resourceExternalId: 'UC123456789',
      environment: 'production',
      permissionScopes: ['youtube.readonly'],
      bindingRole: 'publishing',
      confirmationPolicy: 'always_confirm',
      delegationPolicy: 'approved_workers',
      allowSharedAccount: true,
    })
    await updateProjectPlatformBinding(17, 41, { label: 'Kanał produkcyjny' })
    await disableProjectPlatformBinding(17, 41)
    await verifyProjectPlatformBinding(17, 41)

    const calls = vi.mocked(fetch).mock.calls
    expect(calls[0][0]).toEqual(expect.stringContaining('/projects/17/app-links'))
    expect(calls[0][1]).toEqual(expect.objectContaining({ method: 'POST' }))
    expect(String(calls[0][1]?.body)).not.toMatch(/secret|token/i)
    expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({
      binding_role: 'publishing',
      confirmation_policy: 'always_confirm',
      delegation_policy: 'approved_workers',
      allow_shared_account: true,
    })
    expect(calls[1]).toEqual([
      expect.stringContaining('/projects/17/app-links/41'),
      expect.objectContaining({ method: 'PATCH' }),
    ])
    expect(calls[2]).toEqual([
      expect.stringContaining('/projects/17/app-links/41'),
      expect.objectContaining({ method: 'DELETE' }),
    ])
    expect(calls[3]).toEqual([
      expect.stringContaining('/projects/17/app-links/41/verify'),
      expect.objectContaining({ method: 'POST' }),
    ])
  })

  it('pobiera listę kont bez wartości sekretów', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => [{
        id: 64,
        label: 'Skrzynka właściciela',
        provider_key: 'gmail',
        account_identifier: 'account-b2096dbc@example.invalid',
        secret_type: 'oauth',
        status: 'active',
        has_secret: true,
        risk_level: 'high',
        allowed_capabilities: ['gmail.readonly'],
        linked_integration_key: 'gmail',
        linked_project_id: null,
        notes: null,
        masked_secret_state: 'Stored securely',
      }],
      text: async () => '',
    } as Response)

    const accounts = await fetchAccountCredentials()

    expect(accounts[0]).toMatchObject({ id: 64, providerKey: 'gmail', hasSecret: true })
    expect(accounts[0]).not.toHaveProperty('secretValue')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/accounts'), expect.any(Object))
  })
})

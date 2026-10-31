import { ChevronDown, Link2, LoaderCircle, Pencil, Plus, RefreshCw, ShieldOff, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  createProjectPlatformBinding,
  disableProjectPlatformBinding,
  fetchAccountCredentials,
  fetchProjectPlatformBindings,
  updateProjectPlatformBinding,
  verifyProjectPlatformBinding,
  type ProjectPlatformBinding,
  type ProjectPlatformBindingCreatePayload,
  type AccountCredentialResult,
  type ProjectPlatformEnvironment,
  type ProjectPlatformPermissionProfile,
  type ProjectPlatformResourceType,
} from '../lib/api'

const INPUT_CLASS =
  'mt-1 w-full rounded-md border border-border-default bg-app px-2.5 py-2 text-sm text-text-primary outline-none transition focus:border-accent-primary disabled:cursor-not-allowed disabled:opacity-60'
const LABEL_CLASS = 'block text-xs font-medium text-text-secondary'

const RESOURCE_TYPES: Array<{ value: ProjectPlatformResourceType; label: string }> = [
  { value: 'account', label: 'Konto' },
  { value: 'channel', label: 'Kanał' },
  { value: 'mailbox', label: 'Skrzynka pocztowa' },
  { value: 'broker_account', label: 'Konto brokerskie' },
  { value: 'workspace', label: 'Workspace' },
  { value: 'profile', label: 'Profil' },
]

const PERMISSION_PROFILES: Array<{ value: ProjectPlatformPermissionProfile; label: string }> = [
  { value: 'metadata_only', label: 'Tylko metadane' },
  { value: 'read_only', label: 'Tylko odczyt' },
  { value: 'draft_write', label: 'Zapis draftów' },
  { value: 'supervised_write_later', label: 'Zapis nadzorowany' },
  { value: 'publish_preview_later', label: 'Podgląd publikacji' },
  { value: 'financial_read_later', label: 'Odczyt finansowy' },
  { value: 'trading_preview_later', label: 'Podgląd paper tradingu' },
]

interface BindingFormState {
  integrationKey: string
  accountId: string
  label: string
  purpose: string
  status: ProjectPlatformBinding['status']
  permissionProfile: ProjectPlatformPermissionProfile
  resourceType: ProjectPlatformResourceType
  resourceExternalId: string
  resourceLabel: string
  environment: ProjectPlatformEnvironment
  permissionScopes: string
  bindingRole: string
  confirmationPolicy: ProjectPlatformBinding['confirmationPolicy']
  delegationPolicy: ProjectPlatformBinding['delegationPolicy']
  allowSharedAccount: boolean
}

const EMPTY_FORM: BindingFormState = {
  integrationKey: '',
  accountId: '',
  label: '',
  purpose: '',
  status: 'configured',
  permissionProfile: 'read_only',
  resourceType: 'account',
  resourceExternalId: '',
  resourceLabel: '',
  environment: 'production',
  permissionScopes: '',
  bindingRole: 'primary',
  confirmationPolicy: 'supervised',
  delegationPolicy: 'project_only',
  allowSharedAccount: false,
}

function bindingForm(binding: ProjectPlatformBinding): BindingFormState {
  return {
    integrationKey: binding.integrationKey,
    accountId: binding.accountId == null ? '' : String(binding.accountId),
    label: binding.label,
    purpose: binding.purpose ?? '',
    status: binding.status,
    permissionProfile: binding.permissionProfile,
    resourceType: binding.resourceType,
    resourceExternalId: binding.resourceExternalId ?? '',
    resourceLabel: binding.resourceLabel ?? '',
    environment: binding.environment,
    permissionScopes: binding.permissionScopes.join('\n'),
    bindingRole: binding.bindingRole,
    confirmationPolicy: binding.confirmationPolicy,
    delegationPolicy: binding.delegationPolicy,
    allowSharedAccount: binding.allowSharedAccount,
  }
}

function splitList(value: string): string[] {
  return [...new Set(value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean))]
}

function dateTime(value: string | null): string {
  if (!value) return 'Nie weryfikowano'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('pl-PL')
}

function statusStyle(status: string): string {
  if (status === 'connected' || status === 'credential_reference_present') {
    return 'border-sem-success/40 bg-sem-success/10 text-sem-success'
  }
  if (status === 'disabled' || status === 'missing_account' || status === 'not_connected') {
    return 'border-sem-danger/40 bg-sem-danger/10 text-sem-danger'
  }
  return 'border-sem-warning/40 bg-sem-warning/10 text-sem-warning'
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    connected: 'Połączono',
    credential_reference_present: 'Referencja danych dostępna',
    disabled: 'Wyłączone',
    missing_account: 'Brak konta',
    not_connected: 'Niepołączone',
    verification_unavailable: 'Brak weryfikatora',
    unverified: 'Niesprawdzone',
  }
  return labels[status] ?? status.replaceAll('_', ' ')
}

function replaceBinding(items: ProjectPlatformBinding[], next: ProjectPlatformBinding) {
  return items.map((item) => (item.id === next.id ? next : item))
}

export function ProjectPlatformBindingsPanel({ projectId }: { projectId: number }) {
  const [bindings, setBindings] = useState<ProjectPlatformBinding[]>([])
  const [accounts, setAccounts] = useState<AccountCredentialResult[]>([])
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<BindingFormState>(EMPTY_FORM)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [nextBindings, nextAccounts] = await Promise.all([
        fetchProjectPlatformBindings(projectId),
        fetchAccountCredentials(),
      ])
      setBindings(nextBindings)
      setAccounts(nextAccounts)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nie udało się pobrać powiązań platform.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setFormOpen(false)
    setEditingId(null)
    setForm(EMPTY_FORM)
    void load()
    // load uses only the active project identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const setField = <K extends keyof BindingFormState>(key: K, value: BindingFormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }))
  }

  const beginCreate = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setFormOpen(true)
    setError(null)
    setNotice(null)
  }

  const beginEdit = (binding: ProjectPlatformBinding) => {
    setEditingId(binding.id)
    setForm(bindingForm(binding))
    setFormOpen(true)
    setError(null)
    setNotice(null)
  }

  const closeForm = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setFormOpen(false)
  }

  const save = async () => {
    const label = form.label.trim()
    const integrationKey = form.integrationKey.trim()
    const resourceExternalId = form.resourceExternalId.trim()
    if (!label || !integrationKey || !resourceExternalId) {
      setError('Nazwa, klucz integracji i trwały identyfikator zasobu są wymagane.')
      return
    }
    const accountId = form.accountId.trim() ? Number(form.accountId) : null
    if (accountId != null && (!Number.isInteger(accountId) || accountId <= 0)) {
      setError('ID konta musi być dodatnią liczbą całkowitą.')
      return
    }

    setBusyKey(editingId == null ? 'create' : `edit-${editingId}`)
    setError(null)
    setNotice(null)
    try {
      const common = {
        accountId,
        label,
        purpose: form.purpose.trim() || null,
        status: form.status,
        permissionProfile: form.permissionProfile,
        resourceType: form.resourceType,
        resourceExternalId,
        resourceLabel: form.resourceLabel.trim() || null,
        environment: form.environment,
        permissionScopes: splitList(form.permissionScopes),
        bindingRole: form.bindingRole.trim() || 'primary',
        confirmationPolicy: form.confirmationPolicy,
        delegationPolicy: form.delegationPolicy,
        allowSharedAccount: form.allowSharedAccount,
      }
      if (editingId == null) {
        const created = await createProjectPlatformBinding(projectId, {
          ...common,
          integrationKey,
        } satisfies ProjectPlatformBindingCreatePayload)
        setBindings((current) => [created, ...current])
        setNotice('Powiązanie zostało zapisane. Uruchom weryfikację live, aby potwierdzić połączenie.')
      } else {
        const updated = await updateProjectPlatformBinding(projectId, editingId, common)
        setBindings((current) => replaceBinding(current, updated))
        setNotice('Powiązanie zostało zaktualizowane.')
      }
      closeForm()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nie udało się zapisać powiązania.')
    } finally {
      setBusyKey(null)
    }
  }

  const verify = async (binding: ProjectPlatformBinding) => {
    setBusyKey(`verify-${binding.id}`)
    setError(null)
    setNotice(null)
    try {
      const updated = await verifyProjectPlatformBinding(projectId, binding.id)
      setBindings((current) => replaceBinding(current, updated))
      setNotice(`Weryfikacja „${binding.label}”: ${statusLabel(updated.connectionStatus)}.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Weryfikacja live nie powiodła się.')
    } finally {
      setBusyKey(null)
    }
  }

  const disable = async (binding: ProjectPlatformBinding) => {
    setBusyKey(`disable-${binding.id}`)
    setError(null)
    setNotice(null)
    try {
      const updated = await disableProjectPlatformBinding(projectId, binding.id)
      setBindings((current) => replaceBinding(current, updated))
      setNotice(`Powiązanie „${binding.label}” zostało wyłączone.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nie udało się wyłączyć powiązania.')
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <details className="mt-4 rounded-lg border border-border-default bg-app/35" data-testid="project-platform-bindings">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-3 marker:hidden">
        <div className="flex items-center gap-2">
          <Link2 size={15} className="text-accent-primary" />
          <div>
            <div className="label-eyebrow">POWIĄZANIA PLATFORM</div>
            <p className="mt-0.5 text-xs text-text-tertiary">
              {loading ? 'Odczytuję backend…' : `${bindings.length} trwałych powiązań w tym projekcie`}
            </p>
          </div>
        </div>
        <ChevronDown size={16} className="text-text-tertiary" />
      </summary>

      <div className="border-t border-border-default p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="max-w-3xl text-xs leading-5 text-text-secondary">
            Powiązanie wskazuje konto i stabilny zasób platformy. Nie przechowuje hasła, tokenu ani kodu OAuth.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading || busyKey != null}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border-default px-2.5 py-1.5 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary disabled:opacity-50"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Odśwież
            </button>
            <button
              type="button"
              onClick={beginCreate}
              disabled={busyKey != null}
              className="inline-flex items-center gap-1.5 rounded-lg border border-accent-primary/60 bg-accent-primary/10 px-2.5 py-1.5 text-xs font-medium text-accent-primary disabled:opacity-50"
            >
              <Plus size={13} /> Dodaj powiązanie
            </button>
          </div>
        </div>

        {error ? <div className="mt-3 rounded-md border border-sem-danger/40 bg-sem-danger/10 px-3 py-2 text-xs text-sem-danger">{error}</div> : null}
        {notice ? <div className="mt-3 rounded-md border border-sem-success/40 bg-sem-success/10 px-3 py-2 text-xs text-sem-success">{notice}</div> : null}

        {formOpen ? (
          <div className="mt-3 rounded-lg border border-accent-primary/35 bg-panel/70 p-3" data-testid="platform-binding-form">
            <div className="flex items-center justify-between gap-2">
              <div className="label-eyebrow">{editingId == null ? 'NOWE POWIĄZANIE' : 'EDYCJA POWIĄZANIA'}</div>
              <button type="button" onClick={closeForm} aria-label="Zamknij formularz" className="text-text-tertiary hover:text-text-primary">
                <X size={15} />
              </button>
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <label className={LABEL_CLASS}>
                Nazwa powiązania
                <input aria-label="Nazwa powiązania" className={INPUT_CLASS} value={form.label} onChange={(event) => setField('label', event.target.value)} />
              </label>
              <label className={LABEL_CLASS}>
                Klucz integracji
                <input aria-label="Klucz integracji" className={INPUT_CLASS} value={form.integrationKey} disabled={editingId != null} placeholder="youtube-studio" onChange={(event) => setField('integrationKey', event.target.value)} />
              </label>
              <label className={LABEL_CLASS}>
                Konto platformowe (opcjonalne)
                <select aria-label="Konto platformowe" className={INPUT_CLASS} value={form.accountId} onChange={(event) => setField('accountId', event.target.value)}>
                  <option value="">Bez przypisanego konta</option>
                  {accounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.label} · {account.providerKey} · {account.accountIdentifier}
                    </option>
                  ))}
                </select>
              </label>
              <label className={LABEL_CLASS}>
                Typ zasobu
                <select aria-label="Typ zasobu" className={INPUT_CLASS} value={form.resourceType} onChange={(event) => setField('resourceType', event.target.value as ProjectPlatformResourceType)}>
                  {RESOURCE_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <label className={LABEL_CLASS}>
                Trwały identyfikator zasobu
                <input aria-label="Trwały identyfikator zasobu" className={INPUT_CLASS} value={form.resourceExternalId} placeholder="UC… / mailbox / account UUID" onChange={(event) => setField('resourceExternalId', event.target.value)} />
              </label>
              <label className={LABEL_CLASS}>
                Etykieta zasobu
                <input aria-label="Etykieta zasobu" className={INPUT_CLASS} value={form.resourceLabel} onChange={(event) => setField('resourceLabel', event.target.value)} />
              </label>
              <label className={LABEL_CLASS}>
                Środowisko
                <select aria-label="Środowisko" className={INPUT_CLASS} value={form.environment} onChange={(event) => setField('environment', event.target.value as ProjectPlatformEnvironment)}>
                  <option value="production">Produkcja</option>
                  <option value="test">Test</option>
                  <option value="demo">Demo</option>
                </select>
              </label>
              <label className={LABEL_CLASS}>
                Profil uprawnień
                <select aria-label="Profil uprawnień" className={INPUT_CLASS} value={form.permissionProfile} onChange={(event) => setField('permissionProfile', event.target.value as ProjectPlatformPermissionProfile)}>
                  {PERMISSION_PROFILES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <label className={LABEL_CLASS}>
                Rola powiązania
                <input aria-label="Rola powiązania" className={INPUT_CLASS} value={form.bindingRole} placeholder="primary" onChange={(event) => setField('bindingRole', event.target.value)} />
              </label>
              <label className={LABEL_CLASS}>
                Polityka potwierdzenia
                <select aria-label="Polityka potwierdzenia" className={INPUT_CLASS} value={form.confirmationPolicy} onChange={(event) => setField('confirmationPolicy', event.target.value as ProjectPlatformBinding['confirmationPolicy'])}>
                  <option value="none">Bez potwierdzenia</option>
                  <option value="supervised">Nadzorowana</option>
                  <option value="always_confirm">Zawsze potwierdzaj</option>
                </select>
              </label>
              <label className={LABEL_CLASS}>
                Polityka delegacji
                <select aria-label="Polityka delegacji" className={INPUT_CLASS} value={form.delegationPolicy} onChange={(event) => setField('delegationPolicy', event.target.value as ProjectPlatformBinding['delegationPolicy'])}>
                  <option value="disabled">Wyłączona</option>
                  <option value="project_only">Tylko projekt</option>
                  <option value="approved_workers">Zatwierdzeni workerzy</option>
                </select>
              </label>
              <label className={LABEL_CLASS}>
                Status
                <select aria-label="Status powiązania" className={INPUT_CLASS} value={form.status} onChange={(event) => setField('status', event.target.value as ProjectPlatformBinding['status'])}>
                  <option value="configured">Skonfigurowane</option>
                  <option value="planned">Planowane</option>
                  <option value="needs_review">Wymaga przeglądu</option>
                  <option value="disabled">Wyłączone</option>
                </select>
              </label>
              <label className={`${LABEL_CLASS} md:col-span-2 xl:col-span-3`}>
                Zakresy uprawnień (po jednym w wierszu)
                <textarea aria-label="Zakresy uprawnień" className={`${INPUT_CLASS} min-h-20 resize-y`} value={form.permissionScopes} placeholder="youtube.readonly" onChange={(event) => setField('permissionScopes', event.target.value)} />
              </label>
              <label className={`${LABEL_CLASS} md:col-span-2 xl:col-span-3`}>
                Cel powiązania
                <textarea aria-label="Cel powiązania" className={`${INPUT_CLASS} min-h-16 resize-y`} value={form.purpose} onChange={(event) => setField('purpose', event.target.value)} />
              </label>
              <label className="flex items-start gap-2 rounded-md border border-border-default bg-app/55 px-3 py-2 text-xs text-text-secondary md:col-span-2 xl:col-span-3">
                <input type="checkbox" aria-label="Zezwól na współdzielenie konta" className="mt-0.5" checked={form.allowSharedAccount} onChange={(event) => setField('allowSharedAccount', event.target.checked)} />
                <span>
                  Zezwól na współdzielenie tego konta z innymi projektami. Domyślnie konto może należeć tylko do jednego projektu.
                </span>
              </label>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" onClick={closeForm} className="rounded-lg border border-border-default px-3 py-2 text-xs text-text-secondary">Anuluj</button>
              <button type="button" onClick={() => void save()} disabled={busyKey != null} className="inline-flex items-center gap-1.5 rounded-lg border border-accent-primary bg-accent-primary/10 px-3 py-2 text-xs font-medium text-accent-primary disabled:opacity-50">
                {busyKey === 'create' || busyKey === `edit-${editingId}` ? <LoaderCircle size={13} className="animate-spin" /> : null}
                Zapisz powiązanie
              </button>
            </div>
          </div>
        ) : null}

        <div className="mt-3 space-y-2">
          {!loading && bindings.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border-default px-3 py-5 text-center text-sm text-text-tertiary">
              Ten projekt nie ma jeszcze trwałych powiązań platform.
            </div>
          ) : null}
          {bindings.map((binding) => (
            <article key={binding.id} className="rounded-lg border border-border-default bg-panel/65 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-text-primary">{binding.label}</h3>
                    <span className="rounded-pill border border-border-default px-2 py-0.5 text-[10px] uppercase text-text-tertiary">{binding.environment}</span>
                    <span className={`rounded-pill border px-2 py-0.5 text-[10px] font-semibold ${statusStyle(binding.connectionStatus)}`}>{statusLabel(binding.connectionStatus)}</span>
                  </div>
                  <p className="mt-1 font-mono text-[11px] text-text-secondary">
                    {binding.integrationLabel} · {binding.resourceType} · {binding.resourceExternalId ?? 'brak trwałego ID'}
                  </p>
                  <p className="mt-1 text-xs text-text-tertiary">
                    Konto: {binding.accountSummary ? `${binding.accountSummary.label} (${binding.accountSummary.accountIdentifier})` : binding.accountId == null ? 'nieprzypisane' : `#${binding.accountId}`}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={() => beginEdit(binding)} disabled={busyKey != null} aria-label={`Edytuj ${binding.label}`} className="inline-flex items-center gap-1 rounded-md border border-border-default px-2 py-1.5 text-xs text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:opacity-50">
                    <Pencil size={12} /> Edytuj
                  </button>
                  <button type="button" onClick={() => void verify(binding)} disabled={busyKey != null || binding.status === 'disabled'} aria-label={`Weryfikuj ${binding.label}`} className="inline-flex items-center gap-1 rounded-md border border-border-default px-2 py-1.5 text-xs text-text-secondary hover:border-sem-success hover:text-sem-success disabled:opacity-50">
                    {busyKey === `verify-${binding.id}` ? <LoaderCircle size={12} className="animate-spin" /> : <RefreshCw size={12} />} Weryfikuj live
                  </button>
                  <button type="button" onClick={() => void disable(binding)} disabled={busyKey != null || binding.status === 'disabled'} aria-label={`Wyłącz ${binding.label}`} className="inline-flex items-center gap-1 rounded-md border border-sem-danger/30 px-2 py-1.5 text-xs text-sem-danger disabled:opacity-40">
                    <ShieldOff size={12} /> Wyłącz
                  </button>
                </div>
              </div>
              <div className="mt-3 grid gap-2 text-xs md:grid-cols-3">
                <div className="rounded-md border border-border-default/80 bg-app/45 px-2.5 py-2">
                  <span className="text-text-tertiary">Profil:</span> <span className="text-text-secondary">{binding.permissionProfile.replaceAll('_', ' ')}</span>
                </div>
                <div className="rounded-md border border-border-default/80 bg-app/45 px-2.5 py-2">
                  <span className="text-text-tertiary">Binding:</span> <span className="text-text-secondary">{binding.status.replaceAll('_', ' ')}</span>
                </div>
                <div className="rounded-md border border-border-default/80 bg-app/45 px-2.5 py-2">
                  <span className="text-text-tertiary">Ostatni live check:</span> <span className="text-text-secondary">{dateTime(binding.lastVerifiedAt)}</span>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {binding.permissionScopes.length ? binding.permissionScopes.map((scope) => (
                  <span key={scope} className="rounded-md border border-border-default px-2 py-1 font-mono text-[10px] text-text-tertiary">{scope}</span>
                )) : <span className="text-xs text-text-tertiary">Brak zadeklarowanych zakresów.</span>}
              </div>
              {binding.connectionDetail ? <p className="mt-2 text-xs leading-5 text-text-secondary">{binding.connectionDetail}</p> : null}
            </article>
          ))}
        </div>
      </div>
    </details>
  )
}

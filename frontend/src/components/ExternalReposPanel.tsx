import { useEffect, useState } from 'react'
import { API_BASE_URL } from '../lib/api'

type Repo = {
  id: string
  name: string
  type: string
  license: string
  trust: string
  status: string
  commit: string
  docs_url: string
  summary: string
  items_discovered?: number
  skills_enabled?: number
  skills_total?: number
  runtime?: string
  live_mode?: string
  social_sdk_node?: string
  connected_accounts?: number
  last_execution?: { capability?: string; status?: string; finished_at?: string } | null
}

type SocialAccount = {
  account_id: string
  display_name: string
  credential_stored: boolean
  scopes: string[]
  last_validation?: string | null
  connected_at?: string
}

type Social = {
  id: string
  name: string
  status: string
  connected: boolean
  display_name?: string
  credential_stored?: boolean
  account_count?: number
  accounts?: SocialAccount[]
  capabilities: Array<{ id: string }>
  password_accepted: boolean
  publish: string
  auth?: string
}

type Skill = {
  id: string
  classification: string
  enabled: boolean
  trust: string
  commit: string
  description?: string
  capabilities?: string[]
  source_repo?: string
  executable?: boolean
  requires_data?: boolean
  last_run?: { status?: string; finished_at?: string } | null
}

type ProjectMap = {
  project_id: string
  platform_or_account: string | null
}

type OpenToonz = {
  status: string
  path: string | null
  detail: string
}

const PROJECT_LABELS: Record<string, string> = {
  wealth_blueprint: 'Wealth Blueprint',
  wonder_crew: 'Wonder Crew',
  dark_chronicles: 'Dark Chronicles',
}

export default function ExternalReposPanel() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [social, setSocial] = useState<Social[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [projects, setProjects] = useState<ProjectMap[]>([])
  const [opentoonz, setOpenToonz] = useState<OpenToonz | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [connectForm, setConnectForm] = useState<Record<string, { name: string; ref: string }>>({})
  const [openToonzPath, setOpenToonzPath] = useState('')
  const [discoverQuery, setDiscoverQuery] = useState('python')
  const [discoverHits, setDiscoverHits] = useState<Array<{ name: string; url: string; trust: string }>>([])
  const [inspected, setInspected] = useState<string | null>(null)

  const load = () => {
    void Promise.all([
      fetch(`${API_BASE_URL}/integrations/external-repos`).then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json()
      }),
      fetch(`${API_BASE_URL}/integrations/external-repos/social`).then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json()
      }),
      fetch(`${API_BASE_URL}/integrations/external-repos/skills`).then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json()
      }),
      fetch(`${API_BASE_URL}/integrations/external-repos/projects`).then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json()
      }),
      fetch(`${API_BASE_URL}/integrations/external-repos/opentoonz`).then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json()
      }),
    ])
      .then(([repoRows, socialRows, skillRows, projectRows, openToonzStatus]) => {
        setRepos(repoRows as Repo[])
        setSocial(socialRows as Social[])
        setSkills(skillRows as Skill[])
        setProjects((projectRows as { projects: ProjectMap[] }).projects || [])
        setOpenToonz(openToonzStatus as OpenToonz)
        if ((openToonzStatus as OpenToonz).path) {
          setOpenToonzPath((openToonzStatus as OpenToonz).path || '')
        }
        setError(null)
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : 'Nie udało się wczytać repozytoriów.')
      })
  }

  useEffect(() => {
    load()
  }, [])

  const syncAwesome = async () => {
    setBusy('awesome')
    const response = await fetch(`${API_BASE_URL}/integrations/external-repos/awesome/sync`, { method: 'POST' })
    setBusy(null)
    if (!response.ok) {
      setError('Synchronizacja Awesome nie doszła.')
      return
    }
    const payload = (await response.json()) as { items_discovered?: number }
    setMessage(`Awesome: indeks ${payload.items_discovered ?? 0} linków (DISCOVERED).`)
    load()
  }

  const runDiscover = async () => {
    setBusy('discover')
    const response = await fetch(
      `${API_BASE_URL}/integrations/external-repos/awesome/discover?q=${encodeURIComponent(discoverQuery)}`,
    )
    setBusy(null)
    if (!response.ok) {
      setError('Discovery nie doszło.')
      return
    }
    const payload = (await response.json()) as { items: Array<{ name: string; url: string; trust: string }> }
    setDiscoverHits(payload.items || [])
  }

  const toggleSkill = async (id: string, enabled: boolean) => {
    await fetch(`${API_BASE_URL}/integrations/external-repos/skills/${id}/enabled`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    load()
  }

  const inspectSkill = async (id: string) => {
    const response = await fetch(`${API_BASE_URL}/integrations/external-repos/skills/${id}`)
    if (!response.ok) {
      setError('Inspect nie doszedł.')
      return
    }
    const payload = (await response.json()) as { skill?: Skill; view_source?: string }
    setInspected(
      `${payload.skill?.id}: ${payload.skill?.description || ''} Capabilities: ${(payload.skill?.capabilities || []).join(', ')}. Source: ${payload.view_source}`,
    )
  }

  const validateSkill = async (id: string) => {
    const response = await fetch(`${API_BASE_URL}/integrations/external-repos/skills/${id}/validate`, {
      method: 'POST',
    })
    const payload = (await response.json()) as { status: string; detail?: string }
    setMessage(`${id}: ${payload.status}${payload.detail ? ` — ${payload.detail}` : ''}`)
  }

  const connectAccount = async (platformId: string) => {
    const form = connectForm[platformId] || { name: '', ref: '' }
    setBusy(`connect-${platformId}`)
    const response = await fetch(`${API_BASE_URL}/integrations/external-repos/social/${platformId}/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        display_name: form.name,
        credential_ref: form.ref,
      }),
    })
    setBusy(null)
    const payload = (await response.json()) as { detail?: string; status?: string }
    if (!response.ok) {
      setError(payload.detail || 'Connect nie doszedł.')
      return
    }
    setMessage(`${platformId}: ${payload.detail || 'Connected. Credentials stored securely.'}`)
    setConnectForm((current) => ({ ...current, [platformId]: { name: '', ref: '' } }))
    load()
  }

  const testAccount = async (platformId: string, accountId?: string) => {
    setBusy(`test-${platformId}`)
    const response = await fetch(`${API_BASE_URL}/integrations/external-repos/social/${platformId}/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: accountId || null }),
    })
    setBusy(null)
    const payload = (await response.json()) as { status: string; detail?: string; display_name?: string }
    setMessage(
      `${platformId}: ${payload.status}${payload.display_name ? ` as ${payload.display_name}` : ''}. ${payload.detail || ''}`,
    )
    load()
  }

  const disconnectAccount = async (platformId: string, accountId?: string) => {
    setBusy(`disconnect-${platformId}`)
    const params = new URLSearchParams()
    if (accountId) params.set('account_id', accountId)
    params.set('remove_vault_secret', 'true')
    const response = await fetch(
      `${API_BASE_URL}/integrations/external-repos/social/${platformId}/disconnect?${params.toString()}`,
      { method: 'POST' },
    )
    setBusy(null)
    if (!response.ok) {
      setError('Disconnect nie doszedł.')
      return
    }
    setMessage(`${platformId}: disconnected. Credentials removed from vault when possible.`)
    load()
  }

  const setProjectMapping = async (projectId: string, value: string) => {
    await fetch(`${API_BASE_URL}/integrations/external-repos/projects/${projectId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ platform_or_account: value || null }),
    })
    load()
  }

  const saveOpenToonz = async () => {
    setBusy('opentoonz')
    const response = await fetch(`${API_BASE_URL}/integrations/external-repos/opentoonz/path`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: openToonzPath }),
    })
    setBusy(null)
    const payload = (await response.json()) as OpenToonz
    if (!response.ok) {
      setError((payload as { detail?: string }).detail || 'Ścieżka OpenToonz odrzucona.')
      return
    }
    setOpenToonz(payload)
    setMessage(`OpenToonz: ${payload.status}. ${payload.detail}`)
    load()
  }

  return (
    <section className="mt-6 space-y-3" data-testid="external-repos">
      <div>
        <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-tertiary">
          Integracje → Social / Skills / Discovery / Local Tools
        </div>
        <p className="mt-2 max-w-3xl text-sm text-text-secondary">
          Zarządzanie z Settings. Social SDK nie jest uruchamiany jako pakiet Node. Awesome nie jest pluginem.
          OpenToonz nie jest pobierany bez zgody. Sekrety zostają w sejfie — UI pokazuje tylko konto.
        </p>
      </div>
      {error ? <div className="border border-error/40 bg-error/10 p-3 text-sm text-error">{error}</div> : null}
      {message ? <div className="border border-border-default p-3 text-sm text-text-secondary">{message}</div> : null}
      {inspected ? <div className="border border-border-default p-3 text-xs text-text-tertiary">{inspected}</div> : null}

      {repos.map((repo) => (
        <article key={repo.id} className="border border-border-default bg-void p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="font-display text-sm uppercase tracking-[0.12em] text-text-primary">{repo.name}</h3>
              <p className="mt-2 text-sm text-text-secondary">{repo.summary}</p>
            </div>
            <span className="border border-border-default px-2 py-1 font-mono text-[10px] uppercase text-text-tertiary">
              {repo.status} / {repo.trust}
            </span>
          </div>
          <p className="mt-2 text-xs text-text-tertiary">
            {repo.type}. Licencja {repo.license}. Commit {repo.commit.slice(0, 12)}.
            {repo.runtime ? ` Runtime: ${repo.runtime}.` : ''}
            {repo.live_mode ? ` Live: ${repo.live_mode}.` : ''}
            {repo.social_sdk_node ? ` Node SDK: ${repo.social_sdk_node}.` : ''}
            {repo.connected_accounts != null ? ` Connected accounts: ${repo.connected_accounts}.` : ''}
            {repo.items_discovered != null ? ` Odkryte linki: ${repo.items_discovered}.` : ''}
            {repo.skills_total != null ? ` Skills enabled: ${repo.skills_enabled}/${repo.skills_total}.` : ''}
            {repo.last_execution
              ? ` Last: ${repo.last_execution.capability} ${repo.last_execution.status}.`
              : ''}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {repo.id === 'awesome' ? (
              <button
                type="button"
                disabled={busy === 'awesome'}
                onClick={() => void syncAwesome()}
                className="border border-accent-primary px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
              >
                Sync index
              </button>
            ) : null}
            <a className="border border-border-default px-3 py-2 text-[11px] uppercase text-text-secondary" href={repo.docs_url}>
              View source
            </a>
          </div>
        </article>
      ))}

      <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-tertiary">Social Accounts</div>
      {social.map((account) => {
        const form = connectForm[account.id] || { name: '', ref: '' }
        return (
          <article key={account.id} className="border border-border-default p-3 text-sm text-text-secondary">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="text-text-primary">{account.name}</div>
                <p className="mt-1 text-xs">
                  {account.connected
                    ? `Connected as: ${account.display_name || 'konto'}. Credentials stored securely.`
                    : 'Disconnected'}
                  . Status {account.status}. Auth {account.auth || 'oauth'}. Publikacja: {account.publish}.
                </p>
                <p className="mt-1 text-xs text-text-tertiary">
                  Capabilities: {account.capabilities.map((item) => item.id).join(', ') || 'UNVERIFIED'}. Hasło
                  platformy nie jest przyjmowane.
                </p>
              </div>
              <span className="font-mono text-[10px] uppercase text-text-tertiary">
                {account.account_count || 0} accounts
              </span>
            </div>
            {(account.accounts || []).map((row) => (
              <div key={row.account_id} className="mt-2 flex flex-wrap items-center gap-2 border border-border-default p-2">
                <span className="text-xs text-text-primary">{row.display_name}</span>
                <span className="text-[10px] text-text-tertiary">
                  {row.credential_stored ? 'Credential stored' : 'No credential'} · validation{' '}
                  {row.last_validation || 'none'}
                </span>
                <button
                  type="button"
                  disabled={busy === `test-${account.id}`}
                  onClick={() => void testAccount(account.id, row.account_id)}
                  className="border border-border-default px-2 py-1 text-[10px] uppercase"
                >
                  Test connection
                </button>
                <button
                  type="button"
                  disabled={busy === `disconnect-${account.id}`}
                  onClick={() => void disconnectAccount(account.id, row.account_id)}
                  className="border border-border-default px-2 py-1 text-[10px] uppercase"
                >
                  Disconnect
                </button>
              </div>
            ))}
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              <label className="block text-xs text-text-tertiary">
                Nazwa konta
                <input
                  value={form.name}
                  onChange={(event) =>
                    setConnectForm((current) => ({
                      ...current,
                      [account.id]: { ...form, name: event.target.value },
                    }))
                  }
                  placeholder="Wealth Blueprint"
                  className="mt-1 w-full border border-border-default bg-void px-2 py-1 text-sm text-text-primary"
                />
              </label>
              <label className="block text-xs text-text-tertiary">
                credential_ref (sejf)
                <input
                  value={form.ref}
                  onChange={(event) =>
                    setConnectForm((current) => ({
                      ...current,
                      [account.id]: { ...form, ref: event.target.value },
                    }))
                  }
                  placeholder="oauth:youtube:… albo aes_256_gcm:…"
                  className="mt-1 w-full border border-border-default bg-void px-2 py-1 text-sm text-text-primary"
                />
              </label>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy === `connect-${account.id}`}
                onClick={() => void connectAccount(account.id)}
                className="border border-accent-primary px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
              >
                {account.connected ? 'Reconnect' : 'Connect'}
              </button>
              {!account.connected ? (
                <button
                  type="button"
                  disabled={busy === `test-${account.id}`}
                  onClick={() => void testAccount(account.id)}
                  className="border border-border-default px-3 py-2 text-[11px] uppercase text-text-secondary"
                >
                  Test connection
                </button>
              ) : null}
            </div>
          </article>
        )
      })}

      <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-tertiary">
        Project → Social account
      </div>
      {projects.map((project) => (
        <article key={project.project_id} className="flex flex-wrap items-center gap-3 border border-border-default p-3">
          <div className="min-w-[10rem] text-sm text-text-primary">
            {PROJECT_LABELS[project.project_id] || project.project_id}
          </div>
          <select
            value={project.platform_or_account || ''}
            onChange={(event) => void setProjectMapping(project.project_id, event.target.value)}
            className="border border-border-default bg-void px-2 py-1 text-sm text-text-primary"
          >
            <option value="">NONE</option>
            {social.flatMap((platform) => {
              if (!platform.accounts?.length) {
                return [
                  <option key={platform.id} value={platform.id}>
                    {platform.name} (platform)
                  </option>,
                ]
              }
              return platform.accounts.map((row) => (
                <option key={`${platform.id}:${row.account_id}`} value={`${platform.id}:${row.account_id}`}>
                  {platform.name}: {row.display_name}
                </option>
              ))
            })}
          </select>
        </article>
      ))}

      <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-tertiary">
        Discovery (Awesome)
      </div>
      <article className="border border-border-default p-3">
        <div className="flex flex-wrap gap-2">
          <input
            value={discoverQuery}
            onChange={(event) => setDiscoverQuery(event.target.value)}
            className="border border-border-default bg-void px-2 py-1 text-sm text-text-primary"
            placeholder="np. python"
          />
          <button
            type="button"
            disabled={busy === 'discover'}
            onClick={() => void runDiscover()}
            className="border border-border-default px-3 py-2 text-[11px] uppercase text-text-secondary"
          >
            Discover
          </button>
        </div>
        {discoverHits.map((hit) => (
          <p key={hit.url} className="mt-2 text-xs text-text-tertiary">
            {hit.name} · {hit.trust} · {hit.url}
          </p>
        ))}
      </article>

      <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-tertiary">OpenToonz</div>
      <article className="border border-border-default p-3">
        <p className="text-sm text-text-secondary">
          {opentoonz?.status || '…'}. {opentoonz?.detail}
        </p>
        <label className="mt-2 block text-xs text-text-tertiary">
          Ścieżka OpenToonz.exe
          <input
            value={openToonzPath}
            onChange={(event) => setOpenToonzPath(event.target.value)}
            placeholder="E:\Atnos\Tools\OpenToonz\OpenToonz.exe"
            className="mt-1 w-full border border-border-default bg-void px-2 py-1 text-sm text-text-primary"
          />
        </label>
        <button
          type="button"
          disabled={busy === 'opentoonz'}
          onClick={() => void saveOpenToonz()}
          className="mt-2 border border-border-default px-3 py-2 text-[11px] uppercase text-text-secondary"
        >
          Test / Save path
        </button>
      </article>

      <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-tertiary">Skills</div>
      {skills.map((skill) => (
        <article key={skill.id} className="flex flex-wrap items-center justify-between gap-2 border border-border-default p-3">
          <div>
            <div className="text-sm text-text-primary">{skill.id}</div>
            <p className="text-xs text-text-tertiary">
              {skill.classification}. Trust {skill.trust}. {skill.commit.slice(0, 12)}.
              {skill.executable ? ' Executable.' : ' Not executable until enabled.'}
              {skill.requires_data ? ' Requires data.' : ''}
              {skill.last_run ? ` Last run: ${skill.last_run.status}.` : ''}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void inspectSkill(skill.id)}
              className="border border-border-default px-3 py-2 text-[11px] uppercase text-text-secondary"
            >
              Inspect
            </button>
            <button
              type="button"
              onClick={() => void validateSkill(skill.id)}
              className="border border-border-default px-3 py-2 text-[11px] uppercase text-text-secondary"
            >
              Validate
            </button>
            <button
              type="button"
              onClick={() => void toggleSkill(skill.id, !skill.enabled)}
              className="border border-border-default px-3 py-2 text-[11px] uppercase text-text-secondary"
            >
              {skill.enabled ? 'Disable' : 'Enable'}
            </button>
          </div>
        </article>
      ))}
    </section>
  )
}

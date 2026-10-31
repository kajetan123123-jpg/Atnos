import { Bot, FileSearch, ListTodo, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  fetchProjectCommandOverview,
  verifyYouTubeChannelIdentityLive,
  type ProjectCommandOverview,
} from '../lib/api'
import { formatRelativeDateTime } from '../lib/format'
import { ProjectPlatformBindingsPanel } from './ProjectPlatformBindingsPanel'
import { YouTubeChannelSetupPanel } from './YouTubeChannelSetupPanel'

const QUICK_ACTIONS: Array<{ command: string; Icon: typeof Bot }> = [
  { command: 'Jaki jest stan projektu?', Icon: ShieldCheck },
  { command: 'Co obecnie blokuje projekt?', Icon: TriangleAlert },
  { command: 'Pokaż najbliższy krok i dowody.', Icon: FileSearch },
  { command: 'Uruchom testy backendu i pokaż dowody.', Icon: ShieldCheck },
  { command: 'Zleć lokalny przegląd dokumentacji projektu.', Icon: Bot },
]

const STATUS_STYLES: Record<string, string> = {
  verified: 'border-sem-success/40 bg-sem-success/10 text-sem-success',
  partially_verified: 'border-sem-warning/40 bg-sem-warning/10 text-sem-warning',
  blocked: 'border-sem-danger/40 bg-sem-danger/10 text-sem-danger',
  insufficient_evidence: 'border-border-default bg-panel/80 text-text-secondary',
}

function statusLabel(status: string) {
  switch (status) {
    case 'verified':
      return 'ZWERYFIKOWANY'
    case 'partially_verified':
      return 'CZĘŚCIOWO POTWIERDZONY'
    case 'blocked':
      return 'ZABLOKOWANY'
    case 'insufficient_evidence':
      return 'BRAK DOWODÓW'
    default:
      return status.replaceAll('_', ' ').toUpperCase()
  }
}

export function ProjectCommandPanel({
  projectId,
  projectName,
  refreshKey = 0,
  onCommand,
}: {
  projectId: number | null
  projectName: string
  refreshKey?: number
  onCommand: (command: string) => void
}) {
  const [overview, setOverview] = useState<ProjectCommandOverview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [identityProbeLoading, setIdentityProbeLoading] = useState(false)
  const [identityProbeError, setIdentityProbeError] = useState<string | null>(null)

  const refresh = async () => {
    if (projectId == null) {
      setOverview(null)
      setError(null)
      return
    }
    setLoading(true)
    try {
      const next = await fetchProjectCommandOverview(projectId)
      setOverview(next)
      setError(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nie udało się odczytać stanu projektu.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    // refresh is intentionally recreated from the active project identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, refreshKey])

  if (projectId == null) {
    return (
      <section className="shell-panel-strong border border-dashed border-border-default p-4" data-testid="project-command-panel">
        <div className="label-eyebrow">PROJECT COMMAND</div>
        <p className="mt-2 text-sm leading-6 text-text-secondary">
          Wybierz projekt, aby Atnos odpowiadał na podstawie jego health-checków, zadań, dowodów i wiedzy.
        </p>
      </section>
    )
  }

  const blockers = overview?.blockers ?? []
  const evidence = overview?.recent_evidence ?? []
  const workItems = overview?.work_items ?? []
  const delegations = overview?.delegations ?? []
  const runtime = overview?.runtime
  const brain = overview?.brain
  const brainState = brain?.state
  const brainFacts = brain?.facts ?? []
  const brainGoals = brain?.goals ?? []
  const rollbackPoints = overview?.rollback_points ?? []
  const knowledgeMatches = overview?.knowledge.library_matches ?? []
  const channelIdentity = overview?.channel_identity
  const channelRoutingConfigured = Boolean(channelIdentity?.identity)
  const channelEligible = channelIdentity?.publish_eligibility.eligible_by_local_policy ?? false
  const canProbeChannelIdentity = Boolean(channelIdentity?.identity?.oauth_connection_id)
  const liveProbe = channelIdentity?.identity?.verification_evidence?.live_probe as Record<string, unknown> | undefined
  const status = overview?.status ?? 'insufficient_evidence'

  const verifyChannelIdentity = async () => {
    if (projectId == null || !canProbeChannelIdentity) return
    setIdentityProbeLoading(true)
    setIdentityProbeError(null)
    try {
      await verifyYouTubeChannelIdentityLive(projectId)
      await refresh()
    } catch (caught) {
      setIdentityProbeError(caught instanceof Error ? caught.message : 'Nie udało się odczytać kanału przez YouTube API.')
    } finally {
      setIdentityProbeLoading(false)
    }
  }

  return (
    <section className="shell-panel-strong border border-border-default bg-panel/85 p-4 shadow-panel" data-testid="project-command-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="label-eyebrow">PROJECT COMMAND / LIVE</div>
          <h2 className="mt-1 text-base font-semibold text-text-primary">{projectName}</h2>
          <p className="mt-1 text-xs text-text-tertiary">
            Stan pochodzi z bieżących odczytów, nie z deklaracji konfiguracji.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded-pill border px-2.5 py-1 text-[11px] font-semibold ${STATUS_STYLES[status] ?? STATUS_STYLES.insufficient_evidence}`}>
            {statusLabel(status)}
          </span>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border-default px-2.5 py-1.5 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary disabled:opacity-50"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Odśwież
          </button>
        </div>
      </div>

      {error ? (
        <div className="mt-3 rounded-lg border border-sem-danger/40 bg-sem-danger/10 px-3 py-2 text-xs text-sem-danger">
          Nie udało się pobrać dowodów: {error}
        </div>
      ) : null}

      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-app/40 p-3">
          <div className="flex items-center gap-2 text-xs text-text-tertiary"><ShieldCheck size={14} /> RUNTIME</div>
          <div className="mt-2 text-lg font-semibold text-text-primary">{runtime?.status?.toUpperCase() ?? 'SPRAWDZANIE'}</div>
          <div className="mt-1 text-xs text-text-secondary">
            {runtime ? `${runtime.summary.ready} ready · ${runtime.summary.degraded + runtime.summary.blocked + runtime.summary.missing} wymaga uwagi` : 'Brak odczytu'}
          </div>
        </div>
        <div className="rounded-lg border border-border-default bg-app/40 p-3">
          <div className="flex items-center gap-2 text-xs text-text-tertiary"><TriangleAlert size={14} /> BLOKERY</div>
          <div className="mt-2 text-lg font-semibold text-text-primary">{blockers.length}</div>
          <div className="mt-1 text-xs text-text-secondary">Tylko blokery ze źródłem i czasem odczytu.</div>
        </div>
        <div className="rounded-lg border border-border-default bg-app/40 p-3">
          <div className="flex items-center gap-2 text-xs text-text-tertiary"><ListTodo size={14} /> ZADANIA</div>
          <div className="mt-2 text-lg font-semibold text-text-primary">{workItems.length}</div>
          <div className="mt-1 text-xs text-text-secondary">Aktywne elementy pamięci operacyjnej projektu.</div>
        </div>
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-[1.2fr_1fr]">
        <div className="rounded-lg border border-border-default bg-app/35 p-3">
          <div className="label-eyebrow">NASTĘPNY KROK</div>
          <p className="mt-2 text-sm leading-6 text-text-primary">
            {overview?.next_step.action ?? 'Pobierz aktualny stan projektu.'}
          </p>
          <p className="mt-1 text-xs text-text-tertiary">
            Źródło: {overview?.next_step.source ?? 'project_command'} · {overview?.next_step.reason ?? 'brak dodatkowych szczegółów'}
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-app/35 p-3">
          <div className="label-eyebrow">OSTATNI DOWÓD</div>
          {evidence[0] ? (
            <>
              <p className="mt-2 text-sm text-text-primary">{evidence[0].summary}</p>
              <p className="mt-1 text-xs text-text-tertiary">
                {evidence[0].source} · {evidence[0].checked_at ? formatRelativeDateTime(evidence[0].checked_at) : 'bez czasu'}
              </p>
            </>
          ) : <p className="mt-2 text-sm text-text-secondary">Brak zapisanego dowodu.</p>}
          {rollbackPoints[0] ? (
            <p className={`mt-2 text-xs ${rollbackPoints[0].status === 'verified' ? 'text-sem-success' : 'text-sem-warning'}`}>
              Snapshot zmiany #{rollbackPoints[0].id}: {rollbackPoints[0].status.replaceAll('_', ' ')}
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-[1.2fr_1fr]" data-testid="project-command-brain">
        <div className="rounded-lg border border-border-default bg-app/35 p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="label-eyebrow">PROJECT BRAIN</div>
            {brainState ? (
              <span className={`text-[10px] uppercase tracking-[0.12em] ${brainState.verified ? 'text-sem-success' : 'text-text-tertiary'}`}>
                {brainState.verified ? 'ŹRÓDŁA POTWIERDZONE' : 'CZĘŚCIOWE ŹRÓDŁA'}
              </span>
            ) : null}
          </div>
          {brainState ? (
            <>
              <p className="mt-2 text-sm leading-6 text-text-primary">{brainState.summary}</p>
              <p className="mt-1 text-xs text-text-tertiary">
                Faza: {brainState.phase} · odczyt {formatRelativeDateTime(brainState.observed_at)}
              </p>
              {brainState.open_questions.length > 0 ? (
                <p className="mt-2 text-xs leading-5 text-sem-warning">
                  Otwarte: {brainState.open_questions[0].question}
                </p>
              ) : null}
            </>
          ) : <p className="mt-2 text-sm text-text-secondary">Stan zostanie zapisany przy następnym poprawnym odczycie źródeł.</p>}
        </div>
        <div className="rounded-lg border border-border-default bg-app/35 p-3">
          <div className="label-eyebrow">CELE I FAKTY</div>
          <div className="mt-2 text-sm text-text-primary">
            {brainGoals.length > 0 ? brainGoals[0].title : 'Brak deklarowanego celu projektu.'}
          </div>
          <p className="mt-1 text-xs leading-5 text-text-secondary">
            {brainGoals.length > 0 ? `${brainGoals[0].source} · deklaracja projektu` : 'Dodaj opis projektu, aby utworzyć cel ze źródłem.'}
          </p>
          <p className="mt-3 text-xs text-text-tertiary">
            {brainFacts.length} faktów z aktualnym źródłem · {brainFacts.filter((fact) => fact.verification_status === 'verified').length} zweryfikowanych
          </p>
        </div>
      </div>

      {channelRoutingConfigured ? (
        <div className="mt-4 rounded-lg border border-border-default bg-app/35 p-3" data-testid="project-command-channel-identity">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="label-eyebrow">CHANNEL IDENTITY / ROUTING</div>
              <p className="mt-2 text-sm font-medium text-text-primary">
                {channelIdentity?.identity?.display_name ?? channelIdentity?.identity?.account_binding_key}
              </p>
              <p className="mt-1 font-mono text-xs text-text-secondary">
                {channelIdentity?.identity?.youtube_channel_id ?? 'Brak stabilnego ID kanału (UC...)'}
              </p>
            </div>
            <span className={`rounded-pill border px-2.5 py-1 text-[10px] font-semibold ${channelEligible ? STATUS_STYLES.verified : STATUS_STYLES.blocked}`}>
              {channelEligible ? 'ROUTING GOTOWY LOKALNIE' : 'UPLOAD ZABLOKOWANY'}
            </span>
          </div>
          <div className="mt-3 grid gap-2 text-xs text-text-secondary md:grid-cols-3">
            <div className="rounded-md border border-border-default/80 bg-panel/60 px-2.5 py-2">
              Tożsamość: {channelIdentity?.identity?.owner_confirmed ? 'potwierdzona przez właściciela' : 'wymaga potwierdzenia'}
            </div>
            <div className="rounded-md border border-border-default/80 bg-panel/60 px-2.5 py-2">
              Manifest: {channelIdentity?.manifest.status ?? 'nie rozpoczęto'}
            </div>
            <div className="rounded-md border border-border-default/80 bg-panel/60 px-2.5 py-2">
              Polityka: {channelIdentity?.publishing_policy.status ?? 'nie rozpoczęto'}
            </div>
          </div>
          {!channelEligible && channelIdentity?.publish_eligibility.reasons[0] ? (
            <p className="mt-3 text-xs leading-5 text-sem-warning">
              {channelIdentity.publish_eligibility.reasons[0].detail}
            </p>
          ) : null}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void verifyChannelIdentity()}
              disabled={!canProbeChannelIdentity || identityProbeLoading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border-default bg-panel px-2.5 py-1.5 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw size={13} className={identityProbeLoading ? 'animate-spin' : ''} />
              Sprawdź identyfikator przez YouTube
            </button>
            <span className="text-[11px] text-text-tertiary">
              {liveProbe?.checked_at ? `Live probe: ${formatRelativeDateTime(String(liveProbe.checked_at))}` : 'Live probe nie został wykonany.'}
            </span>
          </div>
          {identityProbeError ? (
            <p className="mt-2 text-xs leading-5 text-sem-danger">{identityProbeError}</p>
          ) : null}
          <p className="mt-2 text-[11px] leading-5 text-text-tertiary">
            Status dotyczy lokalnego routingu kanału. Nie zastępuje live probe OAuth ani nie oznacza wykonanego uploadu.
          </p>
        </div>
      ) : null}

      {channelIdentity ? (
        <YouTubeChannelSetupPanel
          projectId={projectId}
          snapshot={channelIdentity}
          onUpdated={refresh}
        />
      ) : null}

      <ProjectPlatformBindingsPanel projectId={projectId} />

      {knowledgeMatches[0] ? (
        <div className="mt-4 rounded-lg border border-border-default bg-app/35 p-3" data-testid="project-command-knowledge-evidence">
          <div className="label-eyebrow">MATERIAŁ ŹRÓDŁOWY</div>
          <p className="mt-2 text-sm font-medium text-text-primary">{knowledgeMatches[0].title}</p>
          <p className="mt-1 text-xs leading-5 text-text-secondary">
            {knowledgeMatches[0].citation ?? `knowledge/${knowledgeMatches[0].path}`}
          </p>
        </div>
      ) : null}

      {blockers.length > 0 ? (
        <div className="mt-4 rounded-lg border border-sem-warning/35 bg-sem-warning/5 p-3">
          <div className="label-eyebrow text-sem-warning">ZWERYFIKOWANE BLOKERY</div>
          <div className="mt-2 space-y-2">
            {blockers.slice(0, 3).map((blocker) => (
              <div key={`${blocker.source}-${blocker.key}`} className="rounded-md border border-border-default/80 bg-app/50 px-3 py-2">
                <div className="text-sm font-medium text-text-primary">{blocker.title}</div>
                <div className="mt-1 text-xs leading-5 text-text-secondary">{blocker.detail}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-4 grid gap-3 xl:grid-cols-2">
        <div className="rounded-lg border border-border-default bg-app/35 p-3" data-testid="project-command-work-items">
          <div className="label-eyebrow">AKTYWNE ZADANIA</div>
          {workItems.length > 0 ? (
            <div className="mt-2 space-y-2">
              {workItems.slice(0, 3).map((item) => (
                <div key={item.id} className="rounded-md border border-border-default/80 bg-app/50 px-3 py-2">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-sm font-medium text-text-primary">{item.title}</span>
                    <span className="text-[10px] uppercase tracking-[0.12em] text-text-tertiary">{item.status}</span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-text-secondary">{item.next_step ?? item.summary}</p>
                </div>
              ))}
            </div>
          ) : <p className="mt-2 text-sm text-text-secondary">Brak aktywnych zadań zapisanych dla projektu.</p>}
        </div>

        <div className="rounded-lg border border-border-default bg-app/35 p-3" data-testid="project-command-delegations">
          <div className="label-eyebrow">DELEGACJE</div>
          {delegations.length > 0 ? (
            <div className="mt-2 space-y-2">
              {delegations.slice(0, 3).map((delegation) => (
                <div key={delegation.id} className="rounded-md border border-border-default/80 bg-app/50 px-3 py-2">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-sm font-medium text-text-primary">#{delegation.id} · {delegation.worker_key ?? 'nieprzypisany'}</span>
                    <span className="text-[10px] uppercase tracking-[0.12em] text-text-tertiary">{delegation.status}</span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-text-secondary">
                    {delegation.result_summary ?? delegation.blocked_reason ?? delegation.error_message ?? 'Oczekuje na wynik workera.'}
                  </p>
                </div>
              ))}
            </div>
          ) : <p className="mt-2 text-sm text-text-secondary">Brak delegacji dla tego projektu.</p>}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2 border-t border-border-default pt-3">
        {QUICK_ACTIONS.map(({ command, Icon }) => {
          return (
            <button
              key={command}
              type="button"
              onClick={() => onCommand(command)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border-default bg-panel px-2.5 py-2 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
            >
              <Icon size={13} />
              {command}
            </button>
          )
        })}
      </div>
    </section>
  )
}

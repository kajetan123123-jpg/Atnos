import { Link } from 'react-router-dom'
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, CheckCircle2, CircleDollarSign, Film, Lock, RefreshCw, ShieldCheck } from 'lucide-react'
import {
  ensureProductionStyleGuide,
  fetchProductionPublicQualityStatus,
  fetchProjectReadiness,
  runProductionVisualBackendCheck,
  runProductionVoiceProviderCheck,
  type ProductionPublicQualityStatusPayload,
  type ProjectReadinessPayload,
  type ProjectReadinessProject,
} from '../lib/api'
import { useAccountStore } from '../stores/accountStore'
import type { Account, AccountType } from '../types/contentMachine'

type ProjectFilter = 'all' | 'youtube' | 'synthetic_creator' | 'investment'
type StatusFilter = 'all' | 'ready' | 'limited' | 'blocked'

interface ProjectControlRow {
  projectId: string
  projectType: string
  name: string
  description: string
  status: string
  safeMode: boolean
  capabilities: ProjectReadinessProject['capabilities']
  blockers: string[]
  nextAction: string
  requirementReady: number
  requirementTotal: number
  readiness: ProjectReadinessProject | null
  account: Account | null
}

type QualityByProject = Record<string, ProductionPublicQualityStatusPayload | null>

const PROJECT_TYPE_LABELS: Record<string, string> = {
  youtube: 'YouTube Shorts',
  synthetic_creator: 'Synthetic creator',
  investment: 'Portfel inwestycyjny',
  instagram_reels: 'Instagram Reels',
  tiktok: 'TikTok',
  onlyfans: 'Creator platform',
  custom: 'Projekt własny',
}

const ACCOUNT_TYPE_TO_PROJECT_TYPE: Record<AccountType, string> = {
  youtube: 'youtube',
  instagram_reels: 'instagram_reels',
  tiktok: 'tiktok',
  onlyfans: 'synthetic_creator',
  investment: 'investment',
  custom: 'custom',
}

function statusClass(status: string) {
  if (status === 'ready') return 'border-success/50 bg-success/10 text-success'
  if (status === 'blocked' || status === 'missing') return 'border-error/50 bg-error/10 text-error'
  return 'border-warning/50 bg-warning/10 text-warning'
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    ready: 'Gotowe',
    limited: 'Ograniczone',
    blocked: 'Blokada',
    missing: 'Brak danych',
  }
  return labels[status] ?? status.replaceAll('_', ' ')
}

function projectTypeLabel(projectType: string) {
  return PROJECT_TYPE_LABELS[projectType] ?? projectType.replaceAll('_', ' ')
}

function projectTypeIcon(projectType: string) {
  if (projectType === 'investment') return CircleDollarSign
  if (projectType === 'synthetic_creator') return ShieldCheck
  if (projectType === 'youtube' || projectType === 'instagram_reels' || projectType === 'tiktok') return Film
  return Lock
}

function boolClass(value: boolean) {
  return value ? 'border-success/40 bg-success/10 text-success' : 'border-border-default bg-void text-text-tertiary'
}

function boolLabel(value: boolean) {
  return value ? 'Tak' : 'Nie'
}

function summarizeReadiness(projects: ProjectControlRow[]) {
  return {
    total: projects.length,
    ready: projects.filter((project) => project.status === 'ready').length,
    limited: projects.filter((project) => project.status === 'limited').length,
    blocked: projects.filter((project) => project.status === 'blocked').length,
    youtube: projects.filter((project) => project.projectType === 'youtube').length,
  }
}

function qualityStatusClass(quality: ProductionPublicQualityStatusPayload | null | undefined) {
  if (!quality) return 'border-text-tertiary/30 bg-elevated/40 text-text-secondary'
  return quality.ready ? 'border-success/50 bg-success/10 text-success' : 'border-error/50 bg-error/10 text-error'
}

function qualityStatusLabel(quality: ProductionPublicQualityStatusPayload | null | undefined) {
  if (!quality) return 'Brak danych'
  return quality.ready ? 'Public-ready' : 'Blokada jakości'
}

function qualityNextAction(quality: ProductionPublicQualityStatusPayload | null | undefined) {
  if (!quality) return 'Uruchom backendowy public-quality check dla tego projektu.'
  return quality.next_actions[0] ?? 'Quality gate nie zgłasza następnej akcji.'
}

function fallbackRow(account: Account): ProjectControlRow {
  const projectType = ACCOUNT_TYPE_TO_PROJECT_TYPE[account.type] ?? account.type
  const canDraft = account.type === 'youtube'
  return {
    projectId: account.id,
    projectType,
    name: account.name,
    description: account.niche || 'Projekt bez opisu.',
    status: account.status === 'error' ? 'blocked' : account.status === 'live' ? 'limited' : 'limited',
    safeMode: true,
    capabilities: {
      can_research: canDraft,
      can_generate_private: canDraft,
      can_upload_private: false,
      can_publish_public: false,
      can_trade_or_purchase: false,
    },
    blockers: ['Brak pełnego snapshotu backend readiness dla tego projektu.'],
    nextAction: account.type === 'investment'
      ? 'Otwórz kontrolę projektu i uzupełnij watchlistę lub brokera read-only.'
      : account.type === 'onlyfans'
        ? 'Otwórz kontrolę projektu i skonfiguruj politykę synthetic creator.'
        : 'Otwórz kontrolę projektu i sprawdź brakujące dostępy.',
    requirementReady: 0,
    requirementTotal: 0,
    readiness: null,
    account,
  }
}

function mergeProjects(readiness: ProjectReadinessPayload | null, accounts: Account[]): ProjectControlRow[] {
  const rows = new Map<string, ProjectControlRow>()

  for (const project of readiness?.projects ?? []) {
    const account = accounts.find((item) => item.id === project.project_id) ?? null
    rows.set(project.project_id, {
      projectId: project.project_id,
      projectType: project.project_type,
      name: project.name,
      description: project.description,
      status: project.status,
      safeMode: project.safe_mode,
      capabilities: project.capabilities,
      blockers: project.blockers,
      nextAction: project.next_action,
      requirementReady: project.requirements.filter((item) => item.status === 'ready').length,
      requirementTotal: project.requirements.length,
      readiness: project,
      account,
    })
  }

  for (const account of accounts) {
    if (!rows.has(account.id)) {
      rows.set(account.id, fallbackRow(account))
    }
  }

  return Array.from(rows.values()).sort((a, b) => {
    const rank = { youtube: 0, synthetic_creator: 1, investment: 2 } as Record<string, number>
    return (rank[a.projectType] ?? 9) - (rank[b.projectType] ?? 9) || a.name.localeCompare(b.name)
  })
}

function ProjectCapabilityPill({ label, value }: { label: string; value: boolean }) {
  return (
    <div className={`border px-2 py-2 ${boolClass(value)}`}>
      <div className="font-mono text-[9px] uppercase text-current/70">{label}</div>
      <div className="mt-1 font-display text-[12px] font-semibold uppercase">{boolLabel(value)}</div>
    </div>
  )
}

function ProjectQualityPanel({
  projectType,
  quality,
  voiceActionRunning,
  visualActionRunning,
  styleActionRunning,
  onRunVoiceCheck,
  onRunVisualCheck,
  onEnsureStyleGuide,
}: {
  projectType: string
  quality: ProductionPublicQualityStatusPayload | null | undefined
  voiceActionRunning: boolean
  visualActionRunning: boolean
  styleActionRunning: boolean
  onRunVoiceCheck: () => void
  onRunVisualCheck: () => void
  onEnsureStyleGuide: () => void
}) {
  if (projectType !== 'youtube') {
    return (
      <div className="border border-border-default bg-void p-3">
        <div className="font-mono text-[9px] uppercase text-text-tertiary">Jakość produkcji</div>
        <div className="mt-1 text-xs leading-relaxed text-text-secondary">
          Ten typ projektu ma osobny cockpit jakości w swoim panelu szczegółowym.
        </div>
      </div>
    )
  }

  return (
    <div className={`border p-3 ${qualityStatusClass(quality)}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="font-mono text-[9px] uppercase text-current/70">Public-quality gate</div>
          <div className="mt-1 font-display text-[13px] font-semibold uppercase">{qualityStatusLabel(quality)}</div>
        </div>
        <span className="border border-current/30 px-2 py-1 font-mono text-[9px] uppercase">
          {quality?.blockers.length ?? 0} blokad
        </span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        {[
          ['Głos', quality?.voice_provider.public_voice_ready ?? false],
          ['Wizual', quality?.visual_backend.public_visual_ready ?? false],
          ['Style guide', quality?.style_guide.ready ?? false],
        ].map(([label, value]) => (
          <div key={String(label)} className="border border-current/20 bg-void/60 p-2">
            <div className="font-mono text-[9px] uppercase text-current/70">{label}</div>
            <div className="mt-1 font-display text-[11px] font-semibold uppercase">{boolLabel(Boolean(value))}</div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs leading-relaxed text-current/90">{qualityNextAction(quality)}</p>
      {quality?.blockers.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {quality.blockers.slice(0, 3).map((blocker) => (
            <span key={blocker} className="border border-current/30 px-2 py-1 font-mono text-[9px] uppercase">
              {blocker.replaceAll('_', ' ')}
            </span>
          ))}
        </div>
      ) : null}
      <div className="mt-3 grid gap-2 md:grid-cols-3">
        <button
          type="button"
          onClick={onRunVoiceCheck}
          disabled={voiceActionRunning}
          className="border border-current/40 bg-void/70 px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {voiceActionRunning ? 'Sprawdzam głos...' : 'Sprawdź głos'}
        </button>
        <button
          type="button"
          onClick={onRunVisualCheck}
          disabled={visualActionRunning}
          className="border border-current/40 bg-void/70 px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {visualActionRunning ? 'Sprawdzam wideo...' : 'Sprawdź backend wideo'}
        </button>
        <button
          type="button"
          onClick={onEnsureStyleGuide}
          disabled={styleActionRunning}
          className="border border-current/40 bg-void/70 px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {styleActionRunning ? 'Tworzę guide...' : 'Utwórz style guide'}
        </button>
      </div>
    </div>
  )
}

function ProjectCard({
  row,
  quality,
  qualityActionKey,
  onRunVoiceCheck,
  onRunVisualCheck,
  onEnsureStyleGuide,
}: {
  row: ProjectControlRow
  quality: ProductionPublicQualityStatusPayload | null | undefined
  qualityActionKey: string | null
  onRunVoiceCheck: (projectId: string) => void
  onRunVisualCheck: (projectId: string) => void
  onEnsureStyleGuide: (row: ProjectControlRow) => void
}) {
  const Icon = projectTypeIcon(row.projectType)
  const requirementsReady =
    row.requirementTotal > 0 ? `${row.requirementReady}/${row.requirementTotal}` : 'local'

  return (
    <article className="border border-border-default bg-panel">
      <div className="flex items-start justify-between gap-3 border-b border-border-default p-4">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center border border-border-default bg-void text-accent-primary">
            <Icon size={18} />
          </div>
          <div className="min-w-0">
            <div className="font-display text-[15px] font-semibold uppercase tracking-[0.12em] text-text-primary">
              {row.name}
            </div>
            <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.16em] text-text-tertiary">
              {projectTypeLabel(row.projectType)} / {row.projectId}
            </div>
          </div>
        </div>
        <span className={`shrink-0 border px-2 py-1 font-mono text-[10px] uppercase ${statusClass(row.status)}`}>
          {statusLabel(row.status)}
        </span>
      </div>

      <div className="space-y-4 p-4">
        <p className="min-h-10 text-sm leading-relaxed text-text-secondary">{row.description}</p>

        <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
          <ProjectCapabilityPill label="Research" value={row.capabilities.can_research} />
          <ProjectCapabilityPill label="Draft" value={row.capabilities.can_generate_private} />
          <ProjectCapabilityPill label="Private" value={row.capabilities.can_upload_private} />
          <ProjectCapabilityPill label="Public" value={row.capabilities.can_publish_public} />
          <ProjectCapabilityPill label="Trade/buy" value={row.capabilities.can_trade_or_purchase} />
        </div>

        <ProjectQualityPanel
          projectType={row.projectType}
          quality={quality}
          voiceActionRunning={qualityActionKey === `${row.projectId}:voice`}
          visualActionRunning={qualityActionKey === `${row.projectId}:visual`}
          styleActionRunning={qualityActionKey === `${row.projectId}:style`}
          onRunVoiceCheck={() => onRunVoiceCheck(row.projectId)}
          onRunVisualCheck={() => onRunVisualCheck(row.projectId)}
          onEnsureStyleGuide={() => onEnsureStyleGuide(row)}
        />

        <div className="grid gap-3 md:grid-cols-[140px_1fr]">
          <div className="border border-border-default bg-void p-3">
            <div className="font-mono text-[9px] uppercase text-text-tertiary">Wymagania</div>
            <div className="mt-1 font-display text-lg font-semibold uppercase text-accent-primary">
              {requirementsReady}
            </div>
            <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">
              {row.safeMode ? 'Safe-mode' : 'Auto-mode'}
            </div>
          </div>
          <div className="border border-border-default bg-void p-3">
            <div className="font-mono text-[9px] uppercase text-text-tertiary">Następny ruch</div>
            <div className="mt-1 text-sm leading-relaxed text-text-primary">{row.nextAction}</div>
          </div>
        </div>

        {row.blockers.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {row.blockers.slice(0, 4).map((blocker) => (
              <span key={blocker} className="border border-warning/30 px-2 py-1 font-mono text-[9px] uppercase text-warning">
                {blocker}
              </span>
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-success">
            <CheckCircle2 size={14} />
            Brak aktywnych blokad w snapshotcie.
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border-default pt-3">
          <div className="font-mono text-[10px] uppercase text-text-tertiary">
            Konto lokalne: {row.account ? row.account.name : 'brak w store'}
          </div>
          <Link
            to={`/projects/${encodeURIComponent(row.projectId)}`}
            className="inline-flex items-center gap-2 border border-accent-primary/60 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
          >
            Otwórz kontrolę
            <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </article>
  )
}

export default function ProjectsCommandCenterRoute() {
  const accounts = useAccountStore((state) => state.accounts)
  const [readiness, setReadiness] = useState<ProjectReadinessPayload | null>(null)
  const [qualityByProject, setQualityByProject] = useState<QualityByProject>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [qualityActionKey, setQualityActionKey] = useState<string | null>(null)
  const [qualityActionMessage, setQualityActionMessage] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<ProjectFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  const loadReadiness = async () => {
    setLoading(true)
    setError(null)
    try {
      const nextReadiness = await fetchProjectReadiness()
      setReadiness(nextReadiness)
      const youtubeProjects = nextReadiness.projects.filter((project) => project.project_type === 'youtube')
      const qualityPairs = await Promise.all(
        youtubeProjects.map(async (project) => {
          try {
            return [project.project_id, await fetchProductionPublicQualityStatus(project.project_id)] as const
          } catch {
            return [project.project_id, null] as const
          }
        }),
      )
      setQualityByProject(Object.fromEntries(qualityPairs))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Nie udało się pobrać readiness projektów.')
      setReadiness(null)
      setQualityByProject({})
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadReadiness()
  }, [])

  const runQualityRepairAction = async (
    projectId: string,
    actionType: 'voice' | 'visual' | 'style',
    action: () => Promise<{ status?: unknown; ready?: unknown }>,
  ) => {
    const actionKey = `${projectId}:${actionType}`
    setQualityActionKey(actionKey)
    setQualityActionMessage(null)
    setError(null)
    try {
      const result = await action()
      const status = String(result.status ?? result.ready ?? 'done')
      const label = actionType === 'voice' ? 'Głos' : actionType === 'visual' ? 'Backend wideo' : 'Style guide'
      setQualityActionMessage(
        `${label}: ${status}. Odświeżono bramki jakości.`,
      )
      await loadReadiness()
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Akcja quality gate nie powiodła się.')
    } finally {
      setQualityActionKey(null)
    }
  }

  const rows = useMemo(() => mergeProjects(readiness, accounts), [accounts, readiness])
  const summary = useMemo(() => summarizeReadiness(rows), [rows])
  const visibleRows = useMemo(
    () =>
      rows.filter((row) => {
        const typeMatches = typeFilter === 'all' || row.projectType === typeFilter
        const statusMatches = statusFilter === 'all' || row.status === statusFilter
        return typeMatches && statusMatches
      }),
    [rows, statusFilter, typeFilter],
  )

  return (
    <div className="space-y-4 p-4 md:p-6">
      <header className="border border-border-default bg-panel p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-text-tertiary">
              Project Command Center
            </div>
            <h1 className="mt-2 font-display text-2xl font-semibold uppercase tracking-[0.12em] text-text-primary">
              Centrum kontroli projektów
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-text-secondary">
              Jeden widok dla trzech kanałów Shorts, synthetic creator i portfela inwestycyjnego. Każdy projekt ma osobne
              bramki dostępu, tryb safe-mode i następny ruch operacyjny.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadReadiness()}
            className="inline-flex items-center gap-2 border border-accent-primary/60 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Odśwież
          </button>
        </div>
      </header>

      <section className="grid gap-3 md:grid-cols-5">
        {[
          ['Projekty', summary.total],
          ['Gotowe', summary.ready],
          ['Ograniczone', summary.limited],
          ['Blokady', summary.blocked],
          ['Kanały YT', summary.youtube],
        ].map(([label, value]) => (
          <div key={String(label)} className="border border-border-default bg-panel p-4">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">{label}</div>
            <div className="mt-1 font-display text-2xl font-semibold uppercase text-accent-primary">{value}</div>
          </div>
        ))}
      </section>

      <section className="border border-border-default bg-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <AlertTriangle size={16} className="text-warning" />
            Publiczny upload, realne zakupy i realny trading pozostają zablokowane bez osobnych bramek jakości i zgody.
          </div>
          <div className="flex flex-wrap gap-2">
            <select
              value={typeFilter}
              onChange={(event) => setTypeFilter(event.target.value as ProjectFilter)}
              className="border border-border-default bg-void px-3 py-2 font-mono text-[11px] uppercase text-text-secondary"
            >
              <option value="all">Wszystkie typy</option>
              <option value="youtube">YouTube</option>
              <option value="synthetic_creator">Synthetic creator</option>
              <option value="investment">Inwestycje</option>
            </select>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
              className="border border-border-default bg-void px-3 py-2 font-mono text-[11px] uppercase text-text-secondary"
            >
              <option value="all">Wszystkie statusy</option>
              <option value="ready">Gotowe</option>
              <option value="limited">Ograniczone</option>
              <option value="blocked">Blokady</option>
            </select>
          </div>
        </div>
        {error ? (
          <div className="mt-3 border border-warning/40 bg-warning/10 p-3 text-xs leading-relaxed text-warning">
            Backend readiness offline: {error}. Pokazuję lokalny fallback kont.
          </div>
        ) : null}
        {qualityActionMessage ? (
          <div className="mt-3 border border-success/40 bg-success/10 p-3 text-xs leading-relaxed text-success">
            {qualityActionMessage}
          </div>
        ) : null}
      </section>

      {readiness?.money_machine.global_blockers?.length ? (
        <section className="border border-error/30 bg-error/5 p-4">
          <div className="font-mono text-[10px] uppercase text-error">Globalne blokady maszyny</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {readiness.money_machine.global_blockers.map((blocker) => (
              <span key={blocker} className="border border-error/30 px-2 py-1 font-mono text-[10px] uppercase text-error">
                {blocker}
              </span>
            ))}
          </div>
        </section>
      ) : null}

      <section className="grid gap-4 xl:grid-cols-2">
        {visibleRows.map((row) => (
          <ProjectCard
            key={row.projectId}
            row={row}
            quality={qualityByProject[row.projectId]}
            qualityActionKey={qualityActionKey}
            onRunVoiceCheck={(projectId) =>
              void runQualityRepairAction(projectId, 'voice', runProductionVoiceProviderCheck)
            }
            onRunVisualCheck={(projectId) =>
              void runQualityRepairAction(projectId, 'visual', runProductionVisualBackendCheck)
            }
            onEnsureStyleGuide={(project) =>
              void runQualityRepairAction(project.projectId, 'style', () =>
                ensureProductionStyleGuide({
                  accountId: project.projectId,
                  channelName: project.name,
                  niche: project.description,
                }),
              )
            }
          />
        ))}
        {!visibleRows.length ? (
          <div className="border border-dashed border-border-default p-8 text-center text-sm text-text-tertiary xl:col-span-2">
            Brak projektów dla wybranego filtra.
          </div>
        ) : null}
      </section>
    </div>
  )
}

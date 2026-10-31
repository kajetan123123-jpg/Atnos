import { Link, useParams } from 'react-router-dom'
import { useEffect, useMemo, useState } from 'react'
import {
  addInvestmentWatchlistItem,
  bootstrapInvestmentEducationSources,
  createLatestDistributionPackage,
  createAccountCredential,
  ensureDefaultCreatorCockpit,
  fetchCreatorCockpit,
  fetchDistributionCockpit,
  fetchInvestmentCockpit,
  fetchMoneyMachineSummary,
  fetchProductionOperatingStandards,
  fetchProductionPublicQualityStatus,
  fetchProductionQualityRemediationStatus,
  fetchProductionQualityReviewStatus,
  fetchProductionJobs,
  fetchProjectReadiness,
  fetchTopicQueue,
  generateTopics,
  recordInvestmentPaperTrade,
  runIntelligenceCollectors,
  runInvestmentEducationResearch,
  runInvestmentPaperAutopilot,
  runProductionContentLoopOnce,
  runProductionQualityRemediationOnce,
  runProductionQualityReviewOnce,
  seedCreatorCockpitWorkItems,
  uploadProductionJobPrivate,
  type CreatorCockpitStatus,
  type DistributionCockpitPayload,
  type InvestmentCockpitStatus,
  type MoneyMachineOperatorSummaryPayload,
  type MoneyMachineSummaryJob,
  type ProductionJobPayload,
  type ProductionOperatingStandardsPayload,
  type ProductionPublicQualityStatusPayload,
  type ProductionQualityRemediationRunResult,
  type ProductionQualityRemediationStatusPayload,
  type ProductionQualityReviewRunResult,
  type ProductionQualityReviewStatusPayload,
  type ProjectDistributionTarget,
  type ProjectReadinessProject,
  type ProjectToolingItem,
  type ProjectToolingPlan,
  type TopicQueueItem,
} from '../lib/api'
import { useAccountStore } from '../stores/accountStore'
import type { Account } from '../types/contentMachine'

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border border-border-default bg-panel">
      <div className="border-b border-border-default px-4 py-3 font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
        {title}
      </div>
      {children}
    </section>
  )
}

function decodeProjectId(value: string | undefined) {
  if (!value) return ''
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function statusClass(status: string | undefined) {
  if (status === 'ready' || status === 'active') return 'border-success/50 bg-success/10 text-success'
  if (status === 'limited' || status === 'degraded' || status === 'baseline' || status === 'planned') return 'border-warning/50 bg-warning/10 text-warning'
  if (status === 'blocked' || status === 'missing' || status === 'blocked_until_configured') return 'border-error/50 bg-error/10 text-error'
  return 'border-text-tertiary/30 bg-elevated/40 text-text-secondary'
}

function statusLabel(status: string | undefined) {
  const labels: Record<string, string> = {
    ready: 'Gotowe',
    limited: 'Ograniczone',
    blocked: 'Blokada',
    missing: 'Brak',
    degraded: 'Ograniczone',
    active: 'Aktywne',
    baseline: 'Bazowe',
    planned: 'Planowane',
    blocked_until_configured: 'Blokada do konfiguracji',
  }
  return status ? labels[status] ?? status.replaceAll('_', ' ') : 'Brak danych'
}

function decisionClass(state: string | undefined) {
  if (state === 'ready_public_review' || state === 'ready_private_upload_review') {
    return 'border-success/50 bg-success/10'
  }
  if (state === 'can_generate_private' || state === 'research_only' || state === 'research_paper_only' || state === 'drafts_allowed') {
    return 'border-accent-primary/50 bg-accent-primary/10'
  }
  if (state === 'blocked_public_quality' || state === 'needs_creator_policy' || state === 'needs_account_access') {
    return 'border-warning/50 bg-warning/10'
  }
  return 'border-border-default bg-panel'
}

function riskClass(value: string | undefined) {
  if (value === 'low') return 'text-success'
  if (value === 'medium') return 'text-warning'
  if (value === 'high') return 'text-error'
  return 'text-text-secondary'
}

function riskLabel(value: string | undefined) {
  const labels: Record<string, string> = {
    low: 'Niskie',
    medium: 'Średnie',
    high: 'Wysokie',
  }
  return value ? labels[value] ?? value.replaceAll('_', ' ') : 'Brak'
}

function modeLabel(mode: string | undefined) {
  const labels: Record<string, string> = {
    supervised_private_upload: 'Nadzorowany prywatny upload',
    draft_only_until_creator_access: 'Drafty do czasu podłączenia kont',
    research_only_until_broker_read_access: 'Research do czasu read-only brokera',
    local_setup_required: 'Wymaga lokalnej konfiguracji',
  }
  return mode ? labels[mode] ?? mode.replaceAll('_', ' ') : 'Brak trybu'
}

function boolLabel(value: boolean | undefined) {
  return value ? 'Tak' : 'Nie'
}

function boolClass(value: boolean | undefined) {
  return value ? 'text-success' : 'text-text-tertiary'
}

function formatMoney(value: number | null | undefined) {
  return `${Number(value ?? 0).toFixed(2)} PLN`
}

function formatMarketPrice(value: number | null | undefined, currency?: string | null) {
  return `${Number(value ?? 0).toFixed(4)} ${currency || ''}`.trim()
}

function formatQuantity(value: number | null | undefined) {
  return Number(value ?? 0).toLocaleString('pl-PL', { maximumFractionDigits: 6 })
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return 'brak'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('pl-PL', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatQualityBlocker(value: string) {
  const labels: Record<string, string> = {
    semantic_visual_thumbnail_flag_childish: 'Miniaturka wygląda zbyt dziecinnie',
    semantic_visual_thumbnail_flag_generic_blur: 'Miniaturka jest generyczna albo rozmyta',
    semantic_visual_thumbnail_flag_off_topic: 'Miniaturka nie pasuje do tematu',
    semantic_visual_thumbnail_flag_unprofessional: 'Miniaturka wygląda nieprofesjonalnie',
    semantic_visual_thumbnail_low_score: 'Miniaturka nie przechodzi oceny wizualnej',
    semantic_first_frame_flag_childish: 'Pierwsza klatka wygląda zbyt dziecinnie',
    semantic_first_frame_flag_generic_blur: 'Pierwsza klatka jest generyczna albo rozmyta',
    semantic_first_frame_flag_off_topic: 'Pierwsza klatka nie pasuje do tematu',
    semantic_first_frame_flag_unprofessional: 'Pierwsza klatka wygląda nieprofesjonalnie',
    semantic_first_frame_low_score: 'Pierwsza klatka nie przechodzi oceny wizualnej',
    thumbnail_quality_low_contrast: 'Za niski kontrast miniaturki',
    thumbnail_quality_blurry: 'Miniaturka jest zbyt rozmyta',
    thumbnail_quality_low_edge_strength: 'Miniaturka nie ma czytelnych kształtów',
    thumbnail_quality_low_saturation: 'Miniaturka jest zbyt płaska kolorystycznie',
    premium_voice_required: 'Brakuje premium voice',
    premium_visual_backend_required: 'Brakuje premium visual backendu',
  }
  return labels[value] ?? value.replaceAll('_', ' ')
}

function qualityRunSummary(result: ProductionQualityReviewRunResult | null) {
  if (!result) return 'Brak ręcznego uruchomienia w tej sesji.'
  return `Sprawdzono ${result.checked_count}, zablokowano ${result.blocked_count}, przepuszczono ${result.passed_count}.`
}

function remediationRunSummary(result: ProductionQualityRemediationRunResult | null) {
  if (!result) return 'Brak ręcznej auto-naprawy w tej sesji.'
  return `Zaplanowano ${result.planned_count}, uruchomiono ${result.started_count}. Status: ${result.status}.`
}

function readinessPillClass(value: boolean | null | undefined) {
  if (value === true) return 'border-success/50 bg-success/10 text-success'
  if (value === false) return 'border-error/50 bg-error/10 text-error'
  return 'border-text-tertiary/30 bg-elevated/40 text-text-secondary'
}

function priceSourceClass(value: string | undefined) {
  if (value === 'xtb_demo_quotes') return 'text-success'
  if (value === 'synthetic_fallback') return 'text-warning'
  return 'text-text-tertiary'
}

function actionClass(value: string | undefined) {
  if (value === 'paper_buy') return 'text-success'
  if (value === 'paper_sell') return 'text-warning'
  if (value?.startsWith('rejected')) return 'text-error'
  return 'text-text-secondary'
}

function splitConfigList(value: unknown): string[] {
  const raw = Array.isArray(value) ? value.join('\n') : String(value ?? '')
  return raw
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function projectNiches(account: Account | undefined, readiness: ProjectReadinessProject | null): string[] {
  const evidence = readiness?.evidence?.niches
  if (Array.isArray(evidence)) {
    const values = evidence.map((item) => String(item).trim()).filter(Boolean)
    if (values.length) return values
  }
  const configured = splitConfigList(account?.platformConfig.niches)
  if (configured.length) return configured
  return splitConfigList(account?.niche)
}

interface QualityReviewBacklog {
  status: string
  blockedCount: number
  passedCount: number
  totalCount: number
  latestBlocked: Array<{
    jobId: string
    topicId: string
    checkedAt?: string | null
    blockers: string[]
    nextAction: string
  }>
  topBlockers: string[]
  remediationEndpoint: string
  schedulerEndpoint: string
}

function qualityReviewBacklog(project: ProjectReadinessProject | null): QualityReviewBacklog | null {
  const raw = project?.evidence?.quality_review
  if (!raw || typeof raw !== 'object') return null
  const data = raw as Record<string, unknown>
  const latest = Array.isArray(data.latest_blocked) ? data.latest_blocked : []
  return {
    status: String(data.status ?? 'unknown'),
    blockedCount: Number(data.blocked_count ?? 0),
    passedCount: Number(data.passed_count ?? 0),
    totalCount: Number(data.total_count ?? 0),
    latestBlocked: latest
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      .map((item) => ({
        jobId: String(item.job_id ?? ''),
        topicId: String(item.topic_id ?? ''),
        checkedAt: typeof item.quality_review_checked_at === 'string' ? item.quality_review_checked_at : null,
        blockers: Array.isArray(item.blockers) ? item.blockers.map((blocker) => String(blocker)) : [],
        nextAction: String(item.next_action ?? ''),
      })),
    topBlockers: Array.isArray(data.top_blockers) ? data.top_blockers.map((item) => String(item)) : [],
    remediationEndpoint: String(data.remediation_endpoint ?? ''),
    schedulerEndpoint: String(data.scheduler_endpoint ?? ''),
  }
}

function firstReadyPrivateJob(
  moneySummary: MoneyMachineOperatorSummaryPayload | null,
  projectId: string,
): MoneyMachineSummaryJob | null {
  return moneySummary?.private_upload_ready.find((job) => job.account_id === projectId) ?? null
}

function platformProviderKey(platform: string) {
  if (platform === 'instagram') return 'instagram'
  if (platform === 'tiktok') return 'tiktok'
  return platform
}

function platformLoginUrl(platform: string) {
  if (platform === 'instagram') return 'https://www.instagram.com/'
  if (platform === 'tiktok') return 'https://www.tiktok.com/'
  return ''
}

function fallbackDistributionTargets(projectId: string, isYoutube: boolean): ProjectDistributionTarget[] {
  if (!isYoutube) return []
  return [
    {
      platform: 'youtube',
      label: 'YouTube Shorts',
      status: 'limited',
      connected: false,
      account_identifier: projectId,
      ready_for_private: false,
      ready_for_public: false,
      required_connection: 'YouTube Studio OAuth',
      next_action: 'Czekam na live readiness z backendu.',
    },
    {
      platform: 'tiktok',
      label: 'TikTok',
      status: 'missing',
      connected: false,
      account_identifier: null,
      ready_for_private: false,
      ready_for_public: false,
      required_connection: 'TikTok trusted account/session',
      next_action: 'Dodaj konto TikTok, żeby Atnos mógł przygotować cross-posting.',
    },
    {
      platform: 'instagram',
      label: 'Instagram Reels',
      status: 'missing',
      connected: false,
      account_identifier: null,
      ready_for_private: false,
      ready_for_public: false,
      required_connection: 'Instagram trusted account/session',
      next_action: 'Dodaj konto Instagram, żeby Atnos mógł przygotować cross-posting.',
    },
  ]
}

function toolingItems(plan: ProjectReadinessProject['tooling_plan'] | undefined): ProjectToolingItem[] {
  if (!plan) return []
  return [...(plan.must_have ?? []), ...(plan.nice_to_have ?? []), ...(plan.not_now ?? [])]
}

function toolingReadyCount(plan: ProjectReadinessProject['tooling_plan'] | undefined) {
  return toolingItems(plan).filter((item) => item.status === 'ready').length
}

function toolingTotalCount(plan: ProjectReadinessProject['tooling_plan'] | undefined) {
  return toolingItems(plan).length
}

function fallbackToolingPlan(projectId: string, account: Account | undefined): ProjectToolingPlan {
  const projectType = projectId.startsWith('investment:')
    ? 'investment'
    : projectId.startsWith('creator:')
      ? 'synthetic_creator'
      : account?.type ?? 'youtube'
  if (projectType === 'investment') {
    return {
      must_have: [
        { key: 'education', label: 'KNF/SEC/FINRA education', purpose: 'Safe education and scam-risk baseline.', status: 'ready', next_action: 'Use as research source.' },
        { key: 'market_data', label: 'Market data provider', purpose: 'Quotes, filings, macro and watchlists.', status: 'missing', next_action: 'Configure NBP/FRED/OpenBB and a market data provider.' },
        { key: 'paper_trading', label: 'Paper trading sandbox', purpose: 'Test automation without real capital.', status: 'missing', next_action: 'Create a paper trading account before trading tests.' },
        { key: 'risk_policy', label: 'Risk policy', purpose: 'Limits, forbidden instruments and kill switch.', status: 'missing', next_action: 'Define risk rules before broker actions.' },
      ],
      nice_to_have: [],
      not_now: [
        { key: 'live_trading', label: 'Live auto-trading', purpose: 'Real money execution.', status: 'blocked', next_action: 'Blocked until explicit approval and risk policy exist.' },
      ],
    }
  }
  if (projectType === 'synthetic_creator') {
    return {
      must_have: [
        { key: 'character_bible', label: 'Character Bible', purpose: 'Fictional adult persona and safety boundaries.', status: 'missing', next_action: 'Define persona, style and forbidden likeness.' },
        { key: 'asset_ledger', label: 'Asset ledger', purpose: 'Track prompts, model, cost, license and disclosure.', status: 'missing', next_action: 'Create storage-backed asset ledger.' },
        { key: 'disclosure', label: 'AI disclosure policy', purpose: 'Required for realistic synthetic content.', status: 'missing', next_action: 'Configure disclosure rules.' },
      ],
      nice_to_have: [],
      not_now: [
        { key: 'faceswap', label: 'Face-swap/deepfake tools', purpose: 'Risky real-person likeness tooling.', status: 'blocked', next_action: 'Do not use for MVP.' },
      ],
    }
  }
  return {
    report_ref: 'data/learning/tooling-research/20260608T003659Z/atnos_tooling_research_report.md',
    report_kind: 'tooling_research',
    must_have: [
      { key: 'youtube_oauth', label: 'YouTube Studio OAuth', purpose: 'Upload, scheduling and analytics.', status: 'limited', next_action: 'Wait for backend readiness or connect OAuth.' },
      { key: 'research_signals', label: 'Trends + search signals', purpose: 'Real topic demand instead of LLM guesses.', status: 'limited', next_action: 'Run intelligence collection.' },
      { key: 'premium_voice', label: 'Premium voice', purpose: 'Public quality gate rejects fallback TTS.', status: 'missing', next_action: 'Use OpenAI TTS or working paid ElevenLabs.' },
      { key: 'visual_backend', label: 'ComfyUI LTX/Wan or image-motion', purpose: 'Visuals tied to every segment.', status: 'limited', next_action: 'Verify live render before public upload.' },
    ],
    nice_to_have: [
      { key: 'vidiq', label: 'vidIQ', purpose: 'Competitor tracking after baseline analytics.', status: 'planned', next_action: 'Use after 30-50 Shorts.' },
    ],
    not_now: [
      { key: 'public_autopost', label: 'Public autopost without review', purpose: 'Too risky before QA and retention are proven.', status: 'blocked', next_action: 'Keep private/supervised flow.' },
    ],
  }
}

function ToolingList({ title, items }: { title: string; items: ProjectToolingItem[] }) {
  if (!items.length) return null
  return (
    <div className="space-y-2">
      <div className="font-mono text-[10px] uppercase text-text-tertiary">{title}</div>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {items.map((item) => (
          <div key={item.key} className="border border-border-default bg-void p-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-display text-[12px] font-semibold uppercase text-text-primary">{item.label}</div>
                <div className="mt-1 text-xs leading-relaxed text-text-secondary">{item.purpose}</div>
              </div>
              <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${statusClass(item.status)}`}>
                {statusLabel(item.status)}
              </span>
            </div>
            {item.detail ? <p className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">{item.detail}</p> : null}
            <p className="mt-2 text-xs leading-relaxed text-warning">{item.next_action}</p>
            {item.link ? (
              <a href={item.link} target="_blank" rel="noreferrer" className="mt-2 inline-block font-mono text-[10px] uppercase text-accent-primary">
                Zrodlo / docs
              </a>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  )
}

function InvestmentEvidencePanel({
  cockpit,
  lastRun,
  lastRunUsesXtbQuotes,
}: {
  cockpit: InvestmentCockpitStatus | null
  lastRun: NonNullable<InvestmentCockpitStatus['strategy_runs']>[number] | null
  lastRunUsesXtbQuotes: boolean
}) {
  const marketSources = cockpit?.market_data_sources ?? []
  const learningResources = cockpit?.learning_resources ?? []
  const blockers = cockpit?.blockers ?? []
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Panel title="Dane rynku i broker">
        <div className="space-y-3 p-4">
          <div className="border border-border-default bg-void p-3">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">Broker access</div>
            <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${cockpit?.broker_access === 'read_only_verified' ? 'text-success' : 'text-warning'}`}>
              {cockpit?.broker_access?.replaceAll('_', ' ') ?? 'brak danych'}
            </div>
            <p className="mt-2 text-xs leading-relaxed text-text-secondary">
              Ten projekt używa tylko demo/read-only i paper tradingu. Realne zlecenia są zablokowane.
            </p>
          </div>
          <div className="border border-border-default bg-void p-3">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">Market data</div>
            <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${marketSources.length ? 'text-success' : 'text-warning'}`}>
              {cockpit?.market_data_status?.replaceAll('_', ' ') ?? 'brak danych'}
            </div>
            <p className="mt-2 text-xs leading-relaxed text-text-secondary">
              {marketSources.length
                ? `Źródła: ${marketSources.join(', ')}. Autopilot wymaga dodatkowo świeżego snapshotu quote'ów XTB.`
                : 'Brak skonfigurowanego źródła cen. Paper autopilot jest zablokowany do czasu zweryfikowania świeżych quotes XTB.'}
            </p>
          </div>
        </div>
      </Panel>

      <Panel title="Edukacja i strategia">
        <div className="space-y-3 p-4">
          <div className="border border-border-default bg-void p-3">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">Materiały dla Atnosa</div>
            <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${learningResources.length ? 'text-success' : 'text-warning'}`}>
              {learningResources.length} źródeł
            </div>
            <div className="mt-2 space-y-1 text-xs leading-relaxed text-text-secondary">
              {learningResources.slice(0, 4).map((resource) => (
                <div key={resource}>{resource}</div>
              ))}
              {!learningResources.length ? <div>Dodaj kursy, playbooki i materiały, zanim Atnos będzie zmieniał strategię.</div> : null}
            </div>
          </div>
          <div className="border border-border-default bg-void p-3">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">Ostatnia sesja strategii</div>
            <div className="mt-1 font-display text-[13px] font-semibold uppercase text-text-primary">
              {lastRun?.status ?? 'brak runu'}
            </div>
            <p className="mt-2 text-xs leading-relaxed text-text-secondary">
              Źródło cen ostatniego runu: {lastRun ? (lastRunUsesXtbQuotes ? 'XTB demo quotes' : 'brak zweryfikowanych quote’ów') : 'brak danych'}.
            </p>
          </div>
        </div>
      </Panel>

      <Panel title="Co wolno teraz">
        <div className="space-y-3 p-4">
          <div>
            <div className="font-mono text-[10px] uppercase text-success">Dozwolone</div>
            <div className="mt-2 space-y-1">
              {(cockpit?.next_actions ?? []).slice(0, 4).map((item) => (
                <div key={item} className="text-xs leading-relaxed text-text-secondary">{item}</div>
              ))}
              {!cockpit?.next_actions?.length ? (
                <div className="text-xs leading-relaxed text-text-secondary">
                  Research, watchlista, notatki ryzyka i paper trading bez realnych zleceń.
                </div>
              ) : null}
            </div>
          </div>
          <div>
            <div className="font-mono text-[10px] uppercase text-warning">Blokady</div>
            <div className="mt-2 space-y-1">
              {blockers.slice(0, 6).map((blocker) => (
                <div key={blocker} className="text-xs leading-relaxed text-text-secondary">{blocker}</div>
              ))}
              {!blockers.length ? (
                <div className="text-xs leading-relaxed text-text-secondary">
                  Live trading i zakupy kartą nadal wymagają osobnej ręcznej zgody i limitów ryzyka.
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </Panel>
    </div>
  )
}

export default function ProjectControlRoute() {
  const params = useParams()
  const projectId = decodeProjectId(params.projectId)
  const accounts = useAccountStore((state) => state.accounts)
  const activeAccountId = useAccountStore((state) => state.activeAccountId)
  const setActiveAccount = useAccountStore((state) => state.setActiveAccount)

  const localAccount = useMemo(
    () => accounts.find((account) => account.id === projectId),
    [accounts, projectId],
  )

  const [readinessProject, setReadinessProject] = useState<ProjectReadinessProject | null>(null)
  const [moneySummary, setMoneySummary] = useState<MoneyMachineOperatorSummaryPayload | null>(null)
  const [publicQuality, setPublicQuality] = useState<ProductionPublicQualityStatusPayload | null>(null)
  const [qualityReviewStatus, setQualityReviewStatus] = useState<ProductionQualityReviewStatusPayload | null>(null)
  const [qualityReviewRun, setQualityReviewRun] = useState<ProductionQualityReviewRunResult | null>(null)
  const [qualityRemediationStatus, setQualityRemediationStatus] = useState<ProductionQualityRemediationStatusPayload | null>(null)
  const [qualityRemediationRun, setQualityRemediationRun] = useState<ProductionQualityRemediationRunResult | null>(null)
  const [operatingStandards, setOperatingStandards] = useState<ProductionOperatingStandardsPayload | null>(null)
  const [creatorCockpit, setCreatorCockpit] = useState<CreatorCockpitStatus | null>(null)
  const [distributionCockpit, setDistributionCockpit] = useState<DistributionCockpitPayload | null>(null)
  const [investmentCockpit, setInvestmentCockpit] = useState<InvestmentCockpitStatus | null>(null)
  const [topics, setTopics] = useState<TopicQueueItem[]>([])
  const [jobs, setJobs] = useState<ProductionJobPayload[]>([])
  const [loading, setLoading] = useState(true)
  const [actionKey, setActionKey] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [connectPlatform, setConnectPlatform] = useState<string | null>(null)
  const [connectIdentifier, setConnectIdentifier] = useState('')
  const [connectSecret, setConnectSecret] = useState('')
  const [watchSymbol, setWatchSymbol] = useState('')
  const [watchName, setWatchName] = useState('')
  const [watchThesis, setWatchThesis] = useState('')
  const [watchRiskNotes, setWatchRiskNotes] = useState('')
  const [paperSymbol, setPaperSymbol] = useState('')
  const [paperSide, setPaperSide] = useState<'buy' | 'sell'>('buy')
  const [paperQuantity, setPaperQuantity] = useState('')
  const [paperPricePln, setPaperPricePln] = useState('')
  const [paperReason, setPaperReason] = useState('')

  const isYoutube = readinessProject?.project_type === 'youtube' || localAccount?.type === 'youtube'
  const isInvestment = readinessProject?.project_type === 'investment' || projectId.startsWith('investment:')
  const isCreator = readinessProject?.project_type === 'synthetic_creator' || projectId.startsWith('creator:')
  const niches = useMemo(() => projectNiches(localAccount, readinessProject), [localAccount, readinessProject])
  const readyPrivateJob = firstReadyPrivateJob(moneySummary, projectId)
  const investmentAutopilotSchedule = investmentCockpit?.autopilot_schedule ?? null
  const lastInvestmentRun = investmentCockpit?.strategy_runs?.[0] ?? null
  const lastInvestmentDecisions = lastInvestmentRun?.decisions ?? []
  const lastRunUsesXtbQuotes = lastInvestmentDecisions.some((decision) => decision.price_source === 'xtb_demo_quotes')
  const distributionTargets = readinessProject?.distribution_targets?.length
    ? readinessProject.distribution_targets
    : fallbackDistributionTargets(projectId, isYoutube)
  const toolingPlan = readinessProject?.tooling_plan ?? fallbackToolingPlan(projectId, localAccount)
  const qualityBacklog = useMemo(() => qualityReviewBacklog(readinessProject), [readinessProject])

  const loadProject = async () => {
    setLoading(true)
    setError(null)
    try {
      const [readiness, standards] = await Promise.all([
        fetchProjectReadiness(),
        fetchProductionOperatingStandards(projectId),
      ])
      setOperatingStandards(standards)
      const nextProject = readiness.projects.find((project) => project.project_id === projectId) ?? null
      setReadinessProject(nextProject)

      const shouldLoadYoutubeData = nextProject?.project_type === 'youtube' || localAccount?.type === 'youtube'
      const shouldLoadInvestmentData = nextProject?.project_type === 'investment' || projectId.startsWith('investment:')
      const shouldLoadCreatorData = nextProject?.project_type === 'synthetic_creator' || projectId.startsWith('creator:')
      if (shouldLoadCreatorData) {
        setCreatorCockpit(await fetchCreatorCockpit())
      } else {
        setCreatorCockpit(null)
      }
      if (shouldLoadInvestmentData) {
        setInvestmentCockpit(await fetchInvestmentCockpit())
      } else {
        setInvestmentCockpit(null)
      }
      if (shouldLoadYoutubeData) {
        const [
          summary,
          topicQueue,
          productionJobs,
          publicQualityStatus,
          crossPlatform,
          qualityReview,
          qualityRemediation,
        ] = await Promise.all([
          fetchMoneyMachineSummary({ accountId: projectId, includeOauth: true, maxUploads: 5, perAccountLimit: 5 }),
          fetchTopicQueue({ accountId: projectId, status: 'all' }),
          fetchProductionJobs({ accountId: projectId, limit: 8 }),
          fetchProductionPublicQualityStatus(projectId),
          fetchDistributionCockpit({ accountId: projectId, limit: 5 }),
          fetchProductionQualityReviewStatus(),
          fetchProductionQualityRemediationStatus(),
        ])
        setMoneySummary(summary)
        setTopics(topicQueue)
        setJobs(productionJobs.jobs)
        setPublicQuality(publicQualityStatus)
        setDistributionCockpit(crossPlatform)
        setQualityReviewStatus(qualityReview)
        setQualityRemediationStatus(qualityRemediation)
      } else {
        setMoneySummary(null)
        setPublicQuality(null)
        setQualityReviewStatus(null)
        setQualityRemediationStatus(null)
        setDistributionCockpit(null)
        setTopics([])
        setJobs([])
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Nie udało się pobrać danych projektu.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadProject()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const runAction = async (key: string, action: () => Promise<string>) => {
    setActionKey(key)
    setMessage(null)
    setError(null)
    try {
      const nextMessage = await action()
      setMessage(nextMessage)
      await loadProject()
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Akcja projektu nie powiodła się.')
    } finally {
      setActionKey(null)
    }
  }

  const runResearch = () =>
    runAction('research', async () => {
      const result = await runIntelligenceCollectors({
        accountId: projectId,
        niches,
        competitorUrls: splitConfigList(localAccount?.platformConfig.competitorChannels),
        referenceUrls: splitConfigList(localAccount?.platformConfig.referenceChannels),
        region: String(localAccount?.platformConfig.region ?? 'PL'),
      })
      return `Zbieranie danych uruchomione. Status: ${String(result.status ?? 'started')}.`
    })

  const bootstrapEducationSources = () =>
    runAction('investment-education-sources', async () => {
      const result = await bootstrapInvestmentEducationSources()
      return `Źródła zapisane: ${result.verified_count} dostępnych, ${result.unavailable_count} niedostępnych. Atnos nie utworzył jeszcze wniosków ani strategii.`
    })

  const runEducationResearch = () =>
    runAction('investment-education-research', async () => {
      const result = await runInvestmentEducationResearch({ maxSources: 4 })
      return `Utworzono cytowany digest: ${result.source_count} źródeł, ${result.citation_count} cytatów. Nie zmienił strategii ani nie uruchomił tradingu.`
    })

  const runTopicGeneration = () =>
    runAction('topics', async () => {
      const generated = await generateTopics({ accountId: projectId, count: 20 })
      return `Wygenerowano ${generated.length} tematów. Top: ${generated[0]?.title ?? '-'}.`
    })

  const runProduction = () =>
    runAction('production', async () => {
      const result = await runProductionContentLoopOnce({ accountId: projectId, maxJobs: 1 })
      return `Produkcja uruchomiona. Status: ${String(result.status ?? 'started')}.`
    })

  const runQualityReview = () =>
    runAction('quality-review', async () => {
      const result = await runProductionQualityReviewOnce({ limit: 5 })
      setQualityReviewRun(result)
      return `Audyt jakości zakończony. ${qualityRunSummary(result)}`
    })

  const runQualityRemediation = () =>
    runAction('quality-remediation', async () => {
      const result = await runProductionQualityRemediationOnce({ limit: 3 })
      setQualityRemediationRun(result)
      return `Auto-naprawa jakości zakończona. ${remediationRunSummary(result)}`
    })

  const uploadPrivate = () => {
    if (!readyPrivateJob) return
    void runAction('upload-private', async () => {
      const result = await uploadProductionJobPrivate(readyPrivateJob.job_id)
      return result.youtube_url
        ? `Prywatny upload gotowy: ${result.youtube_url}`
        : `Prywatny upload: ${result.status}.`
    })
  }

  const prepareCrossPlatformPackages = () =>
    runAction('distribution-package-latest', async () => {
      const result = await createLatestDistributionPackage({ accountId: projectId })
      if (result.status === 'blocked') {
        return `Paczki dystrybucji zablokowane: ${result.reason ?? 'brak gotowego joba'}.`
      }
      return `Paczki dystrybucji: ${result.status}. Utworzono ${result.package?.created_count ?? 0}, zablokowano ${result.package?.blocked_count ?? 0}.`
    })

  const openPlatformConnection = (target: ProjectDistributionTarget) => {
    setConnectPlatform(target.platform)
    setConnectIdentifier(target.account_identifier ?? '')
    setConnectSecret('')
    setMessage(null)
    setError(null)
  }

  const submitPlatformConnection = () => {
    const platform = connectPlatform
    const identifier = connectIdentifier.trim()
    if (!platform || !identifier) {
      setError('Podaj login, e-mail albo nazwę konta dla platformy.')
      return
    }
    void runAction(`connect-${platform}`, async () => {
      const hasSecret = connectSecret.trim().length > 0
      const providerKey = platformProviderKey(platform)
      await createAccountCredential({
        label: `${title} / ${platform}`,
        providerKey,
        accountIdentifier: identifier,
        secretType: hasSecret ? 'password' : 'session_cookie_later',
        secretValue: hasSecret ? connectSecret : null,
        riskLevel: 'high',
        allowedCapabilities: [
          'read_metadata',
          'draft_write',
          'browser_login_later',
          'browser_workflow',
          'confirmed_write_later',
          'publish_later',
        ],
        linkedIntegrationKey: providerKey,
        notes: `Project Control cross-posting account for ${projectId}. Login URL: ${platformLoginUrl(platform)}. Public publishing still requires approval gates.`,
      })
      setConnectPlatform(null)
      setConnectIdentifier('')
      setConnectSecret('')
      return hasSecret
        ? `Konto ${platform} zapisane w lokalnym backendzie. Odświeżam readiness.`
        : `Konto ${platform} zapisane jako sesja do podpięcia później. Dodaj sekret lub sesję, żeby status był gotowy.`
    })
  }

  const submitWatchlistItem = () =>
    runAction('investment-watchlist', async () => {
      const symbol = watchSymbol.trim()
      if (!symbol) throw new Error('Podaj symbol do watchlisty.')
      const item = await addInvestmentWatchlistItem({
        symbol,
        name: watchName.trim() || null,
        assetType: 'custom',
        thesis: watchThesis.trim(),
        riskNotes: watchRiskNotes.trim(),
      })
      setWatchSymbol('')
      setWatchName('')
      setWatchThesis('')
      setWatchRiskNotes('')
      return `Dodano do watchlisty: ${item.symbol}.`
    })

  const submitPaperTrade = () =>
    runAction('investment-paper-trade', async () => {
      const symbol = paperSymbol.trim()
      const quantity = Number(paperQuantity)
      const pricePln = Number(paperPricePln)
      if (!symbol) throw new Error('Podaj symbol paper-trade.')
      if (!Number.isFinite(quantity) || quantity <= 0) throw new Error('Ilosc musi byc wieksza od zera.')
      if (!Number.isFinite(pricePln) || pricePln <= 0) throw new Error('Cena musi byc wieksza od zera.')
      const trade = await recordInvestmentPaperTrade({
        symbol,
        side: paperSide,
        quantity,
        pricePln,
        reason: paperReason.trim(),
      })
      setPaperSymbol('')
      setPaperQuantity('')
      setPaperPricePln('')
      setPaperReason('')
      return `Zapisano paper-trade: ${trade.side.toUpperCase()} ${trade.symbol}. Realny broker nie zostal uzyty.`
    })

  const runPaperAutopilot = () =>
    runAction('investment-paper-autopilot', async () => {
      const run = await runInvestmentPaperAutopilot({
        strategyId: 'portfolio_alpha_operator_v1',
        maxTrades: 3,
      })
      if (run.status === 'blocked') {
        return 'Paper autopilot nie wygenerowal decyzji: wymagany jest swiezy, zweryfikowany snapshot quoteow XTB demo. Otworz Ustawienia Portfolio Alpha i wykonaj test read-only.'
      }
      const usesXtb = run.decisions.some((decision) => decision.price_source === 'xtb_demo_quotes')
      const source = usesXtb ? 'XTB demo quotes' : 'brak zweryfikowanych quoteow'
      return `Paper autopilot zakonczony. Decyzje: ${run.decisions.length}, trade’y: ${run.executed_trade_count}, ceny: ${source}. Realny broker nie zostal uzyty.`
    })

  const initializeCreatorCockpit = () =>
    runAction('creator-init', async () => {
      const result = await ensureDefaultCreatorCockpit()
      return result.project
        ? `Synthetic Model Lab gotowy w trybie ${result.status}.`
        : 'Nie udalo sie utworzyc Synthetic Model Lab.'
    })

  const seedCreatorDrafts = () =>
    runAction('creator-seed', async () => {
      const result = await seedCreatorCockpitWorkItems()
      return `Dodano prywatne drafty: ${result.cockpit.counts.work_items}. Publikacja zewnetrzna nadal zablokowana.`
    })

  const title = readinessProject?.name ?? localAccount?.name ?? projectId
  const description = readinessProject?.description ?? localAccount?.niche ?? 'Projekt Atnosa'
  const active = activeAccountId === projectId
  const operatorDecision = readinessProject?.operator_decision
  const projectFinancials = readinessProject?.financials
  const qualityBlockedJobs = jobs.filter((job) => job.quality_review_status === 'quality_review_blocked').slice(0, 4)
  const latestQualityRunJobs = qualityReviewRun?.jobs ?? []

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border border-border-default bg-panel p-4">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-tertiary">
            Project Control / {readinessProject?.project_type ?? localAccount?.type ?? 'unknown'}
          </div>
          <h1 className="mt-2 font-display text-2xl font-semibold uppercase tracking-[0.08em] text-text-primary">
            {title}
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-text-secondary">{description}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className={`border px-3 py-2 font-mono text-[10px] uppercase ${statusClass(readinessProject?.status)}`}>
            {statusLabel(readinessProject?.status)}
          </span>
          <span className="border border-warning/40 bg-warning/10 px-3 py-2 font-mono text-[10px] uppercase text-warning">
            {readinessProject?.safe_mode ?? true ? 'Safe-mode' : 'Auto'}
          </span>
          <Link
            to="/dashboard"
            className="border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary"
          >
            Wróć
          </Link>
        </div>
      </div>

      {loading ? (
        <div className="border border-border-default bg-panel p-4 font-mono text-[10px] uppercase text-text-tertiary">
          Ładuję projekt...
        </div>
      ) : null}
      {message ? (
        <div className="border border-success/40 bg-success/10 p-3 text-sm text-success">{message}</div>
      ) : null}
      {error ? (
        <div className="border border-error/40 bg-error/10 p-3 text-sm text-error">{error}</div>
      ) : null}

      {operatorDecision ? (
        <section className={`border p-4 ${decisionClass(operatorDecision.state)}`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-4xl">
              <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-tertiary">
                Decyzja operatora / {operatorDecision.state.replaceAll('_', ' ')}
              </div>
              <h2 className="mt-2 font-display text-xl font-semibold uppercase tracking-[0.08em] text-text-primary">
                {operatorDecision.label}
              </h2>
              <p className="mt-2 text-sm leading-relaxed text-text-secondary">{operatorDecision.primary_action}</p>
              <p className="mt-2 text-xs leading-relaxed text-text-tertiary">{operatorDecision.reason}</p>
            </div>
            <div className="min-w-[220px] border border-border-default bg-void p-3">
              <div className="font-mono text-[10px] uppercase text-text-tertiary">Następny koszt</div>
              <div className="mt-1 font-display text-lg font-semibold text-text-primary">
                {formatMoney(operatorDecision.estimated_cost_pln)}
              </div>
              <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">
                Kasa: {formatMoney(operatorDecision.available_balance_pln)}
              </div>
            </div>
          </div>

          <div className="mt-4 grid gap-2 md:grid-cols-3 xl:grid-cols-6">
            <div className="border border-border-default bg-void p-3">
              <div className="font-mono text-[9px] uppercase text-text-tertiary">Można uruchomić</div>
              <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${boolClass(operatorDecision.safe_to_run_now)}`}>
                {boolLabel(operatorDecision.safe_to_run_now)}
              </div>
            </div>
            <div className="border border-border-default bg-void p-3">
              <div className="font-mono text-[9px] uppercase text-text-tertiary">Wymaga zgody</div>
              <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${operatorDecision.requires_owner_approval ? 'text-warning' : 'text-success'}`}>
                {boolLabel(operatorDecision.requires_owner_approval)}
              </div>
            </div>
            <div className="border border-border-default bg-void p-3">
              <div className="font-mono text-[9px] uppercase text-text-tertiary">Budżet</div>
              <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${boolClass(operatorDecision.budget_allowed)}`}>
                {operatorDecision.budget_allowed ? 'OK' : 'Blokada'}
              </div>
            </div>
            <div className="border border-border-default bg-void p-3">
              <div className="font-mono text-[9px] uppercase text-text-tertiary">Ryzyko</div>
              <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${riskClass(operatorDecision.risk_level)}`}>
                {riskLabel(operatorDecision.risk_level)}
              </div>
            </div>
            <div className="border border-border-default bg-void p-3">
              <div className="font-mono text-[9px] uppercase text-text-tertiary">Karta</div>
              <div className="mt-1 font-display text-[13px] font-semibold uppercase text-success">
                {operatorDecision.card_data_storage === 'forbidden' ? 'Nie zapisana' : operatorDecision.card_data_storage}
              </div>
            </div>
            <div className="border border-border-default bg-void p-3">
              <div className="font-mono text-[9px] uppercase text-text-tertiary">Checkout</div>
              <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${operatorDecision.manual_checkout_only ? 'text-warning' : 'text-error'}`}>
                {operatorDecision.manual_checkout_only ? 'Manualny' : 'Auto'}
              </div>
            </div>
          </div>

          {operatorDecision.budget_blockers.length || operatorDecision.forbidden_actions.length || projectFinancials?.rule ? (
            <div className="mt-4 grid gap-3 xl:grid-cols-3">
              {projectFinancials?.rule ? (
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Zasada pieniędzy</div>
                  <p className="mt-2 text-xs leading-relaxed text-text-secondary">{projectFinancials.rule}</p>
                </div>
              ) : null}
              {operatorDecision.budget_blockers.length ? (
                <div className="border border-warning/40 bg-warning/5 p-3">
                  <div className="font-mono text-[10px] uppercase text-warning">Blokady budżetu</div>
                  <div className="mt-2 space-y-1">
                    {operatorDecision.budget_blockers.map((blocker) => (
                      <div key={blocker} className="font-mono text-[10px] uppercase text-text-secondary">
                        {blocker.replaceAll('_', ' ')}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {operatorDecision.forbidden_actions.length ? (
                <div className="border border-error/40 bg-error/5 p-3">
                  <div className="font-mono text-[10px] uppercase text-error">Twardo zakazane</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {operatorDecision.forbidden_actions.map((action) => (
                      <span key={action} className="border border-error/30 px-2 py-1 font-mono text-[9px] uppercase text-text-secondary">
                        {action.replaceAll('_', ' ')}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel title="Dostępy i blokady">
          <div className="space-y-3 p-4">
            <div className="border border-border-default bg-void p-3">
              <div className="font-mono text-[10px] uppercase text-text-tertiary">Tryb pracy</div>
              <div className="mt-1 font-display text-[13px] font-semibold uppercase text-accent-primary">
                {modeLabel(readinessProject?.access_playbook?.mode)}
              </div>
              <p className="mt-2 text-xs leading-relaxed text-warning">
                {readinessProject?.access_playbook?.operator_warning ?? 'Brak playbooka dostępu dla tego projektu.'}
              </p>
            </div>

            <div className="grid gap-2 md:grid-cols-2">
              {(readinessProject?.access_playbook?.required_connections ?? []).map((connection) => (
                <div key={connection.key} className="border border-border-default bg-void p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-display text-[12px] font-semibold uppercase text-text-primary">
                        {connection.label}
                      </div>
                      <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">{connection.where}</div>
                    </div>
                    <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${statusClass(connection.status)}`}>
                      {statusLabel(connection.status)}
                    </span>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-text-secondary">{connection.notes}</p>
                </div>
              ))}
            </div>

            {readinessProject?.blockers?.length ? (
              <div className="border border-warning/30 bg-warning/5 p-3">
                <div className="font-mono text-[10px] uppercase text-warning">Blokady</div>
                <div className="mt-2 space-y-1">
                  {readinessProject.blockers.map((blocker) => (
                    <div key={blocker} className="font-mono text-[10px] uppercase text-text-secondary">
                      {blocker.replaceAll('_', ' ')}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {isYoutube && qualityBacklog ? (
              <div className={[
                'border p-3',
                qualityBacklog.blockedCount > 0 ? 'border-error/40 bg-error/10' : 'border-success/30 bg-success/5',
              ].join(' ')}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-mono text-[10px] uppercase text-text-tertiary">Backlog jakości</div>
                    <div className={[
                      'mt-1 font-display text-[13px] font-semibold uppercase',
                      qualityBacklog.blockedCount > 0 ? 'text-error' : 'text-success',
                    ].join(' ')}>
                      {qualityBacklog.blockedCount > 0 ? 'Blokuje review' : 'Czysto'}
                    </div>
                  </div>
                  <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${statusClass(qualityBacklog.blockedCount > 0 ? 'blocked' : 'ready')}`}>
                    {qualityBacklog.status}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2">
                  {[
                    ['Zablokowane', qualityBacklog.blockedCount],
                    ['Przeszły', qualityBacklog.passedCount],
                    ['Razem', qualityBacklog.totalCount],
                  ].map(([label, value]) => (
                    <div key={String(label)} className="border border-border-default bg-void p-2">
                      <div className="font-mono text-[9px] uppercase text-text-tertiary">{label}</div>
                      <div className="mt-1 font-display text-[15px] font-semibold text-text-primary">{value}</div>
                    </div>
                  ))}
                </div>
                {qualityBacklog.topBlockers.length ? (
                  <div className="mt-3 space-y-1">
                    {qualityBacklog.topBlockers.slice(0, 5).map((blocker) => (
                      <div key={blocker} className="font-mono text-[10px] uppercase text-error">
                        {formatQualityBlocker(blocker)}
                      </div>
                    ))}
                  </div>
                ) : null}
                {qualityBacklog.latestBlocked.length ? (
                  <div className="mt-3 space-y-2">
                    {qualityBacklog.latestBlocked.slice(0, 2).map((job) => (
                      <div key={job.jobId} className="border border-border-default bg-void p-2">
                        <div className="font-mono text-[10px] uppercase text-text-secondary">
                          {job.jobId || 'job'} / {job.topicId || 'topic'}
                        </div>
                        <div className="mt-1 text-xs text-text-tertiary">
                          {job.nextAction || 'Uruchom auto-remediation albo regenerację wizuali.'}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
                {qualityBacklog.blockedCount > 0 ? (
                  <div className="mt-3 font-mono text-[9px] uppercase text-text-tertiary">
                    Auto-naprawa: {qualityBacklog.remediationEndpoint || 'POST /api/production/quality-remediation/run-once'}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel title="Akcje projektu">
          <div className="space-y-3 p-4">
            <div className="grid grid-cols-2 gap-2">
              {(isInvestment
                ? [
                    ['Research rynku', investmentCockpit?.capabilities.can_research],
                    ['Watchlista', investmentCockpit?.capabilities.can_manage_watchlist],
                    ['Paper trading', investmentCockpit?.capabilities.can_paper_trade],
                    ['Realne zlecenia', investmentCockpit?.capabilities.can_trade_real_money],
                    ['Karta / bank', investmentCockpit?.capabilities.can_use_card_or_bank],
                    ['Aktywny w UI', active],
                  ]
                : [
                    ['Research', readinessProject?.capabilities.can_research],
                    ['Produkcja prywatna', readinessProject?.capabilities.can_generate_private],
                    ['Prywatny upload', readinessProject?.capabilities.can_upload_private],
                    ['Publiczny upload', readinessProject?.capabilities.can_publish_public],
                    ['Zakupy / trading', readinessProject?.capabilities.can_trade_or_purchase],
                    ['Aktywny w UI', active],
                  ]
              ).map(([label, value]) => (
                <div key={String(label)} className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[9px] uppercase text-text-tertiary">{label}</div>
                  <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${boolClass(Boolean(value))}`}>
                    {boolLabel(Boolean(value))}
                  </div>
                </div>
              ))}
            </div>

            <div className="grid gap-2">
              {isInvestment ? (
                <>
                  <div className="border border-border-default bg-void p-3 text-xs leading-relaxed text-text-secondary">
                    Główne akcje inwestycyjne są w kokpicie poniżej: research rynku, watchlista,
                    paper autopilot, dziennik decyzji i limity ryzyka.
                  </div>
                  <div className="border border-warning/30 bg-warning/5 p-3 text-xs leading-relaxed text-warning">
                    Project Control dla portfolio nie uruchamia produkcji wideo ani uploadu YouTube.
                    Tu działają tylko market-data, watchlista, paper trading, dziennik decyzji i alerty.
                  </div>
                </>
              ) : (
                <>
              <button
                type="button"
                onClick={() => void runResearch()}
                disabled={!isYoutube || !readinessProject?.capabilities.can_research || actionKey !== null}
                className="border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary disabled:cursor-not-allowed disabled:opacity-40"
              >
                {actionKey === 'research' ? 'Zbieram dane...' : 'Uruchom research'}
              </button>
              <button
                type="button"
                onClick={() => void runTopicGeneration()}
                disabled={!isYoutube || !readinessProject?.capabilities.can_research || actionKey !== null}
                className="border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary disabled:cursor-not-allowed disabled:opacity-40"
              >
                {actionKey === 'topics' ? 'Generuję...' : 'Generuj 20 tematów'}
              </button>
              <button
                type="button"
                onClick={() => void runProduction()}
                disabled={!isYoutube || !readinessProject?.capabilities.can_generate_private || actionKey !== null}
                className="border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-not-allowed disabled:opacity-40"
              >
                {actionKey === 'production' ? 'Startuję...' : 'Start produkcji'}
              </button>
              <button
                type="button"
                onClick={uploadPrivate}
                disabled={!readyPrivateJob || actionKey !== null}
                className="border border-success/50 bg-success/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-success disabled:cursor-not-allowed disabled:opacity-40"
              >
                {actionKey === 'upload-private' ? 'Wysyłam...' : 'Wrzuć najlepszy prywatnie'}
              </button>
                </>
              )}
              {localAccount ? (
                <button
                  type="button"
                  onClick={() => setActiveAccount(projectId)}
                  disabled={active}
                  className="border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {active ? 'Projekt jest aktywny' : 'Ustaw jako aktywny'}
                </button>
              ) : null}
            </div>
          </div>
        </Panel>
      </div>

      {isCreator ? (
        <Panel title="Synthetic Creator Cockpit">
          <div className="space-y-4 p-4">
            <div className="grid gap-3 md:grid-cols-4">
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Status</div>
                <div className={`mt-1 font-display text-[14px] font-semibold uppercase ${statusClass(creatorCockpit?.status)}`}>
                  {statusLabel(creatorCockpit?.status)}
                </div>
              </div>
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Private drafts</div>
                <div className={`mt-1 font-display text-[14px] font-semibold uppercase ${boolClass(creatorCockpit?.capabilities.can_generate_private_drafts)}`}>
                  {boolLabel(creatorCockpit?.capabilities.can_generate_private_drafts)}
                </div>
              </div>
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">External publish</div>
                <div className={`mt-1 font-display text-[14px] font-semibold uppercase ${creatorCockpit?.capabilities.can_publish_external ? 'text-error' : 'text-success'}`}>
                  {creatorCockpit?.capabilities.can_publish_external ? 'Wlaczone' : 'Zablokowane'}
                </div>
              </div>
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Asset ledger</div>
                <div className="mt-1 font-display text-[14px] font-semibold uppercase text-text-primary">
                  {creatorCockpit?.counts.asset_ledger ?? creatorCockpit?.asset_ledger.length ?? 0}
                </div>
              </div>
            </div>

            <div className="grid gap-3 xl:grid-cols-[0.9fr_1.1fr]">
              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Projekt
                </div>
                {creatorCockpit?.project ? (
                  <div className="mt-3 space-y-2 text-sm text-text-secondary">
                    <div className="font-display text-[15px] font-semibold uppercase text-accent-primary">
                      {creatorCockpit.project.name}
                    </div>
                    <div className="font-mono text-[10px] uppercase text-text-tertiary">
                      {creatorCockpit.project.project_type} / {creatorCockpit.project.operating_mode}
                    </div>
                    <p className="text-xs leading-relaxed">{creatorCockpit.project.content_boundaries}</p>
                    <div className="flex flex-wrap gap-2">
                      {creatorCockpit.project.platforms.map((platform) => (
                        <span key={platform} className="border border-border-default bg-panel px-2 py-1 font-mono text-[9px] uppercase text-text-secondary">
                          {platform.replaceAll('_', ' ')}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="mt-3 border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                    Synthetic Model Lab nie jest jeszcze utworzony w backendzie.
                  </div>
                )}
                <div className="mt-4 grid gap-2">
                  <button
                    type="button"
                    onClick={() => void initializeCreatorCockpit()}
                    disabled={actionKey !== null || Boolean(creatorCockpit?.project)}
                    className="border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {actionKey === 'creator-init' ? 'Tworze...' : 'Inicjalizuj Synthetic Model Lab'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void seedCreatorDrafts()}
                    disabled={actionKey !== null || !creatorCockpit?.capabilities.can_generate_private_drafts}
                    className="border border-warning/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-warning disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {actionKey === 'creator-seed' ? 'Seeduje...' : 'Seed prywatnych draftow'}
                  </button>
                </div>
              </div>

              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Bezpieczenstwo i blokady
                </div>
                <div className="mt-3 grid gap-2 md:grid-cols-2">
                  {(creatorCockpit?.blockers ?? []).map((blocker) => (
                    <div key={blocker} className="border border-warning/30 bg-warning/5 px-3 py-2 font-mono text-[10px] uppercase text-warning">
                      {blocker.replaceAll('_', ' ')}
                    </div>
                  ))}
                  {Object.entries(creatorCockpit?.safety ?? {}).map(([key, value]) => (
                    <div key={key} className="border border-border-default bg-panel px-3 py-2 font-mono text-[10px] uppercase text-text-secondary">
                      {key.replaceAll('_', ' ')}: {String(value).replaceAll('_', ' ')}
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Work items
                </div>
                <div className="mt-3 space-y-2">
                  {(creatorCockpit?.work_items ?? []).slice(0, 8).map((item) => (
                    <div key={`${item.id ?? item.title}`} className="border border-border-default bg-panel p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="font-display text-[12px] font-semibold uppercase text-text-primary">{item.title}</div>
                          <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">
                            {item.work_type} / {item.platform ?? 'internal'}
                          </div>
                        </div>
                        <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${statusClass(item.status)}`}>
                          {statusLabel(item.status)}
                        </span>
                      </div>
                      {item.next_action ? <p className="mt-2 text-xs leading-relaxed text-text-secondary">{item.next_action}</p> : null}
                    </div>
                  ))}
                  {!creatorCockpit?.work_items?.length ? (
                    <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                      Brak work itemow. Uzyj seed prywatnych draftow.
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Asset ledger
                </div>
                <div className="mt-3 space-y-2">
                  {(creatorCockpit?.asset_ledger ?? []).slice(0, 8).map((asset) => (
                    <div key={`${asset.work_item_id ?? asset.title}-${asset.work_type}`} className="border border-border-default bg-panel p-3">
                      <div className="font-display text-[12px] font-semibold uppercase text-text-primary">{asset.title}</div>
                      <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[9px] uppercase text-text-tertiary">
                        <span>Risk {asset.risk_level}</span>
                        <span>License {asset.license_status}</span>
                        <span>Disclosure {boolLabel(asset.disclosure_required)}</span>
                        <span>Approval {boolLabel(asset.approval_required)}</span>
                      </div>
                    </div>
                  ))}
                  {!creatorCockpit?.asset_ledger?.length ? (
                    <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                      Ledger pojawi sie po utworzeniu draftow lub preview batch.
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        </Panel>
      ) : null}

      {isInvestment ? (
        <Panel title="Kokpit inwestycyjny">
          <div className="space-y-4 p-4">
            <div className="grid gap-3 md:grid-cols-4">
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Tryb</div>
                <div className="mt-1 font-display text-[14px] font-semibold uppercase text-accent-primary">
                  {investmentCockpit?.mode?.replaceAll('_', ' ') ?? 'paper trading only'}
                </div>
              </div>
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Realny trading</div>
                <div className={`mt-1 font-display text-[14px] font-semibold uppercase ${investmentCockpit?.real_trading_enabled ? 'text-error' : 'text-success'}`}>
                  {investmentCockpit?.real_trading_enabled ? 'Wlaczony' : 'Zablokowany'}
                </div>
              </div>
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Watchlista</div>
                <div className="mt-1 font-display text-[14px] font-semibold uppercase text-text-primary">
                  {investmentCockpit?.watchlist_count ?? 0}
                </div>
              </div>
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Paper trades</div>
                <div className="mt-1 font-display text-[14px] font-semibold uppercase text-text-primary">
                  {investmentCockpit?.paper_trade_count ?? 0}
                </div>
              </div>
            </div>

            <div className="border border-border-default bg-void p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                    Zweryfikowane źródła edukacji
                  </div>
                  <p className="mt-2 max-w-3xl text-sm leading-relaxed text-text-secondary">
                    Zapisuje tylko publiczne materiały wraz z wynikiem kontroli adresu. To nie jest jeszcze nauka Atnosa,
                    sygnał transakcyjny ani dostęp do konta Binance.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void runEducationResearch()}
                  disabled={actionKey !== null || !(investmentCockpit?.education_sources ?? []).some((source) => source.verification_status === 'available')}
                  className="border border-success/60 px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-success disabled:cursor-wait disabled:opacity-60"
                >
                  {actionKey === 'investment-education-research' ? 'Pobieram digest...' : 'Pobierz cytowany digest'}
                </button>
                <button
                  type="button"
                  onClick={() => void bootstrapEducationSources()}
                  disabled={actionKey !== null}
                  className="border border-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
                >
                  {actionKey === 'investment-education-sources' ? 'Sprawdzam źródła...' : 'Dodaj i sprawdź źródła'}
                </button>
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {(investmentCockpit?.education_sources ?? []).map((source) => (
                  <div key={source.source_id} className="border border-border-default bg-panel p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="font-display text-[12px] font-semibold uppercase text-text-primary">{source.title}</div>
                      <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${source.verification_status === 'available' ? 'border-success/50 text-success' : 'border-warning/50 text-warning'}`}>
                        {source.verification_status.replaceAll('_', ' ')}
                      </span>
                    </div>
                    <div className="mt-2 font-mono text-[9px] uppercase text-text-tertiary">
                      {source.publisher} / {source.access_scope.replaceAll('_', ' ')}
                    </div>
                    <p className="mt-2 text-xs leading-relaxed text-text-secondary">{source.limitation}</p>
                    {source.url ? (
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 inline-block font-mono text-[9px] uppercase text-accent-primary underline underline-offset-2"
                      >
                        Otwórz źródło
                      </a>
                    ) : null}
                    {source.checked_at ? <div className="mt-2 font-mono text-[9px] uppercase text-text-tertiary">Sprawdzone: {formatDateTime(source.checked_at)}</div> : null}
                  </div>
                ))}
                {!(investmentCockpit?.education_sources ?? []).length ? (
                  <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                    Źródła nie zostały jeszcze sprawdzone. Dodanie katalogu nie uruchamia strategii ani paper trade.
                  </div>
                ) : null}
              </div>
              {(investmentCockpit?.education_reports ?? []).slice(0, 2).map((report) => (
                <div key={report.id} className="mt-3 border border-success/25 bg-success/5 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-display text-[12px] font-semibold uppercase text-text-primary">Cytowany digest</span>
                    <span className="font-mono text-[9px] uppercase text-success">{report.status}</span>
                  </div>
                  <div className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">
                    {report.source_count} źródeł / {report.citation_count} cytatów / LLM: {report.llm_synthesis_status.replaceAll('_', ' ')}
                  </div>
                  {(report.digest.entries ?? []).slice(0, 2).map((entry) => (
                    <div key={entry.source_id} className="mt-2 text-xs leading-relaxed text-text-secondary">
                      <span className="font-semibold text-text-primary">{entry.title}: </span>{entry.excerpt}
                    </div>
                  ))}
                  <div className="mt-2 font-mono text-[9px] uppercase text-text-tertiary">Utworzono: {formatDateTime(report.created_at)}. Nie zmienia strategii.</div>
                </div>
              ))}
            </div>

            <div className="border border-accent-primary/30 bg-accent-primary/5 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="max-w-3xl">
                  <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                    Paper autopilot na danych rynku
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-text-secondary">
                    Uruchamia jedną sesję strategii paper-only wyłącznie na świeżych, zweryfikowanych quote’ach XTB demo.
                    Bez takiego snapshotu nie generuje decyzji ani transakcji. Realne zlecenia są nadal zablokowane.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void runPaperAutopilot()}
                  disabled={actionKey !== null || !investmentCockpit?.capabilities.can_run_autonomous_paper_strategy}
                  className="border border-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
                >
                  {actionKey === 'investment-paper-autopilot' ? 'Uruchamiam...' : 'Uruchom paper autopilot'}
                </button>
              </div>

              {!investmentCockpit?.capabilities.can_run_autonomous_paper_strategy ? (
                <div className="mt-3 border border-warning/40 bg-warning/5 p-3 text-xs leading-relaxed text-warning">
                  Autopilot jest zablokowany: wymagany jest swiezy snapshot XTB demo z read-only verification.{' '}
                  <Link to="/settings" className="font-semibold text-accent-primary underline underline-offset-2">
                    Otworz ustawienia Portfolio Alpha
                  </Link>
                  .
                </div>
              ) : null}

              <div className="mt-4 grid gap-3 md:grid-cols-4">
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Ostatni run</div>
                  <div className="mt-1 font-display text-[13px] font-semibold uppercase text-text-primary">
                    {lastInvestmentRun?.status ?? 'brak'}
                  </div>
                </div>
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Źródło cen</div>
                  <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${lastRunUsesXtbQuotes ? 'text-success' : 'text-warning'}`}>
                    {lastInvestmentRun ? (lastRunUsesXtbQuotes ? 'XTB quotes' : 'brak zweryfikowanych quote’ów') : 'brak runu'}
                  </div>
                </div>
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Decyzje</div>
                  <div className="mt-1 font-display text-[13px] font-semibold uppercase text-text-primary">
                    {lastInvestmentDecisions.length}
                  </div>
                </div>
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Realne zlecenia</div>
                  <div className="mt-1 font-display text-[13px] font-semibold uppercase text-success">
                    {lastInvestmentRun?.real_trading_enabled ? 'ryzyko' : '0 / zablokowane'}
                  </div>
                </div>
              </div>

              <div className="mt-3 grid gap-3 md:grid-cols-4">
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Harmonogram</div>
                  <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${investmentAutopilotSchedule?.enabled ? 'text-success' : 'text-warning'}`}>
                    {investmentAutopilotSchedule?.enabled ? 'aktywny' : 'wyłączony'}
                  </div>
                </div>
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Due</div>
                  <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${investmentAutopilotSchedule?.due ? 'text-warning' : 'text-text-secondary'}`}>
                    {investmentAutopilotSchedule?.due ? 'teraz' : 'nie'}
                  </div>
                </div>
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Interwał</div>
                  <div className="mt-1 font-display text-[13px] font-semibold uppercase text-text-primary">
                    {investmentAutopilotSchedule?.interval_minutes ?? 0} min
                  </div>
                </div>
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Alert Gmail</div>
                  <div className={`mt-1 font-display text-[13px] font-semibold uppercase ${investmentAutopilotSchedule?.alerts_enabled ? 'text-success' : 'text-warning'}`}>
                    {investmentAutopilotSchedule?.alerts_enabled ? 'aktywny' : 'wyłączony'}
                  </div>
                </div>
              </div>
              <div className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">
                Następny run: {formatDateTime(investmentAutopilotSchedule?.next_due_at)}
              </div>

              {lastInvestmentDecisions.length ? (
                <div className="mt-4 overflow-x-auto border border-border-default bg-void">
                  <table className="min-w-full text-left text-xs">
                    <thead className="border-b border-border-default font-mono text-[10px] uppercase text-text-tertiary">
                      <tr>
                        <th className="px-3 py-2">Symbol</th>
                        <th className="px-3 py-2">Akcja</th>
                        <th className="px-3 py-2">Cena</th>
                        <th className="px-3 py-2">Źródło</th>
                        <th className="px-3 py-2">Sygnał</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lastInvestmentDecisions.slice(0, 8).map((decision) => (
                        <tr key={`${lastInvestmentRun?.id}-${decision.symbol}`} className="border-b border-border-default/60 last:border-0">
                          <td className="px-3 py-2 font-display text-[12px] font-semibold uppercase text-text-primary">
                            {decision.symbol}
                          </td>
                          <td className={`px-3 py-2 font-mono text-[10px] uppercase ${actionClass(decision.action)}`}>
                            {decision.action.replaceAll('_', ' ')}
                          </td>
                          <td className="px-3 py-2 font-mono text-[10px] uppercase text-text-secondary">
                            {decision.market_price == null
                              ? 'brak quoteu'
                              : formatMarketPrice(decision.market_price, decision.price_currency)}
                          </td>
                          <td className={`px-3 py-2 font-mono text-[10px] uppercase ${priceSourceClass(decision.price_source)}`}>
                            {decision.price_source ?? 'unknown'}
                          </td>
                          <td className="px-3 py-2 font-mono text-[10px] uppercase text-text-tertiary">
                            {decision.quote_range_signal == null ? 'brak' : Number(decision.quote_range_signal).toFixed(3)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="mt-4 border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                  Brak runów strategii. Uruchom paper autopilot, żeby zobaczyć decision journal.
                </div>
              )}
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Dodaj do watchlisty
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <label className="block">
                    <span className="font-mono text-[10px] uppercase text-text-tertiary">Symbol</span>
                    <input
                      value={watchSymbol}
                      onChange={(event) => setWatchSymbol(event.target.value)}
                      className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                      placeholder="np. SPY"
                    />
                  </label>
                  <label className="block">
                    <span className="font-mono text-[10px] uppercase text-text-tertiary">Nazwa</span>
                    <input
                      value={watchName}
                      onChange={(event) => setWatchName(event.target.value)}
                      className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                      placeholder="opis instrumentu"
                    />
                  </label>
                </div>
                <label className="mt-3 block">
                  <span className="font-mono text-[10px] uppercase text-text-tertiary">Teza</span>
                  <textarea
                    value={watchThesis}
                    onChange={(event) => setWatchThesis(event.target.value)}
                    rows={3}
                    className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                    placeholder="Dlaczego Atnos ma to obserwowac?"
                  />
                </label>
                <label className="mt-3 block">
                  <span className="font-mono text-[10px] uppercase text-text-tertiary">Ryzyka</span>
                  <textarea
                    value={watchRiskNotes}
                    onChange={(event) => setWatchRiskNotes(event.target.value)}
                    rows={2}
                    className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                    placeholder="Co moze pojsc zle?"
                  />
                </label>
                <button
                  type="button"
                  onClick={() => void submitWatchlistItem()}
                  disabled={actionKey !== null}
                  className="mt-3 w-full border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
                >
                  {actionKey === 'investment-watchlist' ? 'Zapisuje...' : 'Zapisz watchliste'}
                </button>
              </div>

              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Zapisz paper-trade
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-4">
                  <label className="block md:col-span-1">
                    <span className="font-mono text-[10px] uppercase text-text-tertiary">Symbol</span>
                    <input
                      value={paperSymbol}
                      onChange={(event) => setPaperSymbol(event.target.value)}
                      className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                      placeholder="SPY"
                    />
                  </label>
                  <label className="block">
                    <span className="font-mono text-[10px] uppercase text-text-tertiary">Strona</span>
                    <select
                      value={paperSide}
                      onChange={(event) => setPaperSide(event.target.value as 'buy' | 'sell')}
                      className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                    >
                      <option value="buy">BUY</option>
                      <option value="sell">SELL</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="font-mono text-[10px] uppercase text-text-tertiary">Ilosc</span>
                    <input
                      value={paperQuantity}
                      onChange={(event) => setPaperQuantity(event.target.value)}
                      type="number"
                      min="0"
                      step="0.000001"
                      className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                    />
                  </label>
                  <label className="block">
                    <span className="font-mono text-[10px] uppercase text-text-tertiary">Cena PLN</span>
                    <input
                      value={paperPricePln}
                      onChange={(event) => setPaperPricePln(event.target.value)}
                      type="number"
                      min="0"
                      step="0.01"
                      className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                    />
                  </label>
                </div>
                <label className="mt-3 block">
                  <span className="font-mono text-[10px] uppercase text-text-tertiary">Powod</span>
                  <textarea
                    value={paperReason}
                    onChange={(event) => setPaperReason(event.target.value)}
                    rows={3}
                    className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                    placeholder="Paper-only test, zadnego realnego zlecenia."
                  />
                </label>
                <button
                  type="button"
                  onClick={() => void submitPaperTrade()}
                  disabled={actionKey !== null}
                  className="mt-3 w-full border border-warning/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-warning disabled:cursor-wait disabled:opacity-60"
                >
                  {actionKey === 'investment-paper-trade' ? 'Zapisuje...' : 'Zapisz paper-trade'}
                </button>
              </div>
            </div>

            <div className="grid gap-4 xl:grid-cols-3">
              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Watchlista
                </div>
                <div className="mt-3 space-y-2">
                  {(investmentCockpit?.watchlist ?? []).slice(0, 6).map((item) => (
                    <div key={item.symbol} className="border border-border-default bg-panel p-3">
                      <div className="font-display text-[13px] font-semibold uppercase text-text-primary">{item.symbol}</div>
                      <div className="mt-1 text-xs text-text-secondary">{item.name || item.asset_type}</div>
                      {item.thesis ? <p className="mt-2 text-xs leading-relaxed text-text-secondary">{item.thesis}</p> : null}
                    </div>
                  ))}
                  {!investmentCockpit?.watchlist?.length ? (
                    <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                      Brak symboli. Dodaj pierwszy pomysl do obserwacji.
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Paper pozycje
                </div>
                <div className="mt-3 space-y-2">
                  {(investmentCockpit?.paper_positions ?? []).slice(0, 6).map((position) => (
                    <div key={position.symbol} className="border border-border-default bg-panel p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-display text-[13px] font-semibold uppercase text-text-primary">{position.symbol}</span>
                        <span className="font-mono text-[10px] uppercase text-accent-primary">{position.mode}</span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[10px] uppercase text-text-tertiary">
                        <span>Qty {formatQuantity(position.net_quantity)}</span>
                        <span>Avg {formatMoney(position.average_buy_price_pln)}</span>
                        <span>Buy {formatMoney(position.gross_buy_cost_pln)}</span>
                        <span>Sell {formatMoney(position.gross_sell_value_pln)}</span>
                      </div>
                    </div>
                  ))}
                  {!investmentCockpit?.paper_positions?.length ? (
                    <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                      Brak paper pozycji. Zapisz paper-trade, aby testowac strategie bez pieniedzy.
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="border border-border-default bg-void p-4">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Bezpieczenstwo
                </div>
                <div className="mt-3 space-y-2">
                  {(investmentCockpit?.blockers ?? []).map((blocker) => (
                    <div key={blocker} className="border border-warning/30 bg-warning/5 px-3 py-2 font-mono text-[10px] uppercase text-warning">
                      {blocker.replaceAll('_', ' ')}
                    </div>
                  ))}
                  <div className="border border-success/30 bg-success/5 px-3 py-2 font-mono text-[10px] uppercase text-success">
                    Card / bank data storage: forbidden
                  </div>
                  <div className="border border-success/30 bg-success/5 px-3 py-2 font-mono text-[10px] uppercase text-success">
                    Real orders: blocked
                  </div>
                  {(investmentCockpit?.paper_trades ?? []).slice(0, 4).map((trade) => (
                    <div key={trade.id} className="border border-border-default bg-panel p-3">
                      <div className="font-mono text-[10px] uppercase text-text-tertiary">
                        {trade.side} {trade.symbol} / {formatQuantity(trade.quantity)} @ {formatMoney(trade.price_pln)}
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-text-secondary">{trade.reason}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </Panel>
      ) : null}

      {isYoutube ? (
        <Panel title="Public quality gate">
          <div className="space-y-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3 border border-border-default bg-void p-3">
              <div>
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Status publicznej jakości</div>
                <div className={`mt-1 font-display text-[15px] font-semibold uppercase ${publicQuality?.ready ? 'text-success' : 'text-warning'}`}>
                  {publicQuality ? (publicQuality.ready ? 'Gotowe do public-quality renderu' : 'Blokada jakości') : 'Brak danych'}
                </div>
              </div>
              <span className={`border px-3 py-2 font-mono text-[10px] uppercase ${readinessPillClass(publicQuality?.ready ?? null)}`}>
                {publicQuality ? (publicQuality.ready ? 'READY' : 'BLOCKED') : 'LOADING'}
              </span>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              <div className="border border-border-default bg-void p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-display text-[12px] font-semibold uppercase text-text-primary">Premium voice</span>
                  <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${readinessPillClass(publicQuality?.voice_provider?.public_voice_ready ?? null)}`}>
                    {publicQuality?.voice_provider?.public_voice_ready ? 'OK' : 'BLOK'}
                  </span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-text-secondary">
                  Backend: {publicQuality?.voice_provider?.selected_tts_backend ?? 'brak danych'}.
                </p>
                {publicQuality?.voice_provider?.next_action ? (
                  <p className="mt-2 font-mono text-[10px] uppercase text-warning">{publicQuality.voice_provider.next_action}</p>
                ) : null}
              </div>

              <div className="border border-border-default bg-void p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-display text-[12px] font-semibold uppercase text-text-primary">Visual backend</span>
                  <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${readinessPillClass(publicQuality?.visual_backend?.public_visual_ready ?? null)}`}>
                    {publicQuality?.visual_backend?.public_visual_ready ? 'OK' : 'BLOK'}
                  </span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-text-secondary">
                  Tryb: {publicQuality?.visual_backend?.effective_video_backend ?? publicQuality?.visual_backend?.selected_video_backend ?? 'brak danych'}.
                </p>
                {publicQuality?.visual_backend?.next_action ? (
                  <p className="mt-2 font-mono text-[10px] uppercase text-warning">{publicQuality.visual_backend.next_action}</p>
                ) : null}
              </div>

              <div className="border border-border-default bg-void p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-display text-[12px] font-semibold uppercase text-text-primary">Style guide</span>
                  <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${readinessPillClass(publicQuality?.style_guide?.ready ?? null)}`}>
                    {publicQuality?.style_guide?.ready ? 'OK' : 'BLOK'}
                  </span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-text-secondary">
                  Źródło: {publicQuality?.style_guide?.source ?? publicQuality?.style_guide?.status ?? 'brak danych'}.
                </p>
                {publicQuality?.style_guide?.next_action ? (
                  <p className="mt-2 font-mono text-[10px] uppercase text-warning">{publicQuality.style_guide.next_action}</p>
                ) : null}
              </div>
            </div>

            {publicQuality?.blockers?.length ? (
              <div className="border border-error/40 bg-error/10 p-3">
                <div className="font-mono text-[10px] uppercase text-error">Blokery public-quality</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {publicQuality.blockers.map((blocker) => (
                    <span key={blocker} className="border border-error/30 px-2 py-1 font-mono text-[9px] uppercase text-error">
                      {blocker.replaceAll('_', ' ')}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </Panel>
      ) : null}

      {isYoutube ? (
        <Panel title="Audyt wizualnej jakości i auto-naprawa">
          <div className="space-y-3 p-4">
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="border border-border-default bg-void p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-mono text-[10px] uppercase text-text-tertiary">Quality review scheduler</div>
                    <div className={`mt-1 font-display text-[15px] font-semibold uppercase ${qualityReviewStatus?.enabled ? 'text-success' : 'text-warning'}`}>
                      {qualityReviewStatus?.enabled ? 'Aktywny' : 'Wyłączony'}
                    </div>
                    <p className="mt-2 text-xs leading-relaxed text-text-secondary">
                      Blokuje materiały z dziecinną miniaturką, rozmytą pierwszą klatką albo obrazem bez związku z tematem.
                    </p>
                  </div>
                  <span className={`shrink-0 border px-3 py-2 font-mono text-[10px] uppercase ${readinessPillClass(qualityReviewStatus?.enabled ?? null)}`}>
                    {qualityReviewStatus?.enabled ? 'ON' : 'OFF'}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2">
                  <div className="border border-border-default bg-panel p-2">
                    <div className="font-mono text-[9px] uppercase text-text-tertiary">Do audytu</div>
                    <div className="mt-1 font-display text-[15px] font-semibold text-accent-primary">
                      {qualityReviewStatus?.pending_count ?? 0}
                    </div>
                  </div>
                  <div className="border border-border-default bg-panel p-2">
                    <div className="font-mono text-[9px] uppercase text-text-tertiary">Interwał</div>
                    <div className="mt-1 font-display text-[15px] font-semibold text-text-primary">
                      {qualityReviewStatus?.interval_minutes ?? 0}m
                    </div>
                  </div>
                  <div className="border border-border-default bg-panel p-2">
                    <div className="font-mono text-[9px] uppercase text-text-tertiary">Limit</div>
                    <div className="mt-1 font-display text-[15px] font-semibold text-text-primary">
                      {qualityReviewStatus?.max_jobs_per_tick ?? 0}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void runQualityReview()}
                  disabled={!qualityReviewStatus?.enabled || actionKey !== null}
                  className="mt-3 w-full border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {actionKey === 'quality-review' ? 'Sprawdzam jakość...' : 'Uruchom audyt jakości'}
                </button>
                <p className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">{qualityRunSummary(qualityReviewRun)}</p>
              </div>

              <div className="border border-border-default bg-void p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-mono text-[10px] uppercase text-text-tertiary">Quality auto-remediation</div>
                    <div className={`mt-1 font-display text-[15px] font-semibold uppercase ${qualityRemediationStatus?.enabled ? 'text-success' : 'text-warning'}`}>
                      {qualityRemediationStatus?.enabled ? 'Aktywna' : 'Wyłączona'}
                    </div>
                    <p className="mt-2 text-xs leading-relaxed text-text-secondary">
                      Kolejkuje bezpieczną regenerację słabych rolek. Nie wrzuca i nie publikuje nic automatycznie.
                    </p>
                  </div>
                  <span className={`shrink-0 border px-3 py-2 font-mono text-[10px] uppercase ${readinessPillClass(qualityRemediationStatus?.enabled ?? null)}`}>
                    {qualityRemediationStatus?.enabled ? 'ON' : 'OFF'}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-2">
                  <div className="border border-border-default bg-panel p-2">
                    <div className="font-mono text-[9px] uppercase text-text-tertiary">Do naprawy</div>
                    <div className="mt-1 font-display text-[15px] font-semibold text-warning">
                      {qualityRemediationStatus?.candidate_count ?? 0}
                    </div>
                  </div>
                  <div className="border border-border-default bg-panel p-2">
                    <div className="font-mono text-[9px] uppercase text-text-tertiary">Run job</div>
                    <div className={`mt-1 font-display text-[15px] font-semibold ${qualityRemediationStatus?.run_jobs ? 'text-warning' : 'text-text-secondary'}`}>
                      {qualityRemediationStatus?.run_jobs ? 'Tak' : 'Kolejka'}
                    </div>
                  </div>
                  <div className="border border-border-default bg-panel p-2">
                    <div className="font-mono text-[9px] uppercase text-text-tertiary">Limit</div>
                    <div className="mt-1 font-display text-[15px] font-semibold text-text-primary">
                      {qualityRemediationStatus?.max_jobs_per_tick ?? 0}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void runQualityRemediation()}
                  disabled={!qualityRemediationStatus?.enabled || actionKey !== null}
                  className="mt-3 w-full border border-warning/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-warning disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {actionKey === 'quality-remediation' ? 'Kolejkuję naprawę...' : 'Uruchom auto-naprawę'}
                </button>
                <p className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">{remediationRunSummary(qualityRemediationRun)}</p>
              </div>
            </div>

            <div className="grid gap-3 xl:grid-cols-2">
              <div className="border border-border-default bg-void p-3">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.1em] text-text-primary">
                  Ostatnie blokady projektu
                </div>
                <div className="mt-3 space-y-2">
                  {qualityBlockedJobs.map((job) => (
                    <div key={job.id} className="border border-error/30 bg-error/5 p-3">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <div className="font-mono text-[10px] uppercase text-error">{job.id}</div>
                          <div className="mt-1 text-xs text-text-secondary">
                            {job.topic_title ?? job.topic_id}
                          </div>
                        </div>
                        <span className="border border-error/30 px-2 py-1 font-mono text-[9px] uppercase text-error">
                          {formatDateTime(job.quality_review_checked_at)}
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(job.quality_review_result?.blockers ?? ['quality_review_blocked']).slice(0, 6).map((blocker) => (
                          <span key={blocker} className="border border-error/30 px-2 py-1 font-mono text-[9px] uppercase text-error">
                            {formatQualityBlocker(blocker)}
                          </span>
                        ))}
                      </div>
                      {job.quality_review_result?.next_action ? (
                        <p className="mt-2 text-xs leading-relaxed text-warning">{job.quality_review_result.next_action}</p>
                      ) : null}
                    </div>
                  ))}
                  {!qualityBlockedJobs.length ? (
                    <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                      Brak ostatnich blokad jakości dla tego projektu.
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="border border-border-default bg-void p-3">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.1em] text-text-primary">
                  Wynik ostatniego ręcznego audytu
                </div>
                <div className="mt-3 space-y-2">
                  {latestQualityRunJobs.map((job) => (
                    <div key={job.job_id} className={`border p-3 ${job.status === 'blocked' ? 'border-error/30 bg-error/5' : 'border-success/30 bg-success/5'}`}>
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="font-mono text-[10px] uppercase text-text-secondary">
                          {job.job_id}
                        </div>
                        <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${job.status === 'blocked' ? 'border-error/30 text-error' : 'border-success/30 text-success'}`}>
                          {job.status}
                        </span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(job.blockers.length ? job.blockers : ['passed']).slice(0, 6).map((blocker) => (
                          <span key={blocker} className="border border-border-default px-2 py-1 font-mono text-[9px] uppercase text-text-secondary">
                            {blocker === 'passed' ? 'Przeszło' : formatQualityBlocker(blocker)}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                  {!latestQualityRunJobs.length ? (
                    <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                      Uruchom audyt jakości, żeby zobaczyć świeży wynik.
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        </Panel>
      ) : null}

      <Panel title="Standardy operacyjne">
        <div className="space-y-4 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3 border border-border-default bg-void p-3">
            <div>
              <div className="font-mono text-[10px] uppercase text-text-tertiary">Źródło standardów</div>
              <div className="mt-1 font-display text-[14px] font-semibold uppercase text-text-primary">
                {operatingStandards?.status ? statusLabel(operatingStandards.status) : 'Ładowanie'}
              </div>
              <p className="mt-2 max-w-3xl text-xs leading-relaxed text-text-secondary">
                {operatingStandards?.summary ?? 'Czekam na standardy operacyjne z backendu.'}
              </p>
              {operatingStandards?.source.path ? (
                <p className="mt-2 break-all font-mono text-[9px] uppercase text-text-tertiary">
                  {operatingStandards.source.path}
                </p>
              ) : null}
            </div>
            <div className="grid grid-cols-2 gap-2 text-right">
              <div className="border border-border-default bg-panel px-3 py-2">
                <div className="font-mono text-[9px] uppercase text-text-tertiary">Required</div>
                <div className="font-display text-[15px] font-semibold uppercase text-accent-primary">
                  {operatingStandards?.metrics.required_count ?? 0}
                </div>
              </div>
              <div className="border border-border-default bg-panel px-3 py-2">
                <div className="font-mono text-[9px] uppercase text-text-tertiary">Blocked</div>
                <div className="font-display text-[15px] font-semibold uppercase text-warning">
                  {operatingStandards?.metrics.blocked_until_configured_count ?? 0}
                </div>
              </div>
            </div>
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            {(operatingStandards?.sections ?? []).map((section) => (
              <div key={section.key} className="border border-border-default bg-void p-3">
                <div className="font-display text-[13px] font-semibold uppercase tracking-[0.1em] text-text-primary">
                  {section.title}
                </div>
                <div className="mt-3 space-y-2">
                  {section.items.map((item) => (
                    <div key={item.key} className="border border-border-default bg-panel p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="font-display text-[12px] font-semibold uppercase text-text-primary">
                            {item.label}
                          </div>
                          <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">
                            Gate: {item.gate}
                          </div>
                        </div>
                        <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${statusClass(item.status)}`}>
                          {statusLabel(item.status)}
                        </span>
                      </div>
                      <p className="mt-2 text-xs leading-relaxed text-text-secondary">{item.purpose}</p>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {!operatingStandards?.sections?.length ? (
              <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary xl:col-span-2">
                Backend nie zwrócił jeszcze checklist standardów dla tego projektu.
              </div>
            ) : null}
          </div>

          {operatingStandards?.forbidden?.length ? (
            <div className="border border-error/30 bg-error/5 p-3">
              <div className="font-mono text-[10px] uppercase text-error">Zakazane bez dodatkowej konfiguracji</div>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                {operatingStandards.forbidden.map((item) => (
                  <div key={item} className="border border-error/20 bg-void p-2 text-xs leading-relaxed text-text-secondary">
                    {item}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Panel>

      {isYoutube ? (
        <Panel title="Cross-platform release cockpit">
          <div className="space-y-4 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3 border border-border-default bg-void p-3">
              <div>
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Status paczek</div>
                <div className={`mt-1 font-display text-[15px] font-semibold uppercase ${statusClass(distributionCockpit?.status)}`}>
                  {statusLabel(distributionCockpit?.status)}
                </div>
                <p className="mt-2 max-w-3xl text-xs leading-relaxed text-text-secondary">
                  YouTube moze uzywac API do prywatnego uploadu po review. TikTok i Instagram sa teraz export/manual package, dopoki nie ma osobnych konektorow.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void prepareCrossPlatformPackages()}
                  disabled={actionKey !== null || !distributionCockpit?.capabilities?.can_prepare_release_packages}
                  className="border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {actionKey === 'distribution-package-latest' ? 'Tworze paczki...' : 'Przygotuj paczki'}
                </button>
                <span className="border border-border-default px-3 py-2 font-mono text-[10px] uppercase text-text-tertiary">
                  Ready {distributionCockpit?.ready_package_count ?? 0}
                </span>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              {(distributionCockpit?.platforms ?? []).map((platform) => (
                <div key={platform.platform} className="border border-border-default bg-void p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-display text-[13px] font-semibold uppercase tracking-[0.1em] text-text-primary">
                        {platform.label}
                      </div>
                      <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">
                        {platform.connector_status} / konta {platform.account_count}
                      </div>
                    </div>
                    <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${statusClass(platform.status)}`}>
                      {statusLabel(platform.status)}
                    </span>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-2">
                    {[
                      ['Package', platform.release_package_ready],
                      ['API upload', platform.automatic_upload_ready],
                      ['Konto', platform.account_connected],
                      ['Manual', platform.requires_manual_upload],
                    ].map(([label, value]) => (
                      <div key={String(label)} className="border border-border-default bg-panel p-2">
                        <div className="font-mono text-[9px] uppercase text-text-tertiary">{label}</div>
                        <div className={`mt-1 font-display text-[12px] font-semibold uppercase ${boolClass(Boolean(value))}`}>
                          {boolLabel(Boolean(value))}
                        </div>
                      </div>
                    ))}
                  </div>
                  {platform.blockers.length ? (
                    <div className="mt-3 flex flex-wrap gap-1">
                      {platform.blockers.slice(0, 4).map((blocker) => (
                        <span key={blocker} className="border border-warning/30 px-2 py-1 font-mono text-[9px] uppercase text-warning">
                          {blocker.replaceAll('_', ' ')}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {platform.next_actions[0] ? (
                    <p className="mt-3 text-xs leading-relaxed text-text-secondary">{platform.next_actions[0]}</p>
                  ) : null}
                </div>
              ))}
              {!distributionCockpit?.platforms?.length ? (
                <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary md:col-span-3">
                  Brak jobow produkcyjnych gotowych do cross-platform release.
                </div>
              ) : null}
            </div>

            {distributionCockpit?.next_actions?.length ? (
              <div className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Nastepne akcje</div>
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  {distributionCockpit.next_actions.map((action) => (
                    <div key={action} className="border border-border-default bg-panel p-2 text-xs leading-relaxed text-text-secondary">
                      {action}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </Panel>
      ) : null}

      {isYoutube ? (
      <Panel title="Dystrybucja: YouTube / TikTok / Instagram">
        <div className="grid gap-3 p-4 md:grid-cols-3">
          {distributionTargets.map((target) => (
            <div key={target.platform} className="border border-border-default bg-void p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-display text-[13px] font-semibold uppercase tracking-[0.1em] text-text-primary">
                    {target.label}
                  </div>
                  <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">
                    {target.account_identifier || target.required_connection}
                  </div>
                </div>
                <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${statusClass(target.status)}`}>
                  {statusLabel(target.status)}
                </span>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2">
                {[
                  ['Konto', target.connected],
                  ['Prywatnie', target.ready_for_private],
                  ['Publicznie', target.ready_for_public],
                ].map(([label, value]) => (
                  <div key={String(label)} className="border border-border-default bg-panel p-2">
                    <div className="font-mono text-[9px] uppercase text-text-tertiary">{label}</div>
                    <div className={`mt-1 font-display text-[12px] font-semibold uppercase ${boolClass(Boolean(value))}`}>
                      {boolLabel(Boolean(value))}
                    </div>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-xs leading-relaxed text-text-secondary">{target.next_action}</p>
              {target.platform !== 'youtube' && target.status !== 'ready' ? (
                <button
                  type="button"
                  onClick={() => openPlatformConnection(target)}
                  className="mt-3 w-full border border-accent-primary/50 px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] text-accent-primary"
                >
                  Podepnij konto
                </button>
              ) : null}
            </div>
          ))}
          {!distributionTargets.length ? (
            <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary md:col-span-3">
              Ten projekt nie ma jeszcze skonfigurowanej mapy dystrybucji.
            </div>
          ) : null}
          {connectPlatform ? (
            <div className="border border-accent-primary/40 bg-accent-primary/5 p-4 md:col-span-3">
              <div className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                Podłącz konto: {connectPlatform}
              </div>
              <p className="mt-2 text-xs leading-relaxed text-text-secondary">
                Dane zapisują się w lokalnym backendzie Atnosa. Nie publikuję nic automatycznie; publiczny upload nadal wymaga osobnej bramki jakości i zgody.
              </p>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <label className="block">
                  <span className="font-mono text-[10px] uppercase text-text-tertiary">Login / e-mail / nazwa konta</span>
                  <input
                    value={connectIdentifier}
                    onChange={(event) => setConnectIdentifier(event.target.value)}
                    className="mt-1 w-full border border-border-default bg-void px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                    placeholder={connectPlatform === 'instagram' ? 'np. @konto_instagram' : 'np. @konto_tiktok'}
                  />
                </label>
                <label className="block">
                  <span className="font-mono text-[10px] uppercase text-text-tertiary">Hasło lub token sesji opcjonalnie</span>
                  <input
                    value={connectSecret}
                    onChange={(event) => setConnectSecret(event.target.value)}
                    type="password"
                    className="mt-1 w-full border border-border-default bg-void px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
                    placeholder="Puste = konto zapisane jako sesja do podpięcia później"
                  />
                </label>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={submitPlatformConnection}
                  disabled={actionKey !== null}
                  className="border border-accent-primary bg-accent-primary/10 px-4 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
                >
                  {actionKey?.startsWith('connect-') ? 'Zapisuję...' : 'Zapisz konto platformy'}
                </button>
                <button
                  type="button"
                  onClick={() => setConnectPlatform(null)}
                  disabled={actionKey !== null}
                  className="border border-border-default px-4 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Anuluj
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </Panel>
      ) : null}

      <Panel title="Stack narzedzi i zakupow">
        <div className="space-y-5 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3 border border-border-default bg-void p-3">
            <div>
              <div className="font-mono text-[10px] uppercase text-text-tertiary">Gotowosc stacku</div>
              <div className="mt-1 font-display text-[15px] font-semibold uppercase text-accent-primary">
                {toolingReadyCount(toolingPlan)} / {toolingTotalCount(toolingPlan)} ready
              </div>
            </div>
            <div className="max-w-2xl text-xs leading-relaxed text-text-secondary">
              Ten panel pokazuje narzedzia z researchu Atnosa jako operacyjna checkliste. Nie oznacza automatycznych zakupow ani publikacji.
            </div>
            {toolingPlan.report_ref ? (
              <div className="font-mono text-[9px] uppercase text-text-tertiary">
                Raport{toolingPlan.report_kind ? ` (${toolingPlan.report_kind.replaceAll('_', ' ')})` : ''}: {toolingPlan.report_ref}
              </div>
            ) : null}
          </div>
          <ToolingList title="Must-have teraz" items={toolingPlan.must_have ?? []} />
          <ToolingList title="Nice-to-have po walidacji" items={toolingPlan.nice_to_have ?? []} />
          <ToolingList title="Nie teraz / blokady" items={toolingPlan.not_now ?? []} />
          {!readinessProject?.tooling_plan ? (
            <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
              Pokazuje fallback checklisty, bo backend nie zwrocil jeszcze live tooling planu.
            </div>
          ) : null}
        </div>
      </Panel>

      {isInvestment ? (
        <InvestmentEvidencePanel
          cockpit={investmentCockpit}
          lastRun={lastInvestmentRun}
          lastRunUsesXtbQuotes={lastRunUsesXtbQuotes}
        />
      ) : (
      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Tematy">
          <div className="space-y-2 p-4">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">
              Kolejka: {topics.length}
            </div>
            {topics.slice(0, 5).map((topic) => (
              <div key={topic.id} className="border border-border-default bg-void p-3">
                <div className="font-display text-[12px] font-semibold uppercase text-text-primary">{topic.title}</div>
                <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                  Score {Math.round(topic.total_score)} / {topic.status}
                </div>
              </div>
            ))}
            {!topics.length ? (
              <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                Brak lokalnie pobranej kolejki dla tego projektu.
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel title="Produkcja i upload">
          <div className="space-y-2 p-4">
            {jobs.slice(0, 5).map((job) => (
              <div key={job.id} className="border border-border-default bg-void p-3">
                <div className="font-display text-[12px] font-semibold uppercase text-text-primary">
                  {job.topic_title || job.id}
                </div>
                <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                  {job.status} / {job.current_step ?? 'bez kroku'}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 font-mono text-[9px] uppercase text-text-tertiary">
                  <span>Review: {boolLabel(job.ready_for_review)}</span>
                  <span>Private: {boolLabel(job.ready_for_private_upload)}</span>
                  {job.youtube_url ? <a href={job.youtube_url} className="text-accent-primary">YouTube</a> : null}
                  <Link to={`/studio/${job.id}`} className="text-accent-primary">Studio</Link>
                </div>
              </div>
            ))}
            {!jobs.length ? (
              <div className="border border-dashed border-border-default p-4 text-sm text-text-tertiary">
                Brak ostatnich jobów produkcyjnych dla tego projektu.
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel title="Co wolno teraz">
          <div className="space-y-3 p-4">
            <div>
              <div className="font-mono text-[10px] uppercase text-success">Dozwolone</div>
              <div className="mt-2 space-y-1">
                {(readinessProject?.access_playbook?.allowed_now ?? []).map((item) => (
                  <div key={item} className="text-xs leading-relaxed text-text-secondary">{item}</div>
                ))}
              </div>
            </div>
            <div>
              <div className="font-mono text-[10px] uppercase text-warning">Zablokowane do konfiguracji</div>
              <div className="mt-2 space-y-1">
                {(readinessProject?.access_playbook?.blocked_until_configured ?? []).map((item) => (
                  <div key={item} className="text-xs leading-relaxed text-text-secondary">{item}</div>
                ))}
              </div>
            </div>
            <div className="border border-border-default bg-void p-3">
              <div className="font-mono text-[10px] uppercase text-text-tertiary">Następny krok</div>
              <div className="mt-1 text-sm font-semibold uppercase tracking-[0.08em] text-accent-primary">
                {readinessProject?.next_action ?? 'Brak danych z backendu.'}
              </div>
            </div>
          </div>
        </Panel>
      </div>
      )}
    </div>
  )
}

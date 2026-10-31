import { Link } from 'react-router-dom'
import { Check, ChevronDown, ChevronUp, Play, RefreshCw, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  createProductionDistributionPackage,
  deleteTopicQueueItem,
  ensureModelPipeline,
  fetchModelDownloadProgress,
  fetchMoneyMachineSummary,
  fetchExpenseNotifications,
  fetchProjectReadiness,
  fetchProcurementRequests,
  fetchProductionDelegationReviewStatus,
  fetchProductionDistributionPlan,
  fetchProductionJob,
  fetchProductionReadiness,
  fetchSmartSpendingStatus,
  fetchTopicQueue,
  generateTopics,
  runIntelligenceCollectors,
  createProductionDelegationReviewPlan,
  regenerateProductionJob,
  runProductionDelegationReviewAll,
  runProductionDelegationReviewNext,
  runMoneyMachinePublication,
  runMoneyMachineNext,
  runProductionContentLoopOnce,
  startProduction,
  uploadProductionJobPrivate,
  type MoneyMachineActiveJob,
  type MoneyMachineSummaryAccount,
  type MoneyMachineOperatorSummaryPayload,
  type MoneyMachineRunPublicationResult,
  type MoneyMachineSummaryJob,
  type ProjectReadinessPayload,
  type ProjectReadinessProject,
  updateTopicQueueStatus,
  type ProductionReadinessItem,
  type ProductionReadinessPayload,
  type ProductionDelegationReviewStatus,
  type ProductionDistributionPlan,
  type ProductionJobPayload,
  type ModelDownloadProgressPayload,
  type ExpenseNotificationItem,
  type TopicQueueItem,
  type TopicQueueStatus,
  type ProcurementRequestItem,
  type SmartSpendingStatus,
} from '../lib/api'
import { ProviderDoctorPanel } from '../components/ProviderDoctorPanel'
import { useAccountStore } from '../stores/accountStore'
import { useContentMachineStore } from '../stores/contentMachineStore'
import { useRevenueStore } from '../stores/revenueStore'
import type { Account, AccountType, PipelineStepStatus } from '../types/contentMachine'

const STEP_STATUS_CLASS: Record<PipelineStepStatus, string> = {
  idle: 'border-text-tertiary/25 text-text-tertiary',
  queued: 'border-text-secondary/30 bg-elevated/40 text-text-secondary',
  running: 'border-warning/50 bg-warning/10 text-warning',
  done: 'border-success/50 bg-success/10 text-success',
  error: 'border-error/50 bg-error/10 text-error',
}

const STEP_STATUS_LABELS: Record<PipelineStepStatus, string> = {
  idle: 'czeka',
  queued: 'w kolejce',
  running: 'dziala',
  done: 'gotowe',
  error: 'blad',
}

type TopicSortMode = 'score' | 'freshness' | 'shorts'
type TopicFilterMode = 'all' | TopicQueueStatus

const DIMENSION_LABELS: Array<[keyof TopicQueueItem['dimensions'], string, number]> = [
  ['viral_potential', 'Viral', 25],
  ['search_demand', 'Szukane', 20],
  ['competition_gap', 'Luka', 20],
  ['trend_velocity', 'Tempo', 15],
  ['niche_fit', 'Nisza', 10],
  ['freshness', 'Swieze', 10],
]

const TOPIC_STATUS_LABELS: Record<TopicQueueStatus, string> = {
  pending: 'OCZEKUJE',
  approved: 'ZATWIERDZONY',
  rejected: 'ODRZUCONY',
  in_production: 'W PRODUKCJI',
  done: 'GOTOWY',
}

const AUTOMATION_LABELS: Record<string, string> = {
  manual: 'Ręcznie',
  auto_private: 'Auto prywatnie',
  auto_public: 'Auto publicznie',
}

const READINESS_STATUS_LABELS: Record<string, string> = {
  ready: 'GOTOWE',
  ready_for_private_review: 'PRYWATNY REVIEW',
  degraded: 'OGRANICZONE',
  missing: 'BRAK',
  optional_missing: 'OPCJONALNE',
  needs_configuration: 'WYMAGA KONFIGURACJI',
}

const READINESS_STATUS_CLASS: Record<string, string> = {
  ready: 'border-success/40 bg-success/10 text-success',
  ready_for_private_review: 'border-accent-primary/50 bg-accent-primary/10 text-accent-primary',
  degraded: 'border-warning/50 bg-warning/10 text-warning',
  missing: 'border-error/50 bg-error/10 text-error',
  optional_missing: 'border-text-tertiary/30 bg-elevated/50 text-text-secondary',
  needs_configuration: 'border-warning/50 bg-warning/10 text-warning',
}

const OPERATOR_ACTION_LABELS: Record<string, string> = {
  review_and_execute_private_upload: 'Sprawdź i wyślij prywatnie',
  execute_recovery_regeneration: 'Regeneruj zablokowane joby',
  wait_for_active_production_job: 'Czekaj na aktywny render',
  connect_youtube_oauth: 'Połącz YouTube OAuth',
  generate_topics_from_intelligence: 'Wygeneruj tematy z danych',
  review_or_approve_best_topic: 'Zatwierdź najlepszy temat',
  monitor_and_repeat_daily: 'Monitoruj i powtarzaj dziennie',
  review_or_raise_monthly_api_budget: 'Sprawdź lub podnieś budżet API',
  cost_guard_budget_blocked: 'Budżet API blokuje produkcję',
}

const OPERATOR_BLOCKER_LABELS: Record<string, string> = {
  public_upload_disabled: 'Publiczny upload wyłączony',
  public_quality_gate_failed: 'Publiczny quality gate nie przeszedł',
  private_upload_public_quality_failed: 'Prywatne paczki nie spełniają progu publicznego',
  own_channel_analytics_missing: 'Brak analityki własnego kanału',
  topic_buffer_low: 'Bufor tematów jest niski',
  human_review_required_before_private_upload: 'Wymagany ludzki review przed prywatnym uploadem',
  premium_voice_required_for_public: 'Wymagany lepszy głos do publikacji publicznej',
  premium_visual_backend_required_for_public: 'Wymagany lepszy backend wizualny do publikacji publicznej',
  qa_not_passed_for_public: 'QA nie przeszło progu publicznego',
  scene_concreteness_failed: 'Sceny są zbyt ogólne względem tematu',
  visual_relevance_audit_missing_for_public: 'Brak audytu zgodności scen z tematem',
  visual_relevance_risk_for_public: 'Obraz nie jest wystarczająco związany z tematem',
  topic_anchor_risk_for_public: 'Sceny nie trzymają głównego tematu',
}

const PROJECT_TYPE_LABELS: Record<AccountType, string> = {
  youtube: 'YouTube Shorts',
  instagram_reels: 'Instagram Reels',
  tiktok: 'TikTok',
  onlyfans: 'Synthetic creator',
  investment: 'Inwestycje',
  custom: 'Custom',
}

function scoreClass(score: number) {
  if (score >= 70) {
    return 'border-success/60 bg-success/10 text-success'
  }
  if (score >= 40) {
    return 'border-warning/60 bg-warning/10 text-warning'
  }
  return 'border-error/60 bg-error/10 text-error'
}

function statusLabel(status: TopicQueueStatus) {
  return TOPIC_STATUS_LABELS[status]
}

function readinessStatusLabel(status: string) {
  return READINESS_STATUS_LABELS[status] ?? status.toUpperCase()
}

function readinessStatusClass(status: string) {
  return READINESS_STATUS_CLASS[status] ?? 'border-border-default text-text-secondary'
}

function readinessOverallLabel(readiness: ProductionReadinessPayload | null) {
  if (!readiness) return 'Sprawdzam'
  if (readiness.public_publish_ready || readiness.full_pipeline_ready) return 'Pełny publiczny gotowy'
  if (readiness.private_upload_ready) return 'Prywatny upload gotowy'
  if (readiness.private_review_ready || readiness.ready) return 'Prywatny review gotowy'
  return 'Wymaga konfiguracji'
}

function readinessOverallClass(readiness: ProductionReadinessPayload | null) {
  if (!readiness) return 'border-text-tertiary/30 bg-elevated/50 text-text-secondary'
  if (readiness.public_publish_ready || readiness.full_pipeline_ready) return 'border-success/50 bg-success/10 text-success'
  if (readiness.private_upload_ready || readiness.private_review_ready || readiness.ready) {
    return 'border-accent-primary/50 bg-accent-primary/10 text-accent-primary'
  }
  return 'border-warning/50 bg-warning/10 text-warning'
}

function distributionStatusLabel(status: string) {
  const labels: Record<string, string> = {
    api_private_ready: 'API prywatny gotowy',
    blocked: 'zablokowane',
    export_ready: 'export gotowy',
    manual_upload_required: 'manualny upload',
    private_upload_blocked: 'prywatny blok',
    private_upload_ready: 'prywatny gotowy',
    public_upload_ready: 'publiczny gotowy',
    uploaded_private: 'prywatnie wrzucone',
    scheduled_public: 'zaplanowane',
    published_public: 'publiczne',
  }
  return labels[status] ?? status.replaceAll('_', ' ')
}

function distributionStatusClass(status: string) {
  if (['public_upload_ready', 'published_public', 'scheduled_public'].includes(status)) {
    return 'border-success/60 bg-success/10 text-success'
  }
  if (['api_private_ready', 'uploaded_private', 'manual_upload_required', 'export_ready'].includes(status)) {
    return 'border-accent-primary/60 bg-accent-primary/10 text-accent-primary'
  }
  if (status.includes('block')) {
    return 'border-error/60 bg-error/10 text-error'
  }
  return 'border-warning/60 bg-warning/10 text-warning'
}

function productionReviewStatusLabel(status: string | undefined) {
  const labels: Record<string, string> = {
    missing: 'Brak planu',
    planned: 'Plan gotowy',
    queued: 'W kolejce',
    running: 'W trakcie',
    completed: 'Wykonane',
    blocked: 'Zablokowane',
    failed: 'Błąd',
    no_executable_task: 'Brak taska do uruchomienia',
  }
  return status ? labels[status] ?? status.replaceAll('_', ' ') : 'Brak danych'
}

function productionReviewStatusClass(status: string | undefined) {
  if (status === 'planned' || status === 'completed') return 'border-success/50 bg-success/10 text-success'
  if (status === 'running' || status === 'queued') return 'border-accent-primary/50 bg-accent-primary/10 text-accent-primary'
  if (status === 'blocked' || status === 'failed' || status === 'missing') return 'border-error/50 bg-error/10 text-error'
  return 'border-warning/50 bg-warning/10 text-warning'
}

function productionReviewCounts(status: ProductionDelegationReviewStatus | null) {
  const counts = status?.counts
  return {
    total: counts?.latest_child_tasks ?? 0,
    review: counts?.latest_review_tasks ?? 0,
    completedReview: counts?.latest_completed_review_tasks ?? 0,
    queued: counts?.latest_queued_child_tasks ?? 0,
    running: counts?.latest_running_child_tasks ?? 0,
    completed: counts?.latest_completed_child_tasks ?? 0,
    blocked: counts?.latest_blocked_child_tasks ?? 0,
    failed: counts?.latest_failed_child_tasks ?? 0,
  }
}

function productionReviewBlockerLabel(blocker: string) {
  if (blocker === 'production_review_plan_missing') return 'Brak planu review dla tego tematu'
  if (blocker === 'production_review_child_tasks_missing') return 'Plan review nie ma podzadań'
  if (blocker === 'production_required_review_task_missing') return 'Brak wymaganego taska jakościowego'
  if (blocker.startsWith('production_review_plan_status_')) return `Plan ma status: ${blocker.replace('production_review_plan_status_', '')}`
  if (blocker.startsWith('production_review_tasks_queued:')) return `Czekające taski: ${blocker.split(':')[1] ?? '?'}`
  if (blocker.startsWith('production_review_tasks_running:')) return `Taski w trakcie: ${blocker.split(':')[1] ?? '?'}`
  if (blocker.startsWith('production_review_tasks_blocked:')) return `Zablokowane taski: ${blocker.split(':')[1] ?? '?'}`
  if (blocker.startsWith('production_review_tasks_failed:')) return `Błędne taski: ${blocker.split(':')[1] ?? '?'}`
  if (blocker.startsWith('production_review_tasks_incomplete:')) return `Nieukończone taski: ${blocker.split(':')[1] ?? '?'}`
  if (blocker.startsWith('production_required_review_incomplete:')) return `Nieukończone taski jakościowe: ${blocker.split(':')[1] ?? '?'}`
  return blocker.replaceAll('_', ' ')
}

function productionReviewVerdictLabel(verdict: string | undefined) {
  if (verdict === 'production_ready') return 'Można produkować'
  if (verdict === 'blocked') return 'Produkcja zablokowana'
  return verdict ?? 'Brak werdyktu'
}

function productionReviewActionLabel(action: string | undefined) {
  const labels: Record<string, string> = {
    'Create review plan.': 'Utwórz plan review dla wybranego tematu.',
    'Run all review tasks.': 'Uruchom wszystkie taski review i odśwież werdykt.',
    'Wait for running review task.': 'Poczekaj na zakończenie trwającego taska review.',
    'Open blocked/failed review task and fix its reason.': 'Otwórz błędny albo zablokowany task i usuń przyczynę.',
    'Start production or review generated task outputs before rendering.': 'Możesz rozpocząć produkcję albo ręcznie przejrzeć wyniki review.',
    'Inspect production review blockers.': 'Sprawdź blokery production review.',
  }
  return action ? labels[action] ?? action : 'Brak następnego kroku'
}

function operatorActionLabel(action: string | undefined) {
  if (!action) return 'Brak akcji'
  return OPERATOR_ACTION_LABELS[action] ?? OPERATOR_BLOCKER_LABELS[action] ?? action.replaceAll('_', ' ')
}

function formatPln(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '-'
  return `${value.toFixed(value >= 10 ? 0 : 2)} PLN`
}

function budgetUsagePercent(spent: number, limit: number | null | undefined) {
  if (limit == null || limit <= 0) return 0
  return Math.max(0, Math.min(100, (spent / limit) * 100))
}

function costGuardStatusLabel(costGuard: MoneyMachineOperatorSummaryPayload['cost_guard']) {
  if (!costGuard) return 'brak danych'
  if (costGuard.enabled === false) return 'wyłączony'
  if (costGuard.blocked || costGuard.allowed === false) return 'blokada'
  if (costGuard.warning) return 'ostrzeżenie'
  return 'ok'
}

function costGuardStatusClass(costGuard: MoneyMachineOperatorSummaryPayload['cost_guard']) {
  if (!costGuard || costGuard.enabled === false) return 'border-text-tertiary/30 bg-elevated/40 text-text-secondary'
  if (costGuard.blocked || costGuard.allowed === false) return 'border-error/50 bg-error/10 text-error'
  if (costGuard.warning) return 'border-warning/50 bg-warning/10 text-warning'
  return 'border-success/50 bg-success/10 text-success'
}

function smartSpendingStateClass(state: string | undefined) {
  if (state === 'profitable') return 'border-success/50 bg-success/10 text-success'
  if (state === 'no_cash') return 'border-error/50 bg-error/10 text-error'
  if (state === 'seed_being_spent') return 'border-warning/50 bg-warning/10 text-warning'
  return 'border-accent-primary/50 bg-accent-primary/10 text-accent-primary'
}

function smartSpendingStateLabel(state: string | undefined) {
  const labels: Record<string, string> = {
    bootstrap: 'bootstrap',
    seed_being_spent: 'wydaje seed',
    profitable: 'rentowny',
    no_cash: 'brak kasy',
  }
  return state ? labels[state] ?? state.replaceAll('_', ' ') : 'brak danych'
}

function ledgerEntryLabel(entryType: string) {
  const labels: Record<string, string> = {
    seed_capital: 'seed',
    revenue: 'przychód',
    expense_reserved: 'rezerwacja',
    expense_actual: 'wydatek',
    expense_released: 'zwolnione',
  }
  return labels[entryType] ?? entryType.replaceAll('_', ' ')
}

function activeMoneyJobStatusClass(job: MoneyMachineActiveJob) {
  if (job.stale_candidate) return 'border-error/50 bg-error/10 text-error'
  if (job.status === 'running') return 'border-success/50 bg-success/10 text-success'
  if (job.status === 'queued') return 'border-warning/50 bg-warning/10 text-warning'
  return 'border-text-tertiary/30 bg-elevated/40 text-text-secondary'
}

function formatRuntimeSeconds(seconds: number | null | undefined) {
  if (seconds == null || Number.isNaN(seconds)) return '-'
  const safeSeconds = Math.max(0, Math.round(seconds))
  if (safeSeconds < 60) return `${safeSeconds}s`
  const minutes = Math.floor(safeSeconds / 60)
  const rest = safeSeconds % 60
  if (minutes < 60) return `${minutes}m ${rest}s`
  const hours = Math.floor(minutes / 60)
  const minuteRest = minutes % 60
  return `${hours}h ${minuteRest}m`
}

function formatCount(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '-'
  return new Intl.NumberFormat('pl-PL').format(value)
}

function accountOperationalState(account: MoneyMachineSummaryAccount) {
  const blockers = account.blockers ?? []
  const activeCount = account.job_counts?.active ?? account.active_jobs ?? account.active_job_details?.length ?? 0
  const readyCount = account.job_counts?.ready_private ?? account.ready_private_upload_count ?? 0
  const oauthReady = account.youtube_oauth?.ready ?? account.oauth_ready
  if (!oauthReady) {
    return { label: 'Wymaga OAuth', className: 'border-error/50 bg-error/10 text-error' }
  }
  if (
    blockers.includes('public_quality_gate_failed') ||
    blockers.includes('private_upload_public_quality_failed') ||
    blockers.includes('topic_anchor_risk_for_public') ||
    blockers.includes('visual_relevance_risk_for_public') ||
    blockers.includes('visual_relevance_audit_missing_for_public')
  ) {
    return { label: 'Jakość blokuje', className: 'border-warning/50 bg-warning/10 text-warning' }
  }
  if (activeCount > 0) {
    return { label: 'Produkuje', className: 'border-success/50 bg-success/10 text-success' }
  }
  if (readyCount > 0) {
    return { label: 'Do review', className: 'border-accent-primary/50 bg-accent-primary/10 text-accent-primary' }
  }
  if (blockers.length > 0) {
    return { label: 'Wymaga uwagi', className: 'border-warning/50 bg-warning/10 text-warning' }
  }
  return { label: 'Gotowy', className: 'border-success/50 bg-success/10 text-success' }
}

function projectOperationalState(project: Account) {
  if (project.status === 'live') {
    return {
      label: 'Aktywny',
      className: 'border-success/50 bg-success/10 text-success',
      detail: 'Projekt ma aktywny status w lokalnym panelu.',
    }
  }
  if (project.status === 'error') {
    return {
      label: 'Błąd',
      className: 'border-error/50 bg-error/10 text-error',
      detail: String(project.platformConfig.intelligenceLastMessage ?? 'Projekt wymaga naprawy konfiguracji.'),
    }
  }
  if (project.type === 'onlyfans') {
    return {
      label: 'Wymaga zasad',
      className: 'border-warning/50 bg-warning/10 text-warning',
      detail: 'Podłącz konto twórcze, Vault i zasady publikacji przed automatyzacją.',
    }
  }
  if (project.type === 'investment') {
    return {
      label: 'Wymaga brokera',
      className: 'border-warning/50 bg-warning/10 text-warning',
      detail: 'Podłącz dane portfela, limity ryzyka i reguły zatwierdzania decyzji.',
    }
  }
  return {
    label: 'Konfiguracja',
    className: 'border-text-tertiary/30 bg-elevated/40 text-text-secondary',
    detail: String(project.platformConfig.requiredAccess ?? 'Uzupełnij dostępy i harmonogram.'),
  }
}

function projectReadinessLabel(status: string | undefined) {
  if (status === 'ready') return 'Gotowy'
  if (status === 'limited') return 'Ograniczony'
  if (status === 'blocked') return 'Blokada'
  return status ? status.replaceAll('_', ' ') : 'Brak danych'
}

function projectReadinessClass(status: string | undefined) {
  if (status === 'ready') return 'border-success/50 bg-success/10 text-success'
  if (status === 'limited') return 'border-warning/50 bg-warning/10 text-warning'
  if (status === 'blocked') return 'border-error/50 bg-error/10 text-error'
  return 'border-text-tertiary/30 bg-elevated/40 text-text-secondary'
}

function projectRequirementAccess(project: ProjectReadinessProject) {
  return project.requirements.slice(0, 4).map((requirement) => [
    requirement.label.split(' ')[0] || requirement.id,
    requirement.status === 'ready',
  ]) as Array<[string, boolean | null | undefined]>
}

function playbookStatusClass(status: string | undefined) {
  if (status === 'ready') return 'border-success/40 bg-success/10 text-success'
  if (status === 'limited') return 'border-warning/40 bg-warning/10 text-warning'
  return 'border-error/40 bg-error/10 text-error'
}

function playbookStatusLabel(status: string | undefined) {
  if (status === 'ready') return 'Gotowe'
  if (status === 'limited') return 'Ograniczone'
  if (status === 'missing') return 'Brak'
  if (status === 'blocked') return 'Blokada'
  return status ? status.replaceAll('_', ' ') : 'Brak danych'
}

function playbookModeLabel(mode: string | undefined) {
  const labels: Record<string, string> = {
    supervised_private_upload: 'Nadzorowany prywatny upload',
    draft_only_until_creator_access: 'Drafty do czasu podłączenia kont',
    research_only_until_broker_read_access: 'Research do czasu read-only brokera',
    local_setup_required: 'Wymaga lokalnej konfiguracji',
  }
  return mode ? labels[mode] ?? mode.replaceAll('_', ' ') : 'Brak trybu'
}

function ProductionReviewPanel({
  topic,
  status,
  loading,
  actionLoading,
  error,
  lastRun,
  onRefresh,
  onPlan,
  onRunNext,
  onRunAll,
}: {
  topic: TopicQueueItem | null
  status: ProductionDelegationReviewStatus | null
  loading: boolean
  actionLoading: boolean
  error: string | null
  lastRun: string | null
  onRefresh: () => void
  onPlan: () => void
  onRunNext: () => void
  onRunAll: () => void
}) {
  const counts = productionReviewCounts(status)
  const canRunNext = Boolean(status?.has_review_plan && (status.ready_for_execution || counts.queued > 0 || counts.failed > 0))
  const total = counts.total || status?.root_task?.children.length || 0
  const gateBlockers = status?.production_gate_blockers ?? []
  const summary = status?.review_summary
  const summaryEvidence = summary?.evidence
  const resultSummaries = summaryEvidence?.latest_result_summaries ?? []
  const reviewEvidence = status
    ? `${counts.completed}/${total} wykonane · review ${counts.completedReview}/${counts.review} · ${counts.queued} czeka · ${counts.blocked + counts.failed} blokady/błędy`
    : 'Brak pobranego statusu review.'

  return (
    <Panel title="Production review">
      <div className="space-y-3 p-3">
        <div className="border border-border-default bg-void p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-mono text-[10px] uppercase text-text-tertiary">Temat kontrolowany</div>
              <div className="mt-1 line-clamp-2 font-display text-[13px] uppercase tracking-[0.08em] text-text-primary">
                {topic?.title ?? 'Brak tematu w kolejce'}
              </div>
              {topic ? (
                <div className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">
                  status tematu: {statusLabel(topic.status)} · score: {Math.round(topic.total_score)}
                </div>
              ) : null}
            </div>
            <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${productionReviewStatusClass(status?.status)}`}>
              {loading ? 'Ładuję' : productionReviewStatusLabel(status?.status)}
            </span>
          </div>

          <div className="mt-3 grid grid-cols-4 gap-2 font-mono text-[10px] uppercase">
            <div className="border border-border-default bg-panel p-2">
              <div className="text-text-tertiary">Taski</div>
              <div className="mt-1 text-text-primary">{total}</div>
            </div>
            <div className="border border-border-default bg-panel p-2">
              <div className="text-text-tertiary">Done</div>
              <div className="mt-1 text-success">{counts.completed}</div>
            </div>
            <div className="border border-border-default bg-panel p-2">
              <div className="text-text-tertiary">Queue</div>
              <div className="mt-1 text-accent-primary">{counts.queued}</div>
            </div>
            <div className="border border-border-default bg-panel p-2">
              <div className="text-text-tertiary">Block</div>
              <div className="mt-1 text-warning">{counts.blocked + counts.failed}</div>
            </div>
          </div>

          <div className="mt-3 text-xs text-text-secondary">{status?.reason ?? reviewEvidence}</div>
          {summary ? (
            <div className={`mt-3 border p-3 ${summary.can_start_production ? 'border-success/50 bg-success/10' : 'border-warning/50 bg-warning/10'}`}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Werdykt review</div>
                  <div className={`mt-1 font-display text-[13px] uppercase tracking-[0.08em] ${summary.can_start_production ? 'text-success' : 'text-warning'}`}>
                    {productionReviewVerdictLabel(summary.verdict)}
                  </div>
                </div>
                <div className="font-mono text-[10px] uppercase text-text-tertiary">
                  {summary.completed_tasks}/{summary.total_tasks} taski
                </div>
              </div>
              <div className="mt-2 text-xs text-text-secondary">{summary.headline}</div>
              <div className="mt-2 border border-border-default bg-panel p-2 text-xs text-text-secondary">
                Następny krok: {productionReviewActionLabel(summary.first_action)}
              </div>
              <div className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">
                Źródło: {summaryEvidence?.source ?? 'brak'} · root: {summaryEvidence?.root_task_id ?? '-'} · child: {summaryEvidence?.child_task_ids.length ?? 0}
              </div>
              {resultSummaries.length ? (
                <div className="mt-2 space-y-1">
                  {resultSummaries.slice(0, 2).map((item, index) => (
                    <div key={`${index}-${item}`} className="border border-border-default bg-void p-2 text-xs text-text-secondary">
                      {item}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          {gateBlockers.length ? (
            <div className="mt-2 space-y-1 border border-warning/40 bg-warning/10 p-2 font-mono text-[10px] uppercase text-warning">
              {gateBlockers.slice(0, 5).map((blocker) => (
                <div key={blocker}>{productionReviewBlockerLabel(blocker)}</div>
              ))}
            </div>
          ) : null}
          <div className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">
            Dane z DB: {reviewEvidence}
          </div>
          {status?.next_action ? (
            <div className="mt-2 border border-border-default bg-panel p-2 text-xs text-text-secondary">
              Następny krok: {status.next_action}
            </div>
          ) : null}
          {lastRun ? (
            <div className="mt-2 border border-accent-primary/40 bg-accent-primary/10 p-2 text-xs text-accent-primary">
              Ostatni wynik: {lastRun}
            </div>
          ) : null}
          {error ? (
            <div className="mt-2 border border-error/40 bg-error/10 p-2 text-xs text-error">
              {error}
            </div>
          ) : null}
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={!topic || actionLoading}
            onClick={onPlan}
            className="border border-accent-primary/50 bg-accent-primary/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-50"
          >
            {actionLoading ? 'Pracuję...' : status?.has_review_plan ? 'Odśwież plan' : 'Utwórz plan'}
          </button>
          <button
            type="button"
            disabled={!topic || actionLoading || !canRunNext}
            onClick={onRunNext}
            className="border border-success/50 bg-success/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-success disabled:cursor-wait disabled:opacity-50"
          >
            {actionLoading ? 'Uruchamiam...' : 'Run next review task'}
          </button>
          <button
            type="button"
            disabled={!topic || actionLoading || !canRunNext}
            onClick={onRunAll}
            className="border border-success/50 bg-success/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-success disabled:cursor-wait disabled:opacity-50"
          >
            {actionLoading ? 'Uruchamiam...' : 'Run all review tasks'}
          </button>
          <button
            type="button"
            disabled={!topic || loading}
            onClick={onRefresh}
            className="border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary disabled:cursor-wait disabled:opacity-50"
          >
            {loading ? 'Odświeżam...' : 'Odśwież status'}
          </button>
        </div>
      </div>
    </Panel>
  )
}

function humanProjectAction(action: string | undefined) {
  if (!action) return 'Brak akcji'
  if (!action.includes('_')) return action
  return operatorActionLabel(action)
}

function splitConfigList(value: string | string[] | undefined): string[] {
  const raw = Array.isArray(value) ? value.join('\n') : String(value ?? '')
  return raw
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function projectNiches(project: Account, readiness?: ProjectReadinessProject): string[] {
  const evidence = readiness?.evidence?.niches
  if (Array.isArray(evidence)) {
    const values = evidence.map((item) => String(item).trim()).filter(Boolean)
    if (values.length > 0) return values
  }
  const configured = splitConfigList(project.platformConfig.niches)
  if (configured.length > 0) return configured
  return splitConfigList(project.niche)
}

function findProjectChannel(project: Account, channels: MoneyMachineSummaryAccount[]) {
  if (project.type !== 'youtube') {
    return undefined
  }
  const configuredChannelId = String(project.platformConfig.channelId ?? '')
  return channels.find((channel) => {
    const mappedEmail = channel.youtube_oauth?.mapped_email ?? ''
    return (
      channel.account_id === project.id ||
      channel.account_id === configuredChannelId ||
      mappedEmail.length > 0 && project.id.includes(mappedEmail)
    )
  })
}

function projectControlSnapshot(project: Account, channel?: MoneyMachineSummaryAccount) {
  if (channel) {
    const state = accountOperationalState(channel)
    const blockers = channel.blockers ?? []
    const activeCount = channel.job_counts?.active ?? channel.active_jobs ?? channel.active_job_details?.length ?? 0
    const readyPrivate = channel.job_counts?.ready_private ?? channel.ready_private_upload_count ?? 0
    const topicBuffer = channel.topic_counts?.buffer_count ?? channel.topic_buffer ?? 0
    const approvedTopics = channel.topic_counts?.approved ?? channel.approved_topics ?? 0
    const ownAnalyticsRows = channel.analytics?.own_performance_rows ?? channel.own_performance_rows ?? 0
    return {
      label: state.label,
      className: state.className,
      action: operatorActionLabel(channel.next_action),
      detail: 'Live dane z backendu money-machine dla tego kanału YouTube.',
      metrics: [
        ['Bufor', formatCount(topicBuffer)],
        ['Zatwierdzone', formatCount(approvedTopics)],
        ['Prywatne', formatCount(readyPrivate)],
        ['Aktywne', formatCount(activeCount)],
      ],
      access: [
        ['OAuth', channel.youtube_oauth?.ready ?? channel.oauth_ready],
        ['Miniatury', channel.upload_capabilities?.custom_thumbnail?.ready ?? channel.custom_thumbnail_ready],
        ['Napisy API', channel.upload_capabilities?.captions_api?.ready ?? channel.captions_api_ready],
        ['Analytics', ownAnalyticsRows > 0],
      ] as Array<[string, boolean | null | undefined]>,
      blockers,
    }
  }

  if (project.type === 'onlyfans') {
    return {
      label: 'Wymaga podłączenia',
      className: 'border-warning/50 bg-warning/10 text-warning',
      action: 'Podłącz konto twórcy, zasady treści i vault assetów',
      detail: 'Tryb bezpieczny: Atnos może przygotowywać drafty, ale publikacja i płatności wymagają jawnych zasad oraz akceptacji.',
      metrics: [
        ['Drafty', '0'],
        ['Assety', '0'],
        ['Publikacje', 'OFF'],
        ['Płatności', 'OFF'],
      ],
      access: [
        ['Konto', false],
        ['Vault', false],
        ['Zasady', false],
        ['Billing', false],
      ] as Array<[string, boolean | null | undefined]>,
      blockers: ['creator_account_missing', 'content_policy_missing', 'payment_rules_missing'],
    }
  }

  if (project.type === 'investment') {
    return {
      label: 'Research only',
      className: 'border-warning/50 bg-warning/10 text-warning',
      action: 'Podłącz brokera w trybie read-only i limity ryzyka',
      detail: 'Tryb bezpieczny: analizy i alerty są OK; egzekucja transakcji zostaje wyłączona bez osobnej zgody i limitów.',
      metrics: [
        ['Lista', '0'],
        ['Alerty', '0'],
        ['Trade', 'OFF'],
        ['Ryzyko', 'BRAK'],
      ],
      access: [
        ['Broker', false],
        ['Pozycje', false],
        ['Limity', false],
        ['Akceptacje', false],
      ] as Array<[string, boolean | null | undefined]>,
      blockers: ['broker_read_access_missing', 'risk_policy_missing', 'trade_approval_rules_missing'],
    }
  }

  const state = projectOperationalState(project)
  return {
    label: state.label,
    className: state.className,
    action: 'Uzupełnij konfigurację projektu',
    detail: state.detail,
    metrics: [
      ['Kolejka', formatCount(project.stats.queueSize)],
      ['Dzisiaj', formatCount(project.stats.videosToday)],
      ['Posty/d', formatCount(project.schedule.postsPerDay)],
      ['Auto', AUTOMATION_LABELS[project.automationMode] ?? project.automationMode.replaceAll('_', ' ')],
    ],
    access: [
      ['Konto', project.status === 'live'],
      ['Config', Boolean(project.platformConfig.channelId || project.platformConfig.requiredAccess)],
      ['QA', null],
      ['Upload', null],
    ] as Array<[string, boolean | null | undefined]>,
    blockers: project.platformConfig.requiredAccess ? ['project_access_missing'] : [],
  }
}

function readinessDotClass(ready: boolean | null | undefined) {
  if (ready) return 'bg-success'
  if (ready === false) return 'bg-error'
  return 'bg-text-tertiary'
}

function sortReadinessItems(items: ProductionReadinessItem[]) {
  const weight: Record<string, number> = {
    missing: 0,
    degraded: 1,
    optional_missing: 2,
    ready: 3,
  }
  return [...items].sort((a, b) => {
    const requiredDelta = Number(b.required) - Number(a.required)
    if (requiredDelta !== 0) return requiredDelta
    return (weight[a.status] ?? 9) - (weight[b.status] ?? 9)
  })
}

function modelMissingDetails(item: ProductionReadinessItem) {
  const raw = item.evidence?.missing_details
  if (!Array.isArray(raw)) return []
  return raw
    .map((entry) => {
      const record = entry as Record<string, unknown>
      return {
        id: String(record.model_id ?? ''),
        name: String(record.name ?? record.model_id ?? ''),
        sizeGb: Number(record.size_gb ?? 0),
        status: String(record.status ?? 'missing'),
      }
    })
    .filter((entry) => entry.id)
}

function isProductionTerminalStatus(status: string | undefined) {
  return ['qa_passed', 'qa_failed', 'error', 'uploaded_private', 'done'].includes(String(status ?? ''))
}

function Panel({ title, children, className = '' }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={`border border-border-default bg-panel ${className}`}>
      <div className="border-b border-border-default px-4 py-3 font-display text-[12px] font-semibold uppercase tracking-[0.16em] text-text-primary">
        {title}
      </div>
      {children}
    </section>
  )
}

export default function DashboardRoute() {
  const accounts = useAccountStore((state) => state.accounts)
  const activeAccountId = useAccountStore((state) => state.activeAccountId)
  const setActiveAccount = useAccountStore((state) => state.setActiveAccount)
  const account = accounts.find((item) => item.id === activeAccountId) ?? accounts[0]
  const jobs = useContentMachineStore((state) => state.jobs)
  const queue = useContentMachineStore((state) => state.queue)
  const logs = useContentMachineStore((state) => state.logs)
  const activeJobs = useContentMachineStore((state) => state.activeJobs)
  const ingestPipelineStep = useContentMachineStore((state) => state.ingestPipelineStep)
  const appendLog = useContentMachineStore((state) => state.appendLog)
  const getPoints = useRevenueStore((state) => state.getPoints)
  const getStats = useRevenueStore((state) => state.getStats)
  const [topicQueue, setTopicQueue] = useState<TopicQueueItem[]>([])
  const [topicLoading, setTopicLoading] = useState(false)
  const [topicError, setTopicError] = useState<string | null>(null)
  const [topicSort, setTopicSort] = useState<TopicSortMode>('score')
  const [topicFilter, setTopicFilter] = useState<TopicFilterMode>('pending')
  const [showLowScore, setShowLowScore] = useState(false)
  const [expandedTopicId, setExpandedTopicId] = useState<string | null>(null)
  const [readiness, setReadiness] = useState<ProductionReadinessPayload | null>(null)
  const [readinessLoading, setReadinessLoading] = useState(false)
  const [readinessError, setReadinessError] = useState<string | null>(null)
  const [modelEnsureStatus, setModelEnsureStatus] = useState<string | null>(null)
  const [modelEnsureLoading, setModelEnsureLoading] = useState(false)
  const [modelDownload, setModelDownload] = useState<ModelDownloadProgressPayload | null>(null)
  const [productionJobs, setProductionJobs] = useState<Record<string, ProductionJobPayload>>({})
  const [distributionPlans, setDistributionPlans] = useState<Record<string, ProductionDistributionPlan>>({})
  const [distributionPlanError, setDistributionPlanError] = useState<string | null>(null)
  const [distributionPackageLoadingJobId, setDistributionPackageLoadingJobId] = useState<string | null>(null)
  const [distributionPackageStatus, setDistributionPackageStatus] = useState<string | null>(null)
  const [privateUploadJobId, setPrivateUploadJobId] = useState<string | null>(null)
  const [moneySummary, setMoneySummary] = useState<MoneyMachineOperatorSummaryPayload | null>(null)
  const [moneySummaryLoading, setMoneySummaryLoading] = useState(false)
  const [moneySummaryError, setMoneySummaryError] = useState<string | null>(null)
  const [projectReadiness, setProjectReadiness] = useState<ProjectReadinessPayload | null>(null)
  const [projectReadinessError, setProjectReadinessError] = useState<string | null>(null)
  const [projectActionLoadingId, setProjectActionLoadingId] = useState<string | null>(null)
  const [recoveryJobId, setRecoveryJobId] = useState<string | null>(null)
  const [moneyActionLoading, setMoneyActionLoading] = useState(false)
  const [contentLoopLoading, setContentLoopLoading] = useState(false)
  const [smartSpending, setSmartSpending] = useState<SmartSpendingStatus | null>(null)
  const [procurementRequests, setProcurementRequests] = useState<ProcurementRequestItem[]>([])
  const [expenseNotifications, setExpenseNotifications] = useState<ExpenseNotificationItem[]>([])
  const [procurementError, setProcurementError] = useState<string | null>(null)
  const [procurementLoading, setProcurementLoading] = useState(false)
  const [publicPromotionPlan, setPublicPromotionPlan] = useState<MoneyMachineRunPublicationResult | null>(null)
  const [publicPromotionLoading, setPublicPromotionLoading] = useState(false)
  const [publicPromotionError, setPublicPromotionError] = useState<string | null>(null)
  const [publicPromotionExecuting, setPublicPromotionExecuting] = useState(false)
  const [publicPublishAt, setPublicPublishAt] = useState('')
  const [productionReviewStatus, setProductionReviewStatus] = useState<ProductionDelegationReviewStatus | null>(null)
  const [productionReviewLoading, setProductionReviewLoading] = useState(false)
  const [productionReviewActionLoading, setProductionReviewActionLoading] = useState(false)
  const [productionReviewError, setProductionReviewError] = useState<string | null>(null)
  const [productionReviewLastRun, setProductionReviewLastRun] = useState<string | null>(null)
  const [selectedProductionReviewTopicId, setSelectedProductionReviewTopicId] = useState<string | null>(null)

  const accountId = account?.id ?? ''
  const accountQueue = account ? queue[account.id] ?? [] : []
  const queueJobs = accountQueue.map((jobId) => jobs[jobId]).filter(Boolean)
  const activeJob = account ? jobs[activeJobs[account.id]] ?? queueJobs[0] : undefined
  const activeProductionJob = activeJob ? productionJobs[activeJob.id] : undefined
  const activeDistributionPlan = activeJob ? distributionPlans[activeJob.id] : undefined
  const accountLogs = account ? logs[account.id] ?? [] : []
  const revenue = account ? getPoints(account.id) : []
  const stats = account ? getStats(account.id) : { rpm: 0, cpm: 0, watchTimeHours: 0, ctr: 0 }
  const maxRevenue = Math.max(...revenue.map((point) => point.adsense + point.affiliate + point.sponsors), 1)
  const schedulerReadiness = readiness?.items.find((item) => item.key === 'scheduler')
  const schedulerEvidence = (schedulerReadiness?.evidence ?? {}) as Record<string, unknown>
  const productionAutopilotAutomatic = Boolean(schedulerEvidence.production_autopilot_automatic)
  const productionAutopilotInterval = Number(schedulerEvidence.production_autopilot_interval_minutes ?? 0)
  const costGuard = moneySummary?.cost_guard
  const costGuardBlocksProduction = Boolean(costGuard?.enabled !== false && (costGuard?.blocked || costGuard?.allowed === false))
  const monthlyBudgetPct = budgetUsagePercent(costGuard?.monthly_spent_pln ?? 0, costGuard?.monthly_limit_pln)
  const dailyBudgetPct = budgetUsagePercent(costGuard?.daily_spent_pln ?? 0, costGuard?.daily_limit_pln)
  const publicPromotionReady = publicPromotionPlan?.planned ?? []
  const publicPromotionBlocked = publicPromotionPlan?.blocked ?? []
  const publicPublishAtReady = publicPublishAt.trim().length > 0
  const activeMoneyJobs = moneySummary?.active_jobs ?? []
  const topActiveMoneyJob = activeMoneyJobs[0]
  const topPrivateReviewJob = moneySummary?.private_upload_ready[0]
  const topRecoveryReviewJob = moneySummary?.recovery_ready[0]
  const accountControlRows = moneySummary?.accounts ?? []
  const projectReadinessById = useMemo(() => {
    return new Map((projectReadiness?.projects ?? []).map((project) => [project.project_id, project]))
  }, [projectReadiness])
  const moneySafeActionAvailable = Boolean(
    moneySummary &&
    (
      moneySummary.private_upload_ready.length > 0 ||
      moneySummary.recovery_ready.length > 0 ||
      moneySummary.next_operator_action === 'review_or_approve_best_topic'
    ),
  )
  const productionReviewTopic = useMemo(() => {
    if (!topicQueue.length) {
      return null
    }
    const selected = selectedProductionReviewTopicId
      ? topicQueue.find((topic) => topic.id === selectedProductionReviewTopicId)
      : undefined
    if (selected) {
      return selected
    }
    const candidates = [...topicQueue].sort((a, b) => b.total_score - a.total_score)
    return (
      candidates.find((topic) => topic.status === 'in_production') ??
      candidates.find((topic) => topic.status === 'approved') ??
      candidates[0]
    )
  }, [selectedProductionReviewTopicId, topicQueue])

  const loadReadiness = async (live = false) => {
    setReadinessLoading(true)
    setReadinessError(null)
    try {
      setReadiness(await fetchProductionReadiness(live))
    } catch (error) {
      setReadinessError(
        error instanceof Error ? error.message : 'Nie udało się sprawdzić gotowości produkcji.',
      )
    } finally {
      setReadinessLoading(false)
    }
  }

  const loadMoneySummary = async () => {
    if (!account) {
      return
    }
    setMoneySummaryLoading(true)
    setMoneySummaryError(null)
    try {
      setMoneySummary(await fetchMoneyMachineSummary({
        accountId: account.id,
        includeOauth: true,
        maxUploads: 5,
        perAccountLimit: 2,
      }))
    } catch (error) {
      setMoneySummaryError(
        error instanceof Error ? error.message : 'Nie udało się pobrać statusu maszynki.',
      )
    } finally {
      setMoneySummaryLoading(false)
    }
  }

  const loadProjectReadiness = async () => {
    setProjectReadinessError(null)
    try {
      setProjectReadiness(await fetchProjectReadiness())
    } catch (error) {
      setProjectReadinessError(
        error instanceof Error ? error.message : 'Nie udalo sie pobrac gotowosci projektow.',
      )
    }
  }

  const loadProcurementControl = async () => {
    setProcurementLoading(true)
    setProcurementError(null)
    try {
      const [spending, requests, notifications] = await Promise.all([
        fetchSmartSpendingStatus(),
        fetchProcurementRequests({ limit: 8 }),
        fetchExpenseNotifications({ limit: 8 }),
      ])
      setSmartSpending(spending)
      setProcurementRequests(requests)
      setExpenseNotifications(notifications)
    } catch (error) {
      setProcurementError(
        error instanceof Error ? error.message : 'Nie udało się pobrać kontroli wydatków.',
      )
    } finally {
      setProcurementLoading(false)
    }
  }

  const loadPublicPromotionPlan = async () => {
    if (!account) {
      return
    }
    setPublicPromotionLoading(true)
    setPublicPromotionError(null)
    try {
      setPublicPromotionPlan(await runMoneyMachinePublication({
        accountId: account.id,
        maxPublications: 5,
        perAccountLimit: 2,
        execute: false,
      }))
    } catch (error) {
      setPublicPromotionError(
        error instanceof Error ? error.message : 'Nie udało się pobrać planu publicznej promocji.',
      )
    } finally {
      setPublicPromotionLoading(false)
    }
  }

  const ensureShortsModels = async () => {
    setModelEnsureLoading(true)
    setModelEnsureStatus(null)
    try {
      const result = await ensureModelPipeline('shorts_full')
      setModelDownload(await fetchModelDownloadProgress('shorts_full'))
      setModelEnsureStatus(
        result.status === 'download_started'
          ? 'Pobieranie modeli shortów uruchomione w tle. To może potrwać długo, bo LTX/Wan/T5 ważą kilkanaście GB.'
          : `Status modeli: ${result.status}`,
      )
    } catch (error) {
      setModelEnsureStatus(
        error instanceof Error ? error.message : 'Nie udało się uruchomić pobierania modeli.',
      )
    } finally {
      setModelEnsureLoading(false)
    }
  }

  const refreshModelDownload = async () => {
    try {
      setModelDownload(await fetchModelDownloadProgress('shorts_full'))
    } catch {
      // Readiness remains the source of truth if the lightweight progress endpoint is unavailable.
    }
  }

  const loadTopics = async () => {
    if (!account) {
      return
    }
    setTopicError(null)
    try {
      setTopicQueue(await fetchTopicQueue({ accountId: account.id, status: 'all' }))
    } catch (error) {
      setTopicError(error instanceof Error ? error.message : 'Nie udało się pobrać kolejki tematów.')
    }
  }

  const loadProductionReview = async (topic: TopicQueueItem | null = productionReviewTopic) => {
    if (!account || !topic) {
      setProductionReviewStatus(null)
      setProductionReviewError(null)
      return null
    }
    setProductionReviewLoading(true)
    setProductionReviewError(null)
    try {
      const status = await fetchProductionDelegationReviewStatus({
        accountId: account.id,
        topicId: topic.id,
      })
      setProductionReviewStatus(status)
      return status
    } catch (error) {
      setProductionReviewError(
        error instanceof Error ? error.message : 'Nie udało się pobrać production review.',
      )
      return null
    } finally {
      setProductionReviewLoading(false)
    }
  }

  useEffect(() => {
    void loadTopics()
  }, [accountId])

  useEffect(() => {
    void loadMoneySummary()
    void loadPublicPromotionPlan()
    void loadProjectReadiness()
    void loadProcurementControl()
  }, [accountId])

  useEffect(() => {
    void loadReadiness(true)
  }, [])

  useEffect(() => {
    void loadProductionReview(productionReviewTopic)
  }, [accountId, productionReviewTopic?.id])

  useEffect(() => {
    void refreshModelDownload()
    const timer = window.setInterval(() => {
      void refreshModelDownload()
    }, 5000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail as { accountId?: string } : {}
      if (detail.accountId === accountId) {
        void loadTopics()
      }
    }
    window.addEventListener('atnos:topics-generated', handler)
    return () => window.removeEventListener('atnos:topics-generated', handler)
  }, [accountId])

  const visibleTopics = useMemo(() => {
    return topicQueue
      .filter((topic) => (topicFilter === 'all' ? true : topic.status === topicFilter))
      .filter((topic) => showLowScore || topic.total_score >= 40)
      .sort((a, b) => {
        if (topicSort === 'freshness') {
          return b.dimensions.freshness - a.dimensions.freshness
        }
        if (topicSort === 'shorts') {
          return Number(b.flags.shorts_potential) - Number(a.flags.shorts_potential) || b.total_score - a.total_score
        }
        return b.total_score - a.total_score
      })
  }, [topicFilter, topicQueue, showLowScore, topicSort])

  const ingestProductionJob = (job: ProductionJobPayload) => {
    setProductionJobs((items) => ({ ...items, [job.id]: job }))
    const title = job.topic_title || activeJob?.title || 'Backend pipeline job'
    const steps = job.steps.length > 0
      ? job.steps
      : [{ step: job.current_step ?? 'hook', status: job.status, progress: 0, message: null }]

    for (const step of steps) {
      ingestPipelineStep({
        accountId: job.account_id,
        jobId: job.id,
        title,
        step: step.step,
        status: step.status,
        progress: Math.round(Number(step.progress ?? 0) * 100),
        error: step.message ?? job.error ?? undefined,
        videoId: job.youtube_video_id ?? undefined,
        videoUrl: job.youtube_url ?? undefined,
      })
    }

    if (job.status === 'qa_passed') {
      ingestPipelineStep({
        accountId: job.account_id,
        jobId: job.id,
        title,
        step: 'qa',
        status: 'done',
        progress: 100,
        videoId: job.youtube_video_id ?? undefined,
        videoUrl: job.youtube_url ?? undefined,
      })
      appendLog({
        accountId: job.account_id,
        jobId: job.id,
        step: 'qa',
        level: 'info',
        message: 'QA zaliczone. Pakiet prywatnego uploadu gotowy do review.',
      })
    }
    if (job.status === 'qa_failed' || job.status === 'error') {
      appendLog({
        accountId: job.account_id,
        jobId: job.id,
        step: 'qa',
        level: 'error',
        message: job.error || 'Produkcja zakończyła się błędem.',
      })
    }
  }

  const loadDistributionPlan = async (jobId: string) => {
    setDistributionPlanError(null)
    try {
      const plan = await fetchProductionDistributionPlan(jobId)
      setDistributionPlans((items) => ({ ...items, [jobId]: plan }))
    } catch (error) {
      setDistributionPlanError(
        error instanceof Error ? error.message : 'Nie udało się pobrać planu dystrybucji.',
      )
    }
  }

  const createDistributionPackageForJob = async (job: ProductionJobPayload) => {
    setDistributionPackageLoadingJobId(job.id)
    setDistributionPackageStatus(null)
    try {
      const result = await createProductionDistributionPackage({ jobId: job.id })
      setDistributionPlans((items) => ({ ...items, [job.id]: result.plan }))
      const message = `Paczki dystrybucji: ${result.created_count} utworzone, ${result.blocked_count} zablokowane.`
      setDistributionPackageStatus(message)
      appendLog({
        accountId: job.account_id,
        jobId: job.id,
        step: 'upload',
        level: result.created_count > 0 ? 'info' : 'warn',
        message,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się utworzyć paczek dystrybucji.'
      setDistributionPackageStatus(message)
      appendLog({
        accountId: job.account_id,
        jobId: job.id,
        step: 'upload',
        level: 'error',
        message,
      })
    } finally {
      setDistributionPackageLoadingJobId(null)
    }
  }

  useEffect(() => {
    if (!activeJob?.id.startsWith('prod-')) {
      return
    }

    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const job = await fetchProductionJob(activeJob.id)
        if (cancelled) return
        ingestProductionJob(job)
        void loadDistributionPlan(job.id)
        if (!isProductionTerminalStatus(job.status)) {
          timer = window.setTimeout(poll, 5000)
        }
      } catch (error) {
        if (cancelled || !account) return
        appendLog({
          accountId: account.id,
          jobId: activeJob.id,
          step: 'system',
          level: 'warn',
          message: error instanceof Error ? error.message : 'Nie udało się odświeżyć statusu produkcji.',
        })
        timer = window.setTimeout(poll, 10000)
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timer) {
        window.clearTimeout(timer)
      }
    }
  }, [activeJob?.id, account?.id])

  const handleGenerateTopics = async () => {
    if (!account) {
      return
    }
    setTopicLoading(true)
    setTopicError(null)
    try {
      const generated = await generateTopics({ accountId: account.id, count: 20 })
      setTopicQueue(generated)
      appendLog({
        accountId: account.id,
        step: 'topics',
        level: 'info',
        message: `Wygenerowano i oceniono ${generated.length} tematów. Top: ${generated[0]?.title ?? '-'}`,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Generowanie tematów nie powiodło się.'
      setTopicError(message)
      appendLog({ accountId: account.id, step: 'topics', level: 'error', message })
    } finally {
      setTopicLoading(false)
    }
  }

  const changeTopicStatus = async (topicId: string, status: TopicQueueStatus) => {
    if (!account) {
      return
    }
    const updated = await updateTopicQueueStatus(topicId, status)
    setTopicQueue((items) => items.map((item) => (item.id === topicId ? updated : item)))
    appendLog({
      accountId: account.id,
      step: 'topics',
      level: 'info',
      message: `Temat "${updated.title}" -> ${statusLabel(status)}`,
    })
  }

  const removeTopic = async (topicId: string) => {
    await deleteTopicQueueItem(topicId)
    setTopicQueue((items) => items.filter((item) => item.id !== topicId))
  }

  const produceTopic = async (topic: TopicQueueItem) => {
    if (!account) {
      return
    }
    if (topic.status !== 'approved') {
      return
    }
    const reviewReady = productionReviewStatus?.topic_id === topic.id && productionReviewStatus.ready_for_production_start
    if (!reviewReady) {
      setSelectedProductionReviewTopicId(topic.id)
      const latestReview = await loadProductionReview(topic)
      const blockers = latestReview?.production_gate_blockers ?? ['production_review_status_not_loaded']
      const firstAction = latestReview?.review_summary?.first_action
      const message = `Production review blokuje render: ${
        blockers.length ? blockers.map(productionReviewBlockerLabel).join('; ') : 'odśwież status review dla tematu'
      }. ${productionReviewActionLabel(firstAction)}`
      if (!latestReview?.ready_for_production_start) {
        appendLog({ accountId: account.id, step: 'system', level: 'warn', message })
        setTopicError(message)
        return
      }
    }
    try {
      const result = await startProduction({ accountId: account.id, topicId: topic.id })
      setTopicError(null)
      setProductionReviewLastRun(null)
      setTopicQueue((items) =>
        items.map((item) => (item.id === topic.id ? { ...item, status: 'in_production' } : item)),
      )
      ingestPipelineStep({
        accountId: account.id,
        jobId: result.job_id,
        title: topic.title,
        step: 'hook',
        status: 'queued',
        progress: 0,
      })
      appendLog({
        accountId: account.id,
        jobId: result.job_id,
        step: 'system',
        level: 'info',
        message: `Backendowy pipeline uruchomiony: ${topic.title}`,
      })
      const delegationReview = result.delegation_review
      if (delegationReview) {
        const rootTask = 'root_task' in delegationReview ? delegationReview.root_task : null
        const childCount = rootTask?.children?.length ?? 0
        if ('latest' in delegationReview) {
          setProductionReviewStatus(delegationReview.latest)
        } else {
          setProductionReviewStatus(delegationReview)
        }
        appendLog({
          accountId: account.id,
          jobId: result.job_id,
          step: 'system',
          level: childCount > 0 ? 'info' : 'warn',
          message: `Plan review AI: ${String(delegationReview.status)}; podzadania: ${childCount}.`,
        })
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się uruchomić produkcji.'
      appendLog({ accountId: account.id, step: 'system', level: 'error', message })
      setTopicError(message)
    }
  }

  const ensureProductionReviewPlan = async () => {
    if (!account || !productionReviewTopic) {
      return
    }
    setProductionReviewActionLoading(true)
    setProductionReviewError(null)
    setProductionReviewLastRun(null)
    try {
      const result = await createProductionDelegationReviewPlan({
        accountId: account.id,
        topicId: productionReviewTopic.id,
        allowBrowserAi: false,
        maxSteps: 6,
      })
      setProductionReviewStatus(result.latest)
      appendLog({
        accountId: account.id,
        step: 'system',
        level: result.root_task.children.length > 0 ? 'info' : 'warn',
        message: `Production review plan: ${result.status}; podzadania: ${result.root_task.children.length}.`,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się utworzyć production review plan.'
      setProductionReviewError(message)
      appendLog({ accountId: account.id, step: 'system', level: 'error', message })
    } finally {
      setProductionReviewActionLoading(false)
    }
  }

  const runNextProductionReviewTask = async () => {
    if (!account || !productionReviewTopic) {
      return
    }
    setProductionReviewActionLoading(true)
    setProductionReviewError(null)
    try {
      const result = await runProductionDelegationReviewNext({
        accountId: account.id,
        topicId: productionReviewTopic.id,
      })
      setProductionReviewStatus(result.latest)
      const summaryMessage = result.review_summary
        ? `${result.review_summary.headline} ${productionReviewActionLabel(result.review_summary.first_action)}`
        : result.reason
      setProductionReviewLastRun(summaryMessage)
      appendLog({
        accountId: account.id,
        step: 'system',
        level: result.status === 'completed' ? 'info' : result.status === 'blocked' || result.status === 'failed' ? 'warn' : 'info',
        message: `Production review task: ${result.status}; ${summaryMessage}`,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się uruchomić następnego review taska.'
      setProductionReviewError(message)
      appendLog({ accountId: account.id, step: 'system', level: 'error', message })
    } finally {
      setProductionReviewActionLoading(false)
    }
  }

  const runAllProductionReviewTasks = async () => {
    if (!account || !productionReviewTopic) {
      return
    }
    setProductionReviewActionLoading(true)
    setProductionReviewError(null)
    try {
      const result = await runProductionDelegationReviewAll({
        accountId: account.id,
        topicId: productionReviewTopic.id,
        limit: 6,
      })
      setProductionReviewStatus(result.latest)
      const summaryMessage = result.review_summary
        ? `${result.review_summary.headline} ${productionReviewActionLabel(result.review_summary.first_action)}`
        : result.reason
      const message = `Production review run-all: ${result.status}; wykonano ${result.ran_count}/${result.limit}; ${summaryMessage}`
      setProductionReviewLastRun(message)
      appendLog({
        accountId: account.id,
        step: 'system',
        level: result.status === 'completed' ? 'info' : 'warn',
        message,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się uruchomić wszystkich review tasków.'
      setProductionReviewError(message)
      appendLog({ accountId: account.id, step: 'system', level: 'error', message })
    } finally {
      setProductionReviewActionLoading(false)
    }
  }

  const uploadPrivate = async (job: ProductionJobPayload) => {
    if (!account) {
      return
    }
    setPrivateUploadJobId(job.id)
    try {
      const result = await uploadProductionJobPrivate(job.id)
      appendLog({
        accountId: account.id,
        jobId: job.id,
        step: 'upload',
        level: result.status === 'uploaded_private' ? 'info' : 'warn',
        message:
          result.status === 'uploaded_private'
            ? `Upload prywatny gotowy: ${result.youtube_url ?? result.youtube_video_id ?? job.id}`
            : result.message ?? 'YouTube wymaga konfiguracji połączenia OAuth.',
      })
      ingestPipelineStep({
        accountId: account.id,
        jobId: job.id,
        title: job.topic_title ?? activeJob?.title ?? 'Backend pipeline job',
        step: 'upload',
        status: result.status === 'uploaded_private' ? 'done' : 'error',
        progress: result.status === 'uploaded_private' ? 100 : 92,
        error: result.status === 'uploaded_private' ? undefined : result.message,
        videoId: result.youtube_video_id,
        videoUrl: result.youtube_url,
      })
      const refreshed = await fetchProductionJob(job.id)
      ingestProductionJob(refreshed)
      await Promise.all([loadMoneySummary(), loadPublicPromotionPlan()])
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się wysłać shorta jako prywatny.'
      appendLog({ accountId: account.id, jobId: job.id, step: 'upload', level: 'error', message })
    } finally {
      setPrivateUploadJobId(null)
    }
  }

  const uploadSummaryJobPrivate = async (job: MoneyMachineSummaryJob) => {
    setPrivateUploadJobId(job.job_id)
    try {
      const result = await uploadProductionJobPrivate(job.job_id)
      appendLog({
        accountId: job.account_id,
        jobId: job.job_id,
        step: 'upload',
        level: result.status === 'uploaded_private' ? 'info' : 'warn',
        message:
          result.status === 'uploaded_private'
            ? `Upload prywatny gotowy: ${result.youtube_url ?? result.youtube_video_id ?? job.job_id}`
            : result.message ?? 'YouTube wymaga konfiguracji OAuth.',
      })
      ingestPipelineStep({
        accountId: job.account_id,
        jobId: job.job_id,
        title: job.topic_title,
        step: 'upload',
        status: result.status === 'uploaded_private' ? 'done' : 'error',
        progress: result.status === 'uploaded_private' ? 100 : 92,
        error: result.status === 'uploaded_private' ? undefined : result.message,
        videoId: result.youtube_video_id,
        videoUrl: result.youtube_url,
      })
      try {
        ingestProductionJob(await fetchProductionJob(job.job_id))
      } catch {
        // Summary remains the operator source of truth if the detailed job endpoint is unavailable.
      }
      await loadMoneySummary()
      await loadPublicPromotionPlan()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się wysłać shorta jako prywatny.'
      appendLog({ accountId: job.account_id, jobId: job.job_id, step: 'upload', level: 'error', message })
    } finally {
      setPrivateUploadJobId(null)
    }
  }

  const regenerateSummaryJob = async (job: MoneyMachineSummaryJob) => {
    setRecoveryJobId(job.job_id)
    try {
      const result = await regenerateProductionJob(job.job_id, 'dashboard_quality_recovery')
      appendLog({
        accountId: job.account_id,
        jobId: result.job_id,
        step: 'system',
        level: 'info',
        message: `Regeneracja uruchomiona dla "${job.topic_title}". Nowy job: ${result.job_id}`,
      })
      ingestPipelineStep({
        accountId: job.account_id,
        jobId: result.job_id,
        title: job.topic_title,
        step: 'hook',
        status: 'queued',
        progress: 0,
      })
      await loadMoneySummary()
      await loadPublicPromotionPlan()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się uruchomić regeneracji.'
      appendLog({ accountId: job.account_id, jobId: job.job_id, step: 'system', level: 'error', message })
    } finally {
      setRecoveryJobId(null)
    }
  }

  const runSafeMoneyMachineStep = async () => {
    if (!account || !moneySummary) {
      return
    }
    const hasPrivateUpload = moneySummary.private_upload_ready.length > 0
    const hasRecovery = moneySummary.recovery_ready.length > 0
    if (!hasPrivateUpload && !hasRecovery && moneySummary.next_operator_action === 'review_or_approve_best_topic') {
      const candidate = [...topicQueue]
        .filter((topic) => topic.status === 'pending')
        .sort((a, b) => b.total_score - a.total_score)[0]
      if (!candidate) {
        appendLog({
          accountId: account.id,
          step: 'topics',
          level: 'warn',
          message: 'Nie ma pending topic do zatwierdzenia. Odśwież kolejkę albo wygeneruj tematy.',
        })
        return
      }
      setMoneyActionLoading(true)
      try {
        await changeTopicStatus(candidate.id, 'approved')
        await loadTopics()
        await loadMoneySummary()
        await loadPublicPromotionPlan()
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Nie udało się zatwierdzić najlepszego tematu.'
        appendLog({ accountId: account.id, step: 'topics', level: 'error', message })
      } finally {
        setMoneyActionLoading(false)
      }
      return
    }
    if (!hasPrivateUpload && !hasRecovery) {
      appendLog({
        accountId: account.id,
        step: 'system',
        level: 'warn',
        message: 'Maszynka nie ma teraz bezpiecznego kroku do wykonania.',
      })
      return
    }

    setMoneyActionLoading(true)
    try {
      const result = await runMoneyMachineNext({
        accountId: account.id,
        maxUploads: 1,
        perAccountLimit: 1,
        execute: true,
        approvedPrivateUpload: hasPrivateUpload,
        executeRecovery: !hasPrivateUpload && hasRecovery,
      })
      const executedCount = Number(result.executed_count ?? result.recovery_count ?? 0)
      appendLog({
        accountId: account.id,
        step: 'system',
        level: result.status === 'completed' || result.status === 'recovery_queued' ? 'info' : 'warn',
        message:
          hasPrivateUpload
            ? `Money Machine: prywatny upload wykonany dla ${executedCount} jobów. Status: ${result.status}.`
            : `Money Machine: regeneracja uruchomiona dla ${executedCount} jobów. Status: ${result.status}.`,
      })
      await loadMoneySummary()
      await loadPublicPromotionPlan()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się wykonać następnego kroku maszynki.'
      appendLog({ accountId: account.id, step: 'system', level: 'error', message })
      setMoneySummaryError(message)
    } finally {
      setMoneyActionLoading(false)
    }
  }

  const runContentLoopOnce = async () => {
    if (!account) {
      return
    }
    setContentLoopLoading(true)
    try {
      const result = await runProductionContentLoopOnce({ accountId: account.id, maxJobs: 1 })
      appendLog({
        accountId: account.id,
        step: 'system',
        level: result.status === 'completed' || result.status === 'idle' ? 'info' : 'warn',
        message:
          `Pętla treści: wygenerowane ${result.generated_count}, zaakceptowane ${result.approved_count}, ` +
          `odrzucone ${result.rejected_count}, produkcja ${result.started_count}. Status: ${result.status}.`,
      })
      await Promise.all([loadTopics(), loadMoneySummary()])
      await loadPublicPromotionPlan()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się uruchomić pętli treści.'
      appendLog({ accountId: account.id, step: 'system', level: 'error', message })
      setMoneySummaryError(message)
    } finally {
      setContentLoopLoading(false)
    }
  }

  const runProjectIntelligence = async (project: Account, backendReadiness?: ProjectReadinessProject) => {
    if (project.type !== 'youtube') {
      return
    }
    const loadingId = `${project.id}:intelligence`
    setProjectActionLoadingId(loadingId)
    try {
      const niches = projectNiches(project, backendReadiness)
      const result = await runIntelligenceCollectors({
        accountId: project.id,
        niches,
        competitorUrls: splitConfigList(project.platformConfig.competitorChannels),
        referenceUrls: splitConfigList(project.platformConfig.referenceChannels),
        region: String(project.platformConfig.region ?? 'PL'),
      })
      appendLog({
        accountId: project.id,
        step: 'system',
        level: 'info',
        message: `Research uruchomiony dla ${project.name}. Status: ${String(result.status ?? 'started')}.`,
      })
      await Promise.all([loadProjectReadiness(), loadMoneySummary()])
    } catch (error) {
      appendLog({
        accountId: project.id,
        step: 'system',
        level: 'error',
        message: error instanceof Error ? error.message : 'Nie udalo sie uruchomic researchu projektu.',
      })
    } finally {
      setProjectActionLoadingId(null)
    }
  }

  const generateProjectTopics = async (project: Account) => {
    if (project.type !== 'youtube') {
      return
    }
    const loadingId = `${project.id}:topics`
    setProjectActionLoadingId(loadingId)
    try {
      const generated = await generateTopics({ accountId: project.id, count: 20 })
      if (project.id === accountId) {
        setTopicQueue(generated)
      }
      appendLog({
        accountId: project.id,
        step: 'topics',
        level: 'info',
        message: `Wygenerowano ${generated.length} tematow dla ${project.name}. Top: ${generated[0]?.title ?? '-'}`,
      })
      await Promise.all([loadProjectReadiness(), loadMoneySummary()])
    } catch (error) {
      appendLog({
        accountId: project.id,
        step: 'topics',
        level: 'error',
        message: error instanceof Error ? error.message : 'Nie udalo sie wygenerowac tematow projektu.',
      })
    } finally {
      setProjectActionLoadingId(null)
    }
  }

  const runProjectProduction = async (project: Account) => {
    if (project.type !== 'youtube') {
      return
    }
    const loadingId = `${project.id}:production`
    setProjectActionLoadingId(loadingId)
    try {
      const result = await runProductionContentLoopOnce({ accountId: project.id, maxJobs: 1 })
      appendLog({
        accountId: project.id,
        step: 'system',
        level: result.status === 'completed' || result.status === 'idle' ? 'info' : 'warn',
        message:
          `Projekt ${project.name}: wygenerowane ${result.generated_count}, zaakceptowane ${result.approved_count}, ` +
          `produkcja ${result.started_count}. Status: ${result.status}.`,
      })
      if (project.id === accountId) {
        await loadTopics()
      }
      await Promise.all([loadProjectReadiness(), loadMoneySummary(), loadPublicPromotionPlan()])
    } catch (error) {
      appendLog({
        accountId: project.id,
        step: 'system',
        level: 'error',
        message: error instanceof Error ? error.message : 'Nie udalo sie uruchomic produkcji projektu.',
      })
    } finally {
      setProjectActionLoadingId(null)
    }
  }

  const runPublicPromotion = async () => {
    if (!account) {
      return
    }
    if (!moneySummary?.public_upload_enabled) {
      const message = 'Publiczna publikacja jest wyłączona w backendzie: ALLOW_PUBLIC_UPLOAD=false.'
      setPublicPromotionError(message)
      appendLog({ accountId: account.id, step: 'upload', level: 'warn', message })
      return
    }
    const publishAt = publicPublishAt.trim()
    if (!publishAt) {
      const message = 'Podaj datę publikacji w ISO UTC, np. 2026-06-08T14:00:00Z.'
      setPublicPromotionError(message)
      appendLog({ accountId: account.id, step: 'upload', level: 'warn', message })
      return
    }
    setPublicPromotionExecuting(true)
    setPublicPromotionError(null)
    try {
      const result = await runMoneyMachinePublication({
        accountId: account.id,
        maxPublications: 1,
        perAccountLimit: 1,
        execute: true,
        approvedPublicPromotion: true,
        publishAt,
      })
      appendLog({
        accountId: account.id,
        step: 'upload',
        level: result.status === 'completed' ? 'info' : 'warn',
        message: `Publiczna promocja: wykonane ${result.executed_count ?? 0}, zablokowane ${result.blocked_count ?? 0}. Status: ${result.status}.`,
      })
      await Promise.all([loadMoneySummary(), loadPublicPromotionPlan()])
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się zaplanować publicznej publikacji.'
      setPublicPromotionError(message)
      appendLog({ accountId: account.id, step: 'upload', level: 'error', message })
    } finally {
      setPublicPromotionExecuting(false)
    }
  }

  if (!account) {
    return (
      <div className="p-6">
        <Panel title="Brak kont">
          <div className="p-6 text-text-secondary">Dodaj konto w lewym sidebarze, żeby uruchomić dashboard.</div>
        </Panel>
      </div>
    )
  }

  return (
    <div className="space-y-4 p-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ['Kolejka', String(accountQueue.length)],
          ['Filmy dziś', String(account.stats.videosToday)],
          ['Przychód 30D', `${account.stats.revenueMonth} zł`],
          ['Automatyzacja', AUTOMATION_LABELS[account.automationMode] ?? account.automationMode],
        ].map(([label, value]) => (
          <div key={label} className="border border-border-default bg-panel p-3">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">{label}</div>
            <div className="mt-1 font-display text-lg font-semibold uppercase tracking-[0.08em] text-text-primary">
              {value}
            </div>
          </div>
        ))}
      </div>

      <ProviderDoctorPanel accountId={account.id} />

      <Panel title="Kasa operacyjna i wydatki">
        <div className="grid gap-4 p-4 xl:grid-cols-[1.1fr_1fr_1fr]">
          <div className="border border-border-default bg-void p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-mono text-[10px] uppercase text-text-tertiary">Dostępne saldo</div>
                <div className="mt-1 font-display text-2xl font-semibold uppercase tracking-[0.08em] text-text-primary">
                  {formatPln(smartSpending?.available_balance_pln)}
                </div>
              </div>
              <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${smartSpendingStateClass(smartSpending?.money_awareness.state)}`}>
                {smartSpendingStateLabel(smartSpending?.money_awareness.state)}
              </span>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2 font-mono text-[10px] uppercase">
              <div className="border border-border-default p-2">
                <div className="text-text-tertiary">Seed</div>
                <div className="mt-1 text-text-primary">{formatPln(smartSpending?.starting_balance_pln)}</div>
              </div>
              <div className="border border-border-default p-2">
                <div className="text-text-tertiary">Zarobione</div>
                <div className="mt-1 text-success">{formatPln(smartSpending?.earned_total_pln)}</div>
              </div>
              <div className="border border-border-default p-2">
                <div className="text-text-tertiary">Wydane</div>
                <div className="mt-1 text-warning">{formatPln(smartSpending?.spent_total_pln)}</div>
              </div>
              <div className="border border-border-default p-2">
                <div className="text-text-tertiary">Rezerwacje</div>
                <div className="mt-1 text-warning">{formatPln(smartSpending?.reserved_expenses_pln)}</div>
              </div>
            </div>
            <div className="mt-4 border border-border-default bg-panel p-3 text-xs text-text-secondary">
              {smartSpending?.money_awareness.instruction ?? 'Atnos pobiera status kasy operacyjnej.'}
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <div className="font-mono text-[10px] uppercase text-error">
                Karta: {smartSpending?.card_access.can_use_card ? 'podłączona' : 'brak dostępu'}
              </div>
              <button
                type="button"
                onClick={() => void loadProcurementControl()}
                disabled={procurementLoading}
                className="border border-border-default px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] text-text-secondary disabled:cursor-wait disabled:opacity-60"
              >
                {procurementLoading ? 'Odświeżam...' : 'Odśwież'}
              </button>
            </div>
          </div>

          <div className="border border-border-default bg-void p-4">
            <div className="flex items-center justify-between gap-2">
              <div className="font-display text-[12px] uppercase tracking-[0.14em] text-text-primary">
                Ostatni ledger
              </div>
              <div className="font-mono text-[9px] uppercase text-text-tertiary">bez danych karty</div>
            </div>
            <div className="mt-3 space-y-2">
              {(smartSpending?.recent_entries ?? []).slice(0, 5).map((entry) => (
                <div key={entry.id} className="border border-border-default bg-panel p-2">
                  <div className="flex items-center justify-between gap-2 font-mono text-[10px] uppercase">
                    <span className="truncate text-text-secondary">{ledgerEntryLabel(entry.entry_type)}</span>
                    <span className={entry.entry_type === 'revenue' ? 'text-success' : entry.entry_type === 'seed_capital' ? 'text-accent-primary' : 'text-warning'}>
                      {formatPln(entry.amount_pln)}
                    </span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-xs text-text-tertiary">{entry.reason}</div>
                </div>
              ))}
              {!smartSpending?.recent_entries?.length ? (
                <div className="border border-dashed border-border-default p-4 text-sm text-text-secondary">
                  Brak wpisów poza inicjalizacją salda.
                </div>
              ) : null}
            </div>
          </div>

          <div className="border border-border-default bg-void p-4">
            <div className="font-display text-[12px] uppercase tracking-[0.14em] text-text-primary">
              Zakupy i powiadomienia
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 font-mono text-[10px] uppercase">
              <div className="border border-border-default p-2">
                <div className="text-text-tertiary">Wnioski</div>
                <div className="mt-1 text-text-primary">{procurementRequests.length}</div>
              </div>
              <div className="border border-border-default p-2">
                <div className="text-text-tertiary">Outbox</div>
                <div className="mt-1 text-text-primary">{expenseNotifications.length}</div>
              </div>
            </div>
            <div className="mt-3 space-y-2">
              {procurementRequests.slice(0, 3).map((requestItem) => (
                <div key={requestItem.id} className="border border-border-default bg-panel p-2">
                  <div className="flex items-center justify-between gap-2 font-mono text-[10px] uppercase">
                    <span className="truncate text-text-secondary">{requestItem.provider_id}</span>
                    <span className={readinessStatusClass(requestItem.status)}>{readinessStatusLabel(requestItem.status)}</span>
                  </div>
                  <div className="mt-1 line-clamp-2 text-xs text-text-tertiary">{requestItem.reason}</div>
                </div>
              ))}
              {expenseNotifications.slice(0, 2).map((item) => (
                <div key={item.id} className="border border-warning/30 bg-warning/5 p-2">
                  <div className="font-mono text-[10px] uppercase text-warning">
                    {item.delivery_status}: {formatPln(item.amount_pln)}
                  </div>
                  <div className="mt-1 line-clamp-2 text-xs text-text-tertiary">{item.reason}</div>
                </div>
              ))}
              {!procurementRequests.length && !expenseNotifications.length ? (
                <div className="border border-dashed border-border-default p-4 text-sm text-text-secondary">
                  Brak aktywnych wniosków zakupowych. Płatności tylko przez ręczny checkout właściciela.
                </div>
              ) : null}
            </div>
            {procurementError ? (
              <div className="mt-3 border border-error/40 bg-error/10 p-2 font-mono text-[10px] uppercase text-error">
                {procurementError}
              </div>
            ) : null}
          </div>
        </div>
      </Panel>

      <Panel title="Do sprawdzenia teraz">
        <div className="grid gap-3 p-4 xl:grid-cols-[1.4fr_0.9fr]">
          <div className="border border-accent-primary/40 bg-accent-primary/5 p-4">
            {!moneySummary ? (
              <div>
                <div className="font-display text-lg font-semibold uppercase tracking-[0.12em] text-text-primary">
                  Ładuję status Atnosa
                </div>
                <div className="mt-2 text-sm text-text-secondary">
                  Backend jeszcze zwraca dane. Jeśli ten stan zostaje dłużej niż kilka sekund, odśwież status albo sprawdź API.
                </div>
              </div>
            ) : topActiveMoneyJob ? (
              <div>
                <div className="font-mono text-[10px] uppercase text-success">Render w toku</div>
                <div className="mt-1 font-display text-lg font-semibold uppercase tracking-[0.08em] text-text-primary">
                  {topActiveMoneyJob.topic_title || topActiveMoneyJob.job_id}
                </div>
                <div className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">
                  {topActiveMoneyJob.account_id} / krok: {topActiveMoneyJob.current_step || topActiveMoneyJob.status} / heartbeat {formatRuntimeSeconds(topActiveMoneyJob.heartbeat_age_seconds)}
                </div>
                <Link
                  to={`/studio/${topActiveMoneyJob.job_id}`}
                  className="mt-4 inline-flex border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
                >
                  Otwórz studio
                </Link>
              </div>
            ) : topPrivateReviewJob ? (
              <div>
                <div className="font-mono text-[10px] uppercase text-success">Gotowe do prywatnego review</div>
                <div className="mt-1 font-display text-lg font-semibold uppercase tracking-[0.08em] text-text-primary">
                  {topPrivateReviewJob.topic_title}
                </div>
                <div className="mt-2 grid gap-2 font-mono text-[10px] uppercase text-text-tertiary sm:grid-cols-3">
                  <span>Kanał: {topPrivateReviewJob.account_id}</span>
                  <span>Głos: {topPrivateReviewJob.quality?.voice ?? '-'}</span>
                  <span>Wideo: {topPrivateReviewJob.quality?.video_backend ?? '-'}</span>
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void uploadSummaryJobPrivate(topPrivateReviewJob)}
                    disabled={privateUploadJobId === topPrivateReviewJob.job_id}
                    className="border border-success/50 bg-success/10 px-4 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-success disabled:cursor-wait disabled:opacity-60"
                  >
                    {privateUploadJobId === topPrivateReviewJob.job_id ? 'Wysyłam...' : 'Wrzuć prywatnie'}
                  </button>
                  <Link
                    to={`/studio/${topPrivateReviewJob.job_id}`}
                    className="border border-accent-primary/50 px-4 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
                  >
                    Sprawdź w studio
                  </Link>
                </div>
              </div>
            ) : topRecoveryReviewJob ? (
              <div>
                <div className="font-mono text-[10px] uppercase text-warning">Wymaga regeneracji</div>
                <div className="mt-1 font-display text-lg font-semibold uppercase tracking-[0.08em] text-text-primary">
                  {topRecoveryReviewJob.topic_title}
                </div>
                <div className="mt-2 space-y-1">
                  {topRecoveryReviewJob.public_quality_blockers.slice(0, 3).map((blocker) => (
                    <div key={blocker} className="font-mono text-[10px] uppercase text-warning">
                      {operatorActionLabel(blocker)}
                    </div>
                  ))}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void regenerateSummaryJob(topRecoveryReviewJob)}
                    disabled={recoveryJobId === topRecoveryReviewJob.job_id}
                    className="border border-warning/50 bg-warning/10 px-4 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-warning disabled:cursor-wait disabled:opacity-60"
                  >
                    {recoveryJobId === topRecoveryReviewJob.job_id ? 'Regeneruje...' : 'Regeneruj'}
                  </button>
                  <Link
                    to={`/studio/${topRecoveryReviewJob.job_id}`}
                    className="border border-border-default px-4 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary"
                  >
                    Zobacz raport
                  </Link>
                </div>
              </div>
            ) : (
              <div>
                <div className="font-mono text-[10px] uppercase text-success">Brak pilnych akcji</div>
                <div className="mt-1 font-display text-lg font-semibold uppercase tracking-[0.08em] text-text-primary">
                  Atnos nie ma teraz aktywnego renderu ani paczki do decyzji
                </div>
                <div className="mt-2 text-sm text-text-secondary">
                  Uzyj petli tresci albo zatwierdz temat, jesli chcesz uruchomic kolejny render.
                </div>
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2">
            {[
              ['Status', moneySummary ? operatorActionLabel(moneySummary.status) : 'Ladowanie'],
              ['Prywatne gotowe', String(moneySummary?.totals.ready_private_upload_jobs ?? '-')],
              ['Publiczne QA OK', String(moneySummary?.totals.public_ready_private_upload_jobs ?? '-')],
              ['Regeneracja', String(moneySummary?.totals.blocked_private_upload_jobs ?? '-')],
            ].map(([label, value]) => (
              <div key={label} className="border border-border-default bg-panel p-3">
                <div className="font-mono text-[9px] uppercase text-text-tertiary">{label}</div>
                <div className="mt-1 truncate font-display text-[13px] font-semibold uppercase tracking-[0.08em] text-text-primary">
                  {value}
                </div>
              </div>
            ))}
          </div>
        </div>
      </Panel>

      <Panel title="Projekty operacyjne">
        {projectReadinessError ? (
          <div className="mx-4 mt-4 border border-warning/40 bg-warning/10 p-3 font-mono text-[10px] uppercase text-warning">
            Backend readiness projektów niedostępny: {projectReadinessError}
          </div>
        ) : null}
        <div className="grid gap-3 p-4 xl:grid-cols-3">
          {accounts.map((project) => {
            const linkedChannel = findProjectChannel(project, accountControlRows)
            const backendReadiness = projectReadinessById.get(project.id)
            const snapshot = projectControlSnapshot(project, linkedChannel)
            const cardLabel = backendReadiness ? projectReadinessLabel(backendReadiness.status) : snapshot.label
            const cardClass = backendReadiness ? projectReadinessClass(backendReadiness.status) : snapshot.className
            const cardAction = backendReadiness ? humanProjectAction(backendReadiness.next_action) : snapshot.action
            const cardDetail = backendReadiness?.description ?? snapshot.detail
            const cardAccess = backendReadiness ? projectRequirementAccess(backendReadiness) : snapshot.access
            const cardBlockers = backendReadiness?.blockers ?? snapshot.blockers
            const actionLoading = projectActionLoadingId?.startsWith(`${project.id}:`) ?? false
            const canResearchProject = project.type === 'youtube' && (backendReadiness?.capabilities.can_research ?? true)
            const canGenerateProject = project.type === 'youtube' && (backendReadiness?.capabilities.can_generate_private ?? true)
            const activeProject = project.id === activeAccountId
            return (
              <article
                key={project.id}
                className={[
                  'border bg-void p-4',
                  activeProject ? 'border-accent-primary/60' : 'border-border-default',
                ].join(' ')}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                      {project.name}
                    </div>
                    <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                      {PROJECT_TYPE_LABELS[project.type]}
                    </div>
                  </div>
                  <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${cardClass}`}>
                    {cardLabel}
                  </span>
                </div>
                <div className="mt-3 min-h-10 overflow-hidden text-xs leading-relaxed text-text-secondary">
                  {project.niche}
                </div>

                <div className="mt-3 grid grid-cols-4 gap-2">
                  {snapshot.metrics.map(([label, value]) => (
                    <div key={label} className="border border-border-default bg-panel p-2">
                      <div className="font-mono text-[9px] uppercase text-text-tertiary">{label}</div>
                      <div className="mt-1 truncate font-display text-[13px] font-semibold uppercase text-text-primary">
                        {value}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-3 grid grid-cols-4 gap-2 border border-border-default bg-panel p-3 font-mono text-[10px] uppercase text-text-secondary">
                  {cardAccess.map(([label, ready]) => (
                    <span key={label} className="flex items-center gap-2">
                      <i className={`h-2 w-2 rounded-full ${readinessDotClass(ready)}`} />
                      {label}
                    </span>
                  ))}
                </div>

                {backendReadiness?.distribution_targets?.length ? (
                  <div className="mt-3 grid grid-cols-3 gap-2 border border-border-default bg-panel p-3 font-mono text-[10px] uppercase text-text-secondary">
                    {backendReadiness.distribution_targets.map((target) => (
                      <span key={target.platform} className="flex items-center gap-2">
                        <i className={`h-2 w-2 rounded-full ${readinessDotClass(target.status === 'ready')}`} />
                        {target.label}
                      </span>
                    ))}
                  </div>
                ) : null}

                <div className="mt-3 border border-border-default bg-panel p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Następny krok projektu</div>
                  <div className="mt-1 font-display text-[12px] font-semibold uppercase tracking-[0.08em] text-accent-primary">
                    {cardAction}
                  </div>
                  <div className="mt-2 text-[11px] leading-relaxed text-text-tertiary">
                    {cardDetail}
                  </div>
                </div>

                {cardBlockers.length ? (
                  <div className="mt-3 space-y-1 border border-warning/25 bg-warning/5 p-2">
                    {cardBlockers.slice(0, 4).map((blocker) => (
                      <div key={blocker} className="font-mono text-[10px] uppercase text-warning">
                        {operatorActionLabel(blocker)}
                      </div>
                    ))}
                  </div>
                ) : null}

                {backendReadiness?.access_playbook ? (
                  <div className="mt-3 border border-border-default bg-panel p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="font-mono text-[10px] uppercase text-text-tertiary">Playbook dostępów</div>
                      <span className="font-mono text-[9px] uppercase text-text-tertiary">
                        {playbookModeLabel(backendReadiness.access_playbook.mode)}
                      </span>
                    </div>
                    <div className="mt-2 space-y-1">
                      {backendReadiness.access_playbook.required_connections.slice(0, 3).map((connection) => (
                        <div key={connection.key} className="flex items-start justify-between gap-2 border border-border-default bg-void p-2">
                          <div className="min-w-0">
                            <div className="truncate font-display text-[11px] font-semibold uppercase tracking-[0.08em] text-text-primary">
                              {connection.label}
                            </div>
                            <div className="mt-1 truncate font-mono text-[9px] uppercase text-text-tertiary">
                              {connection.where}
                            </div>
                          </div>
                          <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${playbookStatusClass(connection.status)}`}>
                            {playbookStatusLabel(connection.status)}
                          </span>
                        </div>
                      ))}
                    </div>
                    <div className="mt-2 text-[11px] leading-relaxed text-warning">
                      {backendReadiness.access_playbook.operator_warning}
                    </div>
                  </div>
                ) : null}

                <div className="mt-3 grid grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => void runProjectIntelligence(project, backendReadiness)}
                    disabled={!canResearchProject || actionLoading}
                    className="border border-border-default px-2 py-2 font-display text-[10px] uppercase tracking-[0.1em] text-text-secondary disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Dane
                  </button>
                  <button
                    type="button"
                    onClick={() => void generateProjectTopics(project)}
                    disabled={!canGenerateProject || actionLoading}
                    className="border border-border-default px-2 py-2 font-display text-[10px] uppercase tracking-[0.1em] text-text-secondary disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Tematy
                  </button>
                  <button
                    type="button"
                    onClick={() => void runProjectProduction(project)}
                    disabled={!canGenerateProject || actionLoading}
                    className="border border-accent-primary/50 px-2 py-2 font-display text-[10px] uppercase tracking-[0.1em] text-accent-primary disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {actionLoading ? 'Praca...' : 'Produkcja'}
                  </button>
                </div>

                <button
                  type="button"
                  onClick={() => setActiveAccount(project.id)}
                  className="mt-4 w-full border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
                >
                  {activeProject ? 'Aktywny projekt' : 'Przełącz projekt'}
                </button>
                <Link
                  to={`/projects/${encodeURIComponent(project.id)}`}
                  className="mt-2 flex w-full items-center justify-center border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary"
                >
                  Otwórz kontrolę projektu
                </Link>
              </article>
            )
          })}
        </div>
      </Panel>

      <Panel title="Kontrola kanałów">
        <div className="grid gap-3 p-4 xl:grid-cols-3">
          {accountControlRows.map((channel) => {
            const state = accountOperationalState(channel)
            const activeChannelJobs = channel.active_job_details ?? []
            const oauthReady = channel.youtube_oauth?.ready ?? channel.oauth_ready
            const captionsReady = channel.upload_capabilities?.captions_api?.ready ?? channel.captions_api_ready
            const thumbnailReady = channel.upload_capabilities?.custom_thumbnail?.ready ?? channel.custom_thumbnail_ready
            const topicBuffer = channel.topic_counts?.buffer_count ?? channel.topic_buffer ?? 0
            const approvedTopics = channel.topic_counts?.approved ?? channel.approved_topics ?? 0
            const readyPrivate = channel.job_counts?.ready_private ?? channel.ready_private_upload_count ?? 0
            const activeCount = channel.job_counts?.active ?? channel.active_jobs ?? activeChannelJobs.length
            const ownAnalyticsRows = channel.analytics?.own_performance_rows ?? channel.own_performance_rows ?? 0

            return (
              <article key={channel.account_id} className="border border-border-default bg-void p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                      {channel.channel_title || channel.account_id}
                    </div>
                    <div className="mt-1 truncate font-mono text-[10px] uppercase text-text-tertiary">
                      {channel.account_id}
                    </div>
                  </div>
                  <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${state.className}`}>
                    {state.label}
                  </span>
                </div>

                <div className="mt-4 grid grid-cols-4 gap-2">
                  {[
                    ['Bufor', topicBuffer],
                    ['Zatwierdzone', approvedTopics],
                    ['Prywatne', readyPrivate],
                    ['Aktywne', activeCount],
                  ].map(([label, value]) => (
                    <div key={label} className="border border-border-default bg-panel p-2">
                      <div className="font-mono text-[9px] uppercase text-text-tertiary">{label}</div>
                      <div className="mt-1 font-display text-[15px] font-semibold uppercase text-text-primary">
                        {formatCount(Number(value))}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-4 border border-border-default bg-panel p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Dane i sygnały</div>
                  <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[10px] uppercase text-text-secondary">
                    <span>
                      YT
                      <strong className="block text-text-primary">
                        {formatCount(channel.signals?.youtube_trend_videos)}
                      </strong>
                    </span>
                    <span>
                      Trends
                      <strong className="block text-text-primary">
                        {formatCount(channel.signals?.google_trends_keywords)}
                      </strong>
                    </span>
                    <span>
                      Analytics
                      <strong className={ownAnalyticsRows > 0 ? 'block text-success' : 'block text-warning'}>
                        {formatCount(ownAnalyticsRows)}
                      </strong>
                    </span>
                  </div>
                </div>

                <div className="mt-3 grid grid-cols-3 gap-2 border border-border-default bg-panel p-3 font-mono text-[10px] uppercase text-text-secondary">
                  <span className="flex items-center gap-2">
                    <i className={`h-2 w-2 rounded-full ${readinessDotClass(oauthReady)}`} />
                    OAuth
                  </span>
                  <span className="flex items-center gap-2">
                    <i className={`h-2 w-2 rounded-full ${readinessDotClass(thumbnailReady)}`} />
                    Miniatury
                  </span>
                  <span className="flex items-center gap-2">
                    <i className={`h-2 w-2 rounded-full ${readinessDotClass(captionsReady)}`} />
                    Napisy API
                  </span>
                </div>

                {activeChannelJobs.length > 0 ? (
                  <div className="mt-3 space-y-2">
                    {activeChannelJobs.slice(0, 2).map((job) => (
                      <div key={job.job_id} className="border border-success/25 bg-success/5 p-3">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="truncate font-display text-[12px] font-semibold uppercase text-text-primary">
                              {job.topic_title || job.job_id}
                            </div>
                            <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                              Krok: {job.current_step || job.status}
                            </div>
                          </div>
                          <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${activeMoneyJobStatusClass(job)}`}>
                            {job.stale_candidate ? 'stale' : 'live'}
                          </span>
                        </div>
                        <div className="mt-2 flex items-center justify-between font-mono text-[10px] uppercase text-text-tertiary">
                          <span>Heartbeat {formatRuntimeSeconds(job.heartbeat_age_seconds)}</span>
                          <Link to={`/studio/${job.job_id}`} className="text-accent-primary">
                            Studio
                          </Link>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}

                <div className="mt-3 border border-border-default bg-panel p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Następna akcja</div>
                  <div className="mt-1 font-display text-[12px] font-semibold uppercase tracking-[0.08em] text-accent-primary">
                    {operatorActionLabel(channel.next_action)}
                  </div>
                </div>

                {channel.blockers?.length ? (
                  <div className="mt-3 space-y-1 border border-warning/30 bg-warning/5 p-3">
                    {channel.blockers.slice(0, 4).map((blocker) => (
                      <div key={blocker} className="font-mono text-[10px] uppercase text-warning">
                        {operatorActionLabel(blocker)}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="mt-3 border border-success/25 bg-success/5 p-3 font-mono text-[10px] uppercase text-success">
                    Brak krytycznych blokerów kanału
                  </div>
                )}
              </article>
            )
          })}

          {moneySummary && accountControlRows.length === 0 ? (
            <div className="border border-dashed border-border-default p-5 text-sm text-text-secondary xl:col-span-3">
              Backend nie zwrócił listy kanałów. Odśwież status maszynki albo sprawdź konfigurację kont.
            </div>
          ) : null}

          {!moneySummary ? (
            <div className="border border-dashed border-border-default p-5 text-sm text-text-secondary xl:col-span-3">
              Ładuję status floty kanałów z backendu. Jeśli panel zostaje pusty, backend Atnosa nie odpowiada albo nie ma aktywnej konfiguracji money-machine.
            </div>
          ) : null}
        </div>
      </Panel>

      <Panel title="Maszynka zarabiania">
        <div className="grid gap-4 p-4 xl:grid-cols-[320px_1fr]">
          <div className="border border-border-default bg-void p-4">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">Następny krok operatora</div>
            <div className="mt-2 font-display text-lg font-semibold uppercase tracking-[0.1em] text-accent-primary">
              {operatorActionLabel(moneySummary?.next_operator_action)}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2 font-mono text-[10px] uppercase text-text-tertiary">
              <span>
                Prywatnie gotowe
                <strong className="mt-1 block text-lg text-text-primary">
                  {moneySummary?.totals.ready_private_upload_jobs ?? '-'}
                </strong>
              </span>
              <span>
                Public QA OK
                <strong className="mt-1 block text-lg text-success">
                  {moneySummary?.totals.public_ready_private_upload_jobs ?? '-'}
                </strong>
              </span>
              <span>
                Do regeneracji
                <strong className="mt-1 block text-lg text-warning">
                  {moneySummary?.totals.blocked_private_upload_jobs ?? '-'}
                </strong>
              </span>
              <span>
                Bufor tematów
                <strong className="mt-1 block text-lg text-text-primary">
                  {moneySummary?.totals.topic_buffer ?? '-'}
                </strong>
              </span>
            </div>
            <div className="mt-4 border border-border-default bg-panel p-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Budżet API</div>
                  <div className="mt-1 font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                    {formatPln(costGuard?.monthly_remaining_pln)} zostało
                  </div>
                </div>
                <span
                  className={[
                    'border px-2 py-1 font-mono text-[10px] uppercase',
                    costGuardStatusClass(costGuard),
                  ].join(' ')}
                >
                  {costGuardStatusLabel(costGuard)}
                </span>
              </div>
              <div className="mt-3 space-y-2">
                <div>
                  <div className="mb-1 flex justify-between font-mono text-[10px] uppercase text-text-tertiary">
                    <span>Miesiąc</span>
                    <span>
                      {formatPln(costGuard?.monthly_spent_pln)} / {formatPln(costGuard?.monthly_limit_pln)}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden bg-void">
                    <div
                      className={[
                        'h-full',
                        costGuardBlocksProduction ? 'bg-error' : costGuard?.warning ? 'bg-warning' : 'bg-success',
                      ].join(' ')}
                      style={{ width: `${monthlyBudgetPct}%` }}
                    />
                  </div>
                </div>
                <div>
                  <div className="mb-1 flex justify-between font-mono text-[10px] uppercase text-text-tertiary">
                    <span>Dzień</span>
                    <span>
                      {formatPln(costGuard?.daily_spent_pln)} / {formatPln(costGuard?.daily_limit_pln)}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden bg-void">
                    <div
                      className={[
                        'h-full',
                        costGuardBlocksProduction ? 'bg-error' : costGuard?.warning ? 'bg-warning' : 'bg-success',
                      ].join(' ')}
                      style={{ width: `${dailyBudgetPct}%` }}
                    />
                  </div>
                </div>
              </div>
              <div className="mt-3 font-mono text-[10px] uppercase text-text-tertiary">
                Następny render: {formatPln(costGuard?.estimated_action_cost_pln)}
              </div>
            </div>
            <div className="mt-4 border border-border-default bg-panel p-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Aktywna produkcja</div>
                  <div className="mt-1 font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                    {activeMoneyJobs.length ? `${activeMoneyJobs.length} job działa` : 'Brak aktywnego joba'}
                  </div>
                </div>
                <span
                  className={[
                    'border px-2 py-1 font-mono text-[10px] uppercase',
                    activeMoneyJobs.some((job) => job.stale_candidate)
                      ? 'border-error/50 bg-error/10 text-error'
                      : activeMoneyJobs.length
                        ? 'border-success/50 bg-success/10 text-success'
                        : 'border-text-tertiary/30 bg-elevated/40 text-text-secondary',
                  ].join(' ')}
                >
                  {activeMoneyJobs.some((job) => job.stale_candidate)
                    ? 'uwaga'
                    : activeMoneyJobs.length
                      ? 'pracuje'
                      : 'idle'}
                </span>
              </div>
              <div className="mt-3 space-y-2">
                {activeMoneyJobs.slice(0, 3).map((job) => (
                  <div key={job.job_id} className="border border-border-default bg-void p-2">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate font-display text-[12px] font-semibold uppercase tracking-[0.08em] text-text-primary">
                          {job.topic_title || job.job_id}
                        </div>
                        <div className="mt-1 truncate font-mono text-[10px] uppercase text-text-tertiary">
                          {job.account_id}
                        </div>
                      </div>
                      <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${activeMoneyJobStatusClass(job)}`}>
                        {job.current_step || job.status}
                      </span>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[10px] uppercase text-text-tertiary">
                      <span>Heartbeat: {formatRuntimeSeconds(job.heartbeat_age_seconds)}</span>
                      <span>Stale po: {formatRuntimeSeconds(job.stale_after_seconds)}</span>
                    </div>
                    <Link
                      to={`/studio/${job.job_id}`}
                      className="mt-2 inline-flex border border-accent-primary/50 px-2 py-1 font-display text-[10px] uppercase tracking-[0.12em] text-accent-primary"
                    >
                      Otwórz studio
                    </Link>
                  </div>
                ))}
                {activeMoneyJobs.length === 0 ? (
                  <div className="border border-dashed border-border-default p-3 text-xs text-text-secondary">
                    System nie raportuje aktywnego renderu dla tego konta. Użyj odświeżenia albo uruchom pętlę treści.
                  </div>
                ) : null}
              </div>
            </div>
            <button
              type="button"
              onClick={() => void Promise.all([loadMoneySummary(), loadPublicPromotionPlan()])}
              disabled={moneySummaryLoading}
              className="mt-4 flex w-full items-center justify-center gap-2 border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw size={13} className={moneySummaryLoading ? 'animate-spin' : ''} />
              Odśwież status
            </button>
            <button
              type="button"
              onClick={() => void runSafeMoneyMachineStep()}
              disabled={
                moneyActionLoading ||
                !moneySummary ||
                !moneySafeActionAvailable
              }
              className="mt-2 flex w-full items-center justify-center gap-2 border border-success/50 bg-success/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-success disabled:cursor-not-allowed disabled:opacity-45"
            >
              <Play size={13} />
              {moneyActionLoading ? 'Wykonuję...' : 'Wykonaj następny bezpieczny krok'}
            </button>
            <button
              type="button"
              onClick={() => void runContentLoopOnce()}
              disabled={contentLoopLoading || costGuardBlocksProduction}
              title={
                costGuardBlocksProduction
                  ? 'Budżet API blokuje kolejny render. Zmień limit lub poczekaj na kolejny okres.'
                  : undefined
              }
              className="mt-2 flex w-full items-center justify-center gap-2 border border-warning/50 bg-warning/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-warning disabled:cursor-not-allowed disabled:opacity-45"
            >
              <RefreshCw size={13} className={contentLoopLoading ? 'animate-spin' : ''} />
              {contentLoopLoading ? 'Pętla działa...' : costGuardBlocksProduction ? 'Budżet blokuje pętlę' : 'Uruchom pętlę treści'}
            </button>
            <div className="mt-2 text-[11px] leading-relaxed text-text-tertiary">
              Publiczny upload nie działa automatycznie. Najpierw prywatny upload i review, potem osobny panel publiczny z terminem UTC.
            </div>
            <div className="mt-3 border border-border-default bg-panel p-2">
              <div className="flex items-center justify-between gap-2 font-mono text-[10px] uppercase text-text-tertiary">
                <span>Autopilot scheduler</span>
                <span className={productionAutopilotAutomatic ? 'text-success' : 'text-warning'}>
                  {productionAutopilotAutomatic ? 'automatyczny' : 'ręczny/ograniczony'}
                </span>
              </div>
              <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                Interwał: {productionAutopilotInterval || '-'} min
              </div>
            </div>
            {moneySummaryError ? (
              <div className="mt-3 border border-error/40 bg-error/10 p-2 text-xs text-error">{moneySummaryError}</div>
            ) : null}
            {moneySummary?.global_blockers.length ? (
              <div className="mt-3 space-y-1 border border-warning/30 bg-warning/5 p-2">
                {moneySummary.global_blockers.slice(0, 5).map((blocker) => (
                  <div key={blocker} className="font-mono text-[10px] uppercase text-warning">
                    {operatorActionLabel(blocker)}
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <div className="border border-border-default bg-void p-3">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div>
                  <div className="font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                    Gotowe do prywatnego uploadu
                  </div>
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">
                    Kliknięcie wysyła tylko jako private
                  </div>
                </div>
                <span className="border border-success/40 px-2 py-1 font-mono text-[10px] uppercase text-success">
                  {moneySummary?.private_upload_ready.length ?? 0}
                </span>
              </div>
              <div className="space-y-2">
                {moneySummary?.private_upload_ready.map((job) => (
                  <div key={job.job_id} className="border border-border-default bg-panel p-3">
                    <div className="font-display text-[12px] font-semibold uppercase text-text-primary">
                      {job.topic_title}
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[10px] uppercase text-text-tertiary">
                      <span>Głos: {job.quality?.voice ?? '-'}</span>
                      <span>Wideo: {job.quality?.video_backend ?? '-'}</span>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => void uploadSummaryJobPrivate(job)}
                        disabled={privateUploadJobId === job.job_id}
                        className="border border-success/50 bg-success/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-success disabled:cursor-wait disabled:opacity-60"
                      >
                        {privateUploadJobId === job.job_id ? 'Wysyłam...' : 'Wyślij prywatnie'}
                      </button>
                      <Link
                        to={`/studio/${job.job_id}`}
                        className="border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
                      >
                        Studio
                      </Link>
                    </div>
                  </div>
                ))}
                {moneySummary && moneySummary.private_upload_ready.length === 0 ? (
                  <div className="border border-dashed border-border-default p-4 text-sm text-text-secondary">
                    Brak jobów, które mogą iść prywatnie bez regeneracji.
                  </div>
                ) : null}
              </div>
            </div>

            <div className="border border-border-default bg-void p-3">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div>
                  <div className="font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                    Wymagają regeneracji
                  </div>
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">
                    Public quality gate blokuje słabe rendery
                  </div>
                </div>
                <span className="border border-warning/40 px-2 py-1 font-mono text-[10px] uppercase text-warning">
                  {moneySummary?.recovery_ready.length ?? 0}
                </span>
              </div>
              <div className="space-y-2">
                {moneySummary?.recovery_ready.map((job) => (
                  <div key={job.job_id} className="border border-border-default bg-panel p-3">
                    <div className="font-display text-[12px] font-semibold uppercase text-text-primary">
                      {job.topic_title}
                    </div>
                    <div className="mt-2 space-y-1">
                      {job.public_quality_blockers.slice(0, 3).map((blocker) => (
                        <div key={blocker} className="font-mono text-[10px] uppercase text-warning">
                          {operatorActionLabel(blocker)}
                        </div>
                      ))}
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => void regenerateSummaryJob(job)}
                        disabled={recoveryJobId === job.job_id}
                        className="border border-warning/50 bg-warning/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-warning disabled:cursor-wait disabled:opacity-60"
                      >
                        {recoveryJobId === job.job_id ? 'Regeneruję...' : 'Regeneruj'}
                      </button>
                      <Link
                        to={`/studio/${job.job_id}`}
                        className="border border-border-default px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary"
                      >
                        Raport
                      </Link>
                    </div>
                  </div>
                ))}
                {moneySummary && moneySummary.recovery_ready.length === 0 ? (
                  <div className="border border-dashed border-border-default p-4 text-sm text-text-secondary">
                    Brak zablokowanych jobów w limicie podglądu.
                  </div>
                ) : null}
              </div>
            </div>

            <div className="border border-border-default bg-void p-3">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div>
                  <div className="font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                    Publiczny review
                  </div>
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">
                    Tylko po prywatnym uploadzie i terminie UTC
                  </div>
                </div>
                <span
                  className={[
                    'border px-2 py-1 font-mono text-[10px] uppercase',
                    moneySummary?.public_upload_enabled ? 'border-success/40 text-success' : 'border-warning/40 text-warning',
                  ].join(' ')}
                >
                  {moneySummary?.public_upload_enabled ? publicPromotionReady.length : 'blok'}
                </span>
              </div>
              <div className="space-y-2">
                {publicPromotionReady.slice(0, 3).map((job) => (
                  <div key={job.job_id} className="border border-border-default bg-panel p-3">
                    <div className="font-display text-[12px] font-semibold uppercase text-text-primary">
                      {job.topic_title}
                    </div>
                    <div className="mt-2 font-mono text-[10px] uppercase text-success">
                      {job.reason}
                    </div>
                    {job.youtube_url ? (
                      <a
                        href={job.youtube_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 block truncate font-mono text-[10px] uppercase text-accent-primary"
                      >
                        Prywatny film: {job.youtube_video_id || job.youtube_url}
                      </a>
                    ) : null}
                  </div>
                ))}
                {publicPromotionBlocked.slice(0, 3).map((job) => (
                  <div key={`${job.job_id}-${job.action}`} className="border border-warning/25 bg-warning/5 p-3">
                    <div className="font-display text-[12px] font-semibold uppercase text-text-primary">
                      {job.topic_title || job.job_id}
                    </div>
                    <div className="mt-2 font-mono text-[10px] uppercase text-warning">
                      {operatorActionLabel(job.action)}: {job.reason}
                    </div>
                  </div>
                ))}
                {!publicPromotionLoading && publicPromotionReady.length === 0 && publicPromotionBlocked.length === 0 ? (
                  <div className="border border-dashed border-border-default p-4 text-sm text-text-secondary">
                    Brak prywatnych uploadów gotowych do publicznego review.
                  </div>
                ) : null}
              </div>
              <label className="mt-3 block">
                <span className="font-mono text-[10px] uppercase text-text-tertiary">Publish at UTC</span>
                <input
                  value={publicPublishAt}
                  onChange={(event) => setPublicPublishAt(event.target.value)}
                  placeholder="2026-06-08T14:00:00Z"
                  className="mt-1 w-full border border-border-default bg-panel px-3 py-2 font-mono text-[11px] text-text-primary outline-none focus:border-accent-primary"
                />
              </label>
              <button
                type="button"
                onClick={() => void runPublicPromotion()}
                disabled={
                  publicPromotionExecuting ||
                  publicPromotionReady.length === 0 ||
                  !moneySummary?.public_upload_enabled ||
                  !publicPublishAtReady
                }
                className="mt-3 flex w-full items-center justify-center gap-2 border border-success/50 bg-success/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-success disabled:cursor-not-allowed disabled:opacity-45"
              >
                <Play size={13} />
                {publicPromotionExecuting ? 'Planuję...' : 'Zaplanuj publicznie'}
              </button>
              <button
                type="button"
                onClick={() => void loadPublicPromotionPlan()}
                disabled={publicPromotionLoading}
                className="mt-2 flex w-full items-center justify-center gap-2 border border-accent-primary/40 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
              >
                <RefreshCw size={13} className={publicPromotionLoading ? 'animate-spin' : ''} />
                Odśwież public review
              </button>
              {publicPromotionError ? (
                <div className="mt-3 border border-error/40 bg-error/10 p-2 text-xs text-error">{publicPromotionError}</div>
              ) : null}
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="Gotowość produkcji shortów">
        <div className="grid gap-4 p-4 xl:grid-cols-[280px_1fr]">
          <div className="border border-border-default bg-void p-4">
            <div className="font-mono text-[10px] uppercase text-text-tertiary">Stan systemu</div>
            <div
              className={[
                'mt-2 inline-flex border px-3 py-2 font-display text-[13px] font-semibold uppercase tracking-[0.12em]',
                readinessOverallClass(readiness),
              ].join(' ')}
            >
              {readinessOverallLabel(readiness)}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2 font-mono text-[10px] uppercase text-text-tertiary">
              <span>
                Blokery
                <strong className="mt-1 block text-lg text-text-primary">{readiness?.blocking_count ?? '-'}</strong>
              </span>
              <span>
                Ostrzeżenia
                <strong className="mt-1 block text-lg text-text-primary">{readiness?.warning_count ?? '-'}</strong>
              </span>
              <span>
                Prywatnie
                <strong className={['mt-1 block text-lg', readiness?.private_upload_ready ? 'text-success' : readiness?.private_review_ready ? 'text-accent-primary' : 'text-text-primary'].join(' ')}>
                  {readiness ? (readiness.private_upload_ready ? 'UPLOAD' : readiness.private_review_ready ? 'REVIEW' : 'NIE') : '-'}
                </strong>
              </span>
              <span>
                Publicznie
                <strong className={['mt-1 block text-lg', readiness?.public_publish_ready ? 'text-success' : 'text-warning'].join(' ')}>
                  {readiness ? (readiness.public_publish_ready ? 'TAK' : 'BLOK') : '-'}
                </strong>
              </span>
            </div>
            {readiness && !readiness.public_publish_ready ? (
              <div className="mt-3 border border-warning/40 bg-warning/10 p-2 text-xs text-warning">
                Publiczna publikacja nie jest pełnym green-lightem:{' '}
                {(readiness.public_publish_blocking ?? []).length
                  ? readiness.public_publish_blocking?.join(', ')
                  : readiness.public_upload_enabled === false
                    ? 'ALLOW_PUBLIC_UPLOAD=false'
                    : 'wymaga review/konfiguracji'}
              </div>
            ) : null}
            <button
              type="button"
              onClick={() => void loadReadiness(true)}
              disabled={readinessLoading}
              className="mt-4 flex w-full items-center justify-center gap-2 border border-accent-primary/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw size={13} className={readinessLoading ? 'animate-spin' : ''} />
              Sprawdź live
            </button>
            <button
              type="button"
              onClick={() => void ensureShortsModels()}
              disabled={modelEnsureLoading}
              className="mt-2 flex w-full items-center justify-center gap-2 border border-warning/50 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-warning disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshCw size={13} className={modelEnsureLoading ? 'animate-spin' : ''} />
              Pobierz modele shortów
            </button>
            {modelEnsureStatus ? (
              <div className="mt-3 border border-border-default bg-panel p-2 text-xs text-text-secondary">
                {modelEnsureStatus}
              </div>
            ) : null}
            {modelDownload && modelDownload.status !== 'idle' ? (
              <div className="mt-3 border border-border-default bg-panel p-2">
                <div className="flex items-center justify-between gap-2 font-mono text-[10px] uppercase text-text-tertiary">
                  <span>Download modeli</span>
                  <span className={modelDownload.status === 'running' ? 'text-warning' : 'text-text-secondary'}>
                    {modelDownload.status}
                  </span>
                </div>
                <div className="mt-2 space-y-2">
                  {Object.entries(modelDownload.progress).map(([modelId, progress]) => (
                    <div key={modelId}>
                      <div className="flex items-center justify-between gap-2 font-mono text-[10px] uppercase text-text-tertiary">
                        <span className="truncate">{modelId}</span>
                        <span>{Math.round(progress * 100)}%</span>
                      </div>
                      <div className="mt-1 h-1 border border-border-default bg-void">
                        <div
                          className="h-full bg-accent-primary"
                          style={{ width: `${Math.max(3, Math.round(progress * 100))}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {readinessError ? (
              <div className="mt-3 border border-error/40 bg-error/10 p-2 text-xs text-error">{readinessError}</div>
            ) : null}
          </div>

          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {readiness ? (
              sortReadinessItems(readiness.items).slice(0, 9).map((item) => (
                <div key={item.key} className="border border-border-default bg-void p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate font-display text-[12px] font-semibold uppercase tracking-[0.1em] text-text-primary">
                        {item.label}
                      </div>
                      <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                        {item.required ? 'wymagane' : 'opcjonalne'}
                      </div>
                    </div>
                    <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${readinessStatusClass(item.status)}`}>
                      {readinessStatusLabel(item.status)}
                    </span>
                  </div>
                  <div className="mt-3 line-clamp-2 text-xs text-text-secondary">{item.message}</div>
                  {item.key === 'models' && modelMissingDetails(item).length ? (
                    <div className="mt-3 space-y-1 border-t border-border-default pt-2 font-mono text-[10px] uppercase text-text-tertiary">
                      {modelMissingDetails(item).map((model) => (
                        <div key={model.id} className="flex items-center justify-between gap-2">
                          <span className="truncate">{model.name}</span>
                          <span className="shrink-0 text-warning">{model.sizeGb.toFixed(1)} GB</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="border border-dashed border-border-default p-5 text-sm text-text-secondary md:col-span-2 xl:col-span-3">
                Brak odczytu gotowości. Użyj „Sprawdź live” albo uruchom backend Atnosa.
              </div>
            )}
          </div>
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <Panel title="Status pipeline">
          <div className="p-4">
            {activeJob ? (
              <>
                <div className="mb-4 flex items-center justify-between gap-3">
                  <div>
                    <div className="font-mono text-[10px] uppercase text-text-tertiary">Aktywny job</div>
                    <div className="font-display text-base font-semibold uppercase tracking-[0.08em] text-text-primary">
                      {activeJob.title}
                    </div>
                    {activeProductionJob?.ready_for_review ? (
                      <div className="mt-2 font-mono text-[10px] uppercase text-success">
                        QA gotowe. Pakiet review: {activeProductionJob.ready_for_private_upload ? 'można wysłać prywatnie' : 'wymaga poprawek'}
                      </div>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    {activeProductionJob?.ready_for_private_upload ? (
                      <button
                        type="button"
                        onClick={() => void uploadPrivate(activeProductionJob)}
                        disabled={privateUploadJobId === activeProductionJob.id}
                        className="border border-success/50 bg-success/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-success disabled:cursor-wait disabled:opacity-60"
                      >
                        {privateUploadJobId === activeProductionJob.id ? 'Wysyłam...' : 'Wyślij prywatnie'}
                      </button>
                    ) : null}
                    <Link
                      to={`/studio/${activeJob.id}`}
                      className="border border-accent-primary px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
                    >
                      Otwórz studio
                    </Link>
                  </div>
                </div>
                <div className="grid gap-2 md:grid-cols-6">
                  {activeJob.steps.map((step) => (
                    <button
                      key={step.id}
                      type="button"
                      className={`min-h-24 border p-3 text-left ${STEP_STATUS_CLASS[step.status]}`}
                    >
                      <div className="font-display text-[12px] font-semibold uppercase tracking-[0.12em]">
                        {step.label}
                      </div>
                      <div className="mt-2 font-mono text-[10px] uppercase">{STEP_STATUS_LABELS[step.status]}</div>
                      <div className="mt-3 h-1 bg-void">
                        <div
                          className="h-full bg-current transition-all"
                          style={{ width: `${Math.max(0, Math.min(step.progress, 100))}%` }}
                        />
                      </div>
                    </button>
                  ))}
                </div>
                <div className="mt-4 border border-border-default bg-void">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-default px-3 py-2">
                    <div>
                      <div className="font-display text-[11px] uppercase tracking-[0.16em] text-text-primary">
                        Plan dystrybucji
                      </div>
                      <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                        YouTube Shorts / TikTok / Instagram Reels
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      {activeProductionJob ? (
                        <button
                          type="button"
                          onClick={() => void createDistributionPackageForJob(activeProductionJob)}
                          disabled={distributionPackageLoadingJobId === activeProductionJob.id}
                          className="border border-accent-primary/50 bg-accent-primary/10 px-2 py-1 font-display text-[10px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
                        >
                          {distributionPackageLoadingJobId === activeProductionJob.id ? 'Tworzę...' : 'Przygotuj paczki'}
                        </button>
                      ) : null}
                      {activeDistributionPlan ? (
                        <span className={`border px-2 py-1 font-mono text-[9px] uppercase ${distributionStatusClass(activeDistributionPlan.status)}`}>
                          {distributionStatusLabel(activeDistributionPlan.status)}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <div className="grid gap-2 p-3 md:grid-cols-3">
                    {activeDistributionPlan?.platforms.length ? (
                      activeDistributionPlan.platforms.map((platform) => (
                        <div key={platform.platform} className="border border-border-default bg-panel p-3">
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <div className="font-display text-[12px] uppercase tracking-[0.1em] text-text-primary">
                                {platform.label}
                              </div>
                              <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                                {platform.connector_status === 'implemented' ? 'connector API' : 'export/manual'}
                              </div>
                            </div>
                            <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${distributionStatusClass(platform.status)}`}>
                              {distributionStatusLabel(platform.status)}
                            </span>
                          </div>
                          <div className="mt-3 grid grid-cols-2 gap-2 font-mono text-[10px] uppercase">
                            <span className={platform.export_ready ? 'text-success' : 'text-error'}>
                              export {platform.export_ready ? 'tak' : 'nie'}
                            </span>
                            <span className={platform.upload_ready || platform.public_upload_ready ? 'text-success' : 'text-warning'}>
                              upload {platform.upload_ready || platform.public_upload_ready ? 'tak' : 'blok'}
                            </span>
                          </div>
                          {platform.blockers.length ? (
                            <div className="mt-3 space-y-1 font-mono text-[10px] text-error">
                              {platform.blockers.slice(0, 3).map((blocker) => (
                                <div key={blocker} className="truncate">blok: {blocker}</div>
                              ))}
                            </div>
                          ) : null}
                          {platform.next_actions[0] ? (
                            <div className="mt-3 line-clamp-2 text-xs text-text-secondary">
                              {platform.next_actions[0]}
                            </div>
                          ) : null}
                        </div>
                      ))
                    ) : (
                      <div className="border border-dashed border-border-default p-4 text-sm text-text-secondary md:col-span-3">
                        {distributionPlanError || 'Plan dystrybucji pojawi się po utworzeniu backendowego joba produkcyjnego.'}
                      </div>
                    )}
                  </div>
                  {activeDistributionPlan?.next_actions.length ? (
                    <div className="border-t border-border-default px-3 py-2 font-mono text-[10px] uppercase text-text-tertiary">
                      Następny krok: {activeDistributionPlan.next_actions[0]}
                    </div>
                  ) : null}
                  {distributionPackageStatus ? (
                    <div className="border-t border-border-default px-3 py-2 font-mono text-[10px] uppercase text-text-secondary">
                      {distributionPackageStatus}
                    </div>
                  ) : null}
                </div>
              </>
            ) : (
              <div className="border border-dashed border-border-default p-8 text-center text-text-secondary">
                Brak joba w kolejce. Dodaj temat, żeby zobaczyć pipeline.
              </div>
            )}
          </div>
          <div className="border-t border-border-default">
            <div className="flex items-center justify-between border-b border-border-default px-4 py-2">
              <span className="font-display text-[11px] uppercase tracking-[0.16em] text-text-tertiary">Log joba</span>
              <span className="font-mono text-[10px] text-text-tertiary">ostatnie 50</span>
            </div>
            <div className="h-56 overflow-y-auto bg-void p-3 font-mono text-[11px]">
              {accountLogs.slice(0, 50).map((entry) => (
                <div
                  key={entry.id}
                  className={
                    entry.level === 'error'
                      ? 'text-error'
                      : entry.level === 'warn'
                        ? 'text-warning'
                        : 'text-text-secondary'
                  }
                >
                  [{new Date(entry.timestamp).toLocaleTimeString()}] [{entry.step.toUpperCase()}] {entry.message}
                </div>
              ))}
              {accountLogs.length === 0 ? (
                <div className="text-text-tertiary">Brak logów. Uruchom pipeline, żeby wygenerować lokalny stan v1.</div>
              ) : null}
            </div>
          </div>
        </Panel>

        <div className="space-y-4">
          <ProductionReviewPanel
            topic={productionReviewTopic}
            status={productionReviewStatus}
            loading={productionReviewLoading}
            actionLoading={productionReviewActionLoading}
            error={productionReviewError}
            lastRun={productionReviewLastRun}
            onRefresh={() => void loadProductionReview(productionReviewTopic)}
            onPlan={() => void ensureProductionReviewPlan()}
            onRunNext={() => void runNextProductionReviewTask()}
            onRunAll={() => void runAllProductionReviewTasks()}
          />

          <Panel title="Generuj tematy">
            <div className="space-y-3 p-3">
              <button
                type="button"
                onClick={handleGenerateTopics}
                disabled={topicLoading}
                className="flex w-full items-center justify-center gap-2 border border-accent-primary bg-accent-primary/10 px-3 py-3 font-display text-[12px] uppercase tracking-[0.12em] text-accent-primary disabled:cursor-wait disabled:opacity-60"
              >
                <RefreshCw size={14} className={topicLoading ? 'animate-spin' : ''} />
                {topicLoading ? 'Generuję i oceniam...' : 'Generuj 20 tematów'}
              </button>
              <div className="font-mono text-[10px] uppercase text-text-tertiary">
                Źródło: intelligence.db / sygnały YouTube / Google Trends / lokalny Ollama
              </div>
              {topicError ? <div className="border border-error/40 bg-error/10 p-2 text-xs text-error">{topicError}</div> : null}
            </div>
          </Panel>

          <Panel title="Kolejka tematów">
            <div className="space-y-3 p-3">
              <div className="grid gap-2 sm:grid-cols-3">
                <select
                  value={topicSort}
                  onChange={(event) => setTopicSort(event.target.value as TopicSortMode)}
                  className="border border-border-default bg-void px-2 py-2 font-mono text-[11px] uppercase text-text-secondary"
                >
                  <option value="score">Wynik malejaco</option>
                  <option value="freshness">Swiezosc</option>
                  <option value="shorts">Potencjal shorta</option>
                </select>
                <select
                  value={topicFilter}
                  onChange={(event) => setTopicFilter(event.target.value as TopicFilterMode)}
                  className="border border-border-default bg-void px-2 py-2 font-mono text-[11px] uppercase text-text-secondary"
                >
                  <option value="all">Wszystkie</option>
                  <option value="pending">Oczekujace</option>
                  <option value="approved">Zatwierdzone</option>
                  <option value="rejected">Odrzucone</option>
                  <option value="in_production">W produkcji</option>
                </select>
                <button
                  type="button"
                  onClick={() => setShowLowScore((value) => !value)}
                  className="border border-border-default px-2 py-2 font-mono text-[11px] uppercase text-text-secondary"
                >
                  {showLowScore ? 'Ukryj niską ocenę' : 'Pokaż niską ocenę'}
                </button>
              </div>

              {visibleTopics.map((topic) => {
                const expanded = expandedTopicId === topic.id
                const reviewSelected = productionReviewTopic?.id === topic.id
                const reviewReady = reviewSelected && productionReviewStatus?.topic_id === topic.id && productionReviewStatus.ready_for_production_start
                const produceDisabled = topic.status !== 'approved' || !reviewReady
                return (
                  <div key={topic.id} className="border border-border-default bg-void p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={`border px-2 py-1 font-display text-sm font-semibold ${scoreClass(topic.total_score)}`}>
                            {Math.round(topic.total_score)}
                          </span>
                          <div className="truncate font-display text-[13px] font-semibold uppercase text-text-primary">
                            {topic.title}
                          </div>
                        </div>
                        <div className="mt-2 line-clamp-2 text-xs text-text-secondary">{topic.description || topic.inspired_by}</div>
                      </div>
                      <button
                        type="button"
                        aria-label="Toggle score breakdown"
                        onClick={() => setExpandedTopicId(expanded ? null : topic.id)}
                        className="border border-border-default p-1 text-text-secondary"
                      >
                        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </button>
                    </div>

                    <div className="mt-3 flex flex-wrap gap-1">
                      {topic.flags.evergreen ? <span className="border border-success/40 px-2 py-1 font-mono text-[9px] uppercase text-success">Ponadczasowe</span> : null}
                      {topic.flags.shorts_potential ? <span className="border border-accent-primary/40 px-2 py-1 font-mono text-[9px] uppercase text-accent-primary">Short</span> : null}
                      {topic.flags.clickbait_risk ? <span className="border border-warning/50 px-2 py-1 font-mono text-[9px] uppercase text-warning">Ryzyko clickbait</span> : null}
                      {topic.flags.llm_unavailable ? <span className="border border-error/50 px-2 py-1 font-mono text-[9px] uppercase text-error">LLM offline</span> : null}
                    </div>

                    {expanded ? (
                      <div className="mt-3 space-y-2 border-t border-border-default pt-3">
                        {DIMENSION_LABELS.map(([key, label, max]) => {
                          const value = topic.dimensions[key]
                          return (
                            <div key={key} className="grid grid-cols-[72px_1fr_42px] items-center gap-2 font-mono text-[10px] uppercase text-text-tertiary">
                              <span>{label}</span>
                              <span className="h-1.5 bg-panel">
                                <span className="block h-full bg-accent-primary" style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
                              </span>
                              <span className="text-right text-text-secondary">{value}</span>
                            </div>
                          )
                        })}
                      </div>
                    ) : null}

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <select
                        value={topic.status}
                        onChange={(event) => void changeTopicStatus(topic.id, event.target.value as TopicQueueStatus)}
                        className="border border-border-default bg-panel px-2 py-1 font-mono text-[10px] uppercase text-text-secondary"
                      >
                        <option value="pending">Oczekuje</option>
                        <option value="approved">Zatwierdzony</option>
                        <option value="rejected">Odrzucony</option>
                        <option value="in_production">W produkcji</option>
                        <option value="done">Gotowy</option>
                      </select>
                      <button type="button" onClick={() => void changeTopicStatus(topic.id, 'approved')} className="border border-success/40 px-2 py-1 font-mono text-[10px] uppercase text-success">
                        <Check size={12} className="inline" /> Zatwierdz
                      </button>
                      <button type="button" onClick={() => void changeTopicStatus(topic.id, 'rejected')} className="border border-error/40 px-2 py-1 font-mono text-[10px] uppercase text-error">
                        <X size={12} className="inline" /> Odrzuc
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedProductionReviewTopicId(topic.id)
                          void loadProductionReview(topic)
                        }}
                        className={`border px-2 py-1 font-mono text-[10px] uppercase ${
                          reviewSelected ? 'border-accent-primary/50 text-accent-primary' : 'border-border-default text-text-secondary'
                        }`}
                      >
                        Review
                      </button>
                      <button
                        type="button"
                        disabled={produceDisabled}
                        title={
                          produceDisabled
                            ? 'Produkcja wymaga zatwierdzonego tematu i ukończonego Production review gate.'
                            : 'Production review gate przeszedł dla tego tematu.'
                        }
                        onClick={() => void produceTopic(topic)}
                        className="border border-accent-primary/40 px-2 py-1 font-mono text-[10px] uppercase text-accent-primary disabled:opacity-40"
                      >
                        <Play size={12} className="inline" /> Produkuj
                      </button>
                      <button type="button" onClick={() => void removeTopic(topic.id)} className="ml-auto border border-border-default px-2 py-1 font-mono text-[10px] uppercase text-text-tertiary">
                        <Trash2 size={12} className="inline" /> Usuń
                      </button>
                    </div>
                  </div>
                )
              })}

              {visibleTopics.length === 0 ? (
                <div className="border border-dashed border-border-default p-5 text-sm text-text-secondary">
                  Brak tematów dla wybranego filtra. Wygeneruj nowe tematy albo pokaż niską ocenę.
                </div>
              ) : null}
            </div>
          </Panel>
        </div>
      </div>

      <Panel title="Przychody">
        <div className="border-b border-border-default px-4 py-3 text-xs text-text-secondary">
          Brak realnych danych z YouTube Analytics. Pokazuję zera, dopóki kanał nie ma zebranych wyników.
          Poprzednie kwoty były danymi demo z frontendu, nie rzeczywistym zarobkiem.
        </div>
        <div className="grid gap-4 p-4 xl:grid-cols-[2fr_1fr]">
          <div className="flex h-52 items-end gap-1 border border-border-default bg-void p-3">
            {revenue.map((point) => {
              const total = point.adsense + point.affiliate + point.sponsors
              return (
                <div key={point.date} className="flex h-full flex-1 flex-col justify-end gap-1">
                  <div
                    className="bg-accent-primary/80"
                    style={{ height: total > 0 ? `${Math.max(6, (total / maxRevenue) * 100)}%` : '0%' }}
                    title={`${point.date}: ${total} zł`}
                  />
                  <span className="truncate text-center font-mono text-[9px] text-text-tertiary">{point.date}</span>
                </div>
              )
            })}
          </div>
          <div className="grid grid-cols-2 gap-3">
            {[
              ['RPM', `${stats.rpm} zł`],
              ['CPM', `${stats.cpm} zł`],
              ['Czas oglądania', `${stats.watchTimeHours}h`],
              ['CTR', `${stats.ctr}%`],
            ].map(([label, value]) => (
              <div key={label} className="border border-border-default bg-void p-3">
                <div className="font-mono text-[10px] uppercase text-text-tertiary">{label}</div>
                <div className="mt-1 font-display text-lg text-text-primary">{value}</div>
              </div>
            ))}
          </div>
        </div>
      </Panel>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { AiWorkerTaskEvidence } from '../components/AiWorkerTaskEvidence'
import {
  configureAlpacaPaperAccount,
  configureXtbDemoAccount,
  captureLocalHostScreen,
  configureIntelligenceAccount,
  configureYouTubeOAuthRuntime,
  createAccountCredential,
  delegateAiWorkerPlan,
  delegateAiWorkerTask,
  dispatchLocalHostInput,
  enableSettingsFullAccess,
  ensureProductionStyleGuide,
  fetchAiWorkerTasks,
  fetchAiWorkersStatus,
  fetchAlpacaPaperStatus,
  fetchAccountSecretStorageStatus,
  fetchIntelligenceAccounts,
  fetchInvestmentRiskPolicy,
  fetchLocalHostCapabilities,
  fetchMoneyMachineSummary,
  fetchProductionPublicQualityStatus,
  fetchPortfolioAlphaSettings,
  fetchProductionReadiness,
  fetchRuntimeHealth,
  fetchSettingsControlCenter,
  fetchXtbMarketDataStatus,
  focusLocalHostDesktopApp,
  importYouTubeOAuthClientSecret,
  openLocalHostDesktopApp,
  probeAiWorker,
  probeYouTubeConnector,
  runIntelligenceCollectors,
  runAiWorkerTask,
  startOAuthConnection,
  updatePortfolioAlphaSettings,
  updateInvestmentRiskPolicy,
  updateSettingsPolicy,
  updateSettingsRuntime,
  verifyAlpacaPaperReadOnly,
  verifyXtbReadOnly,
} from '../lib/api'
import type {
  AccountSecretStorageStatus,
  AlpacaPaperStatus,
  AiWorkerTask,
  AiWorkersStatusPayload,
  InvestmentRiskMetrics,
  InvestmentRiskPolicy,
  MoneyMachineOperatorSummaryPayload,
  ProductionPublicQualityStatusPayload,
  PortfolioAlphaSettings,
  ProductionReadinessPayload,
  RuntimeHealthPayload,
  XtbMarketDataStatus,
} from '../lib/api'
import { useAccountStore } from '../stores/accountStore'
import type { Account, AutomationMode } from '../types/contentMachine'
import type { SettingsControlCenterPayload } from '../types/settings'
import type { ContentReadinessItem, LocalHostControlSummary, LocalHostDesktopApp } from '../types/system'
import { QualityCouncilPanel } from '../components/QualityCouncilPanel'
import { MediaObservabilityPanel } from '../components/MediaObservabilityPanel'
import { ReferenceLearningPanel } from '../components/ReferenceLearningPanel'
import { ProviderDoctorPanel } from '../components/ProviderDoctorPanel'

type Tab = 'accounts' | 'pipeline' | 'monetization' | 'automation' | 'runtime' | 'access'
type IntelligenceStatus =
  | 'idle'
  | 'running'
  | 'done'
  | 'partial'
  | 'needs_config'
  | 'limited'
  | 'unavailable'
  | 'error'

type OAuthConnector = SettingsControlCenterPayload['oauthConnectors'][number]

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'accounts', label: 'Konta' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'monetization', label: 'Monetyzacja' },
  { id: 'automation', label: 'Automatyzacja' },
  { id: 'runtime', label: 'Runtime' },
  { id: 'access', label: 'Dostępy' },
]

const STATUS_COPY: Record<IntelligenceStatus, string> = {
  idle: 'GOTOWE',
  running: 'DZIAŁA',
  done: 'OK',
  partial: 'CZĘŚCIOWO',
  needs_config: 'DO KONFIGURACJI',
  limited: 'LIMIT',
  unavailable: 'MODUŁ NIEDOSTĘPNY',
  error: 'BŁĄD',
}

const STATUS_CLASS: Record<IntelligenceStatus, string> = {
  idle: 'text-text-secondary',
  running: 'text-warning',
  done: 'text-success',
  partial: 'text-warning',
  needs_config: 'text-warning',
  limited: 'text-warning',
  unavailable: 'text-warning',
  error: 'text-error',
}

function getIntelligenceStatusCopy(status: IntelligenceStatus) {
  const copy: Record<IntelligenceStatus, string> = {
    idle: 'GOTOWE',
    running: 'DZIAŁA',
    done: 'OK',
    partial: 'CZĘŚCIOWO',
    needs_config: 'DO KONFIGURACJI',
    limited: 'LIMIT',
    unavailable: 'MODUŁ NIEDOSTĘPNY',
    error: 'BŁĄD',
  }
  return copy[status] ?? STATUS_COPY[status]
}

function Field({
  label,
  value,
  onChange,
  type = 'text',
}: {
  label: string
  value: string | number
  onChange: (value: string) => void
  type?: string
}) {
  return (
    <label className="block">
      <span className="font-mono text-[10px] uppercase text-text-tertiary">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full border border-border-default bg-void px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
      />
    </label>
  )
}

function ReadOnlyPipelineField({
  label,
  value,
  source,
}: {
  label: string
  value: string
  source: string
}) {
  return (
    <div className="border border-border-default bg-void p-3">
      <div className="font-mono text-[10px] uppercase text-text-tertiary">{label}</div>
      <div className="mt-2 text-sm text-text-primary">{value}</div>
      <div className="mt-2 font-mono text-[9px] uppercase text-warning">
        Brak odczytu profilu produkcji
      </div>
      <div className="mt-1 break-words font-mono text-[9px] text-text-tertiary">Źródło: {source}</div>
    </div>
  )
}

function TextAreaField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  return (
    <label className="block">
      <span className="font-mono text-[10px] uppercase text-text-tertiary">{label}</span>
      <textarea
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        rows={4}
        className="mt-1 w-full resize-y border border-border-default bg-void px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
      />
    </label>
  )
}

function ToggleField({
  label,
  checked,
  onChange,
  detail,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  detail?: string
}) {
  return (
    <label className="flex items-start justify-between gap-3 border border-border-default bg-void px-3 py-3">
      <span>
        <span className="block font-mono text-[10px] uppercase text-text-secondary">{label}</span>
        {detail ? <span className="mt-1 block text-[11px] leading-5 text-text-tertiary">{detail}</span> : null}
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1"
      />
    </label>
  )
}

function splitLines(value: string | string[] | undefined) {
  if (Array.isArray(value)) return value
  return String(value ?? '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function splitComma(value: string | string[] | undefined) {
  if (Array.isArray(value)) return value
  return String(value ?? '')
    .split(',')
    .map((line) => line.trim())
    .filter(Boolean)
}

function joinLines(value: string[] | undefined) {
  return (value ?? []).join('\n')
}

function mergeLines(value: string | string[] | undefined, nextValue: string) {
  const values = splitLines(value)
  if (!values.includes(nextValue)) values.push(nextValue)
  return joinLines(values)
}

function getXtbQuoteSymbols(status: XtbMarketDataStatus | null) {
  const result = status?.last_check?.result
  const quotes = result && Array.isArray(result.quotes) ? result.quotes : []
  return Array.from(
    new Set(
      quotes
        .map((quote) => {
          if (!quote || typeof quote !== 'object') return ''
          return String((quote as Record<string, unknown>).symbol ?? '').trim().toUpperCase()
        })
        .filter(Boolean),
    ),
  )
}

function getXtbAutopilotPriceStatus(status: XtbMarketDataStatus | null) {
  const quoteSymbols = getXtbQuoteSymbols(status)
  const verifiedQuotes = Boolean(status?.read_only_verified && quoteSymbols.length > 0)
  return {
    quoteSymbols,
    source: verifiedQuotes ? 'xtb_demo_quotes' : 'synthetic_fallback',
    label: verifiedQuotes ? 'XTB demo quotes' : 'Synthetic fallback',
    detail: verifiedQuotes
      ? `Autopilot paper trading użyje ostatnich zweryfikowanych cen dla: ${quoteSymbols.join(', ')}.`
      : 'Autopilot paper trading nie ma jeszcze zweryfikowanych quote’ów XTB, więc użyje cen syntetycznych.',
    className: verifiedQuotes ? 'text-success' : 'text-warning',
  }
}

function translateCollectorMessage(message: string) {
  if (!message.trim()) return ''
  if (/not found|\{"detail"\s*:\s*"not found"\}|endpoint api niedost[eę]pny/i.test(message)) {
    return 'Moduł Intelligence nie odpowiada w aktualnie uruchomionym backendzie. Najczęściej oznacza to starą wersję runtime albo brak restartu Atnosa po aktualizacji.'
  }
  if (/connect youtube studio/i.test(message)) {
    return 'Podłącz konto YouTube Studio/OAuth, żeby zbierać dane z YouTube API. Bez tego Atnos nie pobierze trendów YouTube, konkurencji, wzorców ani danych własnego kanału.'
  }
  if (/oauth|credential|client secret|client_secrets|not authenticated|unauthorized/i.test(message)) {
    return 'Brakuje konfiguracji OAuth/YouTube. To nie jest awaria aplikacji, tylko blokada konfiguracji dostępu do danych kanału.'
  }
  if (/rate_limited|too many requests|429/i.test(message)) {
    return 'Google Trends chwilowo ograniczył zapytania. To nie jest błąd aplikacji. Spróbuj później albo oprzyj scoring na danych YouTube po podłączeniu OAuth.'
  }
  if (/quota/i.test(message)) {
    return 'Limit API został wykorzystany. Atnos odłoży kolejne pobranie do następnego okna limitu.'
  }
  if (/failed to fetch|networkerror|nie udalo sie polaczyc|nie udało się połączyć/i.test(message)) {
    return 'Nie udało się połączyć z lokalnym backendem Atnosa. Sprawdź, czy backend działa.'
  }
  return message
}

function isYoutubeConfigMessage(message: string) {
  return /connect youtube studio|oauth|credential|client secret|client_secrets|not authenticated|unauthorized/i.test(message)
}

function isRateLimitMessage(message: string) {
  return /rate_limited|too many requests|429|quota/i.test(message)
}

function isEndpointUnavailableMessage(message: string) {
  return /not found|\{"detail"\s*:\s*"not found"\}|endpoint api niedost[eę]pny/i.test(message)
}

function normalizeStatus(raw: unknown): IntelligenceStatus {
  const status = String(raw ?? '')
  if (
    status === 'done' ||
    status === 'partial' ||
    status === 'needs_config' ||
    status === 'limited' ||
    status === 'unavailable' ||
    status === 'error'
  ) {
    return status
  }
  return 'idle'
}

function collectHasStatus(value: unknown, statuses: string[]): boolean {
  if (Array.isArray(value)) {
    return value.some((item) => collectHasStatus(item, statuses))
  }
  if (!value || typeof value !== 'object') {
    return false
  }
  const record = value as Record<string, unknown>
  const status = String(record.status ?? '').toLowerCase()
  const message = String(record.message ?? '')
  if (statuses.includes(status)) {
    return true
  }
  if (statuses.includes('needs_config') && isYoutubeConfigMessage(message)) {
    return true
  }
  if (statuses.includes('limited') && isRateLimitMessage(message)) {
    return true
  }
  if (statuses.includes('unavailable') && isEndpointUnavailableMessage(message)) {
    return true
  }
  return Object.values(record).some((item) => collectHasStatus(item, statuses))
}

function collectorHasError(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some(collectorHasError)
  }
  if (!value || typeof value !== 'object') {
    return false
  }
  const record = value as Record<string, unknown>
  const status = String(record.status ?? '').toLowerCase()
  if (
    status === 'error' ||
    status === 'quota_exhausted' ||
    status === 'rate_limited' ||
    status === 'needs_config' ||
    status === 'retry_scheduled' ||
    status === 'unavailable'
  ) {
    return true
  }
  return Object.values(record).some(collectorHasError)
}

function firstCollectorMessage(value: unknown): string | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      const message = firstCollectorMessage(item)
      if (message) return message
    }
    return null
  }
  if (!value || typeof value !== 'object') {
    return null
  }
  const record = value as Record<string, unknown>
  if (typeof record.message === 'string' && record.message.trim()) {
    return translateCollectorMessage(record.message)
  }
  for (const item of Object.values(record)) {
    const message = firstCollectorMessage(item)
    if (message) return message
  }
  return null
}

function collectorSucceeded(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some(collectorSucceeded)
  }
  if (!value || typeof value !== 'object') {
    return false
  }
  const record = value as Record<string, unknown>
  const status = String(record.status ?? '').toLowerCase()
  if (status === 'ok' || status === 'collected' || status === 'completed' || status === 'cached') {
    return true
  }
  if (status === 'error' || status === 'quota_exhausted' || status === 'rate_limited' || status === 'needs_config') {
    return false
  }
  if ('inserted' in record || 'updated' in record || 'stored' in record || 'items' in record) {
    return !collectorHasError(record)
  }
  return Object.values(record).some(collectorSucceeded)
}

function readCollector(result: Record<string, unknown>, key: string) {
  const nested = result.results && typeof result.results === 'object'
    ? (result.results as Record<string, unknown>)
    : result
  return nested[key]
}

type ProjectRequirement = {
  label: string
  ok: boolean
  detail: string
}

type ProjectConnectionStatus = 'ok' | 'missing' | 'limited' | 'blocked'

type ProjectConnectionItem = {
  label: string
  status: ProjectConnectionStatus
  detail: string
  nextAction: string
  source: string
}

type ProjectConnectionEvidence = {
  controlCenter: SettingsControlCenterPayload | null
  xtbStatus: XtbMarketDataStatus | null
  riskPolicy: InvestmentRiskPolicy | null
  contentReadiness: ContentReadinessItem | null
  snapshotError: string | null
}

type ConnectorSnapshot = {
  status: ProjectConnectionStatus
  ready: boolean
  detail: string
  source: string
}

const PROJECT_TYPE_COPY: Record<Account['type'], string> = {
  youtube: 'YouTube Shorts',
  instagram_reels: 'Instagram Reels',
  tiktok: 'TikTok',
  onlyfans: 'Synthetic creator',
  investment: 'Portfel inwestycyjny',
  custom: 'Custom',
}

function hasText(value: unknown): boolean {
  return String(value ?? '').trim().length > 0
}

function hasAnyText(...values: unknown[]): boolean {
  return values.some(hasText)
}

function connectionStatusTone(status: ProjectConnectionStatus) {
  if (status === 'ok') return 'border-success/40 bg-success/10 text-success'
  if (status === 'blocked') return 'border-error/40 bg-error/10 text-error'
  if (status === 'limited') return 'border-warning/40 bg-warning/10 text-warning'
  return 'border-border-default bg-panel text-text-tertiary'
}

function connectionStatusCopy(status: ProjectConnectionStatus) {
  const copy: Record<ProjectConnectionStatus, string> = {
    ok: 'OK',
    missing: 'BRAK',
    limited: 'LIMIT',
    blocked: 'BLOKADA',
  }
  return copy[status]
}

export type YoutubeIntegrationCode = 'LIVE' | 'REQUIRES_SETUP' | 'DEGRADED' | 'ERROR' | 'RUNNING'

export function experimentResultCopy(result: { winner?: string | null; status?: string }): string {
  if (!result.winner) return 'Inconclusive — insufficient observed metrics.'
  return 'Waiting for real performance data is over. Review the experiment evidence before treating this as a rule.'
}

export function youtubeIntegrationPresentation(input: {
  oauth: 'CONNECTED' | 'EXPIRED' | 'MISSING' | 'INSUFFICIENT_SCOPE' | 'ERROR' | 'TOKEN_REVOKED' | 'AWAITING_USER_AUTHORIZATION'
  analytics?: 'available' | 'unavailable' | 'unknown'
  comments?: 'available' | 'unavailable' | 'unknown'
  syncing?: boolean
}): { status: YoutubeIntegrationCode; detail: string } {
  if (input.syncing) {
    return { status: 'RUNNING', detail: 'Synchronizacja YouTube jest w toku.' }
  }
  if (input.oauth === 'MISSING') {
    return {
      status: 'REQUIRES_SETUP',
      detail: 'YouTube is not connected. Analytics and comments are unavailable until OAuth setup is completed.',
    }
  }
  if (input.oauth === 'EXPIRED' || input.oauth === 'TOKEN_REVOKED') {
    return {
      status: 'REQUIRES_SETUP',
      detail: 'YouTube connection expired. Reconnect to restore analytics and comments.',
    }
  }
  if (input.oauth === 'AWAITING_USER_AUTHORIZATION') {
    return { status: 'RUNNING', detail: 'Waiting for YouTube authorization…' }
  }
  if (input.oauth === 'INSUFFICIENT_SCOPE') {
    return {
      status: 'REQUIRES_SETUP',
      detail: 'YouTube jest połączone, ale brakuje uprawnień do analityki albo komentarzy.',
    }
  }
  if (input.oauth === 'ERROR') {
    return {
      status: 'ERROR',
      detail: 'Połączenie YouTube jest zapisane, ale odczyt kanału się nie udał. Spróbuj ponownie.',
    }
  }
  if (input.analytics === 'available' && input.comments === 'available') {
    return { status: 'LIVE', detail: 'YouTube — LIVE. Analytics: available. Comments: available.' }
  }
  if (input.analytics === 'available' && input.comments !== 'available') {
    return { status: 'DEGRADED', detail: 'Dane filmu są dostępne, ale komentarze nie działają.' }
  }
  if (input.comments === 'available' && input.analytics !== 'available') {
    return { status: 'DEGRADED', detail: 'Video data is available, but Analytics API access is missing.' }
  }
  return { status: 'DEGRADED', detail: 'Kanał jest połączony, ale analityka i komentarze nie zostały potwierdzone.' }
}

function youtubeContentStatus(
  controlCenter: SettingsControlCenterPayload | null,
  channel: ContentReadinessItem | null,
): ConnectorSnapshot {
  const source = '/settings/control-center trustedAuto.contentReadiness'

  if (!controlCenter) {
    return {
      status: 'limited',
      ready: false,
      detail: 'Backend snapshot niedostepny. Nie mozna potwierdzic OAuth tego kanalu.',
      source: 'fallback: brak snapshotu /settings/control-center',
    }
  }

  if (!channel) {
    return {
      status: 'missing',
      ready: false,
      detail: 'Backend nie ma mapowania OAuth dla tego kanalu.',
      source,
    }
  }

  if (channel.connected) {
    return {
      status: 'ok',
      ready: true,
      detail: channel.accountId
        ? `YouTube OAuth potwierdzony dla tego kanalu. Connection ID: ${channel.accountId}.`
        : 'YouTube OAuth potwierdzony dla tego kanalu.',
      source,
    }
  }

  const expired = channel.blockingReasons.some((reason) => /wygas|expir|uniewa[zż]/i.test(reason))
  const presented = youtubeIntegrationPresentation({ oauth: expired ? 'EXPIRED' : 'MISSING' })
  return {
    status: 'missing',
    ready: false,
    detail: presented.detail,
    source,
  }
}

function projectConnectionItems(
  account: Account,
  evidence: ProjectConnectionEvidence = { controlCenter: null, xtbStatus: null, riskPolicy: null, contentReadiness: null, snapshotError: null },
): ProjectConnectionItem[] {
  if (account.type === 'investment') {
    const hasBroker = hasAnyText(account.platformConfig.brokerPortalUrl, account.platformConfig.brokerName)
    const hasVault = hasText(account.platformConfig.brokerCredentialVaultRef)
    const hasMarketData = hasAnyText(
      account.platformConfig.marketDataSources,
      account.platformConfig.dataSources,
      account.platformConfig.brokerReadAccess === 'true' ? 'broker-read-access' : '',
    )
    const hasBudget = hasAnyText(account.platformConfig.paymentMethodRef, account.platformConfig.monthlySpendBudgetPln)
    const xtbSource = evidence.xtbStatus
      ? '/api/investment/market-data/xtb-demo/status'
      : evidence.snapshotError
        ? `fallback: backend status niedostepny (${evidence.snapshotError})`
        : 'fallback: lokalna konfiguracja projektu'
    const brokerReady = Boolean(evidence.xtbStatus?.registered) || hasBroker
    const vaultReady = Boolean(evidence.xtbStatus?.has_credential_ref) || hasVault
    const marketDataReady = Boolean(evidence.xtbStatus?.read_only_verified)
    const marketDataLimited = Boolean(evidence.xtbStatus?.registered) || hasMarketData
    const realTradingEnabled = Boolean(evidence.xtbStatus?.real_trading_enabled)
    const riskPolicyConfigured = Boolean(evidence.riskPolicy?.configured)
    const riskPolicySource = evidence.riskPolicy
      ? '/api/investment/risk-policy'
      : evidence.snapshotError
        ? `fallback: backend status niedostepny (${evidence.snapshotError})`
        : 'brak potwierdzenia z backendu'
    return [
      {
        label: 'Broker demo/read-only',
        status: brokerReady ? 'ok' : 'missing',
        detail: evidence.xtbStatus
          ? `Backend widzi XTB demo/read-only: ${evidence.xtbStatus.broker_status || 'status nieznany'}.`
          : 'URL brokera, nazwa platformy i tryb demo/paper. Ten projekt nie uzywa YouTube OAuth.',
        nextAction: brokerReady ? 'Zweryfikuj read-only XTB w sekcji portfela.' : 'Dodaj URL XTB i nazwe brokera.',
        source: xtbSource,
      },
      {
        label: 'Vault brokera',
        status: vaultReady ? 'ok' : 'missing',
        detail: evidence.xtbStatus
          ? `Backend ${evidence.xtbStatus.has_credential_ref ? 'ma' : 'nie ma'} referencje do credential vault.`
          : 'Referencja do lokalnego vaulta z loginem/haslem. Hasla nie zapisujemy w UI ani localStorage.',
        nextAction: vaultReady ? 'Mozesz testowac read-only.' : 'Dodaj tylko vault ref, nie wklejaj hasla.',
        source: xtbSource,
      },
      {
        label: 'Market data / XTB',
        status: marketDataReady ? 'ok' : marketDataLimited ? 'limited' : 'missing',
        detail: evidence.xtbStatus
          ? `Read-only verified: ${evidence.xtbStatus.read_only_verified ? 'tak' : 'nie'}. Tryb: ${evidence.xtbStatus.environment}.`
          : 'Zrodla cen, watchlista i ostatnie quote-y do paper tradingu.',
        nextAction: marketDataReady
          ? 'Uruchom paper autopilot dopiero po sprawdzeniu cen.'
          : 'Zweryfikuj XTB read-only albo dodaj zrodla cen.',
        source: xtbSource,
      },
      {
        label: 'Kursy i strategia',
        status: hasText(account.platformConfig.learningResources) ? 'ok' : 'missing',
        detail: 'Materialy edukacyjne, playbooki i zasady, z ktorych Atnos ma budowac decyzje.',
        nextAction: hasText(account.platformConfig.learningResources) ? 'Uzupelniaj po kazdej sesji nauki.' : 'Dodaj kursy, linki i notatki strategiczne.',
        source: 'localStorage: platformConfig.learningResources',
      },
      {
        label: 'Limity ryzyka',
        status: riskPolicyConfigured ? 'ok' : evidence.riskPolicy ? 'limited' : 'missing',
        detail: evidence.riskPolicy
          ? `Backend: ${evidence.riskPolicy.mode}; emergency stop: ${evidence.riskPolicy.emergency_stop ? 'aktywny' : 'wylaczony'}.`
          : 'Brak odczytu egzekwowanych limitow z backendu.',
        nextAction: riskPolicyConfigured ? 'Limity sa aktywnie egzekwowane dla paper tradingu.' : 'Ustaw limity w sekcji Portfolio Alpha i zapisz je do backendu.',
        source: riskPolicySource,
      },
      {
        label: 'Budzet i platnosci',
        status: hasBudget ? 'limited' : 'missing',
        detail: 'Tylko limit i referencja do metody platnosci. Pelnych danych karty nie zapisujemy.',
        nextAction: hasBudget ? 'Traktuj jako limit kosztow narzedzi, nie zgode na zakupy.' : 'Dodaj limit miesieczny i opcjonalny payment vault ref.',
        source: 'localStorage: paymentMethodRef/monthlySpendBudgetPln',
      },
      {
        label: 'Live trading',
        status: 'blocked',
        detail: realTradingEnabled
          ? 'Backend raportuje mozliwy live trading, ale UI nadal blokuje realne zlecenia do recznej zgody i limitow.'
          : 'Realne zlecenia sa celowo zablokowane do paper tradingu, limitow ryzyka i recznej zgody.',
        nextAction: 'Nie odblokowywac automatycznie. Najpierw paper trading i audyt wynikow.',
        source: evidence.xtbStatus ? xtbSource : 'guardrail UI: live trading blocked',
      },
    ]
  }

  if (account.type === 'youtube') {
    const hasSignals = hasAnyText(account.platformConfig.niches, account.niche)
    const hasReferences = hasAnyText(account.platformConfig.referenceChannels, account.platformConfig.competitorChannels)
    const youtube = youtubeContentStatus(evidence.controlCenter, evidence.contentReadiness)
    const gmailSubject = evidence.controlCenter?.accessReality?.subjects.find(
      (subject) => subject.connectorKey === 'gmail',
    )
    const gmailReady = gmailSubject?.accessLevel === 'live_read' || gmailSubject?.accessLevel === 'live_write'
    const gmail: ConnectorSnapshot = {
      status: gmailReady ? 'ok' : evidence.controlCenter ? 'missing' : 'limited',
      ready: gmailReady,
      detail: gmailReady
        ? 'Gmail ma potwierdzony dostep live w backendzie.'
        : 'Brak potwierdzonej live sesji Gmail OAuth w tescie dostepow.',
      source: evidence.controlCenter
        ? '/settings/control-center accessReality'
        : 'fallback: brak snapshotu /settings/control-center',
    }
    const youtubeReady = youtube.ready
    return [
      {
        label: 'YouTube Studio OAuth',
        status: youtube.status,
        detail: youtube.detail,
        nextAction: youtubeReady ? 'Testuj upload prywatny i analytics.' : 'Polacz oficjalny connector YouTube OAuth.',
        source: youtube.source,
      },
      {
        label: 'Gmail feedback',
        status: gmail.status,
        detail: gmail.ready ? gmail.detail : 'Gmail potrzebny do raportow, linkow prywatnych probek i review uzytkownika.',
        nextAction: gmail.ready ? 'Mozesz wysylac raporty z prywatnymi probkami.' : 'Polacz Gmail OAuth dla konta wlasciciela.',
        source: gmail.source,
      },
      {
        label: 'Nisze i sygnaly',
        status: hasSignals ? 'ok' : 'missing',
        detail: 'Slowa kluczowe dla trendow, researchu i scoringu tematow.',
        nextAction: hasSignals ? 'Uruchom zbieranie danych.' : 'Dodaj 3-10 nisz/keywordow.',
        source: 'localStorage: platformConfig.niches/account.niche',
      },
      {
        label: 'Kanaly referencyjne',
        status: hasReferences ? 'ok' : 'missing',
        detail: 'Inspiracje, konkurencja i wzorce jakosci dla style guide.',
        nextAction: hasReferences ? 'Uruchom edukacje i quality audit.' : 'Dodaj URL-e kanalow po jednym na linie.',
        source: 'localStorage: referenceChannels/competitorChannels',
      },
      {
        label: 'Prywatny upload',
        status:
          youtubeReady && (account.automationMode === 'auto_private' || account.automationMode === 'auto_public')
            ? 'limited'
            : 'missing',
        detail: 'Dozwolony dopiero po QA i wybraniu joba gotowego do prywatnego uploadu.',
        nextAction: 'Publiczny upload zostaje za quality gate i osobna zgoda.',
        source: `${youtube.source} + localStorage: automationMode`,
      },
    ]
  }

  if (account.type === 'instagram_reels' || account.type === 'tiktok') {
    return [
      {
        label: 'Sesja browserowa',
        status: hasAnyText(account.platformConfig.accountUrl, account.platformConfig.browserSessionService) ? 'limited' : 'missing',
        detail: 'Ten projekt wymaga zaufanej sesji browserowej platformy, nie YouTube OAuth.',
        nextAction: 'Dodaj URL konta i nazwe sesji browserowej.',
        source: 'localStorage: accountUrl/browserSessionService',
      },
      {
        label: 'Zasady publikacji',
        status: hasText(account.platformConfig.publishPolicy) ? 'ok' : 'missing',
        detail: 'Warunki QA, prywatnego draftu i recznej akceptacji.',
        nextAction: 'Ustal, co Atnos moze zrobic sam, a co tylko po zgodzie.',
        source: 'localStorage: publishPolicy',
      },
    ]
  }

  if (account.type === 'onlyfans') {
    return [
      {
        label: 'Konto tworcy',
        status: account.platformConfig.creatorAccountConnected === 'true' ? 'ok' : 'missing',
        source: 'localStorage: creatorAccountConnected',
        detail: 'Zaufana sesja lub connector platformy. Bez podszywania sie pod realne osoby.',
        nextAction: 'Podlacz sesje tylko przez kontrolowany moduł i guardrails.',
      },
      {
        label: 'Vault assetow',
        status: account.platformConfig.vaultConnected === 'true' ? 'ok' : 'missing',
        source: 'localStorage: vaultConnected',
        detail: 'Zatwierdzone assety, style, prompty, licencje i disclosure.',
        nextAction: 'Dodaj vault assetow przed produkcja.',
      },
      {
        label: 'Polityka tresci',
        status: hasText(account.platformConfig.contentPolicy) ? 'ok' : 'missing',
        source: 'localStorage: contentPolicy',
        detail: 'Twarde granice tresci, wieku, publikacji i platnosci.',
        nextAction: 'Wpisz zasady przed generowaniem draftow.',
      },
    ]
  }

  return [
    {
      label: 'API / klucze projektu',
      status: hasText(account.platformConfig.requiredAccess) ? 'limited' : 'missing',
      source: 'localStorage: requiredAccess',
      detail: 'Lista potrzebnych API, kont, sesji i vault refow dla projektu custom.',
      nextAction: 'Dodaj wymagane dostepy bez wklejania sekretow w plaintext.',
    },
    {
      label: 'Pliki i kontekst',
      status: hasText(account.platformConfig.projectNotes) ? 'ok' : 'missing',
      source: 'localStorage: projectNotes',
      detail: 'Brief, pliki, notatki i zasady pracy dla Atnosa.',
      nextAction: 'Dodaj opis celu i materialy projektu.',
    },
  ]
}

function projectRequirements(
  account: Account,
  evidence: ProjectConnectionEvidence = { controlCenter: null, xtbStatus: null, riskPolicy: null, contentReadiness: null, snapshotError: null },
): ProjectRequirement[] {
  if (account.type === 'youtube') {
    return [
      {
        label: 'Kanał',
        ok: hasText(account.platformConfig.channelId),
        detail: 'ID kanału lub mapowanie konta YouTube w projekcie.',
      },
      {
        label: 'Nisze',
        ok: hasAnyText(account.platformConfig.niches, account.niche),
        detail: 'Słowa kluczowe do trendów, scoringu i tematów.',
      },
      {
        label: 'Wzorce',
        ok: hasAnyText(account.platformConfig.referenceChannels, account.platformConfig.competitorChannels),
        detail: 'Kanały referencyjne lub konkurencja do analizy jakości.',
      },
      {
        label: 'OAuth',
        ok: Boolean(evidence.contentReadiness?.connected),
        detail: evidence.contentReadiness?.connected
          ? 'OAuth potwierdzony w backendzie dla tego kanału.'
          : 'OAuth nie jest potwierdzony dla tego kanału. Połącz go ponownie i uruchom probe.',
      },
    ]
  }

  if (account.type === 'instagram_reels' || account.type === 'tiktok') {
    return [
      {
        label: 'Konto',
        ok: hasAnyText(account.platformConfig.accountUrl, account.platformConfig.browserSessionService),
        detail: 'URL konta albo zapisana sesja browserowa platformy.',
      },
      {
        label: 'Nisza',
        ok: hasText(account.niche),
        detail: 'Tematy i odbiorca dla krótkich formatów.',
      },
      {
        label: 'Publikacja',
        ok: account.automationMode !== 'manual' && account.status === 'live',
        detail: 'Automatyczny upload tylko po sesji, QA i zasadach akceptacji.',
      },
      {
        label: 'Harmonogram',
        ok: account.schedule.postsPerDay > 0,
        detail: 'Liczba publikacji dziennie i godzina UTC.',
      },
    ]
  }

  if (account.type === 'onlyfans') {
    return [
      {
        label: 'Konto twórcy',
        ok: account.platformConfig.creatorAccountConnected === 'true',
        detail: 'Połączenie z platformą twórcy lub zaufana sesja browserowa.',
      },
      {
        label: 'Vault assetów',
        ok: account.platformConfig.vaultConnected === 'true',
        detail: 'Miejsce na zdjęcia, wideo, prompty, style i materiały referencyjne.',
      },
      {
        label: 'Polityka treści',
        ok: hasText(account.platformConfig.contentPolicy),
        detail: 'Twarde zasady bezpieczeństwa, wieku, publikacji i zakazanych treści.',
      },
      {
        label: 'Płatności',
        ok: account.platformConfig.paymentRulesConfigured === 'true',
        detail: 'Limity wydatków, sprzedaży i brak auto-zakupów bez zgody.',
      },
    ]
  }

  if (account.type === 'investment') {
    return [
      {
        label: 'Broker / XTB',
        ok: hasAnyText(account.platformConfig.brokerPortalUrl, account.platformConfig.brokerName),
        detail: 'URL brokera i nazwa platformy inwestycyjnej.',
      },
      {
        label: 'Vault loginu',
        ok: hasText(account.platformConfig.brokerCredentialVaultRef),
        detail: 'Referencja do lokalnego vaulta z loginem/hasłem, bez plaintext w UI.',
      },
      {
        label: 'Dane rynku',
        ok: hasAnyText(account.platformConfig.dataSources, account.platformConfig.marketDataSources),
        detail: 'Źródła cen, watchlisty, kalendarz wyników i alerty.',
      },
      {
        label: 'Edukacja Atnosa',
        ok: hasText(account.platformConfig.learningResources),
        detail: 'Kursy i materiały, z których Atnos ma budować trading playbook.',
      },
      {
        label: 'Limity ryzyka',
        ok: Boolean(evidence.riskPolicy?.configured),
        detail: evidence.riskPolicy?.configured
          ? 'Twarde limity sa zapisane i egzekwowane przez backend paper tradingu.'
          : 'Brak potwierdzonej konfiguracji limitow ryzyka w backendzie.',
      },
      {
        label: 'Budżet / karta',
        ok: hasAnyText(account.platformConfig.paymentMethodRef, account.platformConfig.monthlySpendBudgetPln),
        detail: 'Limit wydatków i referencja do metody płatności, bez numeru karty w UI.',
      },
    ]
  }

  return [
    {
      label: 'Cel',
      ok: hasText(account.niche),
      detail: 'Opis projektu i oczekiwanego rezultatu.',
    },
    {
      label: 'Dostępy',
      ok: hasText(account.platformConfig.requiredAccess),
      detail: 'Lista wymaganych kont, API albo sesji browserowych.',
    },
    {
      label: 'Harmonogram',
      ok: account.schedule.days.length > 0,
      detail: 'Dni pracy i domyślna godzina.',
    },
    {
      label: 'Tryb',
      ok: account.automationMode !== 'manual',
      detail: 'Manual, auto-private albo auto-public.',
    },
  ]
}

function projectReadinessSummary(account: Account, evidence?: ProjectConnectionEvidence) {
  const requirements = projectRequirements(account, evidence)
  const ready = requirements.filter((item) => item.ok).length
  const missing = requirements.length - ready
  const score = Math.round((ready / requirements.length) * 100)
  return { requirements, ready, missing, score }
}

function projectReadinessTone(score: number, missing: number) {
  if (missing === 0) return 'border-success/40 bg-success/10 text-success'
  if (score >= 50) return 'border-warning/40 bg-warning/10 text-warning'
  return 'border-error/40 bg-error/10 text-error'
}

function projectNextAction(account: Account, requirements: ProjectRequirement[]) {
  const missing = requirements.find((item) => !item.ok)
  if (!missing) {
    if (account.type === 'investment') {
      return 'Gotowe do researchu, zbierania danych rynku i paper tradingu. Live trading nadal wymaga osobnej, ręcznej zgody oraz limitów ryzyka.'
    }
    if (account.type === 'youtube') {
      return 'Gotowe do zbierania danych, scoringu tematów i prywatnych renderów. Publiczny upload nadal przechodzi przez quality gate.'
    }
    return 'Projekt ma komplet podstawowych danych. Możesz uruchamiać workflow właściwy dla tego typu konta.'
  }

  if (account.type === 'investment') {
    const actions: Record<string, string> = {
      'Broker / XTB': 'Dodaj URL brokera, login konta demo i nazwę brokera w sekcji Portfolio inwestycyjny.',
      'Vault loginu': 'Dodaj referencję do lokalnego vaulta. Nie zapisuj hasła XTB w polu tekstowym ani localStorage.',
      'Dane rynku': 'Dodaj źródła market-data, watchlistę i kliknij sprawdzenie XTB read-only.',
      'Edukacja Atnosa': 'Dodaj kursy, playbooki, linki do materiałów i zasady, z których Atnos ma budować strategię.',
      'Limity ryzyka': 'Wpisz twardą politykę ryzyka: max strata dzienna, max ekspozycja, instrumenty dozwolone i zakazane.',
      'Budżet / karta': 'Dodaj tylko referencję do metody płatności i miesięczny limit. Nie zapisuj pełnego numeru karty.',
    }
    return actions[missing.label] ?? missing.detail
  }

  if (account.type === 'youtube') {
    const actions: Record<string, string> = {
      'Kanał': 'Dodaj ID kanału, handle albo upewnij się, że OAuth YouTube jest połączony i odśwież status.',
      'Nisze': 'Dodaj nisze i słowa kluczowe. Bez nich Atnos nie wie, jakie trendy i konkurencję zbierać.',
      'Wzorce': 'Dodaj kanały referencyjne oraz konkurencję. To zasila edukację Atnosa i style guide.',
      'OAuth': 'Połącz YouTube OAuth w karcie poniżej, a potem kliknij test połączenia.',
    }
    return actions[missing.label] ?? missing.detail
  }

  return missing.detail
}

function ProjectSpecificAccessPanel({ account }: { account: Account }) {
  const [connectionEvidence, setConnectionEvidence] = useState<ProjectConnectionEvidence>({
    controlCenter: null,
    xtbStatus: null,
    riskPolicy: null,
    contentReadiness: null,
    snapshotError: null,
  })
  const [connectionSnapshotLoading, setConnectionSnapshotLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    const errorMessage = (error: unknown) =>
      error instanceof Error ? error.message : String(error || 'unknown error')

    setConnectionSnapshotLoading(true)
    void Promise.allSettled([
      fetchSettingsControlCenter(),
      account.type === 'investment' ? fetchXtbMarketDataStatus() : Promise.resolve(null),
      account.type === 'investment' ? fetchInvestmentRiskPolicy() : Promise.resolve(null),
    ])
      .then(([controlCenterResult, xtbResult, riskPolicyResult]) => {
        if (cancelled) return

        const errors: string[] = []
        const nextEvidence: ProjectConnectionEvidence = {
          controlCenter: null,
          xtbStatus: null,
          riskPolicy: null,
          contentReadiness: null,
          snapshotError: null,
        }

        if (controlCenterResult.status === 'fulfilled') {
          nextEvidence.controlCenter = controlCenterResult.value
          if (account.type === 'youtube') {
            nextEvidence.contentReadiness = contentReadinessForAccount(
              controlCenterResult.value,
              contentBackendAccountId(account),
            )
          }
        } else {
          errors.push(`/settings/control-center: ${errorMessage(controlCenterResult.reason)}`)
        }

        if (account.type === 'investment') {
          if (xtbResult.status === 'fulfilled' && xtbResult.value) {
            nextEvidence.xtbStatus = xtbResult.value
          } else if (xtbResult.status === 'rejected') {
            errors.push(`/api/investment/market-data/xtb-demo/status: ${errorMessage(xtbResult.reason)}`)
          }
          if (riskPolicyResult.status === 'fulfilled' && riskPolicyResult.value) {
            nextEvidence.riskPolicy = riskPolicyResult.value.policy
          } else if (riskPolicyResult.status === 'rejected') {
            errors.push(`/api/investment/risk-policy: ${errorMessage(riskPolicyResult.reason)}`)
          }
        }

        nextEvidence.snapshotError = errors.length > 0 ? errors.join(' | ') : null
        setConnectionEvidence(nextEvidence)
      })
      .finally(() => {
        if (!cancelled) setConnectionSnapshotLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [account.id, account.type, account.platformConfig.channelId])

  const summary = projectReadinessSummary(account, connectionEvidence)
  const tone = projectReadinessTone(summary.score, summary.missing)
  const nextAction = projectNextAction(account, summary.requirements)
  const connectionItems = projectConnectionItems(account, connectionEvidence)
  const title =
    account.type === 'investment'
      ? 'Centrum podłączeń: Portfolio Alpha'
      : account.type === 'youtube'
        ? 'Centrum podłączeń: kanał YouTube'
        : `Centrum podłączeń: ${PROJECT_TYPE_COPY[account.type]}`
  const description =
    account.type === 'investment'
      ? 'Ten projekt używa ustawień inwestycyjnych: broker demo/read-only, dane rynku, edukacja Atnosa, limity ryzyka i budżet. YouTube OAuth jest celowo ukryty, bo nie pomaga w tradingu.'
      : account.type === 'youtube'
        ? 'Ten projekt używa ustawień contentowych: YouTube OAuth, nisze, kanały referencyjne, konkurencja, dane Intelligence i quality gate przed uploadem.'
        : 'Ten panel pokazuje tylko dostępy potrzebne dla aktywnego typu projektu. Globalne konektory są niżej w zakładce Dostępy.'

  return (
    <section className="border border-accent-primary/30 bg-accent-primary/5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">
            {title}
          </div>
          <p className="mt-2 max-w-4xl text-sm leading-6 text-text-secondary">{description}</p>
        </div>
        <span className={`border px-3 py-2 font-mono text-[10px] uppercase ${tone}`}>
          {summary.score}% gotowe
        </span>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        {summary.requirements.map((item) => (
          <div key={item.label} className="border border-border-default bg-void p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="font-mono text-[10px] uppercase text-text-tertiary">{item.label}</div>
              <span className={['font-mono text-[9px] uppercase', item.ok ? 'text-success' : 'text-warning'].join(' ')}>
                {item.ok ? 'OK' : 'BRAK'}
              </span>
            </div>
            <div className="mt-2 text-xs leading-5 text-text-secondary">{item.detail}</div>
          </div>
        ))}
      </div>

      <div className="mt-4 border border-border-default bg-void p-4">
        <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">
          Do podłączenia w tym projekcie
        </div>
        <p className="mt-2 max-w-4xl text-xs leading-5 text-text-secondary">
          Lista zależy od typu aktywnego projektu. Atnos nie pokazuje tu konektorów,
          które nie mają sensu dla wybranego workflow.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-[10px] uppercase text-text-tertiary">
          <span>Zrodla statusow: backend snapshot + jawny fallback lokalny</span>
          {connectionSnapshotLoading ? <span className="text-warning">odswiezam backend...</span> : null}
          {connectionEvidence.snapshotError ? (
            <span className="text-warning">fallback aktywny: {connectionEvidence.snapshotError}</span>
          ) : null}
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
          {connectionItems.map((item) => (
            <div key={item.label} className="border border-border-default bg-panel p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="font-display text-[11px] uppercase tracking-[0.12em] text-text-primary">
                  {item.label}
                </div>
                <span
                  className={[
                    'shrink-0 border px-2 py-1 font-mono text-[9px] uppercase',
                    connectionStatusTone(item.status),
                  ].join(' ')}
                >
                  {connectionStatusCopy(item.status)}
                </span>
              </div>
              <div className="mt-2 text-xs leading-5 text-text-secondary">{item.detail}</div>
              <div className="mt-2 font-mono text-[10px] uppercase text-text-tertiary">Zrodlo: {item.source}</div>
              <div className="mt-2 border-t border-border-default pt-2 text-xs leading-5 text-warning">
                {item.nextAction}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 border border-border-default bg-void p-3">
        <div className="font-mono text-[10px] uppercase text-text-tertiary">Następny krok</div>
        <div className="mt-2 text-sm leading-6 text-text-primary">{nextAction}</div>
      </div>

      {account.type === 'investment' ? (
        <div className="mt-3 border border-error/40 bg-error/10 p-3 font-mono text-[11px] leading-5 text-error">
          Guardrail: Atnos nie zapisuje haseł ani pełnych danych karty w projekcie. Używaj tylko referencji do vaulta.
          Live trading pozostaje zablokowany do czasu paper tradingu, limitów ryzyka i ręcznej zgody.
        </div>
      ) : null}
    </section>
  )
}

function ProjectReadinessBoard({ accounts, activeAccountId }: { accounts: Account[]; activeAccountId: string }) {
  return (
    <div className="border border-border-default bg-void p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">
            Macierz konfiguracji lokalnej
          </div>
          <p className="mt-2 max-w-4xl text-sm leading-6 text-text-secondary">
            Ten widok pokazuje kompletność pól zapisanych lokalnie. Nie potwierdza działania OAuth, API,
            brokera, pipeline ani przychodów. Zweryfikowany stan aktywnego projektu jest pokazany wyżej
            razem ze źródłem i czasem odczytu.
          </p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-5">
        {accounts.map((account) => {
          const summary = projectReadinessSummary(account)
          const tone = 'border-text-tertiary/30 bg-elevated/60 text-text-secondary'
          return (
            <article
              key={account.id}
              className={[
                'border p-3',
                account.id === activeAccountId ? 'border-accent-primary/60 bg-accent-primary/5' : 'border-border-default bg-panel',
              ].join(' ')}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-text-primary">
                    {account.name}
                  </div>
                  <div className="mt-1 font-mono text-[9px] uppercase text-text-tertiary">
                    {PROJECT_TYPE_COPY[account.type]}
                  </div>
                </div>
                <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${tone}`}>
                  {summary.score}% konfiguracji
                </span>
              </div>

              <div className="mt-3 h-1.5 bg-void">
                <div
                  className="h-full bg-text-tertiary"
                  style={{ width: `${summary.score}%` }}
                />
              </div>

              <div className="mt-3 space-y-2">
                {summary.requirements.map((item) => (
                  <div key={item.label} className="border border-border-default bg-void p-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[10px] uppercase text-text-secondary">{item.label}</span>
                      <span className={['font-mono text-[9px] uppercase', item.ok ? 'text-text-secondary' : 'text-warning'].join(' ')}>
                        {item.ok ? 'USTAWIONO LOKALNIE' : 'BRAK'}
                      </span>
                    </div>
                    <div className="mt-1 text-[10px] leading-4 text-text-tertiary">{item.detail}</div>
                  </div>
                ))}
              </div>
            </article>
          )
        })}
      </div>
    </div>
  )
}

function asStatusTone(ok: boolean | null | undefined) {
  if (ok === true) return 'border-success/40 bg-success/10 text-success'
  if (ok === false) return 'border-error/40 bg-error/10 text-error'
  return 'border-warning/40 bg-warning/10 text-warning'
}

function statusLabel(ok: boolean | null | undefined) {
  if (ok === true) return 'OK'
  if (ok === false) return 'BRAK'
  return 'SPRAWDZ'
}

function optionalNumberDraft(value: number | null | undefined) {
  return value == null ? '' : String(value)
}

function parseLimitDraft(value: string) {
  const normalized = value.trim().replace(',', '.')
  if (!normalized) return null
  const parsed = Number(normalized)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}

function activeMoneyAccount(summary: MoneyMachineOperatorSummaryPayload | null, accountId: string) {
  return summary?.accounts.find((item) => item.account_id === accountId) ?? null
}

function contentReadinessForAccount(
  controlCenter: SettingsControlCenterPayload | null,
  accountId: string,
): ContentReadinessItem | null {
  return controlCenter?.trustedAuto.contentReadiness?.items.find(
    (item) => item.contentAccountId === accountId,
  ) ?? null
}

/**
 * Backend content services are keyed by the channel identifier kept in the
 * project configuration. Older localStorage entries can have a legacy UI id,
 * so never use that id for OAuth, quality, or production requests.
 */
function contentBackendAccountId(account: Account): string {
  const configuredChannelId = String(account.platformConfig.channelId ?? '').trim()
  return account.type === 'youtube' && configuredChannelId ? configuredChannelId : account.id
}

// Keep the account-scoping rule next to the UI that consumes it and expose it for regression tests.
// eslint-disable-next-line react-refresh/only-export-components
export function scopedYouTubeConnectionState(
  controlCenter: SettingsControlCenterPayload | null,
  account: Account | null,
) {
  if (!controlCenter || !account || account.type !== 'youtube') {
    return {
      connected: false,
      verified: false,
      source: '/settings/control-center trustedAuto.contentReadiness.items',
      observedAt: null,
      readiness: null,
    }
  }

  const readiness = contentReadinessForAccount(controlCenter, contentBackendAccountId(account))
  const connected = Boolean(readiness?.connected && readiness.connectionId)
  return {
    connected,
    verified: connected,
    source: '/settings/control-center trustedAuto.contentReadiness.items',
    observedAt: readiness?.lastCheckedAt ?? null,
    readiness,
  }
}

function OperationalControlPanel({
  account,
  onOpenAccess,
  onOpenAccount,
}: {
  account: Account
  onOpenAccess: () => void
  onOpenAccount: () => void
}) {
  const updateAccount = useAccountStore((state) => state.updateAccount)
  const backendAccountId = contentBackendAccountId(account)
  const [controlCenter, setControlCenter] = useState<SettingsControlCenterPayload | null>(null)
  const [readiness, setReadiness] = useState<ProductionReadinessPayload | null>(null)
  const [publicQuality, setPublicQuality] = useState<ProductionPublicQualityStatusPayload | null>(null)
  const [moneyMachine, setMoneyMachine] = useState<MoneyMachineOperatorSummaryPayload | null>(null)
  const [portfolio, setPortfolio] = useState<PortfolioAlphaSettings | null>(null)
  const [riskPolicySnapshot, setRiskPolicySnapshot] = useState<InvestmentRiskPolicy | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'saving' | 'error'>('idle')
  const [message, setMessage] = useState('')
  const [dailyLimitDraft, setDailyLimitDraft] = useState('')
  const [monthlyLimitDraft, setMonthlyLimitDraft] = useState('')
  const [oauthEnabledDraft, setOauthEnabledDraft] = useState(false)
  const [schedulerEnabledDraft, setSchedulerEnabledDraft] = useState(false)
  const [publicUploadDraft, setPublicUploadDraft] = useState(false)
  const [nichesDraft, setNichesDraft] = useState('')
  const [referenceDraft, setReferenceDraft] = useState('')
  const [competitorDraft, setCompetitorDraft] = useState('')
  const [guidelinesDraft, setGuidelinesDraft] = useState('')

  const updateProjectConfig = (patch: Account['platformConfig']) => {
    updateAccount(account.id, {
      platformConfig: {
        ...account.platformConfig,
        ...patch,
      },
    })
  }

  useEffect(() => {
    setNichesDraft(String(account.platformConfig.niches ?? account.niche ?? ''))
    setReferenceDraft(joinLines(splitLines(account.platformConfig.referenceChannels)))
    setCompetitorDraft(joinLines(splitLines(account.platformConfig.competitorChannels)))
    setGuidelinesDraft(String(account.platformConfig.strategicGuidelines ?? account.platformConfig.contentPolicy ?? ''))
  }, [account.id, account.niche, account.platformConfig])

  const refresh = async () => {
    setStatus('loading')
    setMessage('')
    const results = await Promise.allSettled([
      fetchSettingsControlCenter(),
      fetchProductionReadiness(false),
      fetchMoneyMachineSummary({ accountId: backendAccountId, includeOauth: true, maxUploads: 3, perAccountLimit: 1 }),
      account.type === 'investment' ? fetchPortfolioAlphaSettings() : Promise.resolve(null),
      account.type === 'investment' ? fetchInvestmentRiskPolicy() : Promise.resolve(null),
      account.type === 'youtube' ? fetchProductionPublicQualityStatus(backendAccountId) : Promise.resolve(null),
    ])

    const [controlResult, readinessResult, moneyResult, portfolioResult, riskPolicyResult, publicQualityResult] = results
    const failures: string[] = []

    if (controlResult.status === 'fulfilled') {
      setControlCenter(controlResult.value)
      setDailyLimitDraft(optionalNumberDraft(controlResult.value.policy.maxDailyCost))
      setMonthlyLimitDraft(optionalNumberDraft(controlResult.value.policy.maxMonthlyCost))
      setOauthEnabledDraft(controlResult.value.runtime.oauthEnabled)
      setSchedulerEnabledDraft(controlResult.value.runtime.schedulerEnabled)
      setPublicUploadDraft(controlResult.value.runtime.publicUploadEnabled)
    } else {
      failures.push('Nie udało się pobrać centrum ustawień.')
    }

    if (readinessResult.status === 'fulfilled') {
      setReadiness(readinessResult.value)
    } else {
      failures.push('Nie udało się pobrać readiness produkcji.')
    }

    if (moneyResult.status === 'fulfilled') {
      setMoneyMachine(moneyResult.value)
    } else {
      failures.push('Nie udało się pobrać statusu money-machine.')
    }

    if (portfolioResult.status === 'fulfilled') {
      setPortfolio(portfolioResult.value)
    } else if (account.type === 'investment') {
      failures.push('Nie udało się pobrać Portfolio Alpha.')
    }

    if (riskPolicyResult.status === 'fulfilled') {
      setRiskPolicySnapshot(riskPolicyResult.value?.policy ?? null)
    } else if (account.type === 'investment') {
      failures.push('Nie udalo sie pobrac egzekwowanych limitow ryzyka.')
    }

    if (publicQualityResult.status === 'fulfilled') {
      setPublicQuality(publicQualityResult.value)
    } else if (account.type === 'youtube') {
      failures.push('Brak odczytu public quality dla tego kanalu.')
    }

    setStatus(failures.length ? 'error' : 'idle')
    setMessage(failures.join(' '))
  }

  useEffect(() => {
    void refresh()
  }, [account.id, backendAccountId])

  const saveLimits = async () => {
    setStatus('saving')
    setMessage('')
    try {
      const updated = await updateSettingsPolicy({
        maxDailyCost: parseLimitDraft(dailyLimitDraft),
        maxMonthlyCost: parseLimitDraft(monthlyLimitDraft),
      })
      setControlCenter(updated)
      setMessage('Zapisano limity kosztów w backendzie.')
      setStatus('idle')
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'Nie udało się zapisać limitów.')
    }
  }

  const saveRuntime = async () => {
    setStatus('saving')
    setMessage('')
    try {
      const updated = await updateSettingsRuntime({
        oauthEnabled: oauthEnabledDraft,
        schedulerEnabled: schedulerEnabledDraft,
        publicUploadEnabled: publicUploadDraft,
      })
      setControlCenter(updated)
      setMessage('Zapisano przełączniki runtime.')
      setStatus('idle')
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'Nie udało się zapisać runtime.')
    }
  }

  const saveStrategy = () => {
    updateAccount(account.id, {
      niche: nichesDraft.trim() || account.niche,
      platformConfig: {
        ...account.platformConfig,
        niches: nichesDraft,
        referenceChannels: referenceDraft,
        competitorChannels: competitorDraft,
        strategicGuidelines: guidelinesDraft,
      },
    })
    setMessage('Zapisano materiały strategiczne lokalnie dla tego projektu.')
  }

  const ensureStyleGuide = async () => {
    saveStrategy()
    setStatus('saving')
    setMessage('')
    try {
      const result = await ensureProductionStyleGuide({
        accountId: backendAccountId,
        channelName: account.name,
        niche: nichesDraft || account.niche,
        overwrite: false,
      })
      setMessage(result.message || `Style guide gotowy: ${result.path ?? 'backend'}.`)
      setStatus('idle')
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'Nie udało się przygotować style guide.')
    }
  }

  const probeYoutube = async () => {
    setStatus('loading')
    setMessage('')
    try {
      const channel = contentReadinessForAccount(controlCenter, backendAccountId)
      if (!channel?.accountId) {
        throw new Error('Ten kanal nie ma aktywnego mapowania OAuth do testu. Polacz go ponownie.')
      }
      await probeYouTubeConnector(channel.accountId)
      await refresh()
      setMessage('YouTube OAuth/probe odświeżony. Jeśli zgoda była poprawna, status powinien być OK.')
      setStatus('idle')
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'YouTube probe nie przeszedł.')
    }
  }

  const reconnectYoutube = async () => {
    setStatus('loading')
    setMessage('')
    try {
      const result = await startOAuthConnection('youtube-studio', { contentAccountId: backendAccountId })
      if (result.authorizationUrl) {
        window.open(result.authorizationUrl, '_blank', 'popup,width=720,height=820')
      }
      setMessage('Otwarto zgode Google dla tego kanalu. Callback przypisze nowy token tylko do tego kanalu.')
      await refresh()
      setStatus('idle')
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'Nie udalo sie uruchomic ponownego polaczenia YouTube OAuth.')
    }
  }

  const youtubeConnector = controlCenter?.oauthConnectors.find((item) => item.connectorKey === 'youtube-studio') ?? null
  const channelReadiness = contentReadinessForAccount(controlCenter, backendAccountId)
  const moneyAccount = activeMoneyAccount(moneyMachine, backendAccountId)
  const youtubeReady = channelReadiness ? Boolean(channelReadiness.connected) : null
  const youtubeBlocker =
    channelReadiness?.blockingReasons.find((reason) => !reason.toLowerCase().includes('unverified google api')) ??
    channelReadiness?.blockingReasons[0] ??
    null
  const gmailSubject = controlCenter?.accessReality?.subjects.find((item) => item.connectorKey === 'gmail') ?? null
  const gmailReady = controlCenter
    ? gmailSubject?.accessLevel === 'live_read' || gmailSubject?.accessLevel === 'live_write'
    : null
  const privateUploadReady = moneyMachine ? Boolean(moneyAccount?.job_counts?.ready_private) : null
  const publicReady = publicQuality ? Boolean(publicQuality.ready) : null
  const investmentReady = account.type !== 'investment' ? null : Boolean(portfolio?.broker_read_access === 'read_only_verified')
  const hasBrokerConfig = hasAnyText(
    portfolio?.broker_name,
    portfolio?.broker_portal_url,
    account.platformConfig.brokerName,
    account.platformConfig.brokerPortalUrl,
  )
  const hasBrokerVaultRef = hasAnyText(portfolio?.credential_vault_ref, account.platformConfig.brokerCredentialVaultRef)
  const hasMarketData = Boolean(portfolio?.market_data_sources?.length) || hasAnyText(account.platformConfig.marketDataSources, account.platformConfig.dataSources)
  const hasLearningSources = Boolean(portfolio?.learning_resources?.length) || hasText(account.platformConfig.learningResources)
  const realTradingBlocked = portfolio?.real_trading_blocked ?? account.platformConfig.realTradingBlocked !== 'false'
  const riskPolicyConfigured = Boolean(riskPolicySnapshot?.configured)
  const monthlyBudget =
    typeof portfolio?.monthly_spend_budget_pln === 'number'
      ? portfolio.monthly_spend_budget_pln
      : Number(account.platformConfig.monthlySpendBudgetPln ?? 0)
  const hasBudgetPolicy = Number.isFinite(monthlyBudget) && monthlyBudget > 0
  const hasPaymentRef = Boolean(portfolio?.payment_method_ref) || hasText(account.platformConfig.paymentMethodRef)
  const hasContentStyleGuide = hasAnyText(
    account.platformConfig.strategicGuidelines,
    account.platformConfig.referenceChannels,
    account.platformConfig.competitorChannels,
  )
  const hasContentNiche = hasAnyText(account.platformConfig.niches, account.niche)

  const cards =
    account.type === 'youtube'
      ? [
          {
            label: 'YouTube OAuth',
            ok: youtubeReady,
            detail: youtubeReady
              ? `Połączone${youtubeConnector?.connectedAt ? ` od ${youtubeConnector.connectedAt}` : ''}.`
              : 'Brak aktywnego połączenia w backendzie albo probe nie przeszedł.',
          },
          {
            label: 'Gmail raporty',
            ok: gmailReady,
            detail: gmailReady ? 'Gmail connector jest połączony.' : 'Brak aktywnego Gmail connectora w backendzie.',
          },
          {
            label: 'Prywatny upload',
            ok: privateUploadReady,
            detail: readiness
              ? `${readiness.blocking_count} blokad, ${readiness.warning_count} ostrzeżeń.`
              : 'Brak danych readiness.',
          },
          {
            label: 'Public quality',
            ok: publicReady,
            detail: publicReady
              ? 'Są joby gotowe do publicznej promocji.'
              : 'Publiczny upload nadal wymaga QA, premium voice, visual backend i style guide.',
          },
          {
            label: 'Style guide',
            ok: hasContentStyleGuide && hasContentNiche,
            detail: hasContentStyleGuide
              ? 'Projekt ma niszę, wzorce albo wytyczne jakości do renderów.'
              : 'Dodaj kanały inspiracji, konkurencję i wytyczne jakości przed renderem.',
          },
        ]
      : account.type === 'investment'
        ? [
            {
              label: 'Broker demo',
              ok: hasBrokerConfig,
              detail: hasBrokerConfig
                ? `Broker: ${portfolio?.broker_name || account.platformConfig.brokerName || 'skonfigurowany'}.`
                : 'Dodaj URL brokera i identyfikator konta demo.',
            },
            {
              label: 'Vault brokera',
              ok: hasBrokerVaultRef,
              detail: hasBrokerVaultRef
                ? 'Projekt ma referencję do vaulta. Hasło nie jest trzymane w localStorage.'
                : 'Dodaj tylko vault ref loginu i hasła. Nie wpisuj hasła w zwykłe pole.',
            },
            {
              label: 'XTB read-only',
              ok: investmentReady,
              detail:
                portfolio?.broker_read_access === 'read_only_verified'
                  ? 'XTB read-only zweryfikowane przez backend.'
                  : 'XTB read-only jeszcze niezweryfikowane; real trading pozostaje zablokowany.',
            },
            {
              label: 'Dane i nauka',
              ok: hasMarketData && hasLearningSources,
              detail: hasMarketData
                ? 'Źródła danych rynku są wpisane. Uzupełnij też kursy/playbook, jeśli brakuje.'
                : 'Dodaj market-data, watchlistę i kursy, z których Atnos ma budować playbook.',
            },
            {
              label: 'Ryzyko i budżet',
              ok: realTradingBlocked && riskPolicyConfigured && hasBudgetPolicy,
              detail: !realTradingBlocked
                ? 'Live trading musi pozostac zablokowany.'
                : !riskPolicyConfigured
                  ? 'Brak potwierdzonych limitow w backendzie. Ustaw je w Portfolio Alpha.'
                  : `Live trading zablokowany. Budzet: ${hasBudgetPolicy ? `${monthlyBudget} PLN` : 'brak'}${hasPaymentRef ? ', payment ref zapisany.' : '.'}`,
            },
          ]
        : [
            {
              label: 'Konektory projektu',
              ok: hasText(account.platformConfig.requiredAccess),
              detail: 'Ten typ projektu używa własnych konektorów, a nie YouTube OAuth.',
            },
            {
              label: 'Gmail raporty',
              ok: gmailReady,
              detail: gmailReady ? 'Gmail connector jest połączony.' : 'Brak aktywnego Gmail connectora w backendzie.',
            },
            {
              label: 'Strategia',
              ok: hasAnyText(account.platformConfig.strategicGuidelines, account.platformConfig.contentPolicy, account.niche),
              detail: 'Opis celu, zasad i zakazów projektu.',
            },
            {
              label: 'Harmonogram',
              ok: account.schedule.days.length > 0,
              detail: 'Dni i godzina pracy projektu.',
            },
            {
              label: 'Guardrails',
              ok: account.automationMode !== 'auto_public',
              detail: 'Akcje ryzykowne wymagają oddzielnej zgody i logu.',
            },
          ]

  return (
    <section className="mb-4 w-full min-w-0 max-w-full overflow-hidden border border-accent-primary/30 bg-void">
      <div className="border-b border-accent-primary/20 bg-panel p-4">
        <div className="flex min-w-0 flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 w-full lg:flex-1">
            <div className="font-display text-[11px] uppercase tracking-[0.22em] text-accent-primary">
              Centrum operacyjne
            </div>
            <h2 className="mt-1 font-display text-xl font-semibold uppercase tracking-[0.05em] text-text-primary sm:text-2xl sm:tracking-[0.08em]">
              <span className="block">Dostępy, limity i</span>
              <span className="block">wytyczne Atnosa</span>
            </h2>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-text-secondary">
              Ten panel sprawdza realny backend, nie tylko lokalny stan UI. Tu ustawiasz limity kosztów,
              przełączniki automatyzacji oraz materiały strategiczne dla aktywnego projektu.
            </p>
          </div>
          <div className="grid w-full max-w-full grid-cols-1 gap-2 sm:grid-cols-2 lg:flex lg:w-auto lg:flex-wrap">
            <button
              type="button"
              onClick={() => void refresh()}
              className="w-full border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary lg:w-auto"
            >
              Odśwież stan
            </button>
            <button
              type="button"
              onClick={onOpenAccess}
              className="w-full border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary lg:w-auto"
            >
              Diagnostyka dostępów
            </button>
          </div>
        </div>
      </div>

      <div className="grid gap-4 p-4 2xl:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            {cards.map((card) => {
              const resolvedCard =
                card.label === 'YouTube OAuth'
                  ? {
                      ...card,
                      ok: youtubeReady,
                      detail: youtubeReady
                        ? `Polaczone z kontem: ${channelReadiness?.accountLabel ?? account.name}.`
                        : youtubeBlocker ?? 'Ten kanal wymaga ponownego polaczenia YouTube OAuth.',
                    }
                  : card.label === 'Gmail raporty'
                    ? {
                        ...card,
                        ok: gmailReady,
                        detail: gmailReady
                          ? 'Live sesja Gmail OAuth jest potwierdzona przez test dostepow.'
                          : 'Brak potwierdzonej live sesji Gmail OAuth w tescie dostepow.',
                      }
                    : card.label === 'Prywatny upload'
                      ? {
                          ...card,
                          ok: privateUploadReady,
                          detail: privateUploadReady
                            ? 'Paczka prywatnego uploadu po QA jest gotowa dla tego kanalu.'
                            : 'Brak paczki prywatnego uploadu po QA dla tego kanalu.',
                        }
                      : card.label === 'Public quality'
                        ? {
                            ...card,
                            ok: publicReady,
                            detail: publicReady
                              ? 'Glos, backend wizualny i style guide przeszly dla tego kanalu.'
                              : publicQuality?.blockers[0] ?? 'Public quality nie jest gotowe dla tego kanalu.',
                          }
                        : card.label === 'Style guide'
                          ? {
                              ...card,
                          ok: publicQuality ? Boolean(publicQuality.style_guide.ready) : null,
                              detail: publicQuality?.style_guide.ready
                                ? `Zweryfikowane zrodlo: ${publicQuality.style_guide.source ?? 'lokalny zapis nauki'}.`
                                : publicQuality?.style_guide.next_action ?? 'Style guide kanalu jest wymagany przed renderem.',
                            }
                          : card
              return (
              <article key={resolvedCard.label} className="min-w-0 border border-border-default bg-panel p-3">
                <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 break-words font-display text-[11px] uppercase tracking-[0.12em] text-text-primary">
                    {resolvedCard.label}
                  </div>
                  <span className={`shrink-0 border px-2 py-1 font-mono text-[9px] uppercase ${asStatusTone(resolvedCard.ok)}`}>
                    {statusLabel(resolvedCard.ok)}
                  </span>
                </div>
                <p className="mt-2 min-h-12 text-[11px] leading-5 text-text-tertiary">{resolvedCard.detail}</p>
              </article>
              )
            })}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="border border-border-default bg-panel p-4">
              <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">
                Limity i bezpieczniki
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <Field label="Dzienny limit kosztów PLN" value={dailyLimitDraft} type="number" onChange={setDailyLimitDraft} />
                <Field label="Miesięczny limit kosztów PLN" value={monthlyLimitDraft} type="number" onChange={setMonthlyLimitDraft} />
              </div>
              <div className="mt-3 grid gap-2">
                {account.type === 'youtube' ? (
                  <>
                    <ToggleField
                      label="OAuth YouTube/Gmail aktywny"
                      checked={oauthEnabledDraft}
                      onChange={setOauthEnabledDraft}
                      detail="Pozwala backendowi używać połączonych kont Google/YouTube/Gmail dla kanałów contentowych."
                    />
                    <ToggleField
                      label="Public upload odblokowany"
                      checked={publicUploadDraft}
                      onChange={setPublicUploadDraft}
                      detail="Nie omija QA. Tylko pozwala promować prywatne filmy, gdy public quality gate przejdzie."
                    />
                  </>
                ) : account.type === 'investment' ? (
                  <>
                    <ToggleField
                      label="Paper trading only"
                      checked={account.platformConfig.brokerAccountMode !== 'live_read_only'}
                      onChange={(checked) => updateProjectConfig({ brokerAccountMode: checked ? 'demo_paper' : 'live_read_only' })}
                      detail="Domyślnie Atnos ma pracować na demo/paper. Live nadal wymaga osobnego guardraila i zgody."
                    />
                    <ToggleField
                      label="Real trading zablokowany"
                      checked={account.platformConfig.realTradingBlocked !== 'false'}
                      onChange={(checked) => updateProjectConfig({ realTradingBlocked: checked ? 'true' : 'false' })}
                      detail="Ten bezpiecznik ma zostać włączony, dopóki paper trading nie pokaże wyników i limitów ryzyka."
                    />
                    <ToggleField
                      label="Reguły akceptacji transakcji"
                      checked={account.platformConfig.tradeApprovalRules === 'true'}
                      onChange={(checked) => updateProjectConfig({ tradeApprovalRules: checked ? 'true' : 'false' })}
                      detail="Bez reguł akceptacji Atnos może robić research i alerty, ale nie może przejść do egzekucji."
                    />
                  </>
                ) : (
                  <div className="border border-border-default bg-void px-3 py-3">
                    <span className="block font-mono text-[10px] uppercase text-text-secondary">
                      Dostępy projektowe
                    </span>
                    <span className="mt-1 block text-[11px] leading-5 text-text-tertiary">
                      Aktywny projekt nie jest kanałem YouTube. Użyj pól specyficznych dla tego typu projektu oraz vault refów zamiast haseł.
                    </span>
                  </div>
                )}
                <ToggleField
                  label="Scheduler aktywny"
                  checked={schedulerEnabledDraft}
                  onChange={setSchedulerEnabledDraft}
                  detail="Pozwala Atnosowi uruchamiać cykliczne zbieranie danych i kolejki."
                />
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void saveLimits()}
                  className="border border-accent-primary bg-accent-primary/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
                >
                  Zapisz limity
                </button>
                <button
                  type="button"
                  onClick={() => void saveRuntime()}
                  className="border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary"
                >
                  {account.type === 'youtube' ? 'Zapisz runtime' : 'Zapisz scheduler'}
                </button>
              </div>
            </div>

            <div className="border border-border-default bg-panel p-4">
              <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">
                Aktywny projekt
              </div>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Nazwa</div>
                  <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">{account.name}</div>
                </div>
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Tryb automatyzacji</div>
                  <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">{account.automationMode}</div>
                </div>
                {account.type === 'investment' ? (
                  <>
                    <div className="border border-border-default bg-void p-3">
                      <div className="font-mono text-[10px] uppercase text-text-tertiary">Tryb rachunku</div>
                      <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">
                        {portfolio?.account_mode ?? account.platformConfig.brokerAccountMode ?? 'demo_paper'}
                      </div>
                    </div>
                    <div className="border border-border-default bg-void p-3">
                      <div className="font-mono text-[10px] uppercase text-text-tertiary">Read-only</div>
                      <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">
                        {portfolio?.broker_read_access ?? (account.platformConfig.brokerReadAccess === 'true' ? 'candidate' : 'not_configured')}
                      </div>
                    </div>
                    <div className="border border-border-default bg-void p-3">
                      <div className="font-mono text-[10px] uppercase text-text-tertiary">Budżet</div>
                      <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">
                        {hasBudgetPolicy ? `${monthlyBudget} PLN` : 'brak'}
                      </div>
                    </div>
                    <div className="border border-border-default bg-void p-3">
                      <div className="font-mono text-[10px] uppercase text-text-tertiary">Real trading</div>
                      <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">
                        {realTradingBlocked ? 'zablokowany' : 'wymaga audytu'}
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="border border-border-default bg-void p-3">
                      <div className="font-mono text-[10px] uppercase text-text-tertiary">Topic buffer</div>
                      <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">
                        {moneyAccount?.topic_buffer ?? moneyMachine?.totals.topic_buffer ?? 0}
                      </div>
                    </div>
                    <div className="border border-border-default bg-void p-3">
                      <div className="font-mono text-[10px] uppercase text-text-tertiary">Gotowe prywatne uploady</div>
                      <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">
                        {moneyAccount?.job_counts?.ready_private ?? moneyMachine?.totals.ready_private_upload_jobs ?? 0}
                      </div>
                    </div>
                  </>
                )}
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Raporty Gmail</div>
                  <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">
                    {gmailReady ? 'połączone' : 'brak'}
                  </div>
                </div>
                <div className="border border-border-default bg-void p-3">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Źródło backendu</div>
                  <div className="mt-1 font-display text-sm uppercase tracking-[0.08em] text-text-primary">
                    {account.type === 'investment' ? 'Portfolio API' : 'Content API'}
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={onOpenAccount}
                className="mt-4 border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary"
              >
                Otwórz szczegóły konta
              </button>
            </div>
          </div>
        </div>

        <div className="border border-border-default bg-panel p-4">
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">
            Materiały strategiczne
          </div>
          <p className="mt-2 text-sm leading-6 text-text-secondary">
            To są dane, z których Atnos ma budować tematy, style guide, analizę konkurencji i quality gate.
            Zapis tutaj nie publikuje niczego automatycznie.
          </p>
          <div className="mt-4 space-y-3">
            <TextAreaField
              label="Nisze / słowa kluczowe"
              value={nichesDraft}
              placeholder="personal finance, ETF investing, dark history..."
              onChange={setNichesDraft}
            />
            <TextAreaField
              label="Kanały inspiracji"
              value={referenceDraft}
              placeholder="https://www.youtube.com/@MrBallen"
              onChange={setReferenceDraft}
            />
            <TextAreaField
              label="Konkurencja"
              value={competitorDraft}
              placeholder="Jeden URL kanału na linię"
              onChange={setCompetitorDraft}
            />
            <TextAreaField
              label="Wytyczne jakości / zakazy"
              value={guidelinesDraft}
              placeholder="English only. No childish thumbnails. Visuals must match the topic. No fake finance claims..."
              onChange={setGuidelinesDraft}
            />
          </div>
          {account.type === 'youtube' && channelReadiness?.needsAuth ? (
            <div className="mt-3 border border-warning/40 bg-warning/10 p-3">
              <div className="font-mono text-[10px] uppercase text-warning">Wymagane ponowne polaczenie YouTube OAuth</div>
              <div className="mt-1 text-xs leading-5 text-text-secondary">
                Ta akcja otwiera zgode Google dla {account.name} i przypisuje zwrocony token tylko do tego kanalu.
              </div>
              <button
                type="button"
                onClick={() => void reconnectYoutube()}
                className="mt-3 border border-accent-primary bg-accent-primary/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary hover:bg-accent-primary hover:text-void"
              >
                Polacz ponownie ten kanal
              </button>
            </div>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={saveStrategy}
              className="border border-accent-primary bg-accent-primary/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary"
            >
              Zapisz strategię
            </button>
            <button
              type="button"
              onClick={() => void ensureStyleGuide()}
              className="border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary"
            >
              Zbuduj style guide
            </button>
            {account.type === 'youtube' ? (
              <button
                type="button"
                onClick={() => void probeYoutube()}
                className="border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary"
              >
                Sprawdź YouTube OAuth
              </button>
            ) : null}
          </div>
        </div>
      </div>

      {message ? (
        <div className={['border-t border-border-default px-4 py-3 font-mono text-[11px]', status === 'error' ? 'text-error' : 'text-text-secondary'].join(' ')}>
          {message}
        </div>
      ) : null}
    </section>
  )
}

function runtimeBadgeClass(status: string) {
  if (status === 'ready' || status === 'completed') return 'border-success/40 bg-success/10 text-success'
  if (status === 'missing' || status === 'blocked' || status === 'failed' || status === 'error') {
    return 'border-error/40 bg-error/10 text-error'
  }
  if (status === 'degraded' || status === 'queued' || status === 'running' || status === 'planned') {
    return 'border-warning/40 bg-warning/10 text-warning'
  }
  return 'border-border-default bg-void text-text-tertiary'
}

function RuntimeMetricCard({ label, value, status }: { label: string; value: string; status?: string }) {
  return (
    <div className="border border-border-default bg-void p-3">
      <div className="font-mono text-[10px] uppercase text-text-tertiary">{label}</div>
      <div className={['mt-2 inline-flex border px-2 py-1 font-display text-[12px] uppercase tracking-[0.12em]', runtimeBadgeClass(status ?? value)].join(' ')}>
        {value}
      </div>
    </div>
  )
}

type RuntimeRepairItem = {
  key: string
  label: string
  status: string
  detail: string
  nextAction: string
  source: string
}

function workerRepairAction(worker: AiWorkersStatusPayload['workers'][number]) {
  if (worker.requiresBrowser) {
    return `Uruchom lub zaloguj ${worker.label || worker.workerKey}, potem kliknij "Test workera".`
  }
  if (/ollama/i.test(worker.workerKey) || /ollama/i.test(worker.label)) {
    return 'Uruchom Ollamę, upewnij się że model jest pobrany, potem kliknij "Test workera".'
  }
  return 'Kliknij "Test workera" i sprawdź powód z backendu.'
}

function desktopAppRepairAction(app: LocalHostDesktopApp) {
  if (!app.installed) {
    return app.notes[0] || `Zainstaluj albo wskaż ścieżkę aplikacji ${app.label || app.appKey} w local host registry.`
  }
  if (!app.running && app.launchSupported) {
    return `Kliknij "Otwórz" przy ${app.label || app.appKey}.`
  }
  if (app.running && !app.windowVisible && app.focusSupported) {
    return `Kliknij "Fokus" przy ${app.label || app.appKey}.`
  }
  return app.notes[0] || 'Sprawdź lokalny host control i log aplikacji.'
}

function collectRuntimeRepairItems({
  runtimeHealth,
  workersStatus,
  localHost,
}: {
  runtimeHealth: RuntimeHealthPayload | null
  workersStatus: AiWorkersStatusPayload | null
  localHost: LocalHostControlSummary | null
}): RuntimeRepairItem[] {
  const items: RuntimeRepairItem[] = []

  if (!runtimeHealth) {
    items.push({
      key: 'runtime-health-missing',
      label: 'Runtime health',
      status: 'missing',
      detail: 'Brak odpowiedzi z /api/runtime/health.',
      nextAction: 'Uruchom backend Atnosa przez launcher i kliknij "Odśwież runtime".',
      source: '/api/runtime/health',
    })
  } else {
    const readiness = runtimeHealth.readiness
    if (readiness && !readiness.canRunCore) {
      items.push({
        key: 'runtime-core-blocked',
        label: readiness.headline || 'Core runtime',
        status: readiness.verdict || 'blocked',
        detail: `Blokujące komponenty: ${readiness.blockingComponents.join(', ') || 'brak listy'}.`,
        nextAction: readiness.firstAction || 'Napraw pierwszy blokujący komponent z listy runtime.',
        source: '/api/runtime/health.readiness',
      })
    }
    for (const component of runtimeHealth.components.filter((item) => item.status !== 'ready').slice(0, 8)) {
      items.push({
        key: `runtime:${component.key}`,
        label: component.label,
        status: component.status,
        detail: component.detail,
        nextAction: component.nextAction || component.firstAction || 'Sprawdź konfigurację komponentu runtime.',
        source: `/api/runtime/health:${component.key}`,
      })
    }
  }

  if (!workersStatus) {
    items.push({
      key: 'ai-workers-missing',
      label: 'AI workers',
      status: 'missing',
      detail: 'Brak odpowiedzi z /api/ai-workers/status.',
      nextAction: 'Uruchom backend AI Delegation i kliknij "Odśwież runtime".',
      source: '/api/ai-workers/status',
    })
  } else {
    for (const worker of workersStatus.workers.filter((item) => !item.ready).slice(0, 6)) {
      items.push({
        key: `worker:${worker.workerKey}`,
        label: worker.label || worker.workerKey,
        status: worker.status || 'missing',
        detail: worker.reason || 'Worker nie jest gotowy.',
        nextAction: workerRepairAction(worker),
        source: `/api/ai-workers/status:${worker.workerKey}`,
      })
    }
  }

  if (!localHost) {
    items.push({
      key: 'local-host-missing',
      label: 'Local host control',
      status: 'missing',
      detail: 'Brak odpowiedzi z /local-host/capabilities.',
      nextAction: 'Uruchom Atnos Desktop/launcher i odśwież panel.',
      source: '/local-host/capabilities',
    })
  } else {
    if (!localHost.desktopInputAvailable) {
      items.push({
        key: 'desktop-input-missing',
        label: 'Sterowanie myszą i klawiaturą',
        status: 'missing',
        detail: 'Local host zgłasza brak desktop input bridge.',
        nextAction: 'Włącz lokalny desktop input bridge; bez tego Atnos nie może kontrolować aplikacji PC.',
        source: '/local-host/capabilities.desktopInputAvailable',
      })
    }
    const importantApps = new Set(['atnos-desktop', 'comfy-desktop', 'kimi', 'capcut', 'github-desktop', 'docker-desktop', 'git-bash'])
    for (const app of localHost.desktopApps.filter((item) => importantApps.has(item.appKey) && (!item.installed || !item.running)).slice(0, 8)) {
      items.push({
        key: `desktop-app:${app.appKey}`,
        label: app.label || app.appKey,
        status: app.installed ? 'degraded' : 'missing',
        detail: app.installed ? 'Aplikacja jest wykryta, ale nie działa w tle.' : 'Aplikacja nie została wykryta przez local host registry.',
        nextAction: desktopAppRepairAction(app),
        source: `/local-host/capabilities:${app.appKey}`,
      })
    }
  }

  return items
}

function RuntimeAiDelegationPanel() {
  const [runtimeHealth, setRuntimeHealth] = useState<RuntimeHealthPayload | null>(null)
  const [workersStatus, setWorkersStatus] = useState<AiWorkersStatusPayload | null>(null)
  const [localHost, setLocalHost] = useState<LocalHostControlSummary | null>(null)
  const [tasks, setTasks] = useState<AiWorkerTask[]>([])
  const [status, setStatus] = useState<'idle' | 'loading' | 'running' | 'error'>('idle')
  const [message, setMessage] = useState('')
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const [delegationPrompt, setDelegationPrompt] = useState(
    'Review Atnos UI from user perspective. Focus on real runtime status, project-specific settings and clear next actions.',
  )

  const refresh = async () => {
    setStatus('loading')
    setMessage('')
    const [healthResult, workersResult, tasksResult, localHostResult] = await Promise.allSettled([
      fetchRuntimeHealth(),
      fetchAiWorkersStatus(),
      fetchAiWorkerTasks({ rootsOnly: true, limit: 8 }),
      fetchLocalHostCapabilities(),
    ])

    const failures: string[] = []
    if (healthResult.status === 'fulfilled') setRuntimeHealth(healthResult.value)
    else failures.push('Nie udało się pobrać /api/runtime/health.')

    if (workersResult.status === 'fulfilled') setWorkersStatus(workersResult.value)
    else failures.push('Nie udało się pobrać /api/ai-workers/status.')

    if (tasksResult.status === 'fulfilled') setTasks(tasksResult.value)
    else failures.push('Nie udało się pobrać /api/ai-workers/tasks.')

    if (localHostResult.status === 'fulfilled') setLocalHost(localHostResult.value)
    else failures.push('Nie udało się pobrać /local-host/capabilities.')

    setStatus(failures.length ? 'error' : 'idle')
    setMessage(failures.join(' '))
  }

  useEffect(() => {
    void refresh()
  }, [])

  const probeWorker = async (workerKey: string) => {
    const key = `probe:${workerKey}`
    setPendingKey(key)
    setMessage('')
    try {
      const worker = await probeAiWorker(workerKey)
      await refresh()
      setMessage(`${worker.label || worker.workerKey}: ${worker.status}. ${worker.reason}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Test workera nie powiódł się.')
      setStatus('error')
    } finally {
      setPendingKey(null)
    }
  }

  const delegateLocal = async () => {
    setPendingKey('delegate')
    setStatus('running')
    setMessage('')
    try {
      const task = await delegateAiWorkerTask({
        taskType: 'script_quality_review',
        prompt: delegationPrompt,
        preferredWorker: 'ollama',
        privacyMode: 'strict_local',
      })
      await refresh()
      setMessage(`Delegacja #${task.id}: ${task.status}. ${task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? 'Zapisano w kolejce.'}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Delegacja do Ollamy nie powiodła się.')
      setStatus('error')
    } finally {
      setPendingKey(null)
    }
  }

  const planDelegation = async () => {
    setPendingKey('plan')
    setStatus('running')
    setMessage('')
    try {
      const task = await delegateAiWorkerPlan({
        taskType: 'engineering_runtime_delegation',
        prompt: delegationPrompt,
        preferredWorker: 'ollama',
        allowedWorkers: ['ollama', 'browser_claude', 'browser_kimi', 'browser_chatgpt'],
        privacyMode: 'strict_local',
        maxSteps: 6,
      })
      await refresh()
      setMessage(`Plan #${task.id}: ${task.status}. ${task.resultSummary ?? task.blockedReason ?? 'Zapisano plan delegacji.'}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Plan delegacji nie powiódł się.')
      setStatus('error')
    } finally {
      setPendingKey(null)
    }
  }

  const runChildTask = async (taskId: number) => {
    const key = `run:${taskId}`
    setPendingKey(key)
    setMessage('')
    try {
      const task = await runAiWorkerTask(taskId)
      await refresh()
      setMessage(`Task #${task.id}: ${task.status}. ${task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? 'Zaktualizowano.'}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Uruchomienie taska nie powiodło się.')
      setStatus('error')
    } finally {
      setPendingKey(null)
    }
  }

  const openDesktopApp = async (app: LocalHostDesktopApp) => {
    const key = `open-app:${app.appKey}`
    setPendingKey(key)
    setMessage('')
    try {
      const result = await openLocalHostDesktopApp(app.appKey)
      await refresh()
      setMessage(`${app.label}: ${result.exitCode === 0 ? 'uruchomiono' : 'błąd uruchomienia'}. ${result.stderr || result.stdout || result.command}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Nie udało się uruchomić ${app.label}.`)
      setStatus('error')
    } finally {
      setPendingKey(null)
    }
  }

  const focusDesktopApp = async (app: LocalHostDesktopApp) => {
    const key = `focus-app:${app.appKey}`
    setPendingKey(key)
    setMessage('')
    try {
      const result = await focusLocalHostDesktopApp(app.appKey)
      await refresh()
      setMessage(`${app.label}: ${result.exitCode === 0 ? 'okno aktywowane' : 'nie znaleziono okna'}. ${result.stderr || result.stdout || result.command}`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Nie udało się aktywować ${app.label}.`)
      setStatus('error')
    } finally {
      setPendingKey(null)
    }
  }

  const workers = workersStatus?.workers ?? []
  const readyWorkers = workers.filter((worker) => worker.ready).length
  const latestTasks = tasks.slice(0, 8)
  const desktopApps = localHost?.desktopApps ?? []
  const installedDesktopApps = desktopApps.filter((app) => app.installed)
  const runtimeReadiness = runtimeHealth?.readiness
  const nonReadyComponents = (runtimeHealth?.components ?? []).filter((component) => component.status !== 'ready')
  const repairItems = collectRuntimeRepairItems({ runtimeHealth, workersStatus, localHost })

  return (
    <section className="space-y-4">
      <div className="border border-accent-primary/30 bg-accent-primary/5 p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">
              Runtime & AI Delegation
            </div>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-text-secondary">
              Ten panel pokazuje realne dane z backendu: runtime health, workerów AI, guardrails i kolejkę delegacji.
              Browser AI działa jako supervised handoff; zakupy, OAuth, trading i publikacja pozostają za blokadami.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary"
          >
            Odśwież runtime
          </button>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-4">
          <RuntimeMetricCard label="Runtime" value={runtimeHealth?.status ?? 'loading'} status={runtimeHealth?.status} />
          <RuntimeMetricCard label="Workerzy ready" value={`${readyWorkers}/${workers.length || '-'}`} status={readyWorkers ? 'ready' : 'missing'} />
          <RuntimeMetricCard label="Tryb" value={workersStatus?.mode ?? 'loading'} />
          <RuntimeMetricCard label="Aplikacje PC" value={`${installedDesktopApps.length}/${desktopApps.length || '-'}`} status={installedDesktopApps.length ? 'ready' : 'missing'} />
        </div>

        {runtimeReadiness ? (
          <div className={['mt-4 border p-3', runtimeBadgeClass(runtimeReadiness.verdict)].join(' ')}>
            <div className="font-display text-[11px] uppercase tracking-[0.12em]">Werdykt runtime: {runtimeReadiness.verdict}</div>
            <div className="mt-2 text-sm leading-6 text-text-primary">{runtimeReadiness.headline}</div>
            <div className="mt-2 font-mono text-[11px] leading-5">Następny krok: {runtimeReadiness.firstAction || 'Brak.'}</div>
          </div>
        ) : null}
      </div>

      <QualityCouncilPanel />

      <div className="border border-border-default bg-panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">
              Następne akcje runtime
            </div>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-text-secondary">
              Lista jest liczona z realnych health-checków i local-host capabilities. Jeśli backend nie zwróci źródła,
              panel pokazuje to jako brak danych, nie jako gotowość.
            </p>
          </div>
          <span className={['border px-2 py-1 font-mono text-[9px] uppercase', repairItems.length ? 'border-warning/40 bg-warning/10 text-warning' : 'border-success/40 bg-success/10 text-success'].join(' ')}>
            {repairItems.length ? `${repairItems.length} do zrobienia` : 'brak blokad'}
          </span>
        </div>
        <div className="mt-4 grid gap-2">
          {repairItems.length ? (
            repairItems.slice(0, 10).map((item) => (
              <div key={item.key} className="min-w-0 max-w-full overflow-hidden border border-border-default bg-void p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="break-words font-display text-[12px] uppercase tracking-[0.12em] text-text-primary">{item.label}</div>
                    <div className="mt-1 break-words text-xs leading-5 text-text-secondary">{item.detail}</div>
                  </div>
                  <span className={['border px-2 py-1 font-mono text-[9px] uppercase', runtimeBadgeClass(item.status)].join(' ')}>
                    {item.status}
                  </span>
                </div>
                <div className="mt-2 break-words border-t border-border-default pt-2 text-xs leading-5 text-warning">
                  {item.nextAction}
                </div>
                <div className="mt-2 break-words font-mono text-[9px] uppercase text-text-tertiary [overflow-wrap:anywhere]">
                  źródło: {item.source}
                </div>
              </div>
            ))
          ) : (
            <div className="border border-success/30 bg-success/5 p-3 text-sm leading-6 text-success">
              Runtime, workerzy AI i lokalny host nie zgłaszają blokad w aktualnym snapshotcie.
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <div className="border border-border-default bg-panel p-4">
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">Komponenty runtime</div>
          <div className="mt-4 grid gap-2">
            {(runtimeHealth?.components ?? []).map((component) => (
              <div key={component.key} className="min-w-0 max-w-full overflow-hidden border border-border-default bg-void p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="break-words font-display text-[12px] uppercase tracking-[0.12em] text-text-primary">{component.label}</div>
                    <div className="mt-1 break-words text-xs leading-5 text-text-secondary">{component.detail}</div>
                  </div>
                  <span className={['border px-2 py-1 font-mono text-[9px] uppercase', runtimeBadgeClass(component.status)].join(' ')}>
                    {component.status}
                  </span>
                </div>
                {component.status !== 'ready' ? (
                  <div className="mt-2 break-words border-t border-border-default pt-2 font-mono text-[11px] text-warning [overflow-wrap:anywhere]">
                    Akcja: {component.nextAction || component.firstAction || 'Sprawdź konfigurację.'}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>

        <div className="border border-border-default bg-panel p-4">
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">Workerzy AI</div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {workers.map((worker) => {
              const probeKey = `probe:${worker.workerKey}`
              return (
                <div key={worker.workerKey} className="border border-border-default bg-void p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-display text-[12px] uppercase tracking-[0.12em] text-text-primary">{worker.label || worker.workerKey}</div>
                      <div className="mt-1 font-mono text-[10px] text-text-tertiary">{worker.workerKey}</div>
                    </div>
                    <span className={['border px-2 py-1 font-mono text-[9px] uppercase', runtimeBadgeClass(worker.status)].join(' ')}>
                      {worker.status}
                    </span>
                  </div>
                  <div className="mt-3 text-xs leading-5 text-text-secondary">{worker.reason}</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {worker.capabilities.slice(0, 4).map((capability) => (
                      <span key={capability} className="border border-border-default px-2 py-1 font-mono text-[9px] uppercase text-text-tertiary">
                        {capability}
                      </span>
                    ))}
                  </div>
                  <button
                    type="button"
                    disabled={pendingKey === probeKey}
                    onClick={() => void probeWorker(worker.workerKey)}
                    className="mt-3 border border-border-strong px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:text-text-disabled"
                  >
                    {pendingKey === probeKey ? 'Testuje...' : 'Test workera'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <div className="border border-border-default bg-panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">Aplikacje na tym PC</div>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-text-secondary">
              To jest kontrolowana lista aplikacji, których Atnos może używać przez lokalny host: uruchamianie, fokus okna i sterowanie myszą/klawiaturą.
              Akcje ryzykowne nadal wymagają guardrails, logu i osobnego zatwierdzenia.
            </p>
          </div>
          <span className={['border px-2 py-1 font-mono text-[9px] uppercase', localHost?.desktopInputAvailable ? 'border-success/40 bg-success/10 text-success' : 'border-warning/40 bg-warning/10 text-warning'].join(' ')}>
            desktop input: {localHost?.desktopInputAvailable ? 'ready' : 'missing'}
          </span>
        </div>

        <div className="mt-4 grid min-w-0 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {desktopApps.length ? (
            desktopApps.map((app) => {
              const openKey = `open-app:${app.appKey}`
              const focusKey = `focus-app:${app.appKey}`
              return (
                <div key={app.appKey} className="min-w-0 max-w-full overflow-hidden border border-border-default bg-void p-3">
                  <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 max-w-full">
                      <div className="font-display text-[12px] uppercase tracking-[0.12em] text-text-primary">{app.label || app.appKey}</div>
                      <div className="mt-1 font-mono text-[10px] text-text-tertiary">{app.category} / {app.appKey}</div>
                    </div>
                    <span className={['border px-2 py-1 font-mono text-[9px] uppercase', app.running ? 'border-success/40 bg-success/10 text-success' : app.installed ? 'border-warning/40 bg-warning/10 text-warning' : 'border-border-default bg-panel text-text-tertiary'].join(' ')}>
                      {app.running ? 'running' : app.installed ? 'installed' : 'missing'}
                    </span>
                  </div>
                  <div className="mt-3 min-w-0 max-w-full space-y-1 overflow-hidden font-mono text-[10px] text-text-tertiary">
                    <div>launch: {app.launchSupported ? 'tak' : 'nie'} / focus: {app.focusSupported ? 'tak' : 'nie'}</div>
                    {app.preferredEntryPath ? <div className="break-all">path: {app.preferredEntryPath}</div> : null}
                    {app.visibleWindowTitles.length ? <div className="break-all">window: {app.visibleWindowTitles[0]}</div> : null}
                  </div>
                  {app.notes.length ? (
                    <div className="mt-2 text-xs leading-5 text-text-secondary">{app.notes[0]}</div>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={!app.launchSupported || pendingKey === openKey}
                      onClick={() => void openDesktopApp(app)}
                      className="border border-border-strong px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:border-border-default disabled:text-text-disabled"
                    >
                      {pendingKey === openKey ? 'Otwieram...' : 'Otwórz'}
                    </button>
                    <button
                      type="button"
                      disabled={!app.focusSupported || pendingKey === focusKey}
                      onClick={() => void focusDesktopApp(app)}
                      className="border border-border-strong px-3 py-2 font-display text-[10px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:border-border-default disabled:text-text-disabled"
                    >
                      {pendingKey === focusKey ? 'Fokus...' : 'Fokus'}
                    </button>
                  </div>
                </div>
              )
            })
          ) : (
            <div className="border border-border-default bg-void p-3 text-sm leading-6 text-text-secondary md:col-span-2 xl:col-span-3">
              Backend nie zwrócił jeszcze listy aplikacji. Sprawdź `/local-host/capabilities` albo dodaj aplikacje do rejestru lokalnego hosta.
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="border border-border-default bg-panel p-4">
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">Smoke test delegacji</div>
          <p className="mt-2 text-sm leading-6 text-text-secondary">
            Ten test używa tylko `strict_local` i Ollamy. Nie wysyła sekretów do Claude/Kimi/ChatGPT.
          </p>
          <div className="mt-4">
            <TextAreaField label="Prompt testowy" value={delegationPrompt} onChange={setDelegationPrompt} />
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={pendingKey === 'delegate' || !delegationPrompt.trim()}
              onClick={() => void delegateLocal()}
              className="border border-accent-primary bg-accent-primary/10 px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-accent-primary disabled:border-border-default disabled:bg-transparent disabled:text-text-disabled"
            >
              {pendingKey === 'delegate' ? 'Deleguje...' : 'Deleguj do Ollamy'}
            </button>
            <button
              type="button"
              disabled={pendingKey === 'plan' || !delegationPrompt.trim()}
              onClick={() => void planDelegation()}
              className="border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] text-text-secondary hover:border-accent-primary hover:text-accent-primary disabled:text-text-disabled"
            >
              {pendingKey === 'plan' ? 'Planuje...' : 'Rozbij na workerów'}
            </button>
          </div>
        </div>

        <div className="border border-border-default bg-panel p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">Kolejka AI</div>
            <span className={['border px-2 py-1 font-mono text-[9px] uppercase', tasks.length ? 'border-success/40 bg-success/10 text-success' : 'border-border-default bg-void text-text-tertiary'].join(' ')}>
              {tasks.length} tasków
            </span>
          </div>
          <div className="mt-4 space-y-2">
            {latestTasks.length ? (
              latestTasks.map((task) => (
                <div key={task.id} className="border border-border-default bg-void p-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="font-display text-[12px] uppercase tracking-[0.12em] text-text-primary">{task.taskType}</div>
                      <div className="mt-1 font-mono text-[10px] text-text-tertiary">#{task.id} / {task.workerKey ?? 'unassigned'} / {task.privacyMode}</div>
                    </div>
                    <span className={['border px-2 py-1 font-mono text-[9px] uppercase', runtimeBadgeClass(task.status)].join(' ')}>
                      {task.status}
                    </span>
                  </div>
                  <div className="mt-2 text-xs leading-5 text-text-secondary">
                    {task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? task.inputSummary ?? 'Brak wyniku.'}
                  </div>
                  <AiWorkerTaskEvidence task={task} />
                  {task.children.length ? (
                    <div className="mt-3 space-y-1 border-t border-border-default pt-2">
                      {task.children.slice(0, 5).map((child) => {
                        const runKey = `run:${child.id}`
                        return (
                          <div key={child.id} className="flex items-center justify-between gap-2 font-mono text-[10px] text-text-tertiary">
                            <span>{child.sequenceIndex}. {child.taskRole}: {child.status}</span>
                            {['queued', 'planned', 'failed'].includes(child.status) ? (
                              <button
                                type="button"
                                disabled={pendingKey === runKey}
                                onClick={() => void runChildTask(child.id)}
                                className="border border-border-default px-2 py-1 text-[9px] uppercase text-accent-primary disabled:text-text-disabled"
                              >
                                {pendingKey === runKey ? 'RUN...' : 'RUN'}
                              </button>
                            ) : null}
                          </div>
                        )
                      })}
                    </div>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="border border-border-default bg-void p-3 text-sm text-text-secondary">
                Brak tasków. Uruchom smoke test lub odśwież backend.
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="grid min-w-0 gap-4 xl:grid-cols-2">
        <div className="min-w-0 max-w-full overflow-hidden border border-border-default bg-panel p-4">
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">Guardrails</div>
          <div className="mt-3 grid gap-2">
            {Object.entries(workersStatus?.guardrails ?? {}).map(([key, value]) => (
              <div key={key} className="flex min-w-0 flex-wrap items-center justify-between gap-3 border border-border-default bg-void px-3 py-2 font-mono text-[11px]">
                <span className="min-w-0 break-all text-text-tertiary">{key}</span>
                <span className={['min-w-0 break-all', String(value) === 'true' ? 'text-success' : 'text-text-secondary'].join(' ')}>{String(value)}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="min-w-0 max-w-full overflow-hidden border border-border-default bg-panel p-4">
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">Najbliższe braki</div>
          <div className="mt-3 space-y-2">
            {nonReadyComponents.length ? (
              nonReadyComponents.slice(0, 6).map((component) => (
                <div key={component.key} className="min-w-0 max-w-full overflow-hidden border border-border-default bg-void p-3">
                  <div className="font-display text-[11px] uppercase tracking-[0.12em] text-text-primary">{component.label}</div>
                  <div className="mt-1 break-words text-xs leading-5 text-warning [overflow-wrap:anywhere]">{component.nextAction || component.detail}</div>
                </div>
              ))
            ) : (
              <div className="border border-success/40 bg-success/10 p-3 text-sm text-success">Brak blokad runtime.</div>
            )}
          </div>
        </div>
      </div>

      {message ? (
        <div className={['border px-4 py-3 font-mono text-[11px]', status === 'error' ? 'border-error/40 bg-error/10 text-error' : 'border-border-default bg-void text-text-secondary'].join(' ')}>
          {message}
        </div>
      ) : null}
    </section>
  )
}

function AccountSettings({ account }: { account: Account }) {
  const updateAccount = useAccountStore((state) => state.updateAccount)
  const backendAccountId = contentBackendAccountId(account)
  const initialStatus = normalizeStatus(account.platformConfig.intelligenceLastStatus)
  const [intelligenceStatus, setIntelligenceStatus] = useState<IntelligenceStatus>(initialStatus)
  const [intelligenceMessage, setIntelligenceMessage] = useState<string>(
    translateCollectorMessage(String(account.platformConfig.intelligenceLastMessage ?? '')),
  )
  const [investmentSettingsStatus, setInvestmentSettingsStatus] = useState<'idle' | 'loading' | 'saving' | 'saved' | 'error'>('idle')
  const [investmentSettingsMessage, setInvestmentSettingsMessage] = useState('')
  const [riskPolicy, setRiskPolicy] = useState<InvestmentRiskPolicy | null>(null)
  const [riskMetrics, setRiskMetrics] = useState<InvestmentRiskMetrics | null>(null)
  const [riskPolicyStatus, setRiskPolicyStatus] = useState<'idle' | 'loading' | 'saving' | 'saved' | 'error'>('idle')
  const [riskPolicyMessage, setRiskPolicyMessage] = useState('')
  const [xtbStatus, setXtbStatus] = useState<XtbMarketDataStatus | null>(null)
  const [xtbCheckStatus, setXtbCheckStatus] = useState<'idle' | 'loading' | 'verifying' | 'verified' | 'error'>('idle')
  const [xtbCheckMessage, setXtbCheckMessage] = useState('')
  const [xtbVaultPassword, setXtbVaultPassword] = useState('')
  const [xtbVaultStatus, setXtbVaultStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [xtbVaultMessage, setXtbVaultMessage] = useState('')
  const [alpacaStatus, setAlpacaStatus] = useState<AlpacaPaperStatus | null>(null)
  const [alpacaKeyId, setAlpacaKeyId] = useState('')
  const [alpacaSecretKey, setAlpacaSecretKey] = useState('')
  const [alpacaConnectStatus, setAlpacaConnectStatus] = useState<'idle' | 'saving' | 'verifying' | 'verified' | 'error'>('idle')
  const [alpacaConnectMessage, setAlpacaConnectMessage] = useState('')
  const [secretStorage, setSecretStorage] = useState<AccountSecretStorageStatus | null>(null)
  const [automationSyncStatus, setAutomationSyncStatus] = useState<'idle' | 'loading' | 'saved' | 'error'>('idle')
  const [automationSyncMessage, setAutomationSyncMessage] = useState('')
  const xtbAutopilotPriceStatus = getXtbAutopilotPriceStatus(xtbStatus)
  const xtbApiRetired = xtbStatus?.api_available === false
  const identifierLabel = account.type === 'youtube' ? 'ID kanału / handle' : 'Identyfikator projektu / konta'
  const scheduleLabel = account.type === 'investment' ? 'Godzina raportu UTC' : 'Godzina publikacji UTC'

  useEffect(() => {
    setIntelligenceStatus(normalizeStatus(account.platformConfig.intelligenceLastStatus))
    setIntelligenceMessage(
      translateCollectorMessage(String(account.platformConfig.intelligenceLastMessage ?? '')),
    )
  }, [
    account.id,
    account.platformConfig.intelligenceLastMessage,
    account.platformConfig.intelligenceLastStatus,
  ])

  useEffect(() => {
    if (account.type !== 'youtube') return
    let cancelled = false

    void fetchIntelligenceAccounts()
      .then((items) => {
        if (cancelled) return
        const backendConfig = items.find((item) => item.account_id === backendAccountId)
        if (!backendConfig) {
          setAutomationSyncMessage('Kanał nie jest jeszcze zapisany w backendzie Intelligence. Uruchom najpierw zbieranie danych.')
          return
        }
        if (backendConfig.automation_mode !== account.automationMode) {
          updateAccount(account.id, { automationMode: backendConfig.automation_mode })
        }
        setAutomationSyncMessage(`Backend: ${backendConfig.automation_mode}.`)
      })
      .catch((error) => {
        if (!cancelled) {
          setAutomationSyncStatus('error')
          setAutomationSyncMessage(error instanceof Error ? error.message : 'Nie udało się odczytać trybu automatyzacji z backendu.')
        }
      })

    return () => {
      cancelled = true
    }
  }, [account.id, account.type, account.automationMode, backendAccountId])

  useEffect(() => {
    if (account.type !== 'investment') return
    let cancelled = false

    const loadInvestmentSettings = async () => {
      setInvestmentSettingsStatus('loading')
      setInvestmentSettingsMessage('')
      try {
        const [settings, marketDataStatus, alpacaPaperStatus, policySnapshot, storageStatus] = await Promise.all([
          fetchPortfolioAlphaSettings(),
          fetchXtbMarketDataStatus().catch(() => null),
          fetchAlpacaPaperStatus().catch(() => null),
          fetchInvestmentRiskPolicy().catch(() => null),
          fetchAccountSecretStorageStatus().catch(() => null),
        ])
        if (cancelled) return
        setXtbStatus(marketDataStatus)
        setAlpacaStatus(alpacaPaperStatus)
        setAlpacaConnectStatus(alpacaPaperStatus?.read_only_verified ? 'verified' : 'idle')
        setSecretStorage(storageStatus)
        setRiskPolicy(policySnapshot?.policy ?? null)
        setRiskMetrics(policySnapshot?.metrics ?? null)
        setRiskPolicyStatus(policySnapshot ? 'idle' : 'error')
        setRiskPolicyMessage(policySnapshot ? '' : 'Brak odczytu egzekwowanych limitow ryzyka z backendu.')
        updateAccount(account.id, {
          platformConfig: {
            ...account.platformConfig,
            brokerName: settings.broker_name,
            brokerPortalUrl: settings.broker_portal_url,
            brokerLoginEmail: settings.broker_account_identifier,
            brokerCredentialVaultRef: settings.credential_vault_ref,
            brokerReadAccess:
              settings.broker_read_access === 'read_only_verified' || marketDataStatus?.read_only_verified
                ? 'true'
                : 'false',
            brokerAccountMode: settings.account_mode,
            marketDataSources: joinLines(settings.market_data_sources),
            learningResources: joinLines(settings.learning_resources),
            monthlySpendBudgetPln: String(settings.monthly_spend_budget_pln ?? 0),
            paymentMethodRef: settings.payment_method_ref,
            paymentMethodConfigured: settings.payment_method_configured ? 'true' : 'false',
            realTradingBlocked: settings.real_trading_blocked ? 'true' : 'false',
            tradeApprovalRules: settings.trade_approval_rules_enabled ? 'true' : 'false',
            riskMaxSinglePaperTradePln: policySnapshot ? String(policySnapshot.policy.max_single_paper_trade_pln) : '',
            riskMaxPaperPositionPln: policySnapshot ? String(policySnapshot.policy.max_paper_position_pln) : '',
            riskMaxDailyLossPln: policySnapshot ? String(policySnapshot.policy.max_daily_realized_loss_pln) : '',
            riskMaxWeeklyLossPln: policySnapshot ? String(policySnapshot.policy.max_weekly_realized_loss_pln) : '',
            riskEmergencyStop: policySnapshot?.policy.emergency_stop ? 'true' : 'false',
          },
        })
        setInvestmentSettingsStatus('idle')
        setXtbCheckStatus(marketDataStatus?.read_only_verified ? 'verified' : 'idle')
      } catch (error) {
        if (cancelled) return
        setInvestmentSettingsStatus('error')
        setInvestmentSettingsMessage(error instanceof Error ? error.message : 'Nie udało się pobrać ustawień Portfolio Alpha.')
      }
    }

    void loadInvestmentSettings()
    return () => {
      cancelled = true
    }
  }, [account.id, account.type])

  const runIntelligence = async () => {
    setIntelligenceStatus('running')
    setIntelligenceMessage('')
    try {
      const result = await runIntelligenceCollectors({
        accountId: backendAccountId,
        niches: splitComma(account.platformConfig.niches ?? account.niche),
        competitorUrls: splitLines(account.platformConfig.competitorChannels),
        referenceUrls: splitLines(account.platformConfig.referenceChannels),
        region: String(account.platformConfig.region ?? 'PL'),
      })
      const timestamp = new Date().toISOString()
      const partial = collectorHasError(result)
      const youtubeResult = readCollector(result, 'youtube_trends')
      const googleTrendsResult = readCollector(result, 'google_trends')
      const competitorsResult = readCollector(result, 'competitors')
      const ownChannelResult = readCollector(result, 'own_channel')
      const referenceResult = readCollector(result, 'reference')
      const anySuccess =
        collectorSucceeded(youtubeResult) ||
        collectorSucceeded(googleTrendsResult) ||
        collectorSucceeded(competitorsResult) ||
        collectorSucceeded(ownChannelResult) ||
        collectorSucceeded(referenceResult)
      const message = firstCollectorMessage(result)
      const needsConfig = collectHasStatus(result, ['needs_config'])
      const unavailable = collectHasStatus(result, ['unavailable'])
      const limited = collectHasStatus(result, ['limited', 'quota_exhausted', 'rate_limited', 'retry_scheduled'])
      const nextStatus: IntelligenceStatus = partial
        ? anySuccess
          ? 'partial'
          : unavailable
            ? 'unavailable'
            : needsConfig
              ? 'needs_config'
              : limited
                ? 'limited'
                : 'error'
        : 'done'
      const nextMessage =
        message ??
        (nextStatus === 'partial'
          ? 'Część danych została zebrana. Kolektory YouTube wymagają podpiętego konta Google/YouTube.'
          : nextStatus === 'needs_config'
            ? 'Podłącz YouTube Studio/OAuth w ustawieniach konektorów. Dopiero wtedy Atnos pobierze realne dane kanału, konkurencji i wzorców.'
            : nextStatus === 'unavailable'
              ? 'Moduł Intelligence nie jest dostępny w aktualnym backendzie. Zrestartuj Atnosa po aktualizacji runtime.'
              : nextStatus === 'limited'
                ? 'Źródła danych są chwilowo ograniczone limitem. To nie jest awaria aplikacji.'
                : 'Zbieranie danych zakończone.')
      updateAccount(account.id, {
        platformConfig: {
          ...account.platformConfig,
          intelligenceLastCollectedAt: timestamp,
          intelligenceLastStatus: nextStatus,
          intelligenceLastMessage: nextMessage,
          youtubeTrendsLastCollectedAt: collectorSucceeded(youtubeResult)
            ? timestamp
            : account.platformConfig.youtubeTrendsLastCollectedAt,
          googleTrendsLastCollectedAt: collectorSucceeded(googleTrendsResult)
            ? timestamp
            : account.platformConfig.googleTrendsLastCollectedAt,
          competitorsLastCollectedAt: collectorSucceeded(competitorsResult)
            ? timestamp
            : account.platformConfig.competitorsLastCollectedAt,
          ownChannelLastCollectedAt: collectorSucceeded(ownChannelResult)
            ? timestamp
            : account.platformConfig.ownChannelLastCollectedAt,
          referenceLastCollectedAt: collectorSucceeded(referenceResult)
            ? timestamp
            : account.platformConfig.referenceLastCollectedAt,
        },
      })
      setIntelligenceStatus(nextStatus)
      setIntelligenceMessage(nextMessage)
    } catch (error) {
      const timestamp = new Date().toISOString()
      const rawMessage = error instanceof Error ? error.message : 'Nie udało się uruchomić zbierania danych.'
      const message = translateCollectorMessage(rawMessage)
      const nextStatus: IntelligenceStatus = isEndpointUnavailableMessage(rawMessage)
        ? 'unavailable'
        : isYoutubeConfigMessage(rawMessage)
          ? 'needs_config'
          : isRateLimitMessage(rawMessage)
            ? 'limited'
            : 'error'
      updateAccount(account.id, {
        platformConfig: {
          ...account.platformConfig,
          intelligenceLastErrorAt: timestamp,
          intelligenceLastStatus: nextStatus,
          intelligenceLastMessage: message,
        },
      })
      setIntelligenceMessage(message)
      setIntelligenceStatus(nextStatus)
    }
  }

  const updatePlatformConfig = (patch: Account['platformConfig']) => {
    updateAccount(account.id, {
      platformConfig: {
        ...account.platformConfig,
        ...patch,
      },
    })
  }

  const saveInvestmentRiskPolicy = async () => {
    const readLimit = (key: string, fallback: number, label: string) => {
      const value = Number(account.platformConfig[key] ?? fallback)
      if (!Number.isFinite(value) || value <= 0) {
        throw new Error(`${label} musi byc liczba wieksza od zera.`)
      }
      return value
    }

    setRiskPolicyStatus('saving')
    setRiskPolicyMessage('')
    try {
      const maxSinglePaperTradePln = readLimit(
        'riskMaxSinglePaperTradePln',
        riskPolicy?.max_single_paper_trade_pln ?? 5000,
        'Maksymalna wartosc pojedynczej transakcji',
      )
      const maxPaperPositionPln = readLimit(
        'riskMaxPaperPositionPln',
        riskPolicy?.max_paper_position_pln ?? 20000,
        'Maksymalna pozycja',
      )
      const maxDailyRealizedLossPln = readLimit(
        'riskMaxDailyLossPln',
        riskPolicy?.max_daily_realized_loss_pln ?? 1000,
        'Dzienny limit straty',
      )
      const maxWeeklyRealizedLossPln = readLimit(
        'riskMaxWeeklyLossPln',
        riskPolicy?.max_weekly_realized_loss_pln ?? 2500,
        'Tygodniowy limit straty',
      )
      const saved = await updateInvestmentRiskPolicy({
        maxSinglePaperTradePln,
        maxPaperPositionPln,
        maxDailyRealizedLossPln,
        maxWeeklyRealizedLossPln,
        emergencyStop: account.platformConfig.riskEmergencyStop === 'true',
      })
      setRiskPolicy(saved.policy)
      setRiskMetrics(saved.metrics)
      updatePlatformConfig({
        riskMaxSinglePaperTradePln: String(saved.policy.max_single_paper_trade_pln),
        riskMaxPaperPositionPln: String(saved.policy.max_paper_position_pln),
        riskMaxDailyLossPln: String(saved.policy.max_daily_realized_loss_pln),
        riskMaxWeeklyLossPln: String(saved.policy.max_weekly_realized_loss_pln),
        riskEmergencyStop: saved.policy.emergency_stop ? 'true' : 'false',
      })
      setRiskPolicyStatus('saved')
      setRiskPolicyMessage(
        saved.policy.emergency_stop
          ? 'Egzekwowany emergency stop jest wlaczony. Nowe paper trade sa blokowane.'
          : 'Twarde limity paper tradingu zapisane i egzekwowane przez backend.',
      )
    } catch (error) {
      setRiskPolicyStatus('error')
      setRiskPolicyMessage(error instanceof Error ? error.message : 'Nie udalo sie zapisac limitow ryzyka.')
    }
  }

  const updateContentAutomationMode = async (nextMode: AutomationMode) => {
    if (account.type !== 'youtube') {
      updateAccount(account.id, { automationMode: nextMode })
      return
    }

    setAutomationSyncStatus('loading')
    setAutomationSyncMessage('Zapisuję tryb automatyzacji w backendzie...')
    try {
      const saved = await configureIntelligenceAccount({
        accountId: backendAccountId,
        niches: splitComma(account.platformConfig.niches ?? account.niche),
        region: String(account.platformConfig.region ?? 'PL'),
        channelId: backendAccountId,
        channelTitle: account.name,
        automationMode: nextMode,
        refreshNow: false,
      })
      updateAccount(account.id, { automationMode: saved.automation_mode })
      setAutomationSyncStatus('saved')
      setAutomationSyncMessage(
        saved.automation_mode === 'manual'
          ? 'Backend zatrzymał automatyczne renderowanie dla tego kanału.'
          : 'Backend będzie automatycznie przygotowywał tylko prywatne próbki po przejściu QA.',
      )
    } catch (error) {
      setAutomationSyncStatus('error')
      setAutomationSyncMessage(error instanceof Error ? error.message : 'Nie udało się zapisać trybu automatyzacji.')
    }
  }

  const savePortfolioAlphaSettings = async () => {
    setInvestmentSettingsStatus('saving')
    setInvestmentSettingsMessage('')
    try {
      const credentialVaultRef = String(account.platformConfig.brokerCredentialVaultRef ?? '').trim()
      const saved = await updatePortfolioAlphaSettings({
        brokerName: String(account.platformConfig.brokerName ?? 'XTB Demo'),
        brokerPortalUrl: String(account.platformConfig.brokerPortalUrl ?? 'https://xstation5.xtb.com/'),
        brokerAccountIdentifier: String(account.platformConfig.brokerLoginEmail ?? ''),
        credentialVaultRef,
        brokerReadAccess:
          account.platformConfig.brokerReadAccess === 'true' || credentialVaultRef
            ? 'read_only_candidate'
            : 'not_configured',
        accountMode: String(account.platformConfig.brokerAccountMode ?? 'demo_paper') as 'demo_paper',
        marketDataSources: splitLines(
          String(account.platformConfig.marketDataSources ?? account.platformConfig.dataSources ?? ''),
        ),
        learningResources: splitLines(String(account.platformConfig.learningResources ?? '')),
        monthlySpendBudgetPln: Number(account.platformConfig.monthlySpendBudgetPln ?? 0),
        paymentMethodRef: String(account.platformConfig.paymentMethodRef ?? ''),
        paymentMethodConfigured: account.platformConfig.paymentMethodConfigured === 'true',
        realTradingBlocked: account.platformConfig.realTradingBlocked !== 'false',
        tradeApprovalRulesEnabled: account.platformConfig.tradeApprovalRules === 'true',
      })
      updateAccount(account.id, {
        platformConfig: {
          ...account.platformConfig,
          brokerName: saved.broker_name,
          brokerPortalUrl: saved.broker_portal_url,
          brokerLoginEmail: saved.broker_account_identifier,
          brokerCredentialVaultRef: saved.credential_vault_ref,
          brokerReadAccess: saved.broker_read_access === 'read_only_verified' ? 'true' : 'false',
          brokerAccountMode: saved.account_mode,
          marketDataSources: joinLines(saved.market_data_sources),
          learningResources: joinLines(saved.learning_resources),
          monthlySpendBudgetPln: String(saved.monthly_spend_budget_pln ?? 0),
          paymentMethodRef: saved.payment_method_ref,
          paymentMethodConfigured: saved.payment_method_configured ? 'true' : 'false',
          realTradingBlocked: saved.real_trading_blocked ? 'true' : 'false',
          tradeApprovalRules: saved.trade_approval_rules_enabled ? 'true' : 'false',
        },
      })
      setInvestmentSettingsStatus('saved')
      setInvestmentSettingsMessage('Ustawienia Portfolio Alpha zapisane w backendzie.')
      const marketDataStatus = await fetchXtbMarketDataStatus().catch(() => null)
      setXtbStatus(marketDataStatus)
    } catch (error) {
      setInvestmentSettingsStatus('error')
      setInvestmentSettingsMessage(
        error instanceof Error
          ? error.message
          : 'Nie udało się zapisać ustawień Portfolio Alpha.',
      )
    }
  }

  const saveXtbDemoCredential = async () => {
    const accountIdentifier = String(account.platformConfig.brokerLoginEmail ?? '').trim()
    const password = xtbVaultPassword.trim()
    if (!accountIdentifier || !password) {
      setXtbVaultStatus('error')
      setXtbVaultMessage('Wpisz login/e-mail XTB demo oraz hasło. Hasło nie jest zapisywane w ustawieniach projektu.')
      return
    }
    if (!secretStorage?.isSecretStorageEnabled) {
      setXtbVaultStatus('error')
      setXtbVaultMessage('Lokalny vault sekretów nie jest gotowy. Nie zapisano hasła.')
      return
    }

    setXtbVaultStatus('saving')
    setXtbVaultMessage('')
    try {
      const credential = await createAccountCredential({
        label: `XTB Demo / ${accountIdentifier}`,
        providerKey: 'xtb-demo',
        accountIdentifier,
        secretType: 'password',
        secretValue: password,
        riskLevel: 'high',
        allowedCapabilities: ['financial_read_later'],
        notes: 'XTB demo: tylko dane rynkowe read-only. Brak zgody na zlecenia realne.',
      })
      const vaultRef = `account-vault:${credential.id}`
      const saved = await updatePortfolioAlphaSettings({
        brokerName: String(account.platformConfig.brokerName ?? 'XTB Demo'),
        brokerPortalUrl: String(account.platformConfig.brokerPortalUrl ?? 'https://xstation5.xtb.com/'),
        brokerAccountIdentifier: accountIdentifier,
        credentialVaultRef: vaultRef,
        brokerReadAccess: 'read_only_candidate',
        accountMode: String(account.platformConfig.brokerAccountMode ?? 'demo_paper') as 'demo_paper',
        marketDataSources: splitLines(
          String(account.platformConfig.marketDataSources ?? account.platformConfig.dataSources ?? ''),
        ),
        learningResources: splitLines(String(account.platformConfig.learningResources ?? '')),
        monthlySpendBudgetPln: Number(account.platformConfig.monthlySpendBudgetPln ?? 0),
        paymentMethodRef: String(account.platformConfig.paymentMethodRef ?? ''),
        paymentMethodConfigured: account.platformConfig.paymentMethodConfigured === 'true',
        realTradingBlocked: true,
        tradeApprovalRulesEnabled: account.platformConfig.tradeApprovalRules === 'true',
      })
      await configureXtbDemoAccount({ accountIdentifier, credentialAccountId: credential.id })
      const marketDataStatus = await fetchXtbMarketDataStatus()
      setXtbStatus(marketDataStatus)
      updateAccount(account.id, {
        platformConfig: {
          ...account.platformConfig,
          brokerLoginEmail: saved.broker_account_identifier,
          brokerCredentialVaultRef: vaultRef,
          brokerReadAccess: 'false',
          realTradingBlocked: 'true',
        },
      })
      setXtbVaultStatus('saved')
      setXtbVaultMessage('Poświadczenie zapisano w zaszyfrowanym lokalnym vaulcie i podpięto do XTB demo. Następny krok: sprawdź read-only.')
    } catch (error) {
      setXtbVaultStatus('error')
      setXtbVaultMessage(error instanceof Error ? error.message : 'Nie udało się zapisać poświadczenia XTB demo.')
    } finally {
      setXtbVaultPassword('')
    }
  }

  const connectAlpacaPaper = async () => {
    const apiKeyId = alpacaKeyId.trim()
    const apiSecretKey = alpacaSecretKey.trim()
    if (!apiKeyId || !apiSecretKey) {
      setAlpacaConnectStatus('error')
      setAlpacaConnectMessage('Wpisz Paper API Key ID oraz Paper API Secret. Nie wpisuj hasla do panelu Alpaca.')
      return
    }
    if (!secretStorage?.isSecretStorageEnabled) {
      setAlpacaConnectStatus('error')
      setAlpacaConnectMessage('Lokalny vault sekretow nie jest gotowy. Klucze nie zostaly zapisane.')
      return
    }

    setAlpacaConnectStatus('saving')
    setAlpacaConnectMessage('Zapisuje klucze w lokalnym vaulcie...')
    try {
      const credential = await createAccountCredential({
        label: 'Alpaca Paper / primary',
        providerKey: 'alpaca-paper',
        accountIdentifier: 'alpaca-paper-primary',
        secretType: 'api_key',
        secretValue: JSON.stringify({ api_key_id: apiKeyId, api_secret_key: apiSecretKey }),
        riskLevel: 'high',
        allowedCapabilities: ['financial_read_later'],
        notes: 'Alpaca Paper: account, positions and IEX market data read-only. Live orders are forbidden.',
      })
      await configureAlpacaPaperAccount({ credentialAccountId: credential.id })
      const status = await fetchAlpacaPaperStatus()
      setAlpacaStatus(status)
      updatePlatformConfig({
        brokerName: 'Alpaca Paper',
        brokerPortalUrl: 'https://app.alpaca.markets/paper/dashboard/overview',
        brokerCredentialVaultRef: `account-vault:${credential.id}`,
        brokerAccountMode: 'demo_paper',
        brokerReadAccess: 'false',
        realTradingBlocked: 'true',
      })
      setAlpacaConnectStatus('idle')
      setAlpacaConnectMessage('Klucze zapisano bezpiecznie. Uruchom teraz test read-only.')
    } catch (error) {
      setAlpacaConnectStatus('error')
      setAlpacaConnectMessage(error instanceof Error ? error.message : 'Nie udalo sie podlaczyc Alpaca Paper.')
    } finally {
      setAlpacaKeyId('')
      setAlpacaSecretKey('')
    }
  }

  const verifyPortfolioAlpacaReadOnly = async () => {
    setAlpacaConnectStatus('verifying')
    setAlpacaConnectMessage('Sprawdzam konto paper, pozycje oraz ceny IEX...')
    try {
      await verifyAlpacaPaperReadOnly({ symbols: ['SPY', 'QQQ'], timeoutSeconds: 10 })
      const status = await fetchAlpacaPaperStatus()
      setAlpacaStatus(status)
      updatePlatformConfig({
        brokerReadAccess: status.read_only_verified ? 'true' : 'false',
        marketDataSources: mergeLines(
          String(account.platformConfig.marketDataSources ?? account.platformConfig.dataSources ?? ''),
          'alpaca_iex_quotes',
        ),
        realTradingBlocked: 'true',
      })
      setAlpacaConnectStatus('verified')
      setAlpacaConnectMessage('Alpaca Paper dziala. Atnos odczytal konto i ceny IEX bez mozliwosci wysylania zlecen.')
    } catch (error) {
      setAlpacaConnectStatus('error')
      setAlpacaConnectMessage(error instanceof Error ? error.message : 'Test Alpaca Paper nie powiodl sie.')
    }
  }

  const verifyPortfolioXtbReadOnly = async () => {
    setXtbCheckStatus('verifying')
    setXtbCheckMessage('')
    try {
      await verifyXtbReadOnly({ symbols: ['US500', 'EURUSD'], timeoutSeconds: 10 })
      const marketDataStatus = await fetchXtbMarketDataStatus()
      setXtbStatus(marketDataStatus)
      updatePlatformConfig({
        brokerReadAccess: marketDataStatus.read_only_verified ? 'true' : 'false',
        marketDataSources: mergeLines(
          String(account.platformConfig.marketDataSources ?? account.platformConfig.dataSources ?? ''),
          'xtb_demo_quotes',
        ),
      })
      setXtbCheckStatus('verified')
      setXtbCheckMessage('XTB demo read-only zweryfikowane. Atnos może pobierać ceny bez składania zleceń.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nie udało się zweryfikować XTB read-only.'
      setXtbCheckStatus('error')
      setXtbCheckMessage(message)
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Field label="Nazwa" value={account.name} onChange={(name) => updateAccount(account.id, { name })} />
      <Field label="Nisza" value={account.niche} onChange={(niche) => updateAccount(account.id, { niche })} />
      <Field
        label={identifierLabel}
        value={String(account.platformConfig.channelId ?? '')}
        onChange={(channelId) =>
          updateAccount(account.id, {
            platformConfig: { ...account.platformConfig, channelId },
          })
        }
      />
      <label className="block">
        <span className="font-mono text-[10px] uppercase text-text-tertiary">Tryb automatyzacji</span>
        <select
          value={account.automationMode}
          onChange={(event) => void updateContentAutomationMode(event.target.value as AutomationMode)}
          className="mt-1 w-full border border-border-default bg-void px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
        >
          <option value="manual">ręcznie</option>
          <option value="auto_private">auto prywatnie</option>
          <option value="auto_public">auto publicznie (wymaga zgody)</option>
        </select>
        {account.type === 'youtube' && automationSyncMessage ? (
          <span
            className={[
              'mt-2 block text-xs leading-5',
              automationSyncStatus === 'error' ? 'text-error' : automationSyncStatus === 'saved' ? 'text-success' : 'text-text-tertiary',
            ].join(' ')}
          >
            {automationSyncMessage}
          </span>
        ) : null}
      </label>
      <Field
        label="Końcówka API key"
        value={String(account.platformConfig.apiKeyLast4 ?? '****')}
        onChange={(apiKeyLast4) =>
          updateAccount(account.id, {
            platformConfig: { ...account.platformConfig, apiKeyLast4 },
          })
        }
      />
      <Field
        label={scheduleLabel}
        type="number"
        value={account.schedule.hourUtc}
        onChange={(hourUtc) =>
          updateAccount(account.id, {
            schedule: { ...account.schedule, hourUtc: Number(hourUtc) },
          })
        }
      />

      {(account.type === 'instagram_reels' || account.type === 'tiktok') ? (
        <div className="space-y-4 border border-accent-primary/30 bg-accent-primary/5 p-4 lg:col-span-2">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">
              Konfiguracja social short-form
            </div>
            <p className="mt-2 text-sm leading-6 text-text-secondary">
              Te pola przygotowują projekt pod Reels/TikTok. Automatyczny upload zostaje zależny od sesji browserowej,
              QA i trybu zatwierdzania.
            </p>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Field
              label="URL konta"
              value={String(account.platformConfig.accountUrl ?? '')}
              onChange={(accountUrl) => updatePlatformConfig({ accountUrl })}
            />
            <Field
              label="Browser session service"
              value={String(account.platformConfig.browserSessionService ?? '')}
              onChange={(browserSessionService) => updatePlatformConfig({ browserSessionService })}
            />
            <TextAreaField
              label="Zasady publikacji"
              value={String(account.platformConfig.publishPolicy ?? '')}
              placeholder="Np. tylko prywatny draft, bez publikacji bez QA i zgody"
              onChange={(publishPolicy) => updatePlatformConfig({ publishPolicy })}
            />
            <TextAreaField
              label="Notatki workflow"
              value={String(account.platformConfig.platformWorkflowNotes ?? '')}
              placeholder="Formaty, hashtagi, styl, ograniczenia platformy"
              onChange={(platformWorkflowNotes) => updatePlatformConfig({ platformWorkflowNotes })}
            />
          </div>
        </div>
      ) : null}

      {account.type === 'onlyfans' ? (
        <div className="space-y-4 border border-warning/30 bg-warning/5 p-4 lg:col-span-2">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.16em] text-warning">
              Synthetic creator / modelka
            </div>
            <p className="mt-2 text-sm leading-6 text-text-secondary">
              Ten projekt wymaga szczególnie twardych zasad: pełnoletniość, brak podszywania się pod realne osoby,
              brak publikacji bez review i jasne limity płatności.
            </p>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Field
              label="URL konta twórcy"
              value={String(account.platformConfig.accountUrl ?? '')}
              onChange={(accountUrl) => updatePlatformConfig({ accountUrl })}
            />
            <Field
              label="Ścieżka vault assetów"
              value={String(account.platformConfig.assetVaultPath ?? '')}
              onChange={(assetVaultPath) => updatePlatformConfig({ assetVaultPath })}
            />
            <ToggleField
              label="Konto twórcy podłączone"
              checked={account.platformConfig.creatorAccountConnected === 'true'}
              onChange={(checked) => updatePlatformConfig({ creatorAccountConnected: checked ? 'true' : 'false' })}
              detail="Zaznacz dopiero po zapisaniu sesji lub integracji konta."
            />
            <ToggleField
              label="Vault assetów podłączony"
              checked={account.platformConfig.vaultConnected === 'true'}
              onChange={(checked) => updatePlatformConfig({ vaultConnected: checked ? 'true' : 'false' })}
              detail="Vault powinien zawierać tylko zatwierdzone assety i referencje."
            />
            <TextAreaField
              label="Polityka treści"
              value={String(account.platformConfig.contentPolicy ?? '')}
              placeholder="Np. adult-only, bez realnych osób, bez publikacji publicznej bez akceptacji"
              onChange={(contentPolicy) => updatePlatformConfig({ contentPolicy })}
            />
            <ToggleField
              label="Reguły płatności skonfigurowane"
              checked={account.platformConfig.paymentRulesConfigured === 'true'}
              onChange={(checked) => updatePlatformConfig({ paymentRulesConfigured: checked ? 'true' : 'false' })}
              detail="Limity i zasady zakupów muszą być ustawione przed jakąkolwiek automatyzacją."
            />
          </div>
        </div>
      ) : null}

      {account.type === 'investment' ? (
        <div className="space-y-4 border border-warning/30 bg-warning/5 p-4 lg:col-span-2">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.16em] text-warning">
              Portfel inwestycyjny
            </div>
            <p className="mt-2 text-sm leading-6 text-text-secondary">
              Domyślny tryb jest research-only. Egzekucja transakcji pozostaje wyłączona bez osobnych limitów,
              zasad ryzyka i jawnej akceptacji.
            </p>
            <div className="mt-3 border border-warning/40 bg-void p-3 font-mono text-[11px] leading-5 text-warning">
              To nie jest panel YouTube OAuth. Dla portfolio podłączasz brokera, dane rynku, kursy dla Atnosa,
              limity ryzyka i budżet. Hasła oraz karta nie są zapisywane w localStorage — używaj referencji do vaulta.
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => void savePortfolioAlphaSettings()}
                disabled={investmentSettingsStatus === 'loading' || investmentSettingsStatus === 'saving'}
                className="border border-accent-primary bg-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-void disabled:cursor-not-allowed disabled:opacity-50"
              >
                {investmentSettingsStatus === 'saving' ? 'Zapisuję Portfolio Alpha' : 'Zapisz ustawienia Portfolio Alpha'}
              </button>
              <span
                className={[
                  'font-mono text-[10px] uppercase',
                  investmentSettingsStatus === 'error'
                    ? 'text-error'
                    : investmentSettingsStatus === 'saved'
                      ? 'text-success'
                      : 'text-text-tertiary',
                ].join(' ')}
              >
                {investmentSettingsStatus === 'loading'
                  ? 'Pobieram z backendu'
                  : investmentSettingsStatus === 'saving'
                    ? 'Zapis w toku'
                    : investmentSettingsStatus === 'saved'
                      ? 'Zapisano'
                      : investmentSettingsStatus === 'error'
                        ? 'Błąd'
                        : 'Backend sync'}
              </span>
            </div>
            {investmentSettingsMessage ? (
              <div
                className={[
                  'mt-3 border p-3 text-xs',
                  investmentSettingsStatus === 'error'
                    ? 'border-error/40 bg-error/10 text-error'
                    : 'border-border-default bg-void text-text-secondary',
                ].join(' ')}
              >
                {investmentSettingsMessage}
              </div>
            ) : null}
            <div className="mt-3 border border-accent-primary/50 bg-accent-primary/5 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-display text-[12px] font-semibold uppercase tracking-[0.14em] text-accent-primary">
                    Alpaca Paper API
                  </div>
                  <p className="mt-1 max-w-3xl text-[11px] leading-5 text-text-secondary">
                    Glowny broker testowy Atnosa. Odczyt konta, pozycji i cen IEX jest dozwolony. Wysylanie,
                    zmiana i anulowanie zlecen nie istnieja w adapterze.
                  </p>
                </div>
                <span
                  className={[
                    'border px-2 py-1 font-mono text-[9px] uppercase',
                    alpacaStatus?.read_only_verified
                      ? 'border-success/40 bg-success/10 text-success'
                      : alpacaConnectStatus === 'error'
                        ? 'border-error/40 bg-error/10 text-error'
                        : 'border-warning/40 bg-warning/10 text-warning',
                  ].join(' ')}
                >
                  {alpacaStatus?.read_only_verified
                    ? 'read-only verified'
                    : alpacaStatus?.registered
                      ? 'klucze zapisane'
                      : 'wymaga kluczy paper'}
                </span>
              </div>

              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field
                  label="Paper API Key ID"
                  type="password"
                  value={alpacaKeyId}
                  onChange={setAlpacaKeyId}
                />
                <Field
                  label="Paper API Secret"
                  type="password"
                  value={alpacaSecretKey}
                  onChange={setAlpacaSecretKey}
                />
              </div>
              <div className="mt-3 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => void connectAlpacaPaper()}
                  disabled={alpacaConnectStatus === 'saving' || !secretStorage?.isSecretStorageEnabled}
                  className="border border-accent-primary bg-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-void disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {alpacaConnectStatus === 'saving' ? 'Zapisuje vault' : 'Zapisz klucze paper'}
                </button>
                <button
                  type="button"
                  onClick={() => void verifyPortfolioAlpacaReadOnly()}
                  disabled={!alpacaStatus?.registered || alpacaConnectStatus === 'verifying'}
                  className="border border-success px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-success disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {alpacaConnectStatus === 'verifying' ? 'Sprawdzam API' : 'Sprawdz read-only'}
                </button>
              </div>
              <div className="mt-3 grid gap-1 font-mono text-[10px] uppercase text-text-tertiary sm:grid-cols-3">
                <span>Vault: {secretStorage?.isSecretStorageEnabled ? 'gotowy' : 'niedostepny'}</span>
                <span>Rynek: IEX Basic</span>
                <span>Real trading: zablokowany</span>
              </div>
              {alpacaConnectMessage ? (
                <div className={['mt-3 border p-3 text-xs', alpacaConnectStatus === 'error' ? 'border-error/40 bg-error/10 text-error' : 'border-border-default bg-void text-text-secondary'].join(' ')}>
                  {alpacaConnectMessage}
                </div>
              ) : null}
            </div>
            <div className="mt-3 grid gap-3 border border-border-default bg-void p-3 md:grid-cols-[1fr_auto]">
              <div>
                <div className="font-display text-[11px] uppercase tracking-[0.14em] text-text-primary">
                  XTB read-only market data
                </div>
                <div className="mt-2 grid gap-2 font-mono text-[10px] uppercase text-text-tertiary sm:grid-cols-3">
                  <span>Broker: {xtbStatus?.registered ? 'zarejestrowany' : 'brak'}</span>
                  <span>
                    Vault: {xtbStatus?.has_credential_ref ? 'podpięty' : 'brak'}
                  </span>
                  <span className={xtbApiRetired ? 'text-error' : xtbStatus?.read_only_verified ? 'text-success' : 'text-warning'}>
                    Read-only: {xtbApiRetired ? 'API wycofane' : xtbStatus?.read_only_verified ? 'verified' : 'niezweryfikowane'}
                  </span>
                </div>
                {xtbApiRetired ? (
                  <div className="mt-3 border border-error/40 bg-error/10 px-3 py-2 text-[11px] leading-5 text-error">
                    XTB wycofało publiczne xAPI {xtbStatus?.api_retired_at ?? '14.03.2025'}. Nie zapisuj hasła do tej integracji.
                    Konto demo pozostaje obsługiwane ręcznie w aplikacji XTB; paper trading wymaga osobnego,
                    wspieranego dostawcy danych read-only.
                  </div>
                ) : null}
                <div className="mt-2 text-[11px] leading-5 text-text-secondary">
                  Ostatni test:{' '}
                  {xtbStatus?.last_check?.created_at
                    ? `${xtbStatus.last_check.status} / ${xtbStatus.last_check.created_at}`
                    : 'brak'}
                </div>
                <div className="mt-3 border border-border-default bg-panel px-3 py-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-[10px] uppercase text-text-tertiary">
                      Źródło cen autopilota
                    </span>
                    <span className={['font-mono text-[10px] uppercase', xtbAutopilotPriceStatus.className].join(' ')}>
                      {xtbAutopilotPriceStatus.source}
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] leading-5 text-text-secondary">
                    {xtbAutopilotPriceStatus.detail}
                  </div>
                  <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                    Quote count: {xtbAutopilotPriceStatus.quoteSymbols.length}
                  </div>
                </div>
                {xtbCheckMessage ? (
                  <div className={['mt-2 text-xs', xtbCheckStatus === 'error' ? 'text-error' : 'text-success'].join(' ')}>
                    {xtbCheckMessage}
                  </div>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => void verifyPortfolioXtbReadOnly()}
                disabled={xtbApiRetired || xtbCheckStatus === 'verifying' || investmentSettingsStatus === 'loading'}
                className="border border-warning px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-warning disabled:cursor-not-allowed disabled:opacity-50"
              >
                {xtbApiRetired ? 'XTB API wycofane' : xtbCheckStatus === 'verifying' ? 'Sprawdzam XTB' : 'Sprawdź XTB read-only'}
              </button>
            </div>

            <div className="mt-4 border border-warning/40 bg-void p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-display text-[11px] uppercase tracking-[0.14em] text-warning">
                    Twarde limity paper tradingu
                  </div>
                  <p className="mt-1 text-[11px] leading-5 text-text-secondary">
                    Zapis trafia do backendu i jest sprawdzany przed kazda transakcja paper. Nie odblokowuje realnego tradingu.
                  </p>
                </div>
                <span
                  className={[
                    'font-mono text-[10px] uppercase',
                    riskPolicyStatus === 'error' || riskPolicy?.emergency_stop
                      ? 'text-error'
                      : riskPolicy?.configured
                        ? 'text-success'
                        : 'text-warning',
                  ].join(' ')}
                >
                  {riskPolicyStatus === 'loading'
                    ? 'odczyt backendu'
                    : riskPolicyStatus === 'saving'
                      ? 'zapis backendu'
                      : riskPolicyStatus === 'error'
                        ? 'brak odczytu backendu'
                        : riskPolicy?.emergency_stop
                          ? 'emergency stop aktywny'
                          : riskPolicy?.configured
                            ? 'backend egzekwuje'
                            : 'bezpieczne wartosci bazowe'}
                </span>
              </div>

              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field
                  label="Maks. pojedynczy paper trade (PLN)"
                  type="number"
                  value={String(account.platformConfig.riskMaxSinglePaperTradePln ?? riskPolicy?.max_single_paper_trade_pln ?? '')}
                  onChange={(riskMaxSinglePaperTradePln) => updatePlatformConfig({ riskMaxSinglePaperTradePln })}
                />
                <Field
                  label="Maks. wartosc pozycji paper (PLN)"
                  type="number"
                  value={String(account.platformConfig.riskMaxPaperPositionPln ?? riskPolicy?.max_paper_position_pln ?? '')}
                  onChange={(riskMaxPaperPositionPln) => updatePlatformConfig({ riskMaxPaperPositionPln })}
                />
                <Field
                  label="Dzienny limit zrealizowanej straty (PLN)"
                  type="number"
                  value={String(account.platformConfig.riskMaxDailyLossPln ?? riskPolicy?.max_daily_realized_loss_pln ?? '')}
                  onChange={(riskMaxDailyLossPln) => updatePlatformConfig({ riskMaxDailyLossPln })}
                />
                <Field
                  label="Tygodniowy limit zrealizowanej straty (PLN)"
                  type="number"
                  value={String(account.platformConfig.riskMaxWeeklyLossPln ?? riskPolicy?.max_weekly_realized_loss_pln ?? '')}
                  onChange={(riskMaxWeeklyLossPln) => updatePlatformConfig({ riskMaxWeeklyLossPln })}
                />
              </div>

              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <ToggleField
                  label="Awaryjne zatrzymanie paper tradingu"
                  checked={account.platformConfig.riskEmergencyStop === 'true'}
                  onChange={(checked) => updatePlatformConfig({ riskEmergencyStop: checked ? 'true' : 'false' })}
                  detail="Po zapisie backend zablokuje nowe transakcje paper i automatyczny run."
                />
                <div className="border border-border-default bg-panel px-3 py-3 text-[11px] leading-5 text-text-secondary">
                  <div className="font-mono text-[10px] uppercase text-text-tertiary">Stan obliczony z paper ledgera</div>
                  <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px]">
                    <span>Dzienny P/L</span>
                    <span className="text-text-primary">{riskMetrics ? `${riskMetrics.daily_realized_pnl_pln} PLN` : 'brak odczytu'}</span>
                    <span>Tygodniowy P/L</span>
                    <span className="text-text-primary">{riskMetrics ? `${riskMetrics.weekly_realized_pnl_pln} PLN` : 'brak odczytu'}</span>
                    <span>Wycena otwartych pozycji</span>
                    <span className="text-warning">niedostepna</span>
                  </div>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => void saveInvestmentRiskPolicy()}
                  disabled={riskPolicyStatus === 'loading' || riskPolicyStatus === 'saving'}
                  className="border border-warning px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-warning disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {riskPolicyStatus === 'saving' ? 'Zapisuje limity' : 'Zapisz i egzekwuj limity'}
                </button>
                <span className="font-mono text-[10px] uppercase text-text-tertiary">
                  Paper only: wymuszone | Real trading: zablokowany
                </span>
              </div>
              {riskPolicyMessage ? (
                <div className={['mt-3 border p-3 text-xs', riskPolicyStatus === 'error' ? 'border-error/40 bg-error/10 text-error' : 'border-border-default bg-panel text-text-secondary'].join(' ')}>
                  {riskPolicyMessage}
                </div>
              ) : null}
            </div>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Field
              label="Broker / platforma"
              value={String(account.platformConfig.brokerName ?? '')}
              onChange={(brokerName) => updatePlatformConfig({ brokerName })}
            />
            <Field
              label="URL brokera"
              value={String(account.platformConfig.brokerPortalUrl ?? '')}
              onChange={(brokerPortalUrl) => updatePlatformConfig({ brokerPortalUrl })}
            />
            <Field
              label="Login / e-mail konta"
              value={String(account.platformConfig.brokerLoginEmail ?? '')}
              onChange={(brokerLoginEmail) => updatePlatformConfig({ brokerLoginEmail })}
            />
            <Field
              label="Vault ref loginu i hasła"
              value={String(account.platformConfig.brokerCredentialVaultRef ?? '')}
              onChange={(brokerCredentialVaultRef) => updatePlatformConfig({ brokerCredentialVaultRef })}
            />
            <Field
              label="Tryb rachunku"
              value={String(account.platformConfig.brokerAccountMode ?? 'demo_paper')}
              onChange={(brokerAccountMode) => updatePlatformConfig({ brokerAccountMode })}
            />
            <ToggleField
              label="Broker read-only przygotowany"
              checked={account.platformConfig.brokerReadAccess === 'true'}
              onChange={(checked) => updatePlatformConfig({ brokerReadAccess: checked ? 'true' : 'false' })}
              detail="Zapisuje status kandydata do read-only. Prawdziwe verified nada dopiero test adaptera XTB."
            />
            {xtbApiRetired ? (
              <div className="border border-warning/50 bg-warning/10 p-3 text-[11px] leading-5 text-warning lg:col-span-2">
                Poświadczenie XTB nie jest wymagane: publiczne API brokera zostało wyłączone. Skonfiguruj osobny
                provider market-data; XTB Demo zachowaj jako ręczną aplikację do porównania wyników paper.
              </div>
            ) : (
            <div className="border border-border-default bg-panel p-3 lg:col-span-2">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-display text-[11px] uppercase tracking-[0.14em] text-text-primary">
                    Poświadczenie XTB Demo
                  </div>
                  <p className="mt-1 text-[11px] leading-5 text-text-secondary">
                    Hasło trafia wyłącznie do lokalnego zaszyfrowanego vaulta. Atnos dostaje tylko uprawnienie do danych rynkowych read-only; realne zlecenia pozostają zablokowane.
                  </p>
                </div>
                <span className={['border px-2 py-1 font-mono text-[9px] uppercase', secretStorage?.isSecretStorageEnabled ? 'border-success/40 bg-success/10 text-success' : 'border-error/40 bg-error/10 text-error'].join(' ')}>
                  {secretStorage?.isSecretStorageEnabled ? 'Vault gotowy' : 'Vault niedostępny'}
                </span>
              </div>
              <div className="mt-3 grid gap-3 md:grid-cols-[1fr_auto]">
                <Field
                  label="Hasło XTB demo"
                  type="password"
                  value={xtbVaultPassword}
                  onChange={setXtbVaultPassword}
                />
                <button
                  type="button"
                  onClick={() => void saveXtbDemoCredential()}
                  disabled={xtbVaultStatus === 'saving' || !secretStorage?.isSecretStorageEnabled}
                  className="self-end border border-warning px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-warning disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {xtbVaultStatus === 'saving' ? 'Zapisuję vault' : 'Zapisz i podłącz XTB demo'}
                </button>
              </div>
              <div className="mt-2 font-mono text-[10px] leading-5 text-text-tertiary">
                {secretStorage?.summary ?? 'Sprawdzam status lokalnego vaulta...'}
              </div>
              {xtbVaultMessage ? (
                <div className={['mt-3 border p-3 text-xs', xtbVaultStatus === 'error' ? 'border-error/40 bg-error/10 text-error' : 'border-border-default bg-void text-text-secondary'].join(' ')}>
                  {xtbVaultMessage}
                </div>
              ) : null}
            </div>
            )}
            <ToggleField
              label="Real trading zablokowany"
              checked={account.platformConfig.realTradingBlocked !== 'false'}
              onChange={(checked) => updatePlatformConfig({ realTradingBlocked: checked ? 'true' : 'false' })}
              detail="Ten przełącznik powinien zostać ON, dopóki strategia nie przejdzie paper tradingu i ręcznej zgody."
            />
            <TextAreaField
              label="Źródła danych"
              value={String(account.platformConfig.dataSources ?? '')}
              placeholder="Np. watchlisty, holdings, earnings calendar, macro events"
              onChange={(dataSources) => updatePlatformConfig({ dataSources })}
            />
            <TextAreaField
              label="Źródła market data"
              value={String(account.platformConfig.marketDataSources ?? '')}
              placeholder="Np. XTB read-only feed, stooq, earnings calendar, macro calendar"
              onChange={(marketDataSources) => updatePlatformConfig({ marketDataSources })}
            />
            <TextAreaField
              label="Notatki do polityki ryzyka (nie egzekwowane)"
              value={String(account.platformConfig.riskPolicy ?? '')}
              placeholder="Kontekst, zakazane instrumenty i zasady review. Twarde limity zapisuje panel powyżej."
              onChange={(riskPolicy) => updatePlatformConfig({ riskPolicy })}
            />
            <TextAreaField
              label="Watchlista"
              value={String(account.platformConfig.watchlist ?? '')}
              placeholder="Tickery, ETF-y, sektory, tezy inwestycyjne"
              onChange={(watchlist) => updatePlatformConfig({ watchlist })}
            />
            <TextAreaField
              label="Kursy i nauka dla Atnosa"
              value={String(account.platformConfig.learningResources ?? '')}
              placeholder="Linki do kursów, notatek, strategii, książek, playbooków i zasad analizy"
              onChange={(learningResources) => updatePlatformConfig({ learningResources })}
            />
            <Field
              label="Budżet miesięczny PLN"
              value={String(account.platformConfig.monthlySpendBudgetPln ?? '0')}
              onChange={(monthlySpendBudgetPln) => updatePlatformConfig({ monthlySpendBudgetPln })}
            />
            <Field
              label="Metoda płatności ref"
              value={String(account.platformConfig.paymentMethodRef ?? '')}
              onChange={(paymentMethodRef) => updatePlatformConfig({ paymentMethodRef })}
            />
            <ToggleField
              label="Metoda płatności skonfigurowana"
              checked={account.platformConfig.paymentMethodConfigured === 'true'}
              onChange={(checked) => updatePlatformConfig({ paymentMethodConfigured: checked ? 'true' : 'false' })}
              detail="Nie wpisuj numeru karty w aplikacji. Użyj refa z vaulta albo tokenu operatora płatności."
            />
            <ToggleField
              label="Reguły akceptacji transakcji"
              checked={account.platformConfig.tradeApprovalRules === 'true'}
              onChange={(checked) => updatePlatformConfig({ tradeApprovalRules: checked ? 'true' : 'false' })}
              detail="Bez tego Atnos może generować alerty i research, ale nie może egzekwować zleceń."
            />
          </div>
        </div>
      ) : null}

      {account.type === 'custom' ? (
        <div className="space-y-4 border border-border-default bg-void p-4 lg:col-span-2">
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">
            Projekt custom
          </div>
          <TextAreaField
            label="Wymagane dostępy"
            value={String(account.platformConfig.requiredAccess ?? '')}
            placeholder="Lista kont, API, sesji browserowych i zasad bezpieczeństwa"
            onChange={(requiredAccess) => updatePlatformConfig({ requiredAccess })}
          />
          <TextAreaField
            label="Notatki projektu"
            value={String(account.platformConfig.projectNotes ?? '')}
            placeholder="Co Atnos ma robić, czego nie wolno, jak mierzymy sukces"
            onChange={(projectNotes) => updatePlatformConfig({ projectNotes })}
          />
        </div>
      ) : null}

      {account.type === 'youtube' ? (
        <div className="space-y-4 border border-accent-primary/30 bg-accent-primary/5 p-4 lg:col-span-2">
          <details open>
            <summary className="cursor-pointer font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">
              Analiza danych
            </summary>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <Field
                label="Nisze"
                value={String(account.platformConfig.niches ?? account.niche)}
                onChange={(niches) =>
                  updateAccount(account.id, {
                    platformConfig: { ...account.platformConfig, niches },
                  })
                }
              />
              <Field
                label="Region"
                value={String(account.platformConfig.region ?? 'PL')}
                onChange={(region) =>
                  updateAccount(account.id, {
                    platformConfig: { ...account.platformConfig, region },
                  })
                }
              />
              <TextAreaField
                label="Kanały referencyjne"
                value={String(account.platformConfig.referenceChannels ?? '')}
                placeholder="https://www.youtube.com/@alux"
                onChange={(referenceChannels) =>
                  updateAccount(account.id, {
                    platformConfig: { ...account.platformConfig, referenceChannels },
                  })
                }
              />
              <TextAreaField
                label="Konkurencja"
                value={String(account.platformConfig.competitorChannels ?? '')}
                placeholder="Jeden URL na linię"
                onChange={(competitorChannels) =>
                  updateAccount(account.id, {
                    platformConfig: { ...account.platformConfig, competitorChannels },
                  })
                }
              />
              <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
                <button
                  type="button"
                  disabled={intelligenceStatus === 'running'}
                  onClick={() => void runIntelligence()}
                  className="border border-accent-primary bg-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-void disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Uruchom zbieranie danych
                </button>
                <div className="font-mono text-[10px] uppercase text-text-tertiary">
                  Status: <span className={STATUS_CLASS[intelligenceStatus]}>{getIntelligenceStatusCopy(intelligenceStatus)}</span>
                </div>
                <div className="font-mono text-[10px] uppercase text-text-tertiary">
                  Ostatnio:{' '}
                  <span className="text-text-secondary">
                    {String(account.platformConfig.intelligenceLastCollectedAt ?? 'brak')}
                  </span>
                </div>
              </div>
              {(intelligenceMessage || account.platformConfig.intelligenceLastMessage) ? (
                <div className="border border-border-default bg-void p-3 text-xs text-text-secondary lg:col-span-2">
                  {intelligenceMessage || String(account.platformConfig.intelligenceLastMessage)}
                </div>
              ) : null}
              <div className="grid gap-2 font-mono text-[10px] uppercase text-text-tertiary lg:col-span-2 lg:grid-cols-5">
                <span>YT: {String(account.platformConfig.youtubeTrendsLastCollectedAt ?? '-')}</span>
                <span>GTrends: {String(account.platformConfig.googleTrendsLastCollectedAt ?? '-')}</span>
                <span>Konkurencja: {String(account.platformConfig.competitorsLastCollectedAt ?? '-')}</span>
                <span>Własny: {String(account.platformConfig.ownChannelLastCollectedAt ?? '-')}</span>
                <span>Wzorce: {String(account.platformConfig.referenceLastCollectedAt ?? '-')}</span>
              </div>
              <ReferenceLearningPanel />
            </div>
          </details>
        </div>
      ) : null}
    </div>
  )
}

export function LegacyProjectSpecificAccessPanel({ account }: { account: Account }) {
  if (account.type === 'investment') {
    return (
      <div className="border border-warning/30 bg-warning/5 p-4 lg:col-span-2">
        <div className="font-display text-[12px] uppercase tracking-[0.16em] text-warning">
          Dostępy inwestycyjne
        </div>
        <p className="mt-2 text-sm leading-6 text-text-secondary">
          Dla `Portfolio Alpha` potrzebne są inne integracje niż dla YouTube: XTB demo/read-only, dane rynku,
          materiały edukacyjne, polityka ryzyka i budżet. YouTube OAuth jest ukryty, bo nie pomaga w tradingu.
        </p>
        <div className="mt-4 grid gap-3 lg:grid-cols-4">
          {[
            {
              label: 'XTB broker',
              value: account.platformConfig.brokerReadAccess === 'true' ? 'read-only OK' : 'do podpięcia',
            },
            {
              label: 'Vault ref',
              value: hasText(account.platformConfig.brokerCredentialVaultRef) ? 'zapisany' : 'brak',
            },
            {
              label: 'Market data',
              value: hasAnyText(account.platformConfig.marketDataSources, account.platformConfig.dataSources) ? 'ustawione' : 'brak',
            },
            {
              label: 'Real trading',
              value: account.platformConfig.realTradingBlocked === 'false' ? 'ryzyko: odblokowany' : 'zablokowany',
            },
          ].map((item) => (
            <div key={item.label} className="border border-border-default bg-void p-3">
              <div className="font-mono text-[10px] uppercase text-text-tertiary">{item.label}</div>
              <div className="mt-2 font-display text-[13px] uppercase tracking-[0.12em] text-text-primary">
                {item.value}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 border border-error/40 bg-error/10 p-3 font-mono text-[11px] leading-5 text-error">
          Atnos nie powinien przechowywać hasła XTB ani numeru karty w localStorage. Używaj tylko referencji:
          `brokerCredentialVaultRef` i `paymentMethodRef`. Live trading ma zostać zablokowany do czasu paper tradingu,
          limitów ryzyka i ręcznej zgody.
        </div>
      </div>
    )
  }

  return (
    <div className="border border-border-default bg-void p-4 lg:col-span-2">
      <div className="font-display text-[12px] uppercase tracking-[0.16em] text-text-primary">
        Dostępy projektu
      </div>
      <p className="mt-2 text-sm leading-6 text-text-secondary">
        Ten typ projektu nie używa YouTube OAuth. Podłącz tutaj tylko narzędzia, konta i vault refy potrzebne dla
        wybranego workflow.
      </p>
    </div>
  )
}

function InvestmentPipelineSettings({ account }: { account: Account }) {
  return (
    <div className="space-y-3 border border-warning/30 bg-warning/5 p-4 lg:col-span-2">
      <div className="font-display text-[12px] uppercase tracking-[0.16em] text-warning">
        Pipeline inwestycyjny
      </div>
      <p className="text-sm leading-6 text-text-secondary">
        Aktywny projekt: {account.name}. Ten pipeline nie generuje uploadu YouTube. Kolejność pracy to:
        dane rynku → research → paper decision journal → alert do właściciela.
      </p>
      <div className="grid gap-3 md:grid-cols-4">
        {[
          ['1', 'Market data read-only'],
          ['2', 'Analiza i scoring okazji'],
          ['3', 'Paper trade journal'],
          ['4', 'Gmail alert / review'],
        ].map(([index, label]) => (
          <div key={index} className="border border-border-default bg-void p-3">
            <div className="font-mono text-[10px] text-text-tertiary">KROK {index}</div>
            <div className="mt-2 text-sm text-text-primary">{label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function ProjectPipelineSettings({ account }: { account: Account | null }) {
  if (!account) return null

  if (account.type === 'investment') {
    return <InvestmentPipelineSettings account={account} />
  }

  if (account.type !== 'youtube') {
    return <LegacyProjectSpecificAccessPanel account={account} />
  }

  return (
    <div className="space-y-4">
      <div className="border border-warning/40 bg-warning/10 p-3 font-mono text-[11px] text-warning">
        Ten widok nie odczytał profilu produkcji ani wyboru modeli i głosu dla konkretnego zlecenia.
        Sprawdź zlecenie w Studio. Runtime pokazuje osobno dostępność usług.
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ReadOnlyPipelineField label="Model LLM" value="Nie wczytano wyboru autora i recenzenta" source="brak danych profilu z backendu" />
        <ReadOnlyPipelineField label="Model głosu" value="Nie wczytano wyboru głosu" source="brak danych profilu z backendu" />
        <ReadOnlyPipelineField label="Model wideo" value="Nie wczytano modelu i workflow" source="brak danych profilu z backendu" />
        <ReadOnlyPipelineField
          label="ComfyUI endpoint"
          value="Nie wczytano adresu usługi"
          source="brak danych profilu z backendu"
        />
        <ReadOnlyPipelineField label="Jakość wideo" value="Nie wczytano parametrów filmu" source="brak danych profilu z backendu" />
        <ReadOnlyPipelineField label="Równoległe joby" value="Nie wczytano ustawienia współbieżności" source="brak danych profilu z backendu" />
      </div>
      <div className="flex flex-wrap gap-4 text-sm text-accent-primary">
        <a href="/create" className="underline">Otwórz produkcje i wybierz zlecenie</a>
        <a href="/settings?area=general&tab=production-runtime" className="underline">Sprawdź Runtime produkcji</a>
      </div>
    </div>
  )
}

function YouTubeOAuthSettings({ account }: { account: Account }) {
  const backendAccountId = contentBackendAccountId(account)
  const [connector, setConnector] = useState<OAuthConnector | null>(null)
  const [channelReadiness, setChannelReadiness] = useState<ContentReadinessItem | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [clientSecretPath, setClientSecretPath] = useState('')

  const loadConnector = async () => {
    setLoading(true)
    try {
      const controlCenter = await fetchSettingsControlCenter()
      setConnector(
        controlCenter.oauthConnectors.find((item) => item.connectorKey === 'youtube-studio') ?? null,
      )
      setChannelReadiness(contentReadinessForAccount(controlCenter, backendAccountId))
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Nie udało się pobrać statusu OAuth.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadConnector()
  }, [backendAccountId])

  const connect = async () => {
    setLoading(true)
    setMessage(null)
    try {
      const result = await startOAuthConnection('youtube-studio', { contentAccountId: backendAccountId })
      if (result.authorizationUrl) {
        window.open(result.authorizationUrl, '_blank', 'popup,width=720,height=820')
      }
      setMessage('Waiting for YouTube authorization… Po zgodzie Google wróć tutaj i kliknij Odśwież status. Atnos nie wpisuje hasła ani kodu 2FA.')
      await loadConnector()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Nie udało się uruchomić OAuth YouTube.')
    } finally {
      setLoading(false)
    }
  }

  const probe = async () => {
    setLoading(true)
    setMessage(null)
    try {
      if (!channelReadiness?.accountId) {
        throw new Error('Ten kanal nie ma aktywnego mapowania OAuth do testu. Polacz go ponownie.')
      }
      await probeYouTubeConnector(channelReadiness.accountId)
      setMessage('Probe YouTube przeszedł poprawnie.')
      await loadConnector()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Probe YouTube nie przeszedł.')
    } finally {
      setLoading(false)
    }
  }

  const saveRuntimeConfig = async () => {
    setLoading(true)
    setMessage(null)
    try {
      const result = await configureYouTubeOAuthRuntime({
        clientId,
        clientSecret,
        redirectBaseUrl,
        enableOAuth: true,
      })
      setClientSecret('')
      setMessage(
        `Zapisano konfigurację OAuth. Secret ref: ${result.clientSecretRef ?? 'brak'}. Plik runtime: ${result.envPath}`,
      )
      await loadConnector()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Nie udało się zapisać konfiguracji OAuth.')
    } finally {
      setLoading(false)
    }
  }

  const importClientSecret = async () => {
    setLoading(true)
    setMessage(null)
    try {
      const result = await importYouTubeOAuthClientSecret({
        clientSecretPath,
        redirectBaseUrl,
        enableOAuth: true,
      })
      if (result.clientId) {
        setClientId(result.clientId)
      }
      setClientSecret('')
      setMessage(
        [
          `Zaimportowano Google client_secret.json. Redirect URI: ${result.redirectUri}.`,
          `Sekret zapisany w lokalnym AES vault jako ${result.clientSecretRef ?? 'brak refu'}.`,
          result.warnings.length ? `Ostrzeżenia: ${result.warnings.join(', ')}` : '',
        ]
          .filter(Boolean)
          .join(' '),
      )
      await loadConnector()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Nie udało się zaimportować client_secret.json.')
    } finally {
      setLoading(false)
    }
  }

  const status = connector?.connectionId
    ? 'POŁĄCZONE'
    : connector?.configured
      ? 'GOTOWE DO LOGOWANIA'
      : 'BRAK KONFIGURACJI'
  void status
  const channelStatus = channelReadiness?.connected
    ? 'POLACZONE'
    : channelReadiness?.needsAuth
      ? 'PONOWNE POLACZENIE'
      : connector?.configured
        ? 'KANAL NIEPOLACZONY'
        : 'BRAK KONFIGURACJI'
  const channelBlocker = channelReadiness?.blockingReasons[0] ?? null
  const setupWarnings = connector?.setupWarnings ?? []
  const needsRuntimeConfig =
    connector === null ||
    connector.configured === false ||
    setupWarnings.some((warning) =>
      /oauth is disabled|google oauth client id|google oauth client secret ref/i.test(warning),
    )
  const redirectBaseUrl = 'http://127.0.0.1:8000'
  const redirectUri = `${redirectBaseUrl}/oauth/youtube-studio/callback`

  return (
    <div className="border border-accent-primary/30 bg-accent-primary/5 p-4 lg:col-span-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">
            YouTube OAuth
          </div>
          <div className="mt-2 text-sm text-text-secondary">
            Wymagane do zbierania danych własnego kanału, uploadu prywatnego i analytics 24/48/72h.
          </div>
        </div>
        <span className="border border-border-default px-3 py-2 font-mono text-[10px] uppercase text-text-secondary">
          {loading ? 'SPRAWDZAM' : channelStatus}
        </span>
      </div>

      <div className="mt-3 border border-border-default bg-void p-3 text-xs text-text-secondary">
        <div>Cel kanalu: {account.name}</div>
        <div className="mt-1">Mapowanie OAuth: {channelReadiness?.accountLabel ?? 'brak'}</div>
        {channelBlocker ? <div className="mt-1 text-warning">Blokada: {channelBlocker}</div> : null}
      </div>

      {connector?.setupWarnings?.length ? (
        <div className="mt-4 border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          {connector.setupWarnings.map((warning) => (
            <div key={warning}>- {warning}</div>
          ))}
        </div>
      ) : null}

      {needsRuntimeConfig ? (
        <div className="mt-4 border border-border-default bg-void p-3">
          <div className="font-display text-[11px] uppercase tracking-[0.14em] text-accent-primary">
            Konfiguracja wymagana
          </div>
          <p className="mt-2 text-xs leading-5 text-text-secondary">
            Ten błąd nie oznacza, że pipeline jest zepsuty. Backend nie ma jeszcze danych Google OAuth,
            więc Atnos nie może pobrać YouTube Analytics ani uruchomić logowania kanału.
          </p>
          <div className="mt-3 grid min-w-0 gap-3 [&>*]:min-w-0 lg:grid-cols-2">
            <label className="block min-w-0 lg:col-span-2">
              <span className="font-mono text-[10px] uppercase text-text-tertiary">
                Ścieżka do Google client_secret.json
              </span>
              <input
                value={clientSecretPath}
                onChange={(event) => setClientSecretPath(event.target.value)}
                placeholder="Pozostaw puste, aby użyć bezpiecznej lokalizacji Atnosa"
                className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
              />
              <span className="mt-1 block text-[11px] text-text-tertiary">
                Najbezpieczniej pobierz plik OAuth z Google Cloud Console do prywatnego katalogu konfiguracji Atnosa. Puste pole użyje kanonicznej ścieżki backendu; sekret zostanie zaszyfrowany lokalnie i nie trafi do repozytorium.
              </span>
            </label>
            <button
              type="button"
              onClick={() => void importClientSecret()}
              disabled={loading}
              className="border border-accent-primary bg-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-void disabled:cursor-not-allowed disabled:opacity-50 lg:col-span-2"
            >
              Importuj client_secret.json
            </button>
            <label className="block min-w-0">
              <span className="font-mono text-[10px] uppercase text-text-tertiary">
                Google OAuth client ID
              </span>
              <input
                value={clientId}
                onChange={(event) => setClientId(event.target.value)}
                placeholder="...apps.googleusercontent.com"
                className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
              />
            </label>
            <label className="block min-w-0">
              <span className="font-mono text-[10px] uppercase text-text-tertiary">
                Google OAuth client secret
              </span>
              <input
                type="password"
                value={clientSecret}
                onChange={(event) => setClientSecret(event.target.value)}
                placeholder="Wklej tylko raz. Zostanie zaszyfrowany lokalnie."
                className="mt-1 w-full border border-border-default bg-panel px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
              />
            </label>
            <button
              type="button"
              onClick={() => void saveRuntimeConfig()}
              disabled={loading || !clientId.trim() || (!clientSecret.trim() && !connector?.configured)}
              className="border border-accent-primary bg-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-void disabled:cursor-not-allowed disabled:opacity-50 lg:col-span-2"
            >
              Zapisz konfigurację OAuth lokalnie
            </button>
            <div>
              <div className="font-mono text-[10px] uppercase text-text-tertiary">
                Zmienne runtime
              </div>
              <pre className="mt-1 overflow-x-auto border border-border-default bg-panel p-3 font-mono text-[11px] leading-5 text-text-primary">
{`OAUTH_ENABLED=true
OAUTH_REDIRECT_BASE_URL=${redirectBaseUrl}
OAUTH_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
OAUTH_GOOGLE_CLIENT_SECRET_REF=aes_256_gcm:...`}
              </pre>
            </div>
            <div>
              <div className="font-mono text-[10px] uppercase text-text-tertiary">
                Google Cloud Console
              </div>
              <div className="mt-1 border border-border-default bg-panel p-3 font-mono text-[11px] leading-5 text-text-primary">
                <div>Authorized redirect URI:</div>
                <div className="mt-1 break-all text-accent-primary">{redirectUri}</div>
                <div className="mt-3 text-text-tertiary">
                  Sekret klienta musi być zapisany w lokalnym secret vault pod refem{' '}
                  <span className="text-text-primary">aes_256_gcm:...</span>.
                  Frontend nie przechowuje sekretu; backend zapisuje go lokalnie w zaszyfrowanym vault.
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {message ? (
        <div className="mt-4 border border-border-default bg-void p-3 text-xs text-text-secondary">
          {message}
        </div>
      ) : null}

      {!channelReadiness?.connected ? (
        <div className="mt-4 border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          YouTube connection expired. Reconnect to restore analytics and comments.
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void connect()}
          disabled={loading || connector?.configured === false}
          className="border border-accent-primary bg-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-void disabled:cursor-not-allowed disabled:opacity-50"
        >
          {channelReadiness?.connected ? 'Połącz YouTube OAuth' : 'Reconnect YouTube'}
        </button>
        <button
          type="button"
          onClick={() => void probe()}
          disabled={loading}
          className="border border-border-default px-4 py-2 font-display text-[11px] uppercase tracking-[0.14em] text-text-secondary disabled:cursor-not-allowed disabled:opacity-50"
        >
          Testuj połączenie
        </button>
        <button
          type="button"
          onClick={() => void loadConnector()}
          disabled={loading}
          className="border border-border-default px-4 py-2 font-display text-[11px] uppercase tracking-[0.14em] text-text-secondary disabled:cursor-not-allowed disabled:opacity-50"
        >
          Odśwież status
        </button>
      </div>
    </div>
  )
}

function AccessReadinessPanel({ account }: { account: Account | null }) {
  const [controlCenter, setControlCenter] = useState<SettingsControlCenterPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [screenshotPath, setScreenshotPath] = useState('')

  const load = async () => {
    setLoading(true)
    setMessage('')
    try {
      setControlCenter(await fetchSettingsControlCenter())
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Nie udało się pobrać statusu dostępów.')
    } finally {
      setLoading(false)
    }
  }

  const testScreenshot = async () => {
    setLoading(true)
    setMessage('')
    try {
      const artifact = await captureLocalHostScreen('access-readiness')
      setScreenshotPath(artifact.path)
      setMessage(`Screenshot zapisany: ${artifact.path}`)
      setControlCenter(await fetchSettingsControlCenter())
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Nie udało się zrobić screenshota.')
    } finally {
      setLoading(false)
    }
  }

  const testMouseInput = async () => {
    setLoading(true)
    setMessage('')
    try {
      const result = await dispatchLocalHostInput({
        action: 'move_mouse',
        x: 32,
        y: 32,
      })
      const detail = result.stdout || result.command || 'Mouse input bridge responded.'
      setMessage(`Test myszy OK: ${detail}`)
      setControlCenter(await fetchSettingsControlCenter())
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Nie udało się przetestować sterowania myszą.'
      setMessage(detail)
    } finally {
      setLoading(false)
    }
  }

  const enableFullAccess = async () => {
    setLoading(true)
    setMessage('')
    try {
      const payload = await enableSettingsFullAccess()
      setControlCenter(payload)
      setMessage('Pełny profil dostępu włączony: browser control, external actions, scheduler, OAuth i trusted-auto.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Nie udało się włączyć pełnego profilu dostępu.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const localHost = controlCenter?.localHost
  const runtime = controlCenter?.runtime
  const scopedYouTube = scopedYouTubeConnectionState(controlCenter, account)
  const youtubeOauthConnected = scopedYouTube.connected
  const providersWithSecret = controlCenter?.providerCards.filter((provider) => provider.hasSecret).length ?? 0
  const readyProviders = controlCenter?.providerCards.filter((provider) => provider.readinessStatus === 'ready').length ?? 0
  const screenCaptureSubject = controlCenter?.accessReality?.subjects.find(
    (subject) => subject.subjectKey === 'local_host.screen_capture',
  )
  const screenCaptureReady = Boolean(
    screenshotPath ||
      screenCaptureSubject?.accessLevel === 'live_read' ||
      screenCaptureSubject?.accessLevel === 'live_write',
  )

  const checks: Array<{
    label: string
    ok: boolean
    verified: boolean
    detail: string
    source: string
    observedAt: string | null
  }> = [
    {
      label: 'Sterowanie myszą i klawiaturą',
      ok: Boolean(localHost?.desktopInputAvailable && localHost.executionEnabled),
      verified: Boolean(localHost?.desktopInputAvailable && localHost.executionEnabled),
      detail: localHost?.desktopInputAvailable
        ? localHost.executionEnabled
          ? 'Desktop input bridge działa.'
          : 'Most input istnieje, ale wykonanie lokalne jest wyłączone.'
        : 'Desktop input bridge niedostępny.',
      source: '/settings/control-center.localHost',
      observedAt: null,
    },
    {
      label: 'Widzenie ekranu',
      ok: screenCaptureReady,
      verified: Boolean(screenshotPath),
      detail: screenshotPath || screenCaptureSubject?.summary || 'Kliknij test screenshota, żeby potwierdzić live capture.',
      source: screenshotPath ? 'live local-host screenshot artifact' : '/settings/control-center.accessReality',
      observedAt: null,
    },
    {
      label: 'Browser automation',
      ok: Boolean(localHost?.browserRecipeAvailable || controlCenter?.trustedAuto.browserRuntime?.ready),
      verified: Boolean(controlCenter?.trustedAuto.browserRuntime?.ready),
      detail:
        controlCenter?.trustedAuto.browserRuntime?.statusReason ??
        (localHost?.browserRecipeAvailable ? 'Browser recipes gotowe.' : 'Browser runtime niepotwierdzony.'),
      source: '/settings/control-center.trustedAuto.browserRuntime',
      observedAt: null,
    },
    account?.type === 'youtube'
      ? {
          label: 'YouTube OAuth / konta Google',
          ok: youtubeOauthConnected,
          verified: scopedYouTube.verified,
          detail: youtubeOauthConnected
            ? `YouTube Studio jest połączone dokładnie z aktywnym kontem ${contentBackendAccountId(account)}.`
            : `Brak mapowania OAuth dla aktywnego konta ${contentBackendAccountId(account)}. Globalne połączenie innego kanału nie jest zaliczane.`,
          source: scopedYouTube.source,
          observedAt: scopedYouTube.observedAt,
        }
      : {
          label: account?.type === 'investment' ? 'Dostępy inwestycyjne' : 'Dostępy projektu',
          ok: false,
          verified: false,
          detail:
            account?.type === 'investment'
              ? 'Portfolio nie używa YouTube OAuth. Gotowość potwierdza wyłącznie adapter broker demo/read-only, market-data i polityka ryzyka w ustawieniach projektu.'
              : 'Brak projektowego dowodu dostępu. Globalne konektory nie są przypisywane automatycznie.',
          source: account?.type === 'investment'
            ? '/api/investment/* (weryfikacja w ustawieniach projektu)'
            : 'brak projektowego źródła',
          observedAt: null,
        },
    {
      label: 'Klucze API providerów',
      ok: providersWithSecret > 0 || readyProviders > 0,
      verified: readyProviders > 0,
      detail: `${providersWithSecret} sekretów API w vault, ${readyProviders} providerów gotowych.`,
      source: '/settings/control-center.providerCards',
      observedAt: null,
    },
    {
      label: 'Tryb autonomii',
      ok: runtime?.autonomyMode === 'trusted_auto' && runtime.browserControlEnabled && runtime.externalActionsEnabled,
      verified: Boolean(runtime),
      detail: runtime
        ? `autonomia=${runtime.autonomyMode}, browser=${runtime.browserControlEnabled ? 'on' : 'off'}, akcje=${runtime.externalActionsEnabled ? 'on' : 'off'}`
        : 'Brak danych runtime.',
      source: '/settings/control-center.runtime',
      observedAt: null,
    },
  ]

  return (
    <div className="space-y-4">
      <div className="border border-border-default bg-void p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary">
              Dostępy Atnosa
            </div>
            <p className="mt-2 text-sm leading-6 text-text-secondary">
              Ten panel pokazuje, czy Atnos może realnie widzieć ekran, klikać, korzystać z przeglądarki,
              dostępy projektu, lokalny runtime i zapisane lokalnie klucze API. Hasła i tokeny nie są wyświetlane.
              Dla portfolio inwestycyjnego YouTube OAuth jest celowo ukryty.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="border border-border-default px-4 py-2 font-display text-[11px] uppercase tracking-[0.14em] text-text-secondary disabled:opacity-50"
            >
              Odśwież
            </button>
            <button
              type="button"
              onClick={() => void testScreenshot()}
              disabled={loading}
              className="border border-accent-primary bg-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-void disabled:opacity-50"
            >
              Test screenshot
            </button>
            <button
              type="button"
              onClick={() => void testMouseInput()}
              disabled={loading}
              className="border border-accent-primary px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-accent-primary disabled:opacity-50"
            >
              Test myszy
            </button>
            <button
              type="button"
              onClick={() => void enableFullAccess()}
              disabled={loading}
              className="border border-success bg-success px-4 py-2 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-void disabled:opacity-50"
            >
              Włącz pełny dostęp
            </button>
          </div>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {checks.map((check) => (
          <div
            key={check.label}
            className={[
              'border p-4',
              check.verified
                ? 'border-success/40 bg-success/10'
                : check.ok
                  ? 'border-warning/40 bg-warning/10'
                  : 'border-error/40 bg-error/10',
            ].join(' ')}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="font-display text-[12px] uppercase tracking-[0.14em] text-text-primary">
                {check.label}
              </div>
              <span
                className={[
                  'font-mono text-[10px] uppercase',
                  check.verified ? 'text-success' : check.ok ? 'text-warning' : 'text-error',
                ].join(' ')}
              >
                {check.verified ? 'ZWERYFIKOWANE' : check.ok ? 'SKONFIGUROWANE' : 'NIEZWERYFIKOWANE'}
              </span>
            </div>
            <div className="mt-2 break-words font-mono text-[11px] leading-5 text-text-secondary">
              {check.detail}
            </div>
            <div className="mt-2 break-words font-mono text-[9px] text-text-tertiary">
              Źródło: {check.source}
              {check.observedAt ? ` · Odczyt: ${check.observedAt}` : ''}
            </div>
          </div>
        ))}
      </div>

      {controlCenter ? (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="border border-border-default bg-void p-4">
            <div className="font-display text-[12px] uppercase tracking-[0.14em] text-text-primary">
              Local host
            </div>
            <div className="mt-3 space-y-2 font-mono text-[11px] text-text-secondary">
              <div>Użytkownik: {controlCenter.localHost.currentUser}</div>
              <div>PowerShell: {controlCenter.localHost.powershellAvailable ? 'tak' : 'nie'}</div>
              <div>Mysz/klawiatura: {controlCenter.localHost.desktopInputAvailable ? 'tak' : 'nie'}</div>
              <div>Playwright: {controlCenter.localHost.playwrightAdapterAvailable ? 'tak' : 'nie'}</div>
              <div>Log: {controlCenter.localHost.commandLogPath}</div>
            </div>
          </div>
          {account?.type === 'youtube' ? (
            <div className="border border-border-default bg-void p-4">
              <div className="font-display text-[12px] uppercase tracking-[0.14em] text-text-primary">
                OAuth aktywnego projektu
              </div>
              <div className="mt-3 space-y-2 font-mono text-[11px] text-text-secondary">
                <div>
                  YouTube Studio ({contentBackendAccountId(account)}):{' '}
                  {youtubeOauthConnected ? 'zweryfikowane mapowanie' : 'brak mapowania aktywnego konta'}
                </div>
                <div className="text-text-tertiary">
                  Źródło: {scopedYouTube.source}
                  {scopedYouTube.observedAt ? ` · Odczyt: ${scopedYouTube.observedAt}` : ''}
                </div>
                {controlCenter.oauthConnectors
                  .filter((connector) => connector.connectorKey !== 'youtube-studio')
                  .map((connector) => (
                    <div key={connector.connectorKey}>
                      {connector.displayName}: {connector.connectionId ? 'połączenie globalne' : connector.configured ? 'skonfigurowane' : 'brak'}
                    </div>
                  ))}
              </div>
            </div>
          ) : (
            <div className="border border-warning/40 bg-warning/10 p-4">
              <div className="font-display text-[12px] uppercase tracking-[0.14em] text-warning">
                YouTube OAuth nie dotyczy tego projektu
              </div>
              <div className="mt-3 text-sm leading-6 text-text-secondary">
                Aktywny projekt: {account?.name ?? 'brak'}. Dla inwestycji konfiguruj brokera, vault ref,
                market-data, kursy i politykę ryzyka w zakładce Konta/Pipeline, zamiast konektorów YouTube.
              </div>
            </div>
          )}
          <div className="border border-border-default bg-void p-4">
            <div className="font-display text-[12px] uppercase tracking-[0.14em] text-text-primary">
              Providerzy API
            </div>
            <div className="mt-3 space-y-2 font-mono text-[11px] text-text-secondary">
              {controlCenter.providerCards.map((provider) => (
                <div key={provider.providerKey}>
                  {provider.displayName}: {provider.hasSecret ? 'secret zapisany' : provider.readinessStatus}
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {message ? (
        <div className="border border-border-default bg-void p-3 text-xs text-text-secondary">
          {message}
        </div>
      ) : null}
    </div>
  )
}

export default function ContentMachineSettingsRoute({
  embedded = false,
  initialTab,
}: {
  embedded?: boolean
  initialTab?: Tab
} = {}) {
  const [tab, setTab] = useState<Tab>(initialTab ?? 'accounts')
  const accounts = useAccountStore((state) => state.accounts)
  const activeAccountId = useAccountStore((state) => state.activeAccountId)
  const account = accounts.find((item) => item.id === activeAccountId) ?? accounts[0]

  useEffect(() => {
    if (initialTab) {
      setTab(initialTab)
    }
  }, [initialTab])

  return (
    <div
      className={
        embedded
          ? 'box-border w-full min-w-0 max-w-full overflow-x-hidden'
          : 'box-border w-full min-w-0 max-w-[100vw] overflow-x-hidden p-4'
      }
    >
      <section className="w-full min-w-0 max-w-full border border-border-default bg-panel">
        <div className="flex min-w-0 max-w-full flex-wrap items-center justify-between gap-3 border-b border-border-default px-4 py-3">
          <div className="min-w-0 max-w-full">
            <div className="font-display text-[12px] uppercase tracking-[0.18em] text-text-tertiary">
              {embedded ? 'Produkcja' : 'Ustawienia'}
            </div>
            <div className="mt-1 font-display text-xl font-semibold uppercase tracking-[0.1em] text-text-primary">
              {embedded ? 'Konta i pipeline kanałów' : 'Ustawienia Atnosa'}
            </div>
          </div>
          <div
            role="tablist"
            aria-label="Sekcje ustawień produkcji"
            data-testid="settings-tab-list"
            className="scroll-chrome flex w-full min-w-0 max-w-full snap-x snap-mandatory gap-2 overflow-x-auto overscroll-x-contain pb-2 [scrollbar-gutter:stable] sm:w-auto sm:flex-wrap sm:justify-end sm:overflow-x-visible sm:pb-0"
          >
            {TABS.map((item, index) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                onKeyDown={(event) => {
                  let nextIndex: number | null = null
                  if (event.key === 'ArrowRight') nextIndex = (index + 1) % TABS.length
                  if (event.key === 'ArrowLeft') nextIndex = (index - 1 + TABS.length) % TABS.length
                  if (event.key === 'Home') nextIndex = 0
                  if (event.key === 'End') nextIndex = TABS.length - 1
                  if (nextIndex == null) return

                  event.preventDefault()
                  setTab(TABS[nextIndex].id)
                  const tabButtons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
                    '[role="tab"]',
                  )
                  tabButtons?.[nextIndex]?.focus()
                }}
                role="tab"
                aria-selected={tab === item.id}
                tabIndex={tab === item.id ? 0 : -1}
                className={[
                  'shrink-0 snap-start border px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary',
                  tab === item.id
                    ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                    : 'border-border-default text-text-secondary',
                ].join(' ')}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-w-0 max-w-full overflow-x-hidden p-4">
          {account ? (
            <OperationalControlPanel
              account={account}
              onOpenAccess={() => setTab('access')}
              onOpenAccount={() => setTab('accounts')}
            />
          ) : null}

          {tab === 'accounts' && account ? (
            <div className="space-y-4">
              <div className="border border-warning/40 bg-warning/10 p-3 font-mono text-[11px] text-warning">
                Klucze i tokeny są maskowane. Frontend ich nie przechowuje; backend zapisuje sekrety lokalnie w AES vault.
              </div>
              <ProjectReadinessBoard accounts={accounts} activeAccountId={activeAccountId} />
              <ProjectSpecificAccessPanel account={account} />
              <AccountSettings account={account} />
              {account.type === 'youtube' ? <YouTubeOAuthSettings account={account} /> : null}
            </div>
          ) : null}

          {tab === 'pipeline' ? (
            <div className="space-y-4">
              <ProviderDoctorPanel accountId={account?.id} />
              <ProjectPipelineSettings account={account ?? null} />
            </div>
          ) : null}

          {tab === 'monetization' ? (
            <div className="grid gap-4 lg:grid-cols-3">
              {['AdSense', 'Linki afiliacyjne', 'Sponsorzy CRM'].map((label) => (
                <div key={label} className="min-h-40 border border-border-default bg-void p-4">
                  <div className="font-display text-[12px] uppercase tracking-[0.14em] text-text-primary">
                    {label}
                  </div>
                  <div className="mt-3 font-mono text-[11px] text-text-tertiary">
                    Powierzchnia konfiguracji. Realna integracja backendowa nie jest częścią shell v1.
                  </div>
                </div>
              ))}
            </div>
          ) : null}

          {tab === 'automation' ? (
            <div className="space-y-4">
              <div className="border border-error/40 bg-error/10 p-3 font-mono text-[11px] text-error">
                `auto_public` jest widoczne jako docelowy tryb, ale frontend shell v1 nie publikuje realnie.
              </div>
              {account ? <AccountSettings account={account} /> : null}
            </div>
          ) : null}

          {tab === 'runtime' ? (
            <div className="space-y-4">
              <MediaObservabilityPanel />
              <RuntimeAiDelegationPanel />
            </div>
          ) : null}

          {tab === 'access' ? <AccessReadinessPanel account={account ?? null} /> : null}
        </div>
      </section>
    </div>
  )
}

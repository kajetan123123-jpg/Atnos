import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { BrainCircuit, Database, PlugZap, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { OperationalMemoryPanel } from '../components/OperationalMemoryPanel'
import { SettingsProviderCard } from '../components/SettingsProviderCard'
import {
  PROVIDERS,
  readPersistedProviderConfig,
  writePersistedProviderConfig,
  type PersistedProviderConfig,
  type ProviderId,
} from '../lib/providers'
import {
  confirmJarvisBrowserSessionManualLogin,
  delegateAiWorkerPlan,
  delegateAiWorkerTask,
  disconnectOAuthConnection,
  fetchAiWorkerTasks,
  fetchAiWorkersStatus,
  fetchJarvisBrowserSessions,
  fetchProviderCards,
  fetchRuntimeInventory,
  fetchRuntimeHealth,
  fetchSettingsControlCenter,
  focusLocalHostDesktopApp,
  openLocalHostDesktopApp,
  openLocalHostBrowserUrl,
  probeAiWorker,
  probeGitHubConnector,
  probeGmailConnector,
  probeGoogleCalendarConnector,
  probeTrustedAutoAccounts,
  probeYouTubeConnector,
  runAiWorkerTask,
  startJarvisBrowserSessionManualLogin,
  startJarvisBrowserSessionManualLoginForUrl,
  startOAuthConnection,
  testProviderCard,
  updateProviderCard,
  updateSettingsRuntime,
  upsertTrustedAutoAccount,
  type AiWorkerTask,
  type AiWorkersStatusPayload,
  type RuntimeHealthPayload,
  type RuntimeInventoryPayload,
} from '../lib/api'
import { useChatStore } from '../stores/chatStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import type { SettingsControlCenterPayload, SettingsDecisionLogEntry } from '../types/settings'
import type {
  ConnectionState,
  ContentReadinessItem,
  JarvisBrowserSessionSummary,
  LocalHostDesktopApp,
  ProviderCard,
  TrustedAutoAccount,
  TrustedAutoAccountFeedback,
} from '../types/system'

type SectionKey =
  | 'load'
  | 'runtime'
  | 'trusted:feedback'
  | 'trusted:account:new'
  | `trusted:account:${number}`
  | `browser:start:${string}`
  | `browser:confirm:${string}`
  | `local-host:open:${string}`
  | `local-host:focus:${string}`
  | `oauth:${string}`
  | `provider:${string}`
  | `ai-worker:${string}`

type BrowserLoginTarget = {
  service: string
  displayName: string
  loginUrl: string
  validationUrl: string
}

const CORE_PROVIDER_ORDER: ProviderId[] = ['ollama', 'openai', 'anthropic', 'gemini']

type AccountUrlSuggestion = BrowserLoginTarget & {
  providerKey: string
  linkedIntegrationKey: string
  secretType: string
  allowedCapabilitiesText: string
  notes: string
  connectorMappingJsonText: string
}

type TrustedAccountDraft = {
  label: string
  purpose: string
  providerKey: string
  accountIdentifier: string
  secretType: string
  secretValue: string
  linkedProjectId: string
  linkedIntegrationKey: string
  notes: string
  allowedCapabilitiesText: string
  loginIdentifier: string
  passwordValue: string
  totpValue: string
  backupCodesValue: string
  recoveryEmail: string
  recoveryEmailSecretValue: string
  connectorMappingJsonText: string
}

const PROVIDER_OPTIONS = [
  { value: 'youtube', label: 'YouTube' },
  { value: 'youtube-studio', label: 'YouTube Studio' },
  { value: 'int-mail', label: 'INT mail' },
  { value: 'gmail', label: 'Gmail' },
  { value: 'google', label: 'Google' },
  { value: 'github', label: 'GitHub' },
  { value: 'chatgpt', label: 'ChatGPT' },
  { value: 'claude', label: 'Claude' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'videdge', label: 'VidEdge' },
  { value: 'facelessreels', label: 'FacelessReels' },
  { value: 'zebracat', label: 'Zebracat' },
  { value: 'runway', label: 'Runway' },
  { value: 'luma', label: 'Luma' },
  { value: 'klap', label: 'Klap' },
  { value: 'submagic', label: 'Submagic' },
  { value: 'canva', label: 'Canva' },
  { value: 'capcut', label: 'CapCut' },
  { value: 'custom', label: 'Inna aplikacja' },
] as const

const SECRET_TYPE_OPTIONS = [
  { value: 'oauth_token_later', label: 'OAuth token pozniej' },
  { value: 'session_cookie_later', label: 'Sesja browserowa pozniej' },
  { value: 'api_key', label: 'API key' },
  { value: 'password', label: 'Haslo / sekret' },
] as const

const ACCOUNT_URL_RULES: Array<{
  service: string
  displayName: string
  providerKey: string
  linkedIntegrationKey: string
  loginUrl: string
  validationUrl: string
  hostFragments: string[]
  allowedCapabilitiesText: string
  notes: string
}> = [
  {
    service: 'youtube',
    displayName: 'YouTube Studio',
    providerKey: 'youtube',
    linkedIntegrationKey: 'youtube',
    loginUrl: 'https://studio.youtube.com/',
    validationUrl: 'https://studio.youtube.com/',
    hostFragments: ['studio.youtube.com', 'youtube.com', 'youtu.be'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, connector_read, supervised_publish',
    notes: 'YouTube przez browser session jest fallbackiem. Publikacja zawsze wymaga approval.',
  },
  {
    service: 'int-mail',
    displayName: 'INT mail',
    providerKey: 'int-mail',
    linkedIntegrationKey: 'int_mail_browser_session',
    loginUrl: 'https://int.pl/',
    validationUrl: 'https://int.pl/',
    hostFragments: ['int.pl', 'poczta.int.pl'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, read_metadata, read_content',
    notes:
      'Konto pocztowe Atnosa. Haslo wpisuj wylacznie w Settings/Vault; jesli bylo wklejone w chat, najpierw je zmien.',
  },
  {
    service: 'gmail',
    displayName: 'Gmail',
    providerKey: 'gmail',
    linkedIntegrationKey: 'gmail',
    loginUrl: 'https://mail.google.com/',
    validationUrl: 'https://mail.google.com/',
    hostFragments: ['mail.google.com', 'gmail.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, connector_read',
    notes: 'Gmail przez reczne logowanie browserowe. Dla OAuth nie wpisuj hasla w embed browserze.',
  },
  {
    service: 'google-calendar',
    displayName: 'Google Calendar',
    providerKey: 'google',
    linkedIntegrationKey: 'google_calendar',
    loginUrl: 'https://calendar.google.com/',
    validationUrl: 'https://calendar.google.com/',
    hostFragments: ['calendar.google.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, connector_read',
    notes: 'Google Calendar przez reczna sesje browserowa.',
  },
  {
    service: 'github',
    displayName: 'GitHub',
    providerKey: 'github',
    linkedIntegrationKey: 'github',
    loginUrl: 'https://github.com/login',
    validationUrl: 'https://github.com/',
    hostFragments: ['github.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, connector_read',
    notes: 'GitHub przez browser session. Operacje zapisu wymagaja oddzielnego grantu.',
  },
  {
    service: 'chatgpt',
    displayName: 'ChatGPT',
    providerKey: 'chatgpt',
    linkedIntegrationKey: 'chatgpt_ui_bridge',
    loginUrl: 'https://chatgpt.com/',
    validationUrl: 'https://chatgpt.com/',
    hostFragments: ['chatgpt.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, prompt_to_model',
    notes: 'ChatGPT jako UI bridge. Auto-send pozostaje wylaczony.',
  },
  {
    service: 'claude',
    displayName: 'Claude',
    providerKey: 'claude',
    linkedIntegrationKey: 'claude_ui_bridge',
    loginUrl: 'https://claude.ai/login',
    validationUrl: 'https://claude.ai/new',
    hostFragments: ['claude.ai'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, prompt_to_model',
    notes: 'Claude jako browser quality gate, nie core backend.',
  },
  {
    service: 'gemini',
    displayName: 'Gemini',
    providerKey: 'gemini',
    linkedIntegrationKey: 'gemini_ui_bridge',
    loginUrl: 'https://gemini.google.com/',
    validationUrl: 'https://gemini.google.com/app',
    hostFragments: ['gemini.google.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, prompt_to_model',
    notes: 'Gemini przez browser session. Google moze wymagac zewnetrznego okna logowania.',
  },
  {
    service: 'videdge',
    displayName: 'VidEdge',
    providerKey: 'videdge',
    linkedIntegrationKey: 'videdge_browser_recipe',
    loginUrl: 'https://videdge.ai/',
    validationUrl: 'https://videdge.ai/',
    hostFragments: ['videdge.ai'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, research, script, thumbnails',
    notes: 'VidEdge jako browser-only recipe do researchu shortow.',
  },
  {
    service: 'facelessreels',
    displayName: 'FacelessReels',
    providerKey: 'facelessreels',
    linkedIntegrationKey: 'facelessreels_browser_recipe',
    loginUrl: 'https://www.facelessreels.com/',
    validationUrl: 'https://www.facelessreels.com/',
    hostFragments: ['facelessreels.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, prompt_to_video',
    notes: 'FacelessReels jako browser-only recipe. Bez wydawania pieniedzy i publikacji bez approval.',
  },
  {
    service: 'zebracat',
    displayName: 'Zebracat',
    providerKey: 'zebracat',
    linkedIntegrationKey: 'zebracat_api',
    loginUrl: 'https://www.zebracat.ai/',
    validationUrl: 'https://www.zebracat.ai/',
    hostFragments: ['zebracat.ai'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, api_call, video_generation',
    notes: 'Zebracat przez browser lub API. Koszty i API key wymagaja approval.',
  },
  {
    service: 'runway',
    displayName: 'Runway',
    providerKey: 'runway',
    linkedIntegrationKey: 'runway_browser_recipe',
    loginUrl: 'https://app.runwayml.com/login',
    validationUrl: 'https://app.runwayml.com/',
    hostFragments: ['runwayml.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, confirmed_write_later',
    notes: 'Runway jako opcjonalny browser provider do generowania wideo. Koszty i eksport wymagaja kontroli uzytkownika.',
  },
  {
    service: 'luma',
    displayName: 'Luma',
    providerKey: 'luma',
    linkedIntegrationKey: 'luma_browser_recipe',
    loginUrl: 'https://lumalabs.ai/dream-machine',
    validationUrl: 'https://lumalabs.ai/dream-machine',
    hostFragments: ['luma.ai', 'lumalabs.ai'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, video_generation',
    notes: 'Luma jako opcjonalny browser provider.',
  },
  {
    service: 'klap',
    displayName: 'Klap',
    providerKey: 'klap',
    linkedIntegrationKey: 'klap_browser_recipe',
    loginUrl: 'https://klap.app/',
    validationUrl: 'https://klap.app/',
    hostFragments: ['klap.app'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, confirmed_write_later',
    notes: 'Klap jako narzedzie do klipow. Publikacja, eksport platny i upload wymagaja approval.',
  },
  {
    service: 'submagic',
    displayName: 'Submagic',
    providerKey: 'submagic',
    linkedIntegrationKey: 'submagic_browser_recipe',
    loginUrl: 'https://app.submagic.co/',
    validationUrl: 'https://app.submagic.co/',
    hostFragments: ['submagic.co'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, confirmed_write_later',
    notes: 'Submagic jako narzedzie do napisow i klipow. Browser-only, bez automatycznej publikacji.',
  },
  {
    service: 'canva',
    displayName: 'Canva',
    providerKey: 'canva',
    linkedIntegrationKey: 'canva_browser_recipe',
    loginUrl: 'https://www.canva.com/login',
    validationUrl: 'https://www.canva.com/',
    hostFragments: ['canva.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, design_generation',
    notes: 'Canva jako narzedzie do assetow.',
  },
  {
    service: 'capcut',
    displayName: 'CapCut',
    providerKey: 'capcut',
    linkedIntegrationKey: 'capcut_browser_recipe',
    loginUrl: 'https://www.capcut.com/login',
    validationUrl: 'https://www.capcut.com/editor',
    hostFragments: ['capcut.com'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, video_editing',
    notes: 'CapCut jako browser recipe do montazu.',
  },
  {
    service: 'ideogram',
    displayName: 'Ideogram',
    providerKey: 'custom',
    linkedIntegrationKey: 'ideogram_browser_recipe',
    loginUrl: 'https://ideogram.ai/login',
    validationUrl: 'https://ideogram.ai/t/explore',
    hostFragments: ['ideogram.ai'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, image_generation',
    notes: 'Ideogram jako browser session do assetow.',
  },
  {
    service: 'veed',
    displayName: 'VEED',
    providerKey: 'custom',
    linkedIntegrationKey: 'veed_browser_recipe',
    loginUrl: 'https://www.veed.io/login',
    validationUrl: 'https://www.veed.io/dashboard',
    hostFragments: ['veed.io'],
    allowedCapabilitiesText: 'trusted_login, browser_workflow, video_editing',
    notes: 'VEED jako browser recipe do wideo.',
  },
]

function emptyTrustedAccountDraft(): TrustedAccountDraft {
  return {
    label: '',
    purpose: '',
    providerKey: 'youtube',
    accountIdentifier: '',
    secretType: 'session_cookie_later',
    secretValue: '',
    linkedProjectId: '',
    linkedIntegrationKey: '',
    notes: '',
    allowedCapabilitiesText: 'trusted_login, browser_workflow',
    loginIdentifier: '',
    passwordValue: '',
    totpValue: '',
    backupCodesValue: '',
    recoveryEmail: '',
    recoveryEmailSecretValue: '',
    connectorMappingJsonText: '{}',
  }
}

function mapTrustedAccountToDraft(account: TrustedAutoAccount): TrustedAccountDraft {
  const mapping = account.credentialProfile?.connectorMappingJson ?? {}
  return {
    label: account.label,
    purpose: typeof mapping.purpose === 'string' ? mapping.purpose : '',
    providerKey: account.providerKey,
    accountIdentifier: account.accountIdentifier,
    secretType: normalizeSecretType(account.secretType),
    secretValue: '',
    linkedProjectId: account.linkedProjectId == null ? '' : String(account.linkedProjectId),
    linkedIntegrationKey: account.linkedIntegrationKey ?? '',
    notes: account.credentialProfile?.notes ?? '',
    allowedCapabilitiesText: account.allowedCapabilities.join(', '),
    loginIdentifier: account.credentialProfile?.loginIdentifier ?? '',
    passwordValue: '',
    totpValue: '',
    backupCodesValue: '',
    recoveryEmail: account.credentialProfile?.recoveryEmail ?? '',
    recoveryEmailSecretValue: '',
    connectorMappingJsonText: JSON.stringify(mapping, null, 2),
  }
}

function normalizeSecretType(value: string | null | undefined): string {
  const normalized = String(value ?? '').trim()
  if (SECRET_TYPE_OPTIONS.some((option) => option.value === normalized)) {
    return normalized
  }
  if (normalized === 'oauth_token') {
    return 'oauth_token_later'
  }
  if (normalized === 'session_cookie') {
    return 'session_cookie_later'
  }
  return 'session_cookie_later'
}

function detectAccountUrl(input: string): AccountUrlSuggestion | null {
  const normalizedUrl = normalizeUrlInput(input)
  if (!normalizedUrl) {
    return null
  }
  let parsed: URL
  try {
    parsed = new URL(normalizedUrl)
  } catch {
    return null
  }
  const host = parsed.hostname.toLowerCase().replace(/^www\./, '')
  const rule = ACCOUNT_URL_RULES.find((candidate) =>
    candidate.hostFragments.some((fragment) => host === fragment || host.endsWith(`.${fragment}`)),
  )
  const service = rule?.service ?? `custom-${slugify(host)}`
  const displayName = rule?.displayName ?? displayNameFromHost(host)
  const loginUrl = rule?.loginUrl ?? normalizedUrl
  const validationUrl = rule?.validationUrl ?? `${parsed.protocol}//${parsed.host}/`
  const mapping = {
    login_url: loginUrl,
    validation_url: validationUrl,
    browser_session_service: service,
    domains: [host],
    source_url: normalizedUrl,
    auto_send: false,
  }
  return {
    service,
    displayName,
    loginUrl,
    validationUrl,
    providerKey: rule?.providerKey ?? 'custom',
    linkedIntegrationKey: rule?.linkedIntegrationKey ?? `${service}_browser_recipe`,
    secretType: 'session_cookie_later',
    allowedCapabilitiesText: rule?.allowedCapabilitiesText ?? 'trusted_login, browser_workflow',
    notes:
      rule?.notes ??
      `Konto dodane z URL ${normalizedUrl}. Atnos otworzy reczne logowanie i zapisze sesje dopiero po potwierdzeniu.`,
    connectorMappingJsonText: JSON.stringify(mapping, null, 2),
  }
}

function normalizeUrlInput(input: string): string | null {
  const trimmed = input.trim()
  if (!trimmed) return null
  const withProtocol = trimmed.includes('://') ? trimmed : `https://${trimmed}`
  try {
    const url = new URL(withProtocol)
    if (url.protocol !== 'https:' && url.protocol !== 'http:') {
      return null
    }
    return url.toString()
  } catch {
    return null
  }
}

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/^www\./, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'custom-site'
  )
}

function displayNameFromHost(host: string): string {
  return slugify(host)
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function parseCsvList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function applyUrlSuggestion(draft: TrustedAccountDraft, suggestion: AccountUrlSuggestion): TrustedAccountDraft {
  return {
    ...draft,
    label: draft.label.trim() ? draft.label : suggestion.displayName,
    providerKey: suggestion.providerKey,
    secretType: suggestion.secretType,
    linkedIntegrationKey: suggestion.linkedIntegrationKey,
    notes: draft.notes.trim() ? draft.notes : suggestion.notes,
    allowedCapabilitiesText: suggestion.allowedCapabilitiesText,
    connectorMappingJsonText: suggestion.connectorMappingJsonText,
  }
}

function withDraftPurpose(
  mapping: Record<string, unknown>,
  purpose: string,
): Record<string, unknown> {
  const trimmedPurpose = purpose.trim()
  if (!trimmedPurpose) {
    return mapping
  }
  return {
    ...mapping,
    purpose: trimmedPurpose,
  }
}

function buildAccountNotes(draft: TrustedAccountDraft, connectorMappingJson: Record<string, unknown>) {
  const manualNotes = draft.notes.trim()
  const purpose = draft.purpose.trim()
  const loginUrl = typeof connectorMappingJson.login_url === 'string' ? connectorMappingJson.login_url : ''
  const parts = [
    purpose ? `Do czego: ${purpose}` : '',
    loginUrl ? `URL: ${loginUrl}` : '',
    manualNotes,
  ].filter(Boolean)
  return parts.join('\n')
}

function suggestionFromRule(rule: (typeof ACCOUNT_URL_RULES)[number]): AccountUrlSuggestion {
  return {
    service: rule.service,
    displayName: rule.displayName,
    loginUrl: rule.loginUrl,
    validationUrl: rule.validationUrl,
    providerKey: rule.providerKey,
    linkedIntegrationKey: rule.linkedIntegrationKey,
    secretType: 'session_cookie_later',
    allowedCapabilitiesText: rule.allowedCapabilitiesText,
    notes: rule.notes,
    connectorMappingJsonText: JSON.stringify(
      {
        login_url: rule.loginUrl,
        validation_url: rule.validationUrl,
        browser_session_service: rule.service,
        domains: Array.from(new Set(rule.hostFragments)),
        source_url: rule.loginUrl,
        auto_send: false,
      },
      null,
      2,
    ),
  }
}

function suggestionFromProviderKey(providerKey: string): AccountUrlSuggestion | null {
  const rule =
    ACCOUNT_URL_RULES.find((item) => item.providerKey === providerKey) ??
    ACCOUNT_URL_RULES.find((item) => item.service === providerKey)
  return rule ? suggestionFromRule(rule) : null
}

function applyProviderSelection(draft: TrustedAccountDraft, providerKey: string): TrustedAccountDraft {
  const suggestion = suggestionFromProviderKey(providerKey)
  if (!suggestion) {
    return {
      ...draft,
      providerKey,
      linkedIntegrationKey: providerKey === 'custom' ? 'custom_browser_recipe' : draft.linkedIntegrationKey,
      connectorMappingJsonText: providerKey === 'custom' ? '' : draft.connectorMappingJsonText,
    }
  }
  return applyUrlSuggestion(
    {
      ...draft,
      providerKey,
    },
    suggestion,
  )
}

function applyAtnosMailboxPreset(draft: TrustedAccountDraft): TrustedAccountDraft {
  const suggestion = detectAccountUrl('https://int.pl/') ?? {
    service: 'int-mail',
    displayName: 'INT mail',
    loginUrl: 'https://int.pl/',
    validationUrl: 'https://int.pl/',
    providerKey: 'int-mail',
    linkedIntegrationKey: 'int_mail_browser_session',
    secretType: 'session_cookie_later',
    allowedCapabilitiesText: 'trusted_login, browser_workflow, read_metadata, read_content',
    notes:
      'Konto pocztowe Atnosa. Haslo wpisuj wylacznie w Settings/Vault; jesli bylo wklejone w chat, najpierw je zmien.',
    connectorMappingJsonText: JSON.stringify(
      {
        login_url: 'https://int.pl/',
        validation_url: 'https://int.pl/',
        browser_session_service: 'int-mail',
        domains: ['int.pl'],
        source_url: 'https://int.pl/',
        auto_send: false,
      },
      null,
      2,
    ),
  }

  return {
    ...applyUrlSuggestion(draft, suggestion),
    label: 'Atnos mailbox (int.pl)',
    accountIdentifier: 'account-c5034bf4@example.invalid',
    loginIdentifier: 'account-c5034bf4@example.invalid',
    secretType: 'password',
    secretValue: '',
    passwordValue: '',
  }
}

function browserTargetFromDraft(draft: TrustedAccountDraft): BrowserLoginTarget | null {
  try {
    const mapping = draft.connectorMappingJsonText.trim()
      ? (JSON.parse(draft.connectorMappingJsonText) as Record<string, unknown>)
      : {}
    const service = String(mapping.browser_session_service ?? '').trim()
    const loginUrl = String(mapping.login_url ?? '').trim()
    const validationUrl = String(mapping.validation_url ?? '').trim()
    if (service && loginUrl) {
      return {
        service,
        displayName: draft.label.trim() || service,
        loginUrl,
        validationUrl: validationUrl || loginUrl,
      }
    }
  } catch {
    return null
  }
  const rule =
    ACCOUNT_URL_RULES.find((item) => item.providerKey === draft.providerKey) ??
    ACCOUNT_URL_RULES.find((item) => item.service === draft.providerKey)
  return rule
    ? {
        service: rule.service,
        displayName: draft.label.trim() || rule.displayName,
        loginUrl: rule.loginUrl,
        validationUrl: rule.validationUrl,
      }
    : null
}

function deriveUrlInputFromDraft(draft: TrustedAccountDraft): string {
  return browserTargetFromDraft(draft)?.loginUrl ?? ''
}

function browserTargetFromContentItem(item: ContentReadinessItem): BrowserLoginTarget | null {
  if (item.platformKey === 'bootstrap-mailbox') {
    return null
  }
  const key = item.platformKey === 'youtube' || item.platformKey.startsWith('youtube:')
    ? 'youtube'
    : item.connectorKey ?? item.platformKey
  const rule =
    ACCOUNT_URL_RULES.find((candidate) => candidate.service === key) ??
    ACCOUNT_URL_RULES.find((candidate) => candidate.providerKey === key)
  return rule
    ? {
        service: rule.service,
        displayName: rule.displayName,
        loginUrl: rule.loginUrl,
        validationUrl: rule.validationUrl,
      }
    : null
}

function findSession(
  sessions: JarvisBrowserSessionSummary[],
  service: string,
): JarvisBrowserSessionSummary | null {
  return sessions.find((session) => session.service === service) ?? null
}

function resolveProviderConnectionState(card: ProviderCard | undefined): ConnectionState {
  if (!card) {
    return { kind: 'disconnected' }
  }
  if (!card.providerEnabled) {
    return { kind: 'disconnected' }
  }
  if (card.runtimeHealthStatus === 'rate_limited' || card.status === 'rate_limited') {
    return { kind: 'rate_limited', resetInSec: null }
  }
  if (card.status === 'connected' || card.runtimeHealthStatus === 'healthy') {
    return { kind: 'connected' }
  }
  if (card.status === 'disconnected' || card.readinessStatus === 'blocked') {
    return { kind: 'disconnected' }
  }
  if (
    card.status === 'configured' ||
    card.readinessStatus === 'configured' ||
    card.readinessStatus === 'fallback_only' ||
    card.hasSecret
  ) {
    return {
      kind: 'configured',
      message: card.readinessReason || card.statusReason || 'Konfiguracja zapisana. Uruchom test connection.',
    }
  }
  if (
    card.runtimeHealthStatus === 'unhealthy' ||
    card.runtimeHealthStatus === 'error' ||
    card.status === 'error'
  ) {
    return { kind: 'error', message: card.runtimeHealthReason || card.statusReason || 'Blad providera.' }
  }
  return { kind: 'disconnected' }
}

export default function SettingsRoute() {
  const pushToast = useToastStore((state) => state.push)
  const projects = useChatStore((state) => state.projects)
  const conversations = useChatStore((state) => state.conversations)
  const selectedProjectId = useChatStore((state) => state.selectedProjectId)
  const selectedConversationId = useChatStore((state) => state.selectedConversationId)
  const setRuntimeSummary = useSystemStore((state) => state.setRuntimeSummary)
  const setProviderCards = useSystemStore((state) => state.setProviderCards)
  const setCapabilities = useSystemStore((state) => state.setCapabilities)
  const setAccessReality = useSystemStore((state) => state.setAccessReality)
  const setTrustedAutoSummary = useSystemStore((state) => state.setTrustedAutoSummary)
  const setRemoteWorkers = useSystemStore((state) => state.setRemoteWorkers)
  const setRemoteExecutionRuns = useSystemStore((state) => state.setRemoteExecutionRuns)
  const setAmbientState = useSystemStore((state) => state.setAmbientState)

  const [controlCenter, setControlCenter] = useState<SettingsControlCenterPayload | null>(null)
  const [providerCardsLite, setProviderCardsLite] = useState<ProviderCard[]>([])
  const [loading, setLoading] = useState(true)
  const [sectionSaving, setSectionSaving] = useState<SectionKey | null>(null)
  const [browserSessions, setBrowserSessions] = useState<JarvisBrowserSessionSummary[]>([])
  const [accountFeedback, setAccountFeedback] = useState<TrustedAutoAccountFeedback[]>([])
  const [aiWorkersStatus, setAiWorkersStatus] = useState<AiWorkersStatusPayload | null>(null)
  const [aiWorkerTasks, setAiWorkerTasks] = useState<AiWorkerTask[]>([])
  const [runtimeHealth, setRuntimeHealth] = useState<RuntimeHealthPayload | null>(null)
  const [delegationPrompt, setDelegationPrompt] = useState(
    'Review the next Shorts script for hook strength, human tone and visual relevance. Return concrete fixes only.',
  )
  const [runtimeDraft, setRuntimeDraft] = useState({
    browserControlEnabled: false,
    browserAllowAnyUrl: true,
    externalActionsEnabled: false,
    publicUploadEnabled: false,
    schedulerEnabled: false,
    oauthEnabled: false,
    ambientListeningEnabled: false,
    ambientWakeWord: 'Atnos',
    ambientMicDevice: '',
    ambientMuted: false,
    ambientContextEnabled: false,
    trendWatchEnabled: false,
  })
  const [audioInputDevices, setAudioInputDevices] = useState<Array<{ value: string; label: string }>>([])
  const [providerDrafts, setProviderDrafts] = useState<Record<ProviderId, PersistedProviderConfig>>(() =>
    Object.fromEntries(PROVIDERS.map((provider) => [provider.id, readPersistedProviderConfig(provider.id)])) as Record<
      ProviderId,
      PersistedProviderConfig
    >,
  )

  const applyPayload = useCallback(
    (payload: SettingsControlCenterPayload) => {
      const trustedAuto = payload.trustedAuto ?? {
        runtimeControls: {
          autonomyMode: 'blocked' as const,
          schedulerEnabled: false,
          emergencyStopEnabled: false,
          pauseAllJobs: false,
          loginAutomationEnabled: false,
          twoFactorAutomationEnabled: false,
          publishAutomationEnabled: false,
          defaultRetryBudget: 2,
          defaultVramFloorGb: 4,
        },
        accounts: [],
        trustGrants: [],
        schedulerJobs: [],
        executionRuns: [],
        mediaPipelinePresets: [],
        hardwareSnapshot: null,
        browserRuntime: null,
        youtubeConnector: null,
        contentReadiness: null,
      }
      setControlCenter({ ...payload, trustedAuto })
      setRuntimeSummary(payload.runtimeSummary)
      setProviderCards(payload.providerCards)
      setCapabilities(payload.capabilities)
      setAccessReality(payload.accessReality)
      setTrustedAutoSummary(trustedAuto)
      setRemoteWorkers(payload.remoteWorkers ?? [])
      setRemoteExecutionRuns((payload.remoteWorkers ?? []).flatMap((worker) => worker.recentRuns))
      setAmbientState({
        ambientListeningEnabled: payload.runtime.ambientListeningEnabled,
        ambientWakeWord: payload.runtime.ambientWakeWord,
        ambientMuted: payload.runtime.ambientMuted,
        ambientContextEnabled: payload.runtime.ambientContextEnabled,
        voiceRepliesEnabled: payload.runtime.voiceRepliesEnabled,
        voiceReplyVoiceId: payload.runtime.voiceReplyVoiceId,
        listeningAvailable: payload.runtime.ambientListeningAvailable,
        listeningReason: payload.runtime.ambientListeningReason,
        contextSnapshot: null,
      })
      setRuntimeDraft({
        browserControlEnabled: payload.runtime.browserControlEnabled,
        browserAllowAnyUrl: payload.runtime.browserAllowAnyUrl,
        externalActionsEnabled: payload.runtime.externalActionsEnabled,
        publicUploadEnabled: payload.runtime.publicUploadEnabled,
        schedulerEnabled: payload.runtime.schedulerEnabled,
        oauthEnabled: payload.runtime.oauthEnabled,
        ambientListeningEnabled: payload.runtime.ambientListeningEnabled,
        ambientWakeWord: payload.runtime.ambientWakeWord || 'Atnos',
        ambientMicDevice: payload.runtime.ambientMicDevice ?? '',
        ambientMuted: payload.runtime.ambientMuted,
        ambientContextEnabled: payload.runtime.ambientContextEnabled,
        trendWatchEnabled: payload.runtime.trendWatchEnabled,
      })
    },
    [
      setAccessReality,
      setAmbientState,
      setCapabilities,
      setProviderCards,
      setRemoteExecutionRuns,
      setRemoteWorkers,
      setRuntimeSummary,
      setTrustedAutoSummary,
    ],
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [payload, sessions] = await Promise.all([
        fetchSettingsControlCenter(),
        fetchJarvisBrowserSessions().catch(() => [] as JarvisBrowserSessionSummary[]),
      ])
      applyPayload(payload)
      setProviderCardsLite((current) => (current.length ? current : payload.providerCards))
      setBrowserSessions(sessions)
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udalo sie wczytac ustawien',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setLoading(false)
    }
  }, [applyPayload, pushToast])

  const loadAiDelegation = useCallback(async (notifyOnError = false) => {
    try {
      const [workers, tasks, health] = await Promise.all([
        fetchAiWorkersStatus(),
        fetchAiWorkerTasks({ rootsOnly: true, limit: 8 }).catch(() => [] as AiWorkerTask[]),
        fetchRuntimeHealth(),
      ])
      setAiWorkersStatus(workers)
      setAiWorkerTasks(tasks)
      setRuntimeHealth(health)
    } catch (error) {
      if (notifyOnError) {
        pushToast({
          type: 'error',
          title: 'AI Delegation nie odpowiada',
          message: error instanceof Error ? error.message : 'Unknown error',
        })
      }
    }
  }, [pushToast])

  useEffect(() => {
    let active = true
    fetchProviderCards()
      .then((cards) => {
        if (!active) return
        setProviderCardsLite(cards)
        setProviderCards(cards)
      })
      .catch(() => {
        // Full settings load will surface the user-facing error if provider cards are unavailable.
      })
    return () => {
      active = false
    }
  }, [setProviderCards])

  useEffect(() => {
    void loadAiDelegation()
  }, [loadAiDelegation])

  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([
      fetchSettingsControlCenter(),
      fetchJarvisBrowserSessions().catch(() => [] as JarvisBrowserSessionSummary[]),
    ])
      .then(([payload, sessions]) => {
        if (!active) return
        applyPayload(payload)
        setProviderCardsLite((current) => (current.length ? current : payload.providerCards))
        setBrowserSessions(sessions)
      })
      .catch((error) => {
        if (!active) return
        pushToast({
          type: 'error',
          title: 'Nie udalo sie wczytac ustawien',
          message: error instanceof Error ? error.message : 'Unknown error',
        })
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      setLoading(false)
    }
  }, [applyPayload, pushToast])

  useEffect(() => {
    let active = true
    const mediaDevices = navigator.mediaDevices
    if (!mediaDevices?.enumerateDevices) {
      setAudioInputDevices([])
      return () => {
        active = false
      }
    }
    mediaDevices
      .enumerateDevices()
      .then((devices) => {
        if (!active) return
        const audioInputs = devices
          .filter((device) => device.kind === 'audioinput')
          .map((device, index) => {
            const label = device.label || `Mikrofon ${index + 1}`
            return {
              label,
              value: device.label ? `label:${device.label}` : `device:${device.deviceId}`,
            }
          })
        setAudioInputDevices(audioInputs)
      })
      .catch(() => {
        if (active) setAudioInputDevices([])
      })
    return () => {
      active = false
    }
  }, [])

  const providerCardsByKey = useMemo(() => {
    const map = new Map<string, ProviderCard>()
    for (const card of controlCenter?.providerCards ?? []) {
      map.set(card.providerKey, card)
    }
    // The lightweight endpoint carries the freshest provider health/test state.
    // Control-center snapshots can lag behind and should not downgrade ready cards.
    for (const card of providerCardsLite) {
      map.set(card.providerKey, card)
    }
    return map
  }, [controlCenter?.providerCards, providerCardsLite])

  const providerReadyCount =
    controlCenter?.runtimeSummary.providersReady ??
    CORE_PROVIDER_ORDER.filter((providerId) => {
      const card = providerCardsByKey.get(providerId)
      return (
        card?.status === 'connected' ||
        card?.runtimeHealthStatus === 'healthy' ||
        card?.readinessStatus === 'configured' ||
        card?.readinessStatus === 'fallback_only'
      )
    }).length
  const selectedConversationProjectId = useMemo(() => {
    if (selectedConversationId == null) return null
    return conversations.find((conversation) => conversation.id === selectedConversationId)?.projectId ?? null
  }, [conversations, selectedConversationId])
  const operationalMemoryProjectId = selectedProjectId ?? selectedConversationProjectId
  const operationalMemoryProject = useMemo(
    () => projects.find((project) => project.id === operationalMemoryProjectId) ?? null,
    [operationalMemoryProjectId, projects],
  )
  const n8nRuntime = controlCenter?.n8nYoutubeAutomation ?? null

  const aiToolsCenterSection = (
    <SectionCard
      icon={<BrainCircuit size={18} className="text-accent-primary" />}
      title="AI & Tools Center"
      description="Stabilny core to 4 providerów API. Browserowe AI zostaje osobnym trybem paste-only, nie głównym backendem."
    >
      <div className="space-y-6">
        <div>
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="font-display text-lg text-text-primary">Modele AI</div>
              <div className="mt-1 text-sm text-text-secondary">
                Domyślny chat działa lokalnie. Usługi zewnętrzne wymagają jawnego trybu i mogą generować koszt.
              </div>
            </div>
            <StatusPill label="Adaptery" value={`${providerReadyCount}/4`} tone="muted" />
          </div>
          <div className="flex flex-wrap gap-4">
            {CORE_PROVIDER_ORDER.map((providerId) => {
              const provider = PROVIDERS.find((item) => item.id === providerId)
              if (!provider) {
                return null
              }
              const card = providerCardsByKey.get(provider.id)
              const persisted = providerDrafts[provider.id]
              return (
                <SettingsProviderCard
                  key={provider.id}
                  provider={provider.id}
                  state={resolveProviderConnectionState(card)}
                  apiKey={persisted.apiKey}
                  baseUrl={persisted.baseUrl}
                  lastSync={persisted.lastSync ?? null}
                  onSave={async (next) => {
                    setSectionSaving(`provider:${provider.id}`)
                    try {
                      const merged = { ...persisted, ...next, lastSync: Date.now() }
                      writePersistedProviderConfig(provider.id, merged)
                      setProviderDrafts((state) => ({ ...state, [provider.id]: merged }))
                      await updateProviderCard(provider.id, next)
                    } finally {
                      setSectionSaving(null)
                    }
                  }}
                  onTest={(draft) => testProviderCard(provider.id, draft)}
                />
              )
            })}
          </div>
        </div>

        <RuntimeCapabilityStatusPanel />

        <AiDelegationPanel
          workersStatus={aiWorkersStatus}
          tasks={aiWorkerTasks}
          runtimeHealth={runtimeHealth}
          prompt={delegationPrompt}
          pendingKey={sectionSaving}
          onPromptChange={setDelegationPrompt}
          onRefresh={() => void loadAiDelegation(true)}
          onProbe={(workerKey) => void probeAiDelegationWorker(workerKey)}
          onDelegate={() => void runDelegationSmoke()}
          onPlan={() => void runDelegationPlan()}
          onRunTask={(taskId) => void runAiDelegationTask(taskId)}
          desktopApps={controlCenter?.localHost.desktopApps ?? null}
          localHostExecutionEnabled={controlCenter?.localHost.executionEnabled ?? null}
          onOpenDesktopApp={(app) => void runLocalDesktopAppAction(app.appKey, 'open', app.label)}
          onFocusDesktopApp={(app) => void runLocalDesktopAppAction(app.appKey, 'focus', app.label)}
        />

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <SkillDetail
            title="Konta"
            body="GitHub, Linear, Google i inne konta są podpinane niżej jako OAuth albo kontrolowana sesja browserowa."
            tone={controlCenter?.oauthConnectors.some((connector) => connector.connectionId) ? 'success' : 'neutral'}
          />
          <SkillDetail
            title="Narzędzia"
            body={`Browser: ${controlCenter?.trustedAuto.browserRuntime?.ready ? 'ready' : controlCenter ? 'not ready' : 'loading'} · Obsidian: ${
              controlCenter?.capabilities.obsidianExportEnabled ? 'ready' : controlCenter ? 'off' : 'loading'
            } · Ubuntu sandbox: pełny dostęp tylko po jawnym routingu.`}
            tone={controlCenter?.trustedAuto.browserRuntime?.ready ? 'success' : controlCenter ? 'warning' : 'neutral'}
          />
          <SkillDetail
            title="Uprawnienia"
            body="Full-auto jest dozwolone tylko dla sandboxa Ubuntu, lokalnych plików roboczych i low/medium risk. Publikacja, płatności, sekrety i destrukcyjne akcje na głównym PC wymagają approval."
            tone="warning"
          />
          <SkillDetail
            title="n8n"
            body={`Docker: ${n8nRuntime?.dockerStatus ?? 'loading'}; fallback: ${
              n8nRuntime?.fallbackRunnerAvailable ? 'available' : controlCenter ? 'missing' : 'loading'
            }; workflows: ${n8nRuntime?.workflowTemplatesStatus ?? 'loading'}.`}
            tone={n8nRuntime?.fallbackRunnerAvailable ? 'success' : controlCenter ? 'warning' : 'neutral'}
          />
          <SkillDetail
            title="Koszty"
            body="Cloud działa przez jawne klucze API, test connection i router privacy. Brak klucza daje błąd konfiguracji, nie cichy fallback."
            tone={controlCenter?.runtimeSummary.hasExternalFallback ? 'success' : 'neutral'}
          />
        </div>
      </div>
    </SectionCard>
  )

  const runBrowserAction = useCallback(
    async (
      key: SectionKey,
      action: () => Promise<{ ok: boolean; session: JarvisBrowserSessionSummary }>,
      successTitle: string,
    ) => {
      setSectionSaving(key)
      try {
        const result = await action()
        setBrowserSessions(await fetchJarvisBrowserSessions())
        pushToast({
          type: result.ok ? 'success' : 'warning',
          title: result.ok ? successTitle : 'Sesja nie jest jeszcze gotowa',
          message: result.session.statusReason,
        })
      } catch (error) {
        pushToast({
          type: 'error',
          title: 'Akcja browser session nie powiodla sie',
          message: error instanceof Error ? error.message : 'Unknown error',
        })
      } finally {
        setSectionSaving(null)
      }
    },
    [pushToast],
  )

  const startBrowserLogin = useCallback(
    (target: BrowserLoginTarget) => {
      void runBrowserAction(
        `browser:start:${target.service}`,
        () =>
          startJarvisBrowserSessionManualLoginForUrl({
            service: target.service,
            displayName: target.displayName,
            loginUrl: target.loginUrl,
            validationUrl: target.validationUrl,
          }),
        `${target.displayName}: okno logowania otwarte`,
      )
    },
    [runBrowserAction],
  )

  const confirmBrowserLogin = useCallback(
    (service: string, displayName: string) => {
      void runBrowserAction(
        `browser:confirm:${service}`,
        () => confirmJarvisBrowserSessionManualLogin(service),
        `${displayName}: sesja zapisana`,
      )
    },
    [runBrowserAction],
  )

  const saveRuntime = async () => {
    setSectionSaving('runtime')
    try {
      const payload = await updateSettingsRuntime(runtimeDraft)
      applyPayload(payload)
      pushToast({ type: 'success', title: 'Runtime zapisany' })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Runtime nie zostal zapisany',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const probeAiDelegationWorker = async (workerKey: string) => {
    setSectionSaving(`ai-worker:${workerKey}`)
    try {
      const worker = await probeAiWorker(workerKey)
      await loadAiDelegation()
      pushToast({
        type: worker.ready ? 'success' : 'warning',
        title: `${worker.label || workerKey}: ${worker.status}`,
        message: worker.reason,
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Test AI workera nie powiodl sie',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const runDelegationSmoke = async () => {
    setSectionSaving('ai-worker:delegate')
    try {
      const task = await delegateAiWorkerTask({
        taskType: 'script_quality_review',
        prompt: delegationPrompt,
        preferredWorker: 'ollama',
        privacyMode: 'strict_local',
      })
      await loadAiDelegation()
      pushToast({
        type: task.status === 'completed' ? 'success' : 'warning',
        title: `Delegacja: ${task.status}`,
        message: task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? `Task ${task.id}`,
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Delegacja AI nie powiodla sie',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const runDelegationPlan = async () => {
    setSectionSaving('ai-worker:plan')
    try {
      const task = await delegateAiWorkerPlan({
        taskType: 'engineering_runtime_delegation',
        prompt: delegationPrompt,
        preferredWorker: 'ollama',
        allowedWorkers: ['ollama', 'browser_claude', 'browser_chatgpt', 'browser_kimi'],
        privacyMode: 'strict_local',
        maxSteps: 6,
      })
      await loadAiDelegation()
      pushToast({
        type: task.status === 'planned' ? 'success' : 'warning',
        title: `Plan delegacji: ${task.status}`,
        message: task.resultSummary ?? task.blockedReason ?? `Root task ${task.id}`,
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Plan delegacji AI nie powiodl sie',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const runAiDelegationTask = async (taskId: number) => {
    const key: SectionKey = `ai-worker:run:${taskId}`
    setSectionSaving(key)
    try {
      const task = await runAiWorkerTask(taskId)
      await loadAiDelegation()
      pushToast({
        type: task.status === 'completed' ? 'success' : 'warning',
        title: `Task #${task.id}: ${task.status}`,
        message: task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? 'Task zapisany w DB.',
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Uruchomienie taska nie powiodlo sie',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const runLocalDesktopAppAction = async (
    appKey: string,
    action: 'open' | 'focus',
    label: string,
  ) => {
    const key: SectionKey = `local-host:${action}:${appKey}`
    setSectionSaving(key)
    try {
      const result =
        action === 'open'
          ? await openLocalHostDesktopApp(appKey)
          : await focusLocalHostDesktopApp(appKey)
      const ok = result.exitCode === 0
      pushToast({
        type: ok ? 'success' : 'warning',
        title: `${label}: ${action === 'open' ? 'otwieranie' : 'przywolanie'}`,
        message: result.stdout || result.stderr || result.command,
      })
      await load()
    } catch (error) {
      pushToast({
        type: 'error',
        title: `${label}: akcja nie powiodla sie`,
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const runAccountProbe = async () => {
    setSectionSaving('trusted:feedback')
    try {
      const results = await probeTrustedAutoAccounts()
      setAccountFeedback(results)
      pushToast({
        type: 'success',
        title: 'Sprawdzilem zapisane strony',
        message: results.length ? `Wyniki: ${results.length}` : 'Brak kont do sprawdzenia.',
      })
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Probe stron nie powiodl sie',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const connectOAuth = async (connectorKey: string, displayName: string, contentAccountId?: string | null) => {
    setSectionSaving(`oauth:${connectorKey}:${contentAccountId ?? 'global'}`)
    try {
      const result = contentAccountId
        ? await startOAuthConnection(connectorKey, { contentAccountId })
        : await startOAuthConnection(connectorKey)
      const canUseHostBrowser =
        controlCenter?.localHost.browserOpenAvailable && controlCenter.localHost.executionEnabled
      if (canUseHostBrowser) {
        await openLocalHostBrowserUrl(result.authorizationUrl)
      } else {
        window.open(result.authorizationUrl, '_blank', 'popup,width=720,height=820')
      }
      pushToast({
        type: 'success',
        title: `${displayName}: OAuth otwarty`,
        message: result.contentAccountId ? 'Po powrocie z Google token zostanie przypisany tylko do tego kanalu.' : undefined,
      })
      await load()
    } catch (error) {
      pushToast({
        type: 'error',
        title: `${displayName}: OAuth nie ruszyl`,
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const disconnectOAuth = async (connectionId: number, displayName: string) => {
    setSectionSaving(`oauth:${connectionId}`)
    try {
      await disconnectOAuthConnection(connectionId)
      pushToast({ type: 'success', title: `${displayName}: odlaczono` })
      await load()
    } catch (error) {
      pushToast({
        type: 'error',
        title: `${displayName}: nie odlaczono`,
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  const probeOAuth = async (connectorKey: string, displayName: string, accountId?: number | null) => {
    setSectionSaving(`oauth:${connectorKey}:${accountId ?? 'global'}`)
    try {
      if (connectorKey === 'youtube-studio' || connectorKey === 'youtube') {
        await probeYouTubeConnector(accountId)
      } else if (connectorKey === 'github' || connectorKey === 'github-readonly') {
        await probeGitHubConnector()
      } else if (connectorKey === 'google-calendar' || connectorKey === 'google-calendar-readonly') {
        await probeGoogleCalendarConnector()
      } else if (connectorKey === 'gmail' || connectorKey === 'gmail-readonly') {
        await probeGmailConnector()
      } else {
        pushToast({ type: 'warning', title: `${displayName}: brak live probe` })
        return
      }
      pushToast({ type: 'success', title: `${displayName}: probe ok` })
      await load()
    } catch (error) {
      pushToast({
        type: 'error',
        title: `${displayName}: probe failed`,
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    } finally {
      setSectionSaving(null)
    }
  }

  if (loading && !controlCenter) {
    return (
      <div className="shell-route h-full overflow-y-auto px-4 py-6">
        <div className="mx-auto max-w-[1440px] space-y-6">
          <section className="shell-hero p-6 lg:p-8">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="label-eyebrow">USTAWIENIA</div>
                <h1 className="mt-2 type-h1 text-text-primary">Centrum kontroli Atnosa</h1>
                <p className="mt-3 max-w-3xl type-body text-text-secondary">
                  Najpierw pokazuję modele i klucze AI. Pełne ustawienia narzędzi ładują się w tle.
                </p>
              </div>
              <StatusPill label="loading" value="core" tone="warn" />
            </div>
          </section>
          {aiToolsCenterSection}
          <div className="shell-panel p-4 type-body text-text-secondary">
            Ładuję pełne ustawienia kont, sesji browserowych i runtime...
          </div>
        </div>
      </div>
    )
  }

  if (!controlCenter) {
    return (
      <div className="shell-route h-full overflow-y-auto px-4 py-6">
        <div className="shell-panel mx-auto max-w-[1440px] p-6 type-body text-text-secondary">
          Nie udalo sie zbudowac centrum ustawien.
        </div>
      </div>
    )
  }

  return (
    <div id="diagnostics" className="shell-route scroll-chrome h-full overflow-y-auto px-4 py-6">
      <div className="mx-auto max-w-[1440px] space-y-8">
        <section className="shell-hero p-6 lg:p-8">
          <div className="relative grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div>
              <div className="label-eyebrow">USTAWIENIA</div>
              <h1 className="mt-2 type-h1 text-text-primary">Centrum kontroli Atnosa</h1>
              <p className="mt-4 max-w-3xl type-body text-text-secondary">
                Jedno miejsce na modele, konta, narzedzia, uprawnienia i pamiec projektu. Chat
                pozostaje glowna powierzchnia pracy; tutaj tylko konfigurujesz, do czego Atnos ma dostep.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                {
                  icon: BrainCircuit,
                  title: 'Modele i API',
                  body: 'Providerzy, routing i fallbacki',
                },
                {
                  icon: ShieldCheck,
                  title: 'Konta',
                  body: 'Loginy, OAuth i sesje browserowe',
                },
                {
                  icon: PlugZap,
                  title: 'Narzędzia',
                  body: 'Connectory oraz runtime skilli',
                },
                {
                  icon: SlidersHorizontal,
                  title: 'Uprawnienia',
                  body: 'Approval gates i automatyzacja',
                },
              ].map((item) => {
                const Icon = item.icon
                return (
                  <div key={item.title} className="rounded-2xl border border-border-default bg-panel/65 px-4 py-4">
                    <Icon size={18} className="text-accent-primary" />
                    <div className="mt-3 type-body-sm font-semibold text-text-primary">{item.title}</div>
                    <div className="mt-1 text-xs leading-5 text-text-tertiary">{item.body}</div>
                  </div>
                )
              })}
            </div>
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-4">
          <MetricTile label="Providerzy" value={String(controlCenter.runtimeSummary.providersReady)} tone="success" />
          <MetricTile label="Konta" value={String(controlCenter.trustedAuto.accounts.length)} tone="neutral" />
          <MetricTile
            label="Browser ready"
            value={controlCenter.trustedAuto.browserRuntime?.ready ? 'ready' : 'not ready'}
            tone={controlCenter.trustedAuto.browserRuntime?.ready ? 'success' : 'warning'}
          />
          <MetricTile
            label="Publish"
            value={controlCenter.trustedAuto.runtimeControls.publishAutomationEnabled ? 'on' : 'approval'}
            tone={controlCenter.trustedAuto.runtimeControls.publishAutomationEnabled ? 'warning' : 'neutral'}
          />
        </section>

        {aiToolsCenterSection}

        {controlCenter.n8nYoutubeAutomation ? (
          <SectionCard
            icon={<PlugZap size={18} className="text-accent-primary" />}
            title="n8n YouTube automation"
            description="Status Dockera i lokalnego fallback runnera. Fallback skanuje shorty i tworzy approval, ale nie wykonuje publicznego uploadu."
          >
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <MetricTile
                label="n8n docker"
                value={controlCenter.n8nYoutubeAutomation.dockerStatus}
                tone={controlCenter.n8nYoutubeAutomation.dockerReady ? 'success' : 'warning'}
              />
              <MetricTile
                label="Fallback runner"
                value={controlCenter.n8nYoutubeAutomation.fallbackRunnerStatus}
                tone={controlCenter.n8nYoutubeAutomation.fallbackRunnerAvailable ? 'success' : 'warning'}
              />
              <MetricTile
                label="Workflow JSON"
                value={`${controlCenter.n8nYoutubeAutomation.workflowTemplatesStatus} (${controlCenter.n8nYoutubeAutomation.workflowTemplatesCount})`}
                tone={controlCenter.n8nYoutubeAutomation.workflowTemplatesStatus === 'ready' ? 'success' : 'warning'}
              />
              <MetricTile
                label="Virtualization"
                value={
                  controlCenter.n8nYoutubeAutomation.virtualizationFirmwareEnabled === null
                    ? 'unknown'
                    : controlCenter.n8nYoutubeAutomation.virtualizationFirmwareEnabled
                      ? 'enabled'
                      : 'disabled'
                }
                tone={controlCenter.n8nYoutubeAutomation.virtualizationFirmwareEnabled === false ? 'warning' : 'neutral'}
              />
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <SkillDetail
                title="Docker message"
                body={controlCenter.n8nYoutubeAutomation.dockerMessage}
                tone="neutral"
              />
              <SkillDetail
                title="Fallback path"
                body={controlCenter.n8nYoutubeAutomation.fallbackRunnerPath}
                tone={controlCenter.n8nYoutubeAutomation.fallbackRunnerAvailable ? 'success' : 'warning'}
              />
            </div>
          </SectionCard>
        ) : null}

        <SectionCard
          icon={<ShieldCheck size={18} className="text-accent-primary" />}
          title="Konta i loginy dla Atnosa"
          description="Wklej URL aplikacji, Atnos wykryje serwis, przygotuje konto i otworzy logowanie tak jak dla Claude."
          action={
            <button type="button" disabled={sectionSaving === 'trusted:feedback'} onClick={() => void runAccountProbe()} className={primaryButtonClass(sectionSaving === 'trusted:feedback')}>
              Sprawdz zapisane strony
            </button>
          }
        >
          <div className="space-y-4">
            <div className="rounded-xl border border-sem-warning/30 bg-sem-warning/10 px-4 py-4 text-sm leading-6 text-text-secondary">
              <div className="font-display text-base text-text-primary">Zasada bezpieczenstwa</div>
              <div className="mt-2">
                Atnos nie publikuje, nie wydaje pieniedzy i nie uzywa pustych sekretow bez approval. Browserowe
                SaaS-y sa fallbackiem albo narzedziami pomocniczymi, nie core backendem.
              </div>
            </div>
            {accountFeedback.length ? (
              <div className="grid gap-3 md:grid-cols-2">
                {accountFeedback.map((item) => (
                  <div key={`${item.accountId}:${item.sequence}`} className="rounded-lg border border-border-default bg-base px-3 py-3">
                    <div className="type-body text-text-primary">{item.sequence}. {item.label}</div>
                    <div className="mt-1 type-body-sm text-text-tertiary">{item.url ?? 'brak url'} · {item.providerKey}</div>
                    <div className="mt-2 type-body-sm text-text-secondary">{item.reason}</div>
                  </div>
                ))}
              </div>
            ) : null}
            {controlCenter.trustedAuto.accounts.map((account) => (
              <TrustedAccountEditor
                key={account.accountId}
                account={account}
                projects={projects}
                sessions={browserSessions}
                pendingKey={sectionSaving}
                onStartBrowserLogin={startBrowserLogin}
                onConfirmBrowserLogin={confirmBrowserLogin}
                onSave={async (draft) => {
                  setSectionSaving(`trusted:account:${account.accountId}`)
                  try {
                    await upsertTrustedAutoAccount(draft)
                    await load()
                    pushToast({ type: 'success', title: `Konto ${account.label} zapisane` })
                  } finally {
                    setSectionSaving(null)
                  }
                }}
              />
            ))}
            <TrustedAccountEditor
              key={`new-account-${controlCenter.trustedAuto.accounts.length}`}
              account={null}
              projects={projects}
              sessions={browserSessions}
              pendingKey={sectionSaving}
              onStartBrowserLogin={startBrowserLogin}
              onConfirmBrowserLogin={confirmBrowserLogin}
              onSave={async (draft) => {
                setSectionSaving('trusted:account:new')
                try {
                  await upsertTrustedAutoAccount(draft)
                  await load()
                  pushToast({ type: 'success', title: 'Nowe konto zapisane' })
                } finally {
                  setSectionSaving(null)
                }
              }}
            />
          </div>
        </SectionCard>

        <SectionCard
          icon={<PlugZap size={18} className="text-accent-primary" />}
          title="Kanaly i narzedzia contentowe"
          description="Karty kanalow sa polaczone z Browserowymi sesjami AI, wiec OAuth nie jest jedyna droga logowania."
        >
          <ContentReadinessSection
            items={controlCenter.trustedAuto.contentReadiness?.items ?? []}
            publishMode={controlCenter.trustedAuto.contentReadiness?.summary.publishMode ?? 'draft_only'}
            sessions={browserSessions}
            pendingKey={sectionSaving}
            onStartBrowserLogin={startBrowserLogin}
            onConfirmBrowserLogin={confirmBrowserLogin}
            onConnect={connectOAuth}
            onDisconnect={disconnectOAuth}
            onProbe={probeOAuth}
          />
        </SectionCard>

        <JarvisBrowserSessionsCard
          sessions={browserSessions}
          pendingKey={sectionSaving}
          onRefresh={async () => setBrowserSessions(await fetchJarvisBrowserSessions())}
          onStart={(service) =>
            void runBrowserAction(
              `browser:start:${service}`,
              () => startJarvisBrowserSessionManualLogin(service),
              `${service}: okno logowania otwarte`,
            )
          }
          onConfirm={(service, displayName) => confirmBrowserLogin(service, displayName)}
        />

        <SectionCard
          icon={<SlidersHorizontal size={18} className="text-accent-primary" />}
          title="Runtime"
          description="Najwazniejsze przelaczniki globalne. Ustawienia szczegolowe rozmowy zostaja w Chacie."
          action={
            <button type="button" disabled={sectionSaving === 'runtime'} onClick={() => void saveRuntime()} className={primaryButtonClass(sectionSaving === 'runtime')}>
              Zapisz podstawowe
            </button>
          }
        >
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <ToggleRow label="Browser control" checked={runtimeDraft.browserControlEnabled} onChange={(checked) => setRuntimeDraft((state) => ({ ...state, browserControlEnabled: checked }))} />
            <ToggleRow label="Allow any URL" checked={runtimeDraft.browserAllowAnyUrl} onChange={(checked) => setRuntimeDraft((state) => ({ ...state, browserAllowAnyUrl: checked }))} />
            <ToggleRow label="External actions" checked={runtimeDraft.externalActionsEnabled} onChange={(checked) => setRuntimeDraft((state) => ({ ...state, externalActionsEnabled: checked }))} />
            <ToggleRow label="Publiczny upload" checked={runtimeDraft.publicUploadEnabled} onChange={(checked) => setRuntimeDraft((state) => ({ ...state, publicUploadEnabled: checked }))} />
            <ToggleRow label="OAuth" checked={runtimeDraft.oauthEnabled} onChange={(checked) => setRuntimeDraft((state) => ({ ...state, oauthEnabled: checked }))} />
            <ToggleRow label="Scheduler" checked={runtimeDraft.schedulerEnabled} onChange={(checked) => setRuntimeDraft((state) => ({ ...state, schedulerEnabled: checked }))} />
            <ToggleRow label="Trend watch" checked={runtimeDraft.trendWatchEnabled} onChange={(checked) => setRuntimeDraft((state) => ({ ...state, trendWatchEnabled: checked }))} />
          </div>
        </SectionCard>

        <SectionCard
          icon={<SlidersHorizontal size={18} className="text-accent-primary" />}
          title="Glos i ambient"
          description="Sterowanie nasluchem, mikrofonem i kontekstem glosowym bez mieszania tego z kontami."
          action={
            <button type="button" disabled={sectionSaving === 'runtime'} onClick={() => void saveRuntime()} className={primaryButtonClass(sectionSaving === 'runtime')}>
              Zapisz runtime
            </button>
          }
        >
          <AmbientRuntimeSection
            reason={controlCenter.runtime.ambientListeningReason}
            audioInputDevices={audioInputDevices}
            ambientListeningEnabled={runtimeDraft.ambientListeningEnabled}
            ambientWakeWord={runtimeDraft.ambientWakeWord}
            ambientMicDevice={runtimeDraft.ambientMicDevice}
            ambientMuted={runtimeDraft.ambientMuted}
            ambientContextEnabled={runtimeDraft.ambientContextEnabled}
            onChange={(patch) => setRuntimeDraft((state) => ({ ...state, ...patch }))}
          />
        </SectionCard>

        <SectionCard
          icon={<Database size={18} className="text-accent-primary" />}
          title="Operational Memory"
          description="Stan pracy zakotwiczony w aktywnym projekcie. To tylko review surface: bez wykonania, bez connectorow, bez autopilota."
        >
          <OperationalMemoryPanel
            projectId={operationalMemoryProject?.id ?? operationalMemoryProjectId ?? null}
            projectName={operationalMemoryProject?.name ?? null}
          />
        </SectionCard>

        <SectionCard
          icon={<Database size={18} className="text-accent-primary" />}
          title="Dziennik decyzji"
          description="Najwazniejsze decyzje i zobowiazania z rozmow, bez robienia z tego osobnej zakladki."
        >
          <DecisionLogSection entries={controlCenter.decisionLog} />
        </SectionCard>

        <SectionCard
          icon={<Database size={18} className="text-accent-primary" />}
          title="OAuth connectory"
          description="Oficjalne connectory zostaja, ale nie blokujemy juz pracy, gdy trzeba uzyc recznej sesji browserowej."
        >
          <div className="grid gap-3 md:grid-cols-2">
            {controlCenter.oauthConnectors.map((connector) => (
              <div key={connector.connectorKey} className="rounded-xl border border-border-default bg-elevated px-4 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-display text-base text-text-primary">{connector.displayName}</div>
                    <div className="mt-1 font-mono text-xs text-text-tertiary">{connector.connectorKey}</div>
                  </div>
                  <StatusPill label="OAuth" value={connector.status} tone={connector.connectionId ? 'ok' : connector.configured ? 'warn' : 'muted'} />
                </div>
                {connector.setupWarnings.length ? (
                  <div className="mt-3 type-body-sm text-text-secondary">{connector.setupWarnings.join(' ')}</div>
                ) : null}
                <div className="mt-4 flex flex-wrap gap-2">
                  {!connector.connectionId && connector.enabled && connector.configured ? (
                    <button type="button" onClick={() => void connectOAuth(connector.connectorKey, connector.displayName)} className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary">
                      Polacz OAuth
                    </button>
                  ) : null}
                  {connector.connectionId ? (
                    <button type="button" onClick={() => void disconnectOAuth(connector.connectionId as number, connector.displayName)} className="rounded-md border border-border-strong px-3 py-2 text-sm text-text-secondary">
                      Odlacz
                    </button>
                  ) : null}
                  <button type="button" onClick={() => void probeOAuth(connector.connectorKey, connector.displayName)} className="rounded-md border border-border-strong px-3 py-2 text-sm text-text-secondary">
                    Test connection
                  </button>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>
    </div>
  )
}

function ContentReadinessSection({
  items,
  publishMode,
  sessions,
  pendingKey,
  onStartBrowserLogin,
  onConfirmBrowserLogin,
  onConnect,
  onDisconnect,
  onProbe,
}: {
  items: ContentReadinessItem[]
  publishMode: string
  sessions: JarvisBrowserSessionSummary[]
  pendingKey: SectionKey | null
  onStartBrowserLogin: (target: BrowserLoginTarget) => void
  onConfirmBrowserLogin: (service: string, displayName: string) => void
  onConnect: (connectorKey: string, displayName: string, contentAccountId?: string | null) => void | Promise<void>
  onDisconnect: (connectionId: number, displayName: string) => void | Promise<void>
  onProbe: (connectorKey: string, displayName: string, accountId?: number | null) => void | Promise<void>
}) {
  const blockedCount = items.filter((item) => item.status === 'blocked').length
  const readyCount = items.filter((item) => item.status === 'supervised_ready').length
  const authCount = items.filter((item) => item.needsAuth).length

  if (!items.length) {
    return (
      <div className="rounded-xl border border-border-default bg-elevated px-4 py-4 type-body text-text-secondary">
        Brak danych o gotowosci kanalow.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <MetricTile label="Publish mode" value={publishMode === 'supervised_publish' ? 'supervised' : 'draft only'} tone="neutral" />
        <MetricTile label="Supervised ready" value={String(readyCount)} tone={readyCount ? 'success' : 'neutral'} />
        <MetricTile label="Needs auth" value={String(authCount)} tone={authCount ? 'warning' : 'neutral'} />
        <MetricTile label="Blocked" value={String(blockedCount)} tone={blockedCount ? 'warning' : 'neutral'} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {items.map((item) => {
          const target = browserTargetFromContentItem(item)
          const session = target ? findSession(sessions, target.service) : null
          const startKey = target ? `browser:start:${target.service}` : ''
          const confirmKey = target ? `browser:confirm:${target.service}` : ''
          const oauthBlocked = hasOAuthSetupBlocker(item)
          return (
            <div key={`${item.platformKey}:${item.connectionId ?? item.accountId ?? 'none'}`} className="rounded-xl border border-border-default bg-elevated p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-display text-base text-text-primary">{item.displayName}</div>
                  <div className="mt-1 font-mono text-xs text-text-tertiary">{item.connectorKey ?? 'browser-only'}</div>
                </div>
                <StatusPill label="Stan" value={item.status} tone={item.connected ? 'ok' : item.status === 'blocked' ? 'warn' : 'muted'} />
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                <SkillDetail label="Konto" value={item.accountLabel ?? (item.connected ? 'polaczone' : 'niepolaczone')} />
                <SkillDetail label="Approval" value={item.requiresApproval ? 'required' : 'not required'} />
                <SkillDetail label="Ostatni test" value={new Date(item.lastCheckedAt).toLocaleString('pl-PL')} />
              </div>
              {item.contentAccountId ? (
                <div className="mt-3 font-mono text-[10px] uppercase text-text-tertiary">
                  Konfiguracja kanału: {item.contentAccountId}
                </div>
              ) : null}
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <SkillList label="Dostepne akcje" items={item.availableActions} />
                <SkillList label="Co blokuje" items={item.blockingReasons.length ? item.blockingReasons : ['brak']} />
              </div>
              {target ? (
                <div className="mt-4 rounded-lg border border-accent-primary/25 bg-base px-3 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="label-eyebrow text-accent-primary">Browserowe logowanie</div>
                      <div className="mt-2 type-body-sm text-text-secondary">
                        Otworz okno, zaloguj sie recznie, potem potwierdz zapis sesji. To jest ten sam mechanizm co
                        w sekcji Browserowe sesje AI.
                      </div>
                    </div>
                    <StatusPill
                      label="Sesja"
                      value={session?.ready ? 'ready' : session?.loginWindowOpen ? 'otwarta' : session?.hasStorageState ? 'zapisana' : 'brak'}
                      tone={session?.ready ? 'ok' : session?.hasStorageState ? 'warn' : 'muted'}
                    />
                  </div>
                  <div className="mt-3 grid gap-2 md:grid-cols-2">
                    <SkillDetail label="Service" value={target.service} />
                    <SkillDetail label="Login URL" value={target.loginUrl} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button type="button" disabled={pendingKey === startKey} onClick={() => onStartBrowserLogin(target)} className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:text-text-disabled">
                      {pendingKey === startKey ? 'Otwieram...' : 'Zaloguj browserowo'}
                    </button>
                    <button
                      type="button"
                      disabled={pendingKey === confirmKey || (!session?.loginWindowOpen && !session?.hasStorageState)}
                      onClick={() => onConfirmBrowserLogin(target.service, target.displayName)}
                      className="rounded-md border border-border-strong px-3 py-2 text-sm text-text-secondary disabled:text-text-disabled"
                    >
                      {pendingKey === confirmKey ? 'Zapisuje...' : 'Potwierdz zapis sesji'}
                    </button>
                  </div>
                </div>
              ) : null}
              <div className="mt-4 flex flex-wrap gap-2">
                {item.needsAuth && item.connectorKey && item.platformKey !== 'bootstrap-mailbox' ? (
                  <button type="button" disabled={oauthBlocked} onClick={() => void onConnect(item.connectorKey ?? item.platformKey, item.displayName, item.contentAccountId)} className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:border-border-default disabled:text-text-disabled">
                    {oauthBlocked ? 'Najpierw konfiguracja OAuth' : item.contentAccountId ? 'Polacz ten kanal' : 'Polacz OAuth'}
                  </button>
                ) : null}
                {item.connectionId != null ? (
                  <button type="button" onClick={() => void onDisconnect(item.connectionId as number, item.displayName)} className="rounded-md border border-border-strong px-3 py-2 text-sm text-text-secondary">
                    Odlacz
                  </button>
                ) : null}
                {item.connectorKey ? (
                  <button type="button" onClick={() => void onProbe(item.connectorKey ?? item.platformKey, item.displayName, item.accountId)} className="rounded-md border border-border-strong px-3 py-2 text-sm text-text-secondary">
                    Test connection
                  </button>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function TrustedAccountEditor({
  account,
  projects,
  sessions,
  pendingKey,
  onStartBrowserLogin,
  onConfirmBrowserLogin,
  onSave,
}: {
  account: TrustedAutoAccount | null
  projects: Array<{ id: number; name: string }>
  sessions: JarvisBrowserSessionSummary[]
  pendingKey: SectionKey | null
  onStartBrowserLogin: (target: BrowserLoginTarget) => void
  onConfirmBrowserLogin: (service: string, displayName: string) => void
  onSave: (draft: Parameters<typeof upsertTrustedAutoAccount>[0]) => Promise<void>
}) {
  const [draft, setDraft] = useState<TrustedAccountDraft>(() => {
    return account ? mapTrustedAccountToDraft(account) : emptyTrustedAccountDraft()
  })
  const [urlInput, setUrlInput] = useState(() => (account ? deriveUrlInputFromDraft(mapTrustedAccountToDraft(account)) : ''))
  const [jsonError, setJsonError] = useState<string | null>(null)
  const suggestion = useMemo(() => detectAccountUrl(urlInput), [urlInput])
  const target = browserTargetFromDraft(draft)
  const session = target ? findSession(sessions, target.service) : null
  const startKey = target ? `browser:start:${target.service}` : ''
  const confirmKey = target ? `browser:confirm:${target.service}` : ''
  const resolvedAccountIdentifier =
    draft.accountIdentifier.trim() || draft.loginIdentifier.trim() || draft.label.trim()

  useEffect(() => {
    if (!account) {
      return
    }
    const nextDraft = mapTrustedAccountToDraft(account)
    setDraft(nextDraft)
    setUrlInput(deriveUrlInputFromDraft(nextDraft))
    setJsonError(null)
  }, [account])

  return (
    <div
      data-testid={account ? `trusted-account-editor-${account.accountId}` : 'trusted-account-editor-new'}
      className="rounded-xl border border-border-default bg-elevated p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-display text-lg text-text-primary">
            {account ? account.label : 'Dodaj realne konto'}
          </div>
          <div className="mt-1 type-body-sm text-text-secondary">
            Podaj nazwe, do czego sluzy konto, login lub email, haslo i URL strony. Haslo trafia tylko do lokalnego Vaulta.
          </div>
        </div>
        {account ? <StatusPill label="Konto" value={account.status} tone={account.hasSecret ? 'ok' : 'muted'} /> : null}
      </div>

      <div className="mt-4 rounded-xl border border-accent-primary/25 bg-base px-4 py-4">
        <div className="label-eyebrow text-accent-primary">Podstawowe dane konta</div>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <TextField label="Nazwa konta" value={draft.label} placeholder="Np. Claude Pro" onChange={(value) => setDraft((state) => ({ ...state, label: value }))} />
          <TextField label="Do czego jest konto" value={draft.purpose} placeholder="Np. upload shortow / poczta / narzedzie AI" onChange={(value) => setDraft((state) => ({ ...state, purpose: value }))} />
          <TextField
            label="Email / login"
            value={draft.loginIdentifier}
            placeholder="account-77a076eb@example.invalid"
            onChange={(value) =>
              setDraft((state) => ({
                ...state,
                loginIdentifier: value,
                accountIdentifier:
                  !state.accountIdentifier.trim() || state.accountIdentifier === state.loginIdentifier
                    ? value
                    : state.accountIdentifier,
              }))
            }
          />
          <TextField label="Haslo" type="password" value={draft.passwordValue} placeholder="Opcjonalnie, Vault" onChange={(value) => setDraft((state) => ({ ...state, passwordValue: value }))} />
          <div className="md:col-span-2 grid gap-3 md:grid-cols-[1fr_auto]">
            <TextField label="URL strony logowania" value={urlInput} placeholder="https://studio.youtube.com/" onChange={setUrlInput} />
            <div className="flex items-end">
              <button
                type="button"
                disabled={!suggestion}
                onClick={() => {
                  if (!suggestion) {
                    return
                  }
                  setDraft((state) => applyUrlSuggestion(state, suggestion))
                  setUrlInput(suggestion.loginUrl)
                }}
                className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:border-border-default disabled:text-text-disabled"
              >
                Wykryj i ustaw
              </button>
            </div>
          </div>
        </div>
        <div className="mt-3 type-body-sm text-text-tertiary">
          To jest prosty tryb dodawania konta. Pola techniczne Atnos ustawia automatycznie z URL, a sekret zapisuje po stronie backendu.
        </div>
      </div>

      <div className="mt-4 rounded-xl border border-border-default bg-base px-4 py-4">
        <div className="label-eyebrow text-text-tertiary">Dodaj przez URL aplikacji</div>
        <div className="mt-3 grid gap-3 md:grid-cols-[1fr_auto]">
          <TextField label="URL aplikacji" value={urlInput} placeholder="https://claude.ai" onChange={setUrlInput} />
          <div className="flex items-end">
            <button
              type="button"
              disabled={!suggestion}
              onClick={() => {
                if (!suggestion) {
                  return
                }
                setDraft((state) => applyUrlSuggestion(state, suggestion))
                setUrlInput(suggestion.loginUrl)
              }}
              className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:border-border-default disabled:text-text-disabled"
            >
              Wykryj technicznie
            </button>
          </div>
        </div>
        {suggestion ? (
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            <SkillDetail label="Wykryto" value={suggestion.displayName} />
            <SkillDetail label="Service" value={suggestion.service} />
            <SkillDetail label="Login" value={suggestion.loginUrl} />
          </div>
        ) : (
          <div className="mt-3 type-body-sm text-text-tertiary">
            Po wklejeniu URL Atnos rozpozna Claude, ChatGPT, Gemini, YouTube, Gmail, VidEdge, FacelessReels,
            Zebracat, CapCut, Canva albo utworzy bezpieczny custom service.
          </div>
        )}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border-default bg-panel px-3 py-3">
          <div>
            <div className="type-body-sm text-text-primary">Preset konta technicznego Atnosa</div>
            <div className="mt-1 text-xs text-text-tertiary">
              Ustawia int.pl i login account-c5034bf4@example.invalid. Haslo wpisz recznie w Vault po zmianie hasla.
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              setDraft((state) => applyAtnosMailboxPreset(state))
              setUrlInput('https://int.pl/')
            }}
            className="rounded-md border border-border-strong px-3 py-2 text-sm text-text-secondary hover:border-accent-primary hover:text-accent-primary"
          >
            Ustaw account-c5034bf4@example.invalid
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <SelectField
          label="Typ konta / connector"
          value={draft.providerKey}
          options={PROVIDER_OPTIONS}
          onChange={(value) => {
            const nextDraft = applyProviderSelection(draft, value)
            setDraft(nextDraft)
            setUrlInput(deriveUrlInputFromDraft(nextDraft))
            setJsonError(null)
          }}
        />
        <TextField label="Identyfikator konta" value={draft.accountIdentifier} placeholder={resolvedAccountIdentifier || 'auto'} onChange={(value) => setDraft((state) => ({ ...state, accountIdentifier: value }))} />
        <SelectField label="Linked project" value={draft.linkedProjectId} options={[{ value: '', label: 'Brak projektu' }, ...projects.map((project) => ({ value: String(project.id), label: project.name }))]} onChange={(value) => setDraft((state) => ({ ...state, linkedProjectId: value }))} />
        <SelectField label="Typ sekretu" value={draft.secretType} options={SECRET_TYPE_OPTIONS} onChange={(value) => setDraft((state) => ({ ...state, secretType: value }))} />
        <TextField label="Glowny sekret" type="password" value={draft.secretValue} placeholder="token / API key / cookie" onChange={(value) => setDraft((state) => ({ ...state, secretValue: value }))} />
      </div>

      <details className="mt-4 rounded-xl border border-border-default bg-panel">
        <summary className="cursor-pointer list-none px-4 py-4 type-body-sm text-text-primary">
          Pola zaawansowane
        </summary>
        <div className="border-t border-border-default px-4 py-4">
          <div className="grid gap-4 md:grid-cols-2">
            <TextField label="Integration key" value={draft.linkedIntegrationKey} onChange={(value) => setDraft((state) => ({ ...state, linkedIntegrationKey: value }))} />
            <TextField label="Capabilities CSV" value={draft.allowedCapabilitiesText} onChange={(value) => setDraft((state) => ({ ...state, allowedCapabilitiesText: value }))} />
            <TextField label="Recovery email" value={draft.recoveryEmail} onChange={(value) => setDraft((state) => ({ ...state, recoveryEmail: value }))} />
            <TextField label="TOTP secret" type="password" value={draft.totpValue} onChange={(value) => setDraft((state) => ({ ...state, totpValue: value }))} />
          </div>
          <div className="mt-4 space-y-4">
            <TextAreaField label="Notes" value={draft.notes} rows={3} onChange={(value) => setDraft((state) => ({ ...state, notes: value }))} />
            <TextAreaField label="connector_mapping_json" value={draft.connectorMappingJsonText} rows={6} onChange={(value) => {
              setDraft((state) => ({ ...state, connectorMappingJsonText: value }))
              setJsonError(null)
            }} />
          </div>
        </div>
      </details>
      {jsonError ? <div className="mt-2 text-xs text-sem-error">{jsonError}</div> : null}

      {target ? (
        <div className="mt-4 rounded-lg border border-border-default bg-base px-3 py-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="label-eyebrow">Browser session</div>
              <div className="mt-2 type-body-sm text-text-secondary">
                {session?.statusReason ?? 'Brak zapisanej sesji. Otworz logowanie i potwierdz zapis.'}
              </div>
            </div>
            <StatusPill label="Sesja" value={session?.ready ? 'ready' : session?.loginWindowOpen ? 'otwarta' : session?.hasStorageState ? 'zapisana' : 'brak'} tone={session?.ready ? 'ok' : session?.hasStorageState ? 'warn' : 'muted'} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" disabled={pendingKey === startKey} onClick={() => onStartBrowserLogin(target)} className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:text-text-disabled">
              {pendingKey === startKey ? 'Otwieram...' : 'Zaloguj browserowo'}
            </button>
            <button type="button" disabled={pendingKey === confirmKey || (!session?.loginWindowOpen && !session?.hasStorageState)} onClick={() => onConfirmBrowserLogin(target.service, target.displayName)} className="rounded-md border border-border-strong px-3 py-2 text-sm text-text-secondary disabled:text-text-disabled">
              {pendingKey === confirmKey ? 'Zapisuje...' : 'Potwierdz zapis sesji'}
            </button>
          </div>
        </div>
      ) : null}

      <div className="mt-4">
        <button
          type="button"
          disabled={pendingKey === 'trusted:account:new' || pendingKey === `trusted:account:${account?.accountId ?? -1}`}
          onClick={() => {
            let connectorMappingJson: Record<string, unknown>
            try {
              connectorMappingJson = draft.connectorMappingJsonText.trim()
                ? (JSON.parse(draft.connectorMappingJsonText) as Record<string, unknown>)
                : {}
            } catch {
              setJsonError('connector_mapping_json musi byc poprawnym JSON-em.')
              return
            }
            const normalizedUrl = normalizeUrlInput(urlInput)
            const detectedSuggestion = normalizedUrl ? detectAccountUrl(normalizedUrl) : null
            if (normalizedUrl) {
              const service =
                typeof connectorMappingJson.browser_session_service === 'string'
                  ? connectorMappingJson.browser_session_service
                  : detectedSuggestion?.service ?? draft.providerKey
              connectorMappingJson = {
                ...connectorMappingJson,
                login_url:
                  typeof connectorMappingJson.login_url === 'string'
                    ? connectorMappingJson.login_url
                    : detectedSuggestion?.loginUrl ?? normalizedUrl,
                validation_url:
                  typeof connectorMappingJson.validation_url === 'string'
                    ? connectorMappingJson.validation_url
                    : detectedSuggestion?.validationUrl ?? normalizedUrl,
                browser_session_service: service,
                source_url:
                  typeof connectorMappingJson.source_url === 'string'
                    ? connectorMappingJson.source_url
                    : normalizedUrl,
                auto_send:
                  typeof connectorMappingJson.auto_send === 'boolean'
                    ? connectorMappingJson.auto_send
                    : false,
              }
            }
            connectorMappingJson = withDraftPurpose(connectorMappingJson, draft.purpose)
            const shouldUseDetectedProvider =
              Boolean(detectedSuggestion) &&
              (!draft.linkedIntegrationKey.trim() || draft.providerKey === 'youtube' || draft.providerKey === 'custom')
            const providerKey = shouldUseDetectedProvider
              ? detectedSuggestion?.providerKey ?? draft.providerKey
              : draft.providerKey
            const linkedIntegrationKey = shouldUseDetectedProvider
              ? detectedSuggestion?.linkedIntegrationKey ?? draft.linkedIntegrationKey
              : draft.linkedIntegrationKey
            const secretType = shouldUseDetectedProvider
              ? detectedSuggestion?.secretType ?? draft.secretType
              : draft.secretType
            const allowedCapabilitiesText = shouldUseDetectedProvider
              ? detectedSuggestion?.allowedCapabilitiesText ?? draft.allowedCapabilitiesText
              : draft.allowedCapabilitiesText
            void onSave({
              accountId: account?.accountId ?? null,
              label: draft.label.trim() || resolvedAccountIdentifier || 'Nowe konto',
              providerKey,
              accountIdentifier: resolvedAccountIdentifier || draft.label.trim() || 'new-account',
              secretType,
              secretValue: draft.secretValue.trim() || null,
              linkedProjectId: draft.linkedProjectId ? Number(draft.linkedProjectId) : null,
              linkedIntegrationKey: linkedIntegrationKey.trim() || null,
              notes: buildAccountNotes(draft, connectorMappingJson) || null,
              allowedCapabilities: parseCsvList(allowedCapabilitiesText),
              loginIdentifier: draft.loginIdentifier.trim() || null,
              passwordValue: draft.passwordValue.trim() || null,
              totpValue: draft.totpValue.trim() || null,
              backupCodesValue: draft.backupCodesValue.trim() || null,
              recoveryEmail: draft.recoveryEmail.trim() || null,
              recoveryEmailSecretValue: draft.recoveryEmailSecretValue.trim() || null,
              connectorMappingJson,
            })
          }}
          className={primaryButtonClass(false)}
        >
          {account ? 'Zapisz konto' : 'Dodaj konto'}
        </button>
      </div>
    </div>
  )
}

function JarvisBrowserSessionsCard({
  sessions,
  pendingKey,
  onRefresh,
  onStart,
  onConfirm,
}: {
  sessions: JarvisBrowserSessionSummary[]
  pendingKey: SectionKey | null
  onRefresh: () => void | Promise<void>
  onStart: (service: string) => void
  onConfirm: (service: string, displayName: string) => void
}) {
  const readyCount = sessions.filter((item) => item.ready).length
  const pendingCount = sessions.filter((item) => item.pendingManualLogin || item.loginWindowOpen).length
  const expiredCount = sessions.filter((item) => item.expired).length
  return (
    <SectionCard
      icon={<ShieldCheck size={18} className="text-accent-primary" />}
      title="Browserowe sesje AI"
      description="Reczne logowania zapisane jako storage state. Ten sam mechanizm obsluguje Claude, ChatGPT, Gemini i narzedzia do shortow."
      action={
        <button type="button" onClick={() => void onRefresh()} className="rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary">
          Odswiez sesje
        </button>
      }
    >
      <div className="grid gap-3 md:grid-cols-4">
        <MetricTile label="Sesje" value={String(sessions.length)} tone="neutral" />
        <MetricTile label="Ready" value={String(readyCount)} tone="success" />
        <MetricTile label="Pending" value={String(pendingCount)} tone={pendingCount ? 'warning' : 'neutral'} />
        <MetricTile label="Expired" value={String(expiredCount)} tone={expiredCount ? 'warning' : 'neutral'} />
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {sessions.map((session) => {
          const startKey = `browser:start:${session.service}`
          const confirmKey = `browser:confirm:${session.service}`
          return (
            <div key={session.service} className="rounded-lg border border-border-default bg-base px-3 py-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="type-body text-text-primary">{session.displayName}</div>
                  <div className="mt-1 type-body-sm text-text-tertiary">{session.service}</div>
                </div>
                <StatusPill label="Stan" value={session.ready ? 'ready' : session.loginWindowOpen ? 'otwarte' : session.hasStorageState ? 'zapisane' : 'brak'} tone={session.ready ? 'ok' : session.hasStorageState ? 'warn' : 'muted'} />
              </div>
              <div className="mt-3 type-body-sm text-text-secondary">{session.statusReason}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" disabled={pendingKey === startKey} onClick={() => onStart(session.service)} className="rounded-md border border-accent-primary px-3 py-2 text-sm text-accent-primary disabled:text-text-disabled">
                  {pendingKey === startKey ? 'Otwieram...' : session.ready ? 'Odswiez login' : 'Zaloguj'}
                </button>
                <button type="button" disabled={pendingKey === confirmKey || (!session.loginWindowOpen && !session.hasStorageState)} onClick={() => onConfirm(session.service, session.displayName)} className="rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary disabled:text-text-disabled">
                  {pendingKey === confirmKey ? 'Zapisuje...' : 'Potwierdz zapis'}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </SectionCard>
  )
}

function AiDelegationPanel({
  workersStatus,
  tasks,
  runtimeHealth,
  desktopApps,
  localHostExecutionEnabled,
  prompt,
  pendingKey,
  onPromptChange,
  onRefresh,
  onProbe,
  onDelegate,
  onPlan,
  onRunTask,
  onOpenDesktopApp,
  onFocusDesktopApp,
}: {
  workersStatus: AiWorkersStatusPayload | null
  tasks: AiWorkerTask[]
  runtimeHealth: RuntimeHealthPayload | null
  desktopApps: LocalHostDesktopApp[] | null
  localHostExecutionEnabled: boolean | null
  prompt: string
  pendingKey: SectionKey | null
  onPromptChange: (value: string) => void
  onRefresh: () => void | Promise<void>
  onProbe: (workerKey: string) => void
  onDelegate: () => void
  onPlan: () => void
  onRunTask: (taskId: number) => void
  onOpenDesktopApp: (app: LocalHostDesktopApp) => void
  onFocusDesktopApp: (app: LocalHostDesktopApp) => void
}) {
  const workers = workersStatus?.workers ?? []
  const readyWorkers = workers.filter((worker) => worker.ready).length
  const blockedWorkers = workers.filter((worker) => !worker.ready).length
  const runtimeSummary = runtimeHealth?.summary
  const runtimeReadiness = runtimeHealth?.readiness
  const latestTasks = tasks.slice(0, 6)
  const importantAppOrder = [
    'atnos-desktop',
    'comfy-desktop',
    'kimi',
    'capcut',
    'github-desktop',
    'docker-desktop',
    'git-bash',
  ]
  const desktopAppSnapshot = desktopApps ?? []
  const importantApps = importantAppOrder
    .map((appKey) => desktopAppSnapshot.find((app) => app.appKey === appKey))
    .filter((app): app is LocalHostDesktopApp => Boolean(app))
  const extraApps = desktopAppSnapshot
    .filter((app) => !importantAppOrder.includes(app.appKey))
    .slice(0, Math.max(0, 8 - importantApps.length))
  const visibleDesktopApps = [...importantApps, ...extraApps]

  const runtimeTone = runtimeHealth?.status === 'ready' ? 'success' : runtimeHealth ? 'warning' : 'neutral'

  return (
    <div className="rounded-xl border border-border-default bg-elevated px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-display text-lg text-text-primary">Runtime & AI Delegation</div>
          <div className="mt-1 max-w-3xl type-body-sm text-text-secondary">
            Kontrolowana warstwa delegacji: Ollama robi lokalne drafty, browser AI dostaje tylko supervised handoff. Publikacja,
            trading, platnosci, OAuth i hasla sa blokowane poza action brokerem.
          </div>
        </div>
        <button type="button" onClick={() => void onRefresh()} className="rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary">
          Odswiez
        </button>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <MetricTile label="Runtime" value={runtimeHealth?.status ?? 'loading'} tone={runtimeTone} />
        <MetricTile label="Workerzy ready" value={`${readyWorkers}/${workers.length || '-'}`} tone={readyWorkers ? 'success' : 'warning'} />
        <MetricTile label="Workerzy blocked" value={String(blockedWorkers)} tone={blockedWorkers ? 'warning' : 'neutral'} />
        <MetricTile label="Tryb" value={workersStatus?.mode ?? 'loading'} tone="neutral" />
      </div>

      {runtimeReadiness ? (
        <div className={`mt-3 rounded-lg border px-3 py-3 ${
          runtimeReadiness.verdict === 'ready'
            ? 'border-success/50 bg-success/10'
            : runtimeReadiness.verdict === 'degraded'
              ? 'border-warning/50 bg-warning/10'
              : 'border-error/50 bg-error/10'
        }`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="label-eyebrow">Werdykt runtime</div>
              <div className="mt-1 type-body text-text-primary">{runtimeReadiness.headline}</div>
            </div>
            <StatusPill
              label="Verdict"
              value={runtimeReadiness.verdict}
              tone={runtimeReadiness.verdict === 'ready' ? 'ok' : 'warn'}
            />
          </div>
          <div className="mt-3 rounded-md border border-border-default bg-panel px-3 py-2 type-body-sm text-text-secondary">
            Następny krok: {runtimeReadiness.firstAction || 'Brak akcji.'}
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            <SkillDetail label="Źródło" value={String(runtimeReadiness.evidence.source ?? 'runtime_health_components')} tone="neutral" />
            <SkillDetail label="Komponenty" value={String(runtimeReadiness.evidence.component_count ?? runtimeHealth?.components.length ?? 0)} tone="neutral" />
            <SkillDetail label="Core może działać" value={runtimeReadiness.canRunCore ? 'tak' : 'nie'} tone={runtimeReadiness.canRunCore ? 'success' : 'warning'} />
          </div>
        </div>
      ) : null}

      {runtimeHealth?.launcher ? (
        <div className="mt-3 rounded-lg border border-border-default bg-panel px-3 py-3">
          <div className="label-eyebrow">Launcher runtime</div>
          <div className="mt-2 break-all font-mono text-xs text-text-secondary">{runtimeHealth.launcher.command}</div>
          <div className="mt-2 type-body-sm text-text-tertiary">
            Script: {runtimeHealth.launcher.exists ? 'ready' : 'missing'} / logs: {runtimeHealth.launcher.logDir}
          </div>
        </div>
      ) : null}

      {runtimeSummary ? (
        <div className="mt-3 grid gap-3 md:grid-cols-4">
          <SkillDetail label="Ready" value={String(runtimeSummary.ready)} tone="success" />
          <SkillDetail label="Degraded" value={String(runtimeSummary.degraded)} tone={runtimeSummary.degraded ? 'warning' : 'neutral'} />
          <SkillDetail label="Missing" value={String(runtimeSummary.missing)} tone={runtimeSummary.missing ? 'warning' : 'neutral'} />
          <SkillDetail label="Blocked" value={String(runtimeSummary.blocked)} tone={runtimeSummary.blocked ? 'warning' : 'neutral'} />
        </div>
      ) : null}

      <LocalDesktopAppsPanel
        apps={visibleDesktopApps}
        executionEnabled={localHostExecutionEnabled}
        pendingKey={pendingKey}
        onOpen={onOpenDesktopApp}
        onFocus={onFocusDesktopApp}
      />

      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {workers.length ? (
          workers.map((worker) => {
            const probeKey: SectionKey = `ai-worker:${worker.workerKey}`
            return (
              <div key={worker.workerKey} className="rounded-lg border border-border-default bg-panel px-3 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="type-body text-text-primary">{worker.label || worker.workerKey}</div>
                    <div className="mt-1 font-mono text-xs text-text-tertiary">{worker.workerKey}</div>
                  </div>
                  <StatusPill label="Stan" value={worker.status} tone={worker.ready ? 'ok' : 'warn'} />
                </div>
                <div className="mt-3 type-body-sm text-text-secondary">{worker.reason || 'Brak szczegolow.'}</div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {worker.capabilities.slice(0, 3).map((capability) => (
                    <span key={capability} className="rounded-pill border border-border-default px-2 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-text-tertiary">
                      {capability}
                    </span>
                  ))}
                </div>
                <button
                  type="button"
                  disabled={pendingKey === probeKey}
                  onClick={() => onProbe(worker.workerKey)}
                  className="mt-3 rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary disabled:text-text-disabled"
                >
                  {pendingKey === probeKey ? 'Testuje...' : 'Test workera'}
                </button>
              </div>
            )
          })
        ) : (
          <div className="rounded-lg border border-border-default bg-panel px-3 py-3 type-body-sm text-text-secondary md:col-span-2 xl:col-span-4">
            Brak danych o workerach. Sprawdz backend `/api/ai-workers/status`.
          </div>
        )}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
          <TextAreaField
            label="Test delegacji lokalnej"
            value={prompt}
            rows={5}
            onChange={onPromptChange}
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={pendingKey === 'ai-worker:delegate' || !prompt.trim()}
              onClick={onDelegate}
              className={primaryButtonClass(pendingKey === 'ai-worker:delegate' || !prompt.trim())}
            >
              {pendingKey === 'ai-worker:delegate' ? 'Deleguje...' : 'Deleguj do Ollamy'}
            </button>
            <button
              type="button"
              disabled={pendingKey === 'ai-worker:plan' || !prompt.trim()}
              onClick={onPlan}
              className={primaryButtonClass(pendingKey === 'ai-worker:plan' || !prompt.trim())}
            >
              {pendingKey === 'ai-worker:plan' ? 'Tworze plan...' : 'Rozbij na podzadania'}
            </button>
            <button type="button" onClick={() => void onRefresh()} className="rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary">
              Odswiez kolejke
            </button>
          </div>
          <div className="mt-3 type-body-sm text-text-tertiary">
            Smoke test zapisuje task w DB, redaguje sekrety i uruchamia tylko `strict_local`.
          </div>
        </div>

        <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="font-display text-base text-text-primary">Kolejka delegacji</div>
            <StatusPill label="Tasks" value={String(tasks.length)} tone={tasks.length ? 'ok' : 'muted'} />
          </div>
          <div className="space-y-2">
            {latestTasks.length ? (
              latestTasks.map((task) => (
                <div key={task.id} className="rounded-md border border-border-default bg-base px-3 py-2">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="type-body-sm text-text-primary">{task.taskType}</div>
                      <div className="mt-1 font-mono text-xs text-text-tertiary">#{task.id} / {task.workerKey ?? 'unassigned'}</div>
                    </div>
                    <StatusPill
                      label="Status"
                      value={task.status}
                      tone={task.status === 'completed' ? 'ok' : task.status === 'blocked' || task.status === 'failed' ? 'warn' : 'muted'}
                    />
                  </div>
                  <div className="mt-2 line-clamp-2 type-body-sm text-text-secondary">
                    {task.resultSummary ?? task.blockedReason ?? task.errorMessage ?? task.inputSummary ?? 'Brak wyniku.'}
                  </div>
                  {task.children.length ? (
                    <div className="mt-2 space-y-1 border-t border-border-default pt-2">
                      {task.children.slice(0, 5).map((child) => (
                        <div key={child.id} className="flex items-start justify-between gap-2 font-mono text-[11px] text-text-tertiary">
                          <span>
                            {child.sequenceIndex}. {child.taskRole}: {child.inputSummary ?? child.taskType}
                          </span>
                          <span className="flex shrink-0 items-center gap-2">
                            <span>{child.status}</span>
                            {['queued', 'planned', 'failed'].includes(child.status) ? (
                              <button
                                type="button"
                                disabled={pendingKey === `ai-worker:run:${child.id}`}
                                onClick={() => onRunTask(child.id)}
                                className="rounded border border-border-default px-2 py-1 text-[10px] uppercase tracking-[0.12em] text-accent-primary disabled:text-text-disabled"
                              >
                                {pendingKey === `ai-worker:run:${child.id}` ? 'RUN...' : 'RUN'}
                              </button>
                            ) : null}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="rounded-md border border-border-default bg-base px-3 py-3 type-body-sm text-text-secondary">
                Brak taskow. Uruchom test delegacji albo sprawdz backend.
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <SkillList
          label="Guardrails"
          items={Object.entries(workersStatus?.guardrails ?? {}).map(([key, value]) => `${key}: ${String(value)}`)}
        />
        <SkillList
          label="Najblizsze braki runtime"
          items={(runtimeHealth?.components ?? [])
            .filter((component) => component.status !== 'ready')
            .slice(0, 5)
            .map((component) => `${component.label}: ${component.nextAction || component.detail || component.status}`)}
        />
      </div>
    </div>
  )
}

function LocalDesktopAppsPanel({
  apps,
  executionEnabled,
  pendingKey,
  onOpen,
  onFocus,
}: {
  apps: LocalHostDesktopApp[]
  executionEnabled: boolean
  pendingKey: SectionKey | null
  onOpen: (app: LocalHostDesktopApp) => void
  onFocus: (app: LocalHostDesktopApp) => void
}) {
  return (
    <div className="mt-4 rounded-lg border border-border-default bg-panel px-3 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-display text-base text-text-primary">Aplikacje PC dla Atnosa</div>
          <div className="mt-1 type-body-sm text-text-secondary">
            Realny snapshot z backendu local-host. Atnos moze otwierac lub przywolywac tylko wykryte aplikacje i tylko przez kontrolowane endpointy.
          </div>
        </div>
        <StatusPill
          label="Local host"
          value={executionEnabled == null ? 'loading' : executionEnabled ? 'enabled' : 'disabled'}
          tone={executionEnabled == null ? 'muted' : executionEnabled ? 'ok' : 'warn'}
        />
      </div>
      <div className="mt-2 font-mono text-[11px] uppercase tracking-[0.14em] text-text-tertiary">
        Zrodlo: /api/settings/control-center to localHost.desktopApps
      </div>

      {apps == null ? (
        <div className="mt-4 rounded-md border border-border-default bg-base px-3 py-3 type-body-sm text-text-secondary">
          Laduje snapshot aplikacji PC z backendu local-host...
        </div>
      ) : apps.length ? (
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {apps.map((app) => {
            const status = app.installed
              ? app.running
                ? app.windowVisible
                  ? 'visible'
                  : 'running'
                : 'installed'
              : 'missing'
            const tone = app.installed ? (app.running ? 'ok' : 'warn') : 'muted'
            const openKey: SectionKey = `local-host:open:${app.appKey}`
            const focusKey: SectionKey = `local-host:focus:${app.appKey}`
            const openDisabled = executionEnabled !== true || !app.launchSupported || pendingKey === openKey
            const focusDisabled = executionEnabled !== true || !app.focusSupported || !app.running || pendingKey === focusKey
            return (
              <div key={app.appKey} className="rounded-lg border border-border-default bg-base px-3 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="type-body text-text-primary">{app.label}</div>
                    <div className="mt-1 font-mono text-xs text-text-tertiary">{app.appKey}</div>
                  </div>
                  <StatusPill label="Stan" value={status} tone={tone} />
                </div>
                <div className="mt-3 grid gap-2 md:grid-cols-2">
                  <SkillDetail
                    label="Procesy"
                    value={app.runningProcesses.length ? app.runningProcesses.join(', ') : 'brak'}
                    tone={app.running ? 'success' : 'neutral'}
                  />
                  <SkillDetail
                    label="Okno"
                    value={app.visibleWindowTitles[0] ?? (app.windowVisible ? 'widoczne' : 'brak widocznego okna')}
                    tone={app.windowVisible ? 'success' : 'neutral'}
                  />
                </div>
                <div className="mt-3 break-all rounded-md border border-border-default bg-panel px-3 py-2 font-mono text-[11px] text-text-secondary">
                  {app.preferredEntryPath ?? 'Brak wykrytej sciezki uruchomienia'}
                </div>
                {app.notes.length ? (
                  <div className="mt-2 type-body-sm text-text-tertiary">{app.notes.slice(0, 2).join(' ')}</div>
                ) : null}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={openDisabled}
                    onClick={() => onOpen(app)}
                    className="rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary disabled:text-text-disabled"
                  >
                    {pendingKey === openKey ? 'Otwieram...' : 'Otworz'}
                  </button>
                  <button
                    type="button"
                    disabled={focusDisabled}
                    onClick={() => onFocus(app)}
                    className="rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary disabled:text-text-disabled"
                  >
                    {pendingKey === focusKey ? 'Przywoluje...' : 'Przywolaj'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="mt-4 rounded-md border border-border-default bg-base px-3 py-3 type-body-sm text-text-secondary">
          Brak snapshotu aplikacji PC. Sprawdz endpoint `/api/settings/control-center` albo backend local-host.
        </div>
      )}
    </div>
  )
}

function AmbientRuntimeSection({
  reason,
  audioInputDevices,
  ambientListeningEnabled,
  ambientWakeWord,
  ambientMicDevice,
  ambientMuted,
  ambientContextEnabled,
  onChange,
}: {
  reason: string
  audioInputDevices: Array<{ value: string; label: string }>
  ambientListeningEnabled: boolean
  ambientWakeWord: string
  ambientMicDevice: string
  ambientMuted: boolean
  ambientContextEnabled: boolean
  onChange: (
    patch: Partial<{
      ambientListeningEnabled: boolean
      ambientWakeWord: string
      ambientMicDevice: string
      ambientMuted: boolean
      ambientContextEnabled: boolean
    }>,
  ) => void
}) {
  return (
    <div className="rounded-xl border border-border-default bg-elevated px-4 py-4">
      <div>
        <div className="font-display text-base text-text-primary">Ambient listening</div>
        <div className="mt-2 type-body-sm text-text-secondary">{reason || 'Ambient listening jest gotowy.'}</div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <ToggleRow
          label="Wlacz nasluchiwanie"
          checked={ambientListeningEnabled}
          onChange={(checked) => onChange({ ambientListeningEnabled: checked })}
        />
        <ToggleRow
          label="Ambient context aware"
          checked={ambientContextEnabled}
          onChange={(checked) => onChange({ ambientContextEnabled: checked })}
        />
        <ToggleRow
          label="Ambient muted"
          checked={ambientMuted}
          onChange={(checked) => onChange({ ambientMuted: checked })}
        />
        <label className="block rounded-lg border border-border-default bg-panel px-3 py-3">
          <span className="label-eyebrow">Wake word</span>
          <input
            value={ambientWakeWord}
            onChange={(event) => onChange({ ambientWakeWord: event.target.value })}
            placeholder="Atnos"
            className="mt-2 w-full rounded-md border border-border-default bg-base px-3 py-2 text-sm text-text-primary outline-none transition focus:border-accent-primary"
          />
        </label>
        <label className="block rounded-lg border border-border-default bg-panel px-3 py-3">
          <span className="label-eyebrow">Mikrofon / device</span>
          <select
            value={ambientMicDevice}
            onChange={(event) => onChange({ ambientMicDevice: event.target.value })}
            className="mt-2 w-full rounded-md border border-border-default bg-base px-3 py-2 text-sm text-text-primary outline-none transition focus:border-accent-primary"
          >
            <option value="">Domyslny mikrofon</option>
            {audioInputDevices.map((device) => (
              <option key={device.value} value={device.value}>
                {device.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  )
}

function DecisionLogSection({ entries }: { entries: SettingsDecisionLogEntry[] }) {
  const [filter, setFilter] = useState<'active' | 'resolved' | 'overdue'>('active')
  const now = Date.now()
  const visibleEntries = entries.filter((entry) => {
    const isOverdue = entry.status === 'active' && entry.dueAt != null && Date.parse(entry.dueAt) < now
    if (filter === 'overdue') return isOverdue
    return entry.status === filter
  })

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {(['active', 'resolved', 'overdue'] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setFilter(option)}
            className={`rounded-md border px-3 py-2 text-sm ${
              filter === option
                ? 'border-accent-primary text-accent-primary'
                : 'border-border-strong text-text-secondary'
            }`}
          >
            {option === 'active' ? 'Active' : option === 'resolved' ? 'Resolved' : 'Overdue'}
          </button>
        ))}
      </div>
      {visibleEntries.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {visibleEntries.map((entry) => (
            <div key={entry.decisionId} className="rounded-xl border border-border-default bg-elevated px-4 py-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="type-body text-text-primary">{entry.summary}</div>
                  <div className="mt-1 font-mono text-xs text-text-tertiary">{entry.kind}</div>
                </div>
                <StatusPill label="Status" value={entry.status} tone={entry.status === 'resolved' ? 'ok' : 'warn'} />
              </div>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <SkillDetail label="Made" value={new Date(entry.madeAt).toLocaleString('pl-PL')} />
                <SkillDetail label="Due" value={entry.dueAt ? new Date(entry.dueAt).toLocaleString('pl-PL') : 'brak'} />
              </div>
              {entry.resolutionSummary ? (
                <div className="mt-3 rounded-lg border border-border-default bg-panel px-3 py-3 type-body-sm text-text-secondary">
                  {entry.resolutionSummary}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-border-default bg-elevated px-4 py-4 type-body-sm text-text-secondary">
          Brak wpisow dla tego filtra.
        </div>
      )}
    </div>
  )
}

function hasOAuthSetupBlocker(item: ContentReadinessItem): boolean {
  return item.blockingReasons.some((reason) => {
    const normalized = reason.toLowerCase()
    return (
      normalized.includes('oauth is disabled') ||
      normalized.includes('oauth client id is missing') ||
      normalized.includes('oauth client secret ref is missing') ||
      normalized.includes('oauth provider is not configured')
    )
  })
}

function SectionCard({
  icon,
  title,
  description,
  action,
  children,
}: {
  icon: ReactNode
  title: string
  description: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="shell-panel p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 gap-3">
          <div className="mt-1">{icon}</div>
          <div>
            <h2 className="type-h2 text-text-primary">{title}</h2>
            <p className="mt-2 max-w-3xl type-body-sm text-text-secondary">{description}</p>
          </div>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

function MetricTile({ label, value, tone }: { label: string; value: string; tone: 'success' | 'warning' | 'neutral' }) {
  const toneClass =
    tone === 'success'
      ? 'text-sem-success'
      : tone === 'warning'
        ? 'text-sem-warning'
        : 'text-text-primary'
  return (
    <div className="rounded-xl border border-border-default bg-elevated px-4 py-3">
      <div className="label-eyebrow">{label}</div>
      <div className={`mt-2 font-display text-xl ${toneClass}`}>{value}</div>
    </div>
  )
}

function RuntimeCapabilityStatusPanel() {
  const [inventory, setInventory] = useState<RuntimeInventoryPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setInventory(await fetchRuntimeInventory())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Nie udało się sprawdzić runtime.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const providers = useMemo(
    () => new Map((inventory?.providers ?? []).map((provider) => [provider.provider_key, provider])),
    [inventory?.providers],
  )
  const models = useMemo(
    () => new Map((inventory?.models ?? []).map((model) => [model.model_key, model])),
    [inventory?.models],
  )

  const taskLabels: Record<string, string> = {
    general_chat: 'Rozmowa',
    orchestration: 'Planowanie i koordynacja',
    coding: 'Kodowanie',
    search_research: 'Research',
    vision: 'Analiza obrazu',
    transcription: 'Transkrypcja',
    synthesis: 'Synteza dokumentów',
    image_generation: 'Generowanie obrazów',
  }

  const modelState = (modelKey: string | null) => {
    if (!modelKey) return { label: 'brak modelu', tone: 'warn' as const, detail: '' }
    const model = models.get(modelKey)
    const provider = providers.get(model?.provider_key ?? modelKey.split('/')[0])
    if (!model || !provider || !provider.is_enabled || model.status !== 'ready') {
      return { label: 'wymaga konfiguracji', tone: 'warn' as const, detail: model?.runtime_status_reason ?? '' }
    }
    if (provider.local_first && provider.health_status === 'healthy' && model.runtime_model_present) {
      return {
        label: 'działa lokalnie',
        tone: 'ok' as const,
        detail: model.resolved_runtime_model_name ?? model.model_key,
      }
    }
    if (!provider.local_first && provider.health_status === 'healthy') {
      return {
        label: 'API odpowiada; użycie zewnętrzne wymaga odpowiedniego trybu i może kosztować',
        tone: 'warn' as const,
        detail: provider.label,
      }
    }
    if (!provider.local_first && provider.health_status === 'not_supported') {
      return {
        label: 'adapter gotowy; połączenie niezweryfikowane',
        tone: 'warn' as const,
        detail: 'Dostawca nie udostępnia sondy zdrowia.',
      }
    }
    return {
      label: provider.local_first ? 'runtime lokalny niedostępny' : 'wymaga konta lub konfiguracji',
      tone: 'warn' as const,
      detail: provider.health_reason || model.runtime_status_reason,
    }
  }

  return (
    <section data-testid="runtime-capability-status" className="rounded-xl border border-border-default bg-elevated p-4" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-base text-text-primary">Rzeczywisty status możliwości</h3>
          <p className="mt-1 max-w-3xl type-body-sm text-text-secondary">
            Rozdziela obecność adaptera od działającego modelu. Zewnętrzne API nie jest lokalnym modelem; jego użycie może wymagać konta, jawnego trybu i kosztować.
          </p>
        </div>
        <button type="button" onClick={() => void refresh()} disabled={loading} className="rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary disabled:opacity-60">
          {loading ? 'Sprawdzam...' : 'Odśwież test runtime'}
        </button>
      </div>

      {error ? <div role="alert" className="mt-3 rounded-lg border border-error/50 bg-error/10 px-3 py-2 type-body-sm text-text-primary">Test runtime nie powiódł się: {error}</div> : null}

      {inventory ? (
        <div className="mt-4 grid gap-4 xl:grid-cols-[1.25fr_1fr]">
          <div>
            <div className="label-eyebrow mb-2">Zadania i modele</div>
            <div className="space-y-2">
              {inventory.tasks.map((task) => {
                const local = modelState(task.default_local_model_key)
                const external = task.default_external_model_key ? modelState(task.default_external_model_key) : null
                return (
                  <div key={task.task} className="rounded-lg border border-border-default bg-panel px-3 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="type-body text-text-primary">{taskLabels[task.task] ?? task.task}</div>
                      <StatusPill label="Lokalnie" value={local.label} tone={local.tone} />
                    </div>
                    {local.detail ? <div className="mt-1 break-all font-mono text-xs text-text-tertiary">{local.detail}</div> : null}
                    {external ? (
                      <div className="mt-2 border-t border-border-default pt-2">
                        <div className="type-body-sm text-text-secondary">Zewnętrzny fallback: {external.label}</div>
                        {external.detail ? <div className="mt-1 break-all font-mono text-xs text-text-tertiary">{external.detail}</div> : null}
                      </div>
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>

          <div className="space-y-4">
            <div>
              <div className="label-eyebrow mb-2">Dostawcy</div>
              <div className="space-y-2">
                {inventory.providers.map((provider) => {
                  const state = provider.is_enabled && provider.status === 'ready'
                    ? provider.local_first
                      ? provider.health_status === 'healthy' ? 'działa lokalnie' : 'adapter jest, runtime wymaga sprawdzenia'
                      : provider.health_status === 'healthy' ? 'zewnętrzne API odpowiada' : 'adapter gotowy, połączenie niepotwierdzone'
                    : provider.status === 'disabled' ? 'wyłączony' : 'wymaga konfiguracji'
                  const tone = provider.is_enabled && provider.status === 'ready' && provider.health_status === 'healthy' && provider.local_first ? 'ok' : 'warn'
                  return (
                    <div key={provider.provider_key} className="flex items-start justify-between gap-3 rounded-lg border border-border-default bg-panel px-3 py-2">
                      <div>
                        <div className="type-body-sm text-text-primary">{provider.label}{provider.local_first ? ' · lokalny' : ' · zewnętrzny'}</div>
                        <div className="mt-1 type-caption text-text-tertiary">{provider.health_reason || provider.status_reason}</div>
                      </div>
                      <StatusPill label="Runtime" value={state} tone={tone} />
                    </div>
                  )
                })}
              </div>
            </div>

            <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="type-body text-text-primary">Transkrypcja mowy</div>
                <StatusPill
                  label="Stan"
                  value={inventory.voice_stt.status === 'ready' ? inventory.voice_stt.effective_engine : inventory.voice_stt.status}
                  tone={inventory.voice_stt.status === 'ready' ? 'ok' : 'warn'}
                />
              </div>
              <div className="mt-1 type-body-sm text-text-tertiary">{inventory.voice_stt.status_reason}</div>
            </div>
          </div>
        </div>
      ) : !loading && !error ? (
        <div className="mt-3 type-body-sm text-text-secondary">Brak odczytu runtime. Użyj odświeżenia, aby sprawdzić możliwości.</div>
      ) : null}
    </section>
  )
}

function StatusPill({ label, value, tone }: { label: string; value: string; tone: 'ok' | 'warn' | 'muted' }) {
  const toneClass =
    tone === 'ok'
      ? 'border-sem-success/40 text-sem-success'
      : tone === 'warn'
        ? 'border-sem-warning/40 text-sem-warning'
        : 'border-border-strong text-text-tertiary'
  return (
    <span className={`inline-flex rounded-pill border px-3 py-1 font-mono text-xs uppercase tracking-[0.18em] ${toneClass}`}>
      {label}: {value}
    </span>
  )
}

function SkillDetail({
  label,
  value,
  title,
  body,
  tone = 'neutral',
}: {
  label?: string
  value?: string
  title?: string
  body?: string
  tone?: 'success' | 'warning' | 'neutral'
}) {
  const toneClass =
    tone === 'success'
      ? 'border-sem-success/30 bg-sem-success/8'
      : tone === 'warning'
        ? 'border-sem-warning/30 bg-sem-warning/8'
        : 'border-border-default bg-panel'
  return (
    <div className={`rounded-lg border px-3 py-3 ${toneClass}`}>
      <div className="label-eyebrow">{label ?? title}</div>
      <div className="mt-2 break-words type-body-sm text-text-secondary">{value ?? body ?? 'brak'}</div>
    </div>
  )
}

function SkillList({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="rounded-lg border border-border-default bg-panel px-3 py-3">
      <div className="label-eyebrow">{label}</div>
      <div className="mt-2 space-y-1 type-body-sm text-text-secondary">
        {items.length ? items.map((item) => <div key={item}>- {item}</div>) : <div>- brak</div>}
      </div>
    </div>
  )
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: string
}) {
  return (
    <label className="block">
      <span className="label-eyebrow">{label}</span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full rounded-md border border-border-default bg-base px-3 py-2 text-sm text-text-primary outline-none transition focus:border-accent-primary"
      />
    </label>
  )
}

function TextAreaField({
  label,
  value,
  onChange,
  rows = 4,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  rows?: number
}) {
  return (
    <label className="block">
      <span className="label-eyebrow">{label}</span>
      <textarea
        value={value}
        rows={rows}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full rounded-md border border-border-default bg-base px-3 py-2 text-sm text-text-primary outline-none transition focus:border-accent-primary"
      />
    </label>
  )
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: ReadonlyArray<{ value: string; label: string }>
  onChange: (value: string) => void
}) {
  return (
    <label className="block">
      <span className="label-eyebrow">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full rounded-md border border-border-default bg-base px-3 py-2 text-sm text-text-primary outline-none transition focus:border-accent-primary"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function ToggleRow({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded-lg border border-border-default bg-elevated px-3 py-3">
      <span className="type-body-sm text-text-secondary">{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  )
}

function primaryButtonClass(disabled: boolean) {
  return `rounded-md border border-accent-primary px-4 py-2 text-sm text-accent-primary transition hover:bg-accent-primary/10 ${
    disabled ? 'cursor-not-allowed border-border-default text-text-disabled' : ''
  }`
}

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Account, AccountStatus, AccountType, AutomationMode } from '../types/contentMachine'

const DEFAULT_ACCOUNT_ID = 'youtube:account-c5034bf4@example.invalid'
const LEGACY_DEMO_ACCOUNT_IDS = new Set([
  'yt-finanse-pl',
  'yt-historia-pl',
  'yt-ai-brainrot-pl',
  'yt-dark-chronicles',
  'yt-sigma-squad',
  'yt-wealth-blueprint',
])

function makeId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 9)}`
}

function sanitizedStats(account: Account): Account['stats'] {
  return {
    ...account.stats,
    revenueMonth: 0,
    videosToday: 0,
    totalVideos: 0,
    lastUpload: 'Brak uploadu',
  }
}

export const DEMO_ACCOUNTS: Account[] = [
  {
    id: DEFAULT_ACCOUNT_ID,
    type: 'youtube',
    name: 'Wealth Blueprint',
    status: 'live',
    niche: 'personal finance, investing, money habits',
    platformConfig: {
      channelId: 'youtube:account-c5034bf4@example.invalid',
      defaultVisibility: 'private',
      niches: 'personal finance, long-term investing, compound interest, ETF and index funds, financial psychology',
      referenceChannels: 'https://www.youtube.com/@alux',
      competitorChannels: '',
    },
    schedule: {
      days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
      hourUtc: 14,
      postsPerDay: 1,
    },
    stats: {
      revenueMonth: 0,
      queueSize: 0,
      videosToday: 0,
      totalVideos: 0,
      lastUpload: 'Brak uploadu',
    },
    automationMode: 'manual',
  },
  {
    id: 'youtube:account-d6add2f8@example.invalid',
    type: 'youtube',
    name: 'Dark Chronicles',
    status: 'live',
    niche: 'dark history, historical curiosities, weird history',
    platformConfig: {
      channelId: 'youtube:account-d6add2f8@example.invalid',
      defaultVisibility: 'private',
      niches: 'dark history, forgotten historical facts, medieval oddities, historical mysteries, folklore verified sources only',
      referenceChannels: 'https://www.youtube.com/@MrNightmare\nhttps://www.youtube.com/@MrBallen',
      competitorChannels: '',
    },
    schedule: {
      days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
      hourUtc: 15,
      postsPerDay: 1,
    },
    stats: {
      revenueMonth: 0,
      queueSize: 0,
      videosToday: 0,
      totalVideos: 0,
      lastUpload: 'Brak uploadu',
    },
    automationMode: 'manual',
  },
  {
    id: 'youtube:account-bfd050e9@example.invalid',
    type: 'youtube',
    name: 'Sigma Squad',
    status: 'live',
    niche: 'Gen Alpha brainrot, Sigma memes, Ohio simulations',
    platformConfig: {
      channelId: 'youtube:account-bfd050e9@example.invalid',
      defaultVisibility: 'private',
      niches: 'gen alpha brainrot, sigma, skibidi, ohio, rizz, mewing, NPC, W/L ratio',
      referenceChannels: '',
      competitorChannels: '',
    },
    schedule: {
      days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
      hourUtc: 16,
      postsPerDay: 1,
    },
    stats: {
      revenueMonth: 0,
      queueSize: 0,
      videosToday: 0,
      totalVideos: 0,
      lastUpload: 'Brak uploadu',
    },
    automationMode: 'manual',
  },
  {
    id: 'creator:synthetic-model-lab',
    type: 'onlyfans',
    name: 'Synthetic Model Lab',
    status: 'idle',
    niche: 'synthetic creator content, fashion, lifestyle, behind the scenes',
    platformConfig: {
      projectStage: 'needs_access',
      requiredAccess: 'creator platform account, vault credentials, payment policy, content safety rules',
      contentPolicy: 'adult-only account operations; no minors; no explicit generation or publication without human approval',
      assetPipeline: 'image generation, image enhancement, short-form motion, scheduled posts',
      defaultVisibility: 'draft',
    },
    schedule: {
      days: ['mon', 'wed', 'fri'],
      hourUtc: 18,
      postsPerDay: 1,
    },
    stats: {
      revenueMonth: 0,
      queueSize: 0,
      videosToday: 0,
      totalVideos: 0,
      lastUpload: 'Brak uploadu',
    },
    automationMode: 'manual',
  },
  {
    id: 'investment:portfolio-alpha',
    type: 'investment',
    name: 'Portfolio Alpha',
    status: 'idle',
    niche: 'public equities, ETFs, watchlists, portfolio risk controls',
    platformConfig: {
      projectStage: 'needs_broker_connection',
      requiredAccess: 'XTB demo read access, portfolio limits, risk policy, approval rules, learning sources',
      brokerName: 'XTB Demo',
      brokerPortalUrl: 'https://xstation5.xtb.com',
      brokerAccountMode: 'demo_paper',
      brokerLoginEmail: 'account-c5034bf4@example.invalid',
      brokerCredentialVaultRef: '',
      brokerReadAccess: 'false',
      realTradingBlocked: 'true',
      riskPolicy: 'research and alerts first; no trade execution without explicit human approval',
      tradeApprovalRules: 'false',
      dataSources: 'XTB demo market data, portfolio holdings, earnings calendar, macro events',
      marketDataSources: 'XTB demo read-only feed, public market calendars, company news',
      watchlist: 'US500, US100, DE40, EURUSD, USDPLN, GOLD, OIL.WTI, BTCUSD, AAPL.US, MSFT.US',
      learningResources: 'XTB education, Investopedia, market structure basics, risk management notes',
      monthlySpendBudgetPln: '50',
      paymentMethodRef: '',
      paymentMethodConfigured: 'false',
    },
    schedule: {
      days: ['mon', 'tue', 'wed', 'thu', 'fri'],
      hourUtc: 7,
      postsPerDay: 0,
    },
    stats: {
      revenueMonth: 0,
      queueSize: 0,
      videosToday: 0,
      totalVideos: 0,
      lastUpload: 'Brak uploadu',
    },
    automationMode: 'manual',
  },
]

interface AccountStoreState {
  accounts: Account[]
  activeAccountId: string
  addAccount: (input: {
    type: AccountType
    name: string
    niche?: string
    automationMode?: AutomationMode
  }) => Account
  updateAccount: (accountId: string, patch: Partial<Account>) => void
  removeAccount: (accountId: string) => void
  setActiveAccount: (accountId: string) => void
  setAccountStatus: (accountId: string, status: AccountStatus) => void
}

export function mergeCanonicalAccounts(accounts: Account[]): Account[] {
  const userAccounts = accounts.filter((account) => !LEGACY_DEMO_ACCOUNT_IDS.has(account.id))
  const byId = new Map(userAccounts.map((account) => [account.id, account]))
  for (const canonical of DEMO_ACCOUNTS) {
    const existing = byId.get(canonical.id)
    byId.set(
      canonical.id,
      existing
        ? {
            ...canonical,
            ...existing,
            platformConfig: {
              ...canonical.platformConfig,
              ...existing.platformConfig,
              channelId: canonical.platformConfig.channelId,
            },
          }
        : canonical,
    )
  }
  return Array.from(byId.values())
}

export const useAccountStore = create<AccountStoreState>()(
  persist(
    (set, get) => ({
      accounts: DEMO_ACCOUNTS,
      activeAccountId: DEFAULT_ACCOUNT_ID,
      addAccount: (input) => {
        const account: Account = {
          id: makeId(input.type),
          type: input.type,
          name: input.name.trim() || 'Nowe konto',
          status: 'idle',
          niche: input.niche?.trim() || 'content monetization',
          platformConfig: {},
          schedule: {
            days: ['mon', 'wed', 'fri'],
            hourUtc: 14,
            postsPerDay: 1,
          },
          stats: {
            revenueMonth: 0,
            queueSize: 0,
            videosToday: 0,
            totalVideos: 0,
            lastUpload: 'Brak uploadu',
          },
          automationMode: input.automationMode ?? 'manual',
        }
        set((state) => ({
          accounts: [...state.accounts, account],
          activeAccountId: account.id,
        }))
        return account
      },
      updateAccount: (accountId, patch) =>
        set((state) => ({
          accounts: state.accounts.map((account) =>
            account.id === accountId ? { ...account, ...patch } : account,
          ),
        })),
      removeAccount: (accountId) =>
        set((state) => {
          const accounts = state.accounts.filter((account) => account.id !== accountId)
          return {
            accounts,
            activeAccountId:
              state.activeAccountId === accountId
                ? accounts[0]?.id ?? ''
                : state.activeAccountId,
          }
        }),
      setActiveAccount: (accountId) => {
        if (!get().accounts.some((account) => account.id === accountId)) {
          return
        }
        set({ activeAccountId: accountId })
      },
      setAccountStatus: (accountId, status) =>
        set((state) => ({
          accounts: state.accounts.map((account) =>
            account.id === accountId ? { ...account, status } : account,
          ),
        })),
    }),
    {
      name: 'atnos.content.accounts.v1',
      version: 10,
      migrate: (persistedState) => {
        if (!persistedState || typeof persistedState !== 'object') {
          return persistedState as AccountStoreState
        }
        const state = persistedState as AccountStoreState
        const accounts = mergeCanonicalAccounts(Array.isArray(state.accounts) ? state.accounts : DEMO_ACCOUNTS)
        const activeAccountId =
          !state.activeAccountId || LEGACY_DEMO_ACCOUNT_IDS.has(state.activeAccountId)
            ? DEFAULT_ACCOUNT_ID
            : accounts.some((account) => account.id === state.activeAccountId)
              ? state.activeAccountId
              : DEFAULT_ACCOUNT_ID
        return {
          ...state,
          activeAccountId,
          accounts: accounts.map((account) => {
            const oldMessage = String(account.platformConfig.intelligenceLastMessage ?? '')
            const unavailable =
              account.platformConfig.intelligenceLastStatus === 'error' &&
              /not found|endpoint api niedost[eę]pny/i.test(oldMessage)
            const needsConfig =
              account.platformConfig.intelligenceLastStatus === 'error' &&
              /connect youtube studio|oauth|credential|client secret|unauthorized/i.test(oldMessage)
            return {
              ...account,
              automationMode: account.automationMode,
              platformConfig: {
                ...account.platformConfig,
                intelligenceLastStatus: unavailable
                  ? 'unavailable'
                  : needsConfig
                    ? 'needs_config'
                    : account.platformConfig.intelligenceLastStatus,
                intelligenceLastMessage: unavailable
                  ? 'Moduł Intelligence nie odpowiada w aktualnie uruchomionym backendzie. Zrestartuj Atnosa po aktualizacji runtime.'
                  : account.platformConfig.intelligenceLastMessage,
              },
              stats: sanitizedStats(account),
            }
          }),
        }
      },
      merge: (persistedState, currentState) => {
        if (!persistedState || typeof persistedState !== 'object') {
          return currentState
        }
        const state = persistedState as Partial<AccountStoreState>
        const accounts = mergeCanonicalAccounts(
          Array.isArray(state.accounts) ? state.accounts : currentState.accounts,
        ).map((account) => ({
          ...account,
          stats: sanitizedStats(account),
        }))
        const requestedActiveAccountId = String(state.activeAccountId ?? '')
        const activeAccountId =
          !requestedActiveAccountId || LEGACY_DEMO_ACCOUNT_IDS.has(requestedActiveAccountId)
            ? DEFAULT_ACCOUNT_ID
            : accounts.some((account) => account.id === requestedActiveAccountId)
              ? requestedActiveAccountId
              : DEFAULT_ACCOUNT_ID
        return {
          ...currentState,
          ...state,
          accounts,
          activeAccountId,
        }
      },
    },
  ),
)

export function getActiveAccount() {
  const state = useAccountStore.getState()
  return state.accounts.find((account) => account.id === state.activeAccountId) ?? state.accounts[0] ?? null
}

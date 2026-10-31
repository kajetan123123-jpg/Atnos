import { create } from 'zustand'
import type { RevenuePoint, RevenueStats } from '../types/contentMachine'

const EMPTY_REVENUE: RevenuePoint[] = Array.from({ length: 14 }, (_, index) => {
  const date = new Date(Date.UTC(2026, 4, 19 + index))
  return {
    date: date.toISOString().slice(5, 10),
    adsense: 0,
    affiliate: 0,
    sponsors: 0,
  }
})

interface RevenueStoreState {
  pointsByAccount: Record<string, RevenuePoint[]>
  statsByAccount: Record<string, RevenueStats>
  getPoints: (accountId: string) => RevenuePoint[]
  getStats: (accountId: string) => RevenueStats
}

const DEFAULT_STATS: RevenueStats = {
  rpm: 0,
  cpm: 0,
  watchTimeHours: 0,
  ctr: 0,
}

export const useRevenueStore = create<RevenueStoreState>((_set, get) => ({
  pointsByAccount: {
    'youtube:account-c5034bf4@example.invalid': EMPTY_REVENUE,
    'youtube:account-d6add2f8@example.invalid': EMPTY_REVENUE,
    'youtube:account-bfd050e9@example.invalid': EMPTY_REVENUE,
    'creator:synthetic-model-lab': EMPTY_REVENUE,
    'investment:portfolio-alpha': EMPTY_REVENUE,
  },
  statsByAccount: {
    'youtube:account-c5034bf4@example.invalid': DEFAULT_STATS,
    'youtube:account-d6add2f8@example.invalid': DEFAULT_STATS,
    'youtube:account-bfd050e9@example.invalid': DEFAULT_STATS,
    'creator:synthetic-model-lab': DEFAULT_STATS,
    'investment:portfolio-alpha': DEFAULT_STATS,
  },
  getPoints: (accountId) => get().pointsByAccount[accountId] ?? EMPTY_REVENUE,
  getStats: (accountId) => get().statsByAccount[accountId] ?? DEFAULT_STATS,
}))

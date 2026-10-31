import { describe, expect, it } from 'vitest'
import { DEMO_ACCOUNTS, mergeCanonicalAccounts } from './accountStore'
import type { Account } from '../types/contentMachine'

describe('accountStore canonical account migration', () => {
  it('keeps canonical YouTube content account ids when old localStorage overrides channelId', () => {
    const canonical = DEMO_ACCOUNTS.find((account) => account.id === 'youtube:account-c5034bf4@example.invalid') as Account

    const migrated = mergeCanonicalAccounts([
      {
        ...canonical,
        platformConfig: {
          ...canonical.platformConfig,
          channelId: 'yt-wealth-blueprint',
          niches: 'custom user niche',
        },
      },
    ])

    const wealthBlueprint = migrated.find((account) => account.id === 'youtube:account-c5034bf4@example.invalid')

    expect(wealthBlueprint?.platformConfig.channelId).toBe('youtube:account-c5034bf4@example.invalid')
    expect(wealthBlueprint?.platformConfig.niches).toBe('custom user niche')
  })
})

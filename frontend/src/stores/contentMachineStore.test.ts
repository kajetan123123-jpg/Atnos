import { beforeEach, describe, expect, it } from 'vitest'
import { useAccountStore } from './accountStore'
import { useContentMachineStore } from './contentMachineStore'
import { resetAllStores } from '../test/resetStores'

describe('content machine stores', () => {
  beforeEach(() => {
    window.localStorage.clear()
    resetAllStores()
  })

  it('adds accounts and changes active account', () => {
    const account = useAccountStore.getState().addAccount({
      type: 'youtube',
      name: 'YT Test',
      niche: 'test niche',
    })

    expect(useAccountStore.getState().activeAccountId).toBe(account.id)
    expect(useAccountStore.getState().accounts.some((item) => item.name === 'YT Test')).toBe(true)
  })

  it('adds, reorders, starts and removes jobs', () => {
    const accountId = useAccountStore.getState().activeAccountId
    const first = useContentMachineStore.getState().addJob(accountId, 'Pierwszy short')
    const second = useContentMachineStore.getState().addJob(accountId, 'Drugi short')

    expect(useContentMachineStore.getState().queue[accountId]).toEqual([first.id, second.id])

    useContentMachineStore.getState().moveJob(accountId, second.id, 'up')
    expect(useContentMachineStore.getState().queue[accountId]).toEqual([second.id, first.id])

    useContentMachineStore.getState().startJob(accountId, second.id)
    expect(useContentMachineStore.getState().activeJobs[accountId]).toBe(second.id)
    expect(useContentMachineStore.getState().jobs[second.id].status).toBe('running')
    expect(useContentMachineStore.getState().jobs[second.id].steps[0].status).toBe('running')

    useContentMachineStore.getState().removeJob(accountId, first.id)
    expect(useContentMachineStore.getState().queue[accountId]).toEqual([second.id])
  })

  it('keeps only the latest 200 log entries per account', () => {
    const accountId = useAccountStore.getState().activeAccountId
    for (let index = 0; index < 205; index += 1) {
      useContentMachineStore.getState().appendLog({
        accountId,
        step: 'system',
        level: 'info',
        message: `entry ${index}`,
      })
    }

    const logs = useContentMachineStore.getState().logs[accountId]
    expect(logs).toHaveLength(200)
    expect(logs[0].message).toBe('entry 204')
  })
})

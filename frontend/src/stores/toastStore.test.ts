import { beforeEach, describe, expect, it } from 'vitest'
import { useToastStore } from './toastStore'

describe('toastStore', () => {
  beforeEach(() => {
    useToastStore.setState({ queue: [], toasts: [] })
  })

  it('deduplicates repeated toasts with the same type title and message', () => {
    const push = useToastStore.getState().push

    push({
      type: 'warning',
      title: 'Run blocked',
      message: 'Trusted auto jobs require account and connector context.',
    })
    push({
      type: 'warning',
      title: 'Run blocked',
      message: 'Trusted auto jobs require account and connector context.',
    })

    const { toasts } = useToastStore.getState()
    expect(toasts).toHaveLength(1)
    expect(toasts[0].title).toBe('Run blocked')
  })

  it('keeps only the newest four toasts', () => {
    const push = useToastStore.getState().push

    push({ type: 'error', title: 'One' })
    push({ type: 'error', title: 'Two' })
    push({ type: 'error', title: 'Three' })
    push({ type: 'error', title: 'Four' })
    push({ type: 'error', title: 'Five' })

    const titles = useToastStore.getState().toasts.map((toast) => toast.title)
    expect(titles).toEqual(['Two', 'Three', 'Four', 'Five'])
  })
})

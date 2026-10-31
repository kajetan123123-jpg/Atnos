import { create } from 'zustand'
import type { ToastItem } from '../types/toast'

interface ToastState {
  queue: ToastItem[]
  toasts: ToastItem[]
  push: (toast: Omit<ToastItem, 'id'> & { id?: string }) => string
  dismiss: (id: string) => void
}

export const useToastStore = create<ToastState>((set) => ({
  queue: [],
  toasts: [],
  push: (toast) =>
    {
      const toastId = toast.id ?? crypto.randomUUID()
      set((state) => {
      const duplicateIndex = state.toasts.findIndex(
        (item) =>
          item.type === toast.type &&
          item.title === toast.title &&
        (item.message ?? '') === (toast.message ?? ''),
      )
      if (duplicateIndex >= 0) {
        const preserved = state.toasts.filter((_, index) => index !== duplicateIndex)
        const next = [...preserved, { ...toast, id: toastId }]
        const visible = next.slice(-4)
        return { toasts: visible, queue: visible }
      }
      const next = [...state.toasts, { ...toast, id: toastId }]
      const visible = next.slice(-4)
      return { toasts: visible, queue: visible }
      })
      return toastId
    },
  dismiss: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((toast) => toast.id !== id),
      queue: state.queue.filter((toast) => toast.id !== id),
    })),
}))

export const useToasts = useToastStore

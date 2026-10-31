export interface ToastAction {
  label: string
  action: () => void
  tone?: 'neutral' | 'approve' | 'reject'
}

export interface ToastItem {
  id: string
  type: 'success' | 'error' | 'warning' | 'approval_needed'
  title: string
  message?: string
  durationMs?: number
  actions?: ToastAction[]
}

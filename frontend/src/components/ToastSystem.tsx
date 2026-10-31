import { AnimatePresence } from 'framer-motion'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { useToastStore } from '../stores/toastStore'
import { Toast } from './Toast'

export function ToastSystem() {
  const { isMobile } = useBreakpoint()
  const toasts = useToastStore((state) => state.toasts)
  const dismiss = useToastStore((state) => state.dismiss)
  const visibleToasts = isMobile ? [...toasts].reverse() : toasts

  return (
    <div
      className={[
        'fixed z-toast flex gap-2',
        isMobile
          ? 'left-1/2 top-6 w-[min(92vw,480px)] -translate-x-1/2 flex-col'
          : 'bottom-6 right-6 w-[min(92vw,480px)] flex-col',
      ].join(' ')}
    >
      <AnimatePresence initial={false}>
        {visibleToasts.map((toast) => (
          <Toast key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </AnimatePresence>
    </div>
  )
}

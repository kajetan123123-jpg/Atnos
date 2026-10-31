import { useEffect, useState } from 'react'
import { AlertOctagon, AlertTriangle, CheckCircle2, Hand } from 'lucide-react'
import { motion } from 'framer-motion'
import type { ToastItem } from '../types/toast'
import { DUR, EASE, VARIANTS } from '../lib/motion'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { useUIStore } from '../stores/uiStore'

const TOAST_META = {
  success: {
    icon: CheckCircle2,
    border: 'border-success',
    progress: 'bg-success',
    durationMs: 4000,
  },
  error: {
    icon: AlertOctagon,
    border: 'border-error',
    progress: 'bg-error',
    durationMs: 8000,
  },
  warning: {
    icon: AlertTriangle,
    border: 'border-warning',
    progress: 'bg-warning',
    durationMs: 6000,
  },
  approval_needed: {
    icon: Hand,
    border: 'border-accent-secondary',
    progress: 'bg-accent-secondary',
    durationMs: null,
  },
} as const

function actionToneClass(tone: 'neutral' | 'approve' | 'reject' | undefined) {
  switch (tone) {
    case 'approve':
      return 'border-accent-secondary text-accent-secondary hover:bg-accent-secondary/10'
    case 'reject':
      return 'border-border-default text-text-secondary hover:text-error'
    default:
      return 'border-border-default text-text-secondary hover:text-text-primary'
  }
}

export function Toast({
  toast,
  onDismiss,
}: {
  toast: ToastItem
  onDismiss: (id: string) => void
}) {
  const reducedMotion = useReducedMotionStrict()
  const lowPerf = useUIStore((state) => state.lowPerf)
  const meta = TOAST_META[toast.type]
  const Icon = meta.icon
  const durationMs = toast.durationMs ?? meta.durationMs
  const [steppedProgressWidth, setSteppedProgressWidth] = useState(100)
  const progressMode = reducedMotion ? 'reduced' : lowPerf ? 'low-perf' : 'normal'
  const slideMotion = reducedMotion
    ? {
        initial: { opacity: 0, x: 24 },
        animate: { opacity: 1, x: 0, transition: { duration: 0 } },
        exit: { opacity: 0, x: 24, transition: { duration: 0 } },
      }
    : VARIANTS.slideInRight

  useEffect(() => {
    if (durationMs == null) {
      return
    }
    const timer = window.setTimeout(() => onDismiss(toast.id), durationMs)
    return () => window.clearTimeout(timer)
  }, [durationMs, onDismiss, toast.id])

  useEffect(() => {
    if (durationMs == null || progressMode === 'normal') {
      setSteppedProgressWidth(100)
      return
    }

    if (progressMode === 'reduced') {
      const timers = [75, 50, 25, 0].map((width, index) =>
        window.setTimeout(() => {
          setSteppedProgressWidth(width)
        }, (durationMs / 4) * (index + 1)),
      )
      return () => {
        timers.forEach((timer) => window.clearTimeout(timer))
      }
    }

    let rafId = 0
    let lastTick = 0
    const start = performance.now()
    const frameBudgetMs = 1000 / 12

    const tick = (now: number) => {
      if (lastTick === 0 || now - lastTick >= frameBudgetMs) {
        lastTick = now
        const elapsed = Math.min(now - start, durationMs)
        const remainingRatio = Math.max(0, 1 - elapsed / durationMs)
        const steppedWidth = Math.max(0, Math.ceil(remainingRatio * 20) * 5)
        setSteppedProgressWidth((current) =>
          current === steppedWidth ? current : steppedWidth,
        )
      }

      if (now - start < durationMs) {
        rafId = window.requestAnimationFrame(tick)
      }
    }

    rafId = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(rafId)
  }, [durationMs, progressMode, toast.id])

  return (
    <motion.div
      layout
      initial={slideMotion.initial}
      animate={slideMotion.animate}
      exit={slideMotion.exit}
      transition={reducedMotion ? { duration: 0 } : { layout: { duration: DUR.base, ease: EASE.spring } }}
      role={toast.type === 'error' || toast.type === 'approval_needed' ? 'alert' : 'status'}
      aria-live={toast.type === 'error' || toast.type === 'approval_needed' ? 'assertive' : 'polite'}
      aria-atomic="true"
      className={`shell-panel relative min-w-[320px] max-w-[480px] overflow-hidden rounded-md border border-border-default border-l ${meta.border}`}
    >
      <div className="flex gap-3 px-4 py-3">
        <Icon size={16} className="mt-1 shrink-0 text-text-primary" />
        <div className="min-w-0 flex-1">
          <div className="type-body-sm font-medium text-text-primary">{toast.title}</div>
          {toast.message ? (
            <div className="mt-1 type-body-sm text-text-secondary">{toast.message}</div>
          ) : null}
        </div>
        {toast.actions?.length ? (
          <div className="flex shrink-0 flex-wrap items-start justify-end gap-2">
            {toast.actions.map((action, index) => (
              <button
                key={`${toast.id}-${index}`}
                type="button"
                onClick={action.action}
                className={`rounded-md border px-3 py-1 type-body-sm transition ${actionToneClass(action.tone)}`}
              >
                {action.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {durationMs != null ? (
        progressMode !== 'normal' ? (
          <div
            className={`absolute bottom-0 left-0 h-0.5 ${meta.progress}`}
            style={{ width: `${steppedProgressWidth}%` }}
          />
        ) : (
          <motion.div
            className={`absolute bottom-0 left-0 h-0.5 ${meta.progress}`}
            initial={{ width: '100%' }}
            animate={{ width: '0%' }}
            transition={{ duration: durationMs / 1000, ease: 'linear' }}
          />
        )
      ) : null}
    </motion.div>
  )
}

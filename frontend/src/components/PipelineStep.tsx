import { useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  FileText,
  Image as ImageIcon,
  RefreshCcw,
  Search,
  Upload,
  Video,
  Volume2,
} from 'lucide-react'
import { DUR, EASE } from '../lib/motion'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { useUIStore } from '../stores/uiStore'
import type {
  PipelineStep as PipelineStepModel,
  PipelineStepId,
} from '../types/pipeline'

export const PIPELINE_STEP_LABELS: Record<PipelineStepId, string> = {
  research: 'Badanie trendów',
  script: 'Skrypt',
  tts: 'Synteza mowy',
  video: 'Render wideo',
  thumbnail: 'Miniatura',
  upload: 'Publikacja YouTube',
}

const PIPELINE_STEP_STATE_LABELS: Record<PipelineStepModel['state'], string> = {
  pending: 'CZEKA',
  active: 'W TOKU',
  done: 'GOTOWE',
  error: 'BŁĄD',
}

const STEP_ICONS: Record<PipelineStepId, typeof Search> = {
  research: Search,
  script: FileText,
  tts: Volume2,
  video: Video,
  thumbnail: ImageIcon,
  upload: Upload,
}

function connectorTone(state: PipelineStepModel['state']) {
  switch (state) {
    case 'done':
      return 'bg-success'
    case 'active':
      return 'bg-accent-primary'
    case 'error':
      return 'bg-error'
    default:
      return 'bg-border-default'
  }
}

function stateTone(state: PipelineStepModel['state']) {
  switch (state) {
    case 'done':
      return 'border-success text-success'
    case 'active':
      return 'border-accent-primary text-accent-primary shadow-glow-soft'
    case 'error':
      return 'border-error text-error'
    default:
      return 'border-border-default text-text-tertiary'
  }
}

export interface PipelineStepProps {
  step: PipelineStepModel
  index: number
  onRetry?: (stepId: PipelineStepId) => void
}

export function PipelineStep({
  step,
  index,
  onRetry,
}: PipelineStepProps) {
  const reducedMotion = useReducedMotionStrict()
  const lowPerf = useUIStore((state) => state.lowPerf)
  const Icon = STEP_ICONS[step.id]
  const label = step.label ?? PIPELINE_STEP_LABELS[step.id]
  const progress = Math.max(0, Math.min(100, Math.round((step.progress ?? 0) * 100)))
  const ringClassName = useMemo(
    () =>
      `absolute inset-0 rounded-full border border-accent-primary/70 ${
        lowPerf ? 'border-dashed' : ''
      }`,
    [lowPerf],
  )

  return (
    <div className="flex gap-4">
      <div className="flex w-8 shrink-0 flex-col items-center">
        {index > 0 ? <span className={`mb-2 h-4 w-px ${connectorTone(step.state)}`} /> : <span className="mb-2 h-4" aria-hidden />}
        <motion.div
          key={`${step.id}-${step.state}`}
          initial={{
            scale: 1,
            x: 0,
          }}
          animate={
            reducedMotion
              ? undefined
              : step.state === 'done'
                ? { scale: [1, 1.15, 1] }
                : step.state === 'error'
                  ? { x: [0, -2, 2, -2, 2, 0] }
                  : undefined
          }
          transition={
            reducedMotion
              ? undefined
              : step.state === 'done'
                ? { duration: DUR.base, ease: EASE.spring, times: [0, 0.5, 1] }
                : step.state === 'error'
                  ? {
                      duration: 0.4,
                      ease: 'easeInOut',
                      times: [0, 0.2, 0.4, 0.6, 0.8, 1],
                    }
                  : undefined
          }
          className={`relative flex h-8 w-8 items-center justify-center rounded-full border bg-panel ${stateTone(step.state)}`}
        >
          {step.state === 'active' ? (
            <motion.svg
              viewBox="0 0 32 32"
              className={ringClassName}
              animate={reducedMotion || lowPerf ? undefined : { rotate: 360 }}
              transition={
                reducedMotion || lowPerf
                  ? undefined
                  : { duration: 4, repeat: Infinity, ease: 'linear' }
              }
            >
              <circle
                cx="16"
                cy="16"
                r="15.25"
                fill="none"
                stroke="currentColor"
                strokeWidth="1"
                strokeDasharray={lowPerf ? '4 4' : undefined}
              />
            </motion.svg>
          ) : null}
          <Icon size={16} strokeWidth={1.5} />
        </motion.div>
      </div>

      <div className="min-w-0 flex-1 pb-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="type-body-sm font-medium text-text-primary">{label}</div>
            {step.durationMs != null ? (
              <div className="mt-1 type-mono-sm text-text-tertiary">
                {(step.durationMs / 1000).toFixed(1)} s
              </div>
            ) : step.workType ? (
              <div className="mt-1 type-mono-sm text-text-tertiary">{step.workType}</div>
            ) : null}
          </div>
          <span className="type-mono-sm text-text-secondary">
            {step.state === 'active' ? `${progress}%` : PIPELINE_STEP_STATE_LABELS[step.state]}
          </span>
        </div>

        {step.state === 'active' ? (
          <div className="mt-3 h-1 overflow-hidden rounded-pill bg-elevated">
            <motion.div
              className="h-full rounded-pill bg-accent-primary"
              animate={{ width: `${progress}%` }}
              transition={
                reducedMotion
                  ? { duration: 0 }
                  : { duration: 0.2, ease: EASE.standard }
              }
            />
          </div>
        ) : null}

        {step.errorMessage ? (
          <div className="mt-3 type-body-sm text-error">{step.errorMessage}</div>
        ) : null}

        {step.state === 'error' ? (
          <button
            type="button"
            onClick={() => onRetry?.(step.id)}
            className="mt-3 inline-flex items-center gap-2 rounded-md border border-border-default px-3 py-2 type-body-sm text-text-secondary transition duration-fast ease-standard hover:border-accent-primary hover:text-text-primary"
          >
            <RefreshCcw size={14} strokeWidth={1.5} />
            Ponów krok
          </button>
        ) : null}
      </div>
    </div>
  )
}

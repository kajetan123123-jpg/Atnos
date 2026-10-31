import { motion } from 'framer-motion'
import type { AtnosCoreRuntimeState } from '../types/agent'
import { EASE } from '../lib/motion'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { useUIStore } from '../stores/uiStore'
import { AtnosCoreParticles } from './AtnosCoreParticles'

export interface AtnosCoreProps {
  state: AtnosCoreRuntimeState
  size?: 32 | 48 | 64 | 96 | 128
  ariaLabel?: string
}

export type AtnosCoreState = AtnosCoreRuntimeState

interface AtnosCoreVisualConfig {
  outerStroke: string
  middleStroke: string
  innerStroke: string
  centerFill: string
  outerDuration: number
  middleDuration: number
  innerDuration: number
  outerGlow: string
  innerGlow: string
  middleOpacity: number
  outerDash?: string
  innerDash?: string
  dotBaseOpacity: number
  dotPulse?: {
    values: [number, number, number]
    duration: number
  }
  particles?: 'stream' | 'burst'
}

const STATE_LABELS: Record<AtnosCoreRuntimeState, string> = {
  idle: 'bezczynny',
  thinking: 'myśli',
  executing: 'wykonuje',
  awaiting_human: 'oczekuje na człowieka',
  error: 'błąd',
  success: 'sukces',
  offline: 'offline',
}

const STATE_TO_VISUAL: Record<AtnosCoreRuntimeState, AtnosCoreVisualConfig> = {
  idle: {
    outerStroke: 'var(--text-tertiary)',
    middleStroke: 'var(--text-tertiary)',
    innerStroke: 'var(--text-secondary)',
    centerFill: 'var(--text-secondary)',
    outerDuration: 24,
    middleDuration: 36,
    innerDuration: 0,
    outerGlow: 'drop-shadow(var(--glow-soft))',
    innerGlow: 'none',
    middleOpacity: 0.6,
    dotBaseOpacity: 0.4,
    dotPulse: { values: [0.4, 1, 0.4], duration: 3 },
  },
  thinking: {
    outerStroke: 'var(--accent-primary)',
    middleStroke: 'var(--accent-primary)',
    innerStroke: 'var(--accent-primary)',
    centerFill: 'var(--accent-primary)',
    outerDuration: 8,
    middleDuration: 12,
    innerDuration: 4,
    outerGlow: 'drop-shadow(var(--glow-medium))',
    innerGlow: 'none',
    middleOpacity: 0.6,
    innerDash: '4 4',
    dotBaseOpacity: 0.6,
    dotPulse: { values: [0.6, 1, 0.6], duration: 1 },
  },
  executing: {
    outerStroke: 'var(--accent-primary)',
    middleStroke: 'var(--accent-primary)',
    innerStroke: 'var(--accent-primary)',
    centerFill: 'var(--accent-primary)',
    outerDuration: 4,
    middleDuration: 6,
    innerDuration: 2,
    outerGlow: 'none',
    innerGlow: 'drop-shadow(var(--glow-hard))',
    middleOpacity: 0.6,
    dotBaseOpacity: 1,
    particles: 'stream',
  },
  awaiting_human: {
    outerStroke: 'var(--accent-secondary)',
    middleStroke: 'var(--accent-secondary)',
    innerStroke: 'var(--accent-secondary)',
    centerFill: 'var(--accent-secondary)',
    outerDuration: 0,
    middleDuration: 0,
    innerDuration: 0,
    outerGlow: 'drop-shadow(var(--glow-extreme))',
    innerGlow: 'none',
    middleOpacity: 0.6,
    dotBaseOpacity: 1,
  },
  error: {
    outerStroke: 'var(--sem-error)',
    middleStroke: 'var(--sem-error)',
    innerStroke: 'var(--sem-error)',
    centerFill: 'var(--sem-error)',
    outerDuration: 0,
    middleDuration: 0,
    innerDuration: 0,
    outerGlow: 'none',
    innerGlow: 'drop-shadow(var(--glow-error))',
    middleOpacity: 0.4,
    outerDash: '2 4',
    dotBaseOpacity: 1,
  },
  success: {
    outerStroke: 'var(--sem-success)',
    middleStroke: 'var(--sem-success)',
    innerStroke: 'var(--sem-success)',
    centerFill: 'var(--sem-success)',
    outerDuration: 1.2,
    middleDuration: 0,
    innerDuration: 0,
    outerGlow: 'none',
    innerGlow: 'drop-shadow(var(--glow-success))',
    middleOpacity: 0.6,
    dotBaseOpacity: 1,
    particles: 'burst',
  },
  offline: {
    outerStroke: 'var(--text-disabled)',
    middleStroke: 'var(--text-disabled)',
    innerStroke: 'var(--text-disabled)',
    centerFill: 'var(--text-disabled)',
    outerDuration: 0,
    middleDuration: 0,
    innerDuration: 0,
    outerGlow: 'none',
    innerGlow: 'none',
    middleOpacity: 0.6,
    dotBaseOpacity: 0.3,
  },
}

function loopTransition(duration: number, reducedMotion: boolean) {
  return {
    duration: reducedMotion ? 0 : duration,
    ease: 'linear',
    repeat: duration > 0 && !reducedMotion ? Infinity : 0,
  }
}

export function AtnosCore({
  state,
  size = 96,
  ariaLabel,
}: AtnosCoreProps) {
  const reducedMotion = useReducedMotionStrict()
  const lowPerf = useUIStore((state) => state.lowPerf)
  const visual = STATE_TO_VISUAL[state]

  const outerAnimate =
    reducedMotion || visual.outerDuration === 0
      ? undefined
      : { rotate: 360 }
  const middleAnimate =
    reducedMotion
      ? { rotate: 0, opacity: visual.middleOpacity }
      : state === 'success'
        ? { rotate: 0, opacity: [visual.middleOpacity, 0] }
        : visual.middleDuration > 0
          ? { rotate: -360, opacity: visual.middleOpacity }
          : { rotate: 0, opacity: visual.middleOpacity }
  const innerAnimate =
    reducedMotion
      ? { rotate: 0, scale: 1, x: 0 }
      : state === 'success'
        ? { rotate: 0, scale: [1, 1.2, 1], x: 0 }
        : state === 'error'
          ? { rotate: 0, scale: 1, x: [0, -2, 2, -2, 2, -2, 0] }
          : visual.innerDuration > 0
            ? { rotate: 360, scale: 1, x: 0 }
            : { rotate: 0, scale: 1, x: 0 }
  const dotAnimate =
    reducedMotion || !visual.dotPulse
      ? undefined
      : { opacity: visual.dotPulse.values }
  const shouldRenderParticles =
    !reducedMotion
    && !(
      lowPerf
      && state === 'executing'
    )
    && visual.particles != null

  return (
    <svg
      role="img"
      aria-label={ariaLabel ?? `Stan ATNOS: ${STATE_LABELS[state]}`}
      width={size}
      height={size}
      viewBox="0 0 96 96"
    >
      <motion.circle
        cx="48"
        cy="48"
        r="46"
        fill="none"
        stroke={visual.outerStroke}
        strokeWidth="1"
        strokeDasharray={visual.outerDash}
        animate={outerAnimate}
        transition={
          state === 'success'
            ? { duration: reducedMotion ? 0 : visual.outerDuration, ease: 'linear' }
            : loopTransition(visual.outerDuration, reducedMotion)
        }
        style={{
          transformOrigin: '48px 48px',
          filter: visual.outerGlow,
        }}
      />
      <motion.circle
        cx="48"
        cy="48"
        r="34"
        fill="none"
        stroke={visual.middleStroke}
        strokeWidth="1"
        initial={{ rotate: 0, opacity: visual.middleOpacity }}
        animate={middleAnimate}
        transition={{
          rotate: loopTransition(visual.middleDuration, reducedMotion),
          opacity:
            state === 'success'
              ? { duration: reducedMotion ? 0 : 0.8, ease: EASE.accelerate }
              : undefined,
        }}
        style={{ transformOrigin: '48px 48px', opacity: visual.middleOpacity }}
      />
      <motion.circle
        cx="48"
        cy="48"
        r="20"
        fill="none"
        stroke={visual.innerStroke}
        strokeWidth="1.5"
        strokeDasharray={visual.innerDash}
        animate={innerAnimate}
        transition={
          state === 'success'
            ? { duration: reducedMotion ? 0 : 0.6, ease: EASE.spring, times: [0, 0.5, 1] }
            : state === 'error'
              ? { duration: reducedMotion ? 0 : 0.6, ease: 'easeInOut', times: [0, 0.16, 0.32, 0.48, 0.64, 0.8, 1] }
              : loopTransition(visual.innerDuration, reducedMotion)
        }
        style={{
          transformOrigin: '48px 48px',
          filter: visual.innerGlow,
        }}
      />
      <motion.circle
        cx="48"
        cy="48"
        r="2"
        fill={visual.centerFill}
        initial={{ opacity: visual.dotBaseOpacity }}
        animate={dotAnimate}
        transition={{
          duration: visual.dotPulse?.duration ?? 0,
          repeat: visual.dotPulse && !reducedMotion ? Infinity : 0,
          ease: 'easeInOut',
        }}
        style={{ opacity: visual.dotBaseOpacity }}
      />
      {reducedMotion && state === 'success' ? (
        <motion.circle
          key="success-flash"
          cx="48"
          cy="48"
          r="30"
          fill="var(--sem-success)"
          initial={{ opacity: 0 }}
          animate={{ opacity: [0, 0.28, 0] }}
          transition={{ duration: 0.2, ease: 'easeInOut', times: [0, 0.5, 1] }}
        />
      ) : null}
      {shouldRenderParticles ? (
        <AtnosCoreParticles
          mode={visual.particles!}
          stroke={visual.centerFill}
        />
      ) : null}
    </svg>
  )
}

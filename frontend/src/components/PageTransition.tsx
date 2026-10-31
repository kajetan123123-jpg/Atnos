import type { PropsWithChildren } from 'react'
import { DUR, EASE } from '../lib/motion'

export const RANK: Record<string, number> = {
  '/': 0,
  '/chat': 0,
  '/create': 1,
  '/studio': 1,
  '/pipeline': 2,
  '/agent': 2,
  '/telemetry': 3,
  '/settings': 4,
}

export type TransitionDirection = 'forward' | 'backward' | 'same' | 'settings'

export function resolveRank(pathname: string) {
  if (pathname === '/' || pathname.startsWith('/chat')) {
    return 0
  }
  if (pathname === '/create' || pathname.startsWith('/studio/')) {
    return 1
  }
  if (pathname.startsWith('/pipeline') || pathname.startsWith('/agent')) {
    return 2
  }
  if (pathname.startsWith('/telemetry')) {
    return 3
  }
  if (pathname.startsWith('/settings')) {
    return 4
  }
  return RANK[pathname] ?? 0
}

export function resolveDirection(previousPath: string, nextPath: string): TransitionDirection {
  if (nextPath.startsWith('/settings') && previousPath !== nextPath) {
    return 'settings'
  }
  const previousRank = resolveRank(previousPath)
  const nextRank = resolveRank(nextPath)
  if (nextRank > previousRank) {
    return 'forward'
  }
  if (nextRank < previousRank) {
    return 'backward'
  }
  return 'same'
}

export function resolveVariants(direction: TransitionDirection, reducedMotion: boolean) {
  if (reducedMotion) {
    return {
      initial: { opacity: 0 },
      animate: { opacity: 1, transition: { duration: DUR.instant } },
      exit: { opacity: 0, transition: { duration: DUR.instant } },
    }
  }

  switch (direction) {
    case 'forward':
      return {
        initial: { x: 24, opacity: 0 },
        animate: {
          x: 0,
          opacity: 1,
          transition: { duration: DUR.base, ease: EASE.decelerate },
        },
        exit: {
          x: -24,
          opacity: 0,
          transition: { duration: DUR.fast, ease: EASE.accelerate },
        },
      }
    case 'backward':
      return {
        initial: { x: -24, opacity: 0 },
        animate: {
          x: 0,
          opacity: 1,
          transition: { duration: DUR.base, ease: EASE.decelerate },
        },
        exit: {
          x: 24,
          opacity: 0,
          transition: { duration: DUR.fast, ease: EASE.accelerate },
        },
      }
    case 'settings':
      return {
        initial: { opacity: 0, scale: 1.02 },
        animate: {
          opacity: 1,
          scale: 1,
          transition: { duration: DUR.base, ease: EASE.decelerate },
        },
        exit: {
          opacity: 0,
          scale: 0.98,
          transition: { duration: DUR.fast, ease: EASE.accelerate },
        },
      }
    default:
      return {
        initial: { opacity: 0 },
        animate: {
          opacity: 1,
          transition: { duration: DUR.fast, ease: EASE.decelerate },
        },
        exit: {
          opacity: 0,
          transition: { duration: 0.12, ease: EASE.accelerate },
        },
      }
  }
}

export function PageTransition({ children }: PropsWithChildren) {
  return <div className="min-h-[calc(100vh-56px)]">{children}</div>
}

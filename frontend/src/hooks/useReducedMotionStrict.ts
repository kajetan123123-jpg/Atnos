import { useReducedMotion } from 'framer-motion'
import { useUIStore } from '../stores/uiStore'

export function useReducedMotionStrict() {
  const prefersReducedMotion = useReducedMotion()
  const lowPerf = useUIStore((state) => state.lowPerf)
  return prefersReducedMotion || lowPerf
}

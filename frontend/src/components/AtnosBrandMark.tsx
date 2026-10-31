import { motion, useReducedMotion } from 'framer-motion'
import atnosLogoUrl from '../assets/atnos-logo.png'

export function AtnosBrandMark({
  size = 40,
}: {
  size?: 32 | 40 | 48 | 56
}) {
  const reducedMotion = useReducedMotion()

  return (
    <div
      className="relative flex items-center justify-center overflow-hidden rounded-lg border border-border-default bg-panel"
      style={{ width: size, height: size }}
    >
      <motion.div
        aria-hidden
        className="absolute inset-[3px] rounded-full border border-accent-primary/30"
        animate={
          reducedMotion
            ? undefined
            : {
                rotate: 360,
                opacity: [0.35, 0.75, 0.35],
                scale: [0.96, 1.02, 0.96],
              }
        }
        transition={{
          duration: 10,
          repeat: Infinity,
          ease: 'linear',
        }}
      />
      <motion.img
        src={atnosLogoUrl}
        alt="Logo ATNOS"
        className="relative z-page h-[78%] w-[78%] object-contain"
        animate={
          reducedMotion
            ? undefined
            : {
                scale: [0.98, 1.03, 0.98],
                filter: [
                  'drop-shadow(var(--glow-soft))',
                  'drop-shadow(var(--glow-medium))',
                  'drop-shadow(var(--glow-soft))',
                ],
              }
        }
        transition={{
          duration: 3.2,
          repeat: Infinity,
          ease: 'easeInOut',
        }}
      />
    </div>
  )
}

export const VARIANTS = {
  enter: {
    initial: { opacity: 0, y: 8 },
    animate: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.32, ease: [0, 0, 0.2, 1] },
    },
    exit: {
      opacity: 0,
      y: -4,
      transition: { duration: 0.16, ease: [0.4, 0, 1, 1] },
    },
  },
  overlay: {
    initial: { opacity: 0 },
    animate: { opacity: 1, transition: { duration: 0.16 } },
    exit: { opacity: 0, transition: { duration: 0.16 } },
  },
  panelPop: {
    initial: { opacity: 0, scale: 0.96, y: 8 },
    animate: {
      opacity: 1,
      scale: 1,
      y: 0,
      transition: { duration: 0.24, ease: [0.34, 1.56, 0.64, 1] },
    },
    exit: {
      opacity: 0,
      scale: 0.98,
      transition: { duration: 0.16, ease: [0.4, 0, 1, 1] },
    },
  },
  slideInRight: {
    initial: { opacity: 0, x: 24 },
    animate: {
      opacity: 1,
      x: 0,
      transition: { duration: 0.24, ease: [0.34, 1.56, 0.64, 1] },
    },
    exit: {
      opacity: 0,
      x: 24,
      transition: { duration: 0.16, ease: [0.4, 0, 1, 1] },
    },
  },
  slideUpSticky: {
    initial: { opacity: 0, y: '100%' },
    animate: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.4, ease: [0.34, 1.56, 0.64, 1] },
    },
    exit: {
      opacity: 0,
      y: '100%',
      transition: { duration: 0.32, ease: [0.4, 0, 1, 1] },
    },
  },
  stagger: {
    animate: { transition: { staggerChildren: 0.06 } },
  },
}

export const EASE = {
  standard: [0.2, 0.8, 0.2, 1],
  decelerate: [0, 0, 0.2, 1],
  accelerate: [0.4, 0, 1, 1],
  spring: [0.34, 1.56, 0.64, 1],
} as const

export const DUR = {
  instant: 0.08,
  fast: 0.16,
  base: 0.24,
  slow: 0.4,
  deliberate: 0.8,
} as const

import { motion } from 'framer-motion'
import { DUR, EASE, VARIANTS } from '../lib/motion'
import { CHAT_STATUS } from '../lib/chatStatusCopy'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { AtnosCore } from './AtnosCore'

export function TypingIndicator() {
  const reducedMotion = useReducedMotionStrict()
  const enterMotion = reducedMotion
    ? {
        initial: { opacity: 0, y: 8 },
        animate: { opacity: 1, y: 0, transition: { duration: 0 } },
        exit: { opacity: 0, y: -4, transition: { duration: 0 } },
      }
    : VARIANTS.enter

  return (
    <motion.div
      initial={enterMotion.initial}
      animate={enterMotion.animate}
      exit={enterMotion.exit}
      role="status"
      aria-live="polite"
      aria-label={CHAT_STATUS.thinkingAria}
      className="flex items-center gap-4 py-2"
    >
      <AtnosCore state="thinking" size={32} />
      <div className="flex min-w-0 flex-col gap-1">
        <span className="type-body-sm text-text-secondary">{CHAT_STATUS.answering}</span>
        <div className="flex items-end gap-2" aria-hidden>
        {[0, 0.12, 0.24].map((delay, index) => (
          <motion.span
            key={index}
            className="block w-px rounded-full bg-accent-primary"
            animate={
              reducedMotion
                ? { height: 8, opacity: 1 }
                : { height: [4, 12, 4], opacity: [0.5, 1, 0.5] }
            }
            transition={
              reducedMotion
                ? { duration: 0 }
                : {
                    duration: DUR.deliberate,
                    repeat: Infinity,
                    ease: EASE.standard,
                    delay,
                  }
            }
          />
        ))}
        </div>
      </div>
    </motion.div>
  )
}

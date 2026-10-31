import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'

const PARTICLE_LIFETIME_SECONDS = 1.2
const PARTICLE_START_RADIUS = 20
const PARTICLE_END_RADIUS = 60
const EXECUTING_PARTICLES_PER_SECOND = 8
const EXECUTING_EMIT_INTERVAL_SECONDS = 1 / EXECUTING_PARTICLES_PER_SECOND
const EXECUTING_POOL_COUNT = Math.ceil(
  PARTICLE_LIFETIME_SECONDS / EXECUTING_EMIT_INTERVAL_SECONDS,
)
const EXECUTING_REPEAT_DELAY_SECONDS =
  EXECUTING_POOL_COUNT * EXECUTING_EMIT_INTERVAL_SECONDS - PARTICLE_LIFETIME_SECONDS

interface AtnosCoreParticlesProps {
  mode: 'stream' | 'burst'
  stroke: string
}

interface ParticleDefinition {
  key: string
  x: number
  y: number
  offsetX: number
  offsetY: number
  delay: number
}

function createParticleDefinition(
  key: string,
  delay: number,
): ParticleDefinition {
  const angle = Math.random() * Math.PI * 2
  const startX = 48 + Math.cos(angle) * PARTICLE_START_RADIUS
  const startY = 48 + Math.sin(angle) * PARTICLE_START_RADIUS
  const endX = 48 + Math.cos(angle) * PARTICLE_END_RADIUS
  const endY = 48 + Math.sin(angle) * PARTICLE_END_RADIUS

  return {
    key,
    x: startX,
    y: startY,
    offsetX: endX - startX,
    offsetY: endY - startY,
    delay,
  }
}

export function AtnosCoreParticles({
  mode,
  stroke,
}: AtnosCoreParticlesProps) {
  const reducedMotion = useReducedMotionStrict()
  const particles = useMemo(() => {
    if (mode === 'burst') {
      return Array.from({ length: 16 }, (_, index) =>
        createParticleDefinition(`burst-${index}`, 0),
      )
    }

    return Array.from({ length: EXECUTING_POOL_COUNT }, (_, index) =>
      createParticleDefinition(
        `stream-${index}`,
        index * EXECUTING_EMIT_INTERVAL_SECONDS,
      ),
    )
  }, [mode])

  if (reducedMotion) {
    return null
  }

  return (
    <>
      {particles.map((particle) => (
        <motion.rect
          key={particle.key}
          x={particle.x}
          y={particle.y}
          width="1"
          height="1"
          fill={stroke}
          initial={{ opacity: mode === 'stream' ? 0 : 1, x: 0, y: 0 }}
          animate={{
            opacity: mode === 'stream' ? [0, 1, 0] : [1, 0],
            x: particle.offsetX,
            y: particle.offsetY,
          }}
          transition={{
            duration: PARTICLE_LIFETIME_SECONDS,
            delay: particle.delay,
            ease: 'easeOut',
            repeat: mode === 'stream' ? Number.POSITIVE_INFINITY : 0,
            repeatDelay: mode === 'stream' ? EXECUTING_REPEAT_DELAY_SECONDS : 0,
            times: mode === 'stream' ? [0, 0.01, 1] : [0, 1],
          }}
        />
      ))}
    </>
  )
}

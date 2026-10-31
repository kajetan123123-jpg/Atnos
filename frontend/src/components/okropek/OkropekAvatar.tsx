import { motion, useReducedMotion } from 'framer-motion'
import type { AtnosCoreState } from '../AtnosCore'

type OkropekVisualState = 'idle' | 'thinking' | 'executing' | 'listening' | 'error'
export type OkropekSpecialMove = 'none' | 'wave' | 'hop' | 'peek'

function resolveVisualState(state: AtnosCoreState, listening: boolean): OkropekVisualState {
  if (listening) {
    return 'listening'
  }
  if (state === 'success') {
    return 'executing'
  }
  if (state === 'offline' || state === 'awaiting_human') {
    return 'idle'
  }
  return state
}

function visualPalette(state: OkropekVisualState) {
  // Sweeter, warmer Okropek — soft peach / cream / rose instead of cold cyan.
  switch (state) {
    case 'listening':
      return {
        accent: '#ffb38a',
        eye: '#fff1e6',
        blush: 'rgba(255,166,148,0.55)',
        outline: 'rgba(255,179,138,0.42)',
      }
    case 'error':
      return {
        accent: '#ff9a94',
        eye: '#ffe4e0',
        blush: 'rgba(255,154,148,0.35)',
        outline: 'rgba(255,154,148,0.36)',
      }
    case 'executing':
      return {
        accent: '#ffd0a8',
        eye: '#fff7ef',
        blush: 'rgba(255,186,160,0.5)',
        outline: 'rgba(255,208,168,0.4)',
      }
    case 'thinking':
      return {
        accent: '#ffc4a8',
        eye: '#fff6f0',
        blush: 'rgba(255,186,170,0.45)',
        outline: 'rgba(255,196,168,0.34)',
      }
    case 'idle':
    default:
      return {
        accent: '#ffc9b0',
        eye: '#fff8f3',
        blush: 'rgba(255,176,164,0.48)',
        outline: 'rgba(255,201,176,0.34)',
      }
  }
}

function motionProfile(state: OkropekVisualState, specialMove: OkropekSpecialMove) {
  if (specialMove === 'wave') {
    return {
      paceX: 0,
      bodyY: [0, -0.12, -0.18, -0.12, 0],
      duration: 1.4,
      armSwing: 18,
      legSwing: 2,
      torsoRotate: [0, -2, 0, 2, 0],
      headRotate: [0, -4, 0, 4, 0],
      eyeScaleX: [1, 1.08, 1],
      specialArm: [8, -28, 18, -32, 8] as number[],
    }
  }
  if (specialMove === 'hop') {
    return {
      paceX: 0,
      bodyY: [0, -6, -10, -4, 0, -2, 0],
      duration: 1.15,
      armSwing: 8,
      legSwing: 10,
      torsoRotate: [0, -1, 0, 1, 0],
      headRotate: [0, 2, 0, -2, 0],
      eyeScaleX: [1, 1.15, 0.92, 1],
      specialArm: null as number[] | null,
    }
  }
  if (specialMove === 'peek') {
    return {
      paceX: [0, 4, 7, 4, 0, -3, 0],
      bodyY: [0, -0.08, -0.12, -0.08, 0],
      duration: 2.2,
      armSwing: 3,
      legSwing: 2,
      torsoRotate: [0, 3, 5, 2, 0, -2, 0],
      headRotate: [0, 8, 12, 6, 0, -6, 0],
      eyeScaleX: [1, 0.7, 1.1, 1],
      specialArm: null as number[] | null,
    }
  }

  switch (state) {
    case 'executing':
      return {
        paceX: [0, 1.2, 2.4, 3.1, 2.2, 0, -2.2, -3.1, -2.4, -1.2, 0],
        bodyY: [0, -0.12, -0.2, -0.12, 0],
        duration: 1.95,
        armSwing: 6.5,
        legSwing: 8.5,
        torsoRotate: [0.2, -0.5, 0.2, 0.55, 0.2],
        headRotate: [0.1, -0.35, 0.1, 0.35, 0.1],
        eyeScaleX: [1.04, 1.18, 1.04],
        specialArm: null as number[] | null,
      }
    case 'thinking':
      return {
        paceX: 0,
        bodyY: [0, -0.08, -0.14, -0.08, 0],
        duration: 2.4,
        armSwing: 2.2,
        legSwing: 2.8,
        torsoRotate: 0,
        headRotate: 0,
        eyeScaleX: [0.88, 0.96, 0.88],
        specialArm: null as number[] | null,
      }
    case 'listening':
      return {
        paceX: 0,
        bodyY: [0, -0.1, -0.16, -0.1, 0],
        duration: 2.1,
        armSwing: 2.4,
        legSwing: 3,
        torsoRotate: 0,
        headRotate: [-1, 1, -1],
        eyeScaleX: [1, 1.08, 1],
        specialArm: null as number[] | null,
      }
    case 'error':
      return {
        paceX: 0,
        bodyY: [0, 0.08, 0],
        duration: 2.6,
        armSwing: 2,
        legSwing: 2,
        torsoRotate: 0,
        headRotate: 0,
        eyeScaleX: [0.72, 0.76, 0.72],
        specialArm: null as number[] | null,
      }
    case 'idle':
    default:
      return {
        paceX: 0,
        bodyY: [0, -0.05, -0.09, -0.05, 0],
        duration: 3.8,
        armSwing: 1.8,
        legSwing: 2.2,
        torsoRotate: 0,
        headRotate: [0, 0.8, 0, -0.8, 0],
        eyeScaleX: [1, 1.03, 1],
        specialArm: null as number[] | null,
      }
  }
}

interface LimbProps {
  x: number
  y: number
  width: number
  height: number
  rotation: number
  inverse?: boolean
  duration: number
  reducedMotion: boolean
  fill: string
  rotateKeyframes?: number[] | null
  yKeyframes?: number[] | null
  originYOffset?: number
}

function Limb({
  x,
  y,
  width,
  height,
  rotation,
  inverse = false,
  duration,
  reducedMotion,
  fill,
  rotateKeyframes = null,
  yKeyframes = null,
  originYOffset = 2,
}: LimbProps) {
  return (
    <motion.rect
      x={x}
      y={y}
      width={width}
      height={height}
      rx={width / 2}
      fill={fill}
      animate={
        reducedMotion
          ? undefined
          : {
              rotate: rotateKeyframes ?? [rotation, inverse ? -rotation : rotation * -1, rotation],
              ...(yKeyframes ? { y: yKeyframes } : {}),
            }
      }
      transition={{ duration, repeat: Infinity, ease: 'easeInOut' }}
      style={{ transformOrigin: `${x + width / 2}px ${y + originYOffset}px` }}
    />
  )
}

interface LegSegmentProps {
  x: number
  y: number
  width: number
  height: number
  rotation: number
  duration: number
  reducedMotion: boolean
  fill: string
  rotateKeyframes?: number[] | null
  yKeyframes?: number[] | null
  originYOffset?: number
}

function LegSegment({
  x,
  y,
  width,
  height,
  rotation,
  duration,
  reducedMotion,
  fill,
  rotateKeyframes = null,
  yKeyframes = null,
  originYOffset = 1.5,
}: LegSegmentProps) {
  const topWidth = width * 1.18
  const bottomWidth = width * 0.82
  const centerX = x + width / 2
  const topLeft = centerX - topWidth / 2
  const topRight = centerX + topWidth / 2
  const bottomLeft = centerX - bottomWidth / 2
  const bottomRight = centerX + bottomWidth / 2
  const path = [
    `M ${topLeft} ${y + 0.4}`,
    `Q ${centerX} ${y - 0.1} ${topRight} ${y + 0.4}`,
    `L ${bottomRight} ${y + height - 1.2}`,
    `Q ${centerX} ${y + height + 0.5} ${bottomLeft} ${y + height - 1.2}`,
    'Z',
  ].join(' ')

  return (
    <motion.path
      d={path}
      fill={fill}
      animate={
        reducedMotion
          ? undefined
          : {
              rotate: rotateKeyframes ?? [rotation, -rotation, rotation],
              ...(yKeyframes ? { y: yKeyframes } : {}),
            }
      }
      transition={{ duration, repeat: Infinity, ease: 'easeInOut' }}
      style={{ transformOrigin: `${centerX}px ${y + originYOffset}px` }}
    />
  )
}

interface FootProps {
  x: number
  y: number
  width: number
  height: number
  fill: string
  duration: number
  reducedMotion: boolean
  rotateKeyframes?: number[] | null
  yKeyframes?: number[] | null
  originX: number
  originY: number
}

function Foot({
  x,
  y,
  width,
  height,
  fill,
  duration,
  reducedMotion,
  rotateKeyframes = null,
  yKeyframes = null,
  originX,
  originY,
}: FootProps) {
  const toeInset = width * 0.18
  const heelInset = width * 0.08
  const path = [
    `M ${x + heelInset} ${y + height * 0.38}`,
    `C ${x + width * 0.28} ${y + height * 0.02} ${x + width * 0.78} ${y - height * 0.06} ${x + width - toeInset} ${y + height * 0.34}`,
    `L ${x + width - toeInset * 0.45} ${y + height * 0.78}`,
    `C ${x + width * 0.7} ${y + height * 1.02} ${x + width * 0.26} ${y + height * 1.02} ${x + 0.35} ${y + height * 0.72}`,
    `L ${x + 0.2} ${y + height * 0.46}`,
    'Z',
  ].join(' ')

  return (
    <motion.path
      d={path}
      fill={fill}
      animate={
        reducedMotion
          ? undefined
          : {
              ...(rotateKeyframes ? { rotate: rotateKeyframes } : {}),
              ...(yKeyframes ? { y: yKeyframes } : {}),
            }
      }
      transition={{ duration, repeat: Infinity, ease: 'easeInOut' }}
      style={{ transformOrigin: `${originX}px ${originY}px` }}
    />
  )
}

interface OkropekAvatarProps {
  state: AtnosCoreState
  listening?: boolean
  size: 32 | 48 | 64 | 96 | 128
  specialMove?: OkropekSpecialMove
}

export function OkropekAvatar({
  state,
  listening = false,
  size,
  specialMove = 'none',
}: OkropekAvatarProps) {
  const reducedMotion = useReducedMotion()
  const visualState = resolveVisualState(state, listening)
  const palette = visualPalette(visualState)
  const profile = motionProfile(visualState, specialMove)
  const isExecuting = visualState === 'executing' && specialMove === 'none'
  const leftArmKeyframes =
    profile.specialArm ?? (isExecuting ? [4, 1, -3, -5, -2, 2, 4] : null)
  const rightArmKeyframes = isExecuting ? [-2, -4.5, -1, 3, 5, 2, -2] : null
  const leftLegKeyframes = isExecuting ? [5, 2, -2, -6, -3, 2, 5] : null
  const rightLegKeyframes = isExecuting ? [-3, -6.5, -2, 2, 5, 1.5, -3] : null
  const leftLegLift = isExecuting ? [0, -0.1, -0.35, -0.18, 0, 0.08, 0] : null
  const rightLegLift = isExecuting ? [0, 0.06, 0, -0.12, -0.32, -0.14, 0] : null

  return (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 96 96"
      role="img"
      aria-label={`Okropek: ${specialMove !== 'none' ? specialMove : visualState}`}
      style={{ overflow: 'visible' }}
      animate={reducedMotion ? undefined : { x: profile.paceX }}
      transition={
        reducedMotion
          ? undefined
          : {
              duration: specialMove === 'none' ? profile.duration * 2.8 : profile.duration,
              repeat: Infinity,
              ease: 'easeInOut',
            }
      }
    >
      <defs>
        <linearGradient id="okropek-humanoid-body" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#3a2a34" />
          <stop offset="55%" stopColor="#1a1218" />
          <stop offset="100%" stopColor="#0a0709" />
        </linearGradient>
        <radialGradient id="okropek-humanoid-core" cx="50%" cy="44%" r="60%">
          <stop offset="0%" stopColor={palette.accent} stopOpacity="0.98" />
          <stop offset="55%" stopColor={palette.accent} stopOpacity="0.22" />
          <stop offset="100%" stopColor={palette.accent} stopOpacity="0" />
        </radialGradient>
        <filter id="okropek-humanoid-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="4.8" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <motion.ellipse
        cx="48"
        cy="88"
        rx="14"
        ry="4"
        fill={visualState === 'error' ? 'rgba(255,154,148,0.2)' : 'rgba(255,186,160,0.2)'}
        animate={
          reducedMotion
            ? undefined
            : {
                scaleX: visualState === 'executing' ? [1, 0.93, 0.88, 0.93, 1] : [1, 0.88, 1],
                opacity: [0.18, 0.28, 0.18],
              }
        }
        transition={{ duration: profile.duration, repeat: Infinity, ease: 'easeInOut' }}
      />

      <motion.g
        animate={reducedMotion ? undefined : { y: profile.bodyY, rotate: profile.torsoRotate }}
        transition={{ duration: profile.duration, repeat: Infinity, ease: 'easeInOut' }}
        style={{ transformOrigin: '48px 44px' }}
      >
        <motion.g
          animate={reducedMotion ? undefined : { rotate: profile.headRotate }}
          transition={{ duration: profile.duration, repeat: Infinity, ease: 'easeInOut' }}
          style={{ transformOrigin: '48px 17px' }}
        >
          <motion.circle
            cx="48"
            cy="17"
            r="11.4"
            fill="url(#okropek-humanoid-body)"
            stroke={palette.outline}
            strokeWidth="1"
          />
          <motion.circle
            cx="48"
            cy="17"
            r="8.2"
            fill="url(#okropek-humanoid-core)"
            filter="url(#okropek-humanoid-glow)"
            animate={reducedMotion ? undefined : { opacity: [0.84, 1, 0.84] }}
            transition={{ duration: profile.duration, repeat: Infinity, ease: 'easeInOut' }}
          />
          <ellipse cx="40.2" cy="20.2" rx="2.4" ry="1.5" fill={palette.blush} />
          <ellipse cx="55.8" cy="20.2" rx="2.4" ry="1.5" fill={palette.blush} />
          <motion.circle
            cx="43.6"
            cy="15.6"
            r="1.55"
            fill={palette.eye}
            animate={reducedMotion ? undefined : { scaleY: profile.eyeScaleX }}
            transition={{ duration: profile.duration, repeat: Infinity, ease: 'easeInOut' }}
          />
          <motion.circle
            cx="52.4"
            cy="15.6"
            r="1.55"
            fill={palette.eye}
            animate={reducedMotion ? undefined : { scaleY: profile.eyeScaleX }}
            transition={{ duration: profile.duration, repeat: Infinity, ease: 'easeInOut' }}
          />
          <path
            d="M44.5 20.6 Q48 22.4 51.5 20.6"
            fill="none"
            stroke={palette.eye}
            strokeWidth="1.1"
            strokeLinecap="round"
            opacity="0.9"
          />
        </motion.g>

        <motion.path
          d="M41 28C41 25.8 42.8 24 45 24h6c2.2 0 4 1.8 4 4v13c0 2.2-1.8 4-4 4h-6c-2.2 0-4-1.8-4-4V28Z"
          fill="url(#okropek-humanoid-body)"
          stroke={palette.outline}
          strokeWidth="1"
        />
        <motion.ellipse
          cx="48"
          cy="34.5"
          rx="8"
          ry="10"
          fill="url(#okropek-humanoid-core)"
          opacity="0.22"
          animate={reducedMotion ? undefined : { opacity: [0.18, 0.3, 0.18] }}
          transition={{ duration: profile.duration, repeat: Infinity, ease: 'easeInOut' }}
        />

        <Limb
          x={36}
          y={28}
          width={4.5}
          height={21}
          rotation={profile.armSwing}
          duration={profile.duration}
          reducedMotion={Boolean(reducedMotion)}
          fill="url(#okropek-humanoid-body)"
          rotateKeyframes={leftArmKeyframes}
          originYOffset={3}
        />
        <Limb
          x={55.5}
          y={28}
          width={4.5}
          height={21}
          rotation={profile.armSwing}
          inverse
          duration={profile.duration}
          reducedMotion={Boolean(reducedMotion)}
          fill="url(#okropek-humanoid-body)"
          rotateKeyframes={rightArmKeyframes}
          originYOffset={3}
        />

        <LegSegment
          x={42.6}
          y={44}
          width={5}
          height={24.8}
          rotation={profile.legSwing}
          duration={profile.duration}
          reducedMotion={Boolean(reducedMotion)}
          fill="url(#okropek-humanoid-body)"
          rotateKeyframes={leftLegKeyframes}
          yKeyframes={leftLegLift}
          originYOffset={1.4}
        />
        <LegSegment
          x={48.4}
          y={44}
          width={5}
          height={24.8}
          rotation={profile.legSwing}
          duration={profile.duration}
          reducedMotion={Boolean(reducedMotion)}
          fill="url(#okropek-humanoid-body)"
          rotateKeyframes={rightLegKeyframes}
          yKeyframes={rightLegLift}
          originYOffset={1.4}
        />

        <Foot
          x={39.8}
          y={68}
          width={9.8}
          height={3.9}
          fill="#140e12"
          duration={profile.duration}
          reducedMotion={Boolean(reducedMotion)}
          rotateKeyframes={isExecuting ? [2.8, 1.2, -1.4, -2.6, -1.2, 1.1, 2.8] : null}
          yKeyframes={isExecuting ? [0, 0, -0.08, -0.12, 0, 0.04, 0] : null}
          originX={44.8}
          originY={68.4}
        />
        <Foot
          x={47.2}
          y={68}
          width={9.8}
          height={3.9}
          fill="#140e12"
          duration={profile.duration}
          reducedMotion={Boolean(reducedMotion)}
          rotateKeyframes={isExecuting ? [-1.6, -2.8, -1.3, 1.8, 2.8, 1.2, -1.6] : null}
          yKeyframes={isExecuting ? [0, 0.04, 0, -0.06, -0.12, -0.04, 0] : null}
          originX={51.8}
          originY={68.4}
        />

        {visualState === 'executing' || specialMove !== 'none' ? (
          <>
            <circle cx="31" cy="24" r="1" fill={palette.accent} opacity="0.58" />
            <circle cx="66" cy="36" r="0.8" fill={palette.accent} opacity="0.44" />
            <circle cx="29" cy="50" r="0.8" fill={palette.accent} opacity="0.36" />
          </>
        ) : null}
      </motion.g>
    </motion.svg>
  )
}

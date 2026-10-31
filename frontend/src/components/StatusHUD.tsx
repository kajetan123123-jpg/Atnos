import { AnimatePresence, motion } from 'framer-motion'
import { Database, Menu, ShieldCheck, ShieldOff, ShieldQuestion, Wifi } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { AgentStage } from '../types/agent'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { formatVram } from '../lib/format'
import { DUR, EASE } from '../lib/motion'
import { useSystemStore } from '../stores/systemStore'
import { useUIStore } from '../stores/uiStore'
import { AtnosCore } from './AtnosCore'
import { Kbd } from './kbd'

export interface StatusHUDProps {
  agentStage: AgentStage
  model: { name: string; provider: 'ollama' | 'openai' | 'anthropic' | 'gemini' }
  vramUsedMB: number
  vramTotalMB: number
  chroma: 'connected' | 'reconnecting' | 'down'
  network: 'online' | 'degraded' | 'offline'
  rttMs: number | null
  accessStatus?: 'syncing' | 'active' | 'limited'
  accessDetail?: string | null
}

const VRAM_WARNING_THRESHOLD_MB = 10240
const VRAM_CRITICAL_THRESHOLD_MB = 14336
const VRAM_SCALE_MAX_MB = 16384

const STAGE_LABELS: Record<AgentStage, string> = {
  idle: 'BEZCZYNNY',
  thinking: 'MYŚLI',
  executing: 'WYKONUJE',
  awaiting_human: 'OCZEKUJE_NA_CZŁOWIEKA',
  error: 'BŁĄD',
  offline: 'OFFLINE',
}

const CHROMA_LABELS: Record<StatusHUDProps['chroma'], string> = {
  connected: 'Połączony',
  reconnecting: 'Łączy ponownie',
  down: 'Niedostępny',
}

const NETWORK_LABELS: Record<StatusHUDProps['network'], string> = {
  online: 'Online',
  degraded: 'Ograniczony',
  offline: 'Offline',
}

function stageTone(stage: AgentStage) {
  switch (stage) {
    case 'thinking':
      return 'border-accent-primary text-accent-primary shadow-glow-soft'
    case 'executing':
      return 'border-accent-primary text-accent-primary shadow-glow-medium'
    case 'awaiting_human':
      return 'border-accent-secondary text-accent-secondary shadow-glow-extreme'
    case 'error':
      return 'border-error text-error'
    case 'offline':
      return 'border-border-default text-text-disabled'
    default:
      return 'border-border-default text-text-secondary'
  }
}

function accessTone(status: NonNullable<StatusHUDProps['accessStatus']>) {
  switch (status) {
    case 'active':
      return 'border-success text-success shadow-glow-soft'
    case 'limited':
      return 'border-warning text-warning'
    default:
      return 'border-border-default text-text-secondary'
  }
}

function statusDotColor(status: StatusHUDProps['chroma'] | StatusHUDProps['network']) {
  if (status === 'connected' || status === 'online') {
    return 'bg-success'
  }
  if (status === 'reconnecting' || status === 'degraded') {
    return 'bg-warning'
  }
  return 'bg-error'
}

function formatGigabytes(valueMB: number) {
  return new Intl.NumberFormat('pl-PL', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(valueMB / 1024)
}

function buildVramAriaLabel(usedMB: number, totalMB: number, percent: number) {
  return `VRAM ${formatGigabytes(usedMB)} z ${formatGigabytes(totalMB)} gigabajtów, ${percent} procent`
}

function StatusDot({
  status,
}: {
  status: StatusHUDProps['chroma'] | StatusHUDProps['network']
}) {
  const reducedMotion = useReducedMotionStrict()
  const pulse = status === 'reconnecting' || status === 'degraded'

  return (
    <motion.span
      className={`inline-flex h-2 w-2 rounded-full ${statusDotColor(status)}`}
      aria-hidden
      style={{ opacity: 1 }}
      animate={reducedMotion || !pulse ? undefined : { opacity: [1, 0.4, 1] }}
      transition={
        reducedMotion || !pulse
          ? undefined
          : { duration: 1, repeat: Infinity, ease: 'easeInOut' }
      }
    />
  )
}

function HUDRule() {
  return <span className="shell-divider hidden h-4 w-px md:block" aria-hidden />
}

function StagePill({ agentStage }: { agentStage: AgentStage }) {
  const backgroundStyle =
    agentStage === 'executing'
      ? { backgroundColor: 'var(--surface-live)' }
      : undefined

  return (
    <div
      className={`inline-flex h-8 items-center rounded-pill border px-3 type-mono-sm ${stageTone(agentStage)}`}
      aria-label={`Stan agenta ${STAGE_LABELS[agentStage]}`}
      style={backgroundStyle}
    >
      {STAGE_LABELS[agentStage]}
    </div>
  )
}

function ModelModule({ model }: Pick<StatusHUDProps, 'model'>) {
  return (
    <div
      className="shell-module flex items-center gap-3 px-3 py-2"
      aria-label={`Model ${model.name}, provider ${model.provider}`}
    >
      <span className="type-label text-text-tertiary">MODEL</span>
      <span className="type-mono-sm text-text-primary">{model.name}</span>
      <span className="type-mono-sm text-text-tertiary">· {model.provider}</span>
    </div>
  )
}

function VRAMModule({
  usedMB,
  totalMB,
  compact = false,
}: {
  usedMB: number
  totalMB: number
  compact?: boolean
}) {
  const reducedMotion = useReducedMotionStrict()
  const [announcement, setAnnouncement] = useState('')
  const previousCriticalRef = useRef(usedMB > VRAM_CRITICAL_THRESHOLD_MB)

  const critical = usedMB > VRAM_CRITICAL_THRESHOLD_MB
  const ratio = Math.min(1, Math.max(0, usedMB / VRAM_SCALE_MAX_MB))
  const percent = Math.round(ratio * 100)
  const fillColor =
    usedMB > VRAM_CRITICAL_THRESHOLD_MB
      ? 'var(--sem-error)'
      : usedMB >= VRAM_WARNING_THRESHOLD_MB
        ? 'var(--sem-warning)'
        : 'var(--accent-primary)'

  useEffect(() => {
    if (!critical) {
      previousCriticalRef.current = false
      setAnnouncement('')
      return
    }

    if (previousCriticalRef.current) {
      return
    }

    previousCriticalRef.current = true
    setAnnouncement('Krytyczny poziom pamięci VRAM')
    const timer = window.setTimeout(() => setAnnouncement(''), 1200)
    return () => window.clearTimeout(timer)
  }, [critical])

  return (
    <div
      className={`shell-module flex items-center gap-3 px-3 py-2 ${compact ? 'justify-between' : ''}`}
      aria-label={buildVramAriaLabel(usedMB, totalMB, percent)}
    >
      <span className="type-label text-text-tertiary">VRAM</span>
      <div className="flex items-center gap-3">
        <div className="h-1.5 w-[120px] overflow-hidden rounded-pill bg-elevated">
          <motion.div
            className="h-full rounded-pill"
            style={{ width: `${ratio * 100}%`, backgroundColor: fillColor, opacity: 1 }}
            animate={{
              width: `${ratio * 100}%`,
              backgroundColor: fillColor,
              opacity: critical && !reducedMotion ? [1, 0.4, 1] : 1,
            }}
            transition={{
              width: { duration: reducedMotion ? 0 : DUR.base, ease: EASE.standard },
              backgroundColor: {
                duration: reducedMotion ? 0 : DUR.base,
                ease: EASE.standard,
              },
              opacity:
                critical && !reducedMotion
                  ? { duration: 1, repeat: Infinity, ease: 'easeInOut' }
                  : undefined,
            }}
          />
        </div>
        <span className="type-mono-sm text-text-secondary">{formatVram(usedMB, totalMB)}</span>
        <span className="sr-only" aria-live="polite" aria-atomic="true">
          {announcement}
        </span>
      </div>
    </div>
  )
}

function ChromaModule({ chroma }: Pick<StatusHUDProps, 'chroma'>) {
  return (
    <div
      className="shell-module flex items-center gap-2 px-3 py-2"
      aria-label={`Chroma ${CHROMA_LABELS[chroma]}`}
    >
      <Database size={14} className="text-text-tertiary" />
      <StatusDot status={chroma} />
      <span className="type-mono-sm text-text-secondary">CHROMA</span>
    </div>
  )
}

function NetworkModule({
  network,
  rttMs,
}: Pick<StatusHUDProps, 'network' | 'rttMs'>) {
  return (
    <div
      className="group relative shell-module flex items-center gap-2 px-3 py-2"
      aria-label={
        rttMs == null
          ? `Sieć ${NETWORK_LABELS[network]}`
          : `Sieć ${NETWORK_LABELS[network]}, RTT ${rttMs} milisekund`
      }
    >
      <Wifi size={14} className="text-text-tertiary" />
      <StatusDot status={network} />
      <span className="type-mono-sm text-text-secondary">NETWORK</span>
      {rttMs != null ? (
        <div className="pointer-events-none absolute left-0 top-10 hidden rounded-md border border-border-default bg-panel px-2 py-1 shadow-glow-soft group-hover:block">
          <span className="type-mono-sm text-text-secondary">{rttMs} ms</span>
        </div>
      ) : null}
    </div>
  )
}

function AccessModule({
  accessStatus = 'syncing',
  accessDetail,
}: Pick<StatusHUDProps, 'accessStatus' | 'accessDetail'>) {
  const icon =
    accessStatus === 'active' ? (
      <ShieldCheck size={14} className="text-success" />
    ) : accessStatus === 'limited' ? (
      <ShieldOff size={14} className="text-warning" />
    ) : (
      <ShieldQuestion size={14} className="text-text-tertiary" />
    )

  const label =
    accessStatus === 'active'
      ? 'FULL ACCESS ACTIVE'
      : accessStatus === 'limited'
        ? 'ACCESS LIMITED'
        : 'ACCESS SYNCING'

  const detail =
    accessStatus === 'active'
      ? 'Atnos startuje z pelnym profilem operatora.'
      : accessDetail ?? 'Atnos synchronizuje status uprawnien runtime.'

  return (
    <div
      className="group relative"
      aria-label={`${label}. ${detail}`}
    >
      <div className={`shell-module flex items-center gap-2 px-3 py-2 ${accessTone(accessStatus)}`}>
        {icon}
        <span className="type-mono-sm">{label}</span>
      </div>
      <div className="pointer-events-none absolute left-0 top-10 hidden max-w-[320px] rounded-md border border-border-default bg-panel px-2 py-1 shadow-glow-soft group-hover:block">
        <span className="type-body-xs text-text-secondary">{detail}</span>
      </div>
    </div>
  )
}

function CompactDetailsPanel(props: Omit<StatusHUDProps, 'agentStage'>) {
  return (
    <div className="mx-4 mb-4 shell-panel space-y-4 rounded-lg px-4 py-4">
      <AccessModule accessStatus={props.accessStatus} accessDetail={props.accessDetail} />
      <ModelModule model={props.model} />
      <VRAMModule usedMB={props.vramUsedMB} totalMB={props.vramTotalMB} compact />
      <div className="shell-module flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-2" aria-label={`Chroma ${CHROMA_LABELS[props.chroma]}`}>
          <Database size={14} className="text-text-tertiary" />
          <StatusDot status={props.chroma} />
          <span className="type-mono-sm text-text-secondary">CHROMA</span>
        </div>
        <span className="type-mono-sm text-text-tertiary">{CHROMA_LABELS[props.chroma]}</span>
      </div>
      <div
        className="shell-module flex items-center justify-between px-3 py-2"
        aria-label={`Sieć ${NETWORK_LABELS[props.network]}`}
      >
        <div className="flex items-center gap-2">
          <Wifi size={14} className="text-text-tertiary" />
          <StatusDot status={props.network} />
          <span className="type-mono-sm text-text-secondary">NETWORK</span>
        </div>
        <span className="type-mono-sm text-text-tertiary">
          {props.rttMs == null ? NETWORK_LABELS[props.network] : `${props.rttMs} ms`}
        </span>
      </div>
    </div>
  )
}

export function StatusHUDView({
  agentStage,
  model,
  vramUsedMB,
  vramTotalMB,
  chroma,
  network,
  rttMs,
  accessStatus = 'syncing',
  accessDetail = null,
}: StatusHUDProps) {
  const reducedMotion = useReducedMotionStrict()
  const { isMobile, isCompactMobile } = useBreakpoint()
  const openPalette = useUIStore((state) => state.openPalette)
  const hudDetailsOpen = useUIStore((state) => state.hudDetailsOpen)
  const setHudDetailsOpen = useUIStore((state) => state.setHudDetailsOpen)

  return (
    <header className="fixed inset-x-0 top-0 z-hud shell-topbar">
      <div className="mx-auto flex h-14 max-w-[1920px] items-center gap-4 px-4">
        {isMobile ? (
          <>
            <button
              type="button"
              onClick={() => setHudDetailsOpen(!hudDetailsOpen)}
              className="shell-module flex h-8 w-8 items-center justify-center text-text-secondary transition duration-fast ease-standard hover:text-text-primary"
              aria-label="Pokaż szczegóły HUD"
            >
              <Menu size={16} />
            </button>
            <div className="shell-module flex items-center gap-3 px-3 py-2">
              <AtnosCore state={agentStage} size={32} />
              <StagePill agentStage={agentStage} />
            </div>
            <button
              type="button"
              onClick={openPalette}
              className="ml-auto flex items-center gap-2"
              aria-label="Otwórz paletę poleceń"
            >
              <Kbd>⌘K</Kbd>
            </button>
          </>
        ) : (
          <>
            <div className="shell-module flex items-center gap-3 px-3 py-2">
              <AtnosCore state={agentStage} size={32} />
              <StagePill agentStage={agentStage} />
            </div>
            <HUDRule />
            <AccessModule accessStatus={accessStatus} accessDetail={accessDetail} />
            <HUDRule />
            <ModelModule model={model} />
            <HUDRule />
            <VRAMModule usedMB={vramUsedMB} totalMB={vramTotalMB} />
            <HUDRule />
            <ChromaModule chroma={chroma} />
            <HUDRule />
            <NetworkModule network={network} rttMs={rttMs} />
            <button
              type="button"
              onClick={openPalette}
              className="ml-auto flex items-center gap-2"
              aria-label="Otwórz paletę poleceń"
            >
              <Kbd>⌘K</Kbd>
            </button>
          </>
        )}
      </div>
      <AnimatePresence initial={false}>
        {isMobile && hudDetailsOpen ? (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={
              reducedMotion
                ? { duration: 0 }
                : { duration: DUR.fast, ease: EASE.standard }
            }
          >
            <CompactDetailsPanel
              accessStatus={accessStatus}
              accessDetail={accessDetail}
              model={model}
              vramUsedMB={vramUsedMB}
              vramTotalMB={vramTotalMB}
              chroma={chroma}
              network={network}
              rttMs={isCompactMobile ? null : rttMs}
            />
          </motion.div>
        ) : null}
      </AnimatePresence>
    </header>
  )
}

export function StatusHUD() {
  const agentStage = useSystemStore((state) => state.agentStage)
  const model = useSystemStore((state) => state.model)
  const vramUsedMB = useSystemStore((state) => state.vramUsedMB)
  const vramTotalMB = useSystemStore((state) => state.vramTotalMB)
  const chroma = useSystemStore((state) => state.chroma)
  const network = useSystemStore((state) => state.network)
  const rttMs = useSystemStore((state) => state.rttMs)
  const capabilities = useSystemStore((state) => state.capabilities)
  const trustedAutoSummary = useSystemStore((state) => state.trustedAutoSummary)
  const websocketStatus = useSystemStore((state) => state.websocketStatus)
  const websocketRetryCount = useSystemStore((state) => state.websocketRetryCount)
  const websocketNextRetryMs = useSystemStore((state) => state.websocketNextRetryMs)

  const runtimeControls = trustedAutoSummary?.runtimeControls ?? null
  const missingFlags: string[] = []

  if (runtimeControls == null || capabilities == null) {
    missingFlags.push('status runtime jeszcze sie laduje')
  } else {
    if (runtimeControls.autonomyMode !== 'trusted_auto') {
      missingFlags.push('autonomy mode nie jest trusted_auto')
    }
    if (capabilities.executionEnabled === false) {
      missingFlags.push('execution jest wstrzymane')
    }
    if (!capabilities.browserControlEnabled) {
      missingFlags.push('browser control jest wylaczone')
    }
    if (!capabilities.externalActionsEnabled) {
      missingFlags.push('external actions sa wylaczone')
    }
    if (!capabilities.oauthEnabled) {
      missingFlags.push('oauth jest wylaczone')
    }
    if (!runtimeControls.loginAutomationEnabled) {
      missingFlags.push('login automation jest wylaczone')
    }
    if (!runtimeControls.twoFactorAutomationEnabled) {
      missingFlags.push('2FA automation jest wylaczone')
    }
    if (!runtimeControls.publishAutomationEnabled) {
      missingFlags.push('publish automation jest wylaczone')
    }
  }

  const accessStatus: NonNullable<StatusHUDProps['accessStatus']> =
    runtimeControls == null || capabilities == null
      ? 'syncing'
      : missingFlags.length === 0
        ? 'active'
        : 'limited'

  const accessDetail =
    accessStatus === 'active'
      ? 'Atnos startuje z pelnym profilem operatora: browser, external actions, oauth, login, 2FA i publish sa aktywne.'
      : accessStatus === 'limited'
        ? `Brakuje: ${missingFlags.join(', ')}.`
        : 'Trwa synchronizacja statusu runtime i uprawnien.'

  const showWebSocketRecoveryBanner = websocketStatus === 'background_retry'

  return (
    <>
      <StatusHUDView
        agentStage={agentStage}
        model={model}
        vramUsedMB={vramUsedMB}
        vramTotalMB={vramTotalMB}
        chroma={chroma}
        network={network}
        rttMs={rttMs}
        accessStatus={accessStatus}
        accessDetail={accessDetail}
      />
      {showWebSocketRecoveryBanner ? (
        <div
          role="status"
          className="fixed left-1/2 top-16 z-hud w-[min(720px,calc(100vw-2rem))] -translate-x-1/2 rounded-2xl border border-warning/40 bg-panel/95 px-4 py-3 text-sm text-text-secondary shadow-glow-soft backdrop-blur"
        >
          <span className="font-semibold text-warning">Live sync reconnecting.</span>{' '}
          UI dziala dalej; WebSocket probuje odzyskac stan w tle
          {websocketNextRetryMs != null ? ` co ${Math.round(websocketNextRetryMs / 1000)} s` : ''}.
          {websocketRetryCount > 0 ? ` Proby: ${websocketRetryCount}.` : ''}
        </div>
      ) : null}
    </>
  )
}

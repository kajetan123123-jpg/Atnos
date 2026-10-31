import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { Image as ImageIcon } from 'lucide-react'
import { useApprovalCountdown } from '../hooks/useApprovalCountdown'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { EASE, VARIANTS } from '../lib/motion'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import { useUIStore } from '../stores/uiStore'
import type { PipelineSnapshot } from '../types/pipeline'
import type { AgentActionPreview } from '../types/system'

interface ApprovalGateProps {
  snapshot: PipelineSnapshot | null
  genericPreview?: AgentActionPreview | null
  onApprove: (workItemId: number) => void | Promise<void>
  onReject: (workItemId: number) => void | Promise<void>
  onApproveGeneric?: (executionRunId: number) => void | Promise<void>
  onRejectGeneric?: (executionRunId: number) => void | Promise<void>
}

function ApprovalButton({
  hovered,
  reducedMotion,
  pending,
  onHoverChange,
  onClick,
}: {
  hovered: boolean
  reducedMotion: boolean
  pending: boolean
  onHoverChange: (hovered: boolean) => void
  onClick: () => void
}) {
  return (
    <motion.button
      type="button"
      disabled={pending}
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
      onClick={onClick}
      whileTap={reducedMotion ? undefined : { scale: 0.97 }}
      transition={
        reducedMotion
          ? { duration: 0 }
          : { duration: 0.12, ease: EASE.standard }
      }
      className="relative flex h-10 min-w-[132px] items-center justify-center overflow-hidden rounded-md border border-accent-secondary px-4 type-body-sm text-accent-secondary transition duration-fast ease-standard hover:bg-accent-secondary/10 hover:shadow-glow-extreme disabled:cursor-not-allowed disabled:border-border-default disabled:text-text-disabled disabled:hover:shadow-none"
    >
      {!reducedMotion && hovered ? (
        <motion.div
          aria-hidden
          initial={{ x: -80 }}
          animate={{ x: 'calc(100% + 80px)' }}
          transition={{ duration: 1.8, ease: 'linear', repeat: Infinity }}
          className="pointer-events-none absolute top-1/2 h-px w-20 -translate-y-1/2 bg-gradient-to-r from-transparent via-white/80 to-transparent"
        />
      ) : null}
      <span className="relative">{pending ? 'Zatwierdzam…' : 'ZATWIERDŹ'}</span>
    </motion.button>
  )
}

function RejectButton({
  pending,
  onClick,
}: {
  pending: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={pending}
      onClick={onClick}
      className="flex h-10 min-w-[112px] items-center justify-center rounded-md border border-border-default px-4 type-body-sm text-text-secondary transition duration-fast ease-standard hover:bg-elevated hover:text-text-primary disabled:cursor-not-allowed disabled:text-text-disabled"
    >
      ODRZUĆ
    </button>
  )
}

function GateCard({
  title,
  thumbnailUrl,
  platform,
  countdown,
  progressPct,
  isCritical,
  reducedMotion,
  isMobile,
  hovered,
  pending,
  onHoverChange,
  onApprove,
  onReject,
}: {
  title: string
  thumbnailUrl?: string
  platform: string
  countdown: string
  progressPct: number
  isCritical: boolean
  reducedMotion: boolean
  isMobile: boolean
  hovered: boolean
  pending: boolean
  onHoverChange: (hovered: boolean) => void
  onApprove: () => void
  onReject: () => void
}) {
  return (
    <div className="shell-panel-strong pointer-events-auto w-full overflow-hidden">
      <div
        className={[
          'grid gap-4 px-4 py-4',
          isMobile
            ? 'min-h-[112px] grid-cols-1'
            : 'min-h-[96px] items-center md:grid-cols-[minmax(0,1.4fr)_160px_auto]',
        ].join(' ')}
      >
        <div className="flex min-w-0 items-center gap-4">
          <div className="flex h-[72px] w-[128px] shrink-0 items-center justify-center overflow-hidden rounded-md border border-border-default bg-shell/80">
            {thumbnailUrl ? (
              <img
                src={thumbnailUrl}
                alt="Podgląd materiału do publikacji"
                className="h-full w-full object-cover"
              />
            ) : (
              <ImageIcon size={18} className="text-text-tertiary" />
            )}
          </div>
          <div className="min-w-0">
            <div className="type-label text-accent-secondary">OCZEKUJE_NA_CZŁOWIEKA</div>
            <div className="mt-2 type-h3 text-text-primary">{title}</div>
            <div className="mt-2 inline-flex items-center rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-secondary">
              {platform.toUpperCase()}
            </div>
          </div>
        </div>

        <div className="min-w-0">
          <div className="type-label text-text-tertiary">AUTO-ANULOWANIE</div>
          <div className="mt-2 type-mono-sm text-text-primary">{countdown}</div>
          <div className="mt-3 h-0.5 w-full overflow-hidden rounded-pill bg-elevated">
            <motion.div
              className="h-full rounded-pill bg-accent-secondary"
              style={{ width: `${progressPct}%`, opacity: 1 }}
              animate={reducedMotion || !isCritical ? undefined : { opacity: [1, 0.4, 1] }}
              transition={
                reducedMotion || !isCritical
                  ? undefined
                  : { duration: 1, repeat: Infinity, ease: 'easeInOut' }
              }
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-3">
          <RejectButton pending={pending} onClick={onReject} />
          <ApprovalButton
            hovered={hovered}
            reducedMotion={reducedMotion}
            pending={pending}
            onHoverChange={onHoverChange}
            onClick={onApprove}
          />
        </div>
      </div>
    </div>
  )
}

function GenericApprovalGate({
  genericPreview,
  onApproveGeneric,
  onRejectGeneric,
}: {
  genericPreview: AgentActionPreview
  onApproveGeneric: (executionRunId: number) => void | Promise<void>
  onRejectGeneric: (executionRunId: number) => void | Promise<void>
}) {
  const { isMobile } = useBreakpoint()
  const reducedMotion = useReducedMotionStrict()
  const [hovered, setHovered] = useState(false)
  const [pending, setPending] = useState(false)
  const [dismissing, setDismissing] = useState(false)
  const pushToast = useToastStore((state) => state.push)
  const dismissToast = useToastStore((state) => state.dismiss)

  const { formatted, progressPct, isCritical } = useApprovalCountdown({
    active: true,
    onElapsed: () => {
      void Promise.resolve(onRejectGeneric(genericPreview.executionRunId))
      pushToast({
        type: 'warning',
        title: 'Okno approval wygasło',
        message: 'Żądanie zostało automatycznie odrzucone po 30 minutach.',
      })
    },
  })

  const queueReject = () => {
    const id = pushToast({
      type: 'approval_needed',
      title: 'Odrzucić akcję agenta?',
      message: 'Ta akcja zostanie cofnięta i wróci do kolejki decyzji.',
      actions: [
        {
          label: 'Tak, odrzuć',
          tone: 'reject',
          action: () => {
            dismissToast(id)
            void Promise.resolve(onRejectGeneric(genericPreview.executionRunId))
          },
        },
        {
          label: 'Cofnij',
          tone: 'neutral',
          action: () => dismissToast(id),
        },
      ],
    })
  }

  const handleApprove = async () => {
    setPending(true)
    setDismissing(true)
    try {
      await Promise.resolve(onApproveGeneric(genericPreview.executionRunId))
    } catch (error) {
      setDismissing(false)
      setPending(false)
      pushToast({
        type: 'error',
        title: 'Nie udało się zatwierdzić akcji',
        message: error instanceof Error ? error.message : 'Spróbuj ponownie.',
      })
    }
  }

  return (
    <AnimatePresence>
      {!dismissing ? (
        <motion.div
          initial={reducedMotion ? { opacity: 0 } : VARIANTS.slideUpSticky.initial}
          animate={reducedMotion ? { opacity: 1, y: 0 } : VARIANTS.slideUpSticky.animate}
          exit={{
            opacity: 0,
            y: reducedMotion ? 0 : '100%',
            transition: { duration: reducedMotion ? 0 : 0.32, ease: EASE.accelerate },
          }}
          className="pointer-events-none fixed inset-x-0 bottom-0 z-approval px-4 pb-4"
        >
          <div className="mx-auto w-full max-w-[880px]">
            <GateCard
              title={genericPreview.title}
              platform="system"
              countdown={formatted}
              progressPct={progressPct}
              isCritical={isCritical}
              reducedMotion={reducedMotion}
              isMobile={isMobile}
              hovered={hovered}
              pending={pending}
              onHoverChange={setHovered}
              onApprove={() => void handleApprove()}
              onReject={queueReject}
            />
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  )
}

export function ApprovalGate({
  snapshot,
  genericPreview = null,
  onApprove,
  onReject,
  onApproveGeneric,
  onRejectGeneric,
}: ApprovalGateProps) {
  const { isMobile } = useBreakpoint()
  const reducedMotion = useReducedMotionStrict()
  const currentJob = usePipelineStore((state) => state.currentJob)
  const storedSnapshot = usePipelineStore((state) => state.snapshot)
  const agentStage = useSystemStore((state) => state.agentStage)
  const pushToast = useToastStore((state) => state.push)
  const dismissToast = useToastStore((state) => state.dismiss)
  const setPipelineSheetOpen = useUIStore((state) => state.setPipelineSheetOpen)
  const [hovered, setHovered] = useState(false)
  const [pending, setPending] = useState(false)
  const [dismissing, setDismissing] = useState(false)
  const lastVisibleRef = useRef(false)

  const effectiveSnapshot = snapshot ?? storedSnapshot
  const uploadStep = currentJob?.steps.find((step) => step.id === 'upload') ?? null
  const workItemId =
    typeof uploadStep?.workItemId === 'number'
      ? uploadStep.workItemId
      : typeof effectiveSnapshot?.approvalPackage?.workItem?.id === 'number'
        ? effectiveSnapshot.approvalPackage.workItem.id
        : null
  const title =
    typeof effectiveSnapshot?.approvalPackage?.workItem?.title === 'string'
      ? effectiveSnapshot.approvalPackage.workItem.title
      : currentJob?.title ?? 'Pakiet publikacji oczekuje na decyzję'
  const thumbnailUrl = currentJob?.thumbnailUrl
  const awaitingApproval =
    currentJob?.awaitingApproval ?? effectiveSnapshot?.awaitingApproval ?? false
  const shouldRenderPipelineGate =
    agentStage === 'awaiting_human' &&
    awaitingApproval &&
    uploadStep?.state === 'pending'

  useEffect(() => {
    if (shouldRenderPipelineGate && !lastVisibleRef.current) {
      lastVisibleRef.current = true
      pushToast({
        type: 'approval_needed',
        title: 'ATNOS czeka na decyzję',
        message: 'Pakiet publikacji jest gotowy do zatwierdzenia lub odrzucenia.',
        actions: [
          {
            label: 'Pokaż pipeline',
            tone: 'neutral',
            action: () => setPipelineSheetOpen(true),
          },
        ],
      })
      return
    }

    if (!shouldRenderPipelineGate) {
      lastVisibleRef.current = false
      setDismissing(false)
      setPending(false)
    }
  }, [pushToast, setPipelineSheetOpen, shouldRenderPipelineGate])

  const { formatted, progressPct, isCritical } = useApprovalCountdown({
    active: shouldRenderPipelineGate,
    onElapsed: () => {
      if (workItemId == null) {
        return
      }
      void Promise.resolve(onReject(workItemId))
      pushToast({
        type: 'warning',
        title: 'Okno approval wygasło',
        message: 'Pakiet publikacji został automatycznie cofnięty po 30 minutach.',
      })
    },
  })

  const handleApprove = async () => {
    if (workItemId == null) {
      return
    }
    setPending(true)
    setDismissing(true)
    try {
      await Promise.resolve(onApprove(workItemId))
    } catch (error) {
      setDismissing(false)
      setPending(false)
      pushToast({
        type: 'error',
        title: 'Nie udało się zatwierdzić publikacji',
        message: error instanceof Error ? error.message : 'Spróbuj ponownie.',
      })
    }
  }

  const queueRejectConfirmation = () => {
    if (workItemId == null) {
      return
    }

    const id = pushToast({
      type: 'approval_needed',
      title: 'Odrzucić pakiet publikacji?',
      message: 'Materiał wróci do statusu wymagającego zmian.',
      actions: [
        {
          label: 'Tak, odrzuć',
          tone: 'reject',
          action: () => {
            dismissToast(id)
            void Promise.resolve(onReject(workItemId))
          },
        },
        {
          label: 'Cofnij',
          tone: 'neutral',
          action: () => dismissToast(id),
        },
      ],
    })
  }

  if (
    genericPreview &&
    typeof onApproveGeneric === 'function' &&
    typeof onRejectGeneric === 'function'
  ) {
    return (
      <GenericApprovalGate
        genericPreview={genericPreview}
        onApproveGeneric={onApproveGeneric}
        onRejectGeneric={onRejectGeneric}
      />
    )
  }

  return (
    <AnimatePresence>
      {shouldRenderPipelineGate && !dismissing ? (
        <motion.div
          initial={reducedMotion ? { opacity: 0 } : VARIANTS.slideUpSticky.initial}
          animate={reducedMotion ? { opacity: 1, y: 0 } : VARIANTS.slideUpSticky.animate}
          exit={{
            opacity: 0,
            y: reducedMotion ? 0 : '100%',
            transition: { duration: reducedMotion ? 0 : 0.32, ease: EASE.accelerate },
          }}
          className="pointer-events-none fixed inset-x-0 bottom-0 z-approval px-4 pb-4"
        >
          <div className="mx-auto w-full max-w-[720px] xl:max-w-[880px]">
            <GateCard
              title={title}
              thumbnailUrl={thumbnailUrl}
              platform={currentJob?.platform ?? 'youtube'}
              countdown={formatted}
              progressPct={progressPct}
              isCritical={isCritical}
              reducedMotion={reducedMotion}
              isMobile={isMobile}
              hovered={hovered}
              pending={pending}
              onHoverChange={setHovered}
              onApprove={() => void handleApprove()}
              onReject={queueRejectConfirmation}
            />
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  )
}

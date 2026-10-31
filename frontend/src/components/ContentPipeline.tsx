import { type ReactNode, useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import FocusTrap from 'focus-trap-react'
import { ExternalLink, X } from 'lucide-react'
import { buildAgentArtifactDownloadUrl } from '../lib/api'
import { formatDateTime } from '../lib/format'
import { DUR, EASE, VARIANTS } from '../lib/motion'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { usePipelineStore } from '../stores/pipelineStore'
import { useUIStore } from '../stores/uiStore'
import type { ContentJob, PipelineSnapshot, PipelineStepId } from '../types/pipeline'
import { PIPELINE_STEP_LABELS, PipelineStep } from './PipelineStep'

const THUMBNAIL_SLOT_TRIGGER_STEP_ID: PipelineStepId = 'video'
const VIDEO_STEP_ID: PipelineStepId = 'upload'

function resolveVideoUrl(snapshot: PipelineSnapshot | null, currentJob: ContentJob | null) {
  if (currentJob?.videoUrl) {
    return currentJob.videoUrl
  }

  if (snapshot?.latestVideoArtifact?.externalUrl) {
    return snapshot.latestVideoArtifact.externalUrl
  }

  if (snapshot?.latestVideoArtifact?.storagePath) {
    return buildAgentArtifactDownloadUrl(snapshot.latestVideoArtifact.storagePath)
  }

  return null
}

function resolveStatus(currentJob: ContentJob | null, snapshot: PipelineSnapshot | null) {
  const steps = currentJob?.steps ?? snapshot?.steps ?? []

  if (steps.some((step) => step.state === 'error') || snapshot?.status === 'error') {
    return {
      label: 'Błąd',
      className: 'border-error text-error',
    }
  }

  if (
    snapshot?.awaitingApproval ||
    steps.every((step) => step.state === 'done' && steps.length > 0)
  ) {
    return {
      label: 'Gotowe do zatwierdzenia',
      className: 'border-accent-secondary text-accent-secondary shadow-glow-medium',
    }
  }

  if (steps.some((step) => step.state === 'active')) {
    return {
      label: 'W toku',
      className: 'border-accent-primary text-accent-primary shadow-glow-soft',
    }
  }

  return {
    label: 'Bezczynny',
    className: 'border-border-default text-text-secondary',
  }
}

export function shouldRenderPipelineRail(
  snapshot: PipelineSnapshot | null,
  currentJob: ContentJob | null,
  showPreviewOnly: boolean,
) {
  if (!currentJob) {
    return false
  }

  if (showPreviewOnly) {
    return true
  }

  if (!snapshot?.previewOnly) {
    return true
  }

  return snapshot.awaitingApproval || snapshot.status === 'error'
}

function buildPipelineAnnouncement(currentJob: ContentJob) {
  const activeStep = currentJob.steps.find((step) => step.state === 'active')
  if (activeStep) {
    return `Pipeline: aktywny krok ${PIPELINE_STEP_LABELS[activeStep.id]}.`
  }

  if (currentJob.steps.every((step) => step.state === 'done')) {
    return 'Pipeline: wszystkie kroki zostały ukończone.'
  }

  return 'Pipeline: oczekuję na kolejny krok.'
}

function buildPipelineErrorAnnouncement(currentJob: ContentJob) {
  const errorStep = currentJob.steps.find((step) => step.state === 'error')
  if (!errorStep) {
    return ''
  }

  const base = `Błąd pipeline na kroku ${PIPELINE_STEP_LABELS[errorStep.id]}.`
  return errorStep.errorMessage ? `${base} ${errorStep.errorMessage}` : base
}

function EmptyPipelineState() {
  return (
    <div className="px-6 py-8">
      <div className="rounded-lg border border-dashed border-border-default bg-panel px-4 py-5">
        <div className="type-body-sm text-text-primary">Brak aktywnego joba treściowego.</div>
        <div className="mt-2 type-body-sm text-text-tertiary">
          Pipeline pojawi się tutaj, gdy ATNOS uruchomi badanie, skrypt, TTS, render lub
          publikację.
        </div>
      </div>
    </div>
  )
}

function PipelineAssetSlot({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="space-y-3 rounded-lg border border-border-default bg-panel px-4 py-4">
      <div className="label-eyebrow">{label}</div>
      {children}
    </div>
  )
}

function PipelineBodyInner({
  snapshot,
  currentJob,
  showEmptyState = true,
}: {
  snapshot: PipelineSnapshot | null
  currentJob: ContentJob | null
  showEmptyState?: boolean
}) {
  const [lightboxOpen, setLightboxOpen] = useState(false)
  const reducedMotion = useReducedMotionStrict()
  const setStepState = usePipelineStore((state) => state.setStepState)
  const setStepProgress = usePipelineStore((state) => state.setStepProgress)
  const status = useMemo(() => resolveStatus(currentJob, snapshot), [currentJob, snapshot])
  const pipelineAnnouncement = useMemo(
    () => (currentJob ? buildPipelineAnnouncement(currentJob) : ''),
    [currentJob],
  )
  const pipelineErrorAnnouncement = useMemo(
    () => (currentJob ? buildPipelineErrorAnnouncement(currentJob) : ''),
    [currentJob],
  )

  if (!currentJob) {
    return showEmptyState ? <EmptyPipelineState /> : null
  }

  const videoUrl = resolveVideoUrl(snapshot, currentJob)

  const handleRetry = (stepId: PipelineStepId) => {
    setStepProgress(stepId, 0)
    setStepState(stepId, 'active')
  }

  return (
    <>
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {pipelineAnnouncement}
      </div>
      <div className="sr-only" aria-live="assertive" aria-atomic="true">
        {pipelineErrorAnnouncement}
      </div>

      <div className="flex h-14 items-center justify-between gap-3 border-b border-border-default px-4">
        <span className="label-eyebrow">PIPELINE TREŚCI</span>
        <span className={`rounded-pill border px-3 py-1 type-mono-sm ${status.className}`}>
          {status.label}
        </span>
      </div>

      <div className="scroll-chrome max-h-full overflow-y-auto px-4 py-5">
        <div className="mb-5">
          <div className="type-h3 text-text-primary">{currentJob.title}</div>
          <div className="mt-2 type-mono-sm text-text-tertiary">
            {formatDateTime(new Date(currentJob.createdAt).toISOString())}
          </div>
          {snapshot?.statusReason ? (
            <div className="mt-3 type-body-sm text-text-secondary">{snapshot.statusReason}</div>
          ) : null}
        </div>

        <div className="space-y-0">
          {currentJob.steps.map((step, index) => (
            <div key={step.id}>
              <PipelineStep step={step} index={index} onRetry={handleRetry} />

              {step.id === THUMBNAIL_SLOT_TRIGGER_STEP_ID && step.state === 'done' ? (
                <div className="pb-4 pl-12">
                  <PipelineAssetSlot label="PODGLĄD MINIATURY">
                    <button
                      type="button"
                      disabled={!currentJob.thumbnailUrl}
                      onClick={() => currentJob.thumbnailUrl && setLightboxOpen(true)}
                      className={`group flex h-[90px] w-[160px] items-center justify-center overflow-hidden rounded-md border border-border-default bg-shell transition duration-fast ease-standard ${
                        currentJob.thumbnailUrl ? 'hover:border-accent-primary' : 'cursor-default'
                      }`}
                    >
                      {currentJob.thumbnailUrl ? (
                        <img
                          src={currentJob.thumbnailUrl}
                          alt="Podgląd miniatury pipeline"
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <span className="type-body-sm text-text-tertiary">
                          Czekam na asset miniatury
                        </span>
                      )}
                    </button>
                  </PipelineAssetSlot>
                </div>
              ) : null}

              {step.id === VIDEO_STEP_ID && step.state === 'done' ? (
                <div className="pb-4 pl-12">
                  <PipelineAssetSlot label="PODGLĄD WIDEO">
                    {videoUrl ? (
                      <video
                        controls
                        src={videoUrl}
                        className="pipeline-video aspect-video w-full rounded-md border border-border-default bg-shell"
                        style={{ accentColor: 'var(--accent-primary)' }}
                      />
                    ) : (
                      <div className="flex aspect-video w-full items-center justify-center rounded-md border border-border-default bg-shell">
                        <span className="type-body-sm text-text-tertiary">
                          Wideo gotowe, ale URL artefaktu jeszcze nie dotarł.
                        </span>
                      </div>
                    )}
                  </PipelineAssetSlot>
                </div>
              ) : null}
            </div>
          ))}
        </div>

        {snapshot?.latestVideoArtifact && videoUrl ? (
          <div className="pl-12">
            <a
              id="latest-video-artifact"
              href={videoUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-md border border-border-default px-3 py-2 type-body-sm text-text-secondary transition duration-fast ease-standard hover:border-accent-primary hover:text-text-primary"
            >
              Otwórz ostatni artefakt
              <ExternalLink size={14} />
            </a>
          </div>
        ) : null}
      </div>

      <AnimatePresence>
        {lightboxOpen && currentJob.thumbnailUrl ? (
          <motion.div
            initial={reducedMotion ? { opacity: 0 } : VARIANTS.overlay.initial}
            animate={reducedMotion ? { opacity: 1 } : VARIANTS.overlay.animate}
            exit={reducedMotion ? { opacity: 0 } : VARIANTS.overlay.exit}
            className="fixed inset-0 z-modal flex items-center justify-center px-4"
            style={{ backgroundColor: 'var(--scrim-command-palette)' }}
          >
            <button
              type="button"
              className="absolute inset-0"
              aria-label="Zamknij podgląd miniatury"
              onClick={() => setLightboxOpen(false)}
            />
            <FocusTrap
              active
              focusTrapOptions={{
                clickOutsideDeactivates: true,
                escapeDeactivates: true,
                initialFocus: false,
              }}
            >
              <motion.div
                initial={reducedMotion ? { opacity: 0 } : VARIANTS.panelPop.initial}
                animate={
                  reducedMotion ? { opacity: 1, scale: 1, y: 0 } : VARIANTS.panelPop.animate
                }
                exit={reducedMotion ? { opacity: 0, scale: 1 } : VARIANTS.panelPop.exit}
                transition={
                  reducedMotion
                    ? { duration: 0 }
                    : { duration: DUR.base, ease: EASE.spring }
                }
                className="relative z-modal max-w-[min(90vw,960px)] rounded-lg border border-border-strong bg-panel p-4 shadow-glow-medium"
                role="dialog"
                aria-modal="true"
                aria-label="Podgląd miniatury pipeline"
              >
                <button
                  type="button"
                  onClick={() => setLightboxOpen(false)}
                  className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-md border border-border-default bg-shell text-text-secondary transition duration-fast ease-standard hover:bg-elevated hover:text-text-primary"
                  aria-label="Zamknij lightbox miniatury"
                >
                  <X size={14} />
                </button>
                <img
                  src={currentJob.thumbnailUrl}
                  alt="Podgląd miniatury"
                  className="max-h-[80vh] w-full rounded-md object-contain"
                />
              </motion.div>
            </FocusTrap>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </>
  )
}

export function PipelineBody({
  snapshot,
  showEmptyState = true,
}: {
  snapshot?: PipelineSnapshot | null
  showEmptyState?: boolean
}) {
  const storeSnapshot = usePipelineStore((state) => state.snapshot)
  const currentJob = usePipelineStore((state) => state.currentJob)

  return (
    <PipelineBodyInner
      snapshot={snapshot ?? storeSnapshot}
      currentJob={currentJob}
      showEmptyState={showEmptyState}
    />
  )
}

function SheetCloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-md border border-border-default bg-panel text-text-secondary transition duration-fast ease-standard hover:bg-elevated hover:text-text-primary"
      aria-label="Zamknij panel pipeline"
    >
      <X size={14} />
    </button>
  )
}

function PipelineDesktopRail({
  snapshot,
  showPreviewOnly,
}: {
  snapshot: PipelineSnapshot | null
  showPreviewOnly: boolean
}) {
  const currentJob = usePipelineStore((state) => state.currentJob)
  const { isWide } = useBreakpoint()
  const shouldRenderRail = shouldRenderPipelineRail(snapshot, currentJob, showPreviewOnly)

  if (!shouldRenderRail) {
    return (
      <aside
        className="hidden w-0 shrink-0 overflow-hidden border-l-0 lg:block"
        aria-hidden="true"
      />
    )
  }

  const widthClass = isWide ? 'w-[400px]' : 'w-[360px]'

  return (
    <aside
      className={`hidden shrink-0 border-l border-border-default bg-shell lg:block ${widthClass}`}
    >
      {shouldRenderRail ? <PipelineBody snapshot={snapshot} /> : null}
    </aside>
  )
}

function PipelineTabletSheet({ snapshot }: { snapshot: PipelineSnapshot | null }) {
  const pipelineSheetOpen = useUIStore((state) => state.pipelineSheetOpen)
  const setPipelineSheetOpen = useUIStore((state) => state.setPipelineSheetOpen)
  const reducedMotion = useReducedMotionStrict()

  return (
    <AnimatePresence initial={false}>
      {pipelineSheetOpen ? (
        <>
          <motion.button
            type="button"
            initial={reducedMotion ? { opacity: 0 } : VARIANTS.overlay.initial}
            animate={reducedMotion ? { opacity: 1 } : VARIANTS.overlay.animate}
            exit={reducedMotion ? { opacity: 0 } : VARIANTS.overlay.exit}
            onClick={() => setPipelineSheetOpen(false)}
            className="fixed inset-0 z-pipeline-sheet"
            style={{ backgroundColor: 'var(--scrim-command-palette)' }}
            aria-label="Zamknij panel pipeline"
          />
          <FocusTrap
            active
            focusTrapOptions={{
              clickOutsideDeactivates: true,
              escapeDeactivates: true,
              initialFocus: false,
            }}
            >
              <motion.aside
              initial={reducedMotion ? { opacity: 0, x: 0 } : { x: 320, opacity: 1 }}
              animate={{
                x: 0,
                opacity: 1,
                transition: {
                  duration: reducedMotion ? 0 : DUR.base,
                  ease: EASE.spring,
                },
              }}
              exit={{
                x: reducedMotion ? 0 : 320,
                opacity: reducedMotion ? 0 : 1,
                transition: { duration: reducedMotion ? 0 : DUR.fast, ease: EASE.accelerate },
              }}
              className="fixed bottom-0 right-0 top-14 z-pipeline-sheet w-[320px] border-l border-border-default bg-shell"
              role="dialog"
              aria-modal="true"
              aria-label="Panel pipeline"
            >
              <SheetCloseButton onClick={() => setPipelineSheetOpen(false)} />
              <PipelineBody snapshot={snapshot} />
            </motion.aside>
          </FocusTrap>
        </>
      ) : null}
    </AnimatePresence>
  )
}

function PipelineMobileSheet({ snapshot }: { snapshot: PipelineSnapshot | null }) {
  const pipelineSheetOpen = useUIStore((state) => state.pipelineSheetOpen)
  const setPipelineSheetOpen = useUIStore((state) => state.setPipelineSheetOpen)
  const reducedMotion = useReducedMotionStrict()

  return (
    <AnimatePresence initial={false}>
      {pipelineSheetOpen ? (
        <FocusTrap
          active
          focusTrapOptions={{
            clickOutsideDeactivates: false,
            escapeDeactivates: true,
            initialFocus: false,
          }}
        >
          <motion.div
            initial={reducedMotion ? { opacity: 0 } : VARIANTS.slideUpSticky.initial}
            animate={reducedMotion ? { opacity: 1, y: 0 } : VARIANTS.slideUpSticky.animate}
            exit={reducedMotion ? { opacity: 0, y: 0 } : VARIANTS.slideUpSticky.exit}
            className="fixed inset-0 z-pipeline-sheet bg-shell md:hidden"
            role="dialog"
            aria-modal="true"
            aria-label="Panel pipeline"
          >
            <SheetCloseButton onClick={() => setPipelineSheetOpen(false)} />
            <div className="h-full overflow-hidden pt-0">
              <PipelineBody snapshot={snapshot} />
            </div>
          </motion.div>
        </FocusTrap>
      ) : null}
    </AnimatePresence>
  )
}

export function ContentPipeline({
  snapshot,
  showPreviewOnly = true,
}: {
  snapshot: PipelineSnapshot | null
  showPreviewOnly?: boolean
}) {
  const { isMobile, isTablet, isDesktopUp } = useBreakpoint()

  if (isDesktopUp) {
    return <PipelineDesktopRail snapshot={snapshot} showPreviewOnly={showPreviewOnly} />
  }

  if (isTablet) {
    return <PipelineTabletSheet snapshot={snapshot} />
  }

  if (isMobile) {
    return <PipelineMobileSheet snapshot={snapshot} />
  }

  return null
}

export { PIPELINE_STEP_LABELS }

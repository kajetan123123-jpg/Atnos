import { useEffect, useMemo } from 'react'
import { PipelineBody, PIPELINE_STEP_LABELS } from '../components/ContentPipeline'
import { usePipelineStore } from '../stores/pipelineStore'
import type { ContentJob, PipelineStepId } from '../types/pipeline'

const STEP_ORDER: PipelineStepId[] = [
  'research',
  'script',
  'tts',
  'video',
  'thumbnail',
  'upload',
]

function buildDevJob(): ContentJob {
  return {
    id: 'dev-pipeline-job',
    title: 'ATNOS · QA panel pipeline',
    createdAt: Date.now(),
    platform: 'youtube',
    awaitingApproval: false,
    steps: STEP_ORDER.map((id) => ({
      id,
      label: PIPELINE_STEP_LABELS[id],
      state: 'pending',
      progress: 0,
      durationMs: null,
      errorMessage: null,
    })),
  }
}

export default function DevPipelineRoute() {
  const currentJob = usePipelineStore((state) => state.currentJob)
  const hydratePipeline = usePipelineStore((state) => state.hydratePipeline)
  const setStepState = usePipelineStore((state) => state.setStepState)
  const setStepProgress = usePipelineStore((state) => state.setStepProgress)
  const attachAsset = usePipelineStore((state) => state.attachAsset)

  useEffect(() => {
    hydratePipeline({ currentJob: buildDevJob(), history: [] })
  }, [hydratePipeline])

  const activeStepId = useMemo(
    () => currentJob?.steps.find((step) => step.state === 'active')?.id ?? null,
    [currentJob],
  )

  const activateStep = (stepId: PipelineStepId) => {
    for (const id of STEP_ORDER) {
      if (id === stepId) {
        setStepState(id, 'active')
        setStepProgress(id, 0.32)
        continue
      }

      const currentIndex = STEP_ORDER.indexOf(id)
      const nextIndex = STEP_ORDER.indexOf(stepId)
      if (currentIndex < nextIndex) {
        setStepState(id, 'done')
        setStepProgress(id, 1)
      } else {
        setStepState(id, 'pending')
        setStepProgress(id, 0)
      }
    }
  }

  const completeActiveStep = () => {
    if (!activeStepId) {
      return
    }
    setStepProgress(activeStepId, 1)
    setStepState(activeStepId, 'done')

    if (activeStepId === 'thumbnail') {
      attachAsset(
        'https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1280&q=80',
        'thumbnail',
      )
    }

    if (activeStepId === 'upload') {
      attachAsset(
        'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4',
        'video',
      )
    }
  }

  return (
    <section className="mx-auto flex min-h-[calc(100vh-56px)] w-full max-w-[1480px] flex-col gap-8 px-6 py-24 lg:px-10">
      <header className="space-y-3">
        <p className="label-eyebrow">DEV SURFACE</p>
        <h1 className="type-h1 text-text-primary">ContentPipeline QA</h1>
        <p className="type-body max-w-3xl text-text-secondary">
          Ta powierzchnia pozwala sprawdzić aktywny ring, done-pop, shake błędu,
          progress oraz sloty assetów bez backendu.
        </p>
      </header>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,400px)_minmax(0,1fr)]">
        <aside className="space-y-6 rounded-lg border border-border-default bg-panel p-6">
          <div>
            <div className="label-eyebrow">SEKWENCJA</div>
            <div className="mt-4 grid gap-3">
              {STEP_ORDER.map((stepId, index) => (
                <button
                  key={stepId}
                  type="button"
                  onClick={() => activateStep(stepId)}
                  className="rounded-md border border-border-default bg-shell px-4 py-3 text-left type-body-sm text-text-primary transition duration-fast ease-standard hover:bg-elevated"
                >
                  Aktywuj krok {index + 1}: {PIPELINE_STEP_LABELS[stepId]}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="label-eyebrow">STAN AKTYWNY</div>
            <div className="mt-4 grid gap-3">
              <button
                type="button"
                onClick={completeActiveStep}
                className="rounded-md border border-accent-primary bg-shell px-4 py-3 text-left type-body-sm text-accent-primary transition duration-fast ease-standard hover:bg-accent-primary/10"
              >
                Zakończ aktywny krok
              </button>
              <button
                type="button"
                onClick={() => {
                  setStepState('tts', 'error', { errorMessage: 'Błąd TTS: brak odpowiedzi runtime.' })
                  setStepProgress('tts', 0.48)
                }}
                className="rounded-md border border-error bg-shell px-4 py-3 text-left type-body-sm text-error transition duration-fast ease-standard hover:bg-error/10"
              >
                Wymuś błąd na kroku 3
              </button>
              <button
                type="button"
                onClick={() => hydratePipeline({ currentJob: buildDevJob(), history: [] })}
                className="rounded-md border border-border-default bg-shell px-4 py-3 text-left type-body-sm text-text-secondary transition duration-fast ease-standard hover:bg-elevated hover:text-text-primary"
              >
                Resetuj job
              </button>
            </div>
          </div>

          {activeStepId ? (
            <div>
              <div className="label-eyebrow">PROGRESS AKTYWNEGO KROKU</div>
              <input
                type="range"
                min={0}
                max={100}
                step={1}
                value={Math.round((currentJob?.steps.find((step) => step.id === activeStepId)?.progress ?? 0) * 100)}
                onChange={(event) => setStepProgress(activeStepId, Number(event.target.value) / 100)}
                className="mt-4 w-full accent-[var(--accent-primary)]"
              />
            </div>
          ) : null}
        </aside>

        <section className="overflow-hidden rounded-lg border border-border-default bg-shell">
          <PipelineBody />
        </section>
      </div>
    </section>
  )
}

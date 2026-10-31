import { create } from 'zustand'
import { sendWSFrame } from '../lib/ws'
import type {
  ContentJob,
  PipelineSnapshot,
  PipelineStep,
  PipelineStepId,
  PipelineStepState,
} from '../types/pipeline'

function normalizeStep(step: PipelineStep): PipelineStep {
  return {
    ...step,
    progress: step.progress ?? 0,
    startedAt: step.startedAt,
    finishedAt: step.finishedAt,
    durationMs: step.durationMs ?? null,
    errorMessage: step.errorMessage ?? null,
    previewOnly: step.previewOnly ?? false,
    workItemId: step.workItemId ?? null,
    workType: step.workType ?? null,
    reviewNotes: step.reviewNotes ?? null,
  }
}

function cloneSteps(steps: PipelineStep[]) {
  return steps.map((step) => normalizeStep(step))
}

function normalizeContentJob(job: ContentJob): ContentJob {
  return {
    ...job,
    steps: cloneSteps(job.steps),
    awaitingApproval: job.awaitingApproval ?? false,
    approvalDeadline: job.approvalDeadline,
  }
}

function mergeHistory(
  history: ContentJob[],
  previousCurrentJob: ContentJob | null,
  incomingHistory: ContentJob[] = [],
  nextCurrentJob: ContentJob | null,
) {
  const merged = [...incomingHistory]

  if (
    previousCurrentJob &&
    (!nextCurrentJob || previousCurrentJob.id !== nextCurrentJob.id) &&
    !merged.some((job) => job.id === previousCurrentJob.id)
  ) {
    merged.unshift(previousCurrentJob)
  }

  for (const job of history) {
    if (nextCurrentJob && job.id === nextCurrentJob.id) {
      continue
    }
    if (merged.some((item) => item.id === job.id)) {
      continue
    }
    merged.push(job)
  }

  return merged.slice(0, 12)
}

function deriveContentJob(snapshot: PipelineSnapshot): ContentJob | null {
  if (!snapshot.job) {
    return null
  }

  const timestamp = Date.parse(snapshot.job.updatedAt)
  const artifactUrl =
    snapshot.latestVideoArtifact?.externalUrl ??
    snapshot.latestVideoArtifact?.storagePath ??
    undefined

  return {
    id: `job-${snapshot.job.creatorProjectId}-${snapshot.job.updatedAt || snapshot.job.title}`,
    title: snapshot.job.title,
    createdAt: Number.isNaN(timestamp) ? Date.now() : timestamp,
    steps: cloneSteps(snapshot.steps),
    videoUrl: artifactUrl,
    platform: 'youtube',
    awaitingApproval: snapshot.awaitingApproval,
    approvalDeadline: snapshot.awaitingApproval ? Date.now() + 30 * 60 * 1000 : undefined,
  }
}

function updateSteps(
  steps: PipelineStep[],
  stepId: PipelineStepId | string,
  patch: Partial<PipelineStep>,
) {
  return steps.map((step) =>
    step.id === stepId ? normalizeStep({ ...step, ...patch }) : normalizeStep(step),
  )
}

interface PipelineState {
  snapshot: PipelineSnapshot | null
  currentJob: ContentJob | null
  history: ContentJob[]
  hydratePipeline: (payload: {
    currentJob: ContentJob | null
    history?: ContentJob[]
  }) => void
  setSnapshot: (snapshot: PipelineSnapshot) => void
  updateStep: (stepId: string, patch: Partial<PipelineStep>) => void
  setStepState: (
    stepId: PipelineStepId,
    state: PipelineStepState,
    options?: { errorMessage?: string | null },
  ) => void
  setStepStateForJob: (
    jobId: string,
    stepId: PipelineStepId,
    next: Partial<{
      state: PipelineStepState
      progress: number
      errorMessage: string
      durationMs: number
    }>,
  ) => void
  setStepProgress: (stepId: PipelineStepId, progress: number) => void
  attachAsset: (url: string, kind: 'thumbnail' | 'video') => void
  attachAssetToJob: (jobId: string, kind: 'thumbnail' | 'video', url: string) => void
  setAwaitingApproval: (jobId: string, awaiting: boolean) => void
  approveCurrent: () => boolean
  rejectCurrent: () => boolean
}

export const usePipelineStore = create<PipelineState>((set) => ({
  snapshot: null,
  currentJob: null,
  history: [],
  hydratePipeline: ({ currentJob, history = [] }) =>
    set((state) => ({
      currentJob: currentJob
        ? normalizeContentJob(currentJob)
        : null,
      history: mergeHistory(
        state.history,
        state.currentJob,
        history.map((job) => normalizeContentJob(job)),
        currentJob,
      ),
    })),
  setSnapshot: (snapshot) =>
    set((state) => {
      const derivedJob = deriveContentJob(snapshot)
      return {
        snapshot: {
          ...snapshot,
          steps: cloneSteps(snapshot.steps),
        },
        currentJob: derivedJob,
        history: mergeHistory(state.history, state.currentJob, state.history, derivedJob),
      }
    }),
  updateStep: (stepId, patch) =>
    set((state) => ({
      snapshot: state.snapshot
        ? {
            ...state.snapshot,
            steps: updateSteps(state.snapshot.steps, stepId, patch),
          }
        : null,
      currentJob: state.currentJob
        ? {
            ...state.currentJob,
            steps: updateSteps(state.currentJob.steps, stepId, patch),
          }
        : null,
    })),
  setStepState: (stepId, nextState, options) =>
    set((state) => ({
      snapshot: state.snapshot
        ? {
            ...state.snapshot,
            steps: updateSteps(state.snapshot.steps, stepId, {
              state: nextState,
              errorMessage: options?.errorMessage ?? null,
            }),
          }
        : null,
      currentJob: state.currentJob
        ? {
            ...state.currentJob,
            steps: updateSteps(state.currentJob.steps, stepId, {
              state: nextState,
              errorMessage: options?.errorMessage ?? null,
            }),
          }
        : null,
    })),
  setStepStateForJob: (jobId, stepId, next) =>
    set((state) => {
      if (!state.currentJob || state.currentJob.id !== jobId) {
        return state
      }

      return {
        ...state,
        currentJob: {
          ...state.currentJob,
          steps: updateSteps(state.currentJob.steps, stepId, next),
        },
        snapshot: state.snapshot
          ? {
              ...state.snapshot,
              steps: updateSteps(state.snapshot.steps, stepId, next),
            }
          : null,
      }
    }),
  setStepProgress: (stepId, progress) =>
    set((state) => ({
      snapshot: state.snapshot
        ? {
            ...state.snapshot,
            steps: updateSteps(state.snapshot.steps, stepId, { progress }),
          }
        : null,
      currentJob: state.currentJob
        ? {
            ...state.currentJob,
            steps: updateSteps(state.currentJob.steps, stepId, { progress }),
          }
        : null,
    })),
  attachAsset: (url, kind) =>
    set((state) => ({
      snapshot:
        kind === 'video' && state.snapshot
          ? {
              ...state.snapshot,
              latestVideoArtifact: {
                title: state.snapshot.latestVideoArtifact?.title ?? state.currentJob?.title ?? 'Wideo pipeline',
                storagePath: null,
                externalUrl: url,
                updatedAt: new Date().toISOString(),
              },
            }
          : state.snapshot,
      currentJob: state.currentJob
        ? {
            ...state.currentJob,
            thumbnailUrl: kind === 'thumbnail' ? url : state.currentJob.thumbnailUrl,
            videoUrl: kind === 'video' ? url : state.currentJob.videoUrl,
          }
        : null,
    })),
  attachAssetToJob: (jobId, kind, url) =>
    set((state) => {
      if (!state.currentJob || state.currentJob.id !== jobId) {
        return state
      }

      return {
        ...state,
        currentJob: {
          ...state.currentJob,
          thumbnailUrl: kind === 'thumbnail' ? url : state.currentJob.thumbnailUrl,
          videoUrl: kind === 'video' ? url : state.currentJob.videoUrl,
        },
      }
    }),
  setAwaitingApproval: (jobId, awaiting) =>
    set((state) => {
      if (!state.currentJob || state.currentJob.id !== jobId) {
        return state
      }

      return {
        ...state,
        snapshot: state.snapshot
          ? {
              ...state.snapshot,
              awaitingApproval: awaiting,
              status: awaiting ? 'awaiting_human' : state.snapshot.status === 'awaiting_human' ? 'executing' : state.snapshot.status,
            }
          : null,
        currentJob: {
          ...state.currentJob,
          awaitingApproval: awaiting,
          approvalDeadline: awaiting ? Date.now() + 30 * 60 * 1000 : undefined,
        },
      }
    }),
  approveCurrent: () => {
    const currentJob = usePipelineStore.getState().currentJob
    if (!currentJob) {
      return false
    }
    return sendWSFrame({ type: 'pipeline.approve', payload: { jobId: currentJob.id } })
  },
  rejectCurrent: () => {
    const currentJob = usePipelineStore.getState().currentJob
    if (!currentJob) {
      return false
    }
    return sendWSFrame({ type: 'pipeline.reject', payload: { jobId: currentJob.id } })
  },
}))

export const usePipeline = usePipelineStore

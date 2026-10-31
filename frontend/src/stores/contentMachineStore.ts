import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type {
  LogEntry,
  PipelineJob,
  PipelineJobStatus,
  PipelineStep,
  PipelineStepId,
  PipelineStepStatus,
} from '../types/contentMachine'

const STEP_LABELS: Array<[PipelineStepId, string]> = [
  ['topics', 'TEMATY'],
  ['hook', 'HOOK'],
  ['script', 'SKRYPT'],
  ['voice', 'GŁOS'],
  ['video', 'WIDEO'],
  ['assembly', 'MONTAŻ'],
  ['qa', 'QA'],
  ['upload', 'UPLOAD'],
]

function isoNow() {
  return new Date().toISOString()
}

function makeId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 9)}`
}

export function createPipelineSteps(activeStep?: PipelineStepId): PipelineStep[] {
  return STEP_LABELS.map(([id, label]) => ({
    id,
    label,
    status: activeStep === id ? 'running' : 'idle',
    progress: activeStep === id ? 12 : 0,
  }))
}

function createJob(accountId: string, title: string): PipelineJob {
  const now = isoNow()
  return {
    id: makeId('job'),
    accountId,
    title: title.trim() || 'Nowy short',
    status: 'queued',
    steps: createPipelineSteps(),
    createdAt: now,
    updatedAt: now,
    brief: {
      audience: 'widzowie zainteresowani zarabianiem i prostymi finansami',
      style: 'listicle',
      targetMinutes: 1,
    },
    script: [
      'HOOK: Większość ludzi traci pieniądze przez coś, czego nawet nie widzi.',
      'SEGMENT 1: Małe opłaty bankowe wyglądają niewinnie, ale działają codziennie.',
      'SEGMENT 2: Najpierw sprawdź subskrypcje, potem prowizje, na końcu konto główne.',
      'CTA: Obserwuj, jeśli chcesz prostych finansów bez lania wody.',
    ].join('\n'),
    scenes: [
      {
        id: makeId('scene'),
        title: 'UKRYTE KOSZTY',
        prompt: 'A cinematic macro shot of coins disappearing into dark mechanical gears, slow zoom in, high contrast, vertical frame.',
        model: 'ltx-video',
        seed: 12044,
        steps: 25,
        cfg: 3.5,
      },
      {
        id: makeId('scene'),
        title: 'SPRAWDŹ TO',
        prompt: 'A person reviewing a mobile banking app at night, dramatic blue glow, camera tracking over the shoulder, vertical frame.',
        model: 'ltx-video',
        seed: 42017,
        steps: 25,
        cfg: 3.5,
      },
    ],
  }
}

interface ContentMachineState {
  jobs: Record<string, PipelineJob>
  queue: Record<string, string[]>
  logs: Record<string, LogEntry[]>
  activeJobs: Record<string, string>
  addJob: (accountId: string, title: string) => PipelineJob
  removeJob: (accountId: string, jobId: string) => void
  moveJob: (accountId: string, jobId: string, direction: 'up' | 'down') => void
  updateJob: (jobId: string, patch: Partial<PipelineJob>) => void
  startJob: (accountId: string, jobId: string) => void
  pauseAll: () => void
  updateStep: (
    jobId: string,
    stepId: PipelineStepId,
    patch: Partial<Pick<PipelineStep, 'status' | 'progress' | 'etaSeconds' | 'durationMs' | 'errorMessage'>>,
  ) => void
  ingestPipelineStep: (frame: {
    accountId: string
    jobId?: string
    title?: string
    step: string
    status: string
    progress?: number
    etaSeconds?: number
    durationMs?: number
    error?: string
    videoId?: string
    videoUrl?: string
  }) => void
  appendLog: (entry: Omit<LogEntry, 'id' | 'timestamp'>) => void
}

export const useContentMachineStore = create<ContentMachineState>()(
  persist(
    (set) => ({
      jobs: {},
      queue: {},
      logs: {},
      activeJobs: {},
      addJob: (accountId, title) => {
        const job = createJob(accountId, title)
        set((state) => ({
          jobs: { ...state.jobs, [job.id]: job },
          queue: {
            ...state.queue,
            [accountId]: [...(state.queue[accountId] ?? []), job.id],
          },
          logs: appendLogToMap(state.logs, {
            accountId,
            jobId: job.id,
            step: 'topics',
            level: 'info',
            message: `Dodano do kolejki: ${job.title}`,
          }),
        }))
        return job
      },
      removeJob: (accountId, jobId) =>
        set((state) => {
          const jobs = { ...state.jobs }
          delete jobs[jobId]
          return {
            jobs,
            queue: {
              ...state.queue,
              [accountId]: (state.queue[accountId] ?? []).filter((id) => id !== jobId),
            },
          }
        }),
      moveJob: (accountId, jobId, direction) =>
        set((state) => {
          const queue = [...(state.queue[accountId] ?? [])]
          const index = queue.indexOf(jobId)
          const target = direction === 'up' ? index - 1 : index + 1
          if (index < 0 || target < 0 || target >= queue.length) {
            return state
          }
          ;[queue[index], queue[target]] = [queue[target], queue[index]]
          return {
            ...state,
            queue: { ...state.queue, [accountId]: queue },
          }
        }),
      updateJob: (jobId, patch) =>
        set((state) => ({
          jobs: {
            ...state.jobs,
            [jobId]: {
              ...state.jobs[jobId],
              ...patch,
              updatedAt: isoNow(),
            },
          },
        })),
      startJob: (accountId, jobId) =>
        set((state) => {
          const job = state.jobs[jobId]
          if (!job) {
            return state
          }
          return {
            jobs: {
              ...state.jobs,
              [jobId]: {
                ...job,
                status: 'running',
                updatedAt: isoNow(),
                steps: job.steps.map((step, index) => ({
                  ...step,
                  status: index === 0 ? 'running' : 'queued',
                  progress: index === 0 ? Math.max(step.progress, 18) : 0,
                })),
              },
            },
            activeJobs: { ...state.activeJobs, [accountId]: jobId },
            logs: appendLogToMap(state.logs, {
              accountId,
              jobId,
              step: 'system',
              level: 'info',
              message: `URUCHOMIONO PIPELINE: ${job.title}`,
            }),
          }
        }),
      pauseAll: () =>
        set((state) => ({
          activeJobs: {},
          jobs: Object.fromEntries(
            Object.entries(state.jobs).map(([id, job]) => [
              id,
              job.status === 'running'
                ? {
                    ...job,
                    status: 'paused' as PipelineJobStatus,
                    updatedAt: isoNow(),
                  }
                : job,
            ]),
          ),
          logs: Object.keys(state.queue).reduce(
            (logs, accountId) =>
              appendLogToMap(logs, {
                accountId,
                step: 'system',
                level: 'warn',
                message: 'PAUZA WSZYSTKIEGO: zatrzymano aktywne joby frontend shell',
              }),
            state.logs,
          ),
        })),
      updateStep: (jobId, stepId, patch) =>
        set((state) => {
          const job = state.jobs[jobId]
          if (!job) {
            return state
          }
          const nextStatus = patch.status as PipelineStepStatus | undefined
          return {
            ...state,
            jobs: {
              ...state.jobs,
              [jobId]: {
                ...job,
                status: nextStatus === 'error' ? 'error' : job.status,
                updatedAt: isoNow(),
                steps: job.steps.map((step) =>
                  step.id === stepId ? { ...step, ...patch } : step,
                ),
              },
            },
          }
        }),
      ingestPipelineStep: (frame) =>
        set((state) => {
          const stepId = normalizeStepId(frame.step)
          if (!stepId) {
            return state
          }
          const status = normalizeStepStatus(frame.status)
          const jobId =
            frame.jobId ??
            state.activeJobs[frame.accountId] ??
            state.queue[frame.accountId]?.find((id) => state.jobs[id])
          const existingJob = jobId ? state.jobs[jobId] : null
          const nextJob = existingJob ?? createJob(frame.accountId, frame.title ?? 'Backend pipeline job')
          const resolvedJobId = existingJob ? nextJob.id : frame.jobId ?? nextJob.id
          const queueForAccount = state.queue[frame.accountId] ?? []
          const nextQueue = queueForAccount.includes(resolvedJobId)
            ? queueForAccount
            : [resolvedJobId, ...queueForAccount]
          const nextSteps = nextJob.steps.map((step) =>
            step.id === stepId
              ? {
                  ...step,
                  status,
                  progress:
                    frame.progress ??
                    (status === 'done' ? 100 : status === 'running' ? Math.max(step.progress, 24) : step.progress),
                  etaSeconds: frame.etaSeconds,
                  durationMs: frame.durationMs,
                  errorMessage: frame.error,
                }
              : step,
          )

          return {
            ...state,
            jobs: {
              ...state.jobs,
              [resolvedJobId]: {
                ...nextJob,
                id: resolvedJobId,
                status:
                  status === 'error'
                    ? 'error'
                    : status === 'done' && stepId === 'upload'
                      ? 'done'
                      : 'running',
                updatedAt: isoNow(),
                steps: nextSteps,
                videoUrl: frame.videoUrl ?? nextJob.videoUrl,
              },
            },
            queue: {
              ...state.queue,
              [frame.accountId]: nextQueue,
            },
            activeJobs: {
              ...state.activeJobs,
              [frame.accountId]: resolvedJobId,
            },
            logs: appendLogToMap(state.logs, {
              accountId: frame.accountId,
              jobId: resolvedJobId,
              step: stepId,
              level: status === 'error' ? 'error' : 'info',
              message:
                status === 'error'
                  ? frame.error ?? `${stepId} failed`
                  : `${stepId} -> ${status}${frame.videoId ? ` (${frame.videoId})` : ''}`,
            }),
          }
        }),
      appendLog: (entry) =>
        set((state) => ({
          logs: appendLogToMap(state.logs, entry),
        })),
    }),
    {
      name: 'atnos.content.machine.v1',
      version: 2,
    },
  ),
)

function normalizeStepId(value: string): PipelineStepId | null {
  const normalized = value.trim().toLowerCase().replace(/[_\s.-]+/g, '')
  const aliases: Record<string, PipelineStepId> = {
    topic: 'topics',
    topics: 'topics',
    tematy: 'topics',
    hook: 'hook',
    hooks: 'hook',
    script: 'script',
    skrypt: 'script',
    voice: 'voice',
    glos: 'voice',
    głos: 'voice',
    tts: 'voice',
    visuals: 'video',
    visual: 'video',
    assetplan: 'video',
    thumbnail: 'video',
    video: 'video',
    wideo: 'video',
    render: 'video',
    assembly: 'assembly',
    montaz: 'assembly',
    montaż: 'assembly',
    qa: 'qa',
    quality: 'qa',
    qualitygate: 'qa',
    upload: 'upload',
    uploadpackage: 'upload',
    privateupload: 'upload',
    done: 'upload',
  }
  return aliases[normalized] ?? null
}

function normalizeStepStatus(value: string): PipelineStepStatus {
  const normalized = value.trim().toLowerCase()
  if (normalized === 'running' || normalized === 'active') {
    return 'running'
  }
  if (
    normalized === 'done' ||
    normalized === 'completed' ||
    normalized === 'complete' ||
    normalized === 'qa_passed' ||
    normalized === 'uploaded_private'
  ) {
    return 'done'
  }
  if (normalized === 'error' || normalized === 'failed' || normalized === 'failure' || normalized === 'qa_failed') {
    return 'error'
  }
  if (normalized === 'queued' || normalized === 'pending' || normalized === 'selected') {
    return 'queued'
  }
  return 'idle'
}

function appendLogToMap(
  logs: Record<string, LogEntry[]>,
  entry: Omit<LogEntry, 'id' | 'timestamp'>,
) {
  const accountLogs = logs[entry.accountId] ?? []
  const next: LogEntry = {
    ...entry,
    id: makeId('log'),
    timestamp: isoNow(),
  }
  return {
    ...logs,
    [entry.accountId]: [next, ...accountLogs].slice(0, 200),
  }
}

export type PipelineStepId =
  | 'research'
  | 'script'
  | 'tts'
  | 'video'
  | 'thumbnail'
  | 'upload'

export type StepState = 'pending' | 'active' | 'done' | 'error'

export type PipelineStepState = StepState

export interface PipelineStep {
  id: PipelineStepId
  label?: string
  state: PipelineStepState
  progress?: number
  startedAt?: number
  finishedAt?: number
  durationMs?: number | null
  errorMessage?: string | null
  previewOnly?: boolean
  workItemId?: number | null
  workType?: string | null
  reviewNotes?: string | null
}

export interface ContentJob {
  id: string
  title: string
  createdAt: number
  steps: PipelineStep[]
  thumbnailUrl?: string
  videoUrl?: string
  platform: 'youtube'
  awaitingApproval: boolean
  approvalDeadline?: number
}

export interface PipelineJob {
  creatorProjectId: number
  projectId: number | null
  title: string
  projectType: string
  status: string
  platforms: string[]
  operatingMode: string
  approvalPolicy: string
  updatedAt: string
}

export interface PipelineSnapshot {
  previewOnly: boolean
  job: PipelineJob | null
  steps: PipelineStep[]
  latestVideoArtifact?: {
    title: string
    storagePath: string | null
    externalUrl: string | null
    updatedAt: string | null
  } | null
  awaitingApproval: boolean
  status: 'idle' | 'executing' | 'awaiting_human' | 'error'
  statusReason: string
  approvalPackage: {
    workItem: Record<string, unknown>
    project: Record<string, unknown>
    risk: string
    safetyFlags: string[]
    reviewNotes: string | null
    proposedNextAction: string
    actionBrokerPayloadPreview: Record<string, unknown>
  } | null
  lastReviewUpdate?: {
    workItemId: number
    status: string
    reviewNotes: string | null
  }
}

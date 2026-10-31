export type AccountType =
  | 'youtube'
  | 'instagram_reels'
  | 'tiktok'
  | 'onlyfans'
  | 'investment'
  | 'custom'

export type AccountStatus = 'live' | 'paused' | 'error' | 'idle'
export type AutomationMode = 'manual' | 'auto_private' | 'auto_public'
export type PipelineStepId =
  | 'topics'
  | 'hook'
  | 'script'
  | 'voice'
  | 'video'
  | 'assembly'
  | 'qa'
  | 'upload'
export type PipelineStepStatus = 'idle' | 'queued' | 'running' | 'done' | 'error'
export type PipelineJobStatus = 'queued' | 'running' | 'paused' | 'done' | 'error'
export type LogLevel = 'info' | 'warn' | 'error'

export interface CronSchedule {
  days: string[]
  hourUtc: number
  postsPerDay: number
}

export interface Account {
  id: string
  type: AccountType
  name: string
  status: AccountStatus
  niche: string
  platformConfig: Record<string, string | string[] | undefined>
  schedule: CronSchedule
  stats: {
    revenueMonth: number
    queueSize: number
    videosToday: number
    totalVideos: number
    lastUpload: string
  }
  automationMode: AutomationMode
}

export interface PipelineStep {
  id: PipelineStepId
  label: string
  status: PipelineStepStatus
  progress: number
  etaSeconds?: number
  durationMs?: number
  errorMessage?: string
}

export interface PipelineJob {
  id: string
  accountId: string
  title: string
  status: PipelineJobStatus
  steps: PipelineStep[]
  createdAt: string
  updatedAt: string
  videoUrl?: string
  thumbnailUrl?: string
  brief: {
    audience: string
    style: 'educational' | 'listicle' | 'documentary' | 'commentary'
    targetMinutes: number
  }
  script: string
  scenes: Array<{
    id: string
    title: string
    prompt: string
    model: 'ltx-video' | 'wan-video' | 'stable-video'
    seed: number
    steps: number
    cfg: number
  }>
}

export interface LogEntry {
  id: string
  accountId: string
  jobId?: string
  timestamp: string
  step: PipelineStepId | 'fleet' | 'system'
  level: LogLevel
  message: string
}

export interface RevenuePoint {
  date: string
  adsense: number
  affiliate: number
  sponsors: number
}

export interface RevenueStats {
  rpm: number
  cpm: number
  watchTimeHours: number
  ctr: number
}

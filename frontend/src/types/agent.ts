export type AgentStage =
  | 'idle'
  | 'thinking'
  | 'executing'
  | 'awaiting_human'
  | 'error'
  | 'offline'

export interface AgentSnapshot {
  stage: AgentStage
  lastActionAt: number | null
  currentTask: string | null
  errorMessage: string | null
}

export type AtnosCoreVisualState =
  | 'idle'
  | 'thinking'
  | 'executing'
  | 'error'
  | 'success'
  | 'offline'

export type AtnosCoreRuntimeState = AtnosCoreVisualState | 'awaiting_human'

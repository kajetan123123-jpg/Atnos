import type { LucideIcon } from 'lucide-react'
import type { PipelineSnapshot } from './pipeline'
import type { ConversationMessage } from './message'
import type { TrustedAutoExecutionRun } from './system'
import type { ToastItem } from './toast'
import type { ProjectSummary } from '../stores/chatStore'
import type { ConversationSummary } from './message'

export type CommandGroup =
  | 'CHAT'
  | 'ROZMOWY'
  | 'PROJEKTY'
  | 'NAWIGACJA'
  | 'PIPELINE'
  | 'MODEL'
  | 'SYSTEM'

export type CommandRunResult = 'close' | 'keep-open'
export type CommandScope = 'root' | 'model-picker' | 'conversation-picker' | 'project-picker'
export type ChatQuickToggleKey = 'memoryEnabled' | 'fileEnabled' | 'internetEnabled'

export interface ChatCommandSurface {
  activeConversationId: number | null
  activeConversationTitle: string | null
  activeProjectId: number | null
  activeProjectName: string | null
  quickToggles: {
    memoryEnabled: boolean
    fileEnabled: boolean
    internetEnabled: boolean
  } | null
  actions: {
    archiveConversation: () => Promise<void> | void
    selectConversation: (conversationId: number) => void
    selectProject: (projectId: number | null) => void
    showDecisions: () => Promise<boolean> | boolean
    toggleQuickSetting: (key: ChatQuickToggleKey) => Promise<void> | void
    triggerBriefing: () => Promise<boolean> | boolean
  } | null
}

export interface CommandContext {
  closeModelPicker: () => void
  closeScope: () => void
  conversations: ConversationSummary[]
  executionRuns: TrustedAutoExecutionRun[]
  chatCommandSurface: ChatCommandSurface | null
  messages: ConversationMessage[]
  navigate: (route: string) => void
  openConversationPicker: () => void
  openModelPicker: () => void
  openProjectPicker: () => void
  pathname: string
  pipelineSnapshot: PipelineSnapshot | null
  projects: ProjectSummary[]
  pushToast: (toast: Omit<ToastItem, 'id'>) => void
  requestBriefing: () => void
  requestComposerFocus: () => void
  requestDecisions: () => void
  resetConversationWorkspace: (title?: string | null) => void
  selectConversationWorkspace: (conversationId: number) => void
  selectProjectWorkspace: (projectId: number | null) => void
  selectedConversationId: number | null
  selectedConversationTitle: string | null
  selectedProjectId: number | null
  upsertExecutionRun: (run: TrustedAutoExecutionRun) => void
}

export interface Command {
  id: string
  group: CommandGroup
  label: string
  icon: LucideIcon
  shortcut?: string
  disabled?: boolean
  description?: string
  keywords?: string[]
  run: (ctx: CommandContext) => Promise<CommandRunResult> | CommandRunResult
}

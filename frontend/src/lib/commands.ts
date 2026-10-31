import {
  Archive,
  BarChart3,
  Brain,
  Cpu,
  FilePlus2,
  FolderOpenDot,
  Globe,
  ListChecks,
  MessageSquare,
  Paperclip,
  Search,
  Settings2,
} from 'lucide-react'
import { switchLocalChatModel } from './api'
import { formatRelativeDateTime } from './format'
import type { Command, CommandContext, CommandRunResult } from '../types/command'

export type { Command, CommandContext, CommandGroup, CommandRunResult } from '../types/command'

function sortConversationsByRecency(
  left: CommandContext['conversations'][number],
  right: CommandContext['conversations'][number],
) {
  const leftTimestamp = Date.parse(left.lastMessageAt ?? left.updatedAt ?? '')
  const rightTimestamp = Date.parse(right.lastMessageAt ?? right.updatedAt ?? '')
  return rightTimestamp - leftTimestamp
}

function findProjectName(ctx: CommandContext, projectId: number | null) {
  if (projectId == null) {
    return null
  }
  return ctx.projects.find((project) => project.id === projectId)?.name ?? null
}

function createChatOnlyWarning(ctx: CommandContext, actionLabel: string) {
  ctx.pushToast({
    type: 'warning',
    title: `${actionLabel} wymaga aktywnej rozmowy`,
    message: 'Wroc do Chatu albo otworz rozmowe, aby wykonac te akcje.',
  })
}

function runQuickToggle(
  ctx: CommandContext,
  key: 'memoryEnabled' | 'fileEnabled' | 'internetEnabled',
  successTitle: string,
): CommandRunResult {
  const surface = ctx.chatCommandSurface
  if (!surface?.actions || !surface.quickToggles) {
    createChatOnlyWarning(ctx, successTitle)
    return 'close'
  }
  void surface.actions.toggleQuickSetting(key)
  return 'close'
}

export function getRootCommands(ctx: CommandContext): Command[] {
  const surface = ctx.chatCommandSurface
  const quickToggles = surface?.quickToggles

  return [
    {
      id: 'chat:focus-composer',
      group: 'CHAT',
      label: 'Skup composer',
      description: 'Przenies fokus do pola wpisywania bez klikania w dol rozmowy.',
      icon: MessageSquare,
      shortcut: 'Ctrl+/',
      keywords: ['pisanie', 'prompt', 'focus', 'composer'],
      run: () => {
        ctx.navigate('/chat')
        ctx.requestComposerFocus()
        return 'close'
      },
    },
    {
      id: 'chat:new-conversation',
      group: 'CHAT',
      label: 'Nowa rozmowa',
      description: 'Zacznij czysty watek bez opuszczania aktualnego projektu.',
      icon: FilePlus2,
      shortcut: 'Ctrl+Shift+N',
      keywords: ['nowy chat', 'new conversation', 'draft'],
      run: () => {
        ctx.resetConversationWorkspace('Nowa rozmowa')
        ctx.navigate('/chat')
        ctx.requestComposerFocus()
        return 'close'
      },
    },
    {
      id: 'chat:briefing',
      group: 'CHAT',
      label: 'Uruchom briefing',
      description: 'Zbierz GitHub, Linear, commitments i pamiec projektu w jedna karte.',
      icon: BarChart3,
      shortcut: 'Ctrl+Shift+B',
      keywords: ['daily brief', 'status dnia', 'morning'],
      run: () => {
        if (surface?.actions) {
          void surface.actions.triggerBriefing()
          return 'close'
        }
        ctx.navigate('/chat')
        ctx.requestBriefing()
        return 'close'
      },
    },
    {
      id: 'chat:decisions',
      group: 'CHAT',
      label: 'Pokaz decyzje i commitments',
      description: 'Wypisz aktywne commitments i otwarte follow-upy dla biezacego kontekstu.',
      icon: ListChecks,
      keywords: ['decisions', 'commitments', 'follow-up', 'decyzje'],
      run: () => {
        if (surface?.actions) {
          void surface.actions.showDecisions()
          return 'close'
        }
        ctx.navigate('/chat')
        ctx.requestDecisions()
        return 'close'
      },
    },
    {
      id: 'chat:search-conversations',
      group: 'ROZMOWY',
      label: 'Szukaj rozmow',
      description: 'Przejdz do wybranej rozmowy bez celowania w liste po lewej.',
      icon: Search,
      shortcut: 'Ctrl+K',
      keywords: ['find chat', 'conversation history', 'search'],
      run: () => {
        ctx.openConversationPicker()
        return 'keep-open'
      },
    },
    {
      id: 'chat:switch-project',
      group: 'PROJEKTY',
      label: 'Przelacz projekt',
      description: 'Zmien aktywny projekt i zawęź rozmowy do wlasciwego kontekstu.',
      icon: FolderOpenDot,
      keywords: ['project', 'workspace', 'switch'],
      run: () => {
        ctx.openProjectPicker()
        return 'keep-open'
      },
    },
    {
      id: 'chat:open-active-project',
      group: 'PROJEKTY',
      label: surface?.activeProjectName
        ? `Otworz projekt: ${surface.activeProjectName}`
        : 'Otworz aktywny projekt',
      description: surface?.activeProjectName
        ? 'Skocz prosto do ustawien aktywnego projektu.'
        : 'Brak aktywnego projektu w tej rozmowie.',
      icon: FolderOpenDot,
      disabled: surface?.activeProjectId == null,
      keywords: ['project settings', 'active project'],
      run: () => {
        if (surface?.activeProjectId == null) {
          ctx.pushToast({
            type: 'warning',
            title: 'Brak aktywnego projektu',
            message: 'Ta rozmowa nie jest teraz przypieta do projektu.',
          })
          return 'close'
        }
        surface.actions?.selectProject(surface.activeProjectId)
        ctx.navigate('/chat#project-settings-panel')
        return 'close'
      },
    },
    {
      id: 'chat:toggle-memory',
      group: 'CHAT',
      label: quickToggles?.memoryEnabled ? 'Wylacz pamiec rozmowy' : 'Wlacz pamiec rozmowy',
      description:
        quickToggles == null
          ? 'Dostepne tylko w aktywnej rozmowie.'
          : quickToggles.memoryEnabled
            ? 'Pamiec jest teraz wlaczona dla tej rozmowy.'
            : 'Pamiec jest teraz wylaczona dla tej rozmowy.',
      icon: Brain,
      disabled: quickToggles == null,
      keywords: ['memory', 'context'],
      run: () => runQuickToggle(ctx, 'memoryEnabled', 'Pamiec rozmowy'),
    },
    {
      id: 'chat:toggle-files',
      group: 'CHAT',
      label: quickToggles?.fileEnabled ? 'Wylacz pliki w rozmowie' : 'Wlacz pliki w rozmowie',
      description:
        quickToggles == null
          ? 'Dostepne tylko w aktywnej rozmowie.'
          : quickToggles.fileEnabled
            ? 'Pliki sa teraz wlaczone dla biezacego watku.'
            : 'Pliki sa teraz wylaczone dla biezacego watku.',
      icon: Paperclip,
      disabled: quickToggles == null,
      keywords: ['attachments', 'files', 'upload'],
      run: () => runQuickToggle(ctx, 'fileEnabled', 'Pliki rozmowy'),
    },
    {
      id: 'chat:toggle-internet',
      group: 'CHAT',
      label: quickToggles?.internetEnabled ? 'Wylacz internet w rozmowie' : 'Wlacz internet w rozmowie',
      description:
        quickToggles == null
          ? 'Dostepne tylko w aktywnej rozmowie.'
          : quickToggles.internetEnabled
            ? 'Internet jest teraz wlaczony dla tej rozmowy.'
            : 'Internet jest teraz wylaczony dla tej rozmowy.',
      icon: Globe,
      disabled: quickToggles == null,
      keywords: ['web', 'search', 'internet'],
      run: () => runQuickToggle(ctx, 'internetEnabled', 'Internet rozmowy'),
    },
    {
      id: 'chat:archive-conversation',
      group: 'ROZMOWY',
      label: 'Archiwizuj biezaca rozmowe',
      description: 'Odepnij rozmowe z glownej listy i przejdz do nastepnego watku.',
      icon: Archive,
      shortcut: 'Ctrl+Shift+A',
      disabled: surface?.activeConversationId == null || !surface.actions,
      keywords: ['hide chat', 'archive current'],
      run: () => {
        if (!surface?.actions) {
          createChatOnlyWarning(ctx, 'Archiwizacja')
          return 'close'
        }
        void surface.actions.archiveConversation()
        return 'close'
      },
    },
    {
      id: 'model:switch',
      group: 'MODEL',
      label: 'Przelacz model',
      description: 'Zmien lokalny model czatu bez wychodzenia do ustawien.',
      icon: Cpu,
      keywords: ['model picker', 'llama', 'mistral'],
      run: () => {
        ctx.openModelPicker()
        return 'keep-open'
      },
    },
    {
      id: 'nav:settings',
      group: 'NAWIGACJA',
      label: 'Otworz ustawienia',
      description: 'Przejdz do glownej zakladki ustawien.',
      icon: Settings2,
      keywords: ['settings', 'providers', 'preferences'],
      run: () => {
        ctx.navigate('/settings')
        return 'close'
      },
    },
  ]
}

export function getConversationCommands(ctx: CommandContext): Command[] {
  return ctx.conversations
    .filter((conversation) => !conversation.archived)
    .sort(sortConversationsByRecency)
    .map((conversation) => {
      const projectName = findProjectName(ctx, conversation.projectId)
      const title = conversation.title.trim() || 'Nowa rozmowa'
      const recency = formatRelativeDateTime(conversation.lastMessageAt ?? conversation.updatedAt)
      return {
        id: `conversation:${conversation.id}`,
        group: 'ROZMOWY' as const,
        label: title,
        description: projectName ? `${projectName} · ${recency}` : recency,
        icon: MessageSquare,
        keywords: [conversation.summary ?? '', projectName ?? '', conversation.matchedSource ?? ''],
        run: () => {
          ctx.selectConversationWorkspace(conversation.id)
          ctx.navigate('/chat')
          return 'close' as const
        },
      }
    })
}

export function getProjectCommands(ctx: CommandContext): Command[] {
  const allProjectsCommand: Command = {
    id: 'project:all',
    group: 'PROJEKTY',
    label: 'Wszystkie projekty',
    description: 'Pokaz wszystkie rozmowy bez filtrowania do jednego projektu.',
    icon: FolderOpenDot,
    keywords: ['all projects', 'workspace'],
    run: () => {
      ctx.selectProjectWorkspace(null)
      ctx.navigate('/chat')
      return 'close'
    },
  }

  const projectCommands = ctx.projects.map((project) => ({
    id: `project:${project.id}`,
    group: 'PROJEKTY' as const,
    label: project.name,
    description: project.summary ?? project.description ?? formatProjectStatus(project.status),
    icon: FolderOpenDot,
    keywords: [project.slug, project.status, project.summary ?? '', project.description ?? ''],
    run: () => {
      ctx.selectProjectWorkspace(project.id)
      ctx.navigate('/chat#project-settings-panel')
      return 'close' as const
    },
  }))

  return [allProjectsCommand, ...projectCommands]
}

function formatProjectStatus(status: string) {
  switch (status) {
    case 'active':
      return 'Aktywny projekt'
    case 'archived':
      return 'Projekt archiwalny'
    case 'on_hold':
      return 'Projekt wstrzymany'
    default:
      return status
  }
}

export const MODEL_PICKER_COMMANDS: Command[] = [
  {
    id: 'model:llama3',
    group: 'MODEL',
    label: 'Zaladuj llama3',
    description: 'Przelacz lokalny chat na rodzine llama3.',
    icon: Cpu,
    shortcut: 'M L',
    keywords: ['local model', 'ollama'],
    run: async (ctx): Promise<CommandRunResult> => {
      try {
        const result = await switchLocalChatModel('llama3')
        ctx.pushToast({
          type: 'success',
          title: 'Model przelaczony: llama3',
          message: `${result.runtimeModelName} jest teraz aktywnym lokalnym modelem czatu.`,
        })
      } catch (error) {
        ctx.pushToast({
          type: 'error',
          title: 'Nie udalo sie zaladowac llama3',
          message:
            error instanceof Error
              ? error.message
              : 'Runtime nie potwierdzil zmiany modelu.',
        })
      }
      return 'close'
    },
  },
  {
    id: 'model:mistral',
    group: 'MODEL',
    label: 'Zaladuj mistral',
    description: 'Przelacz lokalny chat na mistrala.',
    icon: Cpu,
    shortcut: 'M M',
    keywords: ['local model', 'mistral'],
    run: async (ctx): Promise<CommandRunResult> => {
      try {
        const result = await switchLocalChatModel('mistral')
        ctx.pushToast({
          type: 'success',
          title: 'Model przelaczony: mistral',
          message: `${result.runtimeModelName} jest teraz aktywnym lokalnym modelem czatu.`,
        })
      } catch (error) {
        ctx.pushToast({
          type: 'error',
          title: 'Nie udalo sie zaladowac mistral',
          message:
            error instanceof Error
              ? error.message
              : 'Runtime nie potwierdzil zmiany modelu.',
        })
      }
      return 'close'
    },
  },
]

export function getTopRowCommand(ctx: CommandContext): Command {
  if (ctx.pathname === '/chat' || ctx.pathname === '/') {
    return {
      id: 'special:focus-composer',
      group: 'CHAT',
      label: 'Skup composer',
      description: 'Najczestsza akcja: od razu piszesz kolejny prompt.',
      icon: MessageSquare,
      shortcut: 'Ctrl+/',
      run: () => {
        ctx.requestComposerFocus()
        return 'close'
      },
    }
  }

  return {
    id: 'special:open-chat',
    group: 'NAWIGACJA',
    label: 'Wroc do rozmowy',
    description: 'Przejdz do glownej powierzchni pracy Atnosa.',
    icon: MessageSquare,
    shortcut: 'G C',
    run: () => {
      ctx.navigate('/chat')
      return 'close'
    },
  }
}

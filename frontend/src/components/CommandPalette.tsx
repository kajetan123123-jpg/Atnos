import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Search } from 'lucide-react'
import FocusTrap from 'focus-trap-react'
import Fuse from 'fuse.js'
import { AnimatePresence, motion } from 'framer-motion'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import type { Command, CommandContext, CommandScope } from '../types/command'
import {
  getConversationCommands,
  getProjectCommands,
  getRootCommands,
  getTopRowCommand,
  MODEL_PICKER_COMMANDS,
} from '../lib/commands'
import { DUR, EASE, VARIANTS } from '../lib/motion'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { useHotkey } from '../hooks/useHotkey'
import { useReducedMotionStrict } from '../hooks/useReducedMotionStrict'
import { useChatStore } from '../stores/chatStore'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import { useUIStore } from '../stores/uiStore'
import { Kbd } from './kbd'

interface PaletteItem {
  command: Command
  isTopRow: boolean
}

function getPaletteTitle(scope: CommandScope) {
  switch (scope) {
    case 'conversation-picker':
      return 'SZUKAJ ROZMOW'
    case 'project-picker':
      return 'PRZELACZ PROJEKT'
    case 'model-picker':
      return 'PRZELACZ MODEL'
    default:
      return 'PALETA POLECEN'
  }
}

function getPaletteSubtitle(scope: CommandScope) {
  switch (scope) {
    case 'conversation-picker':
      return 'Przeskakuj miedzy rozmowami bez celowania w liste po lewej.'
    case 'project-picker':
      return 'Zmien aktywny projekt i od razu wejdz w odpowiedni kontekst pracy.'
    case 'model-picker':
      return 'Lokalny model czatu zmieniasz bez wychodzenia do ustawien.'
    default:
      return 'Najkrotsza droga do codziennych akcji Chatu, projektow i ustawien.'
  }
}

function getPalettePlaceholder(scope: CommandScope) {
  switch (scope) {
    case 'conversation-picker':
      return 'Wpisz tytul rozmowy...'
    case 'project-picker':
      return 'Wpisz nazwe projektu...'
    case 'model-picker':
      return 'Wpisz nazwe modelu...'
    default:
      return 'Wpisz akcje, projekt albo rozmowe...'
  }
}

export function CommandPalette() {
  const navigate = useNavigate()
  const location = useLocation()
  const { isMobile } = useBreakpoint()
  const reducedMotion = useReducedMotionStrict()
  const open = useUIStore((state) => state.paletteOpen)
  const lowPerf = useUIStore((state) => state.lowPerf)
  const openPalette = useUIStore((state) => state.openPalette)
  const closePalette = useUIStore((state) => state.closePalette)
  const requestBriefing = useUIStore((state) => state.requestBriefing)
  const requestComposerFocus = useUIStore((state) => state.requestComposerFocus)
  const requestDecisions = useUIStore((state) => state.requestDecisions)
  const chatCommandSurface = useUIStore((state) => state.chatCommandSurface)
  const conversations = useChatStore((state) => state.conversations)
  const messages = useChatStore((state) => state.messages)
  const projects = useChatStore((state) => state.projects)
  const selectedConversationId = useChatStore((state) => state.selectedConversationId)
  const selectedConversationTitle = useChatStore((state) => state.selectedConversationTitle)
  const selectedProjectId = useChatStore((state) => state.selectedProjectId)
  const setSelectedConversation = useChatStore((state) => state.setSelectedConversation)
  const setSelectedProjectId = useChatStore((state) => state.setSelectedProjectId)
  const resetConversationWorkspace = useChatStore((state) => state.resetConversationWorkspace)
  const pipelineSnapshot = usePipelineStore((state) => state.snapshot)
  const executionRuns = useSystemStore((state) => state.executionRuns)
  const upsertExecutionRun = useSystemStore((state) => state.upsertExecutionRun)
  const pushToast = useToastStore((state) => state.push)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const [scope, setScope] = useState<CommandScope>('root')
  const optionRefs = useRef(new Map<string, HTMLButtonElement>())
  const inputRef = useRef<HTMLInputElement | null>(null)

  const overlayMotion = reducedMotion
    ? {
        initial: { opacity: 0 },
        animate: { opacity: 1, transition: { duration: 0 } },
        exit: { opacity: 0, transition: { duration: 0 } },
      }
    : VARIANTS.overlay
  const panelMotion = reducedMotion
    ? {
        initial: { opacity: 0, scale: 0.96, y: 8 },
        animate: { opacity: 1, scale: 1, y: 0, transition: { duration: 0 } },
        exit: { opacity: 0, scale: 0.98, transition: { duration: 0 } },
      }
    : VARIANTS.panelPop

  useHotkey(['mod+k'], (event) => {
    event.preventDefault()
    if (open) {
      closePalette()
      return
    }
    openPalette()
  })

  useHotkey(['escape'], (event) => {
    if (!open) {
      return
    }
    event.preventDefault()
    if (scope !== 'root') {
      setScope('root')
      setQuery('')
      setSelected(0)
      return
    }
    closePalette()
  })

  useEffect(() => {
    if (open) {
      inputRef.current?.focus()
      return
    }
    setQuery('')
    setSelected(0)
    setScope('root')
  }, [open])

  const closeAndReset = () => {
    closePalette()
    setQuery('')
    setSelected(0)
    setScope('root')
  }

  const selectConversationWorkspace = (conversationId: number) => {
    const nextConversation =
      conversations.find((conversation) => conversation.id === conversationId && !conversation.archived) ?? null
    if (!nextConversation) {
      pushToast({
        type: 'warning',
        title: 'Rozmowa nie jest juz dostepna',
        message: 'Odswiez liste rozmow i sprobuj ponownie.',
      })
      return
    }
    setSelectedConversation(nextConversation.id, nextConversation.title)
  }

  const selectProjectWorkspace = (projectId: number | null) => {
    setSelectedProjectId(projectId)

    const currentConversation = conversations.find(
      (conversation) => conversation.id === selectedConversationId,
    )
    if (currentConversation && (projectId == null || currentConversation.projectId === projectId)) {
      return
    }

    const nextConversation = conversations.find(
      (conversation) => !conversation.archived && (projectId == null || conversation.projectId === projectId),
    )

    if (nextConversation) {
      setSelectedConversation(nextConversation.id, nextConversation.title)
      return
    }

    resetConversationWorkspace('Nowa rozmowa')
  }

  const commandContext = useMemo<CommandContext>(
    () => ({
      chatCommandSurface,
      closeModelPicker: () => {
        setScope('root')
        setQuery('')
        setSelected(0)
      },
      closeScope: () => {
        setScope('root')
        setQuery('')
        setSelected(0)
      },
      conversations,
      executionRuns,
      messages,
      navigate,
      openConversationPicker: () => {
        setScope('conversation-picker')
        setQuery('')
        setSelected(0)
      },
      openModelPicker: () => {
        setScope('model-picker')
        setQuery('')
        setSelected(0)
      },
      openProjectPicker: () => {
        setScope('project-picker')
        setQuery('')
        setSelected(0)
      },
      pathname: location.pathname,
      pipelineSnapshot,
      projects,
      pushToast,
      requestBriefing,
      requestComposerFocus,
      requestDecisions,
      resetConversationWorkspace,
      selectConversationWorkspace,
      selectedConversationId,
      selectedConversationTitle,
      selectedProjectId,
      selectProjectWorkspace,
      upsertExecutionRun,
    }),
    [
      chatCommandSurface,
      conversations,
      executionRuns,
      location.pathname,
      messages,
      navigate,
      pipelineSnapshot,
      projects,
      pushToast,
      requestBriefing,
      requestComposerFocus,
      requestDecisions,
      resetConversationWorkspace,
      selectedConversationId,
      selectedConversationTitle,
      selectedProjectId,
      upsertExecutionRun,
    ],
  )

  const activeCommands = useMemo(() => {
    if (scope === 'model-picker') {
      return MODEL_PICKER_COMMANDS
    }
    if (scope === 'conversation-picker') {
      return getConversationCommands(commandContext)
    }
    if (scope === 'project-picker') {
      return getProjectCommands(commandContext)
    }
    return getRootCommands(commandContext)
  }, [commandContext, scope])

  const topRowCommand = useMemo(
    () => (scope === 'root' && !query.trim() ? getTopRowCommand(commandContext) : null),
    [commandContext, query, scope],
  )

  const fuse = useMemo(
    () =>
      new Fuse(activeCommands, {
        keys: [
          { name: 'label', weight: 0.7 },
          { name: 'description', weight: 0.2 },
          { name: 'keywords', weight: 0.4 },
          { name: 'group', weight: 0.15 },
        ],
        threshold: 0.34,
      }),
    [activeCommands],
  )

  const items = useMemo<PaletteItem[]>(() => {
    const filteredCommands = query.trim()
      ? fuse.search(query).map((entry) => entry.item)
      : activeCommands

    if (!topRowCommand) {
      return filteredCommands.map((command) => ({ command, isTopRow: false }))
    }

    return [
      { command: topRowCommand, isTopRow: true },
      ...filteredCommands.map((command) => ({ command, isTopRow: false })),
    ]
  }, [activeCommands, fuse, query, topRowCommand])

  useEffect(() => {
    if (!items.length) {
      setSelected(0)
      return
    }
    setSelected((current) => Math.min(current, items.length - 1))
  }, [items.length])

  useEffect(() => {
    if (!open) {
      return
    }
    const item = items[selected]
    if (!item) {
      return
    }
    const node = optionRefs.current.get(item.command.id)
    if (!node) {
      return
    }
    window.requestAnimationFrame(() => {
      node.scrollIntoView?.({ block: 'nearest' })
    })
  }, [items, open, selected])

  const executeCommand = async (command: Command | undefined) => {
    if (!command) {
      return
    }
    if (command.disabled) {
      pushToast({
        type: 'warning',
        title: 'Ta akcja jest teraz niedostepna',
        message: 'Najpierw wejdz do aktywnej rozmowy albo wybierz projekt.',
      })
      return
    }
    const outcome = await command.run(commandContext)
    if (outcome !== 'keep-open') {
      closeAndReset()
    }
  }

  const handlePanelKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!items.length) {
      if (event.key === 'Escape') {
        event.preventDefault()
        if (scope !== 'root') {
          setScope('root')
          setQuery('')
          setSelected(0)
          return
        }
        closeAndReset()
      }
      return
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setSelected((value) => (value + 1) % items.length)
      return
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setSelected((value) => (value - 1 + items.length) % items.length)
      return
    }

    if (event.key === 'Enter') {
      event.preventDefault()
      void executeCommand(items[selected]?.command)
      return
    }

    if (event.key === 'Escape') {
      event.preventDefault()
      if (scope !== 'root') {
        setScope('root')
        setQuery('')
        setSelected(0)
        return
      }
      closeAndReset()
    }
  }

  if (typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <AnimatePresence>
      {open ? (
        <motion.div
          initial={
            isMobile
              ? overlayMotion.initial
              : { ...overlayMotion.initial, backdropFilter: 'blur(0px)' }
          }
          animate={
            isMobile
              ? overlayMotion.animate
              : {
                  ...overlayMotion.animate,
                  backdropFilter: lowPerf ? 'blur(0px)' : 'blur(8px)',
                  transition: reducedMotion
                    ? { duration: 0 }
                    : { duration: DUR.fast, ease: EASE.standard },
                }
          }
          exit={
            isMobile
              ? overlayMotion.exit
              : {
                  ...overlayMotion.exit,
                  backdropFilter: 'blur(0px)',
                  transition: reducedMotion
                    ? { duration: 0 }
                    : { duration: DUR.fast, ease: EASE.standard },
                }
          }
          className={`fixed inset-0 z-palette ${isMobile ? 'bg-panel' : ''}`}
          style={
            isMobile
              ? undefined
              : {
                  backgroundColor: lowPerf
                    ? 'var(--scrim-command-palette-low-perf)'
                    : 'var(--scrim-command-palette)',
                }
          }
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeAndReset()
            }
          }}
        >
          <FocusTrap
            active
            focusTrapOptions={{
              clickOutsideDeactivates: true,
              escapeDeactivates: false,
              initialFocus: false,
            }}
          >
            <motion.div
              initial={panelMotion.initial}
              animate={panelMotion.animate}
              exit={panelMotion.exit}
              onKeyDown={handlePanelKeyDown}
              className={
                isMobile
                  ? 'flex h-full flex-col bg-panel'
                  : 'shell-panel-strong shell-route mx-auto mt-16 flex w-[min(680px,92vw)] flex-col overflow-hidden'
              }
              role="dialog"
              aria-modal="true"
              aria-label="Paleta polecen"
            >
              <div className="border-b border-border-default px-4 py-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <div className="type-label text-text-tertiary">{getPaletteTitle(scope)}</div>
                    <div className="mt-1 type-body-sm text-text-secondary">
                      {getPaletteSubtitle(scope)}
                    </div>
                  </div>
                  {!isMobile ? <Kbd>{scope === 'root' ? 'ESC' : 'ESC · WROC'}</Kbd> : null}
                </div>
                <div className="shell-module flex min-h-14 items-center gap-3 px-4">
                  <Search size={16} className="text-text-tertiary" />
                  <input
                    ref={inputRef}
                    autoFocus
                    value={query}
                    onChange={(event) => {
                      setQuery(event.target.value)
                      setSelected(0)
                    }}
                    placeholder={getPalettePlaceholder(scope)}
                    aria-label="Wpisz polecenie albo szukaj"
                    className="type-body-lg h-14 flex-1 bg-transparent text-text-primary outline-none placeholder:text-text-tertiary"
                  />
                </div>
              </div>

              <div
                className="scroll-chrome max-h-[70vh] overflow-y-auto py-2"
                role="listbox"
                aria-label="Wyniki palety polecen"
              >
                {items.length ? (
                  items.map((item, index) => {
                    let previousNonTop: PaletteItem | null = null
                    for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
                      if (!items[cursor]?.isTopRow) {
                        previousNonTop = items[cursor] ?? null
                        break
                      }
                    }

                    const showGroupLabel =
                      !item.isTopRow &&
                      (!previousNonTop || previousNonTop.command.group !== item.command.group)
                    const isSelected = index === selected
                    const Icon = item.command.icon

                    return (
                      <div key={item.command.id}>
                        {showGroupLabel ? (
                          <div className="px-4 pb-2 pt-3 type-label text-text-tertiary">
                            {item.command.group}
                          </div>
                        ) : null}
                        <button
                          id={`command-option-${item.command.id}`}
                          ref={(node) => {
                            if (node) {
                              optionRefs.current.set(item.command.id, node)
                            } else {
                              optionRefs.current.delete(item.command.id)
                            }
                          }}
                          type="button"
                          role="option"
                          aria-selected={isSelected}
                          disabled={item.command.disabled}
                          onMouseEnter={() => setSelected(index)}
                          onClick={() => void executeCommand(item.command)}
                          className={`relative flex min-h-14 w-full items-center gap-3 px-4 py-3 text-left ease-standard ${
                            item.command.disabled
                              ? 'cursor-not-allowed text-text-disabled duration-fast'
                              : isSelected
                                ? 'bg-elevated text-text-primary duration-instant'
                                : 'text-text-secondary duration-instant hover:bg-elevated/80 hover:text-text-primary'
                          }`}
                        >
                          {isSelected ? (
                            <span className="absolute inset-y-0 left-0 w-0.5 bg-accent-primary" />
                          ) : null}
                          <Icon
                            size={16}
                            strokeWidth={1.5}
                            className={item.command.disabled ? 'text-text-disabled' : undefined}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="type-body text-current">{item.command.label}</div>
                            {item.command.description ? (
                              <div className="type-body-sm text-text-tertiary">
                                {item.command.description}
                              </div>
                            ) : null}
                          </div>
                          {item.command.shortcut ? <Kbd>{item.command.shortcut}</Kbd> : null}
                        </button>
                      </div>
                    )
                  })
                ) : (
                  <div className="px-4 py-8 text-center">
                    <div className="type-body text-text-primary">Brak pasujacych akcji</div>
                    <div className="mt-2 type-body-sm text-text-tertiary">
                      Zmien fraze albo wroc do glownej listy komend.
                    </div>
                  </div>
                )}
              </div>
            </motion.div>
          </FocusTrap>
        </motion.div>
      ) : null}
    </AnimatePresence>,
    document.body,
  )
}

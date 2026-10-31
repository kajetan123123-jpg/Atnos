import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { ChatCommandSurface } from '../types/command'

const SIDEBAR_STORAGE_KEY = 'atnos.sidebar.expanded'

function readSidebarExpandedPreference() {
  if (typeof window === 'undefined') {
    return null
  }
  const raw = window.localStorage.getItem(SIDEBAR_STORAGE_KEY)
  if (raw === 'false') {
    return false
  }
  if (raw === 'true') {
    return true
  }
  return null
}

function writeSidebarExpandedPreference(value: boolean) {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(value))
}

interface UIState {
  sidebarExpanded: boolean
  sidebarPreferenceSet: boolean
  sidebarDrawerOpen: boolean
  hudDetailsOpen: boolean
  paletteOpen: boolean
  lowPerf: boolean
  pipelineSheetOpen: boolean
  desktopPipelineOpen: boolean
  okropekModeEnabled: boolean
  okropekPanelOpen: boolean
  okropekDockSide: 'left' | 'right'
  briefingNonce: number
  decisionsNonce: number
  composerFocusNonce: number
  chatCommandSurface: ChatCommandSurface | null
  toggleSidebar: () => void
  setSidebarExpanded: (value: boolean) => void
  applySidebarBreakpointDefault: (value: boolean) => void
  setSidebarDrawerOpen: (value: boolean) => void
  setHudDetailsOpen: (value: boolean) => void
  openPalette: () => void
  closePalette: () => void
  setLowPerf: (value: boolean) => void
  setPipelineSheetOpen: (value: boolean) => void
  setDesktopPipelineOpen: (value: boolean) => void
  setOkropekModeEnabled: (value: boolean) => void
  setOkropekPanelOpen: (value: boolean) => void
  setOkropekDockSide: (value: 'left' | 'right') => void
  requestBriefing: () => void
  requestDecisions: () => void
  requestComposerFocus: () => void
  setChatCommandSurface: (value: ChatCommandSurface | null) => void
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarExpanded: readSidebarExpandedPreference() ?? false,
      sidebarPreferenceSet: readSidebarExpandedPreference() != null,
      sidebarDrawerOpen: false,
      hudDetailsOpen: false,
      paletteOpen: false,
      lowPerf: false,
      pipelineSheetOpen: false,
      desktopPipelineOpen: false,
      okropekModeEnabled: false,
      okropekPanelOpen: false,
      okropekDockSide: 'right',
      briefingNonce: 0,
      decisionsNonce: 0,
      composerFocusNonce: 0,
      chatCommandSurface: null,
      toggleSidebar: () =>
        set((state) => {
          const next = !state.sidebarExpanded
          writeSidebarExpandedPreference(next)
          return { sidebarExpanded: next, sidebarPreferenceSet: true }
        }),
      setSidebarExpanded: (value) => {
        writeSidebarExpandedPreference(value)
        set({ sidebarExpanded: value, sidebarPreferenceSet: true })
      },
      applySidebarBreakpointDefault: (value) =>
        set((state) =>
          state.sidebarPreferenceSet
            ? state
            : { ...state, sidebarExpanded: value },
        ),
      setSidebarDrawerOpen: (value) => set({ sidebarDrawerOpen: value }),
      setHudDetailsOpen: (value) => set({ hudDetailsOpen: value }),
      openPalette: () => set({ paletteOpen: true }),
      closePalette: () => set({ paletteOpen: false }),
      setLowPerf: (value) => set({ lowPerf: value }),
      setPipelineSheetOpen: (value) => set({ pipelineSheetOpen: value }),
      setDesktopPipelineOpen: (value) => set({ desktopPipelineOpen: value }),
      setOkropekModeEnabled: (value) =>
        set({
          okropekModeEnabled: value,
          okropekPanelOpen: false,
        }),
      setOkropekPanelOpen: (value) => set({ okropekPanelOpen: value }),
      setOkropekDockSide: (value) => set({ okropekDockSide: value }),
      requestBriefing: () => set((state) => ({ briefingNonce: state.briefingNonce + 1 })),
      requestDecisions: () => set((state) => ({ decisionsNonce: state.decisionsNonce + 1 })),
      requestComposerFocus: () =>
        set((state) => ({ composerFocusNonce: state.composerFocusNonce + 1 })),
      setChatCommandSurface: (value) => set({ chatCommandSurface: value }),
    }),
    {
      name: 'atnos.ui',
      storage: createJSONStorage(() => window.localStorage),
      merge: (persistedState, currentState) => ({
        ...currentState,
        ...(persistedState as Partial<UIState>),
        sidebarExpanded: readSidebarExpandedPreference() ?? currentState.sidebarExpanded,
        sidebarPreferenceSet:
          readSidebarExpandedPreference() != null ||
          Boolean((persistedState as Partial<UIState> | undefined)?.sidebarPreferenceSet),
      }),
      partialize: (state) => ({
        sidebarPreferenceSet: state.sidebarPreferenceSet,
        okropekModeEnabled: state.okropekModeEnabled,
        okropekDockSide: state.okropekDockSide,
      }),
    },
  ),
)

export const useUI = useUIStore

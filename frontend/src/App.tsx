import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useMatch } from 'react-router-dom'
import { CommandPalette } from './components/CommandPalette'
import { ContentMachineTopBar } from './components/ContentMachineTopBar'
import { MobileTabBar } from './components/MobileTabBar'
import { OkropekCompanion } from './components/OkropekCompanion'
import { Sidebar } from './components/Sidebar'
import { SkipToContent } from './components/SkipToContent'
import { ToastSystem } from './components/ToastSystem'
import { useBootstrap } from './hooks/useBootstrap'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useNetworkStatus } from './hooks/useNetworkStatus'
import { useWebSocket } from './hooks/useWebSocket'

const ProductionWorkspace = lazy(() => import('./routes/ProductionWorkspace'))
const AssistantWorkspace = lazy(() => import('./routes/AssistantWorkspace'))
const ChatRoute = lazy(() => import('./routes/Chat'))
const StudioRoute = lazy(() => import('./routes/Studio'))
const IntelligenceRoute = lazy(() => import('./routes/Intelligence'))
const ChannelSetupRoute = lazy(() => import('./routes/ChannelSetup'))
const ProjectsCommandCenterRoute = lazy(() => import('./routes/ProjectsCommandCenter'))
const ProjectControlRoute = lazy(() => import('./routes/ProjectControl'))
const ContentMachineSettingsRoute = lazy(() => import('./routes/ContentMachineSettings'))
const SettingsHubRoute = lazy(() => import('./routes/SettingsHub'))

export default function App() {
  useBootstrap()
  useNetworkStatus()
  useWebSocket({ forceConnect: true, silentBackgroundRetry: true })
  const isMobileViewport = useMediaQuery('(max-width: 1023px)')
  const isStudio = Boolean(useMatch('/studio/:jobId'))

  return (
    <div
      data-atnos-ui-version="project-agent-v2"
      className="app-shell app-grain min-h-screen bg-void text-text-primary"
    >
      <SkipToContent />
      {!isMobileViewport ? <Sidebar /> : null}
      {!isMobileViewport ? <ContentMachineTopBar studio={isStudio} /> : null}
      <main
        id="main"
        className="min-h-screen"
        style={{
          paddingLeft: isMobileViewport ? 0 : 220,
          paddingTop: isMobileViewport ? 0 : 56,
          paddingBottom: isMobileViewport ? 64 : 0,
        }}
      >
        {isMobileViewport ? (
          <div className="shell-topbar px-4 py-3">
            <div className="font-display text-lg font-bold tracking-[0.18em]">ATNOS</div>
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-accent-primary">
              {isStudio ? 'Studio rolek' : 'Czat · Produkcja · Ustawienia'}
            </div>
          </div>
        ) : null}
        <Suspense fallback={<div role="status" className="p-6 text-sm text-text-secondary">Ładowanie widoku…</div>}>
        <Routes>
          <Route path="/" element={<Navigate to="/assistant" replace />} />
          <Route path="/dashboard" element={<Navigate to="/assistant" replace />} />
          <Route path="/assistant" element={<AssistantWorkspace />} />
          <Route path="/create" element={<ProductionWorkspace />} />
          <Route path="/chat/*" element={<ChatRoute />} />
          <Route path="/intelligence" element={<IntelligenceRoute />} />
          <Route path="/projects" element={<ProjectsCommandCenterRoute />} />
          <Route path="/channel-setup" element={<ChannelSetupRoute />} />
          <Route path="/projects/:projectId" element={<ProjectControlRoute />} />
          <Route path="/studio/:jobId" element={<StudioRoute />} />
          <Route path="/settings" element={<SettingsHubRoute />} />
          <Route path="/settings/production" element={<ContentMachineSettingsRoute />} />
          <Route path="/agent/*" element={<Navigate to="/chat" replace />} />
          <Route path="/pipeline/*" element={<Navigate to="/chat" replace />} />
          <Route path="/telemetry/*" element={<Navigate to="/chat" replace />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
        </Suspense>
      </main>
      {isMobileViewport ? <MobileTabBar /> : null}
      <OkropekCompanion />
      <CommandPalette />
      <ToastSystem />
    </div>
  )
}

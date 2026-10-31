import { useEffect, useMemo, useState } from 'react'
import { AgentActivityWindow } from '../components/AgentActivityWindow'
import { AgentStatusPanel } from '../components/AgentStatusPanel'
import { EmergencyStopButton } from '../components/EmergencyStopButton'
import { PendingApprovalsPanel } from '../components/PendingApprovalsPanel'
import { RecentArtifactsPanel } from '../components/RecentArtifactsPanel'
import { ToolCallsPanel } from '../components/ToolCallsPanel'
import { useBreakpoint } from '../hooks/useBreakpoint'
import {
  approveAgentAction,
  emergencyStopAgent,
  fetchAgentPendingApprovals,
  fetchAgentRecentLogs,
  fetchAgentStatus,
  fetchAgentTools,
  rejectAgentAction,
} from '../lib/api'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'
import type {
  AgentActionPreview,
  AgentEmergencyStopResult,
  AgentRecentLogsPayload,
  AgentStatusPayload,
  AgentToolDescriptor,
} from '../types/system'

function DetailCard({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail?: string | null
}) {
  return (
    <div className="shell-bento rounded-lg px-4 py-4">
      <div className="type-label text-text-tertiary">{label}</div>
      <div className="mt-2 type-body text-text-primary">{value}</div>
      {detail ? <div className="mt-2 type-body-sm text-text-secondary">{detail}</div> : null}
    </div>
  )
}

export default function AgentRoute() {
  const { isWide } = useBreakpoint()
  const pushToast = useToastStore((state) => state.push)
  const agentStage = useSystemStore((state) => state.agentStage)
  const agentActivity = useSystemStore((state) => state.agentActivity)
  const capabilities = useSystemStore((state) => state.capabilities)
  const executionRuns = useSystemStore((state) => state.executionRuns)
  const remoteWorkers = useSystemStore((state) => state.remoteWorkers)
  const trustedAutoSummary = useSystemStore((state) => state.trustedAutoSummary)
  const [agentStatus, setAgentStatus] = useState<AgentStatusPayload | null>(null)
  const [agentLogs, setAgentLogs] = useState<AgentRecentLogsPayload | null>(null)
  const [agentTools, setAgentTools] = useState<AgentToolDescriptor[]>([])
  const [pendingApprovals, setPendingApprovals] = useState<AgentActionPreview[]>([])
  const [panelLoading, setPanelLoading] = useState(false)
  const [panelError, setPanelError] = useState<string | null>(null)
  const [pendingApprovalRunId, setPendingApprovalRunId] = useState<number | null>(null)
  const [emergencyStopPending, setEmergencyStopPending] = useState(false)
  const [emergencyStopResult, setEmergencyStopResult] = useState<AgentEmergencyStopResult | null>(null)

  const activeRun = useMemo(
    () =>
      executionRuns.find((run) => run.status === 'running' || run.status === 'queued') ??
      executionRuns[0] ??
      null,
    [executionRuns],
  )
  const activeStep = useMemo(
    () =>
      activeRun?.steps.find((step) => step.status === 'running') ??
      activeRun?.steps[activeRun.steps.length - 1] ??
      null,
    [activeRun],
  )
  const remoteRun = useMemo(
    () =>
      remoteWorkers.flatMap((worker) => worker.recentRuns).find((run) => run.status === 'running') ??
      remoteWorkers.flatMap((worker) => worker.recentRuns)[0] ??
      null,
    [remoteWorkers],
  )
  const remoteWorker = useMemo(
    () =>
      remoteWorkers.find((worker) => worker.status === 'connected') ??
      remoteWorkers[0] ??
      null,
    [remoteWorkers],
  )
  const visibleApprovals = pendingApprovals.length ? pendingApprovals : agentStatus?.pendingApprovals ?? []

  const loadControlPanel = async () => {
    setPanelLoading(true)
    setPanelError(null)
    try {
      const [statusPayload, logsPayload, toolsPayload, approvalsPayload] = await Promise.all([
        fetchAgentStatus(),
        fetchAgentRecentLogs(20),
        fetchAgentTools(),
        fetchAgentPendingApprovals(),
      ])
      setAgentStatus(statusPayload)
      setAgentLogs(logsPayload)
      setAgentTools(toolsPayload)
      setPendingApprovals(approvalsPayload)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nieznany blad panelu agenta.'
      setPanelError(message)
    } finally {
      setPanelLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setPanelLoading(true)
      setPanelError(null)
      try {
        const [statusPayload, logsPayload, toolsPayload, approvalsPayload] = await Promise.all([
          fetchAgentStatus(),
          fetchAgentRecentLogs(20),
          fetchAgentTools(),
          fetchAgentPendingApprovals(),
        ])
        if (cancelled) return
        setAgentStatus(statusPayload)
        setAgentLogs(logsPayload)
        setAgentTools(toolsPayload)
        setPendingApprovals(approvalsPayload)
      } catch (error) {
        if (cancelled) return
        const message = error instanceof Error ? error.message : 'Nieznany blad panelu agenta.'
        setPanelError(message)
      } finally {
        if (!cancelled) {
          setPanelLoading(false)
        }
      }
    }
    void load()
    const interval = window.setInterval(() => {
      void load()
    }, 15000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

  const handleApproveAction = async (runId: number) => {
    setPendingApprovalRunId(runId)
    try {
      await approveAgentAction(runId)
      pushToast({ type: 'success', title: 'Action approved', message: 'Atnos moze kontynuowac zatwierdzony krok.' })
      await loadControlPanel()
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Approval failed',
        message: error instanceof Error ? error.message : 'Nie udalo sie zatwierdzic akcji.',
      })
    } finally {
      setPendingApprovalRunId(null)
    }
  }

  const handleRejectAction = async (runId: number) => {
    setPendingApprovalRunId(runId)
    try {
      await rejectAgentAction(runId, 'Rejected from Agent control panel.')
      pushToast({ type: 'success', title: 'Action rejected', message: 'Krok zostal zablokowany.' })
      await loadControlPanel()
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Reject failed',
        message: error instanceof Error ? error.message : 'Nie udalo sie odrzucic akcji.',
      })
    } finally {
      setPendingApprovalRunId(null)
    }
  }

  const handleEmergencyStop = async () => {
    setEmergencyStopPending(true)
    try {
      const result = await emergencyStopAgent()
      setEmergencyStopResult(result)
      pushToast({
        type: result.ubuntuStop.ok ? 'success' : 'warning',
        title: 'Emergency stop wykonany',
        message: result.message || 'Aktywne runy zostaly przerwane bez kasowania danych.',
      })
      await loadControlPanel()
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Emergency stop failed',
        message: error instanceof Error ? error.message : 'Nie udalo sie wykonac emergency stop.',
      })
    } finally {
      setEmergencyStopPending(false)
    }
  }

  return (
    <div className="shell-route scroll-chrome h-full overflow-y-auto px-4 py-6">
      <div className={`mx-auto grid max-w-[1480px] gap-6 ${isWide ? 'xl:grid-cols-[minmax(0,1fr)_320px]' : ''}`}>
        <div className="space-y-6">
          <section className="shell-hero p-6">
            <div className="type-label text-text-tertiary">AGENT</div>
            <h1 className="mt-2 type-h1 text-text-primary">Co teraz robi Atnos</h1>
            <p className="mt-4 type-body text-text-secondary">
              Ten widok ma odpowiedzieć po ludzku: jakie zadanie jest wykonywane, na czym stoi,
              gdzie jest wynik i czy potrzebna jest Twoja decyzja.
            </p>
          </section>

          <AgentActivityWindow
            agentStage={agentStage}
            agentActivity={agentActivity}
            activeRun={activeRun}
            activeStep={activeStep}
            browserRuntimeReady={Boolean(
              trustedAutoSummary?.browserRuntime?.ready || capabilities?.browserControlEnabled,
            )}
            remoteRun={remoteRun}
            remoteWorker={remoteWorker}
          />

          {panelError ? (
            <section className="rounded-lg border border-sem-error/40 bg-sem-error/10 p-4">
              <div className="type-label text-sem-error">PANEL ERROR</div>
              <div className="mt-2 type-body-sm text-text-secondary">{panelError}</div>
              <button
                type="button"
                onClick={() => void loadControlPanel()}
                className="mt-3 rounded-pill border border-border-default px-3 py-1.5 text-xs text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
              >
                Retry
              </button>
            </section>
          ) : null}

          <AgentStatusPanel status={agentStatus} tools={agentTools} loading={panelLoading} />
          <ToolCallsPanel logs={agentLogs} />
          <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
            <PendingApprovalsPanel
              approvals={visibleApprovals}
              pendingRunId={pendingApprovalRunId}
              onApprove={(runId) => void handleApproveAction(runId)}
              onReject={(runId) => void handleRejectAction(runId)}
            />
            <RecentArtifactsPanel artifacts={agentStatus?.recentArtifacts ?? []} />
          </div>
          <EmergencyStopButton
            pending={emergencyStopPending}
            result={emergencyStopResult}
            onClick={() => void handleEmergencyStop()}
          />
        </div>

        {isWide ? (
          <aside className="space-y-4">
            <DetailCard
              label="OBECNY STAN"
              value={agentStage.replaceAll('_', ' ')}
              detail={agentActivity?.detail ?? agentActivity?.title ?? 'Atnos czeka na kolejne polecenie.'}
            />
            <DetailCard
              label="AKTYWNE ZADANIE"
              value={activeRun?.goal ? 'W toku lub ostatnio wykonane' : 'Brak'}
              detail={activeRun?.goal ?? 'Nie ma teraz aktywnego zadania wykonawczego.'}
            />
            <DetailCard
              label="ZDALNY WORKER"
              value={remoteWorker?.label ?? 'Brak'}
              detail={
                remoteWorker
                  ? `${remoteWorker.host}:${remoteWorker.sshPort} · ${remoteWorker.status}`
                  : 'Atnos pracuje lokalnie na tym komputerze.'
              }
            />
            <DetailCard
              label="PRZEGLĄDARKA"
              value={capabilities?.browserControlEnabled ? 'Gotowa' : 'Wyłączona'}
              detail={
                trustedAutoSummary?.browserRuntime?.statusReason ??
                'Jeśli zadanie wymaga strony internetowej, Atnos może użyć runtime przeglądarki.'
              }
            />
          </aside>
        ) : null}
      </div>
    </div>
  )
}

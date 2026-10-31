import { useEffect } from 'react'
import { fetchBootstrap, fetchBootstrapShell } from '../lib/api'
import { useChatStore } from '../stores/chatStore'
import { usePipelineStore } from '../stores/pipelineStore'
import { useSystemStore } from '../stores/systemStore'
import { useToastStore } from '../stores/toastStore'

export function useBootstrap() {
  useEffect(() => {
    let active = true

    const hydrate = async () => {
      let shellReady = false
      try {
        const shell = await fetchBootstrapShell()
        if (!active) return
        useChatStore.getState().setBootstrap({
          uiVersion: shell.uiVersion,
          projects: shell.projects,
          conversations: shell.recentConversations,
        })
        shellReady = true
      } catch {
        shellReady = false
      }

      try {
        const payload = await fetchBootstrap()
        if (!active) return
        const trustedAutoSummary = payload.trustedAutoSummary ?? {
          runtimeControls: {
            autonomyMode: 'blocked',
            schedulerEnabled: false,
            emergencyStopEnabled: false,
            pauseAllJobs: false,
            loginAutomationEnabled: false,
            twoFactorAutomationEnabled: false,
            publishAutomationEnabled: false,
            defaultRetryBudget: 2,
            defaultVramFloorGb: 4,
          },
          accounts: [],
          trustGrants: [],
          schedulerJobs: [],
          executionRuns: [],
          mediaPipelinePresets: [],
          hardwareSnapshot: null,
          browserRuntime: null,
          youtubeConnector: null,
          contentReadiness: null,
        }
        useChatStore.getState().setBootstrap({
          uiVersion: payload.uiVersion,
          projects: payload.projects,
          conversations: payload.recentConversations,
        })
        useSystemStore.getState().setRuntimeSummary(payload.runtimeSummary)
        useSystemStore.getState().setProviderCards(payload.providerCards)
        useSystemStore.getState().setCapabilities(payload.capabilities)
        useSystemStore.getState().setAccessReality(payload.accessReality)
        useSystemStore.getState().setTrustedAutoSummary(trustedAutoSummary)
        useSystemStore.getState().setAmbientState(payload.ambientState)
        useSystemStore.getState().setRemoteWorkers(payload.remoteWorkers)
        const hardwareSnapshot = trustedAutoSummary.hardwareSnapshot
        if (hardwareSnapshot) {
          const nextMetrics: {
            vramUsedMB?: number
            vramTotalMB?: number
          } = {}
          if (hardwareSnapshot.usedVramMb != null) {
            nextMetrics.vramUsedMB = hardwareSnapshot.usedVramMb
          }
          if (hardwareSnapshot.totalVramMb != null) {
            nextMetrics.vramTotalMB = hardwareSnapshot.totalVramMb
          }
          if (Object.keys(nextMetrics).length > 0) {
            useSystemStore.getState().setMetrics(nextMetrics)
          }
        }
        useSystemStore
          .getState()
          .setRemoteExecutionRuns(
            payload.remoteWorkers.flatMap((worker) => worker.recentRuns),
          )
        usePipelineStore.getState().setSnapshot(payload.pipeline)
      } catch (error) {
        if (!active) return
        useToastStore.getState().push({
          type: shellReady ? 'warning' : 'error',
          title: shellReady ? 'Status systemu opozniony' : 'Bootstrap nieudany',
          message:
            error instanceof Error
              ? error.message
              : 'Nie udalo sie pobrac pelnego statusu Atnosa.',
        })
      }
    }

    void hydrate()

    return () => {
      active = false
    }
  }, [])
}

import type { AgentStatusPayload, AgentToolDescriptor } from '../types/system'

interface AgentStatusPanelProps {
  status: AgentStatusPayload | null
  tools: AgentToolDescriptor[]
  loading?: boolean
}

function readinessLabel(value: unknown) {
  return value ? 'ready' : 'not ready'
}

function textValue(value: unknown, fallback = 'n/a') {
  if (value == null || value === '') return fallback
  return String(value)
}

function StatusTile({
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

export function AgentStatusPanel({ status, tools, loading = false }: AgentStatusPanelProps) {
  const agent = status?.agent
  const ubuntu = status?.ubuntu ?? {}
  const browser = status?.browser ?? {}
  const shortPipeline = status?.shortPipeline ?? {}
  const youtubeAutomation = status?.youtubeAutomation ?? {}
  const monetizationFactory = status?.monetizationFactory
  const qualityRuntime = status?.qualityRuntime

  return (
    <section className="shell-panel-strong rounded-lg p-5" data-testid="agent-status-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="type-label text-text-tertiary">CONTROL PANEL</div>
          <h2 className="mt-2 type-h3 text-text-primary">Stan agenta i runtime</h2>
          <p className="mt-2 type-body-sm text-text-secondary">
            Krótki podgląd tego, co Atnos może wykonać teraz i gdzie zapisuje ślady pracy.
          </p>
        </div>
        <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-secondary">
          {loading ? 'refreshing' : textValue(agent?.state, 'unknown')}
        </span>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-7">
        <StatusTile
          label="AGENT"
          value={textValue(agent?.state, 'unknown')}
          detail={`${agent?.activeTasks ?? 0} active / ${agent?.pendingApprovals ?? 0} approvals / ${agent?.errors ?? 0} errors`}
        />
        <StatusTile
          label="UBUNTU SSH"
          value={readinessLabel(ubuntu.ready)}
          detail={`${textValue(ubuntu.user)}@${textValue(ubuntu.host)} | ${textValue(ubuntu.workdir)}`}
        />
        <StatusTile
          label="BROWSER"
          value={readinessLabel(browser.ready)}
          detail={textValue(browser.status_reason)}
        />
        <StatusTile
          label="SHORT PIPELINE"
          value={readinessLabel(shortPipeline.ready)}
          detail={textValue(shortPipeline.reason)}
        />
        <StatusTile
          label="YOUTUBE OPS"
          value={textValue(
            youtubeAutomation.n8n_docker_status ?? youtubeAutomation.docker_status,
            readinessLabel(youtubeAutomation.ready),
          )}
          detail={`${Number(youtubeAutomation.pending_approvals ?? 0)} pending / ${Number(
            youtubeAutomation.approved_approvals ?? 0,
          )} approved · fallback ${textValue(youtubeAutomation.fallback_runner_status, 'unknown')}`}
        />
        <StatusTile
          label="QUALITY GATE"
          value={qualityRuntime?.latestRun?.status ?? readinessLabel(qualityRuntime?.ready)}
          detail={
            qualityRuntime?.latestRun
              ? `${qualityRuntime.latestRun.score ?? 'n/a'}/10 | ${qualityRuntime.latestRun.area}`
              : qualityRuntime?.statusReason ?? 'No quality run recorded yet.'
          }
        />
        <StatusTile
          label="SHORTS FACTORY"
          value={monetizationFactory?.status ?? 'not_initialized'}
          detail={`${monetizationFactory?.youtubeReadinessStatus ?? 'youtube_unknown'} / ${
            monetizationFactory?.privateUploadOnly ? 'private-only' : 'policy-check'
          }`}
        />
      </div>

      {monetizationFactory ? (
        <div className="mt-5 rounded-lg border border-border-default bg-elevated/60 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="type-label text-text-tertiary">MONETIZATION CORE</div>
              <div className="mt-2 type-body text-text-primary">Automated Shorts Factory</div>
              <p className="mt-2 type-body-sm text-text-secondary">
                {monetizationFactory.statusReason ?? 'Safe local/private-ready Shorts workflow status.'}
              </p>
            </div>
            <span className="rounded-pill border border-accent-primary/35 px-3 py-1 type-mono-sm text-accent-primary">
              {monetizationFactory.revenueVector}
            </span>
          </div>
          <div className="mt-4 grid gap-2 md:grid-cols-4">
            <div className="rounded-md border border-border-default bg-panel/50 px-3 py-2">
              <div className="type-label text-text-tertiary">STATE MACHINE</div>
              <div className="mt-1 type-mono-sm text-text-secondary">
                {readinessLabel(monetizationFactory.stateMachineReady)}
              </div>
            </div>
            <div className="rounded-md border border-border-default bg-panel/50 px-3 py-2">
              <div className="type-label text-text-tertiary">CHANNEL OPS</div>
              <div className="mt-1 type-mono-sm text-text-secondary">
                {readinessLabel(monetizationFactory.channelOpsReady)}
              </div>
            </div>
            <div className="rounded-md border border-border-default bg-panel/50 px-3 py-2">
              <div className="type-label text-text-tertiary">ARTIFACT CONTRACT</div>
              <div className="mt-1 type-mono-sm text-text-secondary">
                {readinessLabel(monetizationFactory.shortsContractReady)}
              </div>
            </div>
            <div className="rounded-md border border-border-default bg-panel/50 px-3 py-2">
              <div className="type-label text-text-tertiary">APPROVAL GATE</div>
              <div className="mt-1 type-mono-sm text-text-secondary">
                {readinessLabel(monetizationFactory.approvalGateReady)}
              </div>
            </div>
          </div>
          <div className="mt-4 rounded-md border border-border-default bg-panel/50 px-3 py-2">
            <div className="type-label text-text-tertiary">REQUIRED PRE-RENDER ARTIFACTS</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {monetizationFactory.requiredArtifacts.length ? (
                monetizationFactory.requiredArtifacts.map((artifact) => (
                  <span
                    key={artifact}
                    className="rounded-pill border border-border-default px-2.5 py-1 type-mono-sm text-text-secondary"
                  >
                    {artifact}
                  </span>
                ))
              ) : (
                <span className="type-body-sm text-text-tertiary">No artifact contract loaded.</span>
              )}
            </div>
          </div>
          <div className="mt-3 break-all type-mono-sm text-text-tertiary">
            {monetizationFactory.statusPath ?? monetizationFactory.logPath ?? 'No status file path reported.'}
          </div>
        </div>
      ) : null}

      <div className="mt-5 grid gap-3 lg:grid-cols-[1fr_1fr]">
        <div className="shell-bento rounded-lg p-4">
          <div className="type-label text-text-tertiary">TOOLS</div>
          <div className="mt-3 space-y-2">
            {tools.length ? (
              tools.map((tool) => (
                <div
                  key={tool.key}
                  className="flex items-start justify-between gap-3 rounded-md border border-border-default bg-elevated/60 px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="type-body-sm text-text-primary">{tool.label}</div>
                    <div className="mt-1 truncate type-mono-sm text-text-tertiary">{tool.logPath}</div>
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="type-mono-sm text-text-secondary">{tool.status}</div>
                    <div className="mt-1 text-[10px] uppercase tracking-[0.18em] text-text-tertiary">
                      {tool.riskLevel}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="type-body-sm text-text-tertiary">Brak danych o narzędziach.</div>
            )}
          </div>
        </div>

        <div className="shell-bento rounded-lg p-4">
          <div className="type-label text-text-tertiary">LOG LOCATIONS</div>
          <div className="mt-3 space-y-2">
            {status?.logs && Object.keys(status.logs).length ? (
              Object.entries(status.logs).map(([key, value]) => (
                <div key={key} className="rounded-md border border-border-default bg-elevated/60 px-3 py-2">
                  <div className="type-mono-sm text-text-secondary">{key}</div>
                  <div className="mt-1 break-all text-[11px] text-text-tertiary">{value}</div>
                </div>
              ))
            ) : (
              <div className="type-body-sm text-text-tertiary">Ścieżki logów pojawią się po odczycie backendu.</div>
            )}
          </div>
        </div>
      </div>

      {qualityRuntime?.latestRun ? (
        <div className="mt-5 rounded-lg border border-border-default bg-elevated/60 p-4">
          <div className="type-label text-text-tertiary">LATEST QUALITY RUN</div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="rounded-pill border border-accent-primary/35 px-3 py-1 type-mono-sm text-accent-primary">
              {qualityRuntime.latestRun.status}
            </span>
            <span className="type-body-sm text-text-primary">{qualityRuntime.latestRun.task}</span>
          </div>
          <div className="mt-2 break-all text-[11px] text-text-tertiary">
            {qualityRuntime.latestRun.reportPath ?? qualityRuntime.runsDir}
          </div>
        </div>
      ) : null}
    </section>
  )
}

import { useMemo } from 'react'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { useSystemStore } from '../stores/systemStore'

function MetricPanel({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail?: string
}) {
  return (
    <div className="shell-bento rounded-lg px-4 py-4">
      <div className="type-label text-text-tertiary">{label}</div>
      <div className="mt-2 type-mono-sm text-text-primary">{value}</div>
      {detail ? <div className="mt-2 type-body-sm text-text-secondary">{detail}</div> : null}
    </div>
  )
}

function formatGigabytesFromMb(valueMb: number | null | undefined) {
  if (valueMb == null) {
    return 'n/a'
  }
  return `${(valueMb / 1024).toFixed(1)} GB`
}

function formatPercent(value: number | null | undefined) {
  if (value == null) {
    return 'n/a'
  }
  return `${value.toFixed(1)}%`
}

export default function TelemetryRoute() {
  const { isWide } = useBreakpoint()
  const runtimeSummary = useSystemStore((state) => state.runtimeSummary)
  const providerCards = useSystemStore((state) => state.providerCards)
  const network = useSystemStore((state) => state.network)
  const chroma = useSystemStore((state) => state.chroma)
  const model = useSystemStore((state) => state.model)
  const vramUsedMB = useSystemStore((state) => state.vramUsedMB)
  const vramTotalMB = useSystemStore((state) => state.vramTotalMB)
  const accessReality = useSystemStore((state) => state.accessReality)
  const accessRealityChanges = useSystemStore((state) => state.accessRealityChanges)
  const remoteWorkers = useSystemStore((state) => state.remoteWorkers)
  const hardwareSnapshot = useSystemStore((state) => state.hardwareSnapshot)

  const providerReady = useMemo(
    () => providerCards.filter((card) => card.readinessStatus === 'ready').length,
    [providerCards],
  )
  const ramUsedMb =
    hardwareSnapshot?.usedRamMb ??
    (hardwareSnapshot?.totalRamMb != null && hardwareSnapshot?.freeRamMb != null
      ? Math.max(0, hardwareSnapshot.totalRamMb - hardwareSnapshot.freeRamMb)
      : null)
  const ramTotalMb = hardwareSnapshot?.totalRamMb ?? null
  const gpuLabel = hardwareSnapshot?.gpuName ?? 'GPU hosta'

  return (
    <div className="shell-route scroll-chrome h-full overflow-y-auto px-4 py-6">
      <div
        className={`mx-auto grid max-w-[1480px] gap-6 ${
          isWide ? 'xl:grid-cols-[minmax(0,1fr)_320px]' : ''
        }`}
      >
        <div className="space-y-6">
          <section className="shell-hero p-6">
            <div className="type-label text-text-tertiary">TELEMETRIA</div>
            <h1 className="mt-2 type-h1 text-text-primary">Stan runtime i hosta</h1>
            <p className="mt-4 type-body text-text-secondary">
              Tutaj widac tylko realny stan modelu, runtime, zasobow PC i ostatnich zmian
              dostepu. Bez marketingu i bez debugowego smietnika.
            </p>
          </section>

          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <MetricPanel label="MODEL" value={model.name} detail={model.provider} />
            <MetricPanel label="NETWORK" value={network} detail={`Chroma: ${chroma}`} />
            <MetricPanel
              label="CPU"
              value={formatPercent(hardwareSnapshot?.cpuPercent)}
              detail={hardwareSnapshot ? 'Live odczyt hosta lokalnego' : 'Brak snapshotu hosta'}
            />
            <MetricPanel
              label="RAM"
              value={
                ramUsedMb != null && ramTotalMb != null
                  ? `${formatGigabytesFromMb(ramUsedMb)} / ${formatGigabytesFromMb(ramTotalMb)}`
                  : 'n/a'
              }
              detail={
                hardwareSnapshot?.freeRamMb != null
                  ? `${formatGigabytesFromMb(hardwareSnapshot.freeRamMb)} wolne`
                  : 'Brak live odczytu RAM'
              }
            />
            <MetricPanel
              label="VRAM"
              value={
                vramTotalMB > 0
                  ? `${formatGigabytesFromMb(vramUsedMB)} / ${formatGigabytesFromMb(vramTotalMB)}`
                  : formatGigabytesFromMb(vramUsedMB)
              }
              detail={hardwareSnapshot?.gpuName ?? hardwareSnapshot?.status ?? 'Brak snapshotu GPU'}
            />
            <MetricPanel
              label="PROVIDERS READY"
              value={String(providerReady)}
              detail={
                runtimeSummary
                  ? `${runtimeSummary.enabledModels} modeli, ${runtimeSummary.tasksReady} gotowych zadan`
                  : 'Brak runtime summary'
              }
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
            <div className="shell-bento rounded-lg p-6">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="type-label text-text-tertiary">PROVIDER READINESS</div>
                  <div className="mt-2 type-h3 text-text-primary">Dostawcy i routing</div>
                </div>
                <div className="type-mono-sm text-text-tertiary">{providerCards.length} wpisow</div>
              </div>
              <div className="mt-4 space-y-3">
                {providerCards.map((card) => (
                  <div
                    key={card.providerKey}
                    className="shell-bento flex items-start justify-between gap-4 rounded-lg px-4 py-4"
                  >
                    <div>
                      <div className="type-body text-text-primary">{card.displayName}</div>
                      <div className="mt-1 type-mono-sm text-text-tertiary">
                        {card.providerKey} {' | '} {card.runtimeHealthStatus}
                      </div>
                      <div className="mt-2 type-body-sm text-text-secondary">
                        {card.readinessReason}
                      </div>
                    </div>
                    <span className="rounded-pill border border-border-default px-3 py-1 type-mono-sm text-text-secondary">
                      {card.readinessStatus}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="shell-bento rounded-lg p-6">
              <div className="type-label text-text-tertiary">HOST SNAPSHOT</div>
              <div className="mt-2 type-h3 text-text-primary">PC i ostatnie zmiany</div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <MetricPanel
                  label="GPU"
                  value={gpuLabel}
                  detail={hardwareSnapshot?.status ?? 'unknown'}
                />
                <MetricPanel
                  label="DYSK"
                  value={
                    hardwareSnapshot?.freeDiskGb != null
                      ? `${hardwareSnapshot.freeDiskGb.toFixed(1)} GB free`
                      : 'n/a'
                  }
                  detail={hardwareSnapshot?.diskPath ?? 'Brak sciezki'}
                />
              </div>
              {accessReality ? (
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <MetricPanel label="LIVE READ" value={String(accessReality.summary.liveRead)} />
                  <MetricPanel label="LIVE WRITE" value={String(accessReality.summary.liveWrite)} />
                  <MetricPanel label="BLOCKED" value={String(accessReality.summary.blocked)} />
                  <MetricPanel
                    label="UNAVAILABLE"
                    value={String(accessReality.summary.unavailable)}
                  />
                </div>
              ) : (
                <div className="mt-4 type-body-sm text-text-secondary">
                  Brak snapshotu access reality.
                </div>
              )}
              <div className="mt-4 space-y-3">
                {accessRealityChanges.length ? (
                  accessRealityChanges.slice(0, 6).map((change) => (
                    <div
                      key={`${change.subjectKey}-${change.toAccessLevel}`}
                      className="shell-bento rounded-lg px-4 py-4"
                    >
                      <div className="type-body text-text-primary">{change.title}</div>
                      <div className="mt-1 type-mono-sm text-text-tertiary">
                        {change.subjectKey} {' | '} {change.fromAccessLevel} {' -> '}{' '}
                        {change.toAccessLevel}
                      </div>
                      <div className="mt-2 type-body-sm text-text-secondary">
                        {change.detail}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="shell-bento rounded-lg px-4 py-4 type-body-sm text-text-secondary">
                    Brak materialnych zmian od ostatniego snapshotu.
                  </div>
                )}
              </div>
            </div>
          </section>
        </div>

        {isWide ? (
          <aside className="space-y-4">
            <MetricPanel
              label="REMOTE"
              value={`${remoteWorkers.length} wezlow`}
              detail="Skrocony stan warstwy wykonawczej i hosta lokalnego."
            />
            <MetricPanel
              label="HOST"
              value={hardwareSnapshot?.status ?? 'unknown'}
              detail={
                hardwareSnapshot?.notes ??
                (hardwareSnapshot?.freeDiskGb != null
                  ? `${hardwareSnapshot.freeDiskGb.toFixed(1)} GB wolnego miejsca`
                  : 'Brak dodatkowych notatek sprzetowych')
              }
            />
            {remoteWorkers.slice(0, 3).map((worker) => (
              <MetricPanel
                key={worker.nodeId}
                label={worker.label}
                value={`${worker.host}:${worker.sshPort}`}
                detail={`${worker.status} | browser=${
                  worker.capabilitySnapshot?.browserReady ? 'ready' : 'down'
                } | ollama=${worker.capabilitySnapshot?.ollamaReady ? 'ready' : 'down'}`}
              />
            ))}
          </aside>
        ) : null}
      </div>
    </div>
  )
}

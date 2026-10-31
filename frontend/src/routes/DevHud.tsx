import { type ReactNode, useState } from 'react'
import { StatusHUDView, type StatusHUDProps } from '../components/StatusHUD'
import type { AgentStage } from '../types/agent'

const STAGE_OPTIONS: AgentStage[] = [
  'idle',
  'thinking',
  'executing',
  'awaiting_human',
  'error',
  'offline',
]

const CHROMA_OPTIONS: StatusHUDProps['chroma'][] = ['connected', 'reconnecting', 'down']
const NETWORK_OPTIONS: StatusHUDProps['network'][] = ['online', 'degraded', 'offline']
const ACCESS_OPTIONS: NonNullable<StatusHUDProps['accessStatus']>[] = ['active', 'limited', 'syncing']
const PROVIDER_OPTIONS: StatusHUDProps['model']['provider'][] = [
  'ollama',
  'openai',
  'anthropic',
  'gemini',
]

function formatGb(valueMB: number) {
  return new Intl.NumberFormat('pl-PL', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(valueMB / 1024)
}

function DevHudField({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <label className="space-y-2">
      <span className="label-eyebrow">{label}</span>
      {children}
    </label>
  )
}

export default function DevHudRoute() {
  const [agentStage, setAgentStage] = useState<AgentStage>('idle')
  const [modelName, setModelName] = useState('llama3:8b')
  const [provider, setProvider] = useState<StatusHUDProps['model']['provider']>('ollama')
  const [vramUsedMB, setVramUsedMB] = useState(8192)
  const [chroma, setChroma] = useState<StatusHUDProps['chroma']>('connected')
  const [network, setNetwork] = useState<StatusHUDProps['network']>('online')
  const [accessStatus, setAccessStatus] = useState<NonNullable<StatusHUDProps['accessStatus']>>('active')
  const [showRtt, setShowRtt] = useState(true)
  const [rttMs, setRttMs] = useState(42)

  const effectiveRtt = showRtt ? rttMs : null

  return (
    <>
      <StatusHUDView
        agentStage={agentStage}
        model={{ name: modelName, provider }}
        vramUsedMB={vramUsedMB}
        vramTotalMB={16384}
        chroma={chroma}
        network={network}
        rttMs={effectiveRtt}
        accessStatus={accessStatus}
        accessDetail={
          accessStatus === 'active'
            ? 'Atnos startuje z pelnym profilem operatora.'
            : accessStatus === 'limited'
              ? 'Brakuje jednej lub wiecej krytycznych zgod runtime.'
              : 'Bootstrap jeszcze synchronizuje runtime.'
        }
      />

      <section className="mx-auto flex min-h-[calc(100vh-56px)] w-full max-w-[1480px] flex-col px-6 py-24 lg:px-10">
        <header className="mb-8 space-y-3">
          <p className="label-eyebrow">DEV SURFACE</p>
          <h1 className="type-h1 text-text-primary">StatusHUD QA</h1>
          <p className="type-body max-w-3xl text-text-secondary">
            Ta powierzchnia pozwala sprawdzić wszystkie stany HUD-a bez backendu.
            Próg ostrzegawczy zaczyna się przy 10 GB, a krytyczny przy 14 GB.
          </p>
        </header>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,720px)_320px]">
          <div className="rounded-lg border border-border-default bg-panel p-6">
            <div className="grid gap-6 md:grid-cols-2">
              <DevHudField label="STAN AGENTA">
                <select
                  value={agentStage}
                  onChange={(event) => setAgentStage(event.target.value as AgentStage)}
                  className="h-11 w-full rounded-md border border-border-default bg-shell px-4 type-body-sm text-text-primary outline-none transition duration-fast ease-standard focus:border-border-strong"
                >
                  {STAGE_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </DevHudField>

              <DevHudField label="PROVIDER">
                <select
                  value={provider}
                  onChange={(event) =>
                    setProvider(event.target.value as StatusHUDProps['model']['provider'])
                  }
                  className="h-11 w-full rounded-md border border-border-default bg-shell px-4 type-body-sm text-text-primary outline-none transition duration-fast ease-standard focus:border-border-strong"
                >
                  {PROVIDER_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </DevHudField>

              <DevHudField label="ACCESS">
                <select
                  value={accessStatus}
                  onChange={(event) =>
                    setAccessStatus(event.target.value as NonNullable<StatusHUDProps['accessStatus']>)
                  }
                  className="h-11 w-full rounded-md border border-border-default bg-shell px-4 type-body-sm text-text-primary outline-none transition duration-fast ease-standard focus:border-border-strong"
                >
                  {ACCESS_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </DevHudField>

              <DevHudField label="MODEL">
                <input
                  value={modelName}
                  onChange={(event) => setModelName(event.target.value)}
                  className="h-11 w-full rounded-md border border-border-default bg-shell px-4 type-mono-sm text-text-primary outline-none transition duration-fast ease-standard focus:border-border-strong"
                />
              </DevHudField>

              <DevHudField label="RTT">
                <div className="space-y-3">
                  <label className="flex items-center gap-3 type-body-sm text-text-secondary">
                    <input
                      type="checkbox"
                      checked={showRtt}
                      onChange={(event) => setShowRtt(event.target.checked)}
                      className="h-4 w-4 rounded border-border-default bg-shell"
                    />
                    Pokaż tooltip RTT
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={300}
                    step={1}
                    value={rttMs}
                    onChange={(event) => setRttMs(Number(event.target.value))}
                    className="w-full accent-[var(--accent-primary)]"
                    disabled={!showRtt}
                  />
                  <p className="type-mono-sm text-text-tertiary">
                    {showRtt ? `${rttMs} ms` : 'RTT ukryte'}
                  </p>
                </div>
              </DevHudField>

              <DevHudField label="VRAM">
                <div className="space-y-3">
                  <input
                    type="range"
                    min={0}
                    max={16384}
                    step={128}
                    value={vramUsedMB}
                    onChange={(event) => setVramUsedMB(Number(event.target.value))}
                    className="w-full accent-[var(--accent-primary)]"
                  />
                  <div className="flex items-center justify-between gap-3">
                    <input
                      type="number"
                      min={0}
                      max={16384}
                      step={128}
                      value={vramUsedMB}
                      onChange={(event) => setVramUsedMB(Number(event.target.value))}
                      className="h-11 w-32 rounded-md border border-border-default bg-shell px-4 type-mono-sm text-text-primary outline-none transition duration-fast ease-standard focus:border-border-strong"
                    />
                    <p className="type-mono-sm text-text-tertiary">
                      {formatGb(vramUsedMB)} / 16,0 GB
                    </p>
                  </div>
                </div>
              </DevHudField>

              <DevHudField label="CHROMA">
                <select
                  value={chroma}
                  onChange={(event) =>
                    setChroma(event.target.value as StatusHUDProps['chroma'])
                  }
                  className="h-11 w-full rounded-md border border-border-default bg-shell px-4 type-body-sm text-text-primary outline-none transition duration-fast ease-standard focus:border-border-strong"
                >
                  {CHROMA_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </DevHudField>

              <DevHudField label="NETWORK">
                <select
                  value={network}
                  onChange={(event) =>
                    setNetwork(event.target.value as StatusHUDProps['network'])
                  }
                  className="h-11 w-full rounded-md border border-border-default bg-shell px-4 type-body-sm text-text-primary outline-none transition duration-fast ease-standard focus:border-border-strong"
                >
                  {NETWORK_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </DevHudField>
            </div>
          </div>

          <aside className="space-y-6">
            <div className="rounded-lg border border-border-default bg-panel p-6">
              <p className="label-eyebrow">PRESETY QA</p>
              <div className="mt-4 grid gap-3">
                <button
                  type="button"
                  onClick={() => setVramUsedMB(8192)}
                  className="rounded-md border border-border-default bg-shell px-4 py-3 text-left type-body-sm text-text-primary transition duration-fast ease-standard hover:bg-elevated"
                >
                  VRAM niskie: 8,0 GB
                </button>
                <button
                  type="button"
                  onClick={() => setVramUsedMB(12288)}
                  className="rounded-md border border-border-default bg-shell px-4 py-3 text-left type-body-sm text-text-primary transition duration-fast ease-standard hover:bg-elevated"
                >
                  VRAM ostrzegawcze: 12,0 GB
                </button>
                <button
                  type="button"
                  onClick={() => setVramUsedMB(15360)}
                  className="rounded-md border border-border-default bg-shell px-4 py-3 text-left type-body-sm text-text-primary transition duration-fast ease-standard hover:bg-elevated"
                >
                  VRAM krytyczne: 15,0 GB
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAgentStage('thinking')
                    setChroma('reconnecting')
                    setNetwork('degraded')
                    setShowRtt(true)
                    setRttMs(148)
                  }}
                  className="rounded-md border border-border-default bg-shell px-4 py-3 text-left type-body-sm text-text-primary transition duration-fast ease-standard hover:bg-elevated"
                >
                  Symuluj stan pośredni
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAgentStage('awaiting_human')
                    setVramUsedMB(9216)
                    setChroma('connected')
                    setNetwork('online')
                    setShowRtt(true)
                    setRttMs(36)
                  }}
                  className="rounded-md border border-border-default bg-shell px-4 py-3 text-left type-body-sm text-text-primary transition duration-fast ease-standard hover:bg-elevated"
                >
                  Symuluj oczekiwanie na człowieka
                </button>
              </div>
            </div>

            <div className="rounded-lg border border-border-default bg-panel p-6">
              <p className="label-eyebrow">AKTUALNY ODCZYT</p>
              <div className="mt-4 space-y-2">
                <p className="type-body-sm text-text-secondary">
                  VRAM: <span className="type-mono-sm text-text-primary">{vramUsedMB} MB</span>
                </p>
                <p className="type-body-sm text-text-secondary">
                  Próg 10 GB:{' '}
                  <span className="type-mono-sm text-text-primary">
                    {vramUsedMB >= 10240 ? 'przekroczony' : 'nie'}
                  </span>
                </p>
                <p className="type-body-sm text-text-secondary">
                  Próg 14 GB:{' '}
                  <span className="type-mono-sm text-text-primary">
                    {vramUsedMB > 14336 ? 'krytyczny' : 'nie'}
                  </span>
                </p>
              </div>
            </div>
          </aside>
        </div>
      </section>
    </>
  )
}

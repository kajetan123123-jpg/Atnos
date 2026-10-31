import type { AtnosCoreVisualState } from '../types/agent'
import { AtnosCore } from '../components/AtnosCore'

const CORE_STATES: Array<{
  state: AtnosCoreVisualState
  label: string
}> = [
  { state: 'idle', label: 'Bezczynny' },
  { state: 'thinking', label: 'Myśli' },
  { state: 'executing', label: 'Wykonuje' },
  { state: 'error', label: 'Błąd' },
  { state: 'success', label: 'Sukces' },
  { state: 'offline', label: 'Offline' },
]

export default function DevCoreRoute() {
  return (
    <section className="mx-auto flex min-h-[calc(100vh-56px)] w-full max-w-[1680px] flex-col px-8 py-10">
      <header className="mb-8 space-y-3">
        <p className="label-eyebrow">DEV SURFACE</p>
        <h1 className="type-h1 text-text-primary">AtnosCore verification</h1>
        <p className="type-body max-w-3xl text-text-secondary">
          Six canonical AtnosCore states rendered side-by-side for motion and color
          verification. Toggle reduced motion at the OS level to confirm that all
          ring rotations halt while state colors remain intact.
        </p>
      </header>
      <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
        {CORE_STATES.map((item) => (
          <div
            key={item.state}
            className="rounded-lg border border-border-default bg-panel px-6 py-6"
          >
            <div className="mb-6 flex items-center justify-between gap-4">
              <div>
                <p className="label-eyebrow">STATE</p>
                <h2 className="type-h3 text-text-primary">{item.label}</h2>
              </div>
              <span className="type-mono-sm text-text-tertiary">{item.state}</span>
            </div>
            <div className="flex justify-center">
              <AtnosCore state={item.state} size={96} />
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

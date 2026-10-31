import {
  Bot,
  Braces,
  Clapperboard,
  ExternalLink,
  FileStack,
  Globe,
  Image as ImageIcon,
  MonitorPlay,
} from 'lucide-react'
import { buildAgentArtifactDownloadUrl } from '../lib/api'
import type { AgentStage } from '../types/agent'
import type {
  AgentActivityState,
  RemoteExecutionRun,
  RemoteWorkerSummary,
  TrustedAutoExecutionRun,
  TrustedAutoExecutionStep,
} from '../types/system'

type ActivityScope = 'code' | 'graphics' | 'video' | 'browser' | 'files'

interface AgentActivityWindowProps {
  agentStage: AgentStage
  agentActivity: AgentActivityState | null
  activeRun: TrustedAutoExecutionRun | null
  activeStep: TrustedAutoExecutionStep | null
  browserRuntimeReady: boolean
  remoteRun?: RemoteExecutionRun | null
  remoteWorker?: RemoteWorkerSummary | null
  onPause?: (() => void) | undefined
  onInterrupt?: (() => void) | undefined
  controlsDisabled?: boolean
}

interface PreviewCandidate {
  kind: 'image' | 'video'
  label: string
  storagePath: string
}

const STAGE_META: Record<
  AgentStage,
  { label: string; tone: string; dot: string; fallbackTitle: string; fallbackDetail: string }
> = {
  idle: {
    label: 'CZUWA',
    tone: 'border-border-default text-text-secondary',
    dot: 'bg-text-tertiary',
    fallbackTitle: 'ATNOS czeka na kolejne zadanie',
    fallbackDetail:
      'Okno pozostaje gotowe do podglądu kolejnych akcji, renderów i pracy w przeglądarce.',
  },
  thinking: {
    label: 'ANALIZUJE',
    tone: 'border-accent-secondary/40 text-accent-secondary',
    dot: 'bg-accent-secondary',
    fallbackTitle: 'ATNOS analizuje kontekst',
    fallbackDetail: 'Porządkuje kontekst rozmowy i ustawia najlepszy tor wykonania.',
  },
  executing: {
    label: 'WYKONUJE',
    tone: 'border-accent-primary/40 text-accent-primary',
    dot: 'bg-accent-primary',
    fallbackTitle: 'ATNOS wykonuje workflow',
    fallbackDetail: 'Na żywo widzisz bieżący krok, podgląd artefaktu i ostatnie operacje agenta.',
  },
  awaiting_human: {
    label: 'CZEKA NA CIEBIE',
    tone: 'border-warning/40 text-warning',
    dot: 'bg-warning',
    fallbackTitle: 'ATNOS zatrzymał się na decyzji',
    fallbackDetail: 'Kolejny krok wymaga jawnej akceptacji człowieka.',
  },
  error: {
    label: 'BLAD',
    tone: 'border-error/40 text-error',
    dot: 'bg-error',
    fallbackTitle: 'ATNOS zatrzymał się na błędzie',
    fallbackDetail: 'Sprawdź ostatni krok i problem techniczny, zanim workflow ruszy dalej.',
  },
  offline: {
    label: 'OFFLINE',
    tone: 'border-border-default text-text-tertiary',
    dot: 'bg-text-disabled',
    fallbackTitle: 'ATNOS jest offline',
    fallbackDetail: 'Brakuje aktywnego połączenia z runtime albo websocketem.',
  },
}

const SCOPE_META: Record<ActivityScope, { label: string; icon: typeof Bot }> = {
  code: { label: 'Kodowanie', icon: Braces },
  graphics: { label: 'Grafika', icon: ImageIcon },
  video: { label: 'Wideo', icon: Clapperboard },
  browser: { label: 'Przeglądarka', icon: Globe },
  files: { label: 'Pliki', icon: FileStack },
}

function inferScopes(
  agentActivity: AgentActivityState | null,
  activeRun: TrustedAutoExecutionRun | null,
  activeStep: TrustedAutoExecutionStep | null,
) {
  const haystack = [
    agentActivity?.title,
    agentActivity?.detail,
    activeRun?.runType,
    activeRun?.goal,
    activeStep?.stepType,
    activeStep?.stepName,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()

  const scopes: ActivityScope[] = []
  if (/(kod|code|repo|python|react|tsx|bug|fix|patch|frontend|backend|api|github|commit|deploy)/.test(haystack)) {
    scopes.push('code')
  }
  if (/(grafik|grafika|image|obraz|thumbnail|miniatur|portrait|portret|comfyui|cutaway|identity)/.test(haystack)) {
    scopes.push('graphics')
  }
  if (/(video|wideo|film|rolk|reel|short|mp4|render|tts|subtitle|napisy|wav2lip|motion)/.test(haystack)) {
    scopes.push('video')
  }
  if (/(browser|przeglad|stron|login|oauth|youtube|instagram|tiktok|slack|vercel|figma|canva|hubspot|stripe|airtable|publish)/.test(haystack)) {
    scopes.push('browser')
  }
  if (/(plik|file|pdf|word|ppt|excel|arkusz|dokument|obsidian|vault|upload)/.test(haystack)) {
    scopes.push('files')
  }
  return scopes.length ? scopes : (['files'] as ActivityScope[])
}

function mediaKindFromPath(path: string) {
  const lower = path.toLowerCase()
  if (/\.(png|jpe?g|webp|gif|bmp|svg)$/.test(lower)) return 'image' as const
  if (/\.(mp4|webm|mov|m4v)$/.test(lower)) return 'video' as const
  return null
}

function pushCandidate(
  candidates: PreviewCandidate[],
  label: string,
  storagePath: string,
) {
  const normalized = storagePath.trim()
  if (!normalized) return
  const kind = mediaKindFromPath(normalized)
  if (!kind) return
  candidates.push({
    kind,
    label,
    storagePath: normalized,
  })
}

function collectCandidates(
  value: unknown,
  options: {
    label: string
    candidates: PreviewCandidate[]
    depth: number
    seen: Set<unknown>
  },
) {
  const { label, candidates, depth, seen } = options
  if (value == null || depth > 5) return
  if (typeof value !== 'object') return
  if (seen.has(value)) return
  seen.add(value)

  if (Array.isArray(value)) {
    value.forEach((item, index) =>
      collectCandidates(item, {
        label: `${label} ${index + 1}`.trim(),
        candidates,
        depth: depth + 1,
        seen,
      }),
    )
    return
  }

  const record = value as Record<string, unknown>
  if (typeof record.screenshot_path === 'string') {
    pushCandidate(candidates, label || 'Podgląd przeglądarki', record.screenshot_path)
  }
  if (typeof record.storage_path === 'string') {
    pushCandidate(candidates, label || 'Podgląd artefaktu', record.storage_path)
  }

  Object.entries(record).forEach(([key, nested]) => {
    if (key === 'storage_path' || key === 'screenshot_path') return
    collectCandidates(nested, {
      label: label || key.replaceAll('_', ' '),
      candidates,
      depth: depth + 1,
      seen,
    })
  })
}

function resolvePreviewCandidate(
  activeRun: TrustedAutoExecutionRun | null,
  activeStep: TrustedAutoExecutionStep | null,
  scopes: ActivityScope[],
) {
  const candidates: PreviewCandidate[] = []

  if (activeStep?.screenshotPath) {
    pushCandidate(candidates, 'Podgląd przeglądarki', activeStep.screenshotPath)
  }

  if (activeRun) {
    collectCandidates(activeRun.outputJson.browser_result, {
      label: 'Podgląd przeglądarki',
      candidates,
      depth: 0,
      seen: new Set<unknown>(),
    })
    collectCandidates(activeRun.artifactsJson, {
      label: 'Artefakt',
      candidates,
      depth: 0,
      seen: new Set<unknown>(),
    })
    collectCandidates(activeRun.outputJson, {
      label: 'Wynik',
      candidates,
      depth: 0,
      seen: new Set<unknown>(),
    })
  }

  const preferBrowser = scopes.includes('browser')
  const preferVideo = scopes.includes('video')
  const preferGraphics = scopes.includes('graphics')

  return (
    candidates.find((candidate) => preferBrowser && candidate.kind === 'image' && candidate.storagePath.toLowerCase().includes('browser')) ??
    candidates.find((candidate) => preferVideo && candidate.kind === 'video') ??
    candidates.find((candidate) => preferGraphics && candidate.kind === 'image') ??
    candidates[0] ??
    null
  )
}

function localizeStepStatus(status: string) {
  switch (status) {
    case 'running':
      return 'w toku'
    case 'done':
      return 'gotowe'
    case 'failed':
      return 'błąd'
    default:
      return status
  }
}

function formatStepCount(count: number) {
  if (count === 1) return '1 krok'
  return `${count} kroków`
}

function humanizeRunType(runType?: string | null) {
  const normalized = (runType ?? '').toLowerCase()
  if (!normalized) return 'Czeka na zadanie'
  if (normalized.includes('campaign')) return 'Buduje materiał lub kampanię'
  if (normalized.includes('browser')) return 'Pracuje w przeglądarce'
  if (normalized.includes('xlsx') || normalized.includes('excel')) return 'Tworzy arkusz Excel'
  if (normalized.includes('document') || normalized.includes('docx') || normalized.includes('pdf')) return 'Tworzy dokument'
  if (normalized.includes('video') || normalized.includes('short') || normalized.includes('render')) return 'Tworzy wideo'
  if (normalized.includes('image') || normalized.includes('graphic')) return 'Tworzy grafikę'
  return runType.replaceAll('_', ' ').replaceAll('.', ' ')
}

function humanizeStepName(step?: TrustedAutoExecutionStep | null) {
  const raw = step?.stepName?.trim() || step?.stepType?.trim()
  if (!raw) return 'Brak aktywnego kroku'
  const normalized = `${raw} ${step?.stepType ?? ''}`.toLowerCase()
  if (normalized.includes('approval')) return 'Czeka na akceptację lub zamyka bramkę bezpieczeństwa'
  if (normalized.includes('metadata')) return 'Pakuje metadane i opis wyniku'
  if (normalized.includes('render')) return 'Renderuje finalny materiał'
  if (normalized.includes('reflect')) return 'Sprawdza wynik i zapisuje wnioski'
  if (normalized.includes('navigate')) return 'Otwiera stronę w przeglądarce'
  return raw.replaceAll('_', ' ').replaceAll('.', ' ')
}

function describeOutcome(
  agentStage: AgentStage,
  activeRun: TrustedAutoExecutionRun | null,
  activeStep: TrustedAutoExecutionStep | null,
  preview: PreviewCandidate | null,
) {
  if (activeStep?.status === 'failed' || activeRun?.status === 'failed' || agentStage === 'error') {
    return 'Wymaga sprawdzenia'
  }
  if (preview) return 'Wynik jest dostępny'
  if (activeRun?.status === 'done') return 'Zakończone'
  if (activeRun?.status === 'running') return 'W toku'
  if (agentStage === 'awaiting_human') return 'Czeka na decyzję'
  return 'Gotowy'
}

function describeExecutor(remoteRun?: RemoteExecutionRun | null) {
  if (remoteRun) return `Zdalny worker: ${remoteRun.routingTarget}`
  return 'Ten komputer'
}

function describeNextStep(
  agentStage: AgentStage,
  activeRun: TrustedAutoExecutionRun | null,
  activeStep: TrustedAutoExecutionStep | null,
  preview: PreviewCandidate | null,
) {
  if (activeStep?.status === 'failed' || activeRun?.status === 'failed' || agentStage === 'error') {
    return 'Sprawdź ostatni błąd i uruchom poprawkę albo ponów zadanie.'
  }
  if (agentStage === 'awaiting_human') return 'Podejmij decyzję w panelu akceptacji.'
  if (preview) return 'Otwórz wynik, oceń go i zdecyduj, czy Atnos ma poprawiać dalej.'
  if (activeRun?.status === 'running') return 'Poczekaj na zakończenie aktualnego kroku.'
  return 'Wpisz kolejne zadanie w czacie.'
}

export function AgentActivityWindow(props: AgentActivityWindowProps) {
  const stageMeta = STAGE_META[props.agentStage]
  const scopes = inferScopes(props.agentActivity, props.activeRun, props.activeStep)
  const preview = resolvePreviewCandidate(props.activeRun, props.activeStep, scopes)
  const recentSteps = props.activeRun ? [...props.activeRun.steps].slice(-5).reverse() : []
  const title = props.agentActivity?.title ?? stageMeta.fallbackTitle
  const detail =
    props.agentActivity?.detail ??
    props.activeRun?.goal ??
    stageMeta.fallbackDetail
  const outcome = describeOutcome(props.agentStage, props.activeRun, props.activeStep, preview)
  const nextStep = describeNextStep(props.agentStage, props.activeRun, props.activeStep, preview)

  return (
    <section
      data-testid="agent-activity-window"
      className="shell-panel-strong overflow-hidden rounded-lg p-3 lg:p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="label-eyebrow">ATNOS LIVE</div>
          <div className="mt-2 text-base text-text-primary lg:text-lg">{title}</div>
          <div className="mt-2 max-w-3xl text-sm leading-5 text-text-secondary">{detail}</div>
        </div>
        <div className={`inline-flex items-center gap-2 rounded-pill border px-3 py-1.5 text-[11px] font-medium ${stageMeta.tone}`}>
          <span className={`h-2 w-2 rounded-full ${stageMeta.dot}`} aria-hidden />
          {stageMeta.label}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {scopes.map((scope) => {
          const meta = SCOPE_META[scope]
          const Icon = meta.icon
          return (
            <div
              key={scope}
              className="shell-module inline-flex items-center gap-2 rounded-pill px-3 py-1.5 text-xs text-text-secondary"
            >
              <Icon size={12} className="text-accent-primary" />
              {meta.label}
            </div>
          )
        })}
        <div className="shell-module inline-flex items-center gap-2 rounded-pill px-3 py-1.5 text-xs text-text-secondary">
          <MonitorPlay size={12} className="text-accent-primary" />
          Runtime przeglądarki: {props.browserRuntimeReady ? 'gotowy' : 'niegotowy'}
        </div>
        {props.remoteWorker ? (
          <div className="inline-flex items-center gap-2 rounded-pill border border-border-default bg-panel px-3 py-1.5 text-xs text-text-secondary">
            <Globe size={12} className="text-accent-primary" />
            Węzeł zdalny: {props.remoteWorker.label}
          </div>
        ) : null}
        {props.onPause ? (
          <button
            type="button"
            disabled={props.controlsDisabled}
            onClick={props.onPause}
            className="shell-module inline-flex items-center gap-2 rounded-pill px-3 py-1.5 text-xs text-text-secondary transition hover:border-accent-primary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            Wstrzymaj
          </button>
        ) : null}
        {props.onInterrupt ? (
          <button
            type="button"
            disabled={props.controlsDisabled}
            onClick={props.onInterrupt}
            className="inline-flex items-center gap-2 rounded-pill border border-sem-error/40 bg-panel px-3 py-1.5 text-xs text-sem-error transition hover:bg-sem-error/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Przerwij
          </button>
        ) : null}
      </div>

      <div className="mt-3 grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(280px,0.85fr)]">
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <div className="shell-bento rounded-lg px-3 py-3">
              <div className="label-eyebrow">CO ROBI</div>
              <div className="mt-2 text-sm text-text-primary">
                {humanizeRunType(props.activeRun?.runType)}
              </div>
              {props.activeRun?.goal ? (
                <div className="mt-2 line-clamp-2 text-[11px] leading-4 text-text-tertiary">{props.activeRun.goal}</div>
              ) : null}
            </div>
            <div className="shell-bento rounded-lg px-3 py-3">
              <div className="label-eyebrow">NA CZYM STOI</div>
              <div className="mt-2 text-sm text-text-primary">
                {humanizeStepName(props.activeStep)}
              </div>
              {props.activeStep?.stepName ? (
                <div className="mt-2 line-clamp-1 text-[11px] text-text-tertiary">{props.activeStep.stepName}</div>
              ) : null}
            </div>
            <div className="shell-bento rounded-lg px-3 py-3">
              <div className="label-eyebrow">WYNIK</div>
              <div className="mt-2 text-sm text-text-primary">{outcome}</div>
              {preview ? <div className="mt-2 line-clamp-1 text-[11px] text-text-tertiary">{preview.label}</div> : null}
            </div>
            <div className="shell-bento rounded-lg px-3 py-3">
              <div className="label-eyebrow">GDZIE DZIAŁA</div>
              <div className="mt-2 text-sm text-text-primary">
                {describeExecutor(props.remoteRun)}
              </div>
            </div>
          </div>

          <div className="shell-bento rounded-lg px-3 py-3">
            <div className="label-eyebrow">NASTĘPNY KROK</div>
            <div className="mt-2 text-sm leading-5 text-text-primary">{nextStep}</div>
          </div>

          <div className="shell-bento rounded-lg px-3 py-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="label-eyebrow">OSTATNIE OPERACJE</div>
              <div className="text-[11px] text-text-tertiary">
                {recentSteps.length ? formatStepCount(recentSteps.length) : 'brak kroków'}
              </div>
            </div>
            {recentSteps.length ? (
              <div className="space-y-2">
                {recentSteps.map((step) => (
                  <div
                    key={step.executionStepId}
                    className="flex items-start justify-between gap-3 rounded-md border border-border-default bg-elevated/70 px-3 py-2"
                  >
                    <div className="min-w-0">
                      <div className="text-sm text-text-primary">{step.stepName}</div>
                      <div className="mt-1 text-[11px] text-text-tertiary">
                        {step.stepType || 'workflow-step'}
                        {step.screenshotPath ? ' · podgląd' : ''}
                      </div>
                    </div>
                    <div className="shrink-0 font-mono text-[11px] text-text-secondary">
                      {localizeStepStatus(step.status)}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm leading-6 text-text-tertiary">
                Gdy Atnos uruchomi kod, grafikę, render albo przeglądarkę, tutaj od razu zobaczysz żywe kroki.
              </div>
            )}
          </div>
        </div>

        <div className="shell-bento overflow-hidden rounded-lg">
          <div className="flex items-center justify-between border-b border-border-default px-3 py-3">
            <div>
              <div className="label-eyebrow">PODGLĄD PRACY</div>
              <div className="mt-1 text-sm text-text-primary">
                {preview?.label ?? 'Brak gotowego artefaktu podglądu'}
              </div>
            </div>
            {preview ? (
              <a
                href={buildAgentArtifactDownloadUrl(preview.storagePath)}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-2 rounded-pill border border-border-default px-3 py-1.5 text-[11px] text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
              >
                <ExternalLink size={12} />
                Otwórz
              </a>
            ) : null}
          </div>

          {preview ? (
            preview.kind === 'image' ? (
              <img
                src={buildAgentArtifactDownloadUrl(preview.storagePath)}
                alt={preview.label}
                className="h-[220px] w-full object-cover lg:h-[240px]"
              />
            ) : (
              <video
                src={buildAgentArtifactDownloadUrl(preview.storagePath)}
                className="h-[220px] w-full bg-black object-cover lg:h-[240px]"
                controls
                muted
                playsInline
              />
            )
          ) : (
            <div className="flex h-[220px] items-center justify-center px-6 text-center text-xs leading-6 text-text-tertiary lg:h-[240px] lg:text-sm">
              To okno pokazuje ostatni zrzut przeglądarki, grafikę albo finalny klip, gdy workflow zapisze pierwszy artefakt.
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

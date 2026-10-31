import { useEffect, useMemo, useState } from 'react'
import { fetchChannelConfig, saveChannelConfig, type ChannelConfig } from '../lib/api'

const DAYS = [
  { id: 'mon', label: 'Pon' },
  { id: 'tue', label: 'Wt' },
  { id: 'wed', label: 'Śr' },
  { id: 'thu', label: 'Czw' },
  { id: 'fri', label: 'Pt' },
  { id: 'sat', label: 'Sob' },
  { id: 'sun', label: 'Nd' },
]

const EMPTY_CONFIG: ChannelConfig = {
  channel_id: '',
  channel_name: '',
  language: 'en',
  region: 'PL',
  format: 'shorts',
  max_duration_seconds: 60,
  niches: [],
  visual_style: '',
  narration_style: '',
  allowed_topics: [],
  forbidden_topics: [],
  reference_channels: [],
  competitor_channels: [],
  publish_schedule: {
    enabled: false,
    frequency_per_week: 0,
    days_of_week: [],
    time_utc: '',
  },
  target_audience: '',
}

type ListField = 'niches' | 'allowed_topics' | 'forbidden_topics' | 'reference_channels' | 'competitor_channels'

function TextField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  return (
    <label className="block">
      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-text-tertiary">{label}</span>
      <input
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full border border-border-default bg-void px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
      />
    </label>
  )
}

function TextAreaField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  return (
    <label className="block">
      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-text-tertiary">{label}</span>
      <textarea
        value={value}
        placeholder={placeholder}
        rows={4}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full resize-y border border-border-default bg-void px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
      />
    </label>
  )
}

function ChipEditor({
  label,
  items,
  placeholder,
  onChange,
}: {
  label: string
  items: string[]
  placeholder: string
  onChange: (items: string[]) => void
}) {
  const [draft, setDraft] = useState('')

  const add = () => {
    const value = draft.trim()
    if (!value || items.includes(value)) return
    onChange([...items, value])
    setDraft('')
  }

  return (
    <div>
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-text-tertiary">{label}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {items.map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => onChange(items.filter((current) => current !== item))}
            className="border border-accent-primary/40 bg-accent-primary/10 px-2 py-1 font-mono text-[11px] text-accent-primary"
            title="Kliknij, aby usunąć"
          >
            {item} ×
          </button>
        ))}
        {items.length === 0 ? (
          <span className="border border-dashed border-border-default px-2 py-1 font-mono text-[11px] text-text-tertiary">
            brak
          </span>
        ) : null}
      </div>
      <div className="mt-2 flex gap-2">
        <input
          value={draft}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              add()
            }
          }}
          className="min-w-0 flex-1 border border-border-default bg-void px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
        />
        <button
          type="button"
          onClick={add}
          className="border border-border-strong px-3 py-2 font-display text-[11px] uppercase tracking-[0.14em] text-text-primary hover:border-accent-primary"
        >
          Dodaj
        </button>
      </div>
    </div>
  )
}

export default function ChannelSetupRoute() {
  const [config, setConfig] = useState<ChannelConfig>(EMPTY_CONFIG)
  const [warnings, setWarnings] = useState<string[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading')
  const [message, setMessage] = useState('')

  const canStartPipeline = useMemo(() => config.niches.length > 0, [config.niches.length])

  async function load() {
    setStatus('loading')
    setMessage('')
    try {
      const payload = await fetchChannelConfig()
      setConfig({ ...EMPTY_CONFIG, ...payload.config })
      setWarnings(payload.warnings)
      setStatus('ready')
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'Nie udało się wczytać konfiguracji kanału.')
    }
  }

  async function save() {
    setStatus('saving')
    setMessage('')
    try {
      const payload = await saveChannelConfig(config)
      setConfig({ ...EMPTY_CONFIG, ...payload.config })
      setWarnings(payload.warnings)
      setStatus('ready')
      setMessage('Konfiguracja zapisana i gotowa dla pipeline.')
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : 'Nie udało się zapisać konfiguracji kanału.')
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const update = (patch: Partial<ChannelConfig>) => setConfig((current) => ({ ...current, ...patch }))
  const updateList = (field: ListField, items: string[]) => update({ [field]: items } as Partial<ChannelConfig>)

  return (
    <div className="space-y-4 p-4">
      <section className="border border-border-default bg-panel">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-default px-4 py-3">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.18em] text-text-tertiary">
              Channel Intelligence Setup
            </div>
            <h1 className="mt-1 font-display text-xl font-semibold uppercase tracking-[0.1em] text-text-primary">
              Konfiguracja kanału
            </h1>
          </div>
          <div className="font-mono text-[11px] uppercase text-text-tertiary">
            Status:{' '}
            <span className={canStartPipeline ? 'text-success' : 'text-warning'}>
              {canStartPipeline ? 'pipeline gotowy' : 'brak niszy'}
            </span>
          </div>
        </div>

        <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,0.8fr)]">
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <TextField
                label="Nazwa kanału"
                value={config.channel_name}
                placeholder="np. YT Finanse PL"
                onChange={(value) => update({ channel_name: value })}
              />
              <TextField
                label="Channel ID"
                value={config.channel_id}
                placeholder="UC..."
                onChange={(value) => update({ channel_id: value })}
              />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <TextAreaField
                label="Styl wizualny"
                value={config.visual_style}
                placeholder="np. dark finance, kontrast, szybkie cięcia"
                onChange={(value) => update({ visual_style: value })}
              />
              <TextAreaField
                label="Styl narracji"
                value={String(config.narration_style ?? '')}
                placeholder="np. konkretny, emocjonalny, bez sztucznego tonu"
                onChange={(value) => update({ narration_style: value })}
              />
            </div>
            <TextAreaField
              label="Grupa docelowa"
              value={config.target_audience}
              placeholder="Dla kogo Atnos ma produkować shorty?"
              onChange={(value) => update({ target_audience: value })}
            />

            <div className="grid gap-4 lg:grid-cols-2">
              <ChipEditor
                label="Nisze"
                items={config.niches}
                placeholder="finanse osobiste"
                onChange={(items) => updateList('niches', items)}
              />
              <ChipEditor
                label="Dozwolone tematy"
                items={config.allowed_topics ?? []}
                placeholder="oszczędzanie"
                onChange={(items) => updateList('allowed_topics', items)}
              />
              <ChipEditor
                label="Zakazane tematy"
                items={config.forbidden_topics ?? []}
                placeholder="porady medyczne"
                onChange={(items) => updateList('forbidden_topics', items)}
              />
              <ChipEditor
                label="Kanały referencyjne"
                items={config.reference_channels}
                placeholder="https://youtube.com/@alux"
                onChange={(items) => updateList('reference_channels', items)}
              />
              <ChipEditor
                label="Konkurencja"
                items={config.competitor_channels}
                placeholder="https://youtube.com/@konkurent"
                onChange={(items) => updateList('competitor_channels', items)}
              />
            </div>
          </div>

          <aside className="space-y-4">
            <div className="border border-border-default bg-void p-4">
              <div className="font-display text-[12px] uppercase tracking-[0.18em] text-text-primary">
                Harmonogram publikacji
              </div>
              <div className="mt-4 grid grid-cols-7 gap-1">
                {DAYS.map((day) => {
                  const selected = config.publish_schedule.days_of_week.includes(day.id)
                  return (
                    <button
                      key={day.id}
                      type="button"
                      onClick={() => {
                        const current = config.publish_schedule.days_of_week
                        update({
                          publish_schedule: {
                            ...config.publish_schedule,
                            days_of_week: selected
                              ? current.filter((item) => item !== day.id)
                              : [...current, day.id],
                          },
                        })
                      }}
                      className={[
                        'border px-2 py-2 font-mono text-[10px] uppercase',
                        selected
                          ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                          : 'border-border-default text-text-secondary',
                      ].join(' ')}
                    >
                      {day.label}
                    </button>
                  )
                })}
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-1">
                <TextField
                  label="Godzina UTC"
                  value={config.publish_schedule.time_utc}
                  placeholder="14:00"
                  onChange={(value) =>
                    update({ publish_schedule: { ...config.publish_schedule, time_utc: value } })
                  }
                />
                <TextField
                  label="Częstotliwość / tydzień"
                  value={String(config.publish_schedule.frequency_per_week)}
                  onChange={(value) =>
                    update({
                      publish_schedule: {
                        ...config.publish_schedule,
                        frequency_per_week: Number(value) || 0,
                      },
                    })
                  }
                />
              </div>
            </div>

            <div className="border border-border-default bg-void p-4">
              <div className="font-display text-[12px] uppercase tracking-[0.18em] text-text-primary">
                Kontrola
              </div>
              <div className="mt-3 space-y-2 font-mono text-[11px] text-text-secondary">
                <div>Język: {config.language}</div>
                <div>Format: {config.format}</div>
                <div>Maksymalny czas: {config.max_duration_seconds}s</div>
                <div>Stan formularza: {status}</div>
              </div>
              {warnings.length > 0 ? (
                <div className="mt-3 space-y-2">
                  {warnings.map((warning) => (
                    <div key={warning} className="border border-warning/30 bg-warning/10 p-2 font-mono text-[11px] text-warning">
                      {warning}
                    </div>
                  ))}
                </div>
              ) : null}
              {message ? (
                <div className="mt-3 border border-border-default bg-panel p-2 font-mono text-[11px] text-text-secondary">
                  {message}
                </div>
              ) : null}
              <div className="mt-4 grid gap-2">
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={status === 'saving'}
                  className="border border-accent-primary bg-accent-primary/10 px-4 py-3 font-display text-[12px] uppercase tracking-[0.16em] text-accent-primary disabled:opacity-50"
                >
                  Save & Apply
                </button>
                <button
                  type="button"
                  onClick={() => void load()}
                  disabled={status === 'loading'}
                  className="border border-border-strong px-4 py-3 font-display text-[12px] uppercase tracking-[0.16em] text-text-primary disabled:opacity-50"
                >
                  Load from file
                </button>
              </div>
            </div>
          </aside>
        </div>
      </section>
    </div>
  )
}

import { useEffect, useMemo, useState } from 'react'
import {
  fetchIntelligenceCompetitors,
  fetchIntelligenceGoogleTrends,
  fetchIntelligenceOwnPerformance,
  fetchIntelligenceReferenceChannels,
  fetchIntelligenceSignals,
  type IntelligenceCompetitorChannel,
  type IntelligenceCompetitorVideo,
  type IntelligenceGoogleTrendSignal,
  type IntelligenceOwnPerformance,
  type IntelligenceReferenceChannel,
  type IntelligenceYoutubeSignal,
} from '../lib/api'

type IntelligenceTab = 'trends' | 'gtrends' | 'competitors' | 'own' | 'references'

const TABS: Array<{ id: IntelligenceTab; label: string }> = [
  { id: 'trends', label: 'Trendy' },
  { id: 'gtrends', label: 'Google Trends' },
  { id: 'competitors', label: 'Konkurencja' },
  { id: 'own', label: 'Własny kanał' },
  { id: 'references', label: 'Wzorce' },
]

function formatNumber(value: number | null | undefined) {
  const number = Number(value ?? 0)
  return new Intl.NumberFormat('pl-PL', { maximumFractionDigits: 0 }).format(number)
}

function formatPct(value: number | null | undefined) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${Number(value).toFixed(1)}%`
}

function Sparkline({ value }: { value: number }) {
  const points = useMemo(() => {
    const safe = Math.max(0, Math.min(100, Number(value) || 0))
    return Array.from({ length: 8 }, (_, index) => {
      const x = index * 14
      const wave = Math.sin(index * 0.9 + safe / 25) * 12
      const y = 42 - (safe / 100) * 30 + wave
      return `${x},${Math.max(6, Math.min(48, y))}`
    }).join(' ')
  }, [value])

  return (
    <svg viewBox="0 0 98 54" className="h-12 w-24 overflow-visible text-accent-primary">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="square" />
    </svg>
  )
}

export default function IntelligenceRoute() {
  const [tab, setTab] = useState<IntelligenceTab>('trends')
  const [nicheFilter, setNicheFilter] = useState('')
  const [signals, setSignals] = useState<IntelligenceYoutubeSignal[]>([])
  const [gtrends, setGtrends] = useState<IntelligenceGoogleTrendSignal[]>([])
  const [competitorChannels, setCompetitorChannels] = useState<IntelligenceCompetitorChannel[]>([])
  const [competitorVideos, setCompetitorVideos] = useState<IntelligenceCompetitorVideo[]>([])
  const [ownPerformance, setOwnPerformance] = useState<IntelligenceOwnPerformance[]>([])
  const [references, setReferences] = useState<IntelligenceReferenceChannel[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    let alive = true
    async function load() {
      setStatus('loading')
      try {
        const [nextSignals, nextGtrends, competitors, own, refs] = await Promise.all([
          fetchIntelligenceSignals(nicheFilter || undefined),
          fetchIntelligenceGoogleTrends(),
          fetchIntelligenceCompetitors(),
          fetchIntelligenceOwnPerformance(),
          fetchIntelligenceReferenceChannels(),
        ])
        if (!alive) return
        setSignals(nextSignals)
        setGtrends(nextGtrends)
        setCompetitorChannels(competitors.channels)
        setCompetitorVideos(competitors.videos)
        setOwnPerformance(own)
        setReferences(refs)
        setStatus('ready')
      } catch {
        if (!alive) return
        setStatus('error')
      }
    }
    void load()
    return () => {
      alive = false
    }
  }, [nicheFilter])

  return (
    <div className="space-y-4 p-4">
      <section className="border border-border-default bg-panel">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-default px-4 py-3">
          <div>
            <div className="font-display text-[12px] uppercase tracking-[0.18em] text-text-tertiary">
              Intelligence
            </div>
            <h1 className="mt-1 font-display text-xl font-semibold uppercase tracking-[0.1em] text-text-primary">
              Realne sygnały rynku
            </h1>
          </div>
          <div className="font-mono text-[11px] uppercase text-text-tertiary">
            Status: <span className={status === 'error' ? 'text-error' : 'text-accent-primary'}>{status}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b border-border-default p-3">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={[
                'border px-3 py-2 font-display text-[11px] uppercase tracking-[0.12em]',
                tab === item.id
                  ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                  : 'border-border-default text-text-secondary',
              ].join(' ')}
            >
              {item.label}
            </button>
          ))}
          <input
            value={nicheFilter}
            onChange={(event) => setNicheFilter(event.target.value)}
            placeholder="Filtr niszy"
            className="ml-auto min-w-48 border border-border-default bg-void px-3 py-2 font-mono text-[11px] text-text-primary outline-none focus:border-accent-primary"
          />
        </div>

        {status === 'error' ? (
          <div className="border-b border-warning/30 bg-warning/10 p-3 font-mono text-[11px] text-warning">
            Brak polaczenia z backendem intelligence albo brak danych. UI zostaje nieblokujacy.
          </div>
        ) : null}

        <div className="p-4">
          {tab === 'trends' ? <TrendsTable signals={signals} /> : null}
          {tab === 'gtrends' ? <GoogleTrendsPanel items={gtrends} /> : null}
          {tab === 'competitors' ? (
            <CompetitorsPanel channels={competitorChannels} videos={competitorVideos} />
          ) : null}
          {tab === 'own' ? <OwnPerformanceTable items={ownPerformance} /> : null}
          {tab === 'references' ? <ReferenceCards items={references} /> : null}
        </div>
      </section>
    </div>
  )
}

function TrendsTable({ signals }: { signals: IntelligenceYoutubeSignal[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse font-mono text-[11px]">
        <thead className="text-left uppercase text-text-tertiary">
          <tr className="border-b border-border-default">
            <th className="py-2 pr-3">Thumb</th>
            <th className="py-2 pr-3">Title</th>
            <th className="py-2 pr-3">Channel</th>
            <th className="py-2 pr-3 text-right">Views</th>
            <th className="py-2 pr-3 text-right">Likes</th>
            <th className="py-2 pr-3">Collected</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((item) => (
            <tr key={item.video_id} className="border-b border-border-muted text-text-secondary">
              <td className="py-2 pr-3">
                {item.thumbnail_url ? (
                  <img src={item.thumbnail_url} alt="" className="h-[27px] w-12 border border-border-muted object-cover" />
                ) : (
                  <div className="h-[27px] w-12 border border-border-muted bg-void" />
                )}
              </td>
              <td className="max-w-xl py-2 pr-3 text-text-primary">{item.title}</td>
              <td className="py-2 pr-3">{item.channel_title ?? '-'}</td>
              <td className="py-2 pr-3 text-right">{formatNumber(item.view_count)}</td>
              <td className="py-2 pr-3 text-right">{formatNumber(item.like_count)}</td>
              <td className="py-2 pr-3">{item.collected_at?.slice(0, 16) ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {signals.length === 0 ? <EmptyState label="Brak sygnałów YouTube Trends." /> : null}
    </div>
  )
}

function GoogleTrendsPanel({ items }: { items: IntelligenceGoogleTrendSignal[] }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {items.map((item) => (
        <article key={`${item.keyword}-${item.timeframe}-${item.geo}`} className="border border-border-default bg-void p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="font-display text-sm uppercase tracking-[0.14em] text-text-primary">{item.keyword}</div>
              <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">
                {item.geo} / {item.timeframe} / score {item.interest_score}
              </div>
            </div>
            <Sparkline value={item.interest_score} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {(item.related_rising ?? []).slice(0, 6).map((query) => (
              <span key={query.query} className="border border-border-muted bg-panel px-2 py-1 font-mono text-[10px] text-accent-primary">
                {query.query}
              </span>
            ))}
          </div>
        </article>
      ))}
      {items.length === 0 ? <EmptyState label="Brak danych Google Trends." /> : null}
    </div>
  )
}

function CompetitorsPanel({
  channels,
  videos,
}: {
  channels: IntelligenceCompetitorChannel[]
  videos: IntelligenceCompetitorVideo[]
}) {
  return (
    <div className="space-y-3">
      {channels.map((channel) => {
        const topVideos = videos.filter((video) => video.channel_id === channel.channel_id).slice(0, 5)
        return (
          <article key={channel.channel_id} className="border border-border-default bg-void p-4">
            <div className="grid gap-3 lg:grid-cols-[1fr_150px_150px_150px]">
              <div>
                <div className="font-display text-sm uppercase tracking-[0.14em] text-text-primary">
                  {channel.channel_title ?? channel.channel_id}
                </div>
                <div className="mt-1 font-mono text-[10px] uppercase text-text-tertiary">{channel.top_format ?? '-'}</div>
              </div>
              <Metric label="avg 7d" value={formatNumber(channel.avg_views_7d)} />
              <Metric label="cadence / week" value={channel.posting_frequency_per_week.toFixed(1)} />
              <Metric label="refresh" value={channel.last_refreshed?.slice(0, 10) ?? '-'} />
            </div>
            <div className="mt-4 grid gap-2 lg:grid-cols-5">
              {topVideos.map((video) => (
                <div key={video.video_id} className="border border-border-muted bg-panel p-2">
                  <div className="line-clamp-2 min-h-10 font-mono text-[10px] text-text-primary">{video.title}</div>
                  <div className="mt-2 font-mono text-[10px] text-text-tertiary">{formatNumber(video.views)} views</div>
                </div>
              ))}
            </div>
          </article>
        )
      })}
      {channels.length === 0 ? <EmptyState label="Brak zapisanej konkurencji." /> : null}
    </div>
  )
}

function OwnPerformanceTable({ items }: { items: IntelligenceOwnPerformance[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] border-collapse font-mono text-[11px]">
        <thead className="text-left uppercase text-text-tertiary">
          <tr className="border-b border-border-default">
            <th className="py-2 pr-3">Video</th>
            <th className="py-2 pr-3 text-right">Views</th>
            <th className="py-2 pr-3 text-right">Watch min</th>
            <th className="py-2 pr-3 text-right">Avg view %</th>
            <th className="py-2 pr-3 text-right">CTR</th>
            <th className="py-2 pr-3 text-right">Subs</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${item.account_id}-${item.video_id}-${item.collected_at}`} className="border-b border-border-muted text-text-secondary">
              <td className="py-2 pr-3 text-text-primary">{item.title ?? item.video_id}</td>
              <td className="py-2 pr-3 text-right">{formatNumber(item.views)}</td>
              <td className="py-2 pr-3 text-right">{formatNumber(item.watch_time_min)}</td>
              <td className="py-2 pr-3 text-right">{formatPct(item.avg_view_pct)}</td>
              <td className="py-2 pr-3 text-right">{formatPct(item.ctr)}</td>
              <td className="py-2 pr-3 text-right">{formatNumber(item.subs_gained)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 ? <EmptyState label="Brak danych własnego kanału." /> : null}
    </div>
  )
}

function ReferenceCards({ items }: { items: IntelligenceReferenceChannel[] }) {
  return (
    <div className="grid gap-3 lg:grid-cols-3">
      {items.map((item) => (
        <article key={item.channel_id} className="border border-border-default bg-void p-4">
          <div className="font-display text-sm uppercase tracking-[0.14em] text-text-primary">
            {item.channel_title ?? item.channel_id}
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2">
            <Metric label="avg sec" value={String(item.avg_duration_sec)} />
            <Metric label="cadence" value={item.posting_cadence_per_week.toFixed(1)} />
            <Metric label="engage" value={formatPct(item.avg_engagement_rate * 100)} />
          </div>
          <div className="mt-4 space-y-2">
            {item.hook_patterns.slice(0, 4).map((pattern) => (
              <div key={pattern} className="border-l-2 border-accent-primary bg-panel px-3 py-2 font-mono text-[11px] text-text-secondary">
                {pattern}
              </div>
            ))}
          </div>
          {item.hook_pattern_summary ? (
            <p className="mt-4 font-mono text-[11px] leading-relaxed text-text-tertiary">{item.hook_pattern_summary}</p>
          ) : null}
        </article>
      ))}
      {items.length === 0 ? <EmptyState label="Brak kanałów referencyjnych." /> : null}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border-muted bg-panel p-2">
      <div className="font-mono text-[9px] uppercase text-text-tertiary">{label}</div>
      <div className="mt-1 font-display text-sm uppercase text-text-primary">{value}</div>
    </div>
  )
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="border border-dashed border-border-muted bg-void p-6 text-center font-mono text-[11px] uppercase tracking-[0.12em] text-text-tertiary">
      {label}
    </div>
  )
}

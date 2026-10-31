import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, BookOpen, Clapperboard, Layers3, MessageSquareText, Mic, RefreshCw, Search } from 'lucide-react'
import { fetchMemoryIndexMap, fetchProductionOrders, fetchProductionWorkspace, fetchProviderCards, type MemoryIndexDocument, type MemoryIndexMap, type ProductionJobPayload, type ProductionWorkspaceChannel } from '../lib/api'
import { productionStepLabel } from '../lib/productionMessages'
import { useSystemStore } from '../stores/systemStore'
import { useChatStore } from '../stores/chatStore'
import { useUIStore } from '../stores/uiStore'
import type { AgentStage } from '../types/agent'

const dateLabel = (value: string | null | undefined) => {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Brak daty'
  return new Intl.DateTimeFormat('pl-PL', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

const orderActivityTime = (order: ProductionJobPayload) => {
  const updated = Date.parse(order.updated_at ?? '')
  if (Number.isFinite(updated)) return updated
  const created = Date.parse(order.created_at ?? '')
  return Number.isFinite(created) ? created : 0
}

export function assistantStatus(reachable: boolean, checked: boolean, modelReady: boolean, stage: AgentStage, active: boolean) {
  if (!checked) return { key: 'unknown', label: 'Sprawdzam połączenie', detail: 'Czekam na pierwszy status API.' }
  if (!reachable) return { key: 'offline', label: 'Brak połączenia', detail: 'API Atnosa nie odpowiada. Sprawdź usługi w ustawieniach.' }
  if (stage === 'offline') return { key: 'offline', label: 'Model offline', detail: 'Czat zgłasza niedostępność runtime’u. Sprawdź połączenie w ustawieniach.' }
  if (stage === 'error') return { key: 'error', label: 'Wymaga uwagi', detail: 'Ostatnia czynność zakończyła się błędem. Szczegóły są w rozmowie.' }
  if (stage === 'awaiting_human') return { key: 'waiting', label: 'Czekam na decyzję', detail: 'Otwórz rozmowę, aby sprawdzić pytanie lub wymaganą zgodę.' }
  if (stage === 'executing') return { key: 'working', label: 'Wykonuję zadanie', detail: 'Śledź rzeczywiste kroki i wyniki narzędzi w rozmowie.' }
  if (stage === 'thinking' || active) return { key: 'thinking', label: 'Analizuję', detail: 'Trwa obsługa polecenia.' }
  if (!modelReady) return { key: 'unknown', label: 'Model wymaga sprawdzenia', detail: 'API odpowiada, ale gotowość wybranego dostawcy nie jest potwierdzona.' }
  return { key: 'ready', label: 'Gotowy do rozmowy', detail: 'Napisz polecenie lub otwórz panel głosowy.' }
}

const sourceLabel = (type: string) => ({ obsidian_note: 'Obsidian', project_note: 'Notatka projektu', project_documentation: 'Dokumentacja', runtime_report: 'Raport', knowledge_workspace: 'Biblioteka', learning_result: 'Wynik uczenia', test_result: 'Wynik testów', worker_result: 'Wynik zadania' })[type] ?? type.replaceAll('_', ' ')
const documentTopic = (doc: MemoryIndexDocument) => doc.topic && doc.topic !== 'obsidian-note'
  ? doc.topic : doc.path.split('/').slice(1, -1).join(' / ') || sourceLabel(doc.source_type)

/** Each edge means topic membership in the persisted index, not an inferred semantic relation. */
function MemoryMap({ snapshot, selected, onSelect, query, source }: { snapshot: MemoryIndexMap; selected: string | null; onSelect: (doc: MemoryIndexDocument) => void; query: string; source: string }) {
  const docs = useMemo(() => snapshot.documents.filter(doc => (source === 'all' || doc.source_type === source)
    && `${doc.title} ${doc.topic}`.toLocaleLowerCase('pl').includes(query.toLocaleLowerCase('pl'))), [snapshot, query, source])
  const displayed = docs.slice(0, 36)
  const topics = [...new Set(displayed.map(documentTopic))]
  const anchors = topics.map((topic, index) => {
    const angle = index / Math.max(topics.length, 1) * Math.PI * 2 - Math.PI / 2
    return { topic, x: 400 + Math.cos(angle) * 215, y: 228 + Math.sin(angle) * 135 }
  })
  return <>
    <div className="assistant-map-stage">
      {displayed.length ? <svg viewBox="0 0 800 460" className="assistant-map" role="group" aria-label="Mapa dokumentów połączonych z zapisanymi tematami">
        <defs><radialGradient id="assistant-map-light"><stop stopColor="var(--assistant-accent)" stopOpacity=".08" /><stop offset="1" stopColor="var(--assistant-accent)" stopOpacity="0" /></radialGradient></defs>
        <ellipse cx="400" cy="228" rx="350" ry="220" fill="url(#assistant-map-light)" />
        {anchors.map(anchor => <g key={anchor.topic}>
          <line x1="400" y1="228" x2={anchor.x} y2={anchor.y} className="assistant-map-link" />
          <circle cx={anchor.x} cy={anchor.y} r="7" className="assistant-map-topic" />
          <text x={anchor.x} y={anchor.y - 17} textAnchor="middle" className="assistant-map-label">{anchor.topic.length > 25 ? `${anchor.topic.slice(0, 24)}…` : anchor.topic}</text>
        </g>)}
        {displayed.map(doc => {
          const anchor = anchors.find(item => item.topic === documentTopic(doc))!
          const siblings = displayed.filter(item => documentTopic(item) === documentTopic(doc))
          const index = siblings.indexOf(doc)
          const angle = index * 2.39996
          const radius = 24 + Math.sqrt(index + 1) * 12
          const x = anchor.x + Math.cos(angle) * radius, y = anchor.y + Math.sin(angle) * radius
          return <g key={doc.path} role="button" tabIndex={0} aria-label={`Otwórz szczegóły: ${doc.title}`} aria-pressed={selected === doc.path}
            className={`assistant-map-node ${doc.source_type === 'obsidian_note' ? 'is-obsidian' : ''} ${selected === doc.path ? 'is-selected' : ''}`}
            onClick={() => onSelect(doc)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(doc) } }}>
            <title>{doc.title} · {sourceLabel(doc.source_type)}</title>
            <line x1={anchor.x} y1={anchor.y} x2={x} y2={y} className="assistant-map-link" />
            <circle cx={x} cy={y} r="13" fill="transparent" />
            <circle cx={x} cy={y} r={selected === doc.path ? 7 : 4.5} className="assistant-map-dot" />
          </g>
        })}
        <circle cx="400" cy="228" r="27" className="assistant-map-root" />
        <text x="400" y="232" textAnchor="middle" className="assistant-map-root-label">ATNOS</text>
      </svg> : <div className="assistant-empty"><BookOpen size={26} /><p>{snapshot.status === 'indexed' ? 'Brak dokumentów pasujących do filtra.' : 'Mapa czeka na dostępny indeks wiedzy.'}</p></div>}
    </div>
    <p className="assistant-map-caption">Linie pokazują przypisanie do tematu lub folderu. Na mapie: {displayed.length} z {docs.length} pobranych dokumentów.</p>
    {docs.length ? <label className="assistant-note-picker">Wybierz dokument
      <select value={selected && docs.some(doc => doc.path === selected) ? selected : ''} onChange={event => { const doc = docs.find(item => item.path === event.target.value); if (doc) onSelect(doc) }}>
        <option value="">Przeglądaj pamięć…</option>
        {docs.map(doc => <option key={doc.path} value={doc.path}>{doc.title}</option>)}
      </select>
    </label> : null}
  </>
}

export default function AssistantWorkspace() {
  const navigate = useNavigate()
  const stage = useSystemStore(state => state.agentStage)
  const activity = useSystemStore(state => state.agentActivity)
  const reachable = useSystemStore(state => state.networkSignals.backendReachable)
  const checkedAt = useSystemStore(state => state.backendCheckedAt)
  const model = useSystemStore(state => state.model)
  const providers = useSystemStore(state => state.providerCards)
  const setProviderCards = useSystemStore(state => state.setProviderCards)
  const activeTurn = useChatStore(state => state.activeTurn)
  const queue = useChatStore(state => state.queuedTurns)
  const title = useChatStore(state => state.selectedConversationTitle)
  const focusComposer = useUIStore(state => state.requestComposerFocus)
  const enableVoicePanel = useUIStore(state => state.setOkropekModeEnabled)
  const openVoicePanel = useUIStore(state => state.setOkropekPanelOpen)
  const [memory, setMemory] = useState<MemoryIndexMap | null>(null)
  const [memoryError, setMemoryError] = useState<string | null>(null)
  const [channels, setChannels] = useState<ProductionWorkspaceChannel[]>([])
  const [orders, setOrders] = useState<ProductionJobPayload[]>([])
  const [workspaceFailed, setWorkspaceFailed] = useState(false)
  const [ordersFailed, setOrdersFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [runtimeCheckedAt, setRuntimeCheckedAt] = useState<string | null>(null)
  const [runtimeCheckFailed, setRuntimeCheckFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [source, setSource] = useState('all')
  const [selected, setSelected] = useState<MemoryIndexDocument | null>(null)
  const requestId = useRef(0)
  const productionError = workspaceFailed || ordersFailed ? 'Status produkcji jest niedostępny lub nieaktualny.' : null
  const modelKnown = Boolean(model.name.trim()) && model.name !== 'Nieustalony model'
  const state = assistantStatus(reachable, checkedAt != null, !runtimeCheckFailed && modelKnown && providers.some(provider => provider.providerKey === model.provider && provider.readinessStatus === 'ready'), stage, activeTurn != null)

  const refresh = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    const [map, workspace, jobs, runtime] = await Promise.allSettled([fetchMemoryIndexMap(source), fetchProductionWorkspace(), fetchProductionOrders(), fetchProviderCards()])
    if (id !== requestId.current) return
    if (map.status === 'fulfilled') { setMemory(map.value); setMemoryError(null); setSelected(current => map.value.documents.find(doc => doc.path === current?.path) ?? null) }
    else setMemoryError('Nie udało się odczytać indeksu. Poprzedni podgląd może być nieaktualny.')
    if (workspace.status === 'fulfilled') setChannels(workspace.value.channels)
    if (jobs.status === 'fulfilled') setOrders(jobs.value.orders)
    if (runtime.status === 'fulfilled') { setProviderCards(runtime.value); setRuntimeCheckedAt(new Date().toISOString()); setRuntimeCheckFailed(false) }
    else setRuntimeCheckFailed(true)
    setWorkspaceFailed(workspace.status === 'rejected')
    setOrdersFailed(jobs.status === 'rejected')
    setLoading(false)
  }, [source, setProviderCards])
  useEffect(() => { void refresh(); return () => { requestId.current += 1 } }, [refresh])
  const activeProduction = orders.some(order => order.worker_live)
  useEffect(() => {
    if (!activeProduction) return
    let cancelled = false
    let inFlight = false
    const timer = window.setInterval(() => {
      if (document.hidden || inFlight) return
      const id = requestId.current
      inFlight = true
      void fetchProductionOrders().then(result => {
        if (!cancelled && id === requestId.current) { setOrders(result.orders); setOrdersFailed(false) }
      }).catch(() => {
        if (!cancelled && id === requestId.current) setOrdersFailed(true)
      }).finally(() => { inFlight = false })
    }, 15_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [activeProduction])
  const openChat = () => { navigate('/chat'); focusComposer() }
  const indexAgeDays = memory?.last_indexed_at && Number.isFinite(Date.parse(memory.last_indexed_at))
    ? Math.max(0, Math.floor((Date.now() - Date.parse(memory.last_indexed_at)) / 86_400_000)) : null

  return <div className="assistant-page" data-testid="assistant-workspace">
    <header className="assistant-heading">
      <div><p className="assistant-eyebrow">TWÓJ LOKALNY WARSZTAT</p><h1>Centrum Atnosa<span>.</span></h1><p>Pamięć, rozmowa i praca nad kanałami w jednym miejscu.</p></div>
      <button className="assistant-refresh" onClick={() => void refresh()} disabled={loading} aria-label="Odśwież centrum asystenta"><RefreshCw size={16} className={loading ? 'animate-spin' : ''} />{loading ? 'Wczytuję' : 'Odśwież'}</button>
    </header>
    <div className="assistant-grid">
      <section className="assistant-memory" aria-labelledby="assistant-memory-title">
        <div className="assistant-panel-heading"><div><p className="assistant-eyebrow">KONTEKST I WIEDZA</p><h2 id="assistant-memory-title">Pamięć Atnosa</h2></div><span className="assistant-count">{memory?.document_count == null ? '—' : memory.document_count} <span>w indeksie</span></span></div>
        <div className="assistant-map-controls"><label className="assistant-search"><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Szukaj w mapie…" aria-label="Szukaj w mapie pamięci" /></label>
          <select value={source} onChange={event => setSource(event.target.value)} aria-label="Źródło pamięci"><option value="all">Wszystkie źródła</option>{Object.keys(memory?.source_counts ?? {}).map(key => <option key={key} value={key}>{sourceLabel(key)}</option>)}</select></div>
        {memoryError ? <p role="alert" className="assistant-warning">{memoryError}</p> : null}
        {indexAgeDays != null && indexAgeDays > 7 ? <p className="assistant-warning">Indeks sprzed {indexAgeDays} dni. Mapa może nie obejmować nowszych notatek.</p> : null}
        {memory ? <MemoryMap snapshot={memory} selected={selected?.path ?? null} onSelect={setSelected} query={query} source={source} /> : <div className="assistant-empty"><Layers3 size={26} /><p>{loading ? 'Wczytuję zapisany indeks pamięci…' : 'Indeks pamięci jest niedostępny.'}</p></div>}
        <div className="assistant-note-detail" aria-live="polite">
          {selected ? <><div className="assistant-eyebrow">{sourceLabel(selected.source_type)} · {selected.topic}</div><h3>{selected.title}</h3><p className="assistant-citation">{selected.citation_path || selected.path}</p><p>Źródło: {dateLabel(selected.source_mtime)} · indeks: {dateLabel(selected.indexed_at)}</p></>
            : <><h3>Sprawdź, na czym opiera się pamięć.</h3><p>Wybierz punkt lub dokument. Mapa korzysta z zapisanego indeksu; nie uruchamia modelu i nie skanuje całego vaultu przy każdej wiadomości.</p></>}
          <span className="assistant-index-time">Ostatnie indeksowanie: {dateLabel(memory?.last_indexed_at)}</span>
        </div>
      </section>
      <aside className="assistant-status-panel" aria-label="Stan asystenta">
        <div className="assistant-panel-heading"><p className="assistant-eyebrow">ASYSTENT</p><span className={`assistant-connection ${reachable ? 'is-online' : ''}`}>{checkedAt == null ? 'Sprawdzam API' : reachable ? 'API połączone' : 'API offline'}</span></div>
        <div className={`assistant-core is-${state.key}`} aria-hidden="true"><div className="assistant-core-ring" /><div className="assistant-core-orbit" /><div className="assistant-core-inner"><span>A T N O S</span><i /></div></div>
        <div className="assistant-state" role="status"><h2>{state.label}</h2><p>{state.key === 'working' || state.key === 'thinking' ? activity?.title ?? activeTurn?.summary ?? state.detail : state.detail}</p></div>
        <div className="assistant-runtime"><span>Wybrany model</span><strong>{model.name}</strong><span>{model.provider === 'ollama' ? 'Ollama · lokalnie' : model.provider}</span><span>{runtimeCheckFailed ? 'Odczyt gotowości nie powiódł się' : runtimeCheckedAt ? `Sprawdzono: ${dateLabel(runtimeCheckedAt)}` : 'Czekam na sprawdzenie runtime’u'}</span></div>
        <div className="assistant-work-counts"><div><strong>{activeTurn ? 1 : 0}</strong><span>aktywna rozmowa</span></div><div><strong>{queue.length}</strong><span>w kolejce czatu</span></div></div>
        <button className="assistant-primary" onClick={openChat}><MessageSquareText size={17} />{title ? 'Kontynuuj rozmowę' : 'Porozmawiaj z Atnosem'}<ArrowRight size={17} /></button>
        {title ? <p className="assistant-conversation-title">{title}</p> : null}
        <button className="assistant-voice" onClick={() => { enableVoicePanel(true); openVoicePanel(true) }}><Mic size={16} />Otwórz panel głosowy</button>
        <p className="assistant-voice-note">Nasłuch włączasz samodzielnie w panelu.</p>
      </aside>
    </div>
    <section className="assistant-channels" aria-labelledby="assistant-channels-title">
      <div className="assistant-section-heading"><div><p className="assistant-eyebrow">PRODUKCJA</p><h2 id="assistant-channels-title">Co dzieje się na kanałach</h2></div><Link to="/create">Otwórz Studio <ArrowRight size={15} /></Link></div>
      {productionError ? <p role="alert" className="assistant-warning">{productionError}</p> : null}
      <div className="assistant-channel-grid">{channels.map(channel => {
        const jobs = orders.filter(order => order.request?.project === channel.key || order.account_id === `yt-${channel.key.replaceAll('_', '-')}`)
          .sort((a, b) => orderActivityTime(b) - orderActivityTime(a))
        const latest = jobs[0]
        const running = jobs.find(order => order.worker_live)
        const shown = running ?? latest
        const label = shown ? shown.worker_live ? 'Pracuje' : shown.status === 'ready' ? 'Do obejrzenia' : shown.status === 'blocked' ? 'Wymaga uwagi' : shown.status === 'queued' ? 'W kolejce' : shown.status : 'Brak zleceń w podglądzie'
        return <Link key={channel.key} className={`assistant-channel ${shown?.status === 'blocked' ? 'needs-attention' : ''}`} to={shown ? `/studio/${shown.id}` : '/create'}>
          <div className="assistant-channel-top"><Clapperboard size={16} /><span>{channel.language.toUpperCase()}</span><ArrowRight size={16} /></div><h3>{channel.name}</h3><span className="assistant-channel-state">{label}</span><p>{shown ? productionStepLabel(shown.current_step) : channel.description}</p><small>{shown ? `Ostatnia produkcja · ${dateLabel(shown.updated_at)}` : 'Wybierz pomysł w Studiu'}</small>
        </Link>
      })}</div>
      {!loading && channels.length === 0 && !productionError ? <p className="assistant-warning">Brak danych kanałów w konfiguracji.</p> : null}
      <p className="assistant-source-note">Podgląd zapisanej wiedzy i ostatnich 30 zleceń. Gotowość publikacji sprawdzisz przy konkretnym filmie.</p>
    </section>
  </div>
}

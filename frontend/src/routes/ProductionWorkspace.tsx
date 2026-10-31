import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ArrowRight, Check, Film, LoaderCircle, RefreshCw, Settings2 } from 'lucide-react'
import { productionBlocker, productionStepLabel, productionSubmissionBlocker } from '../lib/productionMessages'
import {
  createProductionOrder, fetchProductionImageOptions, fetchProductionOrders, fetchProductionWorkspace, resumeProductionOrder,
  buildProductionReviewArtifactUrl,
  type ProductionImageOptions, type ProductionJobPayload, type ProductionWorkspaceChannel,
} from '../lib/api'

const IMAGE_STYLE_IDS = ['A', 'B', 'C', 'D'] as const

const STATUS: Record<string, string> = {
  ready: 'Gotowe do obejrzenia', running: 'W produkcji', queued: 'W kolejce',
  retrying: 'Ponawianie etapu',
  script_ready: 'Scenariusz gotowy do przeczytania',
  blocked: 'Wymaga uwagi', failed: 'Błąd produkcji', cancelled: 'Anulowane',
}

function orderStatusLabel(order: ProductionJobPayload): string {
  // Match Studio: a saved running state is not evidence of a live worker.
  // Missing telemetry on older orders must not be interpreted as a dead worker.
  if (order.worker_live === false) {
    if (['running', 'retrying'].includes(order.status)) return 'Produkcja przerwana'
    if (order.status === 'queued') return 'Oczekuje na uruchomienie'
  }
  return STATUS[order.status] ?? order.status
}

function latestChannelOrder(channelKey: string, orders: ProductionJobPayload[]): ProductionJobPayload | undefined {
  const matches = orders.filter(order => {
    const request = order.request
    return request && typeof request === 'object' && request.project === channelKey
  })
  return matches.sort((left, right) => {
    const leftTime = Date.parse(left.updated_at || left.created_at || '') || 0
    const rightTime = Date.parse(right.updated_at || right.created_at || '') || 0
    return rightTime - leftTime
  })[0]
}

function formatOrderUpdatedAt(order: ProductionJobPayload): string {
  const value = order.updated_at || order.created_at
  if (!value) return 'czas aktualizacji nie jest dostępny'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'czas aktualizacji nie jest dostępny'
  return `aktualizacja ${new Intl.DateTimeFormat('pl-PL', { dateStyle: 'short', timeStyle: 'short' }).format(date)}`
}

export default function ProductionWorkspace() {
  const navigate = useNavigate()
  const location = useLocation()
  const [channels, setChannels] = useState<ProductionWorkspaceChannel[]>([])
  const [imageOptions, setImageOptions] = useState<ProductionImageOptions | null>(null)
  const [imageOptionsError, setImageOptionsError] = useState('')
  const [imageOptionsLoading, setImageOptionsLoading] = useState(true)
  const [selected, setSelected] = useState('wealth_blueprint')
  const [imageProvider, setImageProvider] = useState<'comfyui-local'>('comfyui-local')
  const [imageReferenceId, setImageReferenceId] = useState<'wb-original-board'>('wb-original-board')
  const [visualStyleId, setVisualStyleId] = useState<'A' | 'B' | 'C' | 'D'>('A')
  const [productionSet, setProductionSet] = useState<'' | 'wb-editorial-v1'>('')
  const [orders, setOrders] = useState<ProductionJobPayload[]>([])
  const [topic, setTopic] = useState('')
  const [direction, setDirection] = useState('')
  const [notes, setNotes] = useState('')
  const [localVoice, setLocalVoice] = useState<'' | 'Ryan-B' | 'Ryan-neutral'>('')
  const [targetDuration, setTargetDuration] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [orderError, setOrderError] = useState('')
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState(false)
  const [resuming, setResuming] = useState<string | null>(null)
  const requestKey = useRef<string | null>(null)
  const submitting = useRef(false)
  const mounted = useRef(true)
  const channel = channels.find(item => item.key === selected)
  const storyPreview = Boolean(channel?.can_prepare_story)
  const needsImageOptions = channel?.key === 'wealth_blueprint' && !storyPreview && !productionSet
  const recipe = channel?.production_sets?.find(item => item.id === productionSet)
  const selectedProvider = imageOptions?.providers.find(item => item.id === imageProvider)
  const selectedReference = imageOptions?.references.find(item => item.id === imageReferenceId)
  const hasAllImageStyles = IMAGE_STYLE_IDS.every(id => imageOptions?.styles.some(style => style.id === id))
  const imageOptionsReady = Boolean(selectedProvider?.available && selectedReference?.available && hasAllImageStyles)
  const freeRequested = Boolean(productionSubmissionBlocker(`${direction} ${notes}`))
  const localVoices = channel?.local_voices ?? (channel?.local_voice ? [channel.local_voice] : [])
  const selectedVoice = localVoices.find(voice => voice.id === localVoice)
  const submissionBlocker = selected === 'wealth_blueprint'
    ? localVoice && !selectedVoice?.available
      ? `${localVoice === 'Ryan-B' ? 'Lokalny Ryan B' : 'Spokojny Ryan'} jest niedostępny. ${selectedVoice?.reason || 'Sprawdź lokalny runtime głosu.'} Nie wybrano zastępczego głosu.`
      : productionSubmissionBlocker(`${direction} ${notes}`, Boolean(localVoice)) : ''
  const canSubmit = Boolean(channel?.can_order || storyPreview) && (!needsImageOptions || imageOptionsReady) && !submissionBlocker
    && (selected !== 'wealth_blueprint' || !productionSet || (recipe?.available && Boolean(localVoice)))

  const readImageOptions = useCallback(async () => {
    setImageOptionsLoading(true)
    setImageOptionsError('')
    try {
      const options = await fetchProductionImageOptions()
      if (!mounted.current) return
      setImageOptions(options)
    } catch (caught) {
      if (!mounted.current) return
      setImageOptions(null)
      setImageOptionsError(caught instanceof Error ? caught.message : 'Nie udało się odczytać dostępności grafiki.')
    } finally {
      if (mounted.current) setImageOptionsLoading(false)
    }
  }, [])

  const refresh = useCallback(async () => {
    const [catalog, jobs, imageOptionsResult] = await Promise.allSettled([
      fetchProductionWorkspace(), fetchProductionOrders(), fetchProductionImageOptions(),
    ])
    if (!mounted.current) return
    if (catalog.status === 'fulfilled') {
      setChannels(catalog.value.channels)
      setLoadError('')
    } else {
      setChannels([])
      setLoadError('Nie udało się połączyć z Atnosem. Uruchom aplikację lub sprawdź połączenia w ustawieniach.')
    }
    if (jobs.status === 'fulfilled') {
      setOrders(jobs.value.orders)
      setOrderError('')
    } else {
      setOrderError('Nie udało się odczytać produkcji. Lista nie jest aktualna.')
    }
    if (imageOptionsResult.status === 'fulfilled') {
      setImageOptions(imageOptionsResult.value)
      setImageOptionsError('')
    } else {
      setImageOptions(null)
      setImageOptionsError(imageOptionsResult.reason instanceof Error
        ? imageOptionsResult.reason.message : 'Nie udało się odczytać dostępności grafiki.')
    }
    setImageOptionsLoading(false)
    setLoading(false)
  }, [])

  useEffect(() => {
    mounted.current = true
    void refresh()
    const interval = window.setInterval(() => { if (!document.hidden) void refresh() }, 10000)
    return () => { mounted.current = false; window.clearInterval(interval) }
  }, [refresh])

  useEffect(() => {
    const state = location.state as { productionPrefill?: unknown } | null
    const prefill = state?.productionPrefill
    if (!prefill || typeof prefill !== 'object') return
    const values = prefill as Record<string, unknown>
    if (values.project !== 'wealth_blueprint' || typeof values.topic !== 'string') return
    setSelected('wealth_blueprint')
    setTopic(values.topic)
    setDirection(typeof values.direction === 'string' ? values.direction : '')
    setNotes(typeof values.optionalNotes === 'string' ? values.optionalNotes : '')
    const brief = `${values.direction || ''} ${values.optionalNotes || ''}`
    if (/Ryan[- ]neutral|spokojn\w*\s+Ryan|Ryan\s+neutral/i.test(brief)) setLocalVoice('Ryan-neutral')
    else if (/\bRyan(?:\s+B)?\b/i.test(brief)) setLocalVoice('Ryan-B')
    const duration = brief.match(/\b(\d{2})\s*[- ]?(?:s\b|sekund\w*|seconds?)/i)
    if (duration && Number(duration[1]) >= 18 && Number(duration[1]) <= 88) setTargetDuration(duration[1])
    navigate(`${location.pathname}${location.search}${location.hash}`, { replace: true, state: null })
  }, [location.hash, location.pathname, location.search, location.state, navigate])

  function edit(setter: (value: string) => void, value: string) {
    setter(value)
    requestKey.current = null
    setActionError('')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitting.current || !channel || !canSubmit || topic.trim().length < 3) return
    submitting.current = true
    setBusy(true)
    setActionError('')
    requestKey.current ??= `workspace-${crypto.randomUUID()}`
    try {
      const order = await createProductionOrder({ project: channel.key, topic: topic.trim(), direction: direction.trim(),
        optionalNotes: notes.trim(), deliveryMode: 'local_only', requestKey: requestKey.current,
        ...(storyPreview ? { productionScope: 'script_preview' as const } : {}),
        ...(!storyPreview && localVoice ? { voiceCandidate: localVoice, providerPolicy: 'strict_local' as const } : {}),
        ...(!storyPreview && targetDuration ? { targetDurationSeconds: Number(targetDuration) } : {}),
        ...(!storyPreview && productionSet ? { productionSetId: productionSet } : {}),
        ...(needsImageOptions ? { imageProvider, imageReferenceId, visualStyleId } : {}) })
      navigate(`/studio/${encodeURIComponent(order.id)}`)
    } catch (error) {
      setActionError(productionBlocker(error instanceof Error ? error.message : 'Nie udało się przyjąć zlecenia.'))
      // Keep the idempotency key: an uncertain response must not duplicate a production.
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  async function resume(id: string) {
    if (resuming) return
    setResuming(id)
    setActionError('')
    try {
      await resumeProductionOrder(id)
      navigate(`/studio/${encodeURIComponent(id)}`)
    } catch (error) {
      setActionError(productionBlocker(error instanceof Error ? error.message : 'Nie udało się wznowić produkcji.'))
    } finally { setResuming(null) }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-5 py-7 md:px-8 md:py-9">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-accent-primary">Twoje studio YouTube</p>
          <h1 className="text-3xl font-semibold tracking-tight md:text-4xl">Od pomysłu do gotowego filmu.</h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">Wybierz kanał, opisz pomysł i śledź produkcję. Scenariusz, lektor i teksty filmu powstają po angielsku.</p>
        </div>
        <Link to="/settings" className="inline-flex items-center gap-2 rounded-md border border-border-default px-3 py-2 text-sm text-text-secondary hover:text-text-primary"><Settings2 size={16} /> Połączenia i ustawienia</Link>
      </div>

      {loadError ? <div role="alert" className="rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm"><p>{loadError}</p><button type="button" onClick={() => void refresh()} className="mt-3 inline-flex items-center gap-2 font-semibold"><RefreshCw size={14} /> Sprawdź ponownie</button></div> : null}

      <section aria-labelledby="channel-heading">
        <h2 id="channel-heading" className="mb-3 text-sm font-semibold">1. Wybierz kanał</h2>
        {loading ? <p role="status" className="text-sm text-text-secondary">Wczytywanie kanałów…</p> : null}
        <div className="grid gap-3 md:grid-cols-3">
          {channels.map(item => <button key={item.key} type="button" aria-pressed={selected === item.key} disabled={busy}
            onClick={() => edit(setSelected, item.key)}
            className={`min-w-0 rounded-lg border p-4 text-left transition-colors ${selected === item.key ? 'border-accent-primary bg-accent-primary/10' : 'border-border-default bg-panel hover:border-border-strong'}`}>
            <span className="flex items-center justify-between gap-2"><span className="text-xs text-text-secondary">{item.category} · EN</span>{selected === item.key ? <Check size={16} className="text-accent-primary" /> : <Film size={16} className="text-text-tertiary" />}</span>
            <span className="mt-3 block text-lg font-semibold">{item.name}</span>
            <span className="mt-2 block text-xs leading-5 text-text-secondary">{item.description}</span>
            {!item.can_order ? <span className="mt-3 block text-xs text-warning">{item.can_prepare_story ? 'Dostępny scenariusz opowieści' : 'Wymaga konfiguracji produkcji'}</span> : null}
          </button>)}
        </div>
      </section>

      <section aria-labelledby="channel-stage-heading" className="rounded-lg border border-border-default bg-panel p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="channel-stage-heading" className="text-sm font-semibold">Bieżący etap kanałów</h2>
            <p className="mt-1 text-xs text-text-secondary">Odczyt z najnowszych zleceń Atnosa; odświeżany co 10 sekund.</p>
          </div>
          <button type="button" onClick={() => void refresh()} className="inline-flex items-center gap-2 text-xs text-text-secondary"><RefreshCw size={14} /> Odśwież statusy</button>
        </div>
        {orderError ? <p role="status" className="text-sm text-warning">Status kanałów nie jest aktualny: {orderError}</p> : loading ? <p role="status" className="text-sm text-text-secondary">Wczytywanie etapów kanałów…</p> : (
          <div className="grid gap-3 md:grid-cols-3">
            {channels.map(item => {
              const latest = latestChannelOrder(item.key, orders)
              return <article key={item.key} className="min-w-0 rounded-md border border-border-default bg-void/40 p-4">
                <h3 className="truncate text-sm font-semibold">{item.name}</h3>
                {latest ? <>
                  <Link to={`/studio/${encodeURIComponent(latest.id)}`} className="mt-2 block truncate text-xs text-accent-primary hover:underline">{latest.topic_title || 'Zlecenie bez tytułu'}</Link>
                  <p className={`mt-2 text-sm ${latest.status === 'blocked' || latest.status === 'failed' ? 'text-warning' : 'text-text-primary'}`}>
                    {orderStatusLabel(latest)}{latest.current_step ? ` · ${productionStepLabel(latest.current_step)}` : ''}
                  </p>
                  <p className="mt-1 text-xs text-text-tertiary">{formatOrderUpdatedAt(latest)}</p>
                </> : <p className="mt-2 text-xs text-text-secondary">Brak zlecenia tego kanału w ostatnich 30 wpisach.</p>}
              </article>
            })}
          </div>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <form onSubmit={event => void submit(event)} className="min-w-0 rounded-lg border border-border-default bg-panel p-5 md:p-6">
          <h2 className="mb-5 text-sm font-semibold">2. Opisz film</h2>
          <fieldset disabled={busy} className="space-y-5">
            {selected === 'wealth_blueprint' && channel?.production_sets?.length ? <label className="block text-sm font-medium">Zestaw produkcyjny
              <select value={productionSet} onChange={event => { setProductionSet(event.target.value as typeof productionSet); requestKey.current = null }} disabled={busy} className="mt-2 block w-full bg-void p-2">
                <option value="">Ilustracje ComfyUI — wybierz styl poniżej</option>
                {channel.production_sets.map(item => <option key={item.id} value={item.id} disabled={!item.available}>{item.label}{item.available ? '' : ' — niedostępny'}</option>)}
              </select>
              {productionSet ? <span className="mt-2 block text-xs text-text-secondary">{recipe?.scope || recipe?.reason} Wymaga jawnego wyboru lokalnego Ryana. Zestaw jest kandydatem do prób, a nie zatwierdzeniem całego filmu.</span> : null}
            </label> : null}
            {needsImageOptions ? <section aria-labelledby="image-options-heading" className="rounded-md border border-border-default bg-void/50 p-4">
              <h3 id="image-options-heading" className="text-sm font-semibold">Grafika Wealth Blueprint</h3>
              <p className="mt-1 text-xs leading-5 text-text-secondary">Wariant grafiki jest próbą stylu. Każdy wygenerowany obraz wymaga oceny wyglądu.</p>
              {imageOptionsLoading ? <p role="status" className="mt-3 text-sm text-text-secondary">Sprawdzanie lokalnego generatora i planszy referencyjnej…</p> : null}
              {!imageOptionsLoading && !imageOptionsReady ? <div role="alert" className="mt-3 rounded border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
                <p>{imageOptionsError
                  ? `Nie udało się odczytać opcji grafiki: ${imageOptionsError}`
                  : !selectedProvider?.available
                    ? 'Lokalny ComfyUI nie jest skonfigurowany na tym komputerze.'
                    : !selectedReference?.available
                      ? selectedReference?.reason === 'invalid'
                        ? 'Plansza referencyjna Wealth Blueprint jest nieczytelna lub ma nieobsługiwany format.'
                        : 'Brakuje planszy referencyjnej Wealth Blueprint.'
                      : 'Nie udało się wczytać pełnego zestawu czterech stylów grafiki.'}</p>
                <button type="button" onClick={() => void readImageOptions()} className="mt-2 inline-flex items-center gap-2 font-semibold"><RefreshCw size={14} /> Sprawdź ponownie</button>
              </div> : null}
              <fieldset disabled={busy || imageOptionsLoading || !imageOptionsReady} className="mt-4 space-y-4 disabled:opacity-70">
                <legend className="text-xs font-medium text-text-secondary">Generator i materiał referencyjny</legend>
                <label className="flex items-start gap-2 text-sm">
                  <input type="radio" name="image-provider" value="comfyui-local" checked={imageProvider === 'comfyui-local'} onChange={() => setImageProvider('comfyui-local')} />
                  <span><span className="block font-medium">{selectedProvider?.label || 'ComfyUI (lokalny, bez opłaty za API)'}</span><span className="block text-xs text-text-secondary">Atnos może wykonać do 40 lokalnych prób ilustracji; obrazy nadal przechodzą kontrolę jakości.</span></span>
                </label>
                <label className="flex items-start gap-2 text-sm">
                  <input type="radio" name="image-reference" value="wb-original-board" checked={imageReferenceId === 'wb-original-board'} onChange={() => setImageReferenceId('wb-original-board')} />
                  <span><span className="block font-medium">{selectedReference?.label || 'Plansza referencyjna Wealth Blueprint'}</span><span className="block text-xs text-text-secondary">Plansza służy jako inspiracja wizualna.</span></span>
                </label>
              </fieldset>
              <fieldset disabled={busy || imageOptionsLoading || !imageOptionsReady} className="mt-4">
                <legend className="text-xs font-medium text-text-secondary">Wybierz wariant stylu</legend>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {imageOptions?.styles.filter(style => IMAGE_STYLE_IDS.includes(style.id)).map(style => <label key={style.id} className={`flex cursor-pointer items-start gap-2 rounded border p-3 text-sm ${visualStyleId === style.id ? 'border-accent-primary bg-accent-primary/10' : 'border-border-default'}`}>
                    <input type="radio" name="visual-style" value={style.id} checked={visualStyleId === style.id} onChange={() => {
                      setVisualStyleId(style.id)
                      requestKey.current = null
                      setActionError('')
                    }} />
                    <span><span className="block font-semibold">Styl {style.id}</span><span className="block text-xs leading-5 text-text-secondary">{style.name}</span></span>
                  </label>)}
                </div>
              </fieldset>
            </section> : null}
            <label className="block text-sm font-medium">Temat lub pomysł
              <input required minLength={3} maxLength={300} value={topic} onChange={event => edit(setTopic, event.target.value)} placeholder={selected === 'children_stories' ? 'Np. Mały lis pomaga odnaleźć zgubioną latarenkę' : selected === 'horror_stories' ? 'Np. Telefon dzwoni z mojego zamkniętego mieszkania' : 'Np. How to build your first emergency fund'} className="mt-2 w-full rounded-md border border-border-default bg-void px-3 py-3 text-sm outline-none focus:border-accent-primary" />
            </label>
            {!storyPreview && selected === 'wealth_blueprint' ? <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-medium">Głos dla tego filmu
                <select value={localVoice} onChange={event => { setLocalVoice(event.target.value === 'Ryan-B' || event.target.value === 'Ryan-neutral' ? event.target.value : ''); requestKey.current = null }} className="mt-2 w-full rounded-md border border-border-default bg-void px-3 py-3 text-sm">
                  <option value="">Głos z konfiguracji kanału</option>
                  <option value="Ryan-B">Ryan B — lokalny kandydat, bez płatnego fallbacku</option>
                  {channel?.local_voices?.some(voice => voice.id === 'Ryan-neutral') ? <option value="Ryan-neutral">Ryan — spokojna interpretacja, lokalny kandydat</option> : null}
                </select>
                {localVoice ? <span className="mt-1 block text-xs text-text-secondary">Wybór do tej próby. Nie zatwierdza jakości filmu ani domyślnego głosu kanału.</span> : null}
                {localVoice && selectedVoice?.available ? <audio
                  aria-label="Próbka wybranego głosu" controls preload="none" className="mt-3 w-full"
                  src={buildProductionReviewArtifactUrl(`/api/production/voice-candidates/${encodeURIComponent(localVoice)}/sample`) ?? undefined}
                /> : null}
              </label>
              <label className="block text-sm font-medium">Docelowa długość w sekundach
                <input type="number" min={18} max={88} value={targetDuration} onWheel={event => event.currentTarget.blur()} onChange={event => edit(setTargetDuration, event.target.value)} placeholder="Z profilu kanału" className="mt-2 w-full rounded-md border border-border-default bg-void px-3 py-3 text-sm" />
              </label>
            </div> : null}
            <label className="block text-sm font-medium">Co widz ma z tego wynieść?
              <textarea rows={3} maxLength={2000} value={direction} onChange={event => edit(setDirection, event.target.value)} placeholder="Opisz przebieg, odbiorcę lub główną myśl. Możesz pisać po polsku." className="mt-2 w-full resize-y rounded-md border border-border-default bg-void px-3 py-3 text-sm outline-none focus:border-accent-primary" />
            </label>
            <details className="text-sm text-text-secondary"><summary className="cursor-pointer">Dodatkowe wskazówki</summary><label className="mt-3 block">Uwagi do obrazu, narracji lub montażu<textarea rows={3} maxLength={4000} value={notes} onChange={event => edit(setNotes, event.target.value)} className="mt-2 w-full rounded-md border border-border-default bg-void px-3 py-3 text-sm outline-none focus:border-accent-primary" /></label></details>
          </fieldset>
          {channel?.blockers.map(blocker => <p key={blocker} className="mt-4 text-sm leading-6 text-warning">{blocker}</p>)}
          {actionError ? <p role="alert" className="mt-4 break-words text-sm text-error">{actionError}</p> : null}
          {submissionBlocker ? <p role="alert" className="mt-4 text-sm text-warning">{submissionBlocker}</p> : null}
          <div className="mt-6 flex flex-wrap items-center justify-between gap-4 border-t border-border-default pt-5">
            <p className="text-xs text-text-secondary">Po zakończeniu produkcji wynik zostanie zapisany na E: do Twojego obejrzenia.</p>
            <button type="submit" disabled={busy || !canSubmit || topic.trim().length < 3} className="inline-flex items-center gap-2 rounded-md bg-accent-primary px-5 py-3 text-sm font-semibold text-void disabled:cursor-not-allowed disabled:opacity-40">{busy ? <LoaderCircle size={17} className="animate-spin" /> : <Film size={17} />}{busy ? 'Przyjmowanie zlecenia…' : storyPreview ? 'Przygotuj scenariusz' : 'Utwórz rolkę'}</button>
          </div>
        </form>
        <aside className="space-y-5 rounded-lg border border-border-default p-5 text-sm">
          <h2 className="font-semibold">{storyPreview ? 'Twój scenariusz' : 'Twój pakiet'}</h2>
          <ul className="space-y-3 text-text-secondary">{(storyPreview ? ['Sześć scen po angielsku', 'Opis postaci i miejsc', 'Kontrola fabuły i odbiorcy'] : ['Film z lektorem i napisami', 'Miniatura i opis', 'Scenariusz i wyniki kontroli jakości']).map(label => <li key={label} className="flex gap-2"><Check size={15} className="mt-0.5 shrink-0 text-accent-primary" />{label}</li>)}</ul>
          <dl className="space-y-3 border-t border-border-default pt-4 text-xs">
            <div className="flex justify-between"><dt className="text-text-secondary">Język filmu</dt><dd>Angielski</dd></div>
            <div className="flex justify-between"><dt className="text-text-secondary">Format</dt><dd>{storyPreview ? 'Scenariusz do Shorta' : channel?.formats.includes('short') ? 'Short · 9:16' : 'Do konfiguracji'}</dd></div>
            {channel?.duration_seconds ? <div className="flex justify-between"><dt className="text-text-secondary">{storyPreview ? 'Docelowa długość filmu' : 'Długość'}</dt><dd>{!storyPreview && targetDuration ? `${targetDuration} s (±2 s)` : `${channel.duration_seconds.join('–')} s`}</dd></div> : null}
            {channel?.max_cost_usd != null ? <div className="flex justify-between"><dt className="text-text-secondary">{localVoice || freeRequested ? 'Płatne API' : 'Limit produkcji'}</dt><dd>{localVoice || freeRequested ? '$0.00 — wyłączone' : `$${channel.max_cost_usd.toFixed(2)}`}</dd></div> : null}
          </dl>
          <p className="text-xs leading-5 text-text-tertiary">Atnos sprawdza wymagane narzędzia przed startem. Limit obejmuje rezerwacje kosztów zapisane w profilu kanału.</p>
          <Link to="/chat" className="inline-flex items-center gap-2 text-xs text-text-secondary hover:text-text-primary">Otwórz rozmowę z Atnosem <ArrowRight size={14} /></Link>
        </aside>
      </div>

      <section aria-labelledby="orders-heading" className="pb-6">
        <div className="mb-4 flex items-center justify-between"><h2 id="orders-heading" className="text-lg font-semibold">Twoje produkcje</h2><button type="button" onClick={() => void refresh()} className="inline-flex items-center gap-2 text-xs text-text-secondary"><RefreshCw size={14} /> Odśwież</button></div>
        {orderError ? <p role="status" className="mb-3 text-sm text-warning">{orderError}</p> : null}
        {!loading && !orderError && orders.length === 0 ? <div className="rounded-lg border border-dashed border-border-default p-8 text-center text-sm text-text-secondary">Tutaj pojawi się pierwszy film. Zacznij od pomysłu powyżej.</div> : null}
        <div className="divide-y divide-border-default">{orders.map(order => <article key={order.id} className="flex flex-wrap items-center justify-between gap-4 py-4">
          <div className="min-w-0 flex-1"><Link to={`/studio/${encodeURIComponent(order.id)}`} className="block truncate text-sm font-semibold hover:text-accent-primary">{order.topic_title || 'Produkcja filmu'}</Link><p className={`mt-1 text-xs ${order.status === 'blocked' ? 'text-warning' : 'text-text-secondary'}`}>{orderStatusLabel(order)}{order.current_step ? ` · ${productionStepLabel(order.current_step)}` : ''}</p>{order.autonomous_recovery?.enabled ? <p className="mt-1 text-xs text-text-secondary">{order.autonomous_recovery.limit_reached ? 'Limit automatycznych wznowień wykorzystany' : 'Automatyczne odzyskiwanie włączone'}</p> : null}</div>
          <div className="flex items-center gap-3">{order.can_resume && !order.worker_live && order.status !== 'ready' ? <button type="button" disabled={resuming !== null || Boolean(orderError)} onClick={() => void resume(order.id)} className="rounded-md border border-border-default px-3 py-2 text-xs disabled:opacity-50">{resuming === order.id ? 'Wznawianie…' : 'Wznów'}</button> : null}<Link to={`/studio/${encodeURIComponent(order.id)}`} className="inline-flex items-center gap-2 text-xs text-accent-primary">{order.status === 'ready' ? 'Obejrzyj film' : 'Zobacz szczegóły'} <ArrowRight size={14} /></Link></div>
        </article>)}</div>
      </section>
    </div>
  )
}

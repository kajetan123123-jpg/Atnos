import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ProductionWorkspace from './ProductionWorkspace'
import * as api from '../lib/api'

vi.mock('../lib/api', () => ({
  createProductionOrder: vi.fn(), fetchProductionImageOptions: vi.fn(), fetchProductionOrders: vi.fn(),
  fetchProductionWorkspace: vi.fn(), resumeProductionOrder: vi.fn(),
  buildProductionReviewArtifactUrl: (path: string | null) => path,
}))

const imageOptions = {
  providers: [{ id: 'comfyui-local' as const, label: 'ComfyUI (lokalny, bez opłaty za API)', available: true, requires_reference: true }],
  references: [{ id: 'wb-original-board' as const, label: 'Plansza referencyjna Wealth Blueprint', available: true }],
  styles: [
    { id: 'A' as const, name: 'Ilustracja redakcyjna' },
    { id: 'B' as const, name: 'Warstwy papieru' },
    { id: 'C' as const, name: 'Rysunek techniczny' },
    { id: 'D' as const, name: 'Kadr filmowy' },
  ],
}

const catalog = { note: '', channels: [
  { key: 'wealth_blueprint', name: 'Wealth Blueprint', category: 'Inwestycje', description: 'Finanse', language: 'en', can_order: true, formats: ['short'], blockers: [], duration_seconds: [35, 60] as [number, number], max_cost_usd: 3 },
  { key: 'children_stories', name: 'Bajki dla dzieci', category: 'Bajki', description: 'Opowieści', language: 'en', can_order: false, formats: [], blockers: ['Profil bajek wymaga przygotowania.'] },
] }

function renderWorkspace(state?: unknown) {
  const initialEntry = state === undefined ? '/create' : { pathname: '/create', state }
  return render(<MemoryRouter initialEntries={[initialEntry]}><Routes><Route path="/create" element={<ProductionWorkspace />} /><Route path="/studio/:jobId" element={<h1>Podgląd zlecenia</h1>} /></Routes></MemoryRouter>)
}

describe('Self-service production', () => {
  it('submits the frozen native recipe without requiring unrelated ComfyUI availability', async () => {
    vi.mocked(api.fetchProductionWorkspace).mockResolvedValue({note:'',channels:[{...catalog.channels[0],
      production_sets:[{id:'wb-editorial-v1',label:'WB Editorial v1',available:true,reason:''}],
      local_voices:[{id:'Ryan-neutral',label:'Spokojny Ryan',available:true}]}]})
    vi.mocked(api.fetchProductionImageOptions).mockRejectedValue(new Error('ComfyUI is not running'))
    vi.mocked(api.createProductionOrder).mockResolvedValue({id:'recipe-order'} as api.ProductionJobPayload)
    const user=userEvent.setup();renderWorkspace()
    await user.selectOptions(await screen.findByLabelText(/Zestaw produkcyjny/),'wb-editorial-v1')
    await user.type(screen.getByLabelText('Temat lub pomysł'),'Two claims on one balance')
    expect(screen.getByRole('button',{name:'Utwórz rolkę'})).toBeDisabled()
    await user.selectOptions(screen.getByLabelText(/Głos dla tego filmu/),'Ryan-neutral')
    await user.click(screen.getByRole('button',{name:'Utwórz rolkę'}))
    await waitFor(()=>expect(api.createProductionOrder).toHaveBeenCalledTimes(1))
    const payload=vi.mocked(api.createProductionOrder).mock.calls[0][0]
    expect(payload).toMatchObject({productionSetId:'wb-editorial-v1',voiceCandidate:'Ryan-neutral',providerPolicy:'strict_local'})
    expect(payload.imageProvider).toBeUndefined();expect(payload.visualStyleId).toBeUndefined()
  })
  it('offers the neutral Ryan sample and binds an explicit strict-local choice', async () => {
    vi.mocked(api.fetchProductionWorkspace).mockResolvedValue({ note: '', channels: [{ ...catalog.channels[0],
      local_voices: [{ id: 'Ryan-neutral', label: 'Ryan — spokojna interpretacja', available: true }] }] })
    vi.mocked(api.createProductionOrder).mockResolvedValue({ id: 'prod-neutral' } as api.ProductionJobPayload)
    const user = userEvent.setup(); renderWorkspace()
    await screen.findByLabelText(/Głos dla tego filmu/)
    await user.selectOptions(screen.getByLabelText(/Głos dla tego filmu/), 'Ryan-neutral')
    expect(screen.getByLabelText('Próbka wybranego głosu')).toHaveAttribute('src', '/api/production/voice-candidates/Ryan-neutral/sample')
    await user.type(screen.getByLabelText('Temat lub pomysł'), 'Neutral voice test')
    await user.click(screen.getByRole('button', { name: 'Utwórz rolkę' }))
    await waitFor(() => expect(api.createProductionOrder).toHaveBeenCalledWith(expect.objectContaining({
      voiceCandidate: 'Ryan-neutral', providerPolicy: 'strict_local', deliveryMode: 'local_only',
    })))
  })
  it.each([
    ['running', false, 'Produkcja przerwana'],
    ['retrying', false, 'Produkcja przerwana'],
    ['queued', false, 'Oczekuje na uruchomienie'],
    ['running', true, 'W produkcji'],
    ['retrying', true, 'Ponawianie etapu'],
    ['queued', true, 'W kolejce'],
    ['running', undefined, 'W produkcji'],
    ['ready', false, 'Gotowe do obejrzenia'],
  ])('shows truthful status for %s with worker_live=%s', async (status, workerLive, label) => {
    vi.mocked(api.fetchProductionOrders).mockResolvedValue({ orders: [{
      id: 'prod-status', topic_title: 'Savings', status, worker_live: workerLive,
    }] as api.ProductionJobPayload[] })
    renderWorkspace()
    expect(await screen.findByText(label)).toBeInTheDocument()
    expect(api.resumeProductionOrder).not.toHaveBeenCalled()
    expect(api.createProductionOrder).not.toHaveBeenCalled()
  })

  it('shows the newest durable production stage for every channel', async () => {
    vi.mocked(api.fetchProductionWorkspace).mockResolvedValue({ note: '', channels: [
      { ...catalog.channels[0] },
      { ...catalog.channels[1], key: 'children_stories', can_prepare_story: true },
      { ...catalog.channels[1], key: 'horror_stories', name: 'Straszne historie', can_prepare_story: true },
    ] })
    vi.mocked(api.fetchProductionOrders).mockResolvedValue({ orders: [
      { id: 'prod-wb-old', account_id: 'wb', topic_id: 't1', topic_title: 'Old', request: { project: 'wealth_blueprint' }, status: 'blocked', current_step: 'script', updated_at: '2026-10-14T10:00:00Z', ready_for_review: false, ready_for_private_upload: false, steps: [] },
      { id: 'prod-kids', account_id: 'kids', topic_id: 't2', topic_title: 'Lantern', request: { project: 'children_stories' }, status: 'running', current_step: 'voice_qa/s03', worker_live: true, updated_at: '2026-10-15T10:00:00Z', ready_for_review: false, ready_for_private_upload: false, steps: [] },
      { id: 'prod-wb-new', account_id: 'wb', topic_id: 't3', topic_title: 'New savings plan', request: { project: 'wealth_blueprint' }, status: 'ready', current_step: 'package', updated_at: '2026-10-16T10:00:00Z', ready_for_review: true, ready_for_private_upload: false, steps: [] },
    ] as api.ProductionJobPayload[] })
    renderWorkspace()

    const overview = await screen.findByRole('region', { name: 'Bieżący etap kanałów' })
    expect(overview).toHaveTextContent('New savings plan')
    expect(overview).toHaveTextContent('Gotowe do obejrzenia')
    expect(overview).not.toHaveTextContent('Old')
    expect(overview).toHaveTextContent('Lantern')
    expect(overview).toHaveTextContent('W produkcji · Sprawdzanie wymowy')
    expect(overview).toHaveTextContent('Straszne historie')
    expect(overview).toHaveTextContent('Brak zlecenia tego kanału w ostatnich 30 wpisach.')
  })

  it('offers a local story script with an explicit scope instead of promising a finished video', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchProductionWorkspace).mockResolvedValue({ note: '', channels: [
      { ...catalog.channels[1], can_prepare_story: true, formats: ['script_preview'] },
    ] })
    vi.mocked(api.createProductionOrder).mockResolvedValue({ id: 'prod-story' } as api.ProductionJobPayload)
    renderWorkspace()
    await user.click(await screen.findByRole('button', { name: /Bajki dla dzieci/ }))
    await user.type(screen.getByLabelText('Temat lub pomysł'), 'A fox finds a lost lantern')
    expect(screen.getByText('Sześć scen po angielsku')).toBeInTheDocument()
    expect(screen.queryByText('Film z lektorem i napisami')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Grafika Wealth Blueprint')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Przygotuj scenariusz' }))
    await screen.findByRole('heading', { name: 'Podgląd zlecenia' })
    expect(api.createProductionOrder).toHaveBeenCalledWith(expect.objectContaining({
      project: 'children_stories', productionScope: 'script_preview', deliveryMode: 'local_only',
    }))
    const payload = vi.mocked(api.createProductionOrder).mock.calls[0][0]
    expect(payload).not.toHaveProperty('imageProvider')
    expect(payload).not.toHaveProperty('imageReferenceId')
    expect(payload).not.toHaveProperty('visualStyleId')
  })

  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.fetchProductionWorkspace).mockResolvedValue(catalog)
    vi.mocked(api.fetchProductionOrders).mockResolvedValue({ orders: [] })
    vi.mocked(api.fetchProductionImageOptions).mockResolvedValue(imageOptions)
  })

  it('shows the selected local image provider, inspiration board, and lets the user choose a style', async () => {
    const user = userEvent.setup()
    vi.mocked(api.createProductionOrder).mockResolvedValue({ id: 'prod-example' } as api.ProductionJobPayload)
    renderWorkspace()
    await screen.findByRole('button', { name: /Wealth Blueprint/ })
    expect(screen.getByText('Wariant grafiki jest próbą stylu. Każdy wygenerowany obraz wymaga oceny wyglądu.')).toBeInTheDocument()
    expect(screen.getByText('Atnos może wykonać do 40 lokalnych prób ilustracji; obrazy nadal przechodzą kontrolę jakości.')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /ComfyUI \(lokalny/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Plansza referencyjna Wealth Blueprint/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Styl A Ilustracja redakcyjna/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Styl B Warstwy papieru/ })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Temat lub pomysł'), 'Build a savings habit')
    await user.type(screen.getByLabelText('Co widz ma z tego wynieść?'), 'Three useful actions')
    await user.click(screen.getByRole('radio', { name: /Styl C Rysunek techniczny/ }))
    await user.click(screen.getByRole('button', { name: 'Utwórz rolkę' }))
    await screen.findByRole('heading', { name: 'Podgląd zlecenia' })
    expect(api.createProductionOrder).toHaveBeenCalledWith(expect.objectContaining({
      project: 'wealth_blueprint', topic: 'Build a savings habit', direction: 'Three useful actions',
      deliveryMode: 'local_only', imageProvider: 'comfyui-local', imageReferenceId: 'wb-original-board',
      visualStyleId: 'C', requestKey: expect.stringContaining('workspace-'),
    }))
    expect(api.createProductionOrder).not.toHaveBeenCalledWith(expect.objectContaining({ voice: expect.anything(), voiceId: expect.anything() }))
  })

  it('does not submit a Ryan B no-paid handoff using a default paid voice', async () => {
    renderWorkspace({ productionPrefill: {
      project: 'wealth_blueprint', topic: 'Test cost constraint',
      direction: 'Głos Ryan B. Bez płatnych API.', optionalNotes: 'No publication',
    } })
    await screen.findByText(/Lokalny Ryan B jest niedostępny/)
    expect(screen.getByRole('button', { name: 'Utwórz rolkę' })).toBeDisabled()
    expect(api.createProductionOrder).not.toHaveBeenCalled()
  })

  it('binds available Ryan B, strict local policy and requested duration in the ordinary form', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchProductionWorkspace).mockResolvedValue({ ...catalog, channels: [
      { ...catalog.channels[0], local_voice: { id: 'Ryan-B', label: 'Ryan B', available: true } },
    ] })
    vi.mocked(api.createProductionOrder).mockResolvedValue({ id: 'prod-local-voice' } as api.ProductionJobPayload)
    renderWorkspace({ productionPrefill: { project: 'wealth_blueprint', topic: 'Test local duration',
      direction: 'Ryan B, około 30 sekund. Bez płatnych API.' } })
    await screen.findByRole('button', { name: /Wealth Blueprint/ })
    expect(screen.getByLabelText(/Głos dla tego filmu/)).toHaveValue('Ryan-B')
    expect(screen.getByLabelText('Docelowa długość w sekundach')).toHaveValue(30)
    expect(screen.getByText('$0.00 — wyłączone')).toBeInTheDocument()
    expect(screen.getByText('30 s (±2 s)')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Utwórz rolkę' }))
    await waitFor(() => expect(api.createProductionOrder).toHaveBeenCalledWith(expect.objectContaining({
      voiceCandidate: 'Ryan-B', providerPolicy: 'strict_local', targetDurationSeconds: 30,
      deliveryMode: 'local_only', imageProvider: 'comfyui-local',
    })))
  })

  it('prefills a Chat handoff and still creates WB with the required local image lane', async () => {
    const user = userEvent.setup()
    vi.mocked(api.createProductionOrder).mockResolvedValue({ id: 'prod-chat-handoff' } as api.ProductionJobPayload)
    renderWorkspace({ productionPrefill: {
      project: 'wealth_blueprint', topic: 'First ten thousand',
      direction: 'Direct practical savings plan', optionalNotes: 'No publication',
    } })
    await screen.findByRole('button', { name: /Wealth Blueprint/ })
    expect(screen.getByLabelText('Temat lub pomysł')).toHaveValue('First ten thousand')
    expect(screen.getByLabelText('Co widz ma z tego wynieść?')).toHaveValue('Direct practical savings plan')
    await user.click(screen.getByText('Dodatkowe wskazówki'))
    expect(screen.getByLabelText('Uwagi do obrazu, narracji lub montażu')).toHaveValue('No publication')
    await user.click(screen.getByRole('button', { name: 'Utwórz rolkę' }))
    await screen.findByRole('heading', { name: 'Podgląd zlecenia' })
    expect(api.createProductionOrder).toHaveBeenCalledWith(expect.objectContaining({
      project: 'wealth_blueprint', topic: 'First ten thousand',
      direction: 'Direct practical savings plan', optionalNotes: 'No publication',
      deliveryMode: 'local_only', imageProvider: 'comfyui-local',
      imageReferenceId: 'wb-original-board', visualStyleId: 'A',
    }))
    const payload = vi.mocked(api.createProductionOrder).mock.calls.at(-1)?.[0]
    expect(payload).not.toHaveProperty('voice')
    expect(payload).not.toHaveProperty('voiceId')
  })

  it.each([
    ['missing', 'Brakuje planszy referencyjnej Wealth Blueprint.'],
    ['invalid', 'Plansza referencyjna Wealth Blueprint jest nieczytelna lub ma nieobsługiwany format.'],
  ] as const)('explains an unavailable reference (%s) and blocks a WB order until options can be reread', async (reason, message) => {
    const user = userEvent.setup()
    vi.mocked(api.fetchProductionImageOptions).mockResolvedValue({
      ...imageOptions,
      references: [{ ...imageOptions.references[0], available: false, reason }],
    })
    renderWorkspace()
    await screen.findByText(message)
    await user.type(screen.getByLabelText('Temat lub pomysł'), 'Build a savings habit')
    expect(screen.getByRole('button', { name: 'Utwórz rolkę' })).toBeDisabled()
    expect(api.createProductionOrder).not.toHaveBeenCalled()
  })

  it('shows a malformed image-options response, blocks submission, and retries the read', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchProductionImageOptions).mockRejectedValueOnce(new Error('Odpowiedź opcji grafiki z API ma nieprawidłowy format.'))
      .mockResolvedValue(imageOptions)
    vi.mocked(api.createProductionOrder).mockResolvedValue({ id: 'prod-example' } as api.ProductionJobPayload)
    renderWorkspace()
    expect(await screen.findByRole('alert')).toHaveTextContent('Odpowiedź opcji grafiki z API ma nieprawidłowy format.')
    await user.type(screen.getByLabelText('Temat lub pomysł'), 'Build a savings habit')
    expect(screen.getByRole('button', { name: 'Utwórz rolkę' })).toBeDisabled()
    expect(api.createProductionOrder).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Sprawdź ponownie' }))
    await waitFor(() => expect(screen.getByRole('radio', { name: /ComfyUI/ })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Utwórz rolkę' }))
    await screen.findByRole('heading', { name: 'Podgląd zlecenia' })
    expect(api.createProductionOrder).toHaveBeenCalledWith(expect.objectContaining({
      imageProvider: 'comfyui-local', imageReferenceId: 'wb-original-board', visualStyleId: 'A',
    }))
  })

  it('preserves idempotency when a response is lost, but renews it for edited content', async () => {
    const user = userEvent.setup()
    vi.mocked(api.createProductionOrder).mockRejectedValue(new Error('Brak odpowiedzi serwera'))
    renderWorkspace()
    await screen.findByRole('button', { name: /Wealth Blueprint/ })
    await user.type(screen.getByLabelText('Temat lub pomysł'), 'Savings habit')
    await user.click(screen.getByRole('button', { name: 'Utwórz rolkę' }))
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: 'Utwórz rolkę' }))
    await waitFor(() => expect(api.createProductionOrder).toHaveBeenCalledTimes(2))
    const calls = vi.mocked(api.createProductionOrder).mock.calls
    expect(calls[0][0].requestKey).toBe(calls[1][0].requestKey)
    await user.type(screen.getByLabelText('Temat lub pomysł'), ' for students')
    await user.click(screen.getByRole('button', { name: 'Utwórz rolkę' }))
    await waitFor(() => expect(api.createProductionOrder).toHaveBeenCalledTimes(3))
    expect(calls[2][0].requestKey).not.toBe(calls[1][0].requestKey)
  })

  it('does not send an unsupported channel through the financial pipeline', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await user.click(await screen.findByRole('button', { name: /Bajki dla dzieci/ }))
    await user.type(screen.getByLabelText('Temat lub pomysł'), 'A friendly dragon')
    expect(screen.getByText('Profil bajek wymaga przygotowania.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Utwórz rolkę' })).toBeDisabled()
    expect(api.createProductionOrder).not.toHaveBeenCalled()
  })

  it('shows connection failure rather than a false empty queue or enabled form', async () => {
    vi.mocked(api.fetchProductionWorkspace).mockRejectedValue(new Error('offline'))
    vi.mocked(api.fetchProductionOrders).mockRejectedValue(new Error('offline'))
    renderWorkspace()
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: 'Utwórz rolkę' })).toBeDisabled()
    expect(screen.queryByText(/Tutaj pojawi się pierwszy film/)).not.toBeInTheDocument()
    expect(screen.getAllByText(/Lista nie jest aktualna/).length).toBeGreaterThan(0)
  })

  it('resumes a blocked order using its existing identifier', async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchProductionOrders).mockResolvedValue({ orders: [{ id: 'prod-existing', topic_title: 'Savings', status: 'blocked', can_resume: true, worker_live: false }] as api.ProductionJobPayload[] })
    vi.mocked(api.resumeProductionOrder).mockResolvedValue({ id: 'prod-existing' } as api.ProductionJobPayload)
    renderWorkspace()
    await user.click(await screen.findByRole('button', { name: 'Wznów' }))
    await screen.findByRole('heading', { name: 'Podgląd zlecenia' })
    expect(api.resumeProductionOrder).toHaveBeenCalledWith('prod-existing')
    expect(api.createProductionOrder).not.toHaveBeenCalled()
  })

  it.each([
    ['script_calculation_plan', 'Sprawdzanie założeń i obliczeń'],
    ['voice_qa/s03', 'Sprawdzanie wymowy'],
    ['render/s02', 'Montaż i renderowanie'],
    ['semantic_qa', 'Kontrola jakości rolki'],
    ['package', 'Przygotowywanie pakietu'],
  ])('translates the actual durable stage %s on the order list', async (stage, label) => {
    vi.mocked(api.fetchProductionOrders).mockResolvedValue({ orders: [{
      id: 'prod-live', topic_title: 'Savings', status: 'running', current_step: stage,
      worker_live: true, can_resume: false,
    }] as api.ProductionJobPayload[] })
    renderWorkspace()
    expect(await screen.findByText(`W produkcji · ${label}`)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Wznów' })).not.toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../lib/api'
import { buildProviderCard } from '../test/factories'
import { resetAllStores } from '../test/resetStores'
import { useSystemStore } from '../stores/systemStore'
import { useChatStore } from '../stores/chatStore'
import { useUIStore } from '../stores/uiStore'
import AssistantWorkspace, { assistantStatus } from './AssistantWorkspace'

vi.mock('../lib/api', async () => ({
  ...await vi.importActual<typeof import('../lib/api')>('../lib/api'),
  fetchMemoryIndexMap: vi.fn(), fetchProductionOrders: vi.fn(), fetchProductionWorkspace: vi.fn(), fetchProviderCards: vi.fn(),
}))

const map: api.MemoryIndexMap = {
  status: 'indexed', source: 'semantic_knowledge_index', document_count: 91,
  source_counts: { obsidian_note: 70, project_documentation: 21 }, truncated: true,
  last_indexed_at: '2026-10-27T10:00:00Z',
  documents: [
    { doc_id: 'a', path: 'obsidian/wb.md', title: 'Plan Wealth Blueprint', topic: 'WB', source_type: 'obsidian_note', project_id: 'wb', citation_path: 'obsidian/wb.md', source_mtime: '2026-10-26', indexed_at: '2026-10-27' },
    { doc_id: 'b', path: 'docs/runtime.md', title: 'Runtime Atnosa', topic: 'PC', source_type: 'project_documentation', project_id: null, citation_path: 'docs/runtime.md', source_mtime: '2026-10-25', indexed_at: '2026-10-27' },
  ],
}

function renderWorkspace() {
  return render(<MemoryRouter initialEntries={['/assistant']}><Routes>
    <Route path="/assistant" element={<AssistantWorkspace />} /><Route path="/chat" element={<p>Istniejący czat</p>} />
  </Routes></MemoryRouter>)
}

beforeEach(() => {
  vi.clearAllMocks()
  resetAllStores()
  vi.mocked(api.fetchMemoryIndexMap).mockResolvedValue(map)
  vi.mocked(api.fetchProductionOrders).mockResolvedValue({ orders: [] })
  vi.mocked(api.fetchProductionWorkspace).mockResolvedValue({ channels: [], note: '' })
  vi.mocked(api.fetchProviderCards).mockResolvedValue([buildProviderCard()])
  useSystemStore.getState().setNetworkSignals({ backendReachable: true })
  useSystemStore.getState().setMetrics({ model: { name: 'qwen3.5:9b', provider: 'ollama' } })
  useSystemStore.getState().setProviderCards([buildProviderCard()])
})

describe('assistant readiness is based on observed runtime', () => {
  it('never says ready before the first check, during API failure or without model readiness', () => {
    expect(assistantStatus(true, false, true, 'idle', false).key).toBe('unknown')
    expect(assistantStatus(false, true, true, 'thinking', true).key).toBe('offline')
    expect(assistantStatus(true, true, false, 'idle', false).key).toBe('unknown')
    expect(assistantStatus(true, true, true, 'offline', false).key).toBe('offline')
    expect(assistantStatus(true, true, true, 'awaiting_human', false).key).toBe('waiting')
  })
})

it('shows real indexed documents, source filter and source dates without claiming a complete vault graph', async () => {
  renderWorkspace()
  const node = await screen.findByRole('button', { name: 'Otwórz szczegóły: Plan Wealth Blueprint' })
  fireEvent.keyDown(node, { key: 'Enter' })
  expect(screen.getByRole('heading', { name: 'Plan Wealth Blueprint' })).toBeInTheDocument()
  expect(screen.getByText('obsidian/wb.md')).toBeInTheDocument()
  expect(screen.getByText(/Linie pokazują przypisanie do tematu lub folderu/)).toHaveTextContent('2 z 2')
  fireEvent.change(screen.getByLabelText('Źródło pamięci'), { target: { value: 'obsidian_note' } })
  expect(screen.queryByRole('button', { name: 'Otwórz szczegóły: Runtime Atnosa' })).not.toBeInTheDocument()
  expect(api.fetchMemoryIndexMap).toHaveBeenLastCalledWith('obsidian_note')
})

it('keeps the same conversation and only opens the existing voice panel, without starting a turn', async () => {
  useChatStore.getState().setSelectedConversation(17, 'Moja rozmowa')
  renderWorkspace()
  await screen.findByRole('button', { name: 'Otwórz szczegóły: Plan Wealth Blueprint' })
  fireEvent.click(screen.getByRole('button', { name: 'Otwórz panel głosowy' }))
  expect(useUIStore.getState().okropekPanelOpen).toBe(true)
  expect(useChatStore.getState().activeTurn).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Kontynuuj rozmowę' }))
  expect(await screen.findByText('Istniejący czat')).toBeInTheDocument()
  expect(useChatStore.getState().selectedConversationId).toBe(17)
})

it('isolates a missing memory endpoint from production and reports unknown rather than zero', async () => {
  vi.mocked(api.fetchMemoryIndexMap).mockRejectedValue(new Error('offline'))
  vi.mocked(api.fetchProductionWorkspace).mockResolvedValue({ channels: [{ key: 'wealth_blueprint', name: 'Wealth Blueprint', category: 'finance', description: 'Finanse', language: 'en', can_order: true, blockers: [], formats: ['short'] }], note: '' })
  renderWorkspace()
  expect(await screen.findByRole('alert')).toHaveTextContent('Nie udało się odczytać indeksu')
  expect(screen.getByRole('heading', { name: 'Wealth Blueprint' })).toBeInTheDocument()
  expect(screen.getByText('Brak zleceń w podglądzie')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText('Wczytuję zapisany indeks pamięci…')).not.toBeInTheDocument())
})

it('ignores a delayed refresh from the previous source filter', async () => {
  let resolvePrevious!: (value: api.MemoryIndexMap) => void
  vi.mocked(api.fetchMemoryIndexMap)
    .mockResolvedValueOnce(map)
    .mockImplementationOnce(() => new Promise(resolve => { resolvePrevious = resolve }))
    .mockResolvedValueOnce({ ...map, documents: [map.documents[1]] })
  renderWorkspace()
  await screen.findByRole('button', { name: 'Otwórz szczegóły: Plan Wealth Blueprint' })
  fireEvent.click(screen.getByRole('button', { name: 'Odśwież centrum asystenta' }))
  fireEvent.change(screen.getByLabelText('Źródło pamięci'), { target: { value: 'project_documentation' } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Odśwież centrum asystenta' })).not.toBeDisabled())
  resolvePrevious({ ...map, document_count: 999, documents: [] })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Otwórz szczegóły: Runtime Atnosa' })).toBeInTheDocument())
  expect(screen.queryByText('999')).not.toBeInTheDocument()
})

it('does not hide an offline runtime behind a stale activity title', async () => {
  useSystemStore.setState({ agentStage: 'offline', agentActivity: { title: 'Stare zadanie', detail: null, stage: 'executing' } })
  renderWorkspace()
  expect(await screen.findByRole('heading', { name: 'Model offline' })).toBeInTheDocument()
  expect(screen.getByText(/Czat zgłasza niedostępność runtime’u/)).toBeInTheDocument()
  expect(screen.queryByText('Stare zadanie')).not.toBeInTheDocument()
})

it('shows a resumed older order before an inactive order created more recently', async () => {
  vi.mocked(api.fetchProductionWorkspace).mockResolvedValue({ channels: [{ key: 'wealth_blueprint', name: 'Wealth Blueprint', category: 'finance', description: 'Finanse', language: 'en', can_order: true, blockers: [], formats: ['short'] }], note: '' })
  const base = { account_id: 'yt-wealth-blueprint', topic_id: 't', status: 'blocked',
    ready_for_review: false, ready_for_private_upload: false, steps: [], worker_live: false }
  vi.mocked(api.fetchProductionOrders).mockResolvedValue({ orders: [
    { ...base, id: 'new-inactive', created_at: '2026-10-28T10:00:00Z', updated_at: '2026-10-28T10:00:00Z' },
    { ...base, id: 'older-resumed', created_at: '2026-10-20T10:00:00Z', updated_at: '2026-10-29T10:00:00Z' },
  ] })
  renderWorkspace()
  expect(await screen.findByRole('link', { name: /Wealth Blueprint/ })).toHaveAttribute('href', '/studio/older-resumed')
  expect(screen.getByText(/ostatnich 30 zleceń/)).toBeInTheDocument()
})

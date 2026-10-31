import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchOperationalMemorySnapshot,
  previewOperationalMemoryPromotion,
  promoteOperationalMemorySelectedNote,
} from '../lib/api'
import {
  buildOperationalMemoryPromotionPreview,
  buildOperationalMemorySnapshot,
} from '../test/factories'
import { OperationalMemoryPanel } from './OperationalMemoryPanel'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    fetchOperationalMemorySnapshot: vi.fn(),
    previewOperationalMemoryPromotion: vi.fn(),
    promoteOperationalMemorySelectedNote: vi.fn(),
  }
})

const fetchOperationalMemorySnapshotMock = vi.mocked(fetchOperationalMemorySnapshot)
const previewOperationalMemoryPromotionMock = vi.mocked(previewOperationalMemoryPromotion)
const promoteOperationalMemorySelectedNoteMock = vi.mocked(promoteOperationalMemorySelectedNote)

describe('OperationalMemoryPanel', () => {
  beforeEach(() => {
    fetchOperationalMemorySnapshotMock.mockReset()
    previewOperationalMemoryPromotionMock.mockReset()
    promoteOperationalMemorySelectedNoteMock.mockReset()
    previewOperationalMemoryPromotionMock.mockResolvedValue(
      buildOperationalMemoryPromotionPreview(),
    )
  })

  it('shows a helper instead of fetching when there is no active project', () => {
    render(<OperationalMemoryPanel projectId={null} projectName={null} />)

    expect(
      screen.getByText(/Wybierz aktywny projekt w Chacie/i),
    ).toBeInTheDocument()
    expect(fetchOperationalMemorySnapshotMock).not.toHaveBeenCalled()
    expect(previewOperationalMemoryPromotionMock).not.toHaveBeenCalled()
  })

  it('renders project-scoped work items and active decisions', async () => {
    fetchOperationalMemorySnapshotMock.mockResolvedValue(buildOperationalMemorySnapshot())
    const user = userEvent.setup()

    render(<OperationalMemoryPanel projectId={1} projectName="Projekt bazowy" />)

    expect(await screen.findByText('Projekt bazowy')).toBeInTheDocument()
    expect(
      screen.getByText('Domknac selected-note triage'),
    ).toBeInTheDocument()
    const primaryCard = screen.getByTestId('operational-memory-work-item-101')
    expect(
      within(primaryCard).getByText(
        'Source note powinna byc widoczna przy work itemie, bez cofania sie do Improvement Notes.',
      ),
    ).toBeInTheDocument()
    const reviewSummary = screen.getByTestId('operational-memory-review-summary')
    expect(within(reviewSummary).getByText('Review state summary')).toBeInTheDocument()
    expect(within(reviewSummary).getByText('Unreviewed')).toBeInTheDocument()
    expect(within(reviewSummary).getByText('In review')).toBeInTheDocument()
    expect(within(reviewSummary).getByText('Reviewed')).toBeInTheDocument()
    expect(within(reviewSummary).getAllByText('1', { selector: '.type-h3' })).toHaveLength(2)
    expect(within(reviewSummary).getByText('0', { selector: '.type-h3' })).toBeInTheDocument()
    expect(within(primaryCard).getByText('unreviewed')).toBeInTheDocument()
    expect(screen.getAllByText('3 aktywne decyzje projektowe')).toHaveLength(2)
    expect(
      within(primaryCard).getByText('Nie mieszac review surface z execution semantics.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Rozpisac read-only panel w Settings.')).toBeInTheDocument()
    const blockedCard = screen.getByTestId('operational-memory-work-item-102')
    expect(within(blockedCard).getByText('in review')).toBeInTheDocument()
    expect(
      within(blockedCard).getByText('Czekamy na decyzje o zakresie kolejnego review flow.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Nie da sie wygodnie przeniesc selected note do trwalego stanu pracy projektu.'),
    ).toBeInTheDocument()
    const decisionToggle = within(primaryCard).getByRole('button', { name: 'Pokaz decyzje' })
    await user.click(decisionToggle)
    const history = within(primaryCard).getByTestId('operational-memory-decision-history-101')
    expect(history).toBeInTheDocument()
    expect(
      within(history).getByText('Nie mieszac review surface z execution semantics.'),
    ).toBeInTheDocument()
    expect(
      within(history).getByText('Potem dodac spokojny review drill-down dla decyzji.'),
    ).toBeInTheDocument()
    await user.click(within(primaryCard).getByRole('button', { name: 'Ukryj decyzje' }))
    expect(
      within(primaryCard).queryByTestId('operational-memory-decision-history-101'),
    ).not.toBeInTheDocument()
    await waitFor(() => {
      expect(fetchOperationalMemorySnapshotMock).toHaveBeenCalledWith({ projectId: 1 })
      expect(previewOperationalMemoryPromotionMock).toHaveBeenCalledWith({ projectId: 1 })
    })
  })

  it('shows an empty state when the snapshot has no work items or decisions', async () => {
    fetchOperationalMemorySnapshotMock.mockResolvedValue(
      buildOperationalMemorySnapshot({
        workItems: [],
        activeDecisions: [],
      }),
    )

    render(<OperationalMemoryPanel projectId={5} projectName="Pusty projekt" />)

    expect(await screen.findByText(/Brak zapisanego stanu pracy dla tego projektu/i)).toBeInTheDocument()
  })

  it('shows a calm error state when the snapshot fetch fails', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    fetchOperationalMemorySnapshotMock.mockRejectedValue(new Error('backend unavailable'))

    render(<OperationalMemoryPanel projectId={2} projectName="Projekt z bledem" />)

    expect(await screen.findByText(/Nie mozna wczytac Operational Memory/i)).toBeInTheDocument()
    expect(screen.getByText('backend unavailable')).toBeInTheDocument()
    expect(errorSpy).toHaveBeenCalled()
    errorSpy.mockRestore()
  })

  it('shows a calm helper when there is no selected note to promote', async () => {
    fetchOperationalMemorySnapshotMock.mockResolvedValue(buildOperationalMemorySnapshot())
    previewOperationalMemoryPromotionMock.mockResolvedValue(
      buildOperationalMemoryPromotionPreview({
        note: null,
        workItemDraft: null,
        eligible: false,
        reason: 'Brak wybranej notatki do promowania. Najpierw zaznacz jedna notatke w Improvement Notes.',
      }),
    )

    render(<OperationalMemoryPanel projectId={1} projectName="Projekt bazowy" />)

    expect(await screen.findByText(/Brak wybranej notatki do promowania/i)).toBeInTheDocument()
  })

  it('reveals a local approval step before creating a work item', async () => {
    fetchOperationalMemorySnapshotMock.mockResolvedValue(buildOperationalMemorySnapshot())
    previewOperationalMemoryPromotionMock.mockResolvedValue(
      buildOperationalMemoryPromotionPreview(),
    )
    promoteOperationalMemorySelectedNoteMock.mockResolvedValue(
      buildOperationalMemorySnapshot().workItems[0],
    )
    const user = userEvent.setup()

    render(<OperationalMemoryPanel projectId={1} projectName="Projekt bazowy" />)

    await screen.findByText('Projekt bazowy')
    await user.click(screen.getByRole('button', { name: /Przygotuj work item/i }))

    expect(screen.getByText('Work item preview')).toBeInTheDocument()
    expect(
      screen.getByText('Domknac selected note -> work item review flow'),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Potwierdz utworzenie/i }))

    await waitFor(() => {
      expect(promoteOperationalMemorySelectedNoteMock).toHaveBeenCalledWith({
        projectId: 1,
        noteId: 7,
      })
    })
    expect(await screen.findByText('Work item zapisany w Operational Memory.')).toBeInTheDocument()
  })

  it('cancels the approval step without creating a work item', async () => {
    fetchOperationalMemorySnapshotMock.mockResolvedValue(buildOperationalMemorySnapshot())
    previewOperationalMemoryPromotionMock.mockResolvedValue(
      buildOperationalMemoryPromotionPreview(),
    )
    const user = userEvent.setup()

    render(<OperationalMemoryPanel projectId={1} projectName="Projekt bazowy" />)

    await screen.findByText('Projekt bazowy')
    await user.click(screen.getByRole('button', { name: /Przygotuj work item/i }))
    expect(screen.getByText('Work item preview')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Anuluj/i }))

    expect(screen.queryByText('Work item preview')).not.toBeInTheDocument()
    expect(promoteOperationalMemorySelectedNoteMock).not.toHaveBeenCalled()
  })

  it('shows a local error when the promotion create call fails', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    fetchOperationalMemorySnapshotMock.mockResolvedValue(buildOperationalMemorySnapshot())
    previewOperationalMemoryPromotionMock.mockResolvedValue(
      buildOperationalMemoryPromotionPreview(),
    )
    promoteOperationalMemorySelectedNoteMock.mockRejectedValue(
      new Error('promotion blocked'),
    )
    const user = userEvent.setup()

    render(<OperationalMemoryPanel projectId={1} projectName="Projekt bazowy" />)

    await screen.findByText('Projekt bazowy')
    await user.click(screen.getByRole('button', { name: /Przygotuj work item/i }))
    await user.click(screen.getByRole('button', { name: /Potwierdz utworzenie/i }))

    expect(await screen.findByText('promotion blocked')).toBeInTheDocument()
    expect(errorSpy).toHaveBeenCalled()
    errorSpy.mockRestore()
  })

  it('does not render execution or connector language', async () => {
    fetchOperationalMemorySnapshotMock.mockResolvedValue(buildOperationalMemorySnapshot())

    render(<OperationalMemoryPanel projectId={1} projectName="Projekt bazowy" />)

    await screen.findByText('Projekt bazowy')
    expect(screen.queryByText(/Run/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Execute/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Create issue/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Send to Codex/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Mission Queue/i)).not.toBeInTheDocument()
  })

  it('keeps work-item context optional when a card has no source note or decision context', async () => {
    fetchOperationalMemorySnapshotMock.mockResolvedValue(
      buildOperationalMemorySnapshot({
        workItems: [
          {
            ...buildOperationalMemorySnapshot().workItems[0],
            id: 501,
            title: 'Samodzielny work item',
            reviewState: 'reviewed',
            blockedReason: null,
            sourceNote: null,
            decisionContext: null,
          },
          {
            ...buildOperationalMemorySnapshot().workItems[1],
            id: 502,
            title: 'Summary only item',
            blockedReason: null,
            sourceNote: null,
            decisionContext: {
              ...buildOperationalMemorySnapshot().workItems[1].decisionContext!,
              recentEntries: [],
            },
          },
        ],
      }),
    )

    render(<OperationalMemoryPanel projectId={1} projectName="Projekt bazowy" />)

    const card = await screen.findByTestId('operational-memory-work-item-501')
    expect(card).toBeInTheDocument()
    expect(card).not.toHaveTextContent('Source note context')
    expect(card).not.toHaveTextContent('Decision context')
    const summaryOnlyCard = screen.getByTestId('operational-memory-work-item-502')
    expect(summaryOnlyCard).toHaveTextContent('Decision context')
    expect(
      within(summaryOnlyCard).queryByRole('button', { name: /Pokaz decyzje/i }),
    ).not.toBeInTheDocument()
    const reviewSummary = screen.getByTestId('operational-memory-review-summary')
    expect(within(reviewSummary).getByText('Reviewed')).toBeInTheDocument()
  })
})

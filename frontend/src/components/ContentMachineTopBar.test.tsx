import { type ReactElement } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import { ContentMachineTopBar } from './ContentMachineTopBar'
import { useAccountStore } from '../stores/accountStore'
import { resetAllStores } from '../test/resetStores'

function renderTopBar(ui: ReactElement, initialPath = '/settings') {
  return render(<MemoryRouter initialEntries={[initialPath]}>{ui}</MemoryRouter>)
}

describe('ContentMachineTopBar personal desktop chrome', () => {
  beforeEach(() => {
    window.localStorage.clear()
    resetAllStores()
  })

  it('keeps topbar as local operator chrome without commercial controls', () => {
    renderTopBar(<ContentMachineTopBar />)

    expect(screen.getByText(/Kolejka lokalna/i)).toBeInTheDocument()
    expect(screen.getByText(/Ostatni zapis/i)).toBeInTheDocument()
    expect(screen.getByText(/Operator PC · lokalnie/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Uruchom pipeline/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/paper \/ read-only/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Trading live/i)).not.toBeInTheDocument()
  })

  it('shows chat-first chrome on /chat', () => {
    renderTopBar(<ContentMachineTopBar />, '/chat')
    expect(screen.getByText(/Rozmowa z Atnosem/i)).toBeInTheDocument()
    expect(screen.getByText(/Lokalny agent · PC · produkcja rolek/i)).toBeInTheDocument()
  })

  it('keeps studio chrome independent from selected project', () => {
    useAccountStore.getState().setActiveAccount('investment:portfolio-alpha')
    renderTopBar(<ContentMachineTopBar studio />, '/studio/job-1')
    expect(screen.getByText('Studio rolek')).toBeInTheDocument()
    expect(screen.getByText('Zlecenie lokalne')).toBeInTheDocument()
    expect(screen.queryByText(/Kolejka lokalna/i)).not.toBeInTheDocument()
  })
})

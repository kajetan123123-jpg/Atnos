import { beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sidebar } from './Sidebar'
import { useAccountStore } from '../stores/accountStore'
import { resetAllStores } from '../test/resetStores'

describe('Sidebar', () => {
  beforeEach(() => {
    window.localStorage.clear()
    resetAllStores()
  })

  it('renders projects and switches the active project', async () => {
    const user = userEvent.setup()
    const account = useAccountStore.getState().addAccount({
      type: 'tiktok',
      name: 'TikTok Growth',
      niche: 'growth hacks',
    })

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.getByText('Projekty')).toBeInTheDocument()
    expect(screen.getByText('Asystent i studio')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Centrum' })).toBeInTheDocument()
    expect(screen.getByText('Wealth Blueprint')).toBeInTheDocument()
    expect(screen.getByText('Dark Chronicles')).toBeInTheDocument()
    expect(screen.getByText('Sigma Squad')).toBeInTheDocument()
    expect(screen.getByText('Synthetic Model Lab')).toBeInTheDocument()
    expect(screen.getByText('Portfolio Alpha')).toBeInTheDocument()
    expect(screen.getByText('TikTok Growth')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Czat', current: 'page' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Produkcja' })).not.toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Ustawienia' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Panel' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Analiza' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Projekty' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Aktywny projekt' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Kanał' })).not.toBeInTheDocument()
    expect(screen.getAllByText(/LOKALN/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Kanał lokalny · produkcja na E:/i).length).toBeGreaterThan(0)
    expect(screen.queryByText('PRZYCHÓD 30D')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /TikTok Growth/i }))
    expect(useAccountStore.getState().activeAccountId).toBe(account.id)
  })

  it('adds a new project from the sidebar form', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Sidebar />
      </MemoryRouter>,
    )

    await user.click(screen.getByRole('button', { name: /Dodaj projekt/i }))
    await user.type(screen.getByLabelText('Nazwa projektu'), 'YT Stoicyzm')
    await user.click(screen.getByRole('button', { name: 'Dodaj' }))

    expect(useAccountStore.getState().accounts.some((account) => account.name === 'YT Stoicyzm')).toBe(true)
  })
})

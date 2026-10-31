import { beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { resetAllStores } from './test/resetStores'

describe('Content Machine routing', () => {
  beforeEach(() => {
    window.localStorage.clear()
    resetAllStores()
  })

  it('opens chat as the primary surface on root', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getAllByText('Nowa rozmowa').length).toBeGreaterThan(0)
    })
    expect(screen.queryByText('Status pipeline')).not.toBeInTheDocument()
  })

  it('redirects the legacy dashboard route to chat', async () => {
    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <App />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getAllByText('Nowa rozmowa').length).toBeGreaterThan(0)
    })
    expect(screen.queryByText('Status pipeline')).not.toBeInTheDocument()
  })

  it('renders conversations route', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <App />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getAllByText('Nowa rozmowa').length).toBeGreaterThan(0)
    })
    expect(screen.queryByText('Status pipeline')).not.toBeInTheDocument()
  })

  it('keeps chat controls collapsed by default and expands them on demand', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <App />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Przywróć panele rozmowy/i })).toBeInTheDocument()
    })
    expect(screen.queryByText('USTAWIENIA ROZMOWY')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Przywróć panele rozmowy/i }))

    expect(screen.getByText('USTAWIENIA ROZMOWY')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Tryb czytania/i })).toBeInTheDocument()
  })

  it('renders content-machine settings', async () => {
    render(
      <MemoryRouter initialEntries={['/settings']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByText('Ustawienia Atnosa')).toBeInTheDocument()
    expect(screen.getByText('Konta')).toBeInTheDocument()
    expect(screen.getByText('Macierz konfiguracji lokalnej')).toBeInTheDocument()
    expect(screen.getAllByText('Synthetic Model Lab').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Portfolio Alpha').length).toBeGreaterThan(0)

    await userEvent.click(screen.getAllByText('Synthetic Model Lab')[0])
    expect(screen.getByText('Synthetic creator / modelka')).toBeInTheDocument()
    expect(screen.getByText('Konto twórcy podłączone')).toBeInTheDocument()

    await userEvent.click(screen.getAllByText('Portfolio Alpha')[0])
    expect(screen.getAllByText('Portfel inwestycyjny').length).toBeGreaterThan(0)
    expect(screen.getByText('Broker read-only przygotowany')).toBeInTheDocument()
    expect(screen.getByText('Centrum podłączeń: Portfolio Alpha')).toBeInTheDocument()
    expect(screen.getByText('Następny krok')).toBeInTheDocument()
    expect(screen.getByText('Do podłączenia w tym projekcie')).toBeInTheDocument()
    expect(screen.getByText('Zrodla statusow: backend snapshot + jawny fallback lokalny')).toBeInTheDocument()
    expect(screen.getAllByText(/Zrodlo:/).length).toBeGreaterThan(0)
    expect(screen.getByText('Broker demo')).toBeInTheDocument()
    expect(screen.getByText('Broker demo/read-only')).toBeInTheDocument()
    expect(screen.getAllByText('Vault brokera').length).toBeGreaterThan(0)
    expect(screen.getByText('XTB read-only')).toBeInTheDocument()
    expect(screen.getByText('Market data / XTB')).toBeInTheDocument()
    expect(screen.getByText('Dane i nauka')).toBeInTheDocument()
    expect(screen.getByText('Ryzyko i budżet')).toBeInTheDocument()
    expect(screen.getByText('Live trading')).toBeInTheDocument()
    expect(screen.getByText('Paper trading only')).toBeInTheDocument()
    expect(screen.getByText('URL brokera')).toBeInTheDocument()
    expect(screen.getByText('Źródło cen autopilota')).toBeInTheDocument()
    expect(screen.getByText('synthetic_fallback')).toBeInTheDocument()
    expect(screen.getByText('Vault ref loginu i hasła')).toBeInTheDocument()
    expect(screen.getByText('Kursy i nauka dla Atnosa')).toBeInTheDocument()
    expect(screen.getByText('Metoda płatności ref')).toBeInTheDocument()
    expect(screen.queryByText('YouTube OAuth')).not.toBeInTheDocument()
    expect(screen.queryByText('Prywatny upload')).not.toBeInTheDocument()
    expect(screen.queryByText('Public quality')).not.toBeInTheDocument()
    expect(screen.queryByText('Public upload odblokowany')).not.toBeInTheDocument()
    expect(screen.queryByText('Gotowe prywatne uploady')).not.toBeInTheDocument()
  })

  it('renders keyboard-accessible settings tabs without clipping on compact viewports', async () => {
    const user = userEvent.setup()
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      writable: true,
      value: 390,
    })
    window.dispatchEvent(new Event('resize'))

    render(
      <MemoryRouter initialEntries={['/settings']}>
        <App />
      </MemoryRouter>,
    )

    const tabList = screen.getByRole('tablist', { name: 'Sekcje ustawień' })
    expect(tabList).toHaveClass('overflow-x-auto', 'overscroll-x-contain', 'sm:flex-wrap')
    const accountsTab = screen.getByRole('tab', { name: 'Konta' })
    expect(accountsTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Dostępy' })).toHaveAttribute('aria-selected', 'false')

    accountsTab.focus()
    await user.keyboard('{ArrowRight}')

    expect(screen.getByRole('tab', { name: 'Pipeline' })).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Pipeline' })).toHaveAttribute('aria-selected', 'true')
  })

  it('renders access readiness tab in content-machine settings', async () => {
    render(
      <MemoryRouter initialEntries={['/settings']}>
        <App />
      </MemoryRouter>,
    )

    await userEvent.click(screen.getByRole('tab', { name: 'Dostępy' }))

    expect(screen.getByText('Dostępy Atnosa')).toBeInTheDocument()
    expect(screen.getByText('Sterowanie myszą i klawiaturą')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test screenshot' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test myszy' })).toBeInTheDocument()
  })

  it('renders runtime repair actions from health sources instead of fake readiness', async () => {
    render(
      <MemoryRouter initialEntries={['/settings']}>
        <App />
      </MemoryRouter>,
    )

    await userEvent.click(screen.getByRole('tab', { name: 'Runtime' }))

    expect(screen.getByText('Runtime & AI Delegation')).toBeInTheDocument()
    expect(screen.getByText('Następne akcje runtime')).toBeInTheDocument()
    expect(screen.getByText('Runtime health')).toBeInTheDocument()
    expect(screen.getByText('AI workers')).toBeInTheDocument()
    expect(screen.getByText('Local host control')).toBeInTheDocument()
    expect(screen.getByText('źródło: /api/runtime/health')).toBeInTheDocument()
  })

  it('renders intelligence dashboard route', () => {
    render(
      <MemoryRouter initialEntries={['/intelligence']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByText('Realne sygnały rynku')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Trendy' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Filtr niszy')).toBeInTheDocument()
  })

  it('renders project command center route', () => {
    render(
      <MemoryRouter initialEntries={['/projects']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByText('Centrum kontroli projektów')).toBeInTheDocument()
    expect(screen.getAllByText('Wealth Blueprint').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Synthetic Model Lab').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Portfolio Alpha').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Public-quality gate').length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: 'Sprawdź głos' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: 'Sprawdź backend wideo' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: 'Utwórz style guide' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /Otwórz kontrolę/i }).length).toBeGreaterThan(0)
  })

  it('renders project control route for a single project', () => {
    render(
      <MemoryRouter initialEntries={['/projects/youtube%3Aatnos%40int.pl']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getAllByText('Wealth Blueprint').length).toBeGreaterThan(0)
    expect(screen.getByText(/Project Control/)).toBeInTheDocument()
    expect(screen.getByText('Dostępy i blokady')).toBeInTheDocument()
    expect(screen.getByText('Akcje projektu')).toBeInTheDocument()
    expect(screen.getByText('Dystrybucja: YouTube / TikTok / Instagram')).toBeInTheDocument()
    expect(screen.getByText('Audyt wizualnej jakości i auto-naprawa')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Uruchom research' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generuj 20 tematów' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Uruchom audyt jakości' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Uruchom auto-naprawę' })).toBeInTheDocument()
  })

  it('renders investment project paper autopilot controls', () => {
    render(
      <MemoryRouter initialEntries={['/projects/investment%3Aportfolio-alpha']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getAllByText('Portfolio Alpha').length).toBeGreaterThan(0)
    expect(screen.getByText('Kokpit inwestycyjny')).toBeInTheDocument()
    expect(screen.getByText('Paper autopilot na danych rynku')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Uruchom paper autopilot' })).toBeInTheDocument()
    expect(screen.getAllByText('Realne zlecenia').length).toBeGreaterThan(0)
    expect(screen.getByText('Harmonogram')).toBeInTheDocument()
    expect(screen.getByText('Dane rynku i broker')).toBeInTheDocument()
    expect(screen.getByText('Edukacja i strategia')).toBeInTheDocument()
    expect(screen.getByText('Zweryfikowane źródła edukacji')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dodaj i sprawdź źródła' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pobierz cytowany digest' })).toBeInTheDocument()
    expect(screen.getByText('Broker access')).toBeInTheDocument()
    expect(screen.getByText('Market data')).toBeInTheDocument()
    expect(screen.getAllByText('Research rynku').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Watchlista').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Paper trading').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Karta / bank').length).toBeGreaterThan(0)
    expect(screen.queryByText('Tematy')).not.toBeInTheDocument()
    expect(screen.queryByText('Produkcja i upload')).not.toBeInTheDocument()
    expect(screen.queryByText('Prywatny upload')).not.toBeInTheDocument()
    expect(screen.queryByText('Publiczny upload')).not.toBeInTheDocument()
    expect(screen.queryByText('Dystrybucja: YouTube / TikTok / Instagram')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start produkcji' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Wrzu/ })).not.toBeInTheDocument()
  })

  it('opens platform account connector form from project control', async () => {
    render(
      <MemoryRouter initialEntries={['/projects/youtube%3Aatnos%40int.pl']}>
        <App />
      </MemoryRouter>,
    )

    await userEvent.click(screen.getAllByRole('button', { name: 'Podepnij konto' })[0])

    expect(screen.getByText(/Podłącz konto:/)).toBeInTheDocument()
    expect(screen.getByLabelText('Login / e-mail / nazwa konta')).toBeInTheDocument()
    expect(screen.getByLabelText('Hasło lub token sesji opcjonalnie')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Zapisz konto platformy' })).toBeInTheDocument()
  })
})

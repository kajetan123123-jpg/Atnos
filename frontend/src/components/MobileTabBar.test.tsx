import { beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import { MobileTabBar } from './MobileTabBar'
import { resetAllStores } from '../test/resetStores'

describe('MobileTabBar', () => {
  beforeEach(() => {
    resetAllStores()
  })

  it('renders chat, production and settings and marks the active route', () => {
    render(
      <MemoryRouter initialEntries={['/settings']}>
        <MobileTabBar />
      </MemoryRouter>,
    )

    expect(screen.getByRole('button', { name: 'Czat' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Produkcja' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ustawienia', current: 'page' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Panel' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Analiza' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Projekty' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pipeline' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Agent' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Telemetria' })).not.toBeInTheDocument()
  })

  it('marks Czat active on /chat without highlighting Produkcja', () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <MobileTabBar />
      </MemoryRouter>,
    )

    expect(screen.getByRole('button', { name: 'Czat', current: 'page' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Produkcja' })).not.toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('marks Produkcja active on studio routes without highlighting Czat', () => {
    render(
      <MemoryRouter initialEntries={['/studio/job-42']}>
        <MobileTabBar />
      </MemoryRouter>,
    )

    expect(screen.getByRole('button', { name: 'Produkcja', current: 'page' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Czat' })).not.toHaveAttribute('aria-current', 'page')
  })
})

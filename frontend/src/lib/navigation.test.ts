import { describe, expect, it } from 'vitest'
import { isNavRouteActive, APP_NAV_ITEMS } from './navigation'

describe('navigation', () => {
  it('keeps Czat first and separates active states', () => {
    expect(APP_NAV_ITEMS.map((item) => item.label)).toEqual(['Czat', 'Produkcja', 'Ustawienia'])
    expect(isNavRouteActive('/chat', '/chat')).toBe(true)
    expect(isNavRouteActive('/chat/extra', '/chat')).toBe(true)
    expect(isNavRouteActive('/chat', '/create')).toBe(false)
    expect(isNavRouteActive('/', '/create')).toBe(false)
    expect(isNavRouteActive('/dashboard', '/create')).toBe(false)
    expect(isNavRouteActive('/create', '/create')).toBe(true)
    expect(isNavRouteActive('/studio/job-1', '/create')).toBe(true)
    expect(isNavRouteActive('/studio/job-1', '/chat')).toBe(false)
    expect(isNavRouteActive('/settings', '/settings')).toBe(true)
    expect(isNavRouteActive('/projects', '/settings')).toBe(true)
  })
})

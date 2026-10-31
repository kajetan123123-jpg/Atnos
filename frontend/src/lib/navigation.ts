import { Clapperboard, MessageSquareText, Orbit, Settings } from 'lucide-react'

export const APP_NAV_ITEMS = [
  {
    icon: Orbit,
    label: 'Centrum',
    route: '/assistant',
    description: 'Pamięć, stan asystenta i bieżąca praca',
  },
  {
    icon: MessageSquareText,
    label: 'Czat',
    route: '/chat',
    description: 'Rozmowa z Atnosem i polecenia na PC',
  },
  {
    icon: Clapperboard,
    label: 'Produkcja',
    route: '/create',
    description: 'Tworzenie rolek, opisów i miniaturek',
  },
  {
    icon: Settings,
    label: 'Ustawienia',
    route: '/settings',
    description: 'Kanały, głos, połączenia i jakość',
  },
] as const

export type AppNavRoute = (typeof APP_NAV_ITEMS)[number]['route']

const SETTINGS_CONTEXT_ROUTES = [
  '/settings',
  '/projects',
  '/intelligence',
  '/channel-setup',
] as const

export function isNavRouteActive(pathname: string, route: AppNavRoute) {
  if (route === '/assistant') return pathname === '/assistant'
  if (route === '/chat') {
    return pathname === '/chat' || pathname.startsWith('/chat/')
  }

  if (route === '/create') {
    // Root redirects to the assistant center, never to production.
    return pathname === '/create' || pathname.startsWith('/studio/')
  }

  return SETTINGS_CONTEXT_ROUTES.some(
    (contextRoute) => pathname === contextRoute || pathname.startsWith(`${contextRoute}/`),
  )
}

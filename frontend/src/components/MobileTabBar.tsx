import { useLocation, useNavigate } from 'react-router-dom'
import { APP_NAV_ITEMS, isNavRouteActive } from '../lib/navigation'

export function MobileTabBar() {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <nav
      className="shell-bottom-nav fixed inset-x-0 bottom-0 z-page lg:hidden"
      aria-label="Nawigacja mobilna"
    >
      <div
        className="grid h-16"
        style={{ gridTemplateColumns: `repeat(${APP_NAV_ITEMS.length}, minmax(0, 1fr))` }}
      >
        {APP_NAV_ITEMS.map((item) => {
          const Icon = item.icon
          const active = isNavRouteActive(location.pathname, item.route)

          return (
            <button
              key={item.route}
              type="button"
              onClick={() => navigate(item.route)}
              aria-current={active ? 'page' : undefined}
              aria-label={item.label}
              className={[
                'relative flex flex-col items-center justify-center gap-1 text-text-secondary outline-none transition-colors duration-fast',
                active
                  ? 'bg-accent-primary/10 text-accent-primary'
                  : 'hover:bg-elevated/40 hover:text-text-primary',
              ].join(' ')}
            >
              {active ? (
                <span
                  className="absolute inset-x-3 top-0 h-0.5 rounded-full bg-accent-primary shadow-glow-soft"
                  aria-hidden
                />
              ) : null}
              <Icon size={18} strokeWidth={active ? 1.85 : 1.5} />
              <span className="max-w-full truncate px-1 text-[10px] leading-3">{item.label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}

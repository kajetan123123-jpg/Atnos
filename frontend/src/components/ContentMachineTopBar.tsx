import { useAccountStore } from '../stores/accountStore'
import { useContentMachineStore } from '../stores/contentMachineStore'
import { Link, useLocation } from 'react-router-dom'

const TYPE_LABELS: Record<string, string> = {
  youtube: 'YouTube',
  instagram_reels: 'Instagram Reels',
  tiktok: 'TikTok',
  onlyfans: 'OnlyFans',
  investment: 'Inwestycje',
  custom: 'Własne',
}

function formatLastUpload(value?: string) {
  if (!value || value === 'Brak uploadu') {
    return 'Brak uploadu'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleString('pl-PL')
}

export function ContentMachineTopBar({ studio = false }: { studio?: boolean }) {
  const location = useLocation()
  const accounts = useAccountStore((state) => state.accounts)
  const activeAccountId = useAccountStore((state) => state.activeAccountId)
  const activeAccount = accounts.find((account) => account.id === activeAccountId) ?? accounts[0]
  const queue = useContentMachineStore((state) => state.queue)

  const accountQueue = activeAccount ? queue[activeAccount.id] ?? [] : []
  const onChat =
    location.pathname === '/chat' || location.pathname.startsWith('/chat/')

  if (location.pathname === '/assistant') {
    return <header className="shell-topbar fixed left-[220px] right-0 top-0 z-hud flex h-14 items-center justify-between px-6">
      <span className="text-sm font-medium text-text-secondary">Centrum asystenta</span>
      <Link to="/chat" className="text-sm text-text-primary transition hover:text-accent-primary">Przejdź do rozmowy →</Link>
    </header>
  }

  if (onChat) {
    return (
      <header className="shell-topbar fixed left-[220px] right-0 top-0 z-hud flex h-14 items-center justify-between px-6">
        <div className="min-w-0">
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-tertiary">
            Pierwszoplanowy czat
          </div>
          <div className="truncate font-display text-sm font-semibold uppercase tracking-[0.1em] text-text-primary">
            Rozmowa z Atnosem
          </div>
        </div>
        <div className="flex items-center gap-5 text-xs text-text-secondary">
          <span>Lokalny agent · PC · produkcja rolek</span>
          <Link
            to="/create"
            className="rounded-pill border border-border-default px-3 py-1 text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
          >
            Produkcja
          </Link>
        </div>
      </header>
    )
  }

  if (location.pathname === '/create') {
    return (
      <header className="shell-topbar fixed left-[220px] right-0 top-0 z-hud flex h-14 items-center justify-between px-6">
        <span className="text-sm font-semibold text-text-primary">Studio produkcji</span>
        <div className="flex items-center gap-5 text-xs text-text-secondary">
          <span>Filmy po angielsku · Pliki na E:</span>
          <Link
            to="/chat"
            className="rounded-pill border border-border-default px-3 py-1 text-text-secondary transition hover:border-accent-primary hover:text-accent-primary"
          >
            Rozmowa z Atnosem
          </Link>
        </div>
      </header>
    )
  }

  return (
    <header className="shell-topbar fixed left-[220px] right-0 top-0 z-hud h-14">
      <div className="flex h-full items-center justify-between gap-4 px-4">
        <div className="min-w-0">
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-tertiary">
            {studio
              ? 'Podgląd produkcji'
              : `Projekt / ${activeAccount ? TYPE_LABELS[activeAccount.type] : 'brak projektu'}`}
          </div>
          <div className="truncate font-display text-sm font-semibold uppercase tracking-[0.1em] text-text-primary">
            {studio ? 'Studio rolek' : activeAccount?.name ?? 'Brak aktywnego projektu'}
          </div>
        </div>
        {studio ? null : (
          <div className="hidden items-center gap-5 font-mono text-[11px] uppercase text-text-secondary lg:flex">
            <span>
              Kolejka lokalna <strong className="text-text-primary">{accountQueue.length}</strong>
            </span>
            <span>
              Ostatni zapis{' '}
              <strong className="text-text-primary">
                {formatLastUpload(activeAccount?.stats.lastUpload)}
              </strong>
            </span>
          </div>
        )}
        <div
          className="rounded-md border border-border-default bg-panel/70 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-text-tertiary backdrop-blur-md"
          title={
            studio
              ? 'Ustawienia tego zlecenia są zapisane przy jobie.'
              : 'Lokalny operator PC — sterowanie przez czat i Studio.'
          }
        >
          {studio ? 'Zlecenie lokalne' : 'Operator PC · lokalnie'}
        </div>
      </div>
    </header>
  )
}

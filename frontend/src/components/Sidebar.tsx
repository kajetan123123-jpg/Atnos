import { useEffect, useRef, useState } from 'react'
import { BarChart3, Film, Plus, RadioTower, Trash2 } from 'lucide-react'
import { NavLink, useLocation } from 'react-router-dom'
import { APP_NAV_ITEMS, isNavRouteActive } from '../lib/navigation'
import { useAccountStore } from '../stores/accountStore'
import type { Account, AccountStatus, AccountType } from '../types/contentMachine'

const STATUS_LABELS: Record<AccountStatus, string> = {
  live: 'AKTYWNE · LOKALNE',
  paused: 'PAUZA · LOKALNA',
  error: 'BŁĄD · LOKALNY',
  idle: 'IDLE · LOKALNY',
}

const STATUS_CLASSES: Record<AccountStatus, string> = {
  live: 'border-sem-success/40 bg-sem-success/10 text-sem-success',
  paused: 'border-text-tertiary/30 bg-elevated/60 text-text-secondary',
  error: 'border-sem-error/40 bg-sem-error/10 text-sem-error',
  idle: 'border-text-tertiary/30 bg-elevated/60 text-text-secondary',
}

const ACCOUNT_TYPES: Array<{ value: AccountType; label: string }> = [
  { value: 'youtube', label: 'YouTube' },
  { value: 'instagram_reels', label: 'Instagram Reels' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'onlyfans', label: 'OnlyFans' },
  { value: 'investment', label: 'Inwestycje' },
  { value: 'custom', label: 'Własne' },
]

function PlatformIcon({ type }: { type: AccountType }) {
  if (type === 'investment') {
    return <BarChart3 size={16} />
  }
  if (type === 'youtube' || type === 'instagram_reels' || type === 'tiktok') {
    return <Film size={16} />
  }
  return <RadioTower size={16} />
}

function AccountCard({
  account,
  active,
  onSelect,
}: {
  account: Account
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className={[
        'shell-magnetic group w-full rounded-md border p-3 text-left',
        active
          ? 'border-accent-primary/50 bg-accent-primary/10'
          : 'border-border-default bg-panel/70 hover:border-accent-primary/30 hover:bg-elevated/70',
      ].join(' ')}
    >
      <div className="flex min-w-0 items-start gap-2">
        <span className="flex min-w-0 flex-1 items-center gap-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-pill border border-border-default bg-void text-accent-primary">
            <PlatformIcon type={account.type} />
          </span>
          <span className="min-w-0">
            <span className="block truncate type-body-sm font-semibold uppercase tracking-wider text-text-primary">
              {account.name}
            </span>
            <span className="mt-1 block truncate type-mono-sm uppercase text-text-tertiary">
              {account.type.replace(/_/g, ' ')}
            </span>
          </span>
        </span>
      </div>
      <div className="mt-2 border-t border-border-default pt-2">
        <span className={`block w-full border px-2 py-1 text-center type-mono-sm ${STATUS_CLASSES[account.status]}`}>
          {STATUS_LABELS[account.status]}
        </span>
      </div>
      <div className="mt-2 type-mono-sm text-text-tertiary">
        Kanał lokalny · produkcja na E:
      </div>
    </button>
  )
}

export function Sidebar() {
  const location = useLocation()
  const accounts = useAccountStore((state) => state.accounts)
  const activeAccountId = useAccountStore((state) => state.activeAccountId)
  const setActiveAccount = useAccountStore((state) => state.setActiveAccount)
  const addAccount = useAccountStore((state) => state.addAccount)
  const removeAccount = useAccountStore((state) => state.removeAccount)
  const [adding, setAdding] = useState(false)
  const [draftName, setDraftName] = useState('')
  const [draftType, setDraftType] = useState<AccountType>('youtube')
  const accountListRef = useRef<HTMLDivElement>(null)

  const activeAccount = accounts.find((account) => account.id === activeAccountId)
  const isProductionWorkspace = location.pathname === '/create' || location.pathname.startsWith('/studio/')

  useEffect(() => {
    if (!adding) return
    const frame = window.requestAnimationFrame(() => {
      const container = accountListRef.current
      if (container) container.scrollTop = container.scrollHeight
    })
    return () => window.cancelAnimationFrame(frame)
  }, [adding])

  const submitAccount = () => {
    const created = addAccount({
      type: draftType,
      name: draftName || ACCOUNT_TYPES.find((item) => item.value === draftType)?.label || 'Nowe konto',
      niche: 'content monetization',
      automationMode: 'manual',
    })
    setActiveAccount(created.id)
    setDraftName('')
    setAdding(false)
  }

  return (
    <aside className="fixed bottom-0 left-0 top-0 z-sidebar flex w-[220px] flex-col border-r border-border-subtle bg-shell/90 backdrop-blur-xl">
      <div className="border-b border-border-subtle px-4 py-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="type-h3 font-normal uppercase tracking-tight text-text-primary">
              ATNOS
            </div>
            <div className="mt-1 type-mono-sm uppercase tracking-widest text-text-tertiary">
              Asystent i studio
            </div>
          </div>
          <Film size={18} className="text-accent-primary" />
        </div>
      </div>

      <div ref={accountListRef} className="flex-1 overflow-y-auto px-3 py-4">
        {isProductionWorkspace ? (
          <div className="space-y-6 px-2 py-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-text-tertiary">Tworzenie filmu</p>
            <ol className="space-y-6 text-sm text-text-secondary">
              <li><span className="mb-1 block font-semibold text-text-primary">01 · Pomysł</span>Wybierz kanał i opisz historię.</li>
              <li><span className="mb-1 block font-semibold text-text-primary">02 · Produkcja</span>Atnos przygotuje obraz, głos i montaż.</li>
              <li><span className="mb-1 block font-semibold text-text-primary">03 · Odbiór</span>Obejrzyj film i sprawdź wyniki jakości.</li>
            </ol>
            <p className="border-t border-border-default pt-6 text-xs leading-5 text-text-tertiary">Konfigurację narzędzi i kanałów znajdziesz w ustawieniach.</p>
          </div>
        ) : <>
        <div className="mb-2 type-label text-text-tertiary">
          Projekty
        </div>
        <div className="space-y-2">
          {accounts.map((account) => (
            <AccountCard
              key={account.id}
              account={account}
              active={account.id === activeAccountId}
              onSelect={() => setActiveAccount(account.id)}
            />
          ))}
        </div>

        {adding ? (
          <div className="mt-3 border border-border-default bg-panel p-3">
            <label htmlFor="account-name" className="block type-mono-sm uppercase text-text-tertiary">
              Nazwa projektu
            </label>
            <input
              id="account-name"
              value={draftName}
              onChange={(event) => setDraftName(event.target.value)}
              className="mt-1 w-full border border-border-default bg-void px-2 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
              placeholder="np. Wealth Blueprint"
            />
            <label htmlFor="account-type" className="mt-3 block type-mono-sm uppercase text-text-tertiary">
              Typ projektu
            </label>
            <select
              id="account-type"
              value={draftType}
              onChange={(event) => setDraftType(event.target.value as AccountType)}
              className="mt-1 w-full border border-border-default bg-void px-2 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
            >
              {ACCOUNT_TYPES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={submitAccount}
                className="flex-1 border border-accent-primary bg-accent-primary/10 px-2 py-2 type-label text-accent-primary"
              >
                Dodaj
              </button>
              <button
                type="button"
                onClick={() => setAdding(false)}
                className="border border-border-default px-2 py-2 type-label text-text-secondary"
              >
                Anuluj
              </button>
            </div>
          </div>
        ) : null}
        </>}
      </div>

      <div className="space-y-2 border-t border-border-subtle p-3">
        {APP_NAV_ITEMS.map((item) => {
          const Icon = item.icon
          const active = isNavRouteActive(location.pathname, item.route)

          return (
            <NavLink
              key={item.route}
              to={item.route}
              aria-current={active ? 'page' : undefined}
              className={[
                'flex items-center gap-2 rounded-md border px-3 py-2 type-label transition',
                active
                  ? 'border-accent-primary/50 bg-accent-primary/10 text-accent-primary'
                  : 'border-border-default text-text-secondary hover:border-accent-primary/30 hover:text-text-primary',
              ].join(' ')}
            >
              <Icon size={15} />
              {item.label}
            </NavLink>
          )
        })}
        {!isProductionWorkspace ? <button
          type="button"
          onClick={() => setAdding(true)}
          className="flex w-full items-center justify-center gap-2 rounded-pill border border-accent-primary bg-accent-primary px-3 py-2 type-label text-void transition hover:brightness-110"
        >
          <Plus size={15} />
          Dodaj projekt
        </button> : null}
        {!isProductionWorkspace && activeAccount && accounts.length > 1 ? (
          <button
            type="button"
            onClick={() => removeAccount(activeAccount.id)}
            className="flex w-full items-center justify-center gap-2 border border-error/30 px-3 py-2 type-label text-error/90"
          >
            <Trash2 size={15} />
            Usuń aktywny projekt
          </button>
        ) : null}
      </div>
    </aside>
  )
}

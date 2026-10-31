import { useCallback, useEffect, useMemo, useState } from 'react'
import { SettingsProviderCard } from './SettingsProviderCard'
import { RuntimeCapabilityStatusPanel } from './RuntimeCapabilityStatusPanel'
import {
  PROVIDERS,
  readPersistedProviderConfig,
  writePersistedProviderConfig,
  type PersistedProviderConfig,
  type ProviderId,
} from '../lib/providers'
import { fetchProviderCards, testProviderCard, updateProviderCard } from '../lib/api'
import type { ConnectionState, ProviderCard } from '../types/system'
import { useToastStore } from '../stores/toastStore'

const CORE_PROVIDER_ORDER: ProviderId[] = ['ollama', 'openai', 'anthropic', 'gemini']

function resolveProviderConnectionState(card: ProviderCard | undefined): ConnectionState {
  if (!card) {
    return { kind: 'disconnected' }
  }
  if (!card.providerEnabled) {
    return { kind: 'disconnected' }
  }
  if (card.runtimeHealthStatus === 'rate_limited' || card.status === 'rate_limited') {
    return { kind: 'rate_limited', resetInSec: null }
  }
  if (card.status === 'connected' || card.runtimeHealthStatus === 'healthy') {
    return { kind: 'connected' }
  }
  if (card.status === 'disconnected' || card.readinessStatus === 'blocked') {
    return { kind: 'disconnected' }
  }
  if (
    card.status === 'configured' ||
    card.readinessStatus === 'configured' ||
    card.readinessStatus === 'fallback_only' ||
    card.hasSecret
  ) {
    return {
      kind: 'configured',
      message: card.readinessReason || card.statusReason || 'Konfiguracja zapisana. Uruchom test connection.',
    }
  }
  if (
    card.runtimeHealthStatus === 'unhealthy' ||
    card.runtimeHealthStatus === 'error' ||
    card.status === 'error'
  ) {
    return { kind: 'error', message: card.runtimeHealthReason || card.statusReason || 'Blad providera.' }
  }
  return { kind: 'disconnected' }
}

export function SettingsGeneralProvidersPanel() {
  const pushToast = useToastStore((state) => state.push)
  const [cards, setCards] = useState<ProviderCard[]>([])
  const [drafts, setDrafts] = useState<Record<string, PersistedProviderConfig>>({})
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const next = await fetchProviderCards()
      setCards(next)
      const nextDrafts: Record<string, PersistedProviderConfig> = {}
      for (const provider of PROVIDERS) {
        const card = next.find((item) => item.providerKey === provider.id)
        const persisted = readPersistedProviderConfig(provider.id)
        nextDrafts[provider.id] = {
          ...persisted,
          apiKey: persisted.apiKey || '',
          baseUrl: card?.baseUrl || persisted.baseUrl || provider.defaultBaseUrl || '',
        }
      }
      setDrafts(nextDrafts)
    } catch (error) {
      pushToast({
        type: 'error',
        title: 'Nie udało się wczytać modeli',
        message: error instanceof Error ? error.message : 'Nieznany błąd',
      })
    } finally {
      setLoading(false)
    }
  }, [pushToast])

  useEffect(() => {
    void reload()
  }, [reload])

  const cardByKey = useMemo(() => {
    const map = new Map<string, ProviderCard>()
    for (const card of cards) {
      map.set(card.providerKey, card)
    }
    return map
  }, [cards])

  if (loading) {
    return (
      <div className="border border-border-default bg-void p-4 text-sm text-text-secondary">
        Wczytuję karty providerów…
      </div>
    )
  }

  return (
    <div className="space-y-4" data-testid="settings-general-providers">
      <div className="border border-border-default bg-panel p-4">
        <div className="label-eyebrow">Modele i API</div>
        <p className="mt-2 text-sm text-text-secondary">
          Podłącz Ollama, OpenAI, Anthropic i Gemini. Klucze zapisują się lokalnie w vault Atnosa — nie
          wklejaj ich do czatu.
        </p>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {CORE_PROVIDER_ORDER.map((providerId) => {
          const provider = PROVIDERS.find((item) => item.id === providerId)
          if (!provider) {
            return null
          }
          const card = cardByKey.get(providerId)
          const persisted = drafts[providerId] ?? readPersistedProviderConfig(providerId)
          return (
            <SettingsProviderCard
              key={providerId}
              provider={providerId}
              state={resolveProviderConnectionState(card)}
              apiKey={persisted.apiKey}
              baseUrl={persisted.baseUrl}
              lastSync={persisted.lastSync ?? null}
              onSave={async (next) => {
                try {
                  const merged = { ...persisted, ...next, lastSync: Date.now() }
                  writePersistedProviderConfig(providerId, merged)
                  setDrafts((state) => ({ ...state, [providerId]: merged }))
                  const updated = await updateProviderCard(providerId, next)
                  setCards((current) => {
                    const without = current.filter((item) => item.providerKey !== providerId)
                    return [...without, updated]
                  })
                  pushToast({
                    type: 'success',
                    title: `${provider.displayName} zapisany`,
                    message: 'Provider gotowy do użycia w czacie i produkcji.',
                  })
                } catch (error) {
                  pushToast({
                    type: 'error',
                    title: `Zapis ${provider.displayName} nieudany`,
                    message: error instanceof Error ? error.message : 'Nieznany błąd',
                  })
                  throw error
                }
              }}
              onTest={(draft) => testProviderCard(providerId, draft)}
            />
          )
        })}
      </div>
      <RuntimeCapabilityStatusPanel />
    </div>
  )
}

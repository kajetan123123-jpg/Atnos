import { createElement, type ComponentPropsWithoutRef, type ReactElement } from 'react'

export type ProviderId = 'openai' | 'anthropic' | 'ollama' | 'gemini'

type ProviderLogoProps = ComponentPropsWithoutRef<'svg'>

function svgLogo(props: ProviderLogoProps, children: ReactElement[]) {
  return createElement(
    'svg',
    {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      strokeWidth: 1.7,
      ...props,
    },
    ...children,
  )
}

function OpenAILogo(props: ProviderLogoProps) {
  return svgLogo(props, [
    createElement('path', { key: 'a', d: 'M12 3.5 7 6.4v5.8l5 2.9 5-2.9V6.4Z' }),
    createElement('path', { key: 'b', d: 'm7 6.4-2.5 4.3L7 15' }),
    createElement('path', { key: 'c', d: 'm17 6.4 2.5 4.3L17 15' }),
    createElement('path', { key: 'd', d: 'M12 15v5.5' }),
  ])
}

function AnthropicLogo(props: ProviderLogoProps) {
  return svgLogo(props, [
    createElement('path', { key: 'a', d: 'M4.5 19 10.8 5h2.4L19.5 19' }),
    createElement('path', { key: 'b', d: 'M7.7 12.2h8.6' }),
    createElement('path', { key: 'c', d: 'M12 5v14' }),
  ])
}

function OllamaLogo(props: ProviderLogoProps) {
  return svgLogo(props, [
    createElement('rect', { key: 'a', x: 5, y: 7, width: 14, height: 10, rx: 2.5 }),
    createElement('path', { key: 'b', d: 'M8.5 12h.01M15.5 12h.01' }),
    createElement('path', { key: 'c', d: 'M9 17v2M15 17v2M9 5v2M15 5v2' }),
  ])
}

function GeminiLogo(props: ProviderLogoProps) {
  return svgLogo(props, [
    createElement('path', { key: 'a', d: 'M12 3.5 13.7 9l5.8 1.7-5.8 1.7L12 20l-1.7-7.6-5.8-1.7L10.3 9Z' }),
    createElement('path', { key: 'b', d: 'M18.5 4.5 19.2 7l2.3.7-2.3.7-.7 2.6-.7-2.6-2.3-.7 2.3-.7Z' }),
  ])
}

export interface ProviderDefinition {
  id: ProviderId
  displayName: string
  requiresApiKey: boolean
  defaultBaseUrl?: string
  Logo: (props: ProviderLogoProps) => ReactElement
}

export const PROVIDERS: ProviderDefinition[] = [
  { id: 'openai', displayName: 'OpenAI', requiresApiKey: true, Logo: OpenAILogo },
  { id: 'anthropic', displayName: 'Anthropic', requiresApiKey: true, Logo: AnthropicLogo },
  {
    id: 'ollama',
    displayName: 'Ollama',
    requiresApiKey: false,
    defaultBaseUrl: 'http://localhost:11434',
    Logo: OllamaLogo,
  },
  { id: 'gemini', displayName: 'Gemini', requiresApiKey: true, Logo: GeminiLogo },
]

export const PROVIDER_MAP = Object.fromEntries(
  PROVIDERS.map((provider) => [provider.id, provider]),
) as Record<ProviderId, ProviderDefinition>

export interface PersistedProviderConfig {
  apiKey?: string
  baseUrl?: string
  lastSync?: number | null
}

const STORAGE_PREFIX = 'atnos.providers.'

export function readPersistedProviderConfig(providerId: ProviderId): PersistedProviderConfig {
  if (typeof window === 'undefined') {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${providerId}`)
    if (!raw) {
      return {}
    }
    const parsed = JSON.parse(raw) as PersistedProviderConfig
    return {
      apiKey: parsed.apiKey ?? undefined,
      baseUrl: parsed.baseUrl ?? undefined,
      lastSync: parsed.lastSync ?? null,
    }
  } catch {
    return {}
  }
}

export function writePersistedProviderConfig(
  providerId: ProviderId,
  config: PersistedProviderConfig,
) {
  if (typeof window === 'undefined') {
    return
  }

  window.localStorage.setItem(`${STORAGE_PREFIX}${providerId}`, JSON.stringify(config))
}

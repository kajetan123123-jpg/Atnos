/** Ujednolicone komunikaty statusu czatu (PL). */

export const CHAT_STATUS = {
  connecting: 'Łączenie z Atnosem…',
  answering: 'Atnos odpowiada…',
  analyzing: 'Analizuję polecenie…',
  awaitingResponse: 'Czekam na odpowiedź Atnosa…',
  loadingConversation: 'Ładuję rozmowę…',
  sending: 'Wysyłanie…',
  ollamaUnavailable: 'Ollama jest niedostępna',
  apiUnavailable: 'API Atnosa jest niedostępne',
  sendFailed: 'Nie udało się wysłać wiadomości',
  retry: 'Spróbuj ponownie',
  thinkingAria: 'Atnos odpowiada',
  cancel: 'Przerwij',
  cancelled: 'Generowanie przerwane',
  interrupted: 'Odpowiedź przerwana',
} as const

export function humanizeChatError(error: unknown): string {
  if (!(error instanceof Error) || !error.message.trim()) {
    return CHAT_STATUS.sendFailed
  }
  const raw = error.message.trim()
  const lower = raw.toLowerCase()
  if (
    lower.includes('failed to fetch') ||
    lower.includes('networkerror') ||
    lower.includes('network request failed') ||
    lower.includes('err_connection') ||
    lower.includes('econnrefused') ||
    lower.includes('nie udało się połączyć z lokalnym runtime')
  ) {
    return CHAT_STATUS.apiUnavailable
  }
  if (
    lower.includes('ollama') &&
    (lower.includes('unavailable') ||
      lower.includes('niedostęp') ||
      lower.includes('not running') ||
      lower.includes('connection refused') ||
      lower.includes('11434'))
  ) {
    return CHAT_STATUS.ollamaUnavailable
  }
  // Nie pokazuj surowych tracebacków / JSON w UI.
  if (raw.includes('Traceback') || raw.startsWith('{') || raw.includes('\n  File "')) {
    return CHAT_STATUS.sendFailed
  }
  if (raw.length > 240) {
    return `${CHAT_STATUS.sendFailed}.`
  }
  return raw
}

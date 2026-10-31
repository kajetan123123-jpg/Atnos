/** Human-readable Polish labels for agentic tool stream events. */

export type AgentToolLabelPhase = 'running' | 'done'

const TOOL_LABELS: Record<string, { running: string; done: string; noun: string }> = {
  ai_queue_tasks: {
    running: 'Zapisuję zadania w kolejce…',
    done: 'Zapisano zadania — oczekują na wykonanie',
    noun: 'Kolejka zadań',
  },
  ai_delegate_status: {
    running: 'Sprawdzam zadania w tle…',
    done: 'Sprawdzono rejestr zadań',
    noun: 'Stan zadań',
  },
  ai_delegate_background: {
    running: 'Zlecam zadanie lokalnemu modelowi…',
    done: 'Przekazano zadanie',
    noun: 'Zadanie w tle',
  },
  browser_open: {
    running: 'Otwieram stronę…',
    done: 'Otwarto stronę',
    noun: 'Przeglądarka',
  },
  browser_tabs: {
    running: 'Sprawdzam karty…',
    done: 'Sprawdzono karty',
    noun: 'Karty przeglądarki',
  },
  browser_learn: {
    running: 'Wyciągam dane ze strony…',
    done: 'Zebrano dane ze strony',
    noun: 'Odczyt strony',
  },
  desktop_app_open: {
    running: 'Otwieram aplikację…',
    done: 'Otwarto aplikację',
    noun: 'Aplikacja',
  },
  fs_list: {
    running: 'Przeglądam pliki…',
    done: 'Przejrzano pliki',
    noun: 'Lista plików',
  },
  fs_read: {
    running: 'Czytam plik…',
    done: 'Odczytano plik',
    noun: 'Odczyt pliku',
  },
  fs_write: {
    running: 'Zapisuję plik…',
    done: 'Zapisano plik',
    noun: 'Zapis pliku',
  },
  fs_delete: {
    running: 'Usuwam plik…',
    done: 'Usunięto plik',
    noun: 'Usuwanie pliku',
  },
  shell_powershell: {
    running: 'Uruchamiam polecenie…',
    done: 'Wykonano polecenie',
    noun: 'Polecenie',
  },
  obsidian_search: {
    running: 'Szukam w notatkach…',
    done: 'Przeszukano notatki',
    noun: 'Szukanie w notatkach',
  },
  obsidian_read: {
    running: 'Czytam notatkę…',
    done: 'Odczytano notatkę',
    noun: 'Odczyt notatki',
  },
  obsidian_write: {
    running: 'Zapisuję notatkę…',
    done: 'Zapisano notatkę',
    noun: 'Zapis notatki',
  },
  self_state: {
    running: 'Sprawdzam stan…',
    done: 'Sprawdzono stan',
    noun: 'Stan Atnosa',
  },
  external_llm_ask: {
    running: 'Pytam zewnętrzny model…',
    done: 'Odpowiedź zewnętrznego modelu',
    noun: 'Zewnętrzny LLM',
  },
  file_inventory_report: {
    running: 'Inwentaryzuję pliki…',
    done: 'Gotowy raport plików',
    noun: 'Inwentaryzacja',
  },
  echo: {
    running: 'Sprawdzam połączenie…',
    done: 'Połączenie OK',
    noun: 'Test',
  },
}

function fallbackLabel(toolName: string, phase: AgentToolLabelPhase): string {
  const soft = toolName.replace(/_/g, ' ').trim()
  if (!soft) {
    return phase === 'running' ? 'Pracuję…' : 'Gotowe'
  }
  return phase === 'running' ? `${soft}…` : soft
}

export function getAgentToolLabel(
  toolName: string,
  phase: AgentToolLabelPhase = 'running',
): string {
  const mapped = TOOL_LABELS[toolName]
  if (!mapped) {
    return fallbackLabel(toolName, phase)
  }
  return phase === 'running' ? mapped.running : mapped.done
}

export function getAgentToolNoun(toolName: string): string {
  return TOOL_LABELS[toolName]?.noun ?? toolName.replace(/_/g, ' ')
}

/** A finished request is not necessarily an executed or successful action. */
export function getAgentToolStateLabel(toolName: string, status?: string | null): string {
  if (isToolCallInProgress(status)) return getAgentToolLabel(toolName, 'running')
  if (status === 'queued_for_manual_execution') return `${getAgentToolNoun(toolName)} — zapisano, czeka na wykonanie`
  if (['ok', 'success', 'completed', 'written', 'deleted', 'opened', 'focused', 'resolved', 'learned', 'executed'].includes(status!)) {
    return getAgentToolLabel(toolName, 'done')
  }
  const noun = getAgentToolNoun(toolName)
  if (status === 'needs_confirmation') return `${noun} — czeka na potwierdzenie`
  return `${noun} — ${formatToolCallStatusLabel(status!)}`
}

export function formatAgenticLoopStatusLabel(status: string): string {
  switch (status) {
    case 'queued_for_manual_execution':
      return 'zapisano, czeka na wykonanie'
    case 'running':
    case 'pending':
      return 'w toku'
    case 'completed':
      return 'gotowe'
    case 'incomplete':
      return 'nieukończone'
    case 'partially_completed':
      return 'zapis potwierdzony — pozostałe sprawdzenia nieukończone'
    case 'source_conflict':
      return 'konflikt źródeł pamięci'
    case 'source_not_found':
      return 'nie znaleziono wskazanej notatki'
    case 'needs_confirmation':
      return 'czekam na potwierdzenie'
    case 'loop_detected':
      return 'przerwane — pętla'
    case 'max_turns':
      return 'limit kroków'
    case 'error':
    case 'failed':
      return 'błąd'
    default:
      return status.replace(/_/g, ' ')
  }
}

export function formatToolCallStatusLabel(status: string): string {
  switch (status) {
    case 'started_background':
      return 'zlecono — wynik jeszcze niegotowy'
    case 'incomplete':
      return 'niepełny wynik'
    case 'ok':
    case 'opened':
    case 'focused':
    case 'resolved':
    case 'success':
    case 'completed':
      return 'OK'
    case 'not_found':
      return 'nie znaleziono'
    case 'needs_confirmation':
      return 'potwierdź'
    case 'error':
    case 'failed':
      return 'błąd'
    case 'running':
    case 'pending':
      return 'w toku'
    default:
      return status.replace(/_/g, ' ')
  }
}

export function isToolCallInProgress(status: string | null | undefined): boolean {
  return status === 'running' || status === 'pending' || !status
}

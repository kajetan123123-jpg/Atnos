import { describe, expect, it } from 'vitest'
import {
  formatAgenticLoopStatusLabel,
  getAgentToolLabel,
  getAgentToolNoun,
  getAgentToolStateLabel,
} from './agentToolLabels'

describe('agentToolLabels', () => {
  it('distinguishes a committed write from incomplete follow-up and source conflicts', () => {
    expect(formatAgenticLoopStatusLabel('partially_completed')).toBe('zapis potwierdzony — pozostałe sprawdzenia nieukończone')
    expect(formatAgenticLoopStatusLabel('source_conflict')).toBe('konflikt źródeł pamięci')
    expect(formatAgenticLoopStatusLabel('source_not_found')).toBe('nie znaleziono wskazanej notatki')
  })
  it('maps known tools to concise Polish stage labels', () => {
    expect(getAgentToolLabel('browser_open', 'running')).toBe('Otwieram stronę…')
    expect(getAgentToolLabel('browser_learn', 'running')).toBe('Wyciągam dane ze strony…')
    expect(getAgentToolLabel('desktop_app_open', 'done')).toBe('Otwarto aplikację')
    expect(getAgentToolNoun('fs_read')).toBe('Odczyt pliku')
  })

  it('softens unknown tools without looking technical', () => {
    expect(getAgentToolLabel('custom_widget_probe', 'running')).toBe('custom widget probe…')
    expect(formatAgenticLoopStatusLabel('completed')).toBe('gotowe')
    expect(formatAgenticLoopStatusLabel('running')).toBe('w toku')
  })

  it('uses outcome evidence rather than treating every tool result as success', () => {
    expect(getAgentToolStateLabel('ai_queue_tasks', 'queued_for_manual_execution')).toBe('Kolejka zadań — zapisano, czeka na wykonanie')
    expect(getAgentToolStateLabel('ai_delegate_background', 'started_background')).toBe('Zadanie w tle — zlecono — wynik jeszcze niegotowy')
    expect(getAgentToolStateLabel('shell_powershell', 'needs_confirmation')).toBe('Polecenie — czeka na potwierdzenie')
    expect(getAgentToolStateLabel('fs_read', 'failed')).toBe('Odczyt pliku — błąd')
    expect(getAgentToolStateLabel('fs_write', 'written')).toBe('Zapisano plik')
    expect(getAgentToolStateLabel('shell_powershell', 'blocked')).not.toBe('Wykonano polecenie')
  })
})

export const CHAT_WORK_MODE_STORAGE_KEY = 'atnos.chat.workMode.v1'

export type ChatWorkMode = 'local_chat' | 'project_command' | 'delegation'

export const CHAT_WORK_MODE_OPTIONS: Array<{ value: ChatWorkMode; label: string; hint: string }> = [
  {
    value: 'local_chat',
    label: 'Rozmowa lokalna',
    hint: 'Domyślny tryb: Ollama + pętla narzędzi. Bez Codexu i chmury.',
  },
  {
    value: 'project_command',
    label: 'Polecenia projektu',
    hint: 'Audytowane polecenia w aktywnym projekcie.',
  },
  {
    value: 'delegation',
    label: 'Delegacja lokalna',
    hint: 'Plan i kroki przez lokalne AI Workers (Ollama).',
  },
]

export function readChatWorkMode(): ChatWorkMode {
  if (typeof window === 'undefined') {
    return 'local_chat'
  }
  const saved = window.localStorage.getItem(CHAT_WORK_MODE_STORAGE_KEY)
  if (saved === 'project_command' || saved === 'delegation' || saved === 'local_chat') {
    return saved
  }
  return 'local_chat'
}

export function writeChatWorkMode(mode: ChatWorkMode) {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.setItem(CHAT_WORK_MODE_STORAGE_KEY, mode)
}

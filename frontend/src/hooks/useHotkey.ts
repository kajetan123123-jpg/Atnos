import { useEffect, useRef } from 'react'

export function useHotkey(keys: string[], handler: (event: KeyboardEvent) => void) {
  const handlerRef = useRef(handler)

  useEffect(() => {
    handlerRef.current = handler
  }, [handler])

  useEffect(() => {
    const matchesHotkey = (event: KeyboardEvent, combo: string) => {
      const normalized = combo.toLowerCase()
      if (normalized === 'escape') {
        return event.key === 'Escape'
      }
      const parts = normalized.split('+')
      const key = parts[parts.length - 1]
      const needsMod = parts.includes('mod')
      const needsCtrl = parts.includes('ctrl')
      const needsShift = parts.includes('shift')
      const needsAlt = parts.includes('alt')
      if (needsMod && !(event.metaKey || event.ctrlKey)) return false
      if (needsCtrl && !event.ctrlKey) return false
      if (needsShift && !event.shiftKey) return false
      if (needsAlt && !event.altKey) return false
      return event.key.toLowerCase() === key
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) {
        return
      }
      if (keys.some((combo) => matchesHotkey(event, combo))) {
        handlerRef.current(event)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [keys])
}

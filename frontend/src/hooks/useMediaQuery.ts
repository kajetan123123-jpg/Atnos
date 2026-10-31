import { useEffect, useState } from 'react'

export function useMediaQuery(query: string) {
  const readMatch = () => window.matchMedia?.(query)?.matches ?? false
  const [matches, setMatches] = useState(readMatch)

  useEffect(() => {
    const media = window.matchMedia?.(query)
    if (!media) {
      setMatches(false)
      return undefined
    }
    const listener = () => setMatches(media.matches)
    listener()
    media.addEventListener('change', listener)
    return () => media.removeEventListener('change', listener)
  }, [query])

  return matches
}

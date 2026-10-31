import { useEffect, useState } from 'react'

export type ShellBreakpoint = 'mobile' | 'tablet' | 'desktop' | 'wide'

function resolveBreakpoint(width: number): ShellBreakpoint {
  if (width >= 1280) {
    return 'wide'
  }
  if (width >= 1024) {
    return 'desktop'
  }
  if (width >= 768) {
    return 'tablet'
  }
  return 'mobile'
}

export function useBreakpoint() {
  const [breakpoint, setBreakpoint] = useState<ShellBreakpoint>(() => {
    if (typeof window === 'undefined') {
      return 'desktop'
    }
    return resolveBreakpoint(window.innerWidth)
  })

  useEffect(() => {
    const onResize = () => setBreakpoint(resolveBreakpoint(window.innerWidth))
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  return {
    breakpoint,
    isMobile: breakpoint === 'mobile',
    isTablet: breakpoint === 'tablet',
    isDesktop: breakpoint === 'desktop',
    isWide: breakpoint === 'wide',
    isDesktopUp: breakpoint === 'desktop' || breakpoint === 'wide',
    isTabletUp: breakpoint !== 'mobile',
    isCompactMobile: typeof window !== 'undefined' ? window.innerWidth <= 480 : false,
  }
}

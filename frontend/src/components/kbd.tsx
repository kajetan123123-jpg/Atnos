import type { PropsWithChildren } from 'react'

export function Kbd({ children }: PropsWithChildren) {
  return (
    <kbd className="shell-module inline-flex min-w-8 items-center justify-center rounded-md px-1 py-1 type-mono-sm text-text-tertiary">
      {children}
    </kbd>
  )
}

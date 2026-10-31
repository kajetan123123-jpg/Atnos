import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RANK, resolveDirection, resolveRank, resolveVariants } from './PageTransition'
import { PageTransition } from './PageTransition'

describe('PageTransition helpers', () => {
  it('resolves route ranks per shell contract', () => {
    expect(RANK['/chat']).toBe(0)
    expect(resolveRank('/create')).toBe(1)
    expect(resolveRank('/studio/abc')).toBe(1)
    expect(resolveRank('/pipeline/current')).toBe(2)
    expect(resolveRank('/settings/providers')).toBe(4)
  })

  it('marks settings as a deeper transition', () => {
    expect(resolveDirection('/chat', '/settings')).toBe('settings')
  })

  it('resolves forward and backward transitions', () => {
    expect(resolveDirection('/chat', '/telemetry')).toBe('forward')
    expect(resolveDirection('/settings', '/chat')).toBe('backward')
    expect(resolveDirection('/chat', '/chat')).toBe('same')
  })

  it('uses reduced-motion fade variants', () => {
    const variants = resolveVariants('forward', true)
    expect(variants.initial).toEqual({ opacity: 0 })
    expect(variants.animate).toMatchObject({ opacity: 1 })
    expect(variants.exit).toMatchObject({ opacity: 0 })
  })

  it('keeps route content visible even when the parent has no explicit height', () => {
    render(
      <PageTransition>
        <div>Chat route content</div>
      </PageTransition>,
    )

    expect(screen.getByText('Chat route content').parentElement).toHaveClass(
      'min-h-[calc(100vh-56px)]',
    )
  })
})

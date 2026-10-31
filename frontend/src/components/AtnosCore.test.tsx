import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AtnosCore } from './AtnosCore'
import { resetAllStores } from '../test/resetStores'

let reducedMotion = false

vi.mock('../hooks/useReducedMotionStrict', () => ({
  useReducedMotionStrict: () => reducedMotion,
}))

vi.mock('./AtnosCoreParticles', () => ({
  AtnosCoreParticles: ({ mode }: { mode: 'stream' | 'burst' }) => (
    <g data-testid={`particles-${mode}`} />
  ),
}))

describe('AtnosCore', () => {
  beforeEach(() => {
    resetAllStores()
    reducedMotion = false
  })

  it.each([
    ['idle', 'Stan ATNOS: bezczynny'],
    ['thinking', 'Stan ATNOS: myśli'],
    ['executing', 'Stan ATNOS: wykonuje'],
    ['awaiting_human', 'Stan ATNOS: oczekuje na człowieka'],
    ['error', 'Stan ATNOS: błąd'],
    ['success', 'Stan ATNOS: sukces'],
    ['offline', 'Stan ATNOS: offline'],
  ] as const)('renders %s with the canonical aria label', (state, label) => {
    render(<AtnosCore state={state} />)

    expect(screen.getByRole('img', { name: label })).toBeInTheDocument()
  })

  it('accepts a custom aria label', () => {
    render(<AtnosCore state="thinking" ariaLabel="Niestandardowy stan Atnosa" />)

    expect(screen.getByRole('img', { name: 'Niestandardowy stan Atnosa' })).toBeInTheDocument()
  })

  it('disables particles when reduced motion is enabled', () => {
    reducedMotion = true

    render(<AtnosCore state="executing" />)

    expect(screen.queryByTestId('particles-stream')).not.toBeInTheDocument()
  })
})

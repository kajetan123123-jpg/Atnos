import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PipelineStep } from './PipelineStep'
import { resetAllStores } from '../test/resetStores'

describe('PipelineStep', () => {
  beforeEach(() => {
    resetAllStores()
  })

  it('renders retry affordance for error state', () => {
    const onRetry = vi.fn()

    render(
      <PipelineStep
        index={2}
        onRetry={onRetry}
        step={{
          id: 'tts',
          label: 'Synteza mowy',
          state: 'error',
          progress: 0.48,
          errorMessage: 'Błąd TTS',
        }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /ponów krok/i }))

    expect(screen.getByText('Błąd TTS')).toBeInTheDocument()
    expect(onRetry).toHaveBeenCalledWith('tts')
  })

  it('shows active progress for running state', () => {
    render(
      <PipelineStep
        index={0}
        step={{
          id: 'research',
          label: 'Badanie trendów',
          state: 'active',
          progress: 0.32,
        }}
      />,
    )

    expect(screen.getByText('32%')).toBeInTheDocument()
    expect(screen.getByText('Badanie trendów')).toBeInTheDocument()
  })
})

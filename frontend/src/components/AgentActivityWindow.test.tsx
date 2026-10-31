import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AgentActivityWindow } from './AgentActivityWindow'

describe('AgentActivityWindow', () => {
  it('renders live action summary and pause/interrupt controls', async () => {
    const user = userEvent.setup()
    const onPause = vi.fn()
    const onInterrupt = vi.fn()

    render(
      <AgentActivityWindow
        agentStage="executing"
        agentActivity={{
          stage: 'executing',
          title: 'Atnos nawiguje na youtube.com',
          detail: 'Wykonuje krok browser_navigate dla Studio.',
        }}
        activeRun={{
          executionRunId: 77,
          projectId: 1,
          projectName: 'Projekt bazowy',
          schedulerJobId: null,
          accountId: null,
          trustGrantId: null,
          runType: 'agent.preview.browser_navigate',
          status: 'running',
          goal: 'Open YouTube Studio',
          connectorKey: 'browser',
          platformKey: 'youtube',
          riskLevel: 'medium',
          inputJson: {},
          outputJson: {},
          artifactsJson: {},
          errorMessage: null,
          startedAt: '2026-05-13T10:00:00Z',
          finishedAt: null,
          preview: null,
          steps: [
            {
              executionStepId: 701,
              stepIndex: 0,
              stepType: 'navigate',
              stepName: 'Open studio.youtube.com',
              status: 'running',
              selectorHint: null,
              errorMessage: null,
              screenshotPath: null,
              startedAt: '2026-05-13T10:00:00Z',
              finishedAt: null,
            },
          ],
        }}
        activeStep={{
          executionStepId: 701,
          stepIndex: 0,
          stepType: 'navigate',
          stepName: 'Open studio.youtube.com',
          status: 'running',
          selectorHint: null,
          errorMessage: null,
          screenshotPath: null,
          startedAt: '2026-05-13T10:00:00Z',
          finishedAt: null,
        }}
        browserRuntimeReady
        onPause={onPause}
        onInterrupt={onInterrupt}
      />,
    )

    expect(screen.getByText(/Atnos nawiguje na youtube.com/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Open studio.youtube.com/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/Runtime przeglądarki:\s*gotowy/i)).toBeInTheDocument()

    expect(screen.getByText('CO ROBI')).toBeInTheDocument()
    expect(screen.getByText('Pracuje w przeglądarce')).toBeInTheDocument()
    expect(screen.getByText('NA CZYM STOI')).toBeInTheDocument()
    expect(screen.getByText('Otwiera stronę w przeglądarce')).toBeInTheDocument()
    expect(screen.getByText('NASTĘPNY KROK')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Wstrzymaj$/i }))
    await user.click(screen.getByRole('button', { name: /^Przerwij$/i }))

    expect(onPause).toHaveBeenCalledTimes(1)
    expect(onInterrupt).toHaveBeenCalledTimes(1)
  })
})

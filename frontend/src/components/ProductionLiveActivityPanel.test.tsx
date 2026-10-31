import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ProductionJobPayload } from '../lib/api'
import {
  activityDetail,
  activityHeadline,
  productionActivityKind,
  productionActivityStepLabel,
  ProductionLiveActivityPanel,
} from './ProductionLiveActivityPanel'

const baseJob = {
  id: 'prod-live',
  account_id: 'acc',
  topic_id: 'topic',
  status: 'running',
  current_step: 'script_draft',
  worker_live: true,
  provider_calls: 2,
  max_provider_calls: 100,
  ready_for_review: false,
  ready_for_private_upload: false,
  request: { production_scope: 'script_preview' },
  steps: [
    {
      step: 'script_draft',
      status: 'running',
      progress: 0,
      attempt: 1,
      message: null,
      started_at: '2026-09-16T22:00:00Z',
      completed_at: null,
    },
  ],
  updated_at: '2026-09-16T22:00:10Z',
} as ProductionJobPayload

it('does not call cached narration verification a new recording during render-only correction', () => {
  const job={...baseJob,current_step:'voice',current_correction:{status:'registered',kind:'render'}} as ProductionJobPayload
  expect(productionActivityStepLabel(job)).toBe('Sprawdzanie zapisanej narracji i napisów przed montażem')
  expect(activityDetail(job,'working')).not.toContain('Nagrywanie')
  expect(productionActivityStepLabel({...job,current_step:'remotion_render'})).toBe('Montaż i renderowanie')
})

describe('ProductionLiveActivityPanel', () => {
  it('shows only the current correction in progress and leaves historical DONE rows collapsed', () => {
    render(<ProductionLiveActivityPanel job={{ ...baseJob, request: { production_scope: 'film' },
      current_step: 'remotion_render', current_correction: {
        status: 'registered', source: 'checkpoint_metadata', kind: 'render', completed: 0, total: 2,
        stages: [{ stage: 'remotion_render', status: 'running', reused: false },
          { stage: 'package', status: 'pending', reused: false }],
      }, steps: [{ step: 'package', status: 'done', progress: 1, attempt: 2 }] }} />)
    expect(screen.getByTestId('production-live-activity')).toHaveTextContent('Etapy tej korekty: 0/2')
    expect(screen.getByTestId('production-live-timeline')).not.toHaveTextContent('done')
    expect(screen.getByText('Historia wcześniejszych i pomocniczych etapów').closest('details')).not.toHaveAttribute('open')
  })
  it('does not present a retained running checkpoint as active after the worker disappears', () => {
    render(<ProductionLiveActivityPanel job={{ ...baseJob, status: 'blocked', worker_live: false }} />)
    expect(screen.getByTestId('production-live-timeline')).toHaveTextContent('przerwano')
    expect(screen.getByTestId('production-live-timeline')).not.toHaveTextContent('teraz')
  })
  it('marks a live worker as working and shows the current stage', () => {
    const now = Date.parse('2026-09-16T22:00:20Z')
    expect(productionActivityKind(baseJob, now)).toBe('working')
    expect(activityHeadline('working')).toBe('Atnos pracuje teraz')
    expect(activityDetail(baseJob, 'working')).toContain('Pisanie pierwszej wersji scenariusza')

    render(<ProductionLiveActivityPanel job={baseJob} nowMs={now} />)
    expect(screen.getByTestId('production-live-activity')).toHaveTextContent('Atnos pracuje teraz')
    expect(screen.getByTestId('production-live-activity')).toHaveTextContent('Worker: żywy')
    expect(screen.getByTestId('production-live-timeline')).toHaveTextContent('Pisanie pierwszej wersji scenariusza')
    expect(screen.getByTestId('production-live-timeline')).toHaveTextContent('teraz')
  })

  it('flags a stale production without pretending work continues', () => {
    const stale = {
      ...baseJob,
      worker_live: false,
      updated_at: '2026-09-16T21:00:00Z',
    } as ProductionJobPayload
    const now = Date.parse('2026-09-16T22:00:00Z')
    expect(productionActivityKind(stale, now)).toBe('stale')
    render(<ProductionLiveActivityPanel job={stale} nowMs={now} />)
    expect(screen.getByText('Brak sygnału — możliwe zawieszenie')).toBeInTheDocument()
    expect(screen.getByText(/Ostatni sygnał jest stary/)).toBeInTheDocument()
  })

  it.each(['failed', 'error', 'blocked'])('does not label a %s production as finished', (status) => {
    const failed = { ...baseJob, status, worker_live: false }
    expect(productionActivityKind(failed)).toBe('blocked')
    render(<ProductionLiveActivityPanel job={failed} />)
    expect(screen.getByText('Wymaga decyzji')).toBeInTheDocument()
    expect(screen.queryByText('Zakończone')).not.toBeInTheDocument()
  })
})

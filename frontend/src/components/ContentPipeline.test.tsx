import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { PipelineBody, shouldRenderPipelineRail } from './ContentPipeline'
import { resetAllStores } from '../test/resetStores'
import { usePipelineStore } from '../stores/pipelineStore'
import type { PipelineSnapshot } from '../types/pipeline'

describe('PipelineBody', () => {
  beforeEach(() => {
    resetAllStores()
  })

  it('renders thumbnail and video slots after done steps', () => {
    usePipelineStore.getState().hydratePipeline({
      currentJob: {
        id: 'job-qa',
        title: 'Job QA',
        createdAt: Date.parse('2026-05-15T10:00:00Z'),
        platform: 'youtube',
        awaitingApproval: false,
        thumbnailUrl: 'https://example.com/thumb.png',
        videoUrl: 'https://example.com/video.mp4',
        steps: [
          { id: 'research', label: 'Badanie trendów', state: 'done', progress: 1 },
          { id: 'script', label: 'Skrypt', state: 'done', progress: 1 },
          { id: 'tts', label: 'Synteza mowy', state: 'done', progress: 1 },
          { id: 'video', label: 'Render wideo', state: 'done', progress: 1 },
          { id: 'thumbnail', label: 'Miniatura', state: 'done', progress: 1 },
          { id: 'upload', label: 'Publikacja YouTube', state: 'done', progress: 1 },
        ],
      },
      history: [],
    })

    render(<PipelineBody />)

    expect(screen.getByText('PODGLĄD MINIATURY')).toBeInTheDocument()
    expect(screen.getByText('PODGLĄD WIDEO')).toBeInTheDocument()
    expect(screen.getByText('Gotowe do zatwierdzenia')).toBeInTheDocument()
  })

  it('hides preview-only rail on chat surfaces unless approval is needed', () => {
    const snapshot: PipelineSnapshot = {
      previewOnly: true,
      job: {
        creatorProjectId: 1,
        projectId: 1,
        title: 'Preview pipeline',
        projectType: 'creator',
        status: 'active',
        platforms: ['youtube'],
        operatingMode: 'planning_only',
        approvalPolicy: 'always',
        updatedAt: '2026-05-15T10:00:00Z',
      },
      steps: [
        { id: 'research', label: 'Badanie trendów', state: 'done', progress: 1 },
        { id: 'script', label: 'Skrypt', state: 'active', progress: 0.4 },
      ],
      latestVideoArtifact: null,
      awaitingApproval: false,
      status: 'executing',
      statusReason: 'Preview only',
      approvalPackage: null,
    }

    usePipelineStore.getState().setSnapshot(snapshot)

    expect(
      shouldRenderPipelineRail(snapshot, usePipelineStore.getState().currentJob, false),
    ).toBe(false)

    usePipelineStore.getState().setSnapshot({
      ...snapshot,
      awaitingApproval: true,
      status: 'awaiting_human',
    })

    expect(
      shouldRenderPipelineRail(
        usePipelineStore.getState().snapshot,
        usePipelineStore.getState().currentJob,
        false,
      ),
    ).toBe(true)
  })
})

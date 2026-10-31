import { beforeEach, describe, expect, it } from 'vitest'
import { usePipelineStore } from './pipelineStore'
import { resetAllStores } from '../test/resetStores'

describe('usePipelineStore', () => {
  beforeEach(() => {
    resetAllStores()
  })

  it('hydrates currentJob and updates step progress, state, and assets', () => {
    usePipelineStore.getState().hydratePipeline({
      currentJob: {
        id: 'job-1',
        title: 'Pipeline test',
        createdAt: 1,
        platform: 'youtube',
        awaitingApproval: false,
        steps: [
          { id: 'research', state: 'pending', progress: 0 },
          { id: 'script', state: 'pending', progress: 0 },
        ],
      },
      history: [],
    })

    usePipelineStore.getState().setStepProgress('research', 0.52)
    usePipelineStore
      .getState()
      .setStepState('research', 'error', { errorMessage: 'Błąd testowy' })
    usePipelineStore
      .getState()
      .attachAsset('https://example.com/thumb.png', 'thumbnail')

    const state = usePipelineStore.getState()

    expect(state.currentJob?.steps[0].progress).toBe(0.52)
    expect(state.currentJob?.steps[0].state).toBe('error')
    expect(state.currentJob?.steps[0].errorMessage).toBe('Błąd testowy')
    expect(state.currentJob?.thumbnailUrl).toBe('https://example.com/thumb.png')
  })

  it('derives currentJob from a legacy pipeline snapshot', () => {
    usePipelineStore.getState().setSnapshot({
      previewOnly: false,
      job: {
        creatorProjectId: 44,
        projectId: 12,
        title: 'Snapshot job',
        projectType: 'content',
        status: 'active',
        platforms: ['youtube'],
        operatingMode: 'execution',
        approvalPolicy: 'always_require_approval',
        updatedAt: '2026-05-15T10:00:00Z',
      },
      steps: [{ id: 'video', label: 'Render wideo', state: 'active', progress: 0.5 }],
      latestVideoArtifact: {
        title: 'Render',
        storagePath: null,
        externalUrl: 'https://example.com/video.mp4',
        updatedAt: '2026-05-15T10:01:00Z',
      },
      awaitingApproval: false,
      status: 'executing',
      statusReason: 'Render wideo trwa.',
      approvalPackage: null,
    })

    const state = usePipelineStore.getState()

    expect(state.currentJob).not.toBeNull()
    expect(state.currentJob?.title).toBe('Snapshot job')
    expect(state.currentJob?.videoUrl).toBe('https://example.com/video.mp4')
    expect(state.currentJob?.steps[0].id).toBe('video')
  })
})

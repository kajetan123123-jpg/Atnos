import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useSystemStore } from '../stores/systemStore'
import { resetAllStores } from '../test/resetStores'
import TelemetryRoute from './Telemetry'

describe('TelemetryRoute', () => {
  beforeEach(() => {
    resetAllStores()
    window.innerWidth = 1440
    window.dispatchEvent(new Event('resize'))
  })

  it('renders live CPU, RAM and GPU host metrics from the hardware snapshot', () => {
    useSystemStore.setState({
      model: { name: 'qwen2.5:14b', provider: 'ollama' },
      network: 'online',
      chroma: 'connected',
      vramUsedMB: 6144,
      vramTotalMB: 16384,
      hardwareSnapshot: {
        freeVramMb: 10240,
        usedVramMb: 6144,
        totalVramMb: 16384,
        freeRamMb: 16384,
        totalRamMb: 32768,
        usedRamMb: 16384,
        cpuPercent: 18.5,
        gpuName: 'AMD Radeon RX 7600 XT',
        freeDiskGb: 512,
        diskPath: 'F:\\',
        status: 'ready',
        notes: null,
      },
    })

    render(<TelemetryRoute />)

    expect(screen.getByText('18.5%')).toBeInTheDocument()
    expect(screen.getByText('16.0 GB / 32.0 GB')).toBeInTheDocument()
    expect(screen.getAllByText('AMD Radeon RX 7600 XT')).toHaveLength(2)
    expect(screen.getByText('6.0 GB / 16.0 GB')).toBeInTheDocument()
  })
})

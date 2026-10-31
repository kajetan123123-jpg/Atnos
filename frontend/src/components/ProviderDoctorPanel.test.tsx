import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ProviderDoctorPanel } from './ProviderDoctorPanel'
import * as api from '../lib/api'

vi.mock('../lib/api', () => ({
  fetchProductionF5TTSRuntimeStatus: vi.fn(), fetchProductionProviderDoctor: vi.fn(),
  runProductionProviderDoctorAction: vi.fn(), startProductionF5TTSRuntime: vi.fn(),
  stopProductionF5TTSRuntime: vi.fn(),
}))

const runtime: api.F5TTSRuntimeStatus = {
  transport: 'cli', transport_ready: true,
  endpoint: 'http://localhost:7860/api/predict', endpoint_ready: false,
  endpoint_status_code: null, endpoint_error: 'connection refused',
  package_installed: false, package_module_path: null, gradio_installed: true,
  runtime_python: 'E:/Atnos/Tools/F5/python.exe', runtime_python_exists: true,
  runtime_package_installed: true, runtime_gradio_installed: true, runtime_probe_error: null,
  install_dir: 'E:/Atnos/Tools/F5', install_exists: true, detected_files: [],
  launch_command_configured: true, launch_supported: true,
  reference_audio_path: 'E:/Atnos/Tools/F5/reference.wav', reference_audio_exists: true,
  diagnosis_code: 'cli_ready', diagnosis_message: 'F5-TTS ma konfigurację do syntezy lokalnej na CPU.',
  blocking_reasons: [], next_action: 'f5_tts_ready_run_voice_provider_doctor',
  suggested_commands: [], started: false,
}

describe('voice runtime diagnostics', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(api.fetchProductionF5TTSRuntimeStatus).mockResolvedValue(runtime)
    vi.mocked(api.fetchProductionProviderDoctor).mockResolvedValue({
      status: 'unknown', cards: [], operator_actions: [],
    } as unknown as api.ProductionProviderDoctorPayload)
  })

  it('shows configured CLI synthesis without an HTTP failure or server-start action', async () => {
    render(<ProviderDoctorPanel />)
    expect(await screen.findByText('Konfiguracja gotowa')).toBeInTheDocument()
    expect(screen.getByText('Synteza lokalna · CPU')).toBeInTheDocument()
    expect(screen.queryByText('connection refused')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Uruchom lokalny F5-TTS' })).not.toBeInTheDocument()
  })

  it('offers server startup when the configured Gradio transport is offline', async () => {
    vi.mocked(api.fetchProductionF5TTSRuntimeStatus).mockResolvedValue({
      ...runtime, transport: 'gradio', transport_ready: false, diagnosis_code: 'server_offline',
      diagnosis_message: 'Serwer F5-TTS nie działa.',
    })
    render(<ProviderDoctorPanel />)
    expect(await screen.findByRole('button', { name: 'Uruchom lokalny F5-TTS' })).toBeInTheDocument()
    expect(screen.queryByText('Konfiguracja gotowa')).not.toBeInTheDocument()
  })

  it('retains the independent F5 result when account diagnostics fail', async () => {
    vi.mocked(api.fetchProductionProviderDoctor).mockRejectedValue(new Error('Konto niedostępne'))
    render(<ProviderDoctorPanel />)
    expect(await screen.findByText('Konto niedostępne')).toBeInTheDocument()
    expect(screen.getByText('Konfiguracja gotowa')).toBeInTheDocument()
  })
})

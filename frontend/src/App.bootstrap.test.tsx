import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { resetAllStores } from './test/resetStores'

// jsdom has no layout for focus-trap's visibility check; the actual palette
// and App wiring remain real. Keyboard focus is also checked in the live UI.
vi.mock('focus-trap-react', () => ({
  default: ({ children }: { children: ReactNode }) => children,
}))

const mocks = vi.hoisted(() => ({
  useBootstrap: vi.fn(),
  useWebSocket: vi.fn(),
  useNetworkStatus: vi.fn(),
}))

vi.mock('./hooks/useBootstrap', () => ({ useBootstrap: mocks.useBootstrap }))
vi.mock('./hooks/useWebSocket', () => ({ useWebSocket: mocks.useWebSocket }))
vi.mock('./hooks/useNetworkStatus', () => ({ useNetworkStatus: mocks.useNetworkStatus }))
vi.mock('./hooks/useMediaQuery', () => ({ useMediaQuery: () => false }))
vi.mock('./components/ContentMachineTopBar', () => ({ ContentMachineTopBar: () => null }))
vi.mock('./components/MobileTabBar', () => ({ MobileTabBar: () => null }))
vi.mock('./components/Sidebar', () => ({ Sidebar: () => null }))
vi.mock('./components/SkipToContent', () => ({ SkipToContent: () => null }))
vi.mock('./components/ToastSystem', () => ({ ToastSystem: () => null }))
vi.mock('./components/OkropekCompanion', () => ({ OkropekCompanion: () => null }))
vi.mock('./routes/Dashboard', () => ({ default: () => <div>Dashboard</div> }))
vi.mock('./routes/Chat', () => ({ default: () => <div>Chat</div> }))
vi.mock('./routes/Studio', () => ({ default: () => null }))
vi.mock('./routes/Intelligence', () => ({ default: () => null }))
vi.mock('./routes/ChannelSetup', () => ({ default: () => null }))
vi.mock('./routes/ProjectsCommandCenter', () => ({ default: () => null }))
vi.mock('./routes/ProjectControl', () => ({ default: () => null }))
vi.mock('./routes/ContentMachineSettings', () => ({ default: () => null }))

import App from './App'

describe('App bootstrap', () => {
  beforeEach(() => {
    resetAllStores()
    mocks.useBootstrap.mockClear()
    mocks.useWebSocket.mockClear()
  })

  it.each(['/chat', '/projects'])('mounts the global command palette on %s', async (route) => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>)
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
    expect(await screen.findByRole('dialog', { name: 'Paleta polecen' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Paleta polecen' })).not.toBeInTheDocument())
  })

  it('hydrates the shared project and runtime stores at application startup', () => {
    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <App />
      </MemoryRouter>,
    )

    expect(mocks.useBootstrap).toHaveBeenCalled()
    expect(mocks.useNetworkStatus).toHaveBeenCalled()
    expect(mocks.useWebSocket).toHaveBeenCalledWith({
      forceConnect: true,
      silentBackgroundRetry: true,
    })
  })
})

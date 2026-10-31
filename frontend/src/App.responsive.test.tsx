import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  useMediaQuery: vi.fn(() => true),
}))

vi.mock('./hooks/useMediaQuery', () => ({ useMediaQuery: mocks.useMediaQuery }))
vi.mock('./hooks/useBootstrap', () => ({ useBootstrap: vi.fn() }))
vi.mock('./hooks/useWebSocket', () => ({ useWebSocket: vi.fn() }))
vi.mock('./components/ContentMachineTopBar', () => ({ ContentMachineTopBar: () => null }))
vi.mock('./components/MobileTabBar', () => ({ MobileTabBar: () => <div>Mobile navigation</div> }))
vi.mock('./components/Sidebar', () => ({ Sidebar: () => <div>Desktop sidebar</div> }))
vi.mock('./components/SkipToContent', () => ({ SkipToContent: () => null }))
vi.mock('./components/ToastSystem', () => ({ ToastSystem: () => null }))
vi.mock('./components/OkropekCompanion', () => ({ OkropekCompanion: () => null }))
vi.mock('./routes/Chat', () => ({ default: () => <div>Chat</div> }))
vi.mock('./routes/Studio', () => ({ default: () => null }))
vi.mock('./routes/Intelligence', () => ({ default: () => null }))
vi.mock('./routes/ChannelSetup', () => ({ default: () => null }))
vi.mock('./routes/ProjectsCommandCenter', () => ({ default: () => null }))
vi.mock('./routes/ProjectControl', () => ({ default: () => null }))
vi.mock('./routes/ContentMachineSettings', () => ({ default: () => null }))

import App from './App'

describe('App responsive shell', () => {
  it('uses the compact shell for tablet and mobile widths', () => {
    const { getByText, queryByText } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <App />
      </MemoryRouter>,
    )

    expect(mocks.useMediaQuery).toHaveBeenCalledWith('(max-width: 1023px)')
    expect(getByText('Mobile navigation')).toBeInTheDocument()
    expect(queryByText('Desktop sidebar')).not.toBeInTheDocument()
  })
})

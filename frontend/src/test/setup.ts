import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { forwardRef, Fragment, createElement, type ComponentPropsWithoutRef, type ReactNode } from 'react'
import { afterEach, beforeAll, vi } from 'vitest'

vi.mock('framer-motion', () => {
  const motionPropNames = new Set([
    'animate',
    'initial',
    'exit',
    'transition',
    'whileHover',
    'whileTap',
    'whileDrag',
    'layout',
    'layoutId',
    'drag',
    'dragMomentum',
    'dragElastic',
    'dragTransition',
    'onDrag',
    'onDragStart',
    'onDragEnd',
  ])
  const stripMotionProps = (props: Record<string, unknown>) =>
    Object.fromEntries(Object.entries(props).filter(([key]) => !motionPropNames.has(key)))

  const motion = new Proxy(
    {},
    {
      get: (_target, key) =>
        forwardRef<HTMLElement, ComponentPropsWithoutRef<'div'>>(({ children, ...props }, ref) =>
          createElement(String(key), { ...stripMotionProps(props), ref }, children),
        ),
    },
  )

  return {
    motion,
    AnimatePresence: ({ children }: { children: ReactNode }) =>
      createElement(Fragment, null, children),
    useReducedMotion: () => false,
  }
})

const matchMediaMock = vi.fn().mockImplementation((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  addListener: vi.fn(),
  removeListener: vi.fn(),
  dispatchEvent: vi.fn(),
}))

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: matchMediaMock,
})

Object.defineProperty(globalThis, 'matchMedia', {
  writable: true,
  value: matchMediaMock,
})

beforeAll(() => {
  Object.defineProperty(window, 'scrollTo', {
    writable: true,
    value: vi.fn(),
  })

  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    writable: true,
    value: vi.fn(),
  })

  Object.defineProperty(globalThis, 'ResizeObserver', {
    writable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  })

  if (!globalThis.crypto?.randomUUID) {
    Object.defineProperty(globalThis, 'crypto', {
      writable: true,
      value: {
        randomUUID: () => 'test-random-uuid',
      },
    })
  }

  if (!URL.createObjectURL) {
    Object.defineProperty(URL, 'createObjectURL', {
      writable: true,
      value: vi.fn(() => 'blob:test-preview'),
    })
  }

  if (!URL.revokeObjectURL) {
    Object.defineProperty(URL, 'revokeObjectURL', {
      writable: true,
      value: vi.fn(),
    })
  }
})

afterEach(() => {
  cleanup()
})

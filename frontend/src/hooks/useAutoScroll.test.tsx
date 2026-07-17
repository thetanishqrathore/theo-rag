import { act, fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAutoScroll } from './useAutoScroll'

function AutoScrollHarness({ enabled = true }: { enabled?: boolean }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const { isSticky, showJump, forceSticky } = useAutoScroll(scrollRef, contentRef, { enabled })

  return (
    <div>
      <div data-testid="scroll" ref={scrollRef}>
        <div data-testid="content" ref={contentRef} />
      </div>
      <button type="button" data-testid="force-sticky" onClick={forceSticky}>
        force
      </button>
      <div data-testid="sticky-state">{isSticky ? 'sticky' : 'unsticky'}</div>
      <div data-testid="jump-state">{showJump ? 'show' : 'hide'}</div>
    </div>
  )
}

function setupScrollableMetrics(el: HTMLDivElement) {
  let top = 500
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: 1000 })
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: 500 })
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (value: number) => {
      top = value
    },
  })
  el.scrollTo = vi.fn((opts?: ScrollToOptions) => {
    if (typeof opts?.top === 'number') top = opts.top
  })
  return {
    setScrollTop: (value: number) => {
      top = value
    },
  }
}

describe('useAutoScroll', () => {
  beforeEach(() => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0)
      return 1
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query: string) =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => false,
        }) as MediaQueryList
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('honors manual scroll-up even during programmatic scroll window', () => {
    render(<AutoScrollHarness />)
    const scroll = screen.getByTestId('scroll') as HTMLDivElement
    const forceStickyButton = screen.getByTestId('force-sticky')
    const { setScrollTop } = setupScrollableMetrics(scroll)

    act(() => {
      fireEvent.click(forceStickyButton)
    })

    act(() => {
      setScrollTop(120)
      fireEvent.scroll(scroll)
    })

    expect(screen.getByTestId('sticky-state').textContent).toBe('unsticky')
    expect(screen.getByTestId('jump-state').textContent).toBe('show')
  })

  it('resets sticky and jump state when forceSticky is invoked', () => {
    render(<AutoScrollHarness />)
    const scroll = screen.getByTestId('scroll') as HTMLDivElement
    const forceStickyButton = screen.getByTestId('force-sticky')
    const { setScrollTop } = setupScrollableMetrics(scroll)

    act(() => {
      setScrollTop(120)
      fireEvent.scroll(scroll)
    })

    expect(screen.getByTestId('sticky-state').textContent).toBe('unsticky')
    expect(screen.getByTestId('jump-state').textContent).toBe('show')

    act(() => {
      fireEvent.click(forceStickyButton)
    })

    expect(screen.getByTestId('sticky-state').textContent).toBe('sticky')
    expect(screen.getByTestId('jump-state').textContent).toBe('hide')
  })
})

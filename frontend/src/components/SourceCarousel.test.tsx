import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { SourceCarousel } from './SourceCarousel'

const citations = [
  {
    evidence_id: 'ev-1',
    parent_id: 'p1',
    label: 'Source 1',
    path: 'path-1',
    is_primary: true,
    context_only: false,
    preview: 'preview 1',
  },
  {
    evidence_id: 'ev-2',
    parent_id: 'p2',
    label: 'Source 2',
    path: 'path-2',
    is_primary: true,
    context_only: false,
    preview: 'preview 2',
  },
  {
    evidence_id: 'ev-3',
    parent_id: 'p3',
    label: 'Source 3',
    path: 'path-3',
    is_primary: false,
    context_only: true,
    preview: 'preview 3',
  },
]

describe('SourceCarousel', () => {
  it('shows active indicator for first source by default', () => {
    const { container } = render(<SourceCarousel citations={citations} />)
    const dots = container.querySelectorAll('.source-carousel-dot')
    expect(dots[0].className).toContain('active')
  })

  it('scrolls to selected source when a dot is clicked', async () => {
    const { container } = render(<SourceCarousel citations={citations} />)

    const rail = container.querySelector('.source-carousel-rail') as HTMLDivElement
    const cards = Array.from(container.querySelectorAll('.source-carousel-card')) as HTMLButtonElement[]
    const scrollTo = vi.fn()
    rail.scrollTo = scrollTo

    Object.defineProperty(rail, 'clientWidth', { value: 320, configurable: true })
    cards.forEach((card, idx) => {
      Object.defineProperty(card, 'offsetLeft', { value: idx * 220, configurable: true })
      Object.defineProperty(card, 'offsetWidth', { value: 220, configurable: true })
    })

    await userEvent.click(screen.getByRole('button', { name: /view source 2/i }))

    expect(scrollTo).toHaveBeenCalledTimes(1)
    expect(scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({
        behavior: 'smooth',
      })
    )
  })

  it('does not trigger source selection on click immediately after mouse drag', () => {
    const onSelect = vi.fn()
    const { container } = render(<SourceCarousel citations={citations} onSelect={onSelect} />)

    const rail = container.querySelector('.source-carousel-rail') as HTMLDivElement
    const firstCard = container.querySelector('.source-carousel-card') as HTMLButtonElement

    fireEvent.pointerDown(rail, { pointerType: 'mouse', button: 0, clientX: 240, pointerId: 1 })
    fireEvent.pointerMove(rail, { pointerType: 'mouse', button: 0, clientX: 210, pointerId: 1 })
    fireEvent.pointerUp(rail, { pointerType: 'mouse', button: 0, clientX: 210, pointerId: 1 })
    fireEvent.click(firstCard)

    expect(onSelect).not.toHaveBeenCalled()
  })
})

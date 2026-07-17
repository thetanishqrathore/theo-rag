import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EvidenceInspector } from './EvidenceInspector'

const fetchEvidenceMock = vi.fn()
const resolveApiUrlMock = vi.fn((path: string) =>
  `https://api.example.com${path.startsWith('/') ? path : `/${path}`}`
)

vi.mock('../lib/api', () => ({
  fetchEvidence: (...args: unknown[]) => fetchEvidenceMock(...args),
  resolveApiUrl: (...args: unknown[]) => resolveApiUrlMock(...args),
}))

describe('EvidenceInspector', () => {
  beforeEach(() => {
    fetchEvidenceMock.mockReset()
    resolveApiUrlMock.mockClear()
    vi.spyOn(window, 'open').mockImplementation(() => null)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('builds static source url using shared api resolver', async () => {
    fetchEvidenceMock.mockRejectedValue(new Error('unavailable'))

    render(
      <EvidenceInspector
        evidence={{
          evidence_id: 'ev-static',
          parent_id: 'p1',
          label: 'Source',
          path: 'books/genesis/lesson_01.pdf',
          is_primary: true,
          context_only: false,
          preview: 'excerpt',
        }}
        onClose={() => {}}
      />
    )

    await userEvent.click(screen.getByRole('button', { name: /open source/i }))
    expect(resolveApiUrlMock).toHaveBeenCalledWith('/static/books/genesis/lesson_01.html')
    expect(window.open).toHaveBeenCalledWith(
      'https://api.example.com/static/books/genesis/lesson_01.html',
      '_blank',
      'noopener'
    )
  })

  it('resolves relative canonical_url with shared api resolver', async () => {
    fetchEvidenceMock.mockResolvedValue({
      evidence_id: 'ev-relative',
      parent_id: 'p1',
      label: 'Source',
      path: 'books/exodus/lesson_01.pdf',
      primary_text: 'primary',
      context_before: [],
      context_after: [],
      metadata: {
        canonical_url: '/canonical/lesson-1',
      },
    })

    render(
      <EvidenceInspector
        evidence={{
          evidence_id: 'ev-relative',
          parent_id: 'p1',
          label: 'Source',
          path: 'books/exodus/lesson_01.pdf',
          is_primary: true,
          context_only: false,
          preview: 'excerpt',
        }}
        onClose={() => {}}
      />
    )

    await waitFor(() => expect(fetchEvidenceMock).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('button', { name: /open source/i }))

    expect(resolveApiUrlMock).toHaveBeenCalledWith('/canonical/lesson-1')
    expect(window.open).toHaveBeenCalledWith(
      'https://api.example.com/canonical/lesson-1',
      '_blank',
      'noopener'
    )
  })

  it('supports prev/next controls and emits copy toast', async () => {
    fetchEvidenceMock.mockResolvedValue({
      evidence_id: 'ev-nav',
      parent_id: 'p1',
      label: 'Source',
      path: 'books/exodus/lesson_01.pdf',
      primary_text: 'primary text',
      context_before: [],
      context_after: [],
      metadata: {},
    })

    const onPrev = vi.fn()
    const onNext = vi.fn()
    const onToast = vi.fn()

    render(
      <EvidenceInspector
        evidence={{
          evidence_id: 'ev-nav',
          parent_id: 'p1',
          label: 'Source',
          path: 'books/exodus/lesson_01.pdf',
          is_primary: true,
          context_only: false,
          preview: 'excerpt',
        }}
        canPrev={false}
        canNext
        onPrev={onPrev}
        onNext={onNext}
        positionLabel="1 / 3"
        onToast={onToast}
        onClose={() => {}}
      />
    )

    const prevButton = screen.getByRole('button', { name: /previous evidence/i })
    const nextButton = screen.getByRole('button', { name: /next evidence/i })
    expect(prevButton.hasAttribute('disabled')).toBe(true)
    expect(nextButton.hasAttribute('disabled')).toBe(false)

    await userEvent.click(nextButton)
    expect(onNext).toHaveBeenCalledTimes(1)
    expect(onPrev).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /copy excerpt/i }))
    await waitFor(() => {
      expect(onToast).toHaveBeenCalledWith('Excerpt copied to clipboard.')
    })
  })
})

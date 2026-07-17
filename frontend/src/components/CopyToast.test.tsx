import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CopyToast } from './CopyToast'

describe('CopyToast', () => {
  it('announces copy feedback as polite live status', () => {
    render(<CopyToast toast={{ id: 1, message: 'Copied to clipboard', tone: 'success' }} />)

    const toast = screen.getByRole('status')
    expect(toast.textContent).toContain('Copied to clipboard')
    expect(toast.getAttribute('aria-live')).toBe('polite')
  })
})

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { InputBar } from './InputBar'

describe('InputBar', () => {
  it('navigates corpus options with arrow keys and selects on Enter', async () => {
    const user = userEvent.setup()
    const onCorpusChange = vi.fn()

    render(
      <InputBar
        onSend={() => {}}
        corpus="clough"
        corpora={['clough', 'kjv', 'niv']}
        onCorpusChange={onCorpusChange}
      />
    )

    const trigger = screen.getByRole('button', { name: /clough/i })
    trigger.focus()
    await user.keyboard('{ArrowDown}')

    const listbox = await screen.findByRole('listbox')
    const options = within(listbox).getAllByRole('option')
    await waitFor(() => {
      expect(document.activeElement).toBe(options[0])
    })

    await user.keyboard('{ArrowDown}')
    await waitFor(() => {
      expect(document.activeElement).toBe(options[1])
    })

    await user.keyboard('{Enter}')

    expect(onCorpusChange).toHaveBeenCalledWith('kjv')
    await waitFor(() => {
      expect(screen.queryByRole('listbox')).toBeNull()
      expect(document.activeElement).toBe(trigger)
    })
  })

  it('closes the corpus list on Escape and restores trigger focus', async () => {
    const user = userEvent.setup()

    render(
      <InputBar
        onSend={() => {}}
        corpus="clough"
        corpora={['clough', 'kjv', 'niv']}
        onCorpusChange={() => {}}
      />
    )

    const trigger = screen.getByRole('button', { name: /clough/i })
    await user.click(trigger)
    expect(screen.getByRole('listbox')).not.toBeNull()

    await user.keyboard('{Escape}')

    await waitFor(() => {
      expect(screen.queryByRole('listbox')).toBeNull()
      expect(document.activeElement).toBe(trigger)
    })
  })
})

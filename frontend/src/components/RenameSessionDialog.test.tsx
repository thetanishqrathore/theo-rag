import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { RenameSessionDialog } from './RenameSessionDialog'

describe('RenameSessionDialog', () => {
  it('submits a valid title with Enter', async () => {
    const onSave = vi.fn()

    render(
      <RenameSessionDialog
        open
        initialValue="Current title"
        onSave={onSave}
        onCancel={() => {}}
      />
    )

    const input = screen.getByRole('textbox', { name: /chat title/i })
    await userEvent.clear(input)
    await userEvent.type(input, 'Updated title{Enter}')

    expect(onSave).toHaveBeenCalledWith('Updated title')
  })

  it('closes on Escape', async () => {
    const onCancel = vi.fn()

    render(
      <RenameSessionDialog
        open
        initialValue="Current title"
        onSave={() => {}}
        onCancel={onCancel}
      />
    )

    await userEvent.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalled()
  })

  it('rejects empty title', async () => {
    const onSave = vi.fn()

    render(
      <RenameSessionDialog
        open
        initialValue="Current title"
        onSave={onSave}
        onCancel={() => {}}
      />
    )

    const input = screen.getByRole('textbox', { name: /chat title/i })
    await userEvent.clear(input)
    expect((screen.getByRole('button', { name: /save/i }) as HTMLButtonElement).disabled).toBe(true)
    await userEvent.keyboard('{Enter}')
    expect(onSave).not.toHaveBeenCalled()
  })
})

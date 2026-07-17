import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { StarterPrompts } from './StarterPrompts'

describe('StarterPrompts', () => {
  it('renders starter prompts and emits selected prompt', async () => {
    const onSelectPrompt = vi.fn()
    render(<StarterPrompts onSelectPrompt={onSelectPrompt} />)

    const firstPrompt = screen.getByRole('button', { name: /what does genesis 1 teach about creation and order\?/i })
    await userEvent.click(firstPrompt)

    expect(onSelectPrompt).toHaveBeenCalledWith('What does Genesis 1 teach about creation and order?')
  })
})

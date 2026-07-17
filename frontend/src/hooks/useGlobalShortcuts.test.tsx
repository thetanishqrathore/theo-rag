import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useGlobalShortcuts } from './useGlobalShortcuts'

function Harness({
  onNewChat,
  onFocusComposer,
  disabled,
}: {
  onNewChat: () => void
  onFocusComposer: () => void
  disabled?: boolean
}) {
  useGlobalShortcuts({ onNewChat, onFocusComposer, disabled })
  return null
}

describe('useGlobalShortcuts', () => {
  it('handles Meta/Ctrl+N and Meta/Ctrl+K', () => {
    const onNewChat = vi.fn()
    const onFocusComposer = vi.fn()
    render(<Harness onNewChat={onNewChat} onFocusComposer={onFocusComposer} />)

    fireEvent.keyDown(window, { key: 'n', metaKey: true })
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })

    expect(onNewChat).toHaveBeenCalledTimes(1)
    expect(onFocusComposer).toHaveBeenCalledTimes(1)
  })

  it('ignores shortcuts when disabled', () => {
    const onNewChat = vi.fn()
    const onFocusComposer = vi.fn()
    render(<Harness onNewChat={onNewChat} onFocusComposer={onFocusComposer} disabled />)

    fireEvent.keyDown(window, { key: 'n', metaKey: true })
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })

    expect(onNewChat).not.toHaveBeenCalled()
    expect(onFocusComposer).not.toHaveBeenCalled()
  })
})

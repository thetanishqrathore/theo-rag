import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Sidebar } from './Sidebar'

const sessions = [
  {
    id: 's1',
    title: 'First chat',
    updatedAt: Date.now(),
    messages: [{ id: 'm1', role: 'user' as const, content: 'hello', ts: Date.now() }],
  },
]

describe('Sidebar', () => {
  it('selects a session via keyboard activation', async () => {
    const onSelectSession = vi.fn()

    render(
      <Sidebar
        sessions={sessions}
        currentSessionId={null}
        onSelectSession={onSelectSession}
        onNewSession={() => {}}
        onDeleteSession={() => {}}
        onRenameSession={() => {}}
        onTogglePinSession={() => {}}
        collapsed={false}
        onToggleCollapse={() => {}}
      />
    )

    const rowButton = screen.getByRole('button', { name: /open chat first chat/i })
    rowButton.focus()
    await userEvent.keyboard('{Enter}')

    expect(onSelectSession).toHaveBeenCalledWith('s1')
  })

  it('renders row actions and supports rename', async () => {
    const onRenameSession = vi.fn()

    render(
      <Sidebar
        sessions={sessions}
        currentSessionId={null}
        onSelectSession={() => {}}
        onNewSession={() => {}}
        onDeleteSession={() => {}}
        onRenameSession={onRenameSession}
        onTogglePinSession={() => {}}
        collapsed={false}
        onToggleCollapse={() => {}}
        mobile
        mobileOpen
      />
    )

    const renameButtons = screen.getAllByRole('button', { name: /rename chat/i })
    expect(renameButtons.length).toBeGreaterThan(0)
    await userEvent.click(renameButtons[0])

    expect(onRenameSession).toHaveBeenCalledWith('s1')
  })
})

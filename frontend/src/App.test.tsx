import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const useChatStoreMock = vi.fn()

vi.mock('./store/chatStore', () => ({
  useChatStore: () => useChatStoreMock(),
}))

vi.mock('./components/Sidebar', () => ({
  Sidebar: ({
    onNewSession,
    onSelectSession,
  }: {
    onNewSession: () => void
    onSelectSession: (id: string) => void
  }) => (
    <div>
      <button type="button" onClick={onNewSession}>
        Sidebar new chat
      </button>
      <button type="button" onClick={() => onSelectSession('s2')}>
        Sidebar switch session
      </button>
    </div>
  ),
}))

vi.mock('./components/InputBar', () => ({
  InputBar: () => <div>Input bar</div>,
}))

vi.mock('./components/ModelSelector', () => ({
  ModelSelector: () => <div>Model selector</div>,
}))

vi.mock('./components/RenameSessionDialog', () => ({
  RenameSessionDialog: () => <div>Rename dialog</div>,
}))

vi.mock('./components/MessageBubble', () => ({
  MessageBubble: ({ onToast }: { onToast?: (message: string, tone?: 'success' | 'error') => void }) => (
    <button type="button" onClick={() => onToast?.('Answer copied to clipboard.')}>Trigger copy toast</button>
  ),
}))

function buildStore(overrides?: Record<string, unknown>) {
  return {
    sessions: [
      {
        id: 's1',
        title: 'Session 1',
        updatedAt: Date.now(),
        messages: [],
      },
    ],
    currentSessionId: 's1',
    streaming: { active: false, buffer: '' },
    collections: ['clough'],
    models: ['llama3.2'],
    selectedModel: 'llama3.2',
    modelStatus: 'ready',
    selectedCorpus: 'clough',
    init: vi.fn().mockResolvedValue(undefined),
    createSession: vi.fn(),
    setActiveSession: vi.fn(),
    setSelectedModel: vi.fn(),
    setSelectedCorpus: vi.fn(),
    deleteSession: vi.fn(),
    renameSession: vi.fn(),
    togglePinSession: vi.fn(),
    sendMessage: vi.fn(),
    cancelStream: vi.fn(),
    health: 'ok',
    ...overrides,
  }
}

/** Render App and flush the init() promise so boot screen clears */
async function renderApp() {
  await act(async () => {
    render(<App />)
  })
}

describe('App', () => {
  beforeEach(() => {
    useChatStoreMock.mockReset()
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      writable: true,
      value: vi.fn(),
    })
  })

  it('does not render starter prompt cards in hero state', async () => {
    useChatStoreMock.mockReturnValue(buildStore())

    await renderApp()

    expect(screen.queryByText(/try a starter question/i)).toBeNull()
  })

  it('wires global shortcuts to new chat and focus-input dispatch', async () => {
    const createSession = vi.fn()
    useChatStoreMock.mockReturnValue(buildStore({ createSession }))
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    await renderApp()

    fireEvent.keyDown(window, { key: 'n', metaKey: true })
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true })

    expect(createSession).toHaveBeenCalledTimes(1)
    expect(dispatchSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'focus-input' }))
  })

  it('renders global copy toast when message bubble emits copy feedback', async () => {
    vi.useFakeTimers()
    useChatStoreMock.mockReturnValue(
      buildStore({
        sessions: [
          {
            id: 's1',
            title: 'Session 1',
            updatedAt: Date.now(),
            messages: [
              {
                id: 'a1',
                role: 'assistant',
                content: 'Answer',
                ts: Date.now(),
                answer_mode: 'confident',
                grounded: true,
                refusal: false,
                evidence: [],
                used_evidence_ids: [],
                follow_ups: [],
              },
            ],
          },
        ],
      })
    )

    await act(async () => {
      render(<App />)
      // Flush the init() microtask under fake timers
      await vi.runAllTimersAsync()
    })

    fireEvent.click(screen.getByRole('button', { name: /trigger copy toast/i }))

    expect(screen.getByRole('status').textContent).toContain('Answer copied to clipboard.')

    await act(async () => {
      vi.advanceTimersByTime(1700)
    })
    expect(screen.queryByRole('status')).toBeNull()
    vi.useRealTimers()
  })

  it('shows a short skeleton placeholder when switching sessions', async () => {
    vi.useFakeTimers()
    useChatStoreMock.mockReturnValue(
      buildStore({
        sessions: [
          {
            id: 's1',
            title: 'Session 1',
            updatedAt: Date.now(),
            messages: [
              {
                id: 'u1',
                role: 'user',
                content: 'What is grace?',
                ts: Date.now(),
              },
            ],
          },
          {
            id: 's2',
            title: 'Session 2',
            updatedAt: Date.now() - 5000,
            messages: [
              {
                id: 'u2',
                role: 'user',
                content: 'What is mercy?',
                ts: Date.now(),
              },
            ],
          },
        ],
      })
    )

    await act(async () => {
      render(<App />)
      await vi.runAllTimersAsync()
    })

    fireEvent.click(screen.getByRole('button', { name: /sidebar switch session/i }))

    expect(screen.getByLabelText(/loading messages/i)).toBeTruthy()

    await act(async () => {
      vi.advanceTimersByTime(240)
    })

    expect(screen.queryByLabelText(/loading messages/i)).toBeNull()
    vi.useRealTimers()
  })
})

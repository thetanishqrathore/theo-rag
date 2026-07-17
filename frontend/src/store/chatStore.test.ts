import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { StreamResult } from '../lib/api'

vi.mock('../lib/api', () => ({
  fetchCollections: vi.fn(async () => ({ collections: [] })),
  fetchHealth: vi.fn(async () => ({ status: 'healthy', services: {} })),
  fetchModels: vi.fn(async () => ({ models: ['m1'], default: 'm1' })),
  streamQuery: vi.fn(),
}))

vi.mock('../lib/storage', () => ({
  loadSessions: vi.fn(async () => []),
  saveSessions: vi.fn(async () => undefined),
  loadSettings: vi.fn(async () => ({})),
  saveSettings: vi.fn(async () => undefined),
}))

import { streamQuery } from '../lib/api'
import { useChatStore } from './chatStore'

const mockedStreamQuery = vi.mocked(streamQuery)

describe('chatStore streaming lifecycle', () => {
  beforeEach(() => {
    useChatStore.setState({
      sessions: [
        {
          id: 's1',
          title: 'Session',
          updatedAt: Date.now(),
          messages: [],
        },
      ],
      currentSessionId: 's1',
      streaming: {
        active: false,
        lifecycle: 'idle',
        assistant_id: null,
        buffer: '',
        citations: [],
      },
    })
    mockedStreamQuery.mockReset()
  })

  it('aborts an active stream and marks assistant message as interrupted', async () => {
    mockedStreamQuery.mockImplementation(
      async (_payload, handlers, options): Promise<StreamResult> =>
        await new Promise((resolve) => {
          handlers.onStatus?.({ type: 'status', phase: 'generating', message: 'Generating...' })
          options?.signal?.addEventListener(
            'abort',
            () => {
              resolve({ ok: false, error_type: 'aborted', message: 'Request canceled.', finalReceived: false })
            },
            { once: true }
          )
        })
    )

    const sendPromise = useChatStore.getState().sendMessage('hello', 'clough', 'm1')

    useChatStore.getState().cancelStream('Canceled by test.')
    await sendPromise

    const state = useChatStore.getState()
    expect(state.streaming.active).toBe(false)
    expect(state.streaming.lifecycle).toBe('aborted')

    const session = state.sessions.find((s) => s.id === 's1')
    const assistant = session?.messages.find((m) => m.role === 'assistant')
    expect(assistant && assistant.role === 'assistant' ? assistant.stream_error_type : null).toBe('aborted')
  })
})

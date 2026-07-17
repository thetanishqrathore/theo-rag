import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const encoder = new TextEncoder()

function streamResponse(chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('api fallback and streaming behavior', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('falls back to the next candidate when first base returns 404', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('not found', { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'healthy' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const { fetchHealth, getResolvedApiBase, resolveApiUrl } = await import('./api')
    const payload = await fetchHealth()

    expect(payload.status).toBe('healthy')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(getResolvedApiBase()).toContain(':8000')
    expect(resolveApiUrl('/api/health')).toContain(':8000/api/health')
  })

  it('falls back to next candidate on network error', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('network down'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ models: ['m1'], default: 'm1' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const { fetchModels } = await import('./api')
    const payload = await fetchModels()

    expect(payload.models).toEqual(['m1'])
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not mask request validation errors with fallback candidates', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Invalid request payload' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        })
      )
    vi.stubGlobal('fetch', fetchMock)

    const { streamQuery } = await import('./api')
    const onError = vi.fn()
    const result = await streamQuery({ query: 'q', corpus: 'clough' }, { onError })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error_type).toBe('validation_error')
    }
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({
        error_type: 'validation_error',
      })
    )
  })

  it('returns ok when final event is received', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamResponse([
          'event: message\n',
          'data: {"type":"status","phase":"retrieving","message":"Finding evidence..."}\n\n',
          'event: message\n',
          'data: {"type":"final","answer_mode":"confident","grounded":true,"refusal":false,"generation_time_ms":10,"follow_ups":[],"used_evidence_ids":[]}\n\n',
        ])
      )
    )

    const { streamQuery } = await import('./api')
    const onFinal = vi.fn()
    const result = await streamQuery({ query: 'q', corpus: 'clough' }, { onFinal })

    expect(result.ok).toBe(true)
    expect(onFinal).toHaveBeenCalledTimes(1)
  })

  it('marks incomplete stream when final event is missing', async () => {
    const onError = vi.fn()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        streamResponse([
          'event: message\n',
          'data: {"type":"status","phase":"retrieving","message":"Finding evidence..."}\n\n',
          'event: message\n',
          'data: {"type":"token","content":"hello"}\n\n',
        ])
      )
    )

    const { streamQuery } = await import('./api')
    const result = await streamQuery({ query: 'q', corpus: 'clough' }, { onError })

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error_type).toBe('stream_incomplete')
    }
    expect(onError).toHaveBeenCalled()
  })

  it('supports cancellation via AbortSignal', async () => {
    const controller = new AbortController()
    controller.abort()
    const onError = vi.fn()

    const { streamQuery } = await import('./api')
    const result = await streamQuery(
      { query: 'q', corpus: 'clough' },
      { onError },
      { signal: controller.signal }
    )

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error_type).toBe('aborted')
    }
  })
})

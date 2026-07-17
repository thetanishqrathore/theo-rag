export type StatusPhase = 'warmup' | 'retrieving' | 'reranking' | 'building' | 'generating' | 'finalizing'
export type RetrievalMode = 'fast' | 'deep'
export type StreamErrorType =
  | 'ollama_unavailable'
  | 'busy'
  | 'model_not_found'
  | 'resource_exhausted'
  | 'service_unavailable'
  | 'network_error'
  | 'stream_incomplete'
  | 'aborted'
  | 'parse_error'
  | 'validation_error'
  | 'timeout'
  | 'http_error'
  | 'stream_error'

export type StatusEvent = {
  type: 'status'
  phase?: StatusPhase
  message: string
}

export type EvidencePreview = {
  evidence_id: string
  parent_id: string
  label: string
  path: string
  lesson?: number | string
  date?: string
  speaker?: string
  child_chunk_index?: number | null
  is_primary: boolean
  context_only: boolean
  source_tag?: string | null
  collection?: string | null
  book_slug?: string | null
  lesson_code?: string | null
  node_path?: string | null
  section_path?: string | null
  citation_ref?: string | null
  page_start?: number | null
  page_end?: number | null
  is_front_matter?: boolean
  preview: string
}

export type CitationsEvent = {
  type: 'citations'
  retrieval_time_ms?: number
  items: EvidencePreview[]
  citation_fallback_reason?: string | null
  citation_strategy_version?: string | null
  num_citations_emitted?: number | null
  num_citations_matched?: number | null
}

export type TokenEvent = {
  type: 'token'
  content: string
}

export type FinalEvent = {
  type: 'final'
  answer_mode: 'confident' | 'narrow' | 'refuse'
  grounded: boolean
  refusal: boolean
  answer_text_final?: string
  grounding_reason?: string
  citation_retry_used?: boolean
  citation_retry_succeeded?: boolean
  citation_automap_used?: boolean
  citation_automap_succeeded?: boolean
  generation_time_ms: number
  follow_ups: string[]
  used_evidence_ids: string[]
  citation_fallback_reason?: string | null
  citation_strategy_version?: string | null
  num_citations_emitted?: number | null
  num_citations_matched?: number | null
}

export type SSEEvent = StatusEvent | CitationsEvent | TokenEvent | FinalEvent

export type StreamHandlers = {
  onStatus?: (event: StatusEvent) => void
  onCitations?: (event: CitationsEvent) => void
  onToken?: (event: TokenEvent) => void
  onFinal?: (event: FinalEvent) => void
  onError?: (error: { message: string; error_type?: StreamErrorType }) => void
}

export type StreamResult =
  | { ok: true; finalReceived: boolean }
  | { ok: false; error_type: StreamErrorType; message: string; finalReceived: boolean }

export type ConversationTurn = { role: string; content: string }

const EXPLICIT_API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/+$/, '') || ''
const SAME_ORIGIN_BASE = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000'
const LEGACY_8000_BASE =
  typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : 'http://localhost:8000'
const LEGACY_HTTP_8000_BASE =
  typeof window !== 'undefined' ? `http://${window.location.hostname}:8000` : 'http://localhost:8000'
const API_BASE_CANDIDATES = Array.from(
  new Set(
    EXPLICIT_API_BASE
      ? [EXPLICIT_API_BASE]
      : [SAME_ORIGIN_BASE, LEGACY_8000_BASE, LEGACY_HTTP_8000_BASE]
  )
)
let resolvedApiBase = EXPLICIT_API_BASE || ''

function withLeadingSlash(path: string) {
  return path.startsWith('/') ? path : `/${path}`
}

function markResolvedBase(base: string) {
  resolvedApiBase = base
}

export function getResolvedApiBase() {
  if (resolvedApiBase) return resolvedApiBase
  return API_BASE_CANDIDATES[0] || 'http://localhost:8000'
}

export function resolveApiUrl(path: string) {
  return `${getResolvedApiBase()}${withLeadingSlash(path)}`
}

async function fetchFromCandidates(
  path: string,
  init?: RequestInit,
  options?: { fallbackOnStatus?: number[] }
): Promise<Response> {
  let lastError: unknown
  const fallbackStatuses = options?.fallbackOnStatus || [404, 405]
  const normalizedPath = withLeadingSlash(path)

  for (let index = 0; index < API_BASE_CANDIDATES.length; index += 1) {
    const base = API_BASE_CANDIDATES[index]
    const url = `${base}${normalizedPath}`
    const isLastCandidate = index === API_BASE_CANDIDATES.length - 1

    try {
      const res = await fetch(url, init)
      if (res.ok) {
        markResolvedBase(base)
        return res
      }

      const shouldFallback =
        !isLastCandidate && (fallbackStatuses.includes(res.status) || res.status >= 500)

      if (shouldFallback) {
        lastError = new Error(`Request failed (${res.status}) at ${url}`)
        continue
      }

      markResolvedBase(base)
      return res
    } catch (err) {
      lastError = err
    }
  }
  if (lastError instanceof Error) throw lastError
  throw new Error('Unable to reach API endpoint')
}

export async function fetchCollections() {
  const res = await fetchFromCandidates('/api/collections', undefined, { fallbackOnStatus: [404, 405] })
  if (!res.ok) throw new Error('Failed to load collections')
  return res.json()
}

export async function fetchModels() {
  const res = await fetchFromCandidates('/api/models', undefined, { fallbackOnStatus: [404, 405] })
  if (!res.ok) throw new Error('Failed to load models')
  return res.json()
}

export async function fetchHealth() {
  const res = await fetchFromCandidates('/api/health', undefined, { fallbackOnStatus: [404, 405] })
  if (!res.ok) throw new Error('Failed to load health')
  return res.json()
}

export async function fetchEvidence(evidenceId: string) {
  const res = await fetchFromCandidates(`/api/evidence/${evidenceId}`, undefined, { fallbackOnStatus: [404, 405] })
  if (!res.ok) throw new Error('Failed to load evidence')
  return res.json()
}

export async function streamQuery(
  payload: {
    query: string
    corpus: string
    model?: string
    history?: ConversationTurn[]
    top_k?: number
    retrieval_mode?: RetrievalMode
  },
  handlers: StreamHandlers,
  options?: { signal?: AbortSignal; watchdogMs?: number }
): Promise<StreamResult> {
  const watchdogMs = Math.max(1000, options?.watchdogMs ?? 120000)
  const controller = new AbortController()
  let watchdogTimer: ReturnType<typeof setTimeout> | null = null
  const finalizeWatchdog = () => {
    if (watchdogTimer) {
      clearTimeout(watchdogTimer)
      watchdogTimer = null
    }
  }
  const resetWatchdog = () => {
    finalizeWatchdog()
    watchdogTimer = setTimeout(() => controller.abort('watchdog_timeout'), watchdogMs)
  }

  if (options?.signal) {
    if (options.signal.aborted) {
      return { ok: false, error_type: 'aborted', message: 'Request canceled.', finalReceived: false }
    }
    options.signal.addEventListener('abort', () => controller.abort('external_abort'), { once: true })
  }

  let res: Response
  try {
    res = await fetchFromCandidates('/api/query/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    }, { fallbackOnStatus: [404, 405] })
  } catch (err) {
    const aborted = err instanceof DOMException && err.name === 'AbortError'
    const error_type: StreamErrorType = aborted ? 'aborted' : 'network_error'
    const message = aborted ? 'Request canceled.' : 'Network error while starting stream.'
    handlers.onError?.({ message, error_type })
    finalizeWatchdog()
    return { ok: false, error_type, message, finalReceived: false }
  }

  if (!res.ok || !res.body) {
    let message = `Stream failed: ${res.status}`
    let errorType: StreamErrorType = 'http_error'
    try {
      const errorPayload = await res.json()
      if (errorPayload?.detail) {
        if (typeof errorPayload.detail === 'string') {
          message = errorPayload.detail
        } else if (typeof errorPayload.detail?.error === 'string') {
          message = errorPayload.detail.error
        }
      }
    } catch {
      try {
        const text = await res.text()
        if (text?.trim()) message = text.trim()
      } catch {
        // Keep fallback message if body parsing fails.
      }
    }
    if (res.status === 503) {
      errorType = /busy/i.test(message) ? 'busy' : 'service_unavailable'
    } else if (res.status === 504) {
      errorType = 'timeout'
    } else if (res.status === 400) {
      errorType = 'validation_error'
    }
    handlers.onError?.({ message, error_type: errorType })
    finalizeWatchdog()
    return { ok: false, error_type: errorType, message, finalReceived: false }
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''
  let currentData: string[] = []
  let finalReceived = false

  const dispatch = (): { ok: true } | { ok: false; error_type: StreamErrorType; message: string } => {
    if (!currentEvent && currentData.length === 0) return { ok: true }
    const payloadText = currentData.join('\n')
    currentEvent = ''
    currentData = []
    if (!payloadText) return { ok: true }
    try {
      const parsed: SSEEvent = JSON.parse(payloadText)
      switch (parsed.type) {
        case 'status':
          handlers.onStatus?.(parsed)
          break
        case 'citations':
          handlers.onCitations?.(parsed)
          break
        case 'token':
          handlers.onToken?.(parsed)
          break
        case 'final':
          finalReceived = true
          handlers.onFinal?.(parsed)
          break
        default:
          break
      }
      return { ok: true }
    } catch {
      const message = 'Failed to parse SSE event'
      handlers.onError?.({ message, error_type: 'parse_error' })
      return { ok: false, error_type: 'parse_error', message }
    }
  }

  resetWatchdog()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        const outcome = dispatch()
        if (outcome && !outcome.ok) {
          return { ok: false, error_type: outcome.error_type, message: outcome.message, finalReceived }
        }
        if (!finalReceived) {
          const message = 'Stream ended before completion.'
          handlers.onError?.({ message, error_type: 'stream_incomplete' })
          return { ok: false, error_type: 'stream_incomplete', message, finalReceived }
        }
        return { ok: true, finalReceived }
      }

      resetWatchdog()
      buffer += decoder.decode(value, { stream: true })
      let newlineIndex
      while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
        const rawLine = buffer.slice(0, newlineIndex)
        buffer = buffer.slice(newlineIndex + 1)
        const line = rawLine.replace(/\r$/, '')
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          const value = line.slice(5).replace(/^ /, '')
          currentData.push(value)
        } else if (line === '') {
          if (currentEvent === 'error') {
            try {
              const payloadText = currentData.join('\n')
              const parsed = JSON.parse(payloadText) as { error?: string; error_type?: StreamErrorType }
              const message = parsed.error || 'Stream error'
              const errorType = parsed.error_type || 'stream_error'
              handlers.onError?.({ message, error_type: errorType })
              return { ok: false, error_type: errorType, message, finalReceived }
            } catch {
              const message = 'Stream error'
              handlers.onError?.({ message, error_type: 'stream_error' })
              return { ok: false, error_type: 'stream_error', message, finalReceived }
            } finally {
              currentEvent = ''
              currentData = []
            }
          } else {
            const outcome = dispatch()
            if (outcome && !outcome.ok) {
              return { ok: false, error_type: outcome.error_type, message: outcome.message, finalReceived }
            }
          }
        }
      }
    }
  } catch (err) {
    const aborted = err instanceof DOMException && err.name === 'AbortError'
    const error_type: StreamErrorType = aborted ? 'aborted' : 'network_error'
    const message =
      aborted && controller.signal.reason === 'watchdog_timeout'
        ? 'Stream stalled due to inactivity.'
        : aborted
        ? 'Request canceled.'
        : 'Network error while reading stream.'
    handlers.onError?.({ message, error_type })
    return { ok: false, error_type, message, finalReceived }
  } finally {
    finalizeWatchdog()
  }
}

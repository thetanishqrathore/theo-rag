import { create } from 'zustand'
import { nanoid } from 'nanoid'
import {
  streamQuery,
  fetchCollections,
  fetchHealth,
  fetchModels,
  type StatusEvent,
  type FinalEvent,
  type StreamErrorType,
  type RetrievalMode,
} from '../lib/api'
import { loadSessions, saveSessions, loadSettings, saveSettings } from '../lib/storage'
import type { Session, Message, StreamingState, ModelStatus } from './types'

type AssistantMessage = Extract<Message, { role: 'assistant' }>

type BlockState = {
  committed: string[]
  buffer: string
  inCode: boolean
}

const HEADING_REGEX = /^\s{0,3}#{1,6}\s+/
const LIST_REGEX = /^\s*(?:[-*+]|\d+\.)\s+/
const MAX_SESSIONS = 100
const MAX_MESSAGES = 400
const STREAM_HARDENING = import.meta.env.VITE_STREAM_HARDENING !== '0'
const STREAM_WATCHDOG_MS = Number(import.meta.env.VITE_STREAM_WATCHDOG_MS || 120000)
const DEFAULT_RETRIEVAL_MODE: RetrievalMode = import.meta.env.VITE_RETRIEVAL_MODE === 'deep' ? 'deep' : 'fast'
const RAW_DEFAULT_TOP_K = Number(import.meta.env.VITE_TOP_K || 7)
const DEFAULT_TOP_K = Number.isFinite(RAW_DEFAULT_TOP_K)
  ? Math.max(1, Math.min(20, Math.floor(RAW_DEFAULT_TOP_K)))
  : 5

function extractStableBlocks(state: BlockState, incoming: string, forceFlush = false) {
  const text = state.buffer + incoming
  const committed = state.committed ? [...state.committed] : []
  let lastCommit = 0
  let lineStart = 0
  let inCode = state.inCode
  const commit = (index: number) => {
    if (index <= lastCommit) return
    committed.push(text.slice(lastCommit, index))
    lastCommit = index
  }
  for (let i = 0; i <= text.length; i += 1) {
    if (i === text.length || text[i] === '\n') {
      const line = text.slice(lineStart, i)
      const trimmed = line.trim()
      if (!inCode && trimmed.startsWith('```')) {
        if (lineStart > lastCommit) commit(lineStart)
        inCode = true
      } else if (inCode && trimmed.startsWith('```')) {
        commit(i + 1)
        inCode = false
      } else if (!inCode && trimmed === '' && line.length > 0) {
        commit(i + 1)
      } else if (!inCode && HEADING_REGEX.test(line)) {
        commit(i + 1)
      } else if (!inCode && LIST_REGEX.test(line)) {
        commit(i + 1)
      }
      lineStart = i + 1
    }
  }
  let buffer = text.slice(lastCommit)
  if (forceFlush && buffer) {
    committed.push(buffer)
    buffer = ''
  }
  return { committed, buffer, inCode }
}

function mergeStreamingText(message: AssistantMessage, incoming: string, forceFlush = false): AssistantMessage {
  const { committed, buffer, inCode } = extractStableBlocks(
    {
      committed: message.committed_blocks ?? [],
      buffer: message.stream_buffer ?? '',
      inCode: message.stream_in_code ?? false,
    },
    incoming,
    forceFlush
  )
  return {
    ...message,
    committed_blocks: committed,
    stream_buffer: buffer,
    stream_in_code: inCode,
    content: committed.join('') + buffer,
  }
}

const sortSessions = (sessions: Session[]) => {
  return [...sessions].sort((a, b) => {
    const aPinned = a.pinned ? 1 : 0
    const bPinned = b.pinned ? 1 : 0
    if (aPinned !== bPinned) return bPinned - aPinned
    return b.updatedAt - a.updatedAt
  })
}

const pruneSessions = (sessions: Session[]) => {
  let pruned = sortSessions(sessions)
  if (pruned.length > MAX_SESSIONS) pruned = pruned.slice(0, MAX_SESSIONS)
  pruned = pruned.map((s) => ({ ...s, messages: s.messages.slice(-MAX_MESSAGES) }))
  return pruned
}

const emptyStreamingState = (
  lifecycle: StreamingState['lifecycle'] = 'idle',
  error?: { error_type?: StreamErrorType; message?: string }
): StreamingState => ({
  active: false,
  lifecycle,
  assistant_id: null,
  buffer: '',
  citations: [],
  status: undefined,
  retrieval_ms: undefined,
  error_type: error?.error_type,
  error_message: error?.message,
  abort_controller: null,
})

const messageForErrorType = (errorType?: StreamErrorType, raw?: string): string => {
  switch (errorType) {
    case 'ollama_unavailable':
      return 'AI model is warming up. Please retry.'
    case 'busy':
      return 'Server is currently at capacity. Please retry in a few seconds.'
    case 'model_not_found':
      return raw || 'Selected model is not available on the server.'
    case 'resource_exhausted':
      return 'Server resources are exhausted. Please retry shortly.'
    case 'service_unavailable':
      return 'A backend service is unavailable right now. Please retry shortly.'
    case 'timeout':
      return 'Request took too long. Try a simpler question.'
    case 'validation_error':
      return raw || 'Invalid request. Please adjust your question.'
    case 'network_error':
      return 'Network error while streaming. Please retry.'
    case 'stream_incomplete':
      return 'The response stream ended early. Partial output was preserved.'
    case 'aborted':
      return raw || 'Request canceled.'
    case 'parse_error':
      return 'Stream payload could not be parsed. Please retry.'
    default:
      return raw || 'Something went wrong. Please retry.'
  }
}

type ChatState = {
  sessions: Session[]
  currentSessionId: string | null
  streaming: StreamingState
  collections: string[]
  models: string[]
  selectedModel: string | null
  modelStatus: ModelStatus
  selectedCorpus: string
  retrievalMode: RetrievalMode
  topK: number
  health: 'unknown' | 'ok' | 'degraded' | 'error'
  healthDetails: { status: string; services?: Record<string, string> } | null
  init: () => Promise<void>
  createSession: () => void
  setActiveSession: (id: string) => void
  setSelectedModel: (model: string) => void
  setSelectedCorpus: (corpus: string) => void
  setRetrievalMode: (mode: RetrievalMode) => void
  setTopK: (topK: number) => void
  deleteSession: (id: string) => void
  renameSession: (id: string, title: string) => void
  togglePinSession: (id: string) => void
  sendMessage: (query: string, corpus: string, model?: string) => Promise<void>
  cancelStream: (reason?: string) => void
  retryLastFailed: () => Promise<void>
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  streaming: emptyStreamingState(),
  collections: [],
  models: [],
  selectedModel: null,
  modelStatus: 'loading',
  selectedCorpus: 'clough',
  retrievalMode: DEFAULT_RETRIEVAL_MODE,
  topK: DEFAULT_TOP_K,
  health: 'unknown',
  healthDetails: null,

  init: async () => {
    const stored = await loadSessions()
    const settings = await loadSettings()
    let sessions = sortSessions(stored)
    if (sessions.length === 0) {
      const session: Session = { id: nanoid(), title: 'New Session', updatedAt: Date.now(), messages: [] }
      sessions = [session]
    }
    const active = sessions[0]?.id || null
    set({
      sessions,
      currentSessionId: active,
      selectedCorpus: settings.corpus || 'clough',
      selectedModel: settings.model || null,
      retrievalMode: settings.retrievalMode === 'deep' ? 'deep' : DEFAULT_RETRIEVAL_MODE,
      topK: Math.max(1, Math.min(20, Math.floor(settings.topK || DEFAULT_TOP_K))),
    })
    saveSessions(sessions)

    try {
      const health = await fetchHealth()
      const services = health?.services || {}
      const values = Object.values(services)
      let nextHealth: 'ok' | 'degraded' | 'error' = 'error'
      if (health?.status === 'healthy') nextHealth = 'ok'
      else if (values.some((v) => v === 'ok')) nextHealth = 'degraded'
      set({ health: nextHealth, healthDetails: health })
    } catch {
      set({ health: 'error', healthDetails: null })
    }

    try {
      const collections = await fetchCollections()
      const names = collections?.collections?.map((c: { name?: string }) => c.name).filter(Boolean) || []
      set({ collections: names })
    } catch {
      set({ collections: [] })
    }

    set({ modelStatus: 'loading' })
    try {
      const modelData = await fetchModels()
      const models = modelData?.models || []
      const defaultModel = modelData?.default || null
      set((state) => ({
        models,
        selectedModel: state.selectedModel || defaultModel || (models[0] || null),
        modelStatus: models.length > 0 ? 'ready' : 'error',
      }))
    } catch {
      set({ models: [], modelStatus: 'error' })
    }
  },

  createSession: () => {
    const session: Session = { id: nanoid(), title: 'New Session', updatedAt: Date.now(), messages: [] }
    const sessions = pruneSessions([session, ...get().sessions])
    set({ sessions, currentSessionId: session.id })
    saveSessions(sessions)
  },

  setActiveSession: (id) => set({ currentSessionId: id }),

  deleteSession: (id) => {
    set((state) => {
      const remaining = state.sessions.filter((s) => s.id !== id)
      if (remaining.length === 0) {
        const session: Session = { id: nanoid(), title: 'New Session', updatedAt: Date.now(), messages: [] }
        saveSessions([session])
        return { sessions: [session], currentSessionId: session.id }
      }
      const pruned = pruneSessions(remaining)
      saveSessions(pruned)
      const nextActive = state.currentSessionId === id ? pruned[0]?.id || null : state.currentSessionId
      return { sessions: pruned, currentSessionId: nextActive }
    })
  },

  renameSession: (id, title) => {
    if (!title.trim()) return
    set((state) => {
      const next = state.sessions.map((session) => (session.id === id ? { ...session, title: title.trim() } : session))
      saveSessions(next)
      return { sessions: next }
    })
  },

  togglePinSession: (id) => {
    set((state) => {
      const next = state.sessions.map((session) => (session.id === id ? { ...session, pinned: !session.pinned } : session))
      const sorted = sortSessions(next)
      saveSessions(sorted)
      return { sessions: sorted }
    })
  },

  setSelectedModel: (model) => {
    set({ selectedModel: model })
    saveSettings({
      model,
      corpus: get().selectedCorpus,
      retrievalMode: get().retrievalMode,
      topK: get().topK,
    })
  },

  setSelectedCorpus: (corpus) => {
    set({ selectedCorpus: corpus })
    saveSettings({
      model: get().selectedModel || undefined,
      corpus,
      retrievalMode: get().retrievalMode,
      topK: get().topK,
    })
  },

  setRetrievalMode: (mode) => {
    set({ retrievalMode: mode })
    saveSettings({
      model: get().selectedModel || undefined,
      corpus: get().selectedCorpus,
      retrievalMode: mode,
      topK: get().topK,
    })
  },

  setTopK: (topK) => {
    const normalized = Math.max(1, Math.min(20, Math.floor(topK)))
    set({ topK: normalized })
    saveSettings({
      model: get().selectedModel || undefined,
      corpus: get().selectedCorpus,
      retrievalMode: get().retrievalMode,
      topK: normalized,
    })
  },

  cancelStream: (reason = 'Request canceled by user.') => {
    const state = get()
    if (!state.streaming.active) return

    state.streaming.abort_controller?.abort('user_abort')

    set((prev) => {
      const sessionIdx = prev.sessions.findIndex((s) => s.id === prev.currentSessionId)
      if (sessionIdx < 0 || !prev.streaming.assistant_id) {
        return { streaming: emptyStreamingState('aborted', { error_type: 'aborted', message: reason }) }
      }

      const session = prev.sessions[sessionIdx]
      const messageIdx = session.messages.findIndex((m) => m.id === prev.streaming.assistant_id)
      if (messageIdx < 0) {
        return { streaming: emptyStreamingState('aborted', { error_type: 'aborted', message: reason }) }
      }

      const current = session.messages[messageIdx]
      if (current.role !== 'assistant') {
        return { streaming: emptyStreamingState('aborted', { error_type: 'aborted', message: reason }) }
      }

      const updated = mergeStreamingText(current, '', true)
      const nextMessage: AssistantMessage = {
        ...updated,
        isStreaming: false,
        phase: undefined,
        status_message: undefined,
        stream_error_type: 'aborted',
        stream_error_message: reason,
        committed_blocks: undefined,
        stream_buffer: undefined,
        stream_in_code: undefined,
      }

      const nextSessions = [...prev.sessions]
      const nextMessages = [...session.messages]
      nextMessages[messageIdx] = nextMessage
      nextSessions[sessionIdx] = { ...session, messages: nextMessages, updatedAt: Date.now() }
      saveSessions(nextSessions)

      return {
        sessions: nextSessions,
        streaming: emptyStreamingState('aborted', { error_type: 'aborted', message: reason }),
      }
    })
  },

  retryLastFailed: async () => {
    const state = get()
    const current = state.sessions.find((s) => s.id === state.currentSessionId)
    if (!current) return

    const lastError = [...current.messages].reverse().find((m) => {
      if (m.role === 'error') return Boolean(m.retry_query)
      if (m.role === 'assistant') return Boolean(m.stream_error_type)
      return false
    })

    if (!lastError) return

    if (lastError.role === 'error' && lastError.retry_query) {
      await get().sendMessage(
        lastError.retry_query,
        lastError.retry_corpus || current.corpus || state.selectedCorpus,
        (lastError.retry_model as string | undefined) || current.model || state.selectedModel || undefined
      )
      return
    }

    const idx = current.messages.findIndex((m) => m.id === lastError.id)
    if (idx <= 0) return
    const previousUser = [...current.messages.slice(0, idx)].reverse().find((m) => m.role === 'user')
    if (!previousUser || previousUser.role !== 'user') return

    await get().sendMessage(previousUser.content, current.corpus || state.selectedCorpus, current.model || state.selectedModel || undefined)
  },

  sendMessage: async (query, corpus, model) => {
    const hadActiveStream = get().streaming.active
    if (hadActiveStream) {
      get().cancelStream('Previous response canceled to start a new request.')
      // Give the backend SSE handler a brief moment to release its generation slot.
      await new Promise((resolve) => setTimeout(resolve, 250))
    }

    const { sessions, currentSessionId } = get()
    if (!currentSessionId) return

    const streamSessionId = currentSessionId
    const sessionIndex = sessions.findIndex((s) => s.id === currentSessionId)
    if (sessionIndex < 0) return

    const userMessage: Message = {
      id: nanoid(),
      role: 'user',
      content: query,
      ts: Date.now(),
    }

    const assistantId = nanoid()
    const assistantMessage: AssistantMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      ts: Date.now(),
      corpus,
      model: model || null,
      answer_mode: null,
      grounded: false,
      refusal: false,
      evidence: [],
      used_evidence_ids: [],
      citation_fallback_reason: null,
      citation_strategy_version: null,
      num_citations_emitted: null,
      num_citations_matched: null,
      follow_ups: [],
      metrics: {},
      isStreaming: true,
      phase: 'warmup',
      status_message: 'Preparing request...',
      committed_blocks: [],
      stream_buffer: '',
      stream_in_code: false,
    }

    const nextSessions = [...sessions]
    const session = { ...nextSessions[sessionIndex] }
    const hadMessages = session.messages.length > 0
    session.messages = [...session.messages, userMessage, assistantMessage]
    session.updatedAt = Date.now()
    session.corpus = corpus
    if (model) session.model = model
    if (!hadMessages) session.title = query.slice(0, 60)
    nextSessions[sessionIndex] = session

    const initialSessions = pruneSessions(nextSessions)
    const controller = new AbortController()

    set({
      sessions: initialSessions,
      streaming: {
        active: true,
        lifecycle: 'starting',
        assistant_id: assistantId,
        buffer: '',
        citations: [],
        status: { phase: 'warmup', message: 'Preparing request...' },
        error_type: undefined,
        error_message: undefined,
        abort_controller: controller,
      },
    })
    saveSessions(initialSessions)

    const updateAssistantMessage = (
      patch: Partial<AssistantMessage> | ((message: AssistantMessage) => AssistantMessage)
    ) => {
      set((state) => {
        const sessionIdx = state.sessions.findIndex((s) => s.id === streamSessionId)
        if (sessionIdx < 0) return {}
        const sessionRef = state.sessions[sessionIdx]
        const messageIdx = sessionRef.messages.findIndex((m) => m.id === assistantId)
        if (messageIdx < 0) return {}
        const current = sessionRef.messages[messageIdx]
        if (current.role !== 'assistant') return {}
        const nextMessage =
          typeof patch === 'function'
            ? (patch as (m: AssistantMessage) => AssistantMessage)(current as AssistantMessage)
            : { ...(current as AssistantMessage), ...patch }
        const nextSessionsState = [...state.sessions]
        const nextMessages = [...sessionRef.messages]
        nextMessages[messageIdx] = nextMessage
        nextSessionsState[sessionIdx] = { ...sessionRef, messages: nextMessages }
        return { sessions: nextSessionsState }
      })
    }

    const finalizeStream = (
      lifecycle: StreamingState['lifecycle'],
      error?: { error_type?: StreamErrorType; message?: string }
    ) => {
      set((state) => ({
        streaming: {
          ...emptyStreamingState(lifecycle, error),
          lifecycle,
        },
        sessions: state.sessions,
      }))
      const finalSessions = pruneSessions(get().sessions)
      set({ sessions: finalSessions })
      saveSessions(finalSessions)
    }

    const failAssistantInline = (errorType: StreamErrorType, message: string) => {
      updateAssistantMessage((msg) => {
        const flushed = mergeStreamingText(msg, '', true)
        return {
          ...flushed,
          isStreaming: false,
          phase: undefined,
          status_message: undefined,
          stream_error_type: errorType,
          stream_error_message: message,
          committed_blocks: undefined,
          stream_buffer: undefined,
          stream_in_code: undefined,
        }
      })
    }

    const history = session.messages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .filter((m) => !(m.role === 'assistant' && m.id === assistantId))
      .slice(-8)
      .map((m) => ({ role: m.role, content: m.content }))

    let finalizedByFinalEvent = false
    let finalLifecycle: StreamingState['lifecycle'] = 'done'
    let finalError: { error_type?: StreamErrorType; message?: string } | undefined

    const result = await streamQuery(
      {
        query,
        corpus,
        model,
        history,
        top_k: get().topK,
        retrieval_mode: get().retrievalMode,
      },
      {
        onStatus: (event: StatusEvent) => {
          set((state) => ({
            streaming: {
              ...state.streaming,
              lifecycle: 'streaming',
              status: { phase: event.phase, message: event.message },
            },
          }))
          updateAssistantMessage({ phase: event.phase, status_message: event.message })
        },
        onCitations: (event) => {
          set((state) => ({
            streaming: {
              ...state.streaming,
              lifecycle: 'streaming',
              citations: event.items,
              retrieval_ms: event.retrieval_time_ms,
            },
          }))
          updateAssistantMessage((msg) => ({
            ...msg,
            evidence: event.items,
            metrics: {
              ...(msg.metrics || {}),
              retrieval_ms: event.retrieval_time_ms ?? msg.metrics?.retrieval_ms,
            },
          }))
        },
        onToken: (event) => {
          set((state) => ({
            streaming: {
              ...state.streaming,
              lifecycle: 'streaming',
              buffer: state.streaming.buffer + event.content,
            },
          }))
          updateAssistantMessage((msg) => mergeStreamingText(msg, event.content))
        },
        onFinal: (event: FinalEvent) => {
          finalizedByFinalEvent = true
          set((state) => ({
            streaming: {
              ...state.streaming,
              lifecycle: 'finalizing',
              status: { phase: 'finalizing', message: 'Finalizing response...' },
            },
          }))

          updateAssistantMessage((msg) => {
            const flushed = mergeStreamingText(msg, '', true)
            const usedEvidenceIds = event.used_evidence_ids || []
            const usedEvidenceSet = new Set(usedEvidenceIds)
            const candidateEvidence = flushed.evidence.length ? flushed.evidence : get().streaming.citations
            const finalAnswerText = (event.answer_text_final || '').trim()
            const resolvedContent = finalAnswerText.length > 0 ? finalAnswerText : flushed.content
            const filteredEvidenceByUsedIds = candidateEvidence.filter((item) =>
              usedEvidenceIds.length > 0 ? usedEvidenceSet.has(item.evidence_id) : true
            )
            const groundedEvidence =
              usedEvidenceSet.size > 0
                ? filteredEvidenceByUsedIds
                : event.answer_mode === 'narrow'
                  ? candidateEvidence
                  : []
            return {
              ...flushed,
              content: resolvedContent,
              answer_mode: event.answer_mode,
              grounded: event.grounded,
              refusal: event.refusal,
              citation_fallback_reason: event.citation_fallback_reason ?? null,
              citation_strategy_version: event.citation_strategy_version ?? null,
              num_citations_emitted: event.num_citations_emitted ?? null,
              num_citations_matched: event.num_citations_matched ?? null,
              evidence: groundedEvidence,
              used_evidence_ids: usedEvidenceIds,
              follow_ups: event.follow_ups || [],
              metrics: {
                ...(flushed.metrics || {}),
                retrieval_ms: flushed.metrics?.retrieval_ms ?? get().streaming.retrieval_ms,
                generation_ms: event.generation_time_ms,
              },
              isStreaming: false,
              phase: undefined,
              status_message: undefined,
              stream_error_type: undefined,
              stream_error_message: undefined,
              committed_blocks: undefined,
              stream_buffer: undefined,
              stream_in_code: undefined,
            }
          })
        },
        onError: (error) => {
          const errorType = (error.error_type || 'stream_error') as StreamErrorType
          const messageText = messageForErrorType(errorType, error.message)
          finalLifecycle = errorType === 'aborted' ? 'aborted' : 'failed'
          finalError = { error_type: errorType, message: messageText }
          failAssistantInline(errorType, messageText)
        },
      },
      {
        signal: STREAM_HARDENING ? controller.signal : undefined,
        watchdogMs: STREAM_HARDENING ? STREAM_WATCHDOG_MS : undefined,
      }
    )

    if (!result.ok) {
      const errorType = result.error_type
      const messageText = messageForErrorType(errorType, result.message)
      if (!finalError) {
        finalError = { error_type: errorType, message: messageText }
        finalLifecycle = errorType === 'aborted' ? 'aborted' : 'failed'
        failAssistantInline(errorType, messageText)
      }
      finalizeStream(finalLifecycle, finalError)
      return
    }

    if (!finalizedByFinalEvent && !result.finalReceived) {
      const messageText = messageForErrorType('stream_incomplete')
      failAssistantInline('stream_incomplete', messageText)
      finalizeStream('failed', { error_type: 'stream_incomplete', message: messageText })
      return
    }

    finalizeStream('done')
  },
}))

import type { EvidencePreview, StatusPhase, StreamErrorType } from '../lib/api'

export type AnswerMode = 'confident' | 'narrow' | 'refuse'

export type Message =
  | {
      id: string
      role: 'user'
      content: string
      ts: number
    }
  | {
      id: string
      role: 'error'
      content: string
      ts: number
      error_type?: string
      retry_query?: string
      retry_corpus?: string
      retry_model?: string | null
    }
  | {
      id: string
      role: 'assistant'
      content: string
      ts: number
      corpus?: string
      model?: string | null
      answer_mode: AnswerMode | null
      grounded: boolean
      refusal: boolean
      evidence: EvidencePreview[]
      used_evidence_ids: string[]
      citation_fallback_reason?: string | null
      citation_strategy_version?: string | null
      num_citations_emitted?: number | null
      num_citations_matched?: number | null
      follow_ups: string[]
      metrics?: { retrieval_ms?: number; generation_ms?: number }
      isStreaming?: boolean
      phase?: StatusPhase
      status_message?: string
      stream_error_type?: StreamErrorType
      stream_error_message?: string
      committed_blocks?: string[]
      stream_buffer?: string
      stream_in_code?: boolean
    }

export type Session = {
  id: string
  title: string
  updatedAt: number
  pinned?: boolean
  corpus?: string
  model?: string
  messages: Message[]
}

export type StreamingState = {
  active: boolean
  lifecycle: 'idle' | 'starting' | 'streaming' | 'finalizing' | 'done' | 'failed' | 'aborted'
  assistant_id: string | null
  buffer: string
  status?: { phase?: StatusPhase; message: string }
  citations: EvidencePreview[]
  retrieval_ms?: number
  error_type?: StreamErrorType
  error_message?: string
  abort_controller?: AbortController | null
}

export type ModelStatus = 'loading' | 'ready' | 'error'

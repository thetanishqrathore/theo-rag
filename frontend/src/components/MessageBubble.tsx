import { useMemo, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  User,
  Copy,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  MessageCircleQuestion,
  RefreshCw,
  ThumbsUp,
  ThumbsDown,
  AlertOctagon,
} from 'lucide-react'
import type { Message } from '../store/types'
import type { EvidencePreview } from '../lib/api'
import { CitationChip } from './CitationChip'
import { cn } from '../lib/utils'
import { SourceCarousel } from './SourceCarousel'

const STAGE_LABELS: Record<string, string> = {
  warmup: 'Loading model',
  retrieving: 'Finding evidence',
  reranking: 'Reading results',
  building: 'Organizing context',
  generating: 'Generating response',
  finalizing: 'Polishing response',
}

const CITATION_FALLBACK_REASON_LABELS: Record<string, string> = {
  narrow_answer_no_primary_matches_found:
    'No direct citation anchors were found in the final answer, so top supporting evidence is shown.',
}

const formatFallbackReason = (reason?: string | null): string | null => {
  if (!reason) return null
  return CITATION_FALLBACK_REASON_LABELS[reason] || reason.replace(/_/g, ' ')
}

const normalizeCitationTokens = (content: string) => {
  let normalized = content
  normalized = normalized.replace(/\[\s*chunk\s*[: ]\s*([0-9a-fA-F-]{1,})\s*\]/gi, '[chunk:$1]')
  normalized = normalized.replace(/\bchunk[_\s-]?id\b\s*:\s*([0-9a-fA-F-]{1,})/gi, '[chunk:$1]')
  // Bare `chunk:<id>` only; canonical `[chunk:<id>]` must not be wrapped again.
  normalized = normalized.replace(/(?<!\[)\bchunk\s*:\s*([0-9a-fA-F-]{1,})\b/gi, '[chunk:$1]')
  normalized = normalized.replace(/(?<!\[)\bchunk\s+([0-9a-fA-F-]{1,})\b/gi, '[chunk:$1]')
  normalized = normalized.replace(/\[\s*source\s+(\d{1,3})\s*\]/gi, '[SOURCE $1]')
  normalized = normalized.replace(/\(\s*source\s+(\d{1,3})\s*\)/gi, '[SOURCE $1]')
  normalized = normalized.replace(/\[\s*·\s*\]/g, '')
  return normalized
}

const transformCitationsToLinks = (
  content: string,
  indexMap: Map<string, number>,
  evidenceByOrdinal: Map<number, string>
) => {
  const normalized = normalizeCitationTokens(content)
  let rendered = normalized.replace(/\[chunk:([0-9a-fA-F-]{1,})\]/gi, (_match, rawRef) => {
    const ref = String(rawRef).trim()
    if (/^\d+$/.test(ref)) {
      const ordinal = Number(ref)
      const evidenceId = evidenceByOrdinal.get(ordinal)
      return evidenceId ? `[${ordinal}](#cite-${evidenceId})` : `[?](#unresolved-${ordinal})`
    }
    const lookupId = ref.toLowerCase()
    const index = indexMap.get(lookupId) ?? 0
    // A reference with no matching source stays visible as an unresolved marker.
    return index ? `[${index}](#cite-${lookupId})` : `[?](#unresolved-${lookupId})`
  })
  rendered = rendered.replace(/\[SOURCE\s+(\d{1,3})\]/gi, (_match, rawNum) => {
    const ordinal = Number(rawNum)
    const evidenceId = evidenceByOrdinal.get(ordinal)
    return evidenceId ? `[${ordinal}](#cite-${evidenceId})` : `[?](#unresolved-${ordinal})`
  })
  return rendered
    .replace(/\[(?:chunk|SOURCE)[^\]]*\]/gi, '')
    // Collapse horizontal whitespace only: newlines (and "  \n" hard breaks) carry
    // the markdown structure.
    .replace(/[^\S\n]{2,}(?!\n)/g, ' ')
    .replace(/[^\S\n]+([.,;:!?])/g, '$1')
    .trim()
}

const ChristSymbol = () => (
  <svg className="h-8 w-8 text-emerald-300" viewBox="0 0 48 48" role="img" aria-label="Christ symbol">
    <line x1="24" y1="7" x2="24" y2="41" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    <line x1="12" y1="20" x2="36" y2="20" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
  </svg>
)

interface MessageBubbleProps {
  message: Message
  onFollowUp?: (question: string) => void
  onRetry?: (query: string, corpus?: string, model?: string | null) => void
  onRegenerate?: (messageId: string) => void
  onFeedback?: (messageId: string, value: 'up' | 'down') => void
  onInspectEvidence?: (items: EvidencePreview[], index: number) => void
  onToast?: (message: string, tone?: 'success' | 'error') => void
}

export function MessageBubble({
  message,
  onFollowUp,
  onRetry,
  onRegenerate,
  onFeedback,
  onInspectEvidence,
  onToast,
}: MessageBubbleProps) {
  const ACTION_ICON_SIZE = 14
  const [copied, setCopied] = useState(false)
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null)
  const [peekEvidenceId, setPeekEvidenceId] = useState<string | null>(null)
  const [liveMessage, setLiveMessage] = useState('')
  const prefersReducedMotion = useReducedMotion()

  const isAssistant = message.role === 'assistant'
  const assistant = isAssistant ? (message as Extract<Message, { role: 'assistant' }>) : null

  const isStreaming = Boolean(assistant?.isStreaming)
  const statusLabel = assistant?.status_message || (assistant?.phase ? STAGE_LABELS[assistant.phase] : undefined)
  const evidence = useMemo(() => assistant?.evidence || [], [assistant?.evidence])
  const followUps = assistant?.follow_ups || []
  const fallbackReason = formatFallbackReason(assistant?.citation_fallback_reason)

  const indexMap = useMemo(() => {
    const map = new Map<string, number>()
    evidence.forEach((item, idx) => map.set(String(item.evidence_id).toLowerCase(), idx + 1))
    return map
  }, [evidence])

  const evidenceMap = useMemo(
    () =>
      new Map(
        evidence.map((item) => [
          String(item.evidence_id).toLowerCase(),
          {
            evidenceId: item.evidence_id,
            preview: item.preview,
            contextOnly: item.context_only,
            tooltipLabel: item.lesson ? `Lesson ${item.lesson}` : 'Source',
          },
        ])
      ),
    [evidence]
  )
  const evidenceByOrdinal = useMemo(() => {
    const map = new Map<number, string>()
    evidence.forEach((item, idx) => map.set(idx + 1, String(item.evidence_id).toLowerCase()))
    return map
  }, [evidence])

  const committedText = isStreaming ? (assistant?.committed_blocks || []).join('') : assistant?.content || ''
  const streamingBuffer = isStreaming ? assistant?.stream_buffer || '' : ''
  const hasStreamedToken = isStreaming && (committedText.length > 0 || streamingBuffer.length > 0)
  const isGenerationPhase = assistant?.phase === 'generating' || assistant?.phase === 'finalizing'
  const showStreamingCaret = Boolean(isStreaming && hasStreamedToken && isGenerationPhase)
  const renderedCommitted = committedText ? transformCitationsToLinks(committedText, indexMap, evidenceByOrdinal) : ''
  const renderedBuffer = streamingBuffer ? transformCitationsToLinks(streamingBuffer, indexMap, evidenceByOrdinal) : ''
  const announce = (message: string) => {
    setLiveMessage(message)
    window.setTimeout(() => setLiveMessage(''), 1600)
  }
  const resultPillBaseClass = 'inline-flex h-6 items-center gap-2 rounded-full border px-3 text-[10px] font-semibold leading-none'
  const resultModePillClass = `${resultPillBaseClass} uppercase tracking-widest`
  const resultMetaPillClass = `${resultPillBaseClass} normal-case tracking-normal`

  const markdownComponents: Components = {
    a: ({ href, children }) => {
      if (href && href.startsWith('#cite-')) {
        const id = href.replace('#cite-', '').toLowerCase()
        const index = indexMap.get(id) || 0
        const info = evidenceMap.get(id)
        return (
          <CitationChip
            index={index}
            onClick={() => setPeekEvidenceId((prev) => (prev === id ? null : id))}
            preview={info?.preview}
            contextOnly={info?.contextOnly}
            label={info?.tooltipLabel}
          />
        )
      }
      if (href && href.startsWith('#unresolved-')) {
        return (
          <span
            className="citation-chip unresolved"
            title="This citation could not be matched to a returned source"
            aria-label="Unresolved citation"
          >
            ?
          </span>
        )
      }
      if (!href) return <span>{children}</span>
      return (
        <a href={href} target="_blank" rel="noreferrer">
          {children}
        </a>
      )
    },
  }

  const activePeek = useMemo(() => {
    if (!peekEvidenceId) return null
    return evidence.find((ev) => String(ev.evidence_id).toLowerCase() === peekEvidenceId) || null
  }, [peekEvidenceId, evidence])

  const handleCopy = async () => {
    if (!assistant?.content) return
    try {
      await navigator.clipboard.writeText(assistant.content)
      setCopied(true)
      announce('Answer copied to clipboard.')
      onToast?.('Answer copied to clipboard.')
      setTimeout(() => setCopied(false), 1500)
    } catch (err) {
      console.error('Failed to copy message', err)
      announce('Copy failed.')
      onToast?.('Copy failed.', 'error')
    }
  }

  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="message-user-bubble bubble-user-bg max-w-[80%] rounded-3xl rounded-tr-md px-5 py-3 text-base text-gray-100 shadow-lg">
          {message.content}
        </div>
        <div className="message-user-avatar ml-3 mt-1 flex h-12 w-12 items-center justify-center rounded-full bg-white/10 text-white">
          <User size={18} className="avatar-icon" />
        </div>
      </div>
    )
  }

  if (message.role === 'error') {
    return (
      <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-50">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-200">Issue</div>
        <p className="text-base text-amber-50">{message.content}</p>
        {message.retry_query && (
          <button
            className="mt-3 rounded-full border border-white/20 bg-white/10 px-4 py-1.5 text-sm font-semibold text-white hover:bg-white/20"
            onClick={() => onRetry?.(message.retry_query!, message.retry_corpus, message.retry_model)}
          >
            Retry
          </button>
        )}
      </div>
    )
  }

  if (!assistant) return null

  return (
    <div className="assistant-row flex w-full justify-start">
      <div className="assistant-avatar mr-4 mt-1 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-900/30 text-emerald-300 ring-1 ring-emerald-400/20">
        <ChristSymbol />
      </div>
      <div className="assistant-content-wrap w-full max-w-[720px]">
        <div className="assistant-bubble bubble-assistant-bg rounded-3xl border border-white/10 px-6 py-5 shadow-[0_25px_80px_rgba(0,0,0,0.45)]">
          <div className="sr-only" role="status" aria-live="polite">
            {liveMessage}
          </div>
          {assistant.stream_error_type && (
            <div className="mb-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
              <div className="inline-flex items-center gap-2 font-semibold">
                <AlertOctagon size={14} />
                Interrupted
              </div>
              <div className="mt-1 text-amber-200">{assistant.stream_error_message || 'The response was interrupted.'}</div>
            </div>
          )}

          {fallbackReason && !isStreaming && (
            <div className="mb-3 rounded-xl border border-amber-500/20 bg-amber-500/8 px-3 py-2 text-sm text-amber-200">
              <div className="inline-flex items-center gap-2 font-semibold">
                <AlertTriangle size={14} />
                Using fallback evidence
              </div>
              <div className="mt-1 text-amber-100/90">{fallbackReason}</div>
            </div>
          )}

          <AnimatePresence mode="wait">
            {statusLabel && (
              <motion.div
                key={statusLabel}
                initial={prefersReducedMotion ? false : { opacity: 0, y: -2 }}
                animate={{ opacity: 1, y: 0 }}
                exit={prefersReducedMotion ? undefined : { opacity: 0, y: 2 }}
                transition={{ duration: prefersReducedMotion ? 0 : 0.18 }}
                className={cn('mb-3 text-base font-medium text-gray-200')}
                role="status"
                aria-live="polite"
                aria-atomic="true"
              >
                <span className="status-pulse">{statusLabel}</span>
              </motion.div>
            )}
          </AnimatePresence>

          <div className="assistant-prose prose prose-invert max-w-none text-gray-100 prose-p:my-4 prose-headings:text-white prose-headings:font-semibold prose-headings:mt-6 prose-ul:my-4 prose-ol:my-4 prose-li:my-2 prose-li:marker:text-emerald-400 prose-strong:font-semibold">
            <div className="space-y-4">
              {renderedCommitted && (
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {renderedCommitted}
                </ReactMarkdown>
              )}
              {isStreaming && (renderedBuffer || showStreamingCaret) && (
                <div className="streaming-buffer">
                  {renderedBuffer && (
                    <div className="streaming-markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                        {renderedBuffer}
                      </ReactMarkdown>
                    </div>
                  )}
                  {showStreamingCaret && (
                    <span className={cn('typing-caret', assistant?.phase && `typing-caret--${assistant.phase}`)} aria-hidden="true">
                      _
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>

          {activePeek && (
            <div className="mt-4 rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-gray-100">
              <div className="flex items-center justify-between text-xs uppercase tracking-widest text-gray-400">
                <span>{activePeek.label || activePeek.path || 'Source excerpt'}</span>
                <button className="text-gray-500 hover:text-white" onClick={() => setPeekEvidenceId(null)}>
                  Close
                </button>
              </div>
              <p className="mt-2 text-base leading-relaxed text-gray-100">{activePeek.preview}</p>
              <div className="mt-3 flex flex-wrap gap-3 text-xs text-gray-300">
                <button
                  className="rounded-full border border-white/10 px-3 py-1 uppercase tracking-widest hover:bg-white/10"
                  onClick={() => {
                    const index = evidence.findIndex((ev) => ev.evidence_id === activePeek.evidence_id)
                    if (index < 0) return
                    onInspectEvidence?.(evidence, index)
                  }}
                >
                  Open inspector
                </button>
                <button
                  className="rounded-full border border-white/10 px-3 py-1 uppercase tracking-widest hover:bg-white/10"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(activePeek.preview)
                      announce('Excerpt copied to clipboard.')
                      onToast?.('Excerpt copied to clipboard.')
                    } catch {
                      announce('Copy failed.')
                      onToast?.('Copy failed.', 'error')
                    }
                  }}
                >
                  Copy excerpt
                </button>
              </div>
            </div>
          )}

          {!isStreaming && (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
              <div className="flex flex-wrap items-center gap-2">
                {assistant.refusal && (
                  <span className={cn(resultModePillClass, 'border-amber-500/20 bg-amber-500/10 text-amber-300')}>
                    <ShieldAlert size={12} /> Refused
                  </span>
                )}
                {!assistant.refusal && assistant.answer_mode === 'confident' && (
                  <span className={cn(resultModePillClass, 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300')}>
                    <ShieldCheck size={12} /> Confident
                  </span>
                )}
                {!assistant.refusal && assistant.answer_mode === 'narrow' && (
                  <span className={cn(resultModePillClass, 'border-yellow-500/20 bg-yellow-500/10 text-yellow-300')}>
                    <AlertTriangle size={12} /> Narrow
                  </span>
                )}
                {!assistant.refusal && assistant.answer_mode === 'refuse' && (
                  <span className={cn(resultModePillClass, 'border-amber-500/20 bg-amber-500/10 text-amber-300')}>
                    <ShieldAlert size={12} /> Refused
                  </span>
                )}
                {(assistant.corpus || assistant.model) && (
                  <span className={cn(resultMetaPillClass, 'border-cyan-500/20 bg-cyan-500/10 text-cyan-200')}>
                    {assistant.corpus || 'corpus'} · {assistant.model || 'default model'}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5 text-[11px] font-medium normal-case tracking-normal">
                <button
                  onClick={handleCopy}
                  aria-label={copied ? 'Copied' : 'Copy answer'}
                  title={copied ? 'Copied' : 'Copy'}
                  className={cn(
                    'touch-target rounded-full border p-1.5 transition',
                    copied
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                      : 'border-white/10 bg-white/5 text-gray-300 hover:bg-white/10'
                  )}
                >
                  <Copy size={ACTION_ICON_SIZE} />
                </button>
                <button
                  onClick={() => onRegenerate?.(assistant.id)}
                  aria-label="Regenerate response"
                  title="Regenerate"
                  className="touch-target rounded-full border border-white/10 bg-white/5 p-1.5 text-gray-300 transition hover:bg-white/10"
                >
                  <RefreshCw size={ACTION_ICON_SIZE} />
                </button>
                <button
                  onClick={() => {
                    setFeedback((prev) => {
                      const next = prev === 'up' ? null : 'up'
                      if (next) onFeedback?.(assistant.id, next)
                      return next
                    })
                  }}
                  className={cn(
                    'touch-target rounded-full border p-1.5 transition',
                    feedback === 'up'
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                      : 'border-white/10 bg-white/5 text-gray-300 hover:bg-white/10'
                  )}
                  aria-label="Helpful"
                >
                  <ThumbsUp size={ACTION_ICON_SIZE} />
                </button>
                <button
                  onClick={() => {
                    setFeedback((prev) => {
                      const next = prev === 'down' ? null : 'down'
                      if (next) onFeedback?.(assistant.id, next)
                      return next
                    })
                  }}
                  className={cn(
                    'touch-target rounded-full border p-1.5 transition',
                    feedback === 'down'
                      ? 'border-amber-500/40 bg-amber-500/10 text-amber-300'
                      : 'border-white/10 bg-white/5 text-gray-300 hover:bg-white/10'
                  )}
                  aria-label="Not helpful"
                >
                  <ThumbsDown size={ACTION_ICON_SIZE} />
                </button>
              </div>
            </div>
          )}
        </div>

        {!isStreaming && evidence.length > 0 && (
          <SourceCarousel citations={evidence} onSelect={(_preview, index) => onInspectEvidence?.(evidence, index)} />
        )}

        {!isStreaming && !assistant.refusal && followUps.length > 0 && (
          <div className="mt-5 rounded-2xl border border-white/10 bg-white/5 p-4">
            <div className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest text-indigo-300">
              <MessageCircleQuestion size={12} />
              Follow ups
            </div>
            <div className="grid gap-2">
              {followUps.map((q, idx) => (
                <button
                  key={`${q}-${idx}`}
                  onClick={() => onFollowUp?.(q)}
                  className="rounded-xl border border-indigo-500/20 bg-indigo-500/10 px-4 py-2 text-left text-sm text-gray-100 transition hover:border-indigo-400/40 hover:bg-indigo-500/20"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

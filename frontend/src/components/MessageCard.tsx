import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../store/types'
import { CitationChip } from './CitationChip'

const normalizeCitationTokens = (content: string) => {
  let normalized = content
  normalized = normalized.replace(/\[\s*chunk\s*[: ]\s*([0-9a-fA-F-]{1,})\s*\]/gi, '[chunk:$1]')
  normalized = normalized.replace(/\bchunk[_\s-]?id\b\s*:\s*([0-9a-fA-F-]{1,})/gi, '[chunk:$1]')
  normalized = normalized.replace(/\bchunk\s*:\s*([0-9a-fA-F-]{1,})\b/gi, '[chunk:$1]')
  normalized = normalized.replace(/\bchunk\s+([0-9a-fA-F-]{1,})\b/gi, '[chunk:$1]')
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
      return evidenceId ? `[${ordinal}](#cite-${evidenceId})` : ''
    }
    const lookupId = ref.toLowerCase()
    const index = indexMap.get(lookupId) ?? 0
    return index ? `[${index}](#cite-${lookupId})` : ''
  })
  rendered = rendered.replace(/\[SOURCE\s+(\d{1,3})\]/gi, (_match, rawNum) => {
    const ordinal = Number(rawNum)
    const evidenceId = evidenceByOrdinal.get(ordinal)
    return evidenceId ? `[${ordinal}](#cite-${evidenceId})` : ''
  })
  return rendered
    .replace(/\[(?:chunk|SOURCE)[^\]]*\]/gi, '')
    .replace(/\s{2,}/g, ' ')
    .replace(/\s+([.,;:!?])/g, '$1')
    .trim()
}

export function MessageCard({
  message,
  onCitationClick,
  onFollowUp,
  onOpenSources,
  onRetry,
}: {
  message: Message
  onCitationClick?: (evidenceId: string) => void
  onFollowUp?: (question: string) => void
  onOpenSources?: () => void
  onRetry?: (query: string, corpus?: string, model?: string | null) => void
}) {
  if (message.role === 'user') {
    return (
      <div className="message-card">
        <div className="user-bubble">{message.content}</div>
      </div>
    )
  }

  if (message.role === 'error') {
    return (
      <div className="message-card error-card">
        <div className="answer-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="meta">Error</span>
          </div>
        </div>
        <div className="content">{message.content}</div>
        {message.retry_query && (
          <div className="answer-actions">
            <button
              className="ghost-btn sm"
              onClick={() => onRetry?.(message.retry_query!, message.retry_corpus, message.retry_model)}
            >
              Retry
            </button>
          </div>
        )}
      </div>
    )
  }

  const evidence = message.evidence || []
  const indexMap = new Map<string, number>()
  evidence.forEach((item, idx) => indexMap.set(String(item.evidence_id).toLowerCase(), idx + 1))
  const evidenceByOrdinal = new Map<number, string>()
  evidence.forEach((item, idx) => evidenceByOrdinal.set(idx + 1, String(item.evidence_id).toLowerCase()))
  const evidenceMap = new Map(
    evidence.map((item) => [
      String(item.evidence_id).toLowerCase(),
      {
        ...item,
        tooltipLabel: item.lesson ? `Lesson ${item.lesson}` : 'Source',
      },
    ])
  )
  const rendered = transformCitationsToLinks(message.content, indexMap, evidenceByOrdinal)

  return (
    <div className="message-card">
      <div className="assistant-card">
        <div className="answer-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="meta">Answer</span>
            {message.answer_mode && (
              <span className={`mode-badge ${message.answer_mode}`}>
                {message.answer_mode.toUpperCase()}
              </span>
            )}
          </div>
          {evidence.length > 0 && (
            <div className="answer-actions">
              <button className="ghost-btn sm" onClick={onOpenSources}>
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{ marginRight: 4 }}
                >
                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                  <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
                </svg>
                Sources
                <span className="count-badge">{evidence.length}</span>
              </button>
            </div>
          )}
        </div>

        <div className="content">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href }) => {
                if (href && href.startsWith('#cite-')) {
                  const id = href.replace('#cite-', '').toLowerCase()
                  const index = indexMap.get(id) || 0
                  const info = evidenceMap.get(id)
                  return (
                    <CitationChip
                      index={index}
                      onClick={() => onCitationClick?.(id)}
                      preview={info?.preview}
                      contextOnly={info?.context_only}
                      label={info?.tooltipLabel}
                    />
                  )
                }
                return null
              },
            }}
          >
            {rendered}
          </ReactMarkdown>
        </div>
      </div>

      {evidence.length > 0 && (
        <div className="sources-block">
          <div className="sources-strip">
            <div className="label">Sources ({evidence.length})</div>
            <div style={{ flex: 1 }} />
            <button className="ghost-btn sm" onClick={onOpenSources}>
              View all
            </button>
          </div>

          <div className="source-carousel" role="list">
            {evidence.map((item) => {
              const lessonLabel = item.lesson ? `Lesson ${item.lesson}` : 'Source'
              return (
                <button
                  key={item.evidence_id}
                  className="source-card"
                  onClick={() => onCitationClick?.(item.evidence_id)}
                  role="listitem"
                >
                  <div className="label">{lessonLabel}</div>
                  <div className="preview">{item.preview}</div>
                </button>
              )
            })}
          </div>
        </div>
      )}

      {message.follow_ups && message.follow_ups.length > 0 && (
        <div className="followups">
          {message.follow_ups.map((q) => (
            <button key={q} className="followup-btn" onClick={() => onFollowUp?.(q)}>
              <span className="followup-text">{q}</span>
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ flexShrink: 0, marginLeft: 'auto', opacity: 0.6 }}
              >
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

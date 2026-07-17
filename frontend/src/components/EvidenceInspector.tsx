import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { EvidencePreview } from '../lib/api'
import { fetchEvidence, resolveApiUrl } from '../lib/api'
import { activateFocusTrap } from '../lib/a11y'

/** Hook: swipe-down-to-dismiss for mobile bottom sheets */
function useSwipeDismiss(
  ref: React.RefObject<HTMLElement | null>,
  onDismiss: () => void,
  { threshold = 120, enabled = true }: { threshold?: number; enabled?: boolean } = {}
) {
  const translateY = useRef(0)
  const startY = useRef(0)
  const isDragging = useRef(false)

  const handleTouchStart = useCallback((e: TouchEvent) => {
    const el = ref.current
    if (!el || !enabled) return
    // Only capture swipe on the drag handle area (first 40px)
    const touch = e.touches[0]
    const rect = el.getBoundingClientRect()
    if (touch.clientY - rect.top > 48) return
    startY.current = touch.clientY
    isDragging.current = true
    el.style.transition = 'none'
  }, [ref, enabled])

  const handleTouchMove = useCallback((e: TouchEvent) => {
    if (!isDragging.current || !ref.current) return
    const delta = e.touches[0].clientY - startY.current
    translateY.current = Math.max(0, delta)
    ref.current.style.transform = `translateY(${translateY.current}px)`
  }, [ref])

  const handleTouchEnd = useCallback(() => {
    if (!isDragging.current || !ref.current) return
    isDragging.current = false
    ref.current.style.transition = 'transform 0.25s cubic-bezier(0.16, 1, 0.3, 1)'
    if (translateY.current > threshold) {
      ref.current.style.transform = `translateY(100%)`
      setTimeout(onDismiss, 250)
    } else {
      ref.current.style.transform = 'translateY(0)'
    }
    translateY.current = 0
  }, [ref, onDismiss, threshold])

  useEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    el.addEventListener('touchstart', handleTouchStart, { passive: true })
    el.addEventListener('touchmove', handleTouchMove, { passive: true })
    el.addEventListener('touchend', handleTouchEnd)
    return () => {
      el.removeEventListener('touchstart', handleTouchStart)
      el.removeEventListener('touchmove', handleTouchMove)
      el.removeEventListener('touchend', handleTouchEnd)
    }
  }, [ref, enabled, handleTouchStart, handleTouchMove, handleTouchEnd])
}

/** Hook: horizontal swipe to navigate between evidence items */
function useSwipeNavigation(
  ref: React.RefObject<HTMLElement | null>,
  { onSwipeLeft, onSwipeRight, enabled = true }: { onSwipeLeft?: () => void; onSwipeRight?: () => void; enabled?: boolean }
) {
  const startX = useRef(0)
  const startY = useRef(0)
  const tracking = useRef(false)

  const handleTouchStart = useCallback((e: TouchEvent) => {
    if (!enabled) return
    const touch = e.touches[0]
    const el = ref.current
    if (!el) return
    // Only track if touch starts below drag-handle zone (top 48px is for dismiss)
    const rect = el.getBoundingClientRect()
    if (touch.clientY - rect.top <= 48) return
    startX.current = touch.clientX
    startY.current = touch.clientY
    tracking.current = true
  }, [ref, enabled])

  const handleTouchEnd = useCallback((e: TouchEvent) => {
    if (!tracking.current) return
    tracking.current = false
    const touch = e.changedTouches[0]
    const deltaX = touch.clientX - startX.current
    const deltaY = Math.abs(touch.clientY - startY.current)
    // Must be a primarily horizontal swipe of at least 60px
    if (Math.abs(deltaX) < 60 || Math.abs(deltaX) < deltaY * 1.5) return
    if (deltaX < 0) {
      onSwipeLeft?.()
    } else {
      onSwipeRight?.()
    }
  }, [onSwipeLeft, onSwipeRight])

  useEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    el.addEventListener('touchstart', handleTouchStart, { passive: true })
    el.addEventListener('touchend', handleTouchEnd)
    return () => {
      el.removeEventListener('touchstart', handleTouchStart)
      el.removeEventListener('touchend', handleTouchEnd)
    }
  }, [ref, enabled, handleTouchStart, handleTouchEnd])
}

type EvidenceDetail = {
  evidence_id: string
  parent_id: string
  label: string
  path: string
  primary_text: string
  context_before: string[]
  context_after: string[]
  metadata: Record<string, string | number>
}

const evidenceCache = new Map<string, EvidenceDetail>()
const MAX_EVIDENCE_CACHE = 200

interface EvidenceInspectorProps {
  evidence: EvidencePreview
  onClose: () => void
  canPrev?: boolean
  canNext?: boolean
  onPrev?: () => void
  onNext?: () => void
  positionLabel?: string
  onToast?: (message: string, tone?: 'success' | 'error') => void
}

const toCanonicalUrl = (filePath?: string) => {
  if (!filePath) return ''
  const normalized = filePath.replace(/\\/g, '/')
  if (/^https?:\/\//i.test(normalized)) return normalized
  const parts = normalized.split('/').filter(Boolean)
  if (parts.length === 0) return ''
  const fileName = parts.pop()!
  const htmlName = fileName.replace(/\.[^.]+$/, '') + '.html'
  const encoded = [...parts.map((segment) => encodeURIComponent(segment)), encodeURIComponent(htmlName)]
  return resolveApiUrl(`/static/${encoded.join('/')}`)
}

export function EvidenceInspector({
  evidence,
  onClose,
  canPrev = false,
  canNext = false,
  onPrev,
  onNext,
  positionLabel,
  onToast,
}: EvidenceInspectorProps) {
  const panelRef = useRef<HTMLElement>(null)
  const closeBtnRef = useRef<HTMLButtonElement>(null)
  const isTouchDevice = typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches
  useSwipeDismiss(panelRef, onClose, { enabled: isTouchDevice })
  useSwipeNavigation(panelRef, {
    onSwipeLeft: canNext ? onNext : undefined,
    onSwipeRight: canPrev ? onPrev : undefined,
    enabled: isTouchDevice,
  })
  const cachedDetail = useMemo(() => evidenceCache.get(evidence.evidence_id) || null, [evidence.evidence_id])
  const [detail, setDetail] = useState<EvidenceDetail | null>(cachedDetail)
  const [copied, setCopied] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [loading, setLoading] = useState(!cachedDetail)
  const [error, setError] = useState<string | null>(null)
  const [contextExpanded, setContextExpanded] = useState(false)
  const titleId = `inspector-title-${evidence.evidence_id}`

  useEffect(() => {
    if (!panelRef.current) return
    return activateFocusTrap(panelRef.current, {
      onEscape: onClose,
      initialFocus: closeBtnRef.current,
    })
  }, [onClose, evidence.evidence_id])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const panel = panelRef.current
      if (!panel || !panel.contains(document.activeElement)) return
      if (event.key === 'ArrowLeft' && canPrev) {
        event.preventDefault()
        onPrev?.()
      }
      if (event.key === 'ArrowRight' && canNext) {
        event.preventDefault()
        onNext?.()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [canPrev, canNext, onPrev, onNext, evidence.evidence_id])

  useEffect(() => {
    if (cachedDetail) {
      return
    }
    fetchEvidence(evidence.evidence_id)
      .then((payload) => {
        evidenceCache.set(evidence.evidence_id, payload)
        if (evidenceCache.size > MAX_EVIDENCE_CACHE) {
          const oldestKey = evidenceCache.keys().next().value
          if (oldestKey) evidenceCache.delete(oldestKey)
        }
        setDetail(payload)
      })
      .catch(() => {
        setError('Full context unavailable—showing inline excerpt.')
      })
      .finally(() => setLoading(false))
  }, [cachedDetail, evidence.evidence_id])

  const handleCopy = () => {
    const text = detail?.primary_text || evidence.preview
    if (!text) return
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true)
        onToast?.('Excerpt copied to clipboard.')
        setTimeout(() => setCopied(false), 1500)
      })
      .catch(() => {
        setCopied(false)
        onToast?.('Copy failed.', 'error')
      })
  }

  const canonicalUrl = useMemo(() => {
    const direct = detail?.metadata?.canonical_url
    if (typeof direct === 'string' && direct.trim()) {
      if (/^https?:\/\//i.test(direct)) return direct
      return resolveApiUrl(direct)
    }
    return toCanonicalUrl((detail?.metadata?.file_path as string | undefined) || evidence.path)
  }, [detail, evidence])

  const location = useMemo(() => {
    const lesson = detail?.metadata?.lesson ?? evidence.lesson
    const partTitle =
      (detail?.metadata?.part_title as string | undefined) ||
      (detail?.metadata?.part as string | undefined) ||
      undefined
    return [partTitle, lesson ? `Lesson ${lesson}` : null].filter(Boolean).join(' • ')
  }, [detail, evidence])

  const pathBadge = useMemo(() => {
    const fromDetail = detail?.metadata?.node_path || detail?.metadata?.section_path
    if (typeof fromDetail === 'string' && fromDetail.trim()) return fromDetail
    return evidence.node_path || evidence.section_path || null
  }, [detail, evidence])

  const pageBadge = useMemo(() => {
    const start = (detail?.metadata?.page_start as number | undefined) ?? evidence.page_start ?? undefined
    const end = (detail?.metadata?.page_end as number | undefined) ?? evidence.page_end ?? undefined
    if (start == null && end == null) return null
    if (start != null && end != null) {
      return start === end ? `p.${start}` : `pp.${start}-${end}`
    }
    return start != null ? `p.${start}` : `p.${end}`
  }, [detail, evidence])

  const excerpt = detail?.primary_text || evidence.preview || ''
  const before = detail?.context_before || []
  const after = detail?.context_after || []
  const beforeToShow = contextExpanded ? before : before.slice(-1)
  const afterToShow = contextExpanded ? after : after.slice(0, 1)

  return (
    <aside
      ref={panelRef}
      className="inspector"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-label="Evidence inspector"
    >
      <div className="inspector-drag-handle" aria-hidden="true">
        <div className="inspector-drag-pill" />
      </div>
      <div className="inspector-header">
        <div>
          {location && <div className="inspector-path">{location}</div>}
          <div className="inspector-title" id={titleId}>{evidence.label || 'Evidence excerpt'}</div>
          {(pathBadge || pageBadge) && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {pathBadge && (
                <span className="rounded-full bg-cyan-500/15 px-2 py-0.5 text-[10px] uppercase tracking-wide text-cyan-200">
                  Path {pathBadge}
                </span>
              )}
              {pageBadge && (
                <span className="rounded-full bg-indigo-500/15 px-2 py-0.5 text-[10px] uppercase tracking-wide text-indigo-200">
                  {pageBadge}
                </span>
              )}
            </div>
          )}
        </div>
        <div className="inspector-header-controls">
          {positionLabel && <span className="inspector-position">{positionLabel}</span>}
          <button className="ghost-btn sm" onClick={onPrev} disabled={!canPrev} aria-label="Previous evidence">
            Previous
          </button>
          <button className="ghost-btn sm" onClick={onNext} disabled={!canNext} aria-label="Next evidence">
            Next
          </button>
          <button ref={closeBtnRef} className="chip" onClick={onClose} aria-label="Close inspector">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <div className="inspector-card">
        <div className="sr-only" role="status" aria-live="polite">
          {copied ? 'Excerpt copied to clipboard.' : ''}
        </div>
        <div className="inspector-actions">
          <button className="ghost-btn sm" onClick={handleCopy}>
            {copied ? 'Copied' : 'Copy excerpt'}
          </button>
          <button
            className="ghost-btn sm"
            onClick={() => {
              if (canonicalUrl) {
                window.open(canonicalUrl, '_blank', 'noopener')
              }
            }}
            disabled={!canonicalUrl}
          >
            Open source
          </button>
          {(before.length > 1 || after.length > 1) && (
            <button className="ghost-btn sm" onClick={() => setContextExpanded((prev) => !prev)}>
              {contextExpanded ? 'Collapse context' : 'Expand context'}
            </button>
          )}
          <button className="ghost-btn sm" onClick={() => setDetailsOpen((prev) => !prev)}>
            {detailsOpen ? 'Hide details' : 'View details'}
          </button>
        </div>

        {error && <div className="inspector-alert">{error}</div>}
        {detailsOpen && (
          <div className="inspector-meta-list">
            {location && (
              <div className="inspector-meta-row">
                <span className="inspector-meta-key">Location</span>
                <span className="inspector-meta-value">{location}</span>
              </div>
            )}
            {Object.entries(detail?.metadata || {})
              .filter(
                ([key]) => !/chunk/i.test(key) && !['file_path', 'lesson', 'part', 'part_title', 'chapter'].includes(key)
              )
              .map(([key, value]) => (
                <div key={key} className="inspector-meta-row">
                  <span className="inspector-meta-key">{key.replace(/_/g, ' ')}</span>
                  <span className="inspector-meta-value">{String(value)}</span>
                </div>
              ))}
            {!detail && (
              <div className="inspector-meta-row text-gray-500 text-sm">
                Metadata will appear when full context loads.
              </div>
            )}
          </div>
        )}
        <div className="inspector-context">
          {beforeToShow.map((c, idx) => (
            <div key={`before-${idx}`} className="inspector-context-muted">
              {c}
            </div>
          ))}
          <div className="inspector-context-highlight">{excerpt}</div>
          {afterToShow.map((c, idx) => (
            <div key={`after-${idx}`} className="inspector-context-muted">
              {c}
            </div>
          ))}
          {loading && <div className="inspector-loading text-xs text-gray-500">Loading full context…</div>}
        </div>
      </div>
    </aside>
  )
}

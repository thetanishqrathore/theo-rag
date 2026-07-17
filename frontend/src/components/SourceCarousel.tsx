import { useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import type { EvidencePreview } from '../lib/api'
import { cn } from '../lib/utils'

interface SourceCarouselProps {
  citations: EvidencePreview[]
  onSelect?: (evidence: EvidencePreview, index: number) => void
}

export function SourceCarousel({ citations, onSelect }: SourceCarouselProps) {
  const railRef = useRef<HTMLDivElement>(null)
  const cardRefs = useRef<Array<HTMLButtonElement | null>>([])
  const [activeIndex, setActiveIndex] = useState(0)
  const dragStateRef = useRef<{ active: boolean; startX: number; startScrollLeft: number; didDrag: boolean }>({
    active: false,
    startX: 0,
    startScrollLeft: 0,
    didDrag: false,
  })
  const suppressClickUntilRef = useRef(0)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    const rail = railRef.current
    if (!rail || citations.length <= 1) return

    let rafId: number | null = null
    const syncActiveIndex = () => {
      const railCenter = rail.scrollLeft + rail.clientWidth / 2
      let nextIndex = 0
      let minDistance = Number.POSITIVE_INFINITY

      cardRefs.current.forEach((card, idx) => {
        if (!card) return
        const cardCenter = card.offsetLeft + card.offsetWidth / 2
        const distance = Math.abs(cardCenter - railCenter)
        if (distance < minDistance) {
          minDistance = distance
          nextIndex = idx
        }
      })

      setActiveIndex(nextIndex)
    }

    const onScroll = () => {
      if (rafId !== null) cancelAnimationFrame(rafId)
      rafId = requestAnimationFrame(syncActiveIndex)
    }

    rail.addEventListener('scroll', onScroll, { passive: true })
    syncActiveIndex()
    return () => {
      rail.removeEventListener('scroll', onScroll)
      if (rafId !== null) cancelAnimationFrame(rafId)
    }
  }, [citations])

  const scrollToIndex = (index: number) => {
    const rail = railRef.current
    const card = cardRefs.current[index]
    if (!rail || !card) return
    const left = card.offsetLeft - Math.max(0, (rail.clientWidth - card.offsetWidth) / 2)
    rail.scrollTo({ left, behavior: 'smooth' })
    setActiveIndex(index)
  }

  const endDrag = (pointerId?: number) => {
    const rail = railRef.current
    if (!rail || !dragStateRef.current.active) return
    dragStateRef.current.active = false
    if (dragStateRef.current.didDrag) {
      suppressClickUntilRef.current = Date.now() + 150
    }
    dragStateRef.current.didDrag = false
    setDragging(false)
    if (pointerId != null && typeof rail.releasePointerCapture === 'function') {
      try {
        rail.releasePointerCapture(pointerId)
      } catch {
        // Ignore environments that do not fully support pointer capture.
      }
    }
  }

  const onRailPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rail = railRef.current
    if (!rail || event.pointerType !== 'mouse' || event.button !== 0) return
    dragStateRef.current.active = true
    dragStateRef.current.startX = event.clientX
    dragStateRef.current.startScrollLeft = rail.scrollLeft
    dragStateRef.current.didDrag = false
    setDragging(false)
    if (typeof rail.setPointerCapture === 'function') {
      try {
        rail.setPointerCapture(event.pointerId)
      } catch {
        // Ignore environments that do not fully support pointer capture.
      }
    }
  }

  const onRailPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rail = railRef.current
    if (!rail || !dragStateRef.current.active) return
    const deltaX = event.clientX - dragStateRef.current.startX
    if (!dragStateRef.current.didDrag && Math.abs(deltaX) > 4) {
      dragStateRef.current.didDrag = true
      setDragging(true)
    }
    if (dragStateRef.current.didDrag) {
      rail.scrollLeft = dragStateRef.current.startScrollLeft - deltaX
      event.preventDefault()
    }
  }

  const onRailClickCapture = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (Date.now() < suppressClickUntilRef.current) {
      event.preventDefault()
      event.stopPropagation()
    }
  }

  if (!citations || citations.length === 0) return null

  return (
    <div className="source-carousel-root mt-6">
      <div className="source-carousel-header mb-3 flex items-center justify-between text-[11px] uppercase tracking-[0.2em] text-gray-400">
        <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-amber-300 font-semibold">
          Sources
        </span>
        <span className="text-[10px] text-gray-500">{citations.length} references</span>
      </div>
      <div
        ref={railRef}
        className={cn('source-carousel-rail flex gap-3 overflow-x-auto pb-3 snap-x snap-mandatory', dragging && 'is-dragging')}
        style={{ scrollbarWidth: 'thin', msOverflowStyle: 'none' }}
        onPointerDown={onRailPointerDown}
        onPointerMove={onRailPointerMove}
        onPointerUp={(event) => endDrag(event.pointerId)}
        onPointerCancel={(event) => endDrag(event.pointerId)}
        onPointerLeave={() => endDrag()}
        onClickCapture={onRailClickCapture}
      >
        {citations.map((cit, idx) => {
          const preview = cit.preview || ''
          const rawLabel = cit.label || (cit.lesson ? `Lesson ${cit.lesson}` : `Source ${idx + 1}`)
          const label = rawLabel.replace(/\s*[·-]?\s*chunk\s+\d+\b/gi, '').trim()
          const pathBadge = cit.node_path || cit.section_path || null
          let pageBadge: string | null = null
          if (cit.page_start != null && cit.page_end != null) {
            pageBadge = cit.page_start === cit.page_end ? `p.${cit.page_start}` : `pp.${cit.page_start}-${cit.page_end}`
          } else if (cit.page_start != null) {
            pageBadge = `p.${cit.page_start}`
          }
          return (
            <button
              ref={(el) => {
                cardRefs.current[idx] = el
              }}
              type="button"
              key={cit.evidence_id}
              onClick={() => onSelect?.(cit, idx)}
              className={cn(
                'source-carousel-card group min-w-[220px] max-w-xs snap-start rounded-2xl border border-white/10 bg-white/5 p-4 text-left transition',
                'hover:border-amber-400/40 hover:bg-white/10 hover:shadow-[0_10px_40px_rgba(0,0,0,0.35)]'
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-amber-300">
                  {label}
                </div>
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wider',
                    cit.context_only ? 'bg-slate-500/20 text-slate-300' : 'bg-emerald-500/20 text-emerald-300'
                  )}
                >
                  {cit.context_only ? 'context-only' : 'primary'}
                </span>
              </div>
              {(pathBadge || pageBadge) && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
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
              <div className="mt-2 text-sm leading-relaxed text-gray-200">
                {preview.slice(0, 220)}
                {preview.length > 220 ? '…' : ''}
              </div>
            </button>
          )
        })}
      </div>
      {citations.length > 1 && (
        <div className="source-carousel-dots mt-2 flex items-center justify-center gap-2">
          {citations.map((_item, idx) => (
            <button
              key={`source-dot-${idx}`}
              type="button"
              className={cn('source-carousel-dot', idx === activeIndex && 'active')}
              onClick={() => scrollToIndex(idx)}
              aria-label={`View source ${idx + 1}`}
              aria-current={idx === activeIndex ? 'true' : undefined}
            />
          ))}
        </div>
      )}
    </div>
  )
}

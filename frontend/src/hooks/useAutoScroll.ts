import { useCallback, useEffect, useRef, useState } from 'react'

interface AutoScrollReturn {
  isSticky: boolean
  showJump: boolean
  hasNewContent: boolean
  scrollToBottom: (smooth?: boolean) => void
  forceSticky: () => void
}

interface UseAutoScrollOptions {
  enabled?: boolean
}

const UNSTICK_THRESHOLD = 150
const RESTICK_THRESHOLD = 60
const PROGRAMMATIC_SCROLL_WINDOW = 400

function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function useAutoScroll(
  scrollRef: React.RefObject<HTMLDivElement | null>,
  contentRef: React.RefObject<HTMLDivElement | null>,
  options: UseAutoScrollOptions = {}
): AutoScrollReturn {
  const { enabled = true } = options
  const [isSticky, setIsSticky] = useState(true)
  const [showJump, setShowJump] = useState(false)
  const [hasNewContent, setHasNewContent] = useState(false)

  const stickyRef = useRef(true)
  const programmaticUntil = useRef(0)
  const prevHeight = useRef(0)

  const scrollToBottom = useCallback(
    (smooth = false) => {
      if (!enabled) return
      const el = scrollRef.current
      if (!el) return
      const behavior = smooth && !prefersReducedMotion() ? 'smooth' : 'instant'
      programmaticUntil.current = Date.now() + PROGRAMMATIC_SCROLL_WINDOW
      el.scrollTo({ top: el.scrollHeight, behavior })
    },
    [enabled, scrollRef]
  )

  const forceSticky = useCallback(() => {
    stickyRef.current = true
    setIsSticky(true)
    setShowJump(false)
    setHasNewContent(false)
    scrollToBottom(true)
  }, [scrollToBottom])

  // Scroll listener — detect user scroll to stick/unstick
  useEffect(() => {
    if (!enabled) return
    const el = scrollRef.current
    if (!el) return

    let ticking = false
    const onScroll = () => {
      if (ticking) return
      ticking = true
      requestAnimationFrame(() => {
        ticking = false
        const distance = Math.max(0, el.scrollHeight - el.scrollTop - el.clientHeight)
        if (Date.now() < programmaticUntil.current) {
          // Ignore only the expected immediate programmatic settle near bottom.
          // If the user scrolls away from bottom, honor that intent immediately.
          if (distance <= RESTICK_THRESHOLD) return
          programmaticUntil.current = 0
        }
        if (stickyRef.current) {
          if (distance > UNSTICK_THRESHOLD) {
            stickyRef.current = false
            setIsSticky(false)
            setShowJump(true)
          }
        } else if (distance < RESTICK_THRESHOLD) {
          stickyRef.current = true
          setIsSticky(true)
          setShowJump(false)
          setHasNewContent(false)
        }
      })
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [enabled, scrollRef])

  // ResizeObserver on content — scroll when DOM grows
  useEffect(() => {
    if (!enabled) return
    const content = contentRef.current
    const scroll = scrollRef.current
    if (!content || !scroll) return

    prevHeight.current = content.getBoundingClientRect().height

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const newHeight = entry.contentRect.height
        if (newHeight > prevHeight.current) {
          if (stickyRef.current) {
            // Content grew while sticky — scroll instantly.
            // No programmatic window needed: the resulting scroll event
            // will see distance ≈ 0 and stay sticky naturally.
            // This lets user scroll-up events during streaming be detected.
            scroll.scrollTop = scroll.scrollHeight
          } else {
            // Content grew while scrolled up — indicate new content
            setHasNewContent(true)
          }
        }
        prevHeight.current = newHeight
      }
    })

    ro.observe(content)
    return () => ro.disconnect()
  }, [enabled, scrollRef, contentRef])

  return { isSticky, showJump, hasNewContent, scrollToBottom, forceSticky }
}

import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import type { EvidencePreview } from './lib/api'
import { useChatStore } from './store/chatStore'
import { Sidebar } from './components/Sidebar'
import { InputBar } from './components/InputBar'
import { MessageBubble } from './components/MessageBubble'
import { ModelSelector } from './components/ModelSelector'
import { RenameSessionDialog } from './components/RenameSessionDialog'
import { CopyToast } from './components/CopyToast'
import { SessionSwitchSkeleton } from './components/SessionSwitchSkeleton'
import { useGlobalShortcuts } from './hooks/useGlobalShortcuts'
import { useAutoScroll } from './hooks/useAutoScroll'

/** Detect swipe from left edge to open mobile sidebar */
function useEdgeSwipe(onSwipeRight: () => void, enabled: boolean) {
  const startX = useRef(0)
  const startY = useRef(0)
  const tracking = useRef(false)

  const handleTouchStart = useCallback((e: TouchEvent) => {
    if (!enabled) return
    const touch = e.touches[0]
    // Only start tracking if touch begins within 24px of left edge
    if (touch.clientX <= 24) {
      startX.current = touch.clientX
      startY.current = touch.clientY
      tracking.current = true
    }
  }, [enabled])

  const handleTouchEnd = useCallback((e: TouchEvent) => {
    if (!tracking.current) return
    tracking.current = false
    const touch = e.changedTouches[0]
    const deltaX = touch.clientX - startX.current
    const deltaY = Math.abs(touch.clientY - startY.current)
    // Must swipe at least 60px right and be more horizontal than vertical
    if (deltaX > 60 && deltaX > deltaY * 1.5) {
      onSwipeRight()
    }
  }, [onSwipeRight])

  useEffect(() => {
    if (!enabled) return
    document.addEventListener('touchstart', handleTouchStart, { passive: true })
    document.addEventListener('touchend', handleTouchEnd)
    return () => {
      document.removeEventListener('touchstart', handleTouchStart)
      document.removeEventListener('touchend', handleTouchEnd)
    }
  }, [enabled, handleTouchStart, handleTouchEnd])
}

const EvidenceInspector = lazy(() =>
  import('./components/EvidenceInspector').then((module) => ({ default: module.EvidenceInspector }))
)

function ChristSymbolIcon() {
  return (
    <svg
      className="hero-cross"
      viewBox="0 0 64 64"
      role="img"
      aria-label="Christ symbol"
    >
      <line x1="32" y1="10" x2="32" y2="54" />
      <line x1="16" y1="24" x2="48" y2="24" />
    </svg>
  )
}

export default function App() {
  const SESSION_SWITCH_SKELETON_MS = 220
  const {
    sessions,
    currentSessionId,
    streaming,
    collections,
    models,
    selectedModel,
    modelStatus,
    selectedCorpus,
    init,
    createSession,
    setActiveSession,
    setSelectedModel,
    setSelectedCorpus,
    deleteSession,
    renameSession,
    togglePinSession,
    sendMessage,
    cancelStream,
    health,
  } = useChatStore()

  const [booting, setBooting] = useState(true)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [isMobile, setIsMobile] = useState(() => window.matchMedia('(max-width: 600px)').matches)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [inspectorState, setInspectorState] = useState<{ items: EvidencePreview[]; index: number } | null>(null)
  const [renameTarget, setRenameTarget] = useState<{ id: string; title: string } | null>(null)
  const [copyToast, setCopyToast] = useState<{ id: number; message: string; tone: 'success' | 'error' } | null>(null)
  const [sessionSwitching, setSessionSwitching] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const sessionSwitchTimeoutRef = useRef<number | null>(null)
  const inspectorEvidence = inspectorState ? inspectorState.items[inspectorState.index] || null : null

  const { showJump, hasNewContent, forceSticky } = useAutoScroll(scrollRef, contentRef, {
    enabled: !booting,
  })

  useEffect(() => {
    init().finally(() => setBooting(false))
  }, [init])

  useEffect(() => {
    if (booting) return
    const rafId = window.requestAnimationFrame(() => {
      forceSticky()
    })
    return () => window.cancelAnimationFrame(rafId)
  }, [booting, currentSessionId, forceSticky])

  useEffect(() => {
    if (collections.length > 0 && !collections.includes(selectedCorpus)) {
      setSelectedCorpus(collections[0])
    }
  }, [collections, selectedCorpus, setSelectedCorpus])

  useEffect(() => {
    const media = window.matchMedia('(max-width: 600px)')
    const handleChange = (event: MediaQueryListEvent) => {
      setIsMobile(event.matches)
      if (!event.matches) {
        setMobileSidebarOpen(false)
      }
    }
    media.addEventListener('change', handleChange)
    return () => media.removeEventListener('change', handleChange)
  }, [])

  useEffect(() => {
    if (!isMobile) return
    const originalOverflow = document.body.style.overflow
    if (mobileSidebarOpen) {
      document.body.style.overflow = 'hidden'
    }
    return () => {
      document.body.style.overflow = originalOverflow
    }
  }, [isMobile, mobileSidebarOpen])

  useEffect(() => {
    if (!renameTarget) return
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = originalOverflow
    }
  }, [renameTarget])

  useEffect(() => {
    if (!copyToast) return
    const timeoutId = window.setTimeout(() => {
      setCopyToast((current) => (current?.id === copyToast.id ? null : current))
    }, 1600)
    return () => window.clearTimeout(timeoutId)
  }, [copyToast])

  useEffect(
    () => () => {
      if (sessionSwitchTimeoutRef.current) {
        window.clearTimeout(sessionSwitchTimeoutRef.current)
      }
    },
    []
  )


  const handleRegenerate = (messageId: string) => {
    const session = sessions.find((s) => s.id === currentSessionId)
    if (!session) return
    const messageIndex = session.messages.findIndex((m) => m.id === messageId)
    if (messageIndex <= 0) return
    const previousUser = [...session.messages.slice(0, messageIndex)].reverse().find((m) => m.role === 'user')
    if (!previousUser) return
    const corpus = session.corpus || selectedCorpus
    const model = session.model || selectedModel || undefined
    forceSticky()
    sendMessage(previousUser.content, corpus, model)
  }

  const showCopyToast = (message: string, tone: 'success' | 'error' = 'success') => {
    setCopyToast({ id: Date.now(), message, tone })
  }

  const sendWithStickyScroll = (query: string, corpus: string, model?: string) => {
    forceSticky()
    sendMessage(query, corpus, model)
  }

  const handleNewSession = () => {
    createSession()
    setInspectorState(null)
    setSessionSwitching(false)
    if (sessionSwitchTimeoutRef.current) {
      window.clearTimeout(sessionSwitchTimeoutRef.current)
      sessionSwitchTimeoutRef.current = null
    }
    if (isMobile) setMobileSidebarOpen(false)
  }

  const showSessionSwitchSkeleton = () => {
    if (sessionSwitchTimeoutRef.current) {
      window.clearTimeout(sessionSwitchTimeoutRef.current)
    }
    setSessionSwitching(true)
    sessionSwitchTimeoutRef.current = window.setTimeout(() => {
      setSessionSwitching(false)
      sessionSwitchTimeoutRef.current = null
    }, SESSION_SWITCH_SKELETON_MS)
  }

  useGlobalShortcuts({
    onNewChat: handleNewSession,
    onFocusComposer: () => window.dispatchEvent(new Event('focus-input')),
    disabled: Boolean(renameTarget || inspectorEvidence || mobileSidebarOpen),
  })

  useEdgeSwipe(
    () => setMobileSidebarOpen(true),
    isMobile && !mobileSidebarOpen
  )

  const currentSession = sessions.find((s) => s.id === currentSessionId)
  const hasMessages = currentSession && currentSession.messages.length > 0
  const showSessionSkeleton = sessionSwitching && !streaming.active && Boolean(hasMessages)
  const sidebarStatus: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }[] = [
    {
      label:
        health === 'ok'
          ? 'Healthy'
          : health === 'degraded'
            ? 'Degraded'
            : health === 'error'
              ? 'Offline'
              : 'Checking',
      tone: health === 'ok' ? 'success' : health === 'degraded' ? 'warning' : health === 'error' ? 'danger' : 'neutral',
    },
  ]
  if (booting) {
    return (
      <div className="boot-screen">
        <div className="boot-inner">
          <div className="hero-symbol">
            <ChristSymbolIcon />
          </div>
          <p className="boot-label">Loading&hellip;</p>
        </div>
      </div>
    )
  }

  return (
    <div
      className={`app-shell ${inspectorEvidence ? 'inspector-open' : ''} ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}
    >
      <Sidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        onSelectSession={(id) => {
          if (id !== currentSessionId && !streaming.active) {
            showSessionSwitchSkeleton()
          }
          setActiveSession(id)
          setInspectorState(null)
          if (isMobile) setMobileSidebarOpen(false)
        }}
        onNewSession={handleNewSession}
        onDeleteSession={deleteSession}
        onRenameSession={(id) => {
          const existing = sessions.find((s) => s.id === id)
          setRenameTarget({ id, title: existing?.title || '' })
        }}
        onTogglePinSession={togglePinSession}
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((prev) => !prev)}
        statusBadges={sidebarStatus}
        mobile={isMobile}
        mobileOpen={mobileSidebarOpen}
        onMobileClose={() => setMobileSidebarOpen(false)}
      />
      {isMobile && mobileSidebarOpen && (
        <div className="mobile-sidebar-overlay" onClick={() => setMobileSidebarOpen(false)} aria-hidden="true" />
      )}

      <div className="chat-panel">
        <div className="chat-topbar">
          <div className="flex flex-1 items-center gap-4">
            {isMobile && (
              <button
                className="mobile-menu-btn"
                onClick={() => setMobileSidebarOpen(true)}
                aria-label="Open chats"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
            )}
            <h1 className="topbar-title text-2xl font-semibold text-white">Research Engine</h1>
          </div>
          <div className="flex items-center gap-3">
            <ModelSelector
              models={models}
              selectedModel={selectedModel || (models[0] || null)}
              onSelectModel={(model) => setSelectedModel(model)}
              status={modelStatus}
            />
            <div
              className={`topbar-status inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] ${
                modelStatus === 'ready'
                  ? 'border-emerald-400/40 bg-emerald-500/15 text-emerald-200'
                  : modelStatus === 'loading'
                    ? 'border-amber-400/40 bg-amber-500/15 text-amber-200'
                    : 'border-red-400/40 bg-red-500/15 text-red-200'
              }`}
            >
              {modelStatus === 'ready' ? 'Ready' : modelStatus === 'loading' ? 'Loading' : 'Unavailable'}
            </div>
          </div>
        </div>

        <div className="chat-scroll" ref={scrollRef}>
          <div className="reading-column" ref={contentRef}>
            {!hasMessages && !streaming.active && !showSessionSkeleton && (
              <section className="hero-empty-state">
                <div className="hero-symbol">
                  <ChristSymbolIcon />
                </div>
                <p className="hero-pill">Bible RAG</p>
                <h2 className="hero-title">Study with clarity and confidence.</h2>
                <p className="hero-subpill">Rooted in Scripture. Verified by sources.</p>
                <p className="hero-copy">
                  Ask questions from your selected corpus. Each answer includes transparent sourcing and gentle refusal when the
                  record is unclear.
                </p>
              </section>
            )}

            {showSessionSkeleton ? (
              <SessionSwitchSkeleton />
            ) : (
              currentSession?.messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  onFollowUp={(q) => {
                    const followupCorpus =
                      message.role === 'assistant' ? message.corpus || selectedCorpus : selectedCorpus
                    const followupModel =
                      message.role === 'assistant' ? message.model || selectedModel || undefined : selectedModel || undefined
                    sendWithStickyScroll(q, followupCorpus, followupModel)
                  }}
                  onRetry={(q, corpus, model) => sendMessage(q, corpus || selectedCorpus, model || undefined)}
                  onRegenerate={handleRegenerate}
                  onInspectEvidence={(items, index) => setInspectorState({ items, index })}
                  onToast={showCopyToast}
                />
              ))
            )}
            <div className="chat-scroll-anchor" aria-hidden="true" />
          </div>
        </div>

        {showJump && (
          <button
            className="jump-latest"
            onClick={() => forceSticky()}
            aria-label="Jump to latest"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
            {hasNewContent && <span className="jump-latest-dot" />}
          </button>
        )}

        <InputBar
          onSend={(text) => sendWithStickyScroll(text, selectedCorpus, selectedModel || undefined)}
          onCancel={() => cancelStream()}
          isStreaming={streaming.active}
          disabled={streaming.active}
          corpus={selectedCorpus}
          corpora={collections.length ? collections : ['clough']}
          onCorpusChange={setSelectedCorpus}
        />
      </div>

      {inspectorEvidence && (
        <>
          <div
            className="inspector-overlay"
            onClick={() => setInspectorState(null)}
            aria-hidden="true"
          />
          <Suspense
            fallback={
              <aside className="inspector">
                <div className="inspector-card">Loading evidence inspector…</div>
              </aside>
            }
          >
            <EvidenceInspector
              key={inspectorEvidence.evidence_id}
              evidence={inspectorEvidence}
              canPrev={Boolean(inspectorState && inspectorState.index > 0)}
              canNext={Boolean(inspectorState && inspectorState.index < inspectorState.items.length - 1)}
              positionLabel={
                inspectorState && inspectorState.items.length > 1
                  ? `${inspectorState.index + 1} / ${inspectorState.items.length}`
                  : undefined
              }
              onPrev={() =>
                setInspectorState((current) => {
                  if (!current) return current
                  return { ...current, index: Math.max(0, current.index - 1) }
                })
              }
              onNext={() =>
                setInspectorState((current) => {
                  if (!current) return current
                  return { ...current, index: Math.min(current.items.length - 1, current.index + 1) }
                })
              }
              onClose={() => setInspectorState(null)}
              onToast={showCopyToast}
            />
          </Suspense>
        </>
      )}
      {renameTarget && (
        <RenameSessionDialog
          key={renameTarget.id}
          open
          initialValue={renameTarget.title}
          onCancel={() => setRenameTarget(null)}
          onSave={(nextTitle) => {
            renameSession(renameTarget.id, nextTitle)
            setRenameTarget(null)
          }}
        />
      )}
      <CopyToast toast={copyToast} />
    </div>
  )
}

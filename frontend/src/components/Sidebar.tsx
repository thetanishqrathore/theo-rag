import { useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { Plus, MessageSquare, Trash2, Menu, Pencil, Pin, Search } from 'lucide-react'
import type { Session } from '../store/types'
import { cn } from '../lib/utils'
import { activateFocusTrap } from '../lib/a11y'

interface SidebarProps {
  sessions: Session[]
  currentSessionId: string | null
  onSelectSession: (id: string) => void
  onNewSession: () => void
  onDeleteSession: (id: string) => void
  onRenameSession: (id: string) => void
  onTogglePinSession: (id: string) => void
  collapsed: boolean
  onToggleCollapse: () => void
  statusBadges?: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }[]
  mobile?: boolean
  mobileOpen?: boolean
  onMobileClose?: () => void
  onExportSessions?: () => void
  onImportSessions?: (file: File) => void
}

export function Sidebar({
  sessions,
  currentSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onRenameSession,
  onTogglePinSession,
  collapsed,
  onToggleCollapse,
  statusBadges = [],
  mobile = false,
  mobileOpen = false,
  onMobileClose,
  onExportSessions,
  onImportSessions,
}: SidebarProps) {
  const importInputRef = useRef<HTMLInputElement>(null)
  const [search, setSearch] = useState('')
  const [showSearchFlyout, setShowSearchFlyout] = useState(false)
  const searchIconRef = useRef<HTMLButtonElement>(null)
  const searchFlyoutRef = useRef<HTMLDivElement>(null)
  const asideRef = useRef<HTMLElement>(null)
  const toggleButtonRef = useRef<HTMLButtonElement>(null)
  const prefersReducedMotion = useReducedMotion()
  const isOpen = mobile ? true : !collapsed
  const deferredSearch = useDeferredValue(search)

  const filtered = useMemo(() => {
    const query = deferredSearch.trim().toLowerCase()
    if (!query) return sessions
    return sessions.filter((session) => {
      const titleMatch = session.title.toLowerCase().includes(query)
      const messageMatch = session.messages?.some((msg) => msg.content?.toLowerCase().includes(query))
      return titleMatch || messageMatch
    })
  }, [sessions, deferredSearch])

  const pinned = filtered.filter((s) => s.pinned)
  const regular = filtered.filter((s) => !s.pinned)
  const statusList = statusBadges.length > 0 ? statusBadges : [{ label: 'Status Unknown', tone: 'neutral' as const }]

  useEffect(() => {
    if (!showSearchFlyout) return
    const handler = (event: MouseEvent) => {
      if (
        searchFlyoutRef.current?.contains(event.target as Node) ||
        searchIconRef.current?.contains(event.target as Node)
      ) {
        return
      }
      setShowSearchFlyout(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showSearchFlyout])

  useEffect(() => {
    if (!mobile || !mobileOpen || !asideRef.current) return
    return activateFocusTrap(asideRef.current, {
      onEscape: () => onMobileClose?.(),
      initialFocus: toggleButtonRef.current,
    })
  }, [mobile, mobileOpen, onMobileClose])

  const formatMeta = (session: Session) => {
    const updated = new Date(session.updatedAt)
    const dateLabel = Number.isNaN(updated.getTime())
      ? ''
      : updated.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    const corpusLabel = session.corpus || 'Corpus'
    return dateLabel ? `${corpusLabel} • ${dateLabel}` : corpusLabel
  }

  return (
    <motion.aside
      ref={asideRef}
      data-collapsed={collapsed}
      data-mobile={mobile}
      role={mobile ? 'dialog' : undefined}
      aria-modal={mobile ? 'true' : undefined}
      aria-label={mobile ? 'Chat sessions' : undefined}
      className={cn(
        'sidebar-root sidebar-surface relative z-20 flex h-[100dvh] flex-col border-r border-white/10 text-gray-100 backdrop-blur',
        mobile &&
          'fixed left-0 top-0 z-40 w-[min(320px,86vw)] max-w-[86vw] shadow-2xl transition-transform duration-200',
        mobile && (mobileOpen ? 'translate-x-0' : '-translate-x-full pointer-events-none')
      )}
      animate={mobile ? undefined : { width: isOpen ? 260 : 78 }}
      transition={mobile ? undefined : { duration: prefersReducedMotion ? 0 : 0.22, ease: [0.25, 0.8, 0.25, 1] }}
    >
      <div
        className={cn(
          'sidebar-header-surface flex items-center border-b border-white/5 px-3',
          mobile ? 'h-auto min-h-16 pt-[max(16px,env(safe-area-inset-top))] pb-3 gap-2' : 'h-16',
          collapsed && !mobile ? 'justify-center' : 'gap-2'
        )}
      >
        <button
          ref={toggleButtonRef}
          onClick={mobile ? () => onMobileClose?.() : onToggleCollapse}
          className="rounded-full p-2 text-gray-400 transition hover:bg-white/10 hover:text-white"
          title={mobile ? 'Close sidebar' : collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {mobile ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          ) : (
            <Menu size={18} />
          )}
        </button>
        {(mobile || !collapsed) && (
          <div className="flex-1 text-center">
            <span className="sidebar-brand text-[11px] font-semibold uppercase tracking-[0.35em] text-gray-100">Bible Rag</span>
          </div>
        )}
        {(mobile || !collapsed) ? <div className="w-8" /> : null}
      </div>

      <div className={cn('px-3 py-3', collapsed && !mobile ? 'flex flex-col items-center gap-3' : 'space-y-3')}>
        <div className={cn('relative w-full', collapsed && !mobile && 'flex justify-center')}>
          <div
            className={cn(
              'flex h-11 w-full items-center rounded-2xl border border-white/10 bg-white/5 text-sm text-gray-200 transition focus-within:border-white/30',
              collapsed && !mobile ? 'w-11 justify-center px-0' : 'gap-2 px-3'
            )}
          >
            {collapsed && !mobile ? (
              <button
                ref={searchIconRef}
                onClick={() => setShowSearchFlyout((prev) => !prev)}
                className="flex h-full w-full items-center justify-center text-gray-400 transition hover:text-white"
                title="Search chats"
              >
                <Search size={16} />
              </button>
            ) : (
              <>
                <Search size={16} className="text-gray-500" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search chats"
                  className="w-full bg-transparent text-sm text-gray-100 placeholder:text-gray-500 outline-none"
                />
              </>
            )}
          </div>
          {collapsed && !mobile && showSearchFlyout && (
            <div
              ref={searchFlyoutRef}
              className="sidebar-search-popover absolute left-[calc(100%+12px)] top-1/2 z-20 w-64 -translate-y-1/2 rounded-2xl border border-white/10 p-3 shadow-2xl"
            >
              <div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-gray-100">
                <Search size={14} className="text-gray-500" />
                <input
                  autoFocus
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search chats"
                  className="w-full bg-transparent outline-none placeholder:text-gray-500"
                />
              </div>
              <div className="mt-2 text-[11px] text-gray-500">Search titles and recent messages</div>
            </div>
          )}
        </div>
        <button
          onClick={onNewSession}
          aria-keyshortcuts="Meta+N Control+N"
          className={cn(
            'flex w-full items-center justify-center gap-2 rounded-2xl border border-emerald-400/30 bg-emerald-500/10 px-3 py-2 text-sm font-semibold text-emerald-200 transition hover:border-emerald-300/70 hover:bg-emerald-500/20',
            collapsed && !mobile && 'h-11 w-11 rounded-2xl px-0 py-0'
          )}
          title="New chat"
        >
          <Plus size={18} />
          {(!collapsed || mobile) && <span>New chat</span>}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-4">
        {!collapsed || mobile ? (
          <nav className="space-y-4">
            {pinned.length > 0 && (
              <SidebarSection label="Pinned" collapsed={collapsed}>
                {pinned.map((session) => (
                  <SidebarRow
                    key={session.id}
                    session={session}
                    meta={formatMeta(session)}
                    active={session.id === currentSessionId}
                    onSelect={() => onSelectSession(session.id)}
                    onDelete={() => onDeleteSession(session.id)}
                    onRename={() => onRenameSession(session.id)}
                    onTogglePin={() => onTogglePinSession(session.id)}
                    collapsed={collapsed}
                  />
                ))}
              </SidebarSection>
            )}
            <SidebarSection label="Recent" collapsed={collapsed}>
              {regular.map((session) => (
                <SidebarRow
                  key={session.id}
                  session={session}
                  meta={formatMeta(session)}
                  active={session.id === currentSessionId}
                  onSelect={() => onSelectSession(session.id)}
                  onDelete={() => onDeleteSession(session.id)}
                  onRename={() => onRenameSession(session.id)}
                  onTogglePin={() => onTogglePinSession(session.id)}
                  collapsed={collapsed}
                />
              ))}
              {regular.length === 0 && <div className="px-3 text-xs text-gray-500">No sessions yet.</div>}
            </SidebarSection>
          </nav>
        ) : null}
      </div>

      <div className="border-t border-white/5 px-3 py-4">
        {(!collapsed || mobile) && (onExportSessions || onImportSessions) && (
          <div className="mb-3 flex gap-2 text-[11px]">
            {onExportSessions && (
              <button
                type="button"
                onClick={onExportSessions}
                className="flex-1 rounded-lg border border-white/10 px-2 py-1 text-gray-300 hover:bg-white/5"
                title="Download all chats as a JSON file (chats are stored only in this browser)"
              >
                Export chats
              </button>
            )}
            {onImportSessions && (
              <>
                <button
                  type="button"
                  onClick={() => importInputRef.current?.click()}
                  className="flex-1 rounded-lg border border-white/10 px-2 py-1 text-gray-300 hover:bg-white/5"
                  title="Restore chats from an exported JSON file"
                >
                  Import chats
                </button>
                <input
                  ref={importInputRef}
                  type="file"
                  accept="application/json,.json"
                  className="hidden"
                  aria-label="Import chats file"
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) onImportSessions(file)
                    e.target.value = ''
                  }}
                />
              </>
            )}
          </div>
        )}
        {collapsed && !mobile ? (
          <div className="flex justify-center">
            {(() => {
              const badge = statusList[0]
              return (
                <span
                  className={cn(
                    'h-3 w-3 rounded-full border border-white/20',
                    badge.tone === 'success'
                      ? 'border-emerald-400/50 bg-emerald-400/80'
                      : badge.tone === 'warning'
                        ? 'border-amber-300/60 bg-amber-300/80'
                        : badge.tone === 'danger'
                          ? 'border-red-400/60 bg-red-400/80'
                          : 'bg-white/40'
                  )}
                  title={badge.label}
                />
              )
            })()}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-2">
            {statusList.map((badge, idx) => (
              <StatusRow key={`sidebar-status-${badge.label}-${idx}`} label={badge.label} tone={badge.tone} />
            ))}
          </div>
        )}
      </div>
    </motion.aside>
  )
}

function SidebarRow({
  session,
  meta,
  active,
  onSelect,
  onDelete,
  onRename,
  onTogglePin,
  collapsed,
}: {
  session: Session
  meta: string
  active: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: () => void
  onTogglePin: () => void
  collapsed: boolean
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const confirmTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (confirmTimerRef.current) window.clearTimeout(confirmTimerRef.current)
    }
  }, [])

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (confirmingDelete) {
      if (confirmTimerRef.current) window.clearTimeout(confirmTimerRef.current)
      setConfirmingDelete(false)
      onDelete()
    } else {
      setConfirmingDelete(true)
      confirmTimerRef.current = window.setTimeout(() => setConfirmingDelete(false), 3000)
    }
  }

  const fallbackTitle =
    session.title?.trim() ||
    session.messages
      .slice()
      .reverse()
      .find((m) => m.role === 'user')?.content?.slice(0, 60) ||
    'New session'

  return (
    <div className="group relative">
      <button
        type="button"
        className={cn(
          'flex w-full cursor-pointer items-center gap-3 rounded-2xl border border-transparent px-3 py-2 text-left transition',
          active
            ? 'border-emerald-500/30 bg-white/5 text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.04)]'
            : 'text-gray-300 hover:border-white/10 hover:bg-white/5',
          collapsed && 'flex-col items-center gap-2 rounded-2xl px-2 py-3 text-center'
        )}
        onClick={onSelect}
        title={fallbackTitle}
        aria-label={`Open chat ${fallbackTitle}`}
      >
        <div
          className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-white/5 text-gray-400 transition',
            active && 'text-emerald-300',
            collapsed && 'h-10 w-10'
          )}
        >
          <MessageSquare size={16} />
        </div>
        <div className={cn('min-w-0 flex-1 pr-6', collapsed && 'hidden')}>
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-white">{fallbackTitle}</span>
            {session.pinned && <Pin size={12} className="text-amber-300" />}
          </div>
          <div className="truncate text-xs text-gray-500">{meta}</div>
        </div>
      </button>
      {!collapsed && (
        <div
          className={cn(
            'sidebar-row-actions-surface pointer-events-none absolute inset-y-1 right-1 hidden items-center gap-0 rounded-full px-0.5 py-0.5 text-xs text-gray-400 shadow-lg group-hover:flex group-hover:pointer-events-auto group-focus-within:flex group-focus-within:pointer-events-auto',
            confirmingDelete && '!flex !pointer-events-auto'
          )}
        >
          {confirmingDelete ? (
            <>
              <button
                type="button"
                className="sidebar-row-action rounded-full p-2 text-red-400 transition hover:text-red-200"
                onClick={handleDeleteClick}
                title="Confirm delete"
                aria-label="Confirm delete"
              >
                <Trash2 size={14} />
              </button>
              <span className="px-1 text-[10px] font-semibold text-red-300">Delete?</span>
              <button
                type="button"
                className="sidebar-row-action rounded-full p-2 text-gray-400 transition hover:text-white"
                onClick={(e) => {
                  e.stopPropagation()
                  setConfirmingDelete(false)
                  if (confirmTimerRef.current) window.clearTimeout(confirmTimerRef.current)
                }}
                title="Cancel"
                aria-label="Cancel delete"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className={cn('sidebar-row-action rounded-full p-2 text-gray-400 transition hover:text-amber-300')}
                onClick={(e) => {
                  e.stopPropagation()
                  onTogglePin()
                }}
                title={session.pinned ? 'Unpin chat' : 'Pin chat'}
                aria-label={session.pinned ? 'Unpin chat' : 'Pin chat'}
              >
                <Pin size={14} />
              </button>
              <button
                type="button"
                className={cn('sidebar-row-action rounded-full p-2 text-gray-400 transition hover:text-blue-300')}
                onClick={(e) => {
                  e.stopPropagation()
                  onRename()
                }}
                title="Rename chat"
                aria-label="Rename chat"
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                className={cn('sidebar-row-action rounded-full p-2 text-gray-400 transition hover:text-red-300')}
                onClick={handleDeleteClick}
                title="Delete chat"
                aria-label="Delete chat"
              >
                <Trash2 size={14} />
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function SidebarSection({ label, children, collapsed }: { label: string; children: ReactNode; collapsed: boolean }) {
  return (
    <section className="space-y-2">
      <div
        className={cn(
          'px-3 text-[11px] font-semibold uppercase tracking-[0.2em] text-gray-500',
          collapsed && 'text-center text-[10px]'
        )}
      >
        {label}
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  )
}

function StatusRow({ label, tone }: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }) {
  const colorClasses =
    tone === 'success'
      ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-200'
      : tone === 'warning'
        ? 'border-amber-500/30 bg-amber-500/5 text-amber-200'
        : tone === 'danger'
          ? 'border-red-500/30 bg-red-500/5 text-red-200'
          : 'border-white/15 bg-white/5 text-gray-300'
  return (
    <div className={cn('flex w-full items-center justify-center gap-2 rounded-2xl border px-3 py-2 text-xs font-medium', colorClasses)}>
      <span
        className={cn(
          'h-2 w-2 rounded-full',
          tone === 'success'
            ? 'bg-emerald-300'
            : tone === 'warning'
              ? 'bg-amber-300'
              : tone === 'danger'
                ? 'bg-red-400'
                : 'bg-gray-400'
        )}
      />
      <span className="truncate">{label}</span>
    </div>
  )
}

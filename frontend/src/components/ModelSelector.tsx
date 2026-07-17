import { useMemo, useRef, useState, useEffect, useCallback } from 'react'
import { ChevronDown, Search, Loader2 } from 'lucide-react'
import { cn } from '../lib/utils'

interface ModelSelectorProps {
  models: string[]
  selectedModel: string | null
  onSelectModel: (model: string) => void
  status?: 'loading' | 'ready' | 'error'
}

export function ModelSelector({ models, selectedModel, onSelectModel, status = 'loading' }: ModelSelectorProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const ref = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const searchInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Focus search input when dropdown opens
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => searchInputRef.current?.focus())
      // Set active index to selected model
      const idx = filtered.findIndex((m) => m === selectedModel)
      setActiveIndex(Math.max(0, idx))
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    if (!query.trim()) return models
    const lower = query.trim().toLowerCase()
    return models.filter((model) => model.toLowerCase().includes(lower))
  }, [models, query])

  // Reset active index when filtered list changes
  useEffect(() => {
    setActiveIndex(0)
  }, [filtered.length])

  const closeDropdown = useCallback((focusTrigger = false) => {
    setOpen(false)
    setQuery('')
    if (focusTrigger) {
      requestAnimationFrame(() => triggerRef.current?.focus())
    }
  }, [])

  const commitSelection = useCallback((index: number) => {
    const model = filtered[index]
    if (!model) return
    onSelectModel(model)
    closeDropdown(true)
  }, [filtered, onSelectModel, closeDropdown])

  const focusOption = useCallback((index: number) => {
    const clamped = Math.max(0, Math.min(filtered.length - 1, index))
    setActiveIndex(clamped)
    requestAnimationFrame(() => optionRefs.current[clamped]?.scrollIntoView({ block: 'nearest' }))
  }, [filtered.length])

  const handleDropdownKeyDown = useCallback((event: React.KeyboardEvent) => {
    if (!open || status !== 'ready') return

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault()
        focusOption(activeIndex + 1)
        break
      case 'ArrowUp':
        event.preventDefault()
        focusOption(activeIndex - 1)
        break
      case 'Home':
        event.preventDefault()
        focusOption(0)
        break
      case 'End':
        event.preventDefault()
        focusOption(filtered.length - 1)
        break
      case 'Enter':
        event.preventDefault()
        commitSelection(activeIndex)
        break
      case 'Escape':
        event.preventDefault()
        closeDropdown(true)
        break
      case 'Tab':
        closeDropdown()
        break
    }
  }, [open, status, activeIndex, filtered.length, focusOption, commitSelection, closeDropdown])

  const handleTriggerKeyDown = useCallback((event: React.KeyboardEvent) => {
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && !open) {
      event.preventDefault()
      setOpen(true)
    }
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      closeDropdown(true)
    }
  }, [open, closeDropdown])

  const label = selectedModel || (status === 'loading' ? 'Loading models…' : 'Select model')

  return (
    <div className="model-selector relative" ref={ref}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={handleTriggerKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-4 py-1.5 text-xs font-semibold text-gray-100 transition hover:bg-white/10"
      >
        <span className="max-w-[180px] truncate">{label}</span>
        <ChevronDown size={12} className={cn('transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <div
          className="model-dropdown-surface absolute right-0 top-full z-30 mt-3 w-72 max-w-[min(92vw,22rem)] rounded-2xl border border-white/10 p-3 shadow-2xl"
          onKeyDown={handleDropdownKeyDown}
          role="listbox"
          aria-label="Select model"
        >
          <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs text-gray-300">
            <Search size={12} />
            <input
              ref={searchInputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search models…"
              className="w-full bg-transparent text-xs text-gray-200 placeholder:text-gray-500 focus:outline-none"
              aria-label="Search models"
            />
          </div>

          <div className="mt-3 max-h-56 space-y-1 overflow-y-auto pr-1">
            {status === 'loading' && (
              <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-gray-400">
                <Loader2 size={12} className="animate-spin" />
                Loading models…
              </div>
            )}
            {status === 'error' && (
              <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                Unable to load models.
              </div>
            )}
            {status === 'ready' && filtered.length === 0 && (
              <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-gray-400">
                No matches.
              </div>
            )}
            {status === 'ready' &&
              filtered.map((model, idx) => (
                <button
                  ref={(el) => { optionRefs.current[idx] = el }}
                  key={model}
                  role="option"
                  aria-selected={model === selectedModel}
                  onClick={() => commitSelection(idx)}
                  onMouseEnter={() => setActiveIndex(idx)}
                  className={cn(
                    'flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-xs transition',
                    model === selectedModel ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-300 hover:bg-white/5',
                    idx === activeIndex && 'outline outline-1 outline-white/25'
                  )}
                >
                  <span className="truncate">{model}</span>
                  {model === selectedModel && (
                    <span className="text-[10px] uppercase tracking-wider text-emerald-300">Active</span>
                  )}
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}

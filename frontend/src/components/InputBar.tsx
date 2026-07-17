import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Square, ChevronDown, Database } from 'lucide-react'
import { cn } from '../lib/utils'

export function InputBar({
  onSend,
  onCancel,
  isStreaming,
  disabled,
  corpus,
  corpora,
  onCorpusChange,
}: {
  onSend: (text: string) => void
  onCancel?: () => void
  isStreaming?: boolean
  disabled?: boolean
  corpus: string
  corpora: string[]
  onCorpusChange: (value: string) => void
}) {
  const [value, setValue] = useState('')
  const [showCorpusDropdown, setShowCorpusDropdown] = useState(false)
  const [activeCorpusIndex, setActiveCorpusIndex] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const corpusRef = useRef<HTMLDivElement>(null)
  const corpusTriggerRef = useRef<HTMLButtonElement>(null)
  const corpusOptionRefs = useRef<Array<HTMLButtonElement | null>>([])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!value.trim() || disabled) return
    onSend(value.trim())
    setValue('')
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (value.trim() && !disabled) {
        onSend(value.trim())
        setValue('')
      }
    }
  }

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = '24px'
    if (!value.trim()) {
      textarea.style.overflowY = 'hidden'
      return
    }
    const newHeight = Math.min(textarea.scrollHeight, 160)
    textarea.style.height = `${newHeight}px`
    textarea.style.overflowY = textarea.scrollHeight > 160 ? 'auto' : 'hidden'
  }, [value])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!corpusRef.current) return
      if (!corpusRef.current.contains(e.target as Node)) {
        setShowCorpusDropdown(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  useEffect(() => {
    if (!showCorpusDropdown) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowCorpusDropdown(false)
        requestAnimationFrame(() => corpusTriggerRef.current?.focus())
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [showCorpusDropdown])

  useEffect(() => {
    const focusListener = () => textareaRef.current?.focus()
    window.addEventListener('focus-input', focusListener as EventListener)
    return () => window.removeEventListener('focus-input', focusListener as EventListener)
  }, [])

  const corpusLabel = corpus
  const focusCorpusIndex = (index: number) => {
    const clamped = Math.max(0, Math.min(corpora.length - 1, index))
    setActiveCorpusIndex(clamped)
    requestAnimationFrame(() => {
      corpusOptionRefs.current[clamped]?.focus()
    })
  }

  const openCorpusDropdown = () => {
    if (corpora.length <= 1) return
    const selectedIndex = Math.max(0, corpora.indexOf(corpus))
    setShowCorpusDropdown(true)
    focusCorpusIndex(selectedIndex)
  }

  const closeCorpusDropdown = (focusTrigger = false) => {
    setShowCorpusDropdown(false)
    if (focusTrigger) {
      requestAnimationFrame(() => corpusTriggerRef.current?.focus())
    }
  }

  const commitCorpusSelection = (index: number) => {
    const name = corpora[index]
    if (!name) return
    onCorpusChange(name)
    closeCorpusDropdown(true)
  }

  const handleDropdownKeyDown = (event: React.KeyboardEvent) => {
    if (!showCorpusDropdown || corpora.length <= 1) return
    const focusedIndex = corpusOptionRefs.current.findIndex((option) => option === document.activeElement)
    const currentIndex = focusedIndex >= 0 ? focusedIndex : activeCorpusIndex
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      focusCorpusIndex(currentIndex + 1)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      focusCorpusIndex(currentIndex - 1)
      return
    }
    if (event.key === 'Home') {
      event.preventDefault()
      focusCorpusIndex(0)
      return
    }
    if (event.key === 'End') {
      event.preventDefault()
      focusCorpusIndex(corpora.length - 1)
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      commitCorpusSelection(currentIndex)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeCorpusDropdown(true)
      return
    }
    if (event.key === 'Tab') {
      closeCorpusDropdown()
    }
  }

  return (
    <form className="input-bar input-bar-form mx-auto w-full max-w-4xl px-4 pb-6" onSubmit={handleSubmit}>
      <div className="input-bar-shell input-shell-surface relative flex h-14 w-full items-center gap-3 rounded-full border border-white/10 pl-4 pr-2 shadow-[0_25px_80px_rgba(0,0,0,0.45)] transition focus-within:border-white/20">
        <div className="relative flex h-full shrink-0 items-center gap-2 pr-3" ref={corpusRef}>
          <button
            ref={corpusTriggerRef}
            type="button"
            onClick={() => {
              if (!showCorpusDropdown) {
                openCorpusDropdown()
              } else {
                closeCorpusDropdown()
              }
            }}
            onKeyDown={(event) => {
              if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && corpora.length > 1) {
                event.preventDefault()
                openCorpusDropdown()
              }
              if (event.key === 'Escape') {
                closeCorpusDropdown()
              }
            }}
            className={cn(
              'inline-flex h-9 items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-xs font-semibold text-emerald-300 transition',
              corpora.length > 1 ? 'hover:border-emerald-400/40 hover:text-white' : 'cursor-default'
            )}
            aria-haspopup="listbox"
            aria-expanded={showCorpusDropdown}
            aria-controls="corpus-listbox"
          >
            <Database size={14} className="input-icon" />
            <span className="max-w-[140px] truncate">{corpusLabel}</span>
            {corpora.length > 1 && (
              <ChevronDown className={cn('input-icon h-3 w-3 transition', showCorpusDropdown && 'rotate-180')} />
            )}
          </button>
          {showCorpusDropdown && corpora.length > 1 && (
            <div
              id="corpus-listbox"
              role="listbox"
              aria-labelledby="corpus-listbox-label"
              onKeyDown={handleDropdownKeyDown}
              className="input-dropdown-surface absolute bottom-full left-0 z-20 mb-2 w-56 rounded-2xl border border-white/10 p-2 shadow-2xl"
            >
              <div className="sr-only" id="corpus-listbox-label">Select corpus</div>
              {corpora.map((name, idx) => (
                <button
                  ref={(element) => {
                    corpusOptionRefs.current[idx] = element
                  }}
                  key={`${name}-${idx}`}
                  type="button"
                  onClick={() => commitCorpusSelection(idx)}
                  onFocus={() => setActiveCorpusIndex(idx)}
                  onMouseEnter={() => setActiveCorpusIndex(idx)}
                  role="option"
                  aria-selected={name === corpus}
                  tabIndex={activeCorpusIndex === idx ? 0 : -1}
                  className={cn(
                    'w-full rounded-xl px-3 py-2 text-left text-sm text-gray-200 transition hover:bg-white/5',
                    name === corpus && 'border border-emerald-400/20 bg-emerald-500/10 text-emerald-200',
                    activeCorpusIndex === idx && 'outline outline-1 outline-white/25'
                  )}
                >
                  {name}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex h-full min-w-0 flex-1 items-center">
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything…"
            disabled={disabled}
            aria-label="Message input"
            className="min-w-0 w-full resize-none bg-transparent py-0 text-base leading-6 text-white placeholder:text-gray-500 focus:outline-none"
          />
        </div>

        <button
          type={isStreaming ? 'button' : 'submit'}
          onClick={isStreaming ? onCancel : undefined}
          disabled={isStreaming ? !onCancel : !value.trim() || disabled}
          aria-label={isStreaming ? 'Stop generation' : 'Send message'}
          className={cn(
            'input-send-btn ml-1.5 flex items-center justify-center rounded-full border border-white/10 transition',
            isStreaming || (value.trim() && !disabled)
              ? 'bg-white text-black hover:bg-gray-200 hover:border-white/20'
              : 'bg-white/5 text-gray-500'
          )}
        >
          {isStreaming ? (
            <Square size={17} className="input-icon" />
          ) : (
            <ArrowUp size={19} strokeWidth={2.5} className="input-icon" />
          )}
        </button>
      </div>
      <div className="input-bar-note mt-3 text-center text-xs text-gray-500">
        Grounded in selected corpus. Verify with original documents.
      </div>
    </form>
  )
}

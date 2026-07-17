import { useEffect, useRef, useState } from 'react'
import { activateFocusTrap } from '../lib/a11y'

interface RenameSessionDialogProps {
  open: boolean
  initialValue: string
  onSave: (nextTitle: string) => void
  onCancel: () => void
}

export function RenameSessionDialog({
  open,
  initialValue,
  onSave,
  onCancel,
}: RenameSessionDialogProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [value, setValue] = useState(initialValue)

  useEffect(() => {
    if (!open || !panelRef.current) return
    return activateFocusTrap(panelRef.current, {
      onEscape: onCancel,
      initialFocus: inputRef.current,
    })
  }, [open, onCancel])

  if (!open) return null

  return (
    <div className="rename-dialog-overlay" role="presentation" onClick={onCancel}>
      <div
        ref={panelRef}
        className="rename-dialog-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rename-session-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="rename-session-title" className="rename-dialog-title">
          Rename chat
        </h2>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            const trimmed = value.trim()
            if (!trimmed) return
            onSave(trimmed)
          }}
          className="rename-dialog-form"
        >
          <input
            ref={inputRef}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            className="rename-dialog-input"
            aria-label="Chat title"
            maxLength={120}
          />
          <div className="rename-dialog-actions">
            <button type="button" className="ghost-btn" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="ghost-btn active" disabled={!value.trim()}>
              Save
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

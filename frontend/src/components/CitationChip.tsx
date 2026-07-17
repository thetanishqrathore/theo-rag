export function CitationChip({
  index,
  onClick,
  preview,
  contextOnly,
  label,
}: {
  index: number
  onClick?: () => void
  preview?: string
  contextOnly?: boolean
  label?: string
}) {
  const tooltipText =
    label && preview
      ? `${label}\n${preview.slice(0, 120)}${preview.length > 120 ? '...' : ''}`
      : label || (preview ? preview.slice(0, 120) : '')

  return (
    <button
      type="button"
      className={`citation-chip ${contextOnly ? 'context-only' : ''}`}
      onClick={onClick}
      title={`View source ${index}`}
      data-tooltip={tooltipText || undefined}
      aria-label={`Citation ${index}${label ? `: ${label}` : ''}`}
      aria-haspopup="dialog"
      onKeyDown={(event) => {
        if ((event.key === 'Enter' || event.key === ' ') && onClick) {
          event.preventDefault()
          onClick()
        }
      }}
    >
      {index}
    </button>
  )
}

interface CopyToastState {
  id: number
  message: string
  tone: 'success' | 'error'
}

export function CopyToast({ toast }: { toast: CopyToastState | null }) {
  if (!toast) return null

  return (
    <div className={`copy-toast copy-toast-${toast.tone}`} role="status" aria-live="polite" aria-atomic="true">
      {toast.message}
    </div>
  )
}

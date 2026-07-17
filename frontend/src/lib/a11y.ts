const FOCUSABLE_SELECTOR = [
  'a[href]',
  'area[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(',')

function isVisible(el: HTMLElement) {
  if (el.hidden) return false
  const style = window.getComputedStyle(el)
  if (style.display === 'none' || style.visibility === 'hidden') return false
  return true
}

function getFocusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => !el.hasAttribute('disabled') && isVisible(el)
  )
}

export function activateFocusTrap(
  container: HTMLElement,
  options?: {
    onEscape?: () => void
    restoreFocus?: boolean
    initialFocus?: HTMLElement | null
  }
) {
  const previousActive = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const shouldRestoreFocus = options?.restoreFocus !== false
  let stopped = false

  if (!container.hasAttribute('tabindex')) {
    container.setAttribute('tabindex', '-1')
  }

  const focusInitial = () => {
    const focusables = getFocusableElements(container)
    const target = options?.initialFocus || focusables[0] || container
    target.focus({ preventScroll: true })
  }

  focusInitial()

  const onKeyDown = (event: KeyboardEvent) => {
    if (stopped) return
    if (event.key === 'Escape') {
      options?.onEscape?.()
      return
    }
    if (event.key !== 'Tab') return

    const focusables = getFocusableElements(container)
    if (focusables.length === 0) {
      event.preventDefault()
      container.focus({ preventScroll: true })
      return
    }

    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    const active = document.activeElement as HTMLElement | null

    if (event.shiftKey) {
      if (!active || active === first || !container.contains(active)) {
        event.preventDefault()
        last.focus({ preventScroll: true })
      }
      return
    }

    if (!active || active === last || !container.contains(active)) {
      event.preventDefault()
      first.focus({ preventScroll: true })
    }
  }

  const onFocusIn = (event: FocusEvent) => {
    if (stopped) return
    if (!container.contains(event.target as Node)) {
      focusInitial()
    }
  }

  document.addEventListener('keydown', onKeyDown)
  document.addEventListener('focusin', onFocusIn)

  return () => {
    stopped = true
    document.removeEventListener('keydown', onKeyDown)
    document.removeEventListener('focusin', onFocusIn)
    if (shouldRestoreFocus && previousActive && document.contains(previousActive)) {
      previousActive.focus({ preventScroll: true })
    }
  }
}

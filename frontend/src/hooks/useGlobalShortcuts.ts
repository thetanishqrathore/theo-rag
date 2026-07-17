import { useEffect } from 'react'

interface UseGlobalShortcutsArgs {
  onNewChat: () => void
  onFocusComposer: () => void
  disabled?: boolean
}

export function useGlobalShortcuts({ onNewChat, onFocusComposer, disabled = false }: UseGlobalShortcutsArgs) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (disabled || event.repeat) return

      const hasPrimaryModifier = event.metaKey || event.ctrlKey
      if (!hasPrimaryModifier || event.altKey) return

      const key = event.key.toLowerCase()
      if (key === 'n') {
        event.preventDefault()
        onNewChat()
      }

      if (key === 'k') {
        event.preventDefault()
        onFocusComposer()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [disabled, onFocusComposer, onNewChat])
}

const STARTER_PROMPTS = [
  'What does Genesis 1 teach about creation and order?',
  'Summarize the Sermon on the Mount with key citations.',
  'Compare faith and works in James 2 and Romans 3.',
  'Where does Scripture discuss wisdom for anxious seasons?',
]

export function StarterPrompts({ onSelectPrompt }: { onSelectPrompt: (prompt: string) => void }) {
  return (
    <div className="starter-prompts" aria-label="Starter prompts">
      <div className="starter-prompts-label">Try a starter question</div>
      <div className="starter-prompts-grid">
        {STARTER_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            onClick={() => onSelectPrompt(prompt)}
            className="starter-prompt-btn"
            aria-label={`Ask: ${prompt}`}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  )
}

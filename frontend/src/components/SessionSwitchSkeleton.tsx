export function SessionSwitchSkeleton() {
  return (
    <section className="session-switch-skeleton" role="status" aria-live="polite" aria-label="Loading messages">
      <div className="session-switch-skeleton-row">
        <span className="session-switch-skeleton-avatar" />
        <div className="session-switch-skeleton-bubble">
          <span className="session-switch-skeleton-line line-lg" />
          <span className="session-switch-skeleton-line line-md" />
          <span className="session-switch-skeleton-line line-sm" />
        </div>
      </div>
      <div className="session-switch-skeleton-row user">
        <div className="session-switch-skeleton-user-bubble">
          <span className="session-switch-skeleton-line line-md" />
        </div>
      </div>
      <div className="session-switch-skeleton-row">
        <span className="session-switch-skeleton-avatar" />
        <div className="session-switch-skeleton-bubble">
          <span className="session-switch-skeleton-line line-lg" />
          <span className="session-switch-skeleton-line line-sm" />
        </div>
      </div>
    </section>
  )
}

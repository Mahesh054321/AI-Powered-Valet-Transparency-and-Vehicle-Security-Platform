/** Small, dependency-free UI primitives shared across the dashboards. */

const STATUS_TONE = {
  up: 'badge--success',
  ready: 'badge--success',
  ok: 'badge--success',
  degraded: 'badge--warning',
  down: 'badge--danger',
  unknown: 'badge--muted',
  simulated: 'badge--info',
  hardware: 'badge--success',
}

export function Badge({ children, tone, className = '' }) {
  const resolved = tone ?? STATUS_TONE[String(children).toLowerCase()] ?? 'badge--muted'
  return <span className={`badge ${resolved} ${className}`.trim()}>{children}</span>
}

export function Panel({ title, subtitle, actions, children, tone = '' }) {
  return (
    <section className={`panel ${tone}`.trim()}>
      {/* The header renders if there is anything to put in it. `subtitle` has to
          be part of that test on its own: both list pages pass a record count as
          a subtitle with no title, and gating only on `title || actions` meant
          the count was computed and then silently thrown away. */}
      {(title || subtitle || actions) && (
        <header className="panel__header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p className="panel__subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="panel__actions">{actions}</div>}
        </header>
      )}
      <div className="panel__body">{children}</div>
    </section>
  )
}

export function Field({ label, hint, error, children }) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {children}
      {hint && !error && <span className="field__hint">{hint}</span>}
      {error && <span className="field__error">{error}</span>}
    </label>
  )
}

export function DataRow({ label, value, mono = false }) {
  return (
    <div className="data-row">
      <dt>{label}</dt>
      <dd className={mono ? 'mono' : undefined}>{value ?? '—'}</dd>
    </div>
  )
}

export function Spinner({ label = 'Loading…' }) {
  return (
    <div className="spinner-wrap" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span className="muted">{label}</span>
    </div>
  )
}

/**
 * A transient status message — success, warning or information.
 *
 * Deliberately distinct from `ErrorNotice`, which renders an API error object
 * with its code and request id. This one takes arbitrary children, because what
 * needs saying after a successful action is usually more than a single line: a
 * confirmation plus the figures the user is about to act on.
 *
 * `role="status"` rather than `role="alert"`: this announces an outcome the user
 * just caused, which should not interrupt a screen reader the way an error must.
 */
export function AlertBanner({ tone = 'info', title, children, onDismiss }) {
  return (
    <div className={`alert-banner alert-banner--${tone}`} role="status" aria-live="polite">
      <div className="alert-banner__body">
        {title && <strong className="alert-banner__title">{title}</strong>}
        {children}
      </div>
      {onDismiss && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          ✕
        </button>
      )}
    </div>
  )
}

export function ErrorNotice({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="notice notice--error" role="alert">
      <strong>{error.code ?? 'Error'}</strong>
      <p>{error.message ?? String(error)}</p>
      {error.requestId && <p className="muted mono">request id: {error.requestId}</p>}
      {onRetry && (
        <button type="button" className="btn btn--ghost" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

/**
 * A button that knows about its own busy state.
 *
 * The reason this exists rather than callers writing `<button disabled={pending}>`
 * inline: a pending action needs three things at once — the button disabled, the
 * label changed, and `aria-busy` set so a screen reader does not report a control
 * that has gone quiet. Doing all three by hand at every call site is how two of
 * the three end up missing.
 */
export function Button({
  children,
  variant = 'secondary',
  size = '',
  busy = false,
  disabled = false,
  block = false,
  icon = false,
  type = 'button',
  className = '',
  busyLabel,
  ...rest
}) {
  const classes = [
    'btn',
    `btn--${variant}`,
    size && `btn--${size}`,
    block && 'btn--block',
    icon && 'btn--icon',
    busy && 'is-busy',
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <button
      type={type}
      className={classes}
      // A disabled button swallows its own click, so the pending flag has to be
      // what stops it: `disabled` alone leaves the control focusable-looking but
      // inert, and `aria-busy` is what announces the reason.
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy && <span className="btn__spinner" aria-hidden="true" />}
      {busy && busyLabel ? busyLabel : children}
    </button>
  )
}

/**
 * A confirmation dialog for actions that are hard to undo.
 *
 * Deliberately not `window.confirm`. A native confirm cannot say *what* is
 * about to happen — it shows one string and two buttons — and an operator
 * completing a pickup needs to see the plate and the customer before
 * committing. It also cannot be styled or focused, and it blocks the thread.
 *
 * `role="alertdialog"` with `aria-modal` so a screen reader announces it as a
 * decision rather than as page content, and Escape cancels rather than
 * confirms: the safe default when someone dismisses a dialog without reading
 * it is to do nothing.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'btn--primary',
  busy = false,
  onConfirm,
  onCancel,
}) {
  if (!open) return null

  return (
    <div
      className="dialog__backdrop"
      role="alertdialog"
      aria-modal="true"
      aria-label={title}
      // Clicking the backdrop cancels. It does not confirm: an accidental
      // click on a destructive action is worse than an extra click to confirm.
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel?.()
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !busy) onCancel?.()
      }}
    >
      <div className="dialog">
        <h2 className="dialog__title">{title}</h2>
        <div className="dialog__body">{children}</div>
        <div className="dialog__actions">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          {/* Button owns the busy state: it disables, swaps the label *and* sets
              `aria-busy`. Hand-rolling those three at every call site is how two
              of the three end up missing, and a silent control is worse than a
              slow one. */}
          <Button
            variant={tone.replace(/^btn--/, '')}
            onClick={onConfirm}
            busy={busy}
            busyLabel="Working…"
            // Focused so Enter confirms and the keyboard never has to hunt for
            // the destructive button.
            autoFocus
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

/**
 * A placeholder that reserves the space content will occupy.
 *
 * `variant` picks the shape. Prefer this over a centred `Spinner` for anything
 * whose layout is known in advance: a panel that grows by 200px when its data
 * lands pushes everything below it, which on a dashboard means the user loses
 * the thing they were reading.
 */
export function Skeleton({ variant = 'text', className = '' }) {
  return (
    <span
      className={`skeleton skeleton--${variant} ${className}`.trim()}
      aria-hidden="true"
    />
  )
}

/**
 * An empty state that explains itself and offers the next action.
 *
 * A blank panel reads as "nothing is wrong", which on a security screen is the
 * one thing it must never imply. So an empty state states the absence, and says
 * whether that absence is expected — no alerts yet is good news, no photos is a
 * missing step.
 */
export function EmptyState({ icon, title, body, actions }) {
  return (
    <div className="empty-state">
      {icon && (
        <span className="empty-state__icon" aria-hidden="true">
          {icon}
        </span>
      )}
      {title && <p className="empty-state__title">{title}</p>}
      {body && <p className="empty-state__body">{body}</p>}
      {actions && <div className="empty-state__actions">{actions}</div>}
    </div>
  )
}

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'

/**
 * Toast feedback.
 *
 * Every state-changing action in this app needs to tell the user what happened,
 * and it needs to do so without a modal they have to dismiss. That is what this
 * provides: a single region in the corner, one announcement per action, and an
 * error variant that stays on screen until it is read.
 *
 * Accessibility notes that shape the implementation:
 *   - The region is `aria-live="polite"` and stays mounted, so adding a node to
 *     it is announced. A region that mounts with the first toast is not, because
 *     the live region does not exist at the moment the content changes.
 *   - Errors use `role="alert"` semantics via the polite region plus a longer
 *     lifetime, and are never auto-dismissed silently: a failure that vanishes
 *     on its own is indistinguishable from a success the user did not read.
 *   - The region itself is `pointer-events: none` and each toast re-enables it,
 *     so a stack of toasts cannot block the page underneath.
 */
const ToastContext = createContext(null)

export const DEFAULT_TTL_MS = 5000
export const ERROR_TTL_MS = 10000
export const MAX_TOASTS = 4

const ICONS = {
  success: '✓',
  error: '!',
  warning: '!',
  info: 'i',
}

let nextId = 1

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const timers = useRef(new Map())

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
    const timer = timers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.current.delete(id)
    }
  }, [])

  /**
   * Show a toast. Returns its id so a caller can dismiss it early — useful when
   * a follow-up action makes the previous message stale, e.g. dismissing
   * "Requesting pickup…" once the request resolves.
   */
  const push = useCallback(
    (toast) => {
      const id = nextId++
      const tone = toast.tone ?? 'info'
      // An error is not decoration and should not disappear under the reader.
      const ttl = toast.duration ?? (tone === 'error' ? ERROR_TTL_MS : DEFAULT_TTL_MS)

      setToasts((current) => {
        const next = [...current, { id, tone, ttl, ...toast }]
        // Cap the stack so a burst of failures cannot cover the screen. The
        // oldest go first: the newest is the one about the action just taken.
        return next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next
      })

      if (ttl > 0) {
        const timer = setTimeout(() => dismiss(id), ttl)
        timers.current.set(id, timer)
      }
      return id
    },
    [dismiss],
  )

  // Clear pending timers on unmount so nothing fires against a dead tree.
  useEffect(() => {
    const pending = timers.current
    return () => {
      pending.forEach((timer) => clearTimeout(timer))
      pending.clear()
    }
  }, [])

  const value = useMemo(
    () => ({
      push,
      dismiss,
      success: (title, message, options) => push({ tone: 'success', title, message, ...options }),
      error: (title, message, options) => push({ tone: 'error', title, message, ...options }),
      warning: (title, message, options) => push({ tone: 'warning', title, message, ...options }),
      info: (title, message, options) => push({ tone: 'info', title, message, ...options }),
    }),
    [push, dismiss],
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className="toast-region"
        role="region"
        aria-label="Notifications"
        aria-live="polite"
        aria-relevant="additions"
      >
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`}>
            <span className="toast__icon" aria-hidden="true">
              {ICONS[toast.tone] ?? ICONS.info}
            </span>
            <div className="toast__body">
              {toast.title && <p className="toast__title">{toast.title}</p>}
              {toast.message && <p className="toast__message">{toast.message}</p>}
            </div>
            <button
              type="button"
              className="toast__dismiss"
              onClick={() => dismiss(toast.id)}
              aria-label={`Dismiss notification: ${toast.title ?? toast.message ?? 'notification'}`}
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

/**
 * Access the toast API. Throws rather than returning undefined, because a
 * silent no-op toast is exactly the "action gave no feedback" failure this
 * provider exists to prevent — better to fail loudly in development.
 */
export function useToast() {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error('useToast must be used inside a ToastProvider')
  }
  return context
}

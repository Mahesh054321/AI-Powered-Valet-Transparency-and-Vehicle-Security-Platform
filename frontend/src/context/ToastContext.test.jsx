import { describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  DEFAULT_TTL_MS,
  ERROR_TTL_MS,
  MAX_TOASTS,
  ToastProvider,
  useToast,
} from './ToastContext'

/**
 * A component that drives the toast API from the inside.
 *
 * The lifetimes are short on purpose. The provider's real defaults are seconds,
 * and asserting those with fake timers deadlocks against userEvent's own
 * timer-driven event loop; driving the actual `setTimeout` with a small
 * `duration` tests the same mechanism without the deadlock.
 */
function Trigger({ onReady }) {
  const toast = useToast()
  onReady?.(toast)
  return (
    <div>
      <button
        type="button"
        onClick={() => toast.success('Saved', 'Your changes are stored.', { duration: 60 })}
      >
        succeed
      </button>
      <button
        type="button"
        onClick={() => toast.error('Upload failed', 'The file was too large.', { duration: 200 })}
      >
        fail
      </button>
      <button type="button" onClick={() => toast.info('Hint', 'Try a different angle.')}>
        inform
      </button>
    </div>
  )
}

/** Render the provider and hand back the toast API it exposed. */
function renderProvider() {
  const handle = {}
  const view = render(
    <ToastProvider>
      <Trigger onReady={(api) => (handle.current = api)} />
    </ToastProvider>,
  )
  return { ...view, toast: () => handle.current }
}

describe('ToastProvider', () => {
  it('renders a live region before any toast exists', () => {
    // The region must exist up front. A live region that mounts together with
    // its first message is not announced, because it does not exist at the
    // moment the content changes.
    renderProvider()

    const region = screen.getByRole('region', { name: /notifications/i })
    expect(region).toBeInTheDocument()
    expect(region).toHaveAttribute('aria-live', 'polite')
  })

  it('shows a success toast with its title and message', async () => {
    const user = userEvent.setup()
    renderProvider()

    await user.click(screen.getByRole('button', { name: /succeed/ }))

    expect(screen.getByText('Saved')).toBeInTheDocument()
    expect(screen.getByText('Your changes are stored.')).toBeInTheDocument()
    expect(document.querySelector('.toast--success')).toBeInTheDocument()
  })

  it('shows an error toast', async () => {
    const user = userEvent.setup()
    renderProvider()

    await user.click(screen.getByRole('button', { name: /fail/ }))

    expect(screen.getByText('Upload failed')).toBeInTheDocument()
    expect(document.querySelector('.toast--error')).toBeInTheDocument()
  })

  it('shows an info toast', async () => {
    const user = userEvent.setup()
    renderProvider()

    await user.click(screen.getByRole('button', { name: /inform/ }))

    expect(screen.getByText('Hint')).toBeInTheDocument()
    expect(document.querySelector('.toast--info')).toBeInTheDocument()
  })

  it('auto-dismisses once its lifetime elapses', async () => {
    const user = userEvent.setup()
    renderProvider()

    await user.click(screen.getByRole('button', { name: /succeed/ }))
    expect(screen.getByText('Saved')).toBeInTheDocument()

    await waitFor(() => expect(screen.queryByText('Saved')).not.toBeInTheDocument(), {
      timeout: 2000,
    })
  })

  it('never auto-dismisses a toast given duration 0', async () => {
    // `duration: 0` means "the user closes this". Without that escape hatch an
    // important failure would vanish while still being read.
    const { toast } = renderProvider()

    act(() => toast().push({ tone: 'error', title: 'Persistent', duration: 0 }))

    expect(screen.getByText('Persistent')).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 150))
    expect(screen.getByText('Persistent')).toBeInTheDocument()
  })

  it('gives an error a longer lifetime than a routine message', () => {
    // Asserted on the constants rather than by waiting: a failure that vanishes
    // on the same clock as a confirmation is easy to miss, and a missed failure
    // is indistinguishable from a success the user did not read.
    expect(ERROR_TTL_MS).toBeGreaterThan(DEFAULT_TTL_MS)
  })

  it('dismisses on demand', async () => {
    const user = userEvent.setup()
    renderProvider()

    await user.click(screen.getByRole('button', { name: /succeed/ }))
    await user.click(screen.getByRole('button', { name: /dismiss notification/i }))

    expect(screen.queryByText('Saved')).not.toBeInTheDocument()
  })

  it('names the notification it dismisses for a screen reader', async () => {
    const user = userEvent.setup()
    renderProvider()

    await user.click(screen.getByRole('button', { name: /fail/ }))

    expect(
      screen.getByRole('button', { name: /dismiss notification: upload failed/i }),
    ).toBeInTheDocument()
  })

  it('caps the stack so a burst of failures cannot cover the screen', () => {
    const { toast } = renderProvider()

    act(() => {
      for (let i = 0; i < MAX_TOASTS + 3; i += 1)
        toast().info(`Message ${i}`, null, { duration: 0 })
    })

    const shown = document.querySelectorAll('.toast')
    expect(shown.length).toBe(MAX_TOASTS)
    // The newest survive; the oldest are dropped, because the newest is the one
    // about the action just taken.
    expect(screen.getByText(`Message ${MAX_TOASTS + 2}`)).toBeInTheDocument()
    expect(screen.queryByText('Message 0')).not.toBeInTheDocument()
  })

  it('throws rather than silently doing nothing outside a provider', () => {
    // A no-op toast is the exact failure this module exists to prevent, so
    // misuse should be loud in development instead of quietly swallowing the
    // user's feedback.
    function Orphan() {
      useToast()
      return null
    }
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Orphan />)).toThrow(/inside a ToastProvider/)
    spy.mockRestore()
  })
})

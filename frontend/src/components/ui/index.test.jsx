/**
 * The design-system primitives.
 *
 * Every page renders through these, so their contracts are load-bearing even
 * though each one is small. The ones worth pinning are the ones where a silent
 * change would degrade accessibility or hide information:
 *
 * - **`ErrorNotice` renders the error code and the request id.** The request id
 *   is the only way to tie a user's complaint to a line in the server log, so
 *   dropping it would make a bug report unactionable.
 * - **`ConfirmDialog` uses `role="alertdialog"`, not `role="dialog"`,** and both
 *   the backdrop click and Escape *cancel*. An accidental click on a destructive
 *   action is worse than an extra click to confirm.
 * - **`Button` busy state does three things at once** — disables, swaps the
 *   label, and sets `aria-busy`. A control that has gone quiet without saying so
 *   is what this prevents.
 * - **`AlertBanner` uses `role="status"`, not `role="alert"`.** It reports an
 *   outcome the user just caused, which should not interrupt a screen reader the
 *   way an error must.
 * - **`Skeleton` is `aria-hidden`.** It reserves layout space; it is not content
 *   and must not be announced.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  AlertBanner,
  Badge,
  Button,
  ConfirmDialog,
  DataRow,
  EmptyState,
  ErrorNotice,
  Field,
  Panel,
  Skeleton,
  Spinner,
} from './index'

describe('Badge', () => {
  it('applies the tone it is given', () => {
    render(<Badge tone="badge--danger">BREACH</Badge>)
    expect(screen.getByText('BREACH')).toHaveClass('badge--danger')
  })

  it('falls back to muted when no tone is supplied', () => {
    render(<Badge>UNKNOWN</Badge>)
    expect(screen.getByText('UNKNOWN')).toHaveClass('badge--muted')
  })
})

describe('Panel', () => {
  it('renders title and subtitle together', () => {
    render(<Panel title="Custody" subtitle="3 sessions">body</Panel>)
    expect(screen.getByRole('heading', { name: 'Custody' })).toBeInTheDocument()
    expect(screen.getByText('3 sessions')).toBeInTheDocument()
  })

  it('renders a subtitle on its own, with no title', () => {
    // Both list pages pass a record count as a subtitle with no title. Gating the
    // header on `title || actions` alone silently discarded the count.
    render(<Panel subtitle="23 ticket(s)">body</Panel>)
    expect(screen.getByText('23 ticket(s)')).toBeInTheDocument()
  })

  it('renders no header at all when there is nothing to put in it', () => {
    const { container } = render(<Panel>body</Panel>)
    expect(container.querySelector('.panel__header')).toBeNull()
    expect(screen.getByText('body')).toBeInTheDocument()
  })

  it('renders actions', () => {
    render(<Panel title="T" actions={<button type="button">Do</button>}>b</Panel>)
    expect(screen.getByRole('button', { name: 'Do' })).toBeInTheDocument()
  })
})

describe('Field', () => {
  it('wraps the control in its label so it is programmatically associated', () => {
    render(
      <Field label="Registration number">
        <input />
      </Field>,
    )
    expect(screen.getByLabelText('Registration number')).toBeInTheDocument()
  })

  it('shows the hint when there is no error', () => {
    render(
      <Field label="Year" hint="Optional">
        <input />
      </Field>,
    )
    expect(screen.getByText('Optional')).toBeInTheDocument()
  })

  it('replaces the hint with the error, rather than showing both', () => {
    // Showing both would put the reassuring hint next to the complaint.
    render(
      <Field label="Year" hint="Optional" error="Enter a year between 1950 and 2100.">
        <input />
      </Field>,
    )
    expect(screen.getByText(/enter a year between/i)).toBeInTheDocument()
    expect(screen.queryByText('Optional')).toBeNull()
  })
})

describe('DataRow', () => {
  it('renders label and value', () => {
    render(<DataRow label="Make" value="Tata" />)
    expect(screen.getByText('Make')).toBeInTheDocument()
    expect(screen.getByText('Tata')).toBeInTheDocument()
  })

  it('renders an em dash for a missing value', () => {
    render(<DataRow label="Notes" value={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})

describe('Spinner', () => {
  it('is a polite live region with a default label', () => {
    render(<Spinner />)
    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-live', 'polite')
    expect(within(status).getByText('Loading…')).toBeInTheDocument()
  })

  it('uses the label it is given', () => {
    render(<Spinner label="Loading vehicles…" />)
    expect(screen.getByText('Loading vehicles…')).toBeInTheDocument()
  })
})

describe('AlertBanner', () => {
  it('announces as a status, not an alert', () => {
    // role="alert" would interrupt a screen reader; this reports an outcome the
    // user just caused, so it must be polite.
    render(<AlertBanner title="Saved">Your changes are stored.</AlertBanner>)
    const banner = screen.getByRole('status')
    expect(banner).toHaveAttribute('aria-live', 'polite')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('applies the tone', () => {
    const { container } = render(<AlertBanner tone="success" title="Done">x</AlertBanner>)
    expect(container.querySelector('.alert-banner--success')).toBeInTheDocument()
  })

  it('offers a dismiss control only when a handler is supplied', () => {
    const onDismiss = vi.fn()
    const { rerender } = render(<AlertBanner title="T">x</AlertBanner>)
    expect(screen.queryByRole('button', { name: /dismiss/i })).toBeNull()

    rerender(
      <AlertBanner title="T" onDismiss={onDismiss}>
        x
      </AlertBanner>,
    )
    screen.getByRole('button', { name: /dismiss/i }).click()
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})

describe('ErrorNotice', () => {
  it('renders nothing when there is no error', () => {
    const { container } = render(<ErrorNotice error={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('is an assertive alert carrying the code and the message', () => {
    render(
      <ErrorNotice
        error={{ code: 'not_found', message: 'No such vehicle' }}
      />,
    )
    const alert = screen.getByRole('alert')
    expect(within(alert).getByText('not_found')).toBeInTheDocument()
    expect(within(alert).getByText('No such vehicle')).toBeInTheDocument()
  })

  it('shows the request id, which is what ties a complaint to a server log line', () => {
    render(
      <ErrorNotice
        error={{ code: 'conflict', message: 'Duplicate', requestId: 'req-abc-123' }}
      />,
    )
    expect(screen.getByText(/request id: req-abc-123/)).toBeInTheDocument()
  })

  it('omits the request id line when there is none', () => {
    render(<ErrorNotice error={{ code: 'x', message: 'y' }} />)
    expect(screen.queryByText(/request id/i)).toBeNull()
  })

  it('offers Try again only when a retry handler is supplied', () => {
    const onRetry = vi.fn()
    const { rerender } = render(<ErrorNotice error={{ message: 'boom' }} />)
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull()

    rerender(<ErrorNotice error={{ message: 'boom' }} onRetry={onRetry} />)
    screen.getByRole('button', { name: /try again/i }).click()
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

describe('Button', () => {
  it('disables, relabels and sets aria-busy together when busy', async () => {
    // All three at once is the point: a disabled control with no explanation
    // reads as broken rather than as working.
    render(
      <Button busy busyLabel="Saving…">
        Save
      </Button>,
    )
    const button = screen.getByRole('button', { name: /saving/i })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(screen.queryByText('Save')).toBeNull()
  })

  it('does not set aria-busy when idle', () => {
    render(<Button>Save</Button>)
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toBeEnabled()
    expect(button).not.toHaveAttribute('aria-busy')
  })

  it('stays disabled when disabled is set even if not busy', () => {
    render(<Button disabled>Save</Button>)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('applies the variant and block modifiers', () => {
    render(
      <Button variant="danger" block>
        Archive
      </Button>,
    )
    const button = screen.getByRole('button', { name: 'Archive' })
    expect(button).toHaveClass('btn--danger')
    expect(button).toHaveClass('btn--block')
  })

  it('defaults to type="button" so it cannot submit a form by accident', () => {
    render(<Button>Go</Button>)
    expect(screen.getByRole('button', { name: 'Go' })).toHaveAttribute('type', 'button')
  })
})

describe('ConfirmDialog', () => {
  it('renders nothing when closed', () => {
    render(<ConfirmDialog open={false} title="Delete?" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('is an alertdialog, not a plain dialog', () => {
    // An operator needs this announced as a decision, not as page content.
    render(<ConfirmDialog open title="Close this alert?" onConfirm={vi.fn()} onCancel={vi.fn()} />)
    const dialog = screen.getByRole('alertdialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleName('Close this alert?')
  })

  it('shows what is being confirmed, which window.confirm cannot do', () => {
    render(
      <ConfirmDialog open title="Close this alert?" onConfirm={vi.fn()} onCancel={vi.fn()}>
        UP85CA1234 — geofence breach
      </ConfirmDialog>,
    )
    expect(screen.getByText(/UP85CA1234/)).toBeInTheDocument()
  })

  it('focuses the confirm button so Enter cannot hunt for it', () => {
    render(
      <ConfirmDialog open title="Confirm?" confirmLabel="Resolve" onConfirm={vi.fn()} onCancel={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: 'Resolve' })).toHaveFocus()
  })

  it('cancels on Escape rather than confirming', async () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(<ConfirmDialog open title="Archive vehicle?" onConfirm={onConfirm} onCancel={onCancel} />)

    await user.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('cancels on a backdrop click rather than confirming', async () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(<ConfirmDialog open title="Archive vehicle?" onConfirm={onConfirm} onCancel={onCancel} />)

    await user.click(screen.getByRole('alertdialog'))
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('ignores Escape and backdrop clicks while busy, so a write cannot be abandoned', async () => {
    const onCancel = vi.fn()
    const user = userEvent.setup()
    render(<ConfirmDialog open busy title="Archive vehicle?" onConfirm={vi.fn()} onCancel={onCancel} />)

    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('alertdialog'))
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('reports its busy state on the confirm button', () => {
    render(
      <ConfirmDialog open busy title="Archive vehicle?" onConfirm={vi.fn()} onCancel={vi.fn()} />,
    )
    const confirm = screen.getByRole('button', { name: /working/i })
    expect(confirm).toBeDisabled()
    expect(confirm).toHaveAttribute('aria-busy', 'true')
  })
})

describe('Skeleton', () => {
  it('is hidden from assistive technology', () => {
    // It reserves layout space; it is not content.
    const { container } = render(<Skeleton variant="card" />)
    expect(container.firstChild).toHaveAttribute('aria-hidden', 'true')
  })

  it('applies the variant', () => {
    const { container } = render(<Skeleton variant="card" />)
    expect(container.firstChild).toHaveClass('skeleton--card')
  })
})

describe('EmptyState', () => {
  it('states the absence and explains it, rather than leaving a blank panel', () => {
    // On a security screen a blank panel reads as "nothing is wrong", which is
    // the one thing it must never imply.
    render(<EmptyState icon="🔔" title="No open alerts" body="Nothing needs attention." />)
    expect(screen.getByText('No open alerts')).toBeInTheDocument()
    expect(screen.getByText('Nothing needs attention.')).toBeInTheDocument()
  })

  it('hides the icon from assistive technology', () => {
    const { container } = render(<EmptyState icon="🔔" title="T" />)
    expect(container.querySelector('.empty-state__icon')).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders actions when supplied', () => {
    render(<EmptyState title="No vehicles" actions={<button type="button">Add one</button>} />)
    expect(screen.getByRole('button', { name: 'Add one' })).toBeInTheDocument()
  })
})

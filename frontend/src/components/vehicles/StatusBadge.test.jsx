/**
 * The shared status badge.
 *
 * One component decides status colour across the whole app, so a status is
 * coloured the same way on a ticket, a vehicle and a custody timeline. The
 * behaviour worth pinning is the fallback: a status the map has never been told
 * about gets a muted badge and shows the raw key. Guessing a colour for an
 * unknown status would be worse than admitting it is not recognised - a red
 * badge on a status that turns out to be benign teaches the operator to ignore
 * red.
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusBadge } from './StatusBadge'

describe('StatusBadge', () => {
  it('shows the raw key when no label map is supplied', () => {
    render(<StatusBadge status="PARKED" />)
    expect(screen.getByText('PARKED')).toBeInTheDocument()
  })

  it('uses the supplied label map for display', () => {
    render(<StatusBadge status="PARKED" labels={{ PARKED: 'Parked in yard' }} />)
    expect(screen.getByText('Parked in yard')).toBeInTheDocument()
  })

  it('falls back to the raw key when the label map has no entry', () => {
    render(<StatusBadge status="PARKED" labels={{ IN_TRANSIT: 'Moving' }} />)
    expect(screen.getByText('PARKED')).toBeInTheDocument()
  })

  it('colours a known custody status as informational', () => {
    render(<StatusBadge status="PARKED" />)
    expect(screen.getByText('PARKED')).toHaveClass('badge--info')
  })

  it('colours a warning-worthy custody status as a warning', () => {
    render(<StatusBadge status="RETRIEVAL_REQUESTED" />)
    expect(screen.getByText('RETRIEVAL_REQUESTED')).toHaveClass('badge--warning')
  })

  it('colours a completed custody status as success', () => {
    render(<StatusBadge status="CHECKED_OUT" />)
    expect(screen.getByText('CHECKED_OUT')).toHaveClass('badge--success')
  })

  it('colours a disputed custody status as danger', () => {
    render(<StatusBadge status="DISPUTED" />)
    expect(screen.getByText('DISPUTED')).toHaveClass('badge--danger')
  })

  it('renders a muted badge and the raw key for an unrecognised status', () => {
    // A status the backend adds tomorrow must degrade honestly rather than
    // borrow a colour that implies a meaning nobody assigned.
    render(<StatusBadge status="SOMETHING_NEW" />)
    const badge = screen.getByText('SOMETHING_NEW')
    expect(badge).toHaveClass('badge--muted')
    expect(badge).not.toHaveClass('badge--danger')
  })

  it('renders an em dash when there is no status at all', () => {
    // Distinct from "unknown status": an absent value is not a state.
    const { container } = render(<StatusBadge status={null} />)
    expect(container.querySelector('.badge--muted')).toHaveTextContent('—')
  })

  it('treats the security override ALERT as danger even though it is not a custody state', () => {
    render(<StatusBadge status="ALERT" />)
    expect(screen.getByText('ALERT')).toHaveClass('badge--danger')
  })
})

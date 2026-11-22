import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AlertCard } from './AlertCard'

function alert(overrides = {}) {
  return {
    id: 'a-1',
    alert_type: 'GEOFENCE_BREACH',
    severity: 'HIGH',
    status: 'OPEN',
    title: 'Vehicle outside its assigned parking zone',
    reason: 'Position is 400 m beyond the zone boundary, outside the 25 m GPS tolerance.',
    confidence: 0.95,
    occurred_at: '2026-09-08T10:05:00+00:00',
    acknowledged_at: null,
    acknowledged_by_email: null,
    resolved_at: null,
    resolved_by_email: null,
    resolution_notes: null,
    evidence: { distance_from_boundary_metres: 400, buffer_metres: 25 },
    ...overrides,
  }
}

describe('AlertCard', () => {
  it('shows the engine\'s own reason verbatim rather than a paraphrase', () => {
    render(<AlertCard alert={alert()} />)

    expect(
      screen.getByText(
        'Position is 400 m beyond the zone boundary, outside the 25 m GPS tolerance.',
      ),
    ).toBeInTheDocument()
  })

  it('labels the alert type and severity in words a manager can read', () => {
    render(<AlertCard alert={alert()} />)

    expect(screen.getByText(/^Geofence breach · 9\/8\/2026,/)).toBeInTheDocument()
    expect(screen.getByText('HIGH')).toBeInTheDocument()
    expect(screen.getByText('Open')).toBeInTheDocument()
  })

  it('renders confidence as a percentage', () => {
    render(<AlertCard alert={alert()} />)

    expect(screen.getByText('95%')).toBeInTheDocument()
  })

  it('shows readable evidence details instead of raw JSON after disclosure', () => {
    render(<AlertCard alert={alert()} />)

    expect(screen.queryByText(/distance_from_boundary_metres/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Show the inputs behind this/i }))

    expect(screen.getByText('Distance from boundary metres')).toBeInTheDocument()
    expect(screen.getByText('400')).toBeInTheDocument()
    expect(screen.queryByText(/distance_from_boundary_metres/)).not.toBeInTheDocument()
  })

  it('keeps the map available on a resolved alert from a completed custody session', () => {
    render(
      <MemoryRouter>
        <AlertCard
          alert={alert({
            status: 'RESOLVED',
            custody_session_id: 'completed-custody',
          })}
        />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'View on Map' })).toHaveAttribute(
      'href',
      '/tracking/completed-custody',
    )
  })

  it('offers no triage controls to a role that cannot triage', () => {
    render(<AlertCard alert={alert()} canTriage={false} />)

    expect(screen.queryByRole('button', { name: /Acknowledge/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Resolve/i })).not.toBeInTheDocument()
  })

  it('offers acknowledge, resolve and dismiss to a manager', () => {
    render(<AlertCard alert={alert()} canTriage />)

    expect(screen.getByRole('button', { name: /Acknowledge/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Resolve/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Dismiss as false positive/i })).toBeInTheDocument()
  })

  it('calls onAcknowledge with the alert', () => {
    const onAcknowledge = vi.fn()
    const record = alert()
    render(<AlertCard alert={record} canTriage onAcknowledge={onAcknowledge} />)

    fireEvent.click(screen.getByRole('button', { name: /Acknowledge/i }))

    expect(onAcknowledge).toHaveBeenCalledWith(record)
  })

  it('asks for notes before resolving, and passes them through', () => {
    const onResolve = vi.fn()
    const record = alert()
    render(<AlertCard alert={record} canTriage onResolve={onResolve} />)

    fireEvent.click(screen.getByRole('button', { name: /^Resolve/i }))
    fireEvent.change(screen.getByLabelText('Resolution notes'), {
      target: { value: 'Valet relocated the vehicle.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(onResolve).toHaveBeenCalledWith(record, {
      notes: 'Valet relocated the vehicle.',
      dismiss: false,
    })
  })

  it('sends an empty note as undefined rather than an empty string', () => {
    const onResolve = vi.fn()
    render(<AlertCard alert={alert()} canTriage onResolve={onResolve} />)

    fireEvent.click(screen.getByRole('button', { name: /^Resolve/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(onResolve).toHaveBeenCalledWith(expect.anything(), {
      notes: undefined,
      dismiss: false,
    })
  })

  it('dismisses without asking for notes, flagging it as a false positive', () => {
    const onResolve = vi.fn()
    const record = alert()
    render(<AlertCard alert={record} canTriage onResolve={onResolve} />)

    fireEvent.click(screen.getByRole('button', { name: /Dismiss as false positive/i }))

    expect(onResolve).toHaveBeenCalledWith(record, { dismiss: true })
  })

  it('offers no triage controls once an alert is closed', () => {
    render(
      <AlertCard
        alert={alert({
          status: 'RESOLVED',
          resolved_at: '2026-09-08T10:20:00+00:00',
          resolved_by_email: 'manager.karan@example.com',
          resolution_notes: 'Valet relocated the vehicle.',
        })}
        canTriage
      />,
    )

    expect(screen.queryByRole('button', { name: /Acknowledge/i })).not.toBeInTheDocument()
    expect(screen.getByText('Valet relocated the vehicle.')).toBeInTheDocument()
    // Two occurrences by design: the status badge, and the attribution label
    // naming who closed it.
    expect(screen.getAllByText('Resolved')).toHaveLength(2)
  })

  it('shows who dismissed a false positive, distinctly from a resolution', () => {
    render(
      <AlertCard
        alert={alert({
          status: 'DISMISSED',
          resolved_at: '2026-09-08T10:20:00+00:00',
          resolved_by_email: 'admin@valet.local',
        })}
        canTriage
      />,
    )

    // The label says "Dismissed", not "Resolved": a false positive is feedback
    // about the detector, and reading it as a resolved incident would hide that.
    expect(screen.getAllByText('Dismissed')).toHaveLength(2)
    expect(screen.getByText(/^admin@valet\.local · 9\/8\/2026,/)).toBeInTheDocument()
  })

  it('disables the buttons while a request is in flight', () => {
    render(<AlertCard alert={alert()} canTriage busy />)

    expect(screen.getByRole('button', { name: /^Resolve/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Dismiss as false positive/i })).toBeDisabled()
  })

  it('reports a missing time rather than rendering "Invalid Date"', () => {
    render(<AlertCard alert={alert({ occurred_at: null })} />)

    expect(screen.getByText(/unknown time/)).toBeInTheDocument()
  })
})

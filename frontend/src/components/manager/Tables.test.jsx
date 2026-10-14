/**
 * The manager dashboard tables and filter bar.
 *
 * These are read-only, and that is a deliberate design decision worth pinning:
 * there is no action column, because changing a custody state belongs on the
 * operator dashboard behind the confirmation dialog and the state machine that
 * already owns that transition. A second place to mutate the same row would be a
 * second place to get it wrong.
 *
 * The other property that matters is honesty in the damage table: an unverified
 * finding is labelled *potential*, never damage. The detector flagged it; nobody
 * has confirmed it. Calling it damage would overstate what the algorithm
 * established, and this table is where a manager decides.
 */

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  ActiveVehiclesTable,
  AlertsTable,
  DamageTable,
  FilterBar,
  RecentTicketsTable,
} from './Tables'

/** The body rows, excluding the header row. */
function dataRows(container) {
  return container.querySelectorAll('.mgr-table__row:not(.mgr-table__row--head)')
}

describe('RecentTicketsTable', () => {
  const row = {
    ticket_id: 't1',
    ticket_code: 'TKT-0001',
    vehicle: { plate_number: 'UP 78 AB 1234' },
    customer: { name: 'Aisha Verma' },
    issued_at: '2026-09-11T09:05:00Z',
    zone_code: 'ZONE-A',
    custody_status: 'PARKED',
    is_open: true,
    duration_hours: 4,
  }

  it('renders one row per ticket with its vehicle, customer and zone', () => {
    const { container } = render(<RecentTicketsTable rows={[row]} />)

    expect(dataRows(container)).toHaveLength(1)
    expect(screen.getByText('TKT-0001')).toBeInTheDocument()
    expect(screen.getByText('UP 78 AB 1234')).toBeInTheDocument()
    expect(screen.getByText('Aisha Verma')).toBeInTheDocument()
    expect(screen.getByText('ZONE-A')).toBeInTheDocument()
  })

  it('renders the labelled custody status, not the raw enum', () => {
    render(<RecentTicketsTable rows={[row]} />)
    expect(screen.getByText('Parked')).toBeInTheDocument()
    expect(screen.queryByText('PARKED')).toBeNull()
  })

  it('shows an em dash for missing vehicle, customer, zone and duration', () => {
    const { container } = render(
      <RecentTicketsTable
        rows={[
          {
            ticket_id: 't2',
            ticket_code: 'TKT-0002',
            vehicle: null,
            customer: null,
            issued_at: null,
            zone_code: null,
            custody_status: 'PARKED',
            duration_hours: null,
          },
        ]}
      />,
    )

    // Vehicle, customer, issued, zone and duration are all absent.
    expect(within(dataRows(container)[0]).getAllByText('—')).toHaveLength(5)
  })

  it('explains an empty range rather than rendering a bare header', () => {
    render(<RecentTicketsTable rows={[]} />)
    expect(
      screen.getByText(/no tickets were issued in the selected range/i),
    ).toBeInTheDocument()
  })

  it('offers no action column, because this table is read-only', () => {
    const { container } = render(<RecentTicketsTable rows={[row]} />)
    expect(container.querySelectorAll('button')).toHaveLength(0)
  })
})

describe('ActiveVehiclesTable', () => {
  const row = {
    custody_session_id: 's1',
    vehicle: { plate_number: 'UP 78 AB 1234', make: 'Maruti', model: 'Swift' },
    customer: { name: 'Aisha Verma' },
    zone_code: 'ZONE-A',
    custody_status: 'PARKED',
    hours_in_custody: 3,
    open_alerts: 0,
  }

  it('renders the vehicle with its make and model', () => {
    render(<ActiveVehiclesTable rows={[row]} />)
    expect(screen.getByText('UP 78 AB 1234')).toBeInTheDocument()
    expect(screen.getByText('Maruti Swift')).toBeInTheDocument()
  })

  it('flags a row that has open alerts', () => {
    const { container } = render(
      <ActiveVehiclesTable rows={[{ ...row, open_alerts: 2 }]} />,
    )
    expect(dataRows(container)[0]).toHaveClass('mgr-table__row--alert')
    expect(screen.getByText('2 open')).toBeInTheDocument()
  })

  it('says "none" rather than flagging a vehicle with no open alerts', () => {
    const { container } = render(<ActiveVehiclesTable rows={[row]} />)
    expect(dataRows(container)[0]).not.toHaveClass('mgr-table__row--alert')
    expect(screen.getByText('none')).toBeInTheDocument()
  })

  it('explains an empty yard', () => {
    render(<ActiveVehiclesTable rows={[]} />)
    expect(screen.getByText(/no vehicles are in custody right now/i)).toBeInTheDocument()
  })
})

describe('AlertsTable', () => {
  const row = {
    id: 'a1',
    severity: 'CRITICAL',
    alert_type: 'GEOFENCE_BREACH',
    reason: 'Vehicle left the yard',
    vehicle: { plate_number: 'UP 78 AB 1234' },
    occurred_at: '2026-09-11T09:30:00Z',
    status: 'OPEN',
    is_open: true,
    resolved_by: null,
    acknowledged_by: null,
  }

  it('renders the alert type with underscores as spaces', () => {
    render(<AlertsTable rows={[row]} />)
    expect(screen.getByText(/geofence breach/i)).toBeInTheDocument()
    expect(screen.getByText('Vehicle left the yard')).toBeInTheDocument()
  })

  it('tones a critical severity as danger', () => {
    render(<AlertsTable rows={[row]} />)
    expect(screen.getByText('CRITICAL')).toHaveClass('badge--danger')
  })

  it('prefers the resolver over the acknowledger, and falls back to a dash', () => {
    const { rerender } = render(<AlertsTable rows={[row]} />)
    expect(screen.getByText('—')).toBeInTheDocument()

    rerender(<AlertsTable rows={[{ ...row, acknowledged_by: 'Suresh Kumar' }]} />)
    expect(screen.getByText('Suresh Kumar')).toBeInTheDocument()

    rerender(
      <AlertsTable
        rows={[{ ...row, acknowledged_by: 'Suresh Kumar', resolved_by: 'Karan Malhotra' }]}
      />,
    )
    // The person who closed it is the more useful attribution.
    expect(screen.getByText('Karan Malhotra')).toBeInTheDocument()
    expect(screen.queryByText('Suresh Kumar')).toBeNull()
  })

  it('explains a quiet range', () => {
    render(<AlertsTable rows={[]} />)
    expect(screen.getByText(/no alerts in the selected range/i)).toBeInTheDocument()
  })
})

describe('DamageTable', () => {
  const row = {
    id: 'd1',
    damage_type: 'PAINT_CHIP',
    severity: 'MINOR',
    region: 'front_bumper',
    vehicle: { plate_number: 'UP 78 AB 1234' },
    confidence: 0.82,
    verification_status: 'UNVERIFIED',
    verified_by: null,
    captured_at: '2026-09-11T13:00:00Z',
  }

  it('labels an unverified finding as potential, not as damage', () => {
    // The detector flagged it; nobody has confirmed it. This table is where a
    // manager decides, so the wording must not decide for them.
    render(<DamageTable rows={[row]} />)
    expect(screen.getByText(/paint chip \(potential\)/i)).toBeInTheDocument()
  })

  it('drops the qualifier once a finding has been verified', () => {
    render(<DamageTable rows={[{ ...row, verification_status: 'CONFIRMED' }]} />)
    expect(screen.getByText(/paint chip/i)).toBeInTheDocument()
    expect(screen.queryByText(/\(potential\)/i)).toBeNull()
  })

  it('shows confidence as a percentage, never as a probability', () => {
    render(<DamageTable rows={[row]} />)
    expect(screen.getByText('82%')).toBeInTheDocument()
  })

  it('says "awaiting review" when nobody has looked at it yet', () => {
    render(<DamageTable rows={[row]} />)
    expect(screen.getByText('awaiting review')).toBeInTheDocument()
  })

  it('names the reviewer once verified', () => {
    render(
      <DamageTable
        rows={[{ ...row, verification_status: 'CONFIRMED', verified_by: 'Karan Malhotra' }]}
      />,
    )
    expect(screen.getByText('Karan Malhotra')).toBeInTheDocument()
  })

  it('humanises the region', () => {
    render(<DamageTable rows={[row]} />)
    expect(screen.getByText('front bumper')).toBeInTheDocument()
  })

  it('explains a clean range', () => {
    render(<DamageTable rows={[]} />)
    expect(
      screen.getByText(/no damage findings in the selected range/i),
    ).toBeInTheDocument()
  })
})

describe('FilterBar', () => {
  const filters = {
    dateFrom: '2026-08-12',
    dateTo: '2026-09-11',
    status: '',
    severity: '',
    vehicleId: '',
  }
  const options = {
    statuses: ['PARKED', 'READY_FOR_PICKUP'],
    severities: ['LOW', 'CRITICAL'],
    vehicles: [{ id: 'v1', label: 'UP 78 AB 1234 — Maruti Swift' }],
  }

  it('builds its options from the backend rather than a local constant', () => {
    // A hard-coded list could offer a status the API would reject, and a vehicle
    // list would drift from the vehicles that actually have sessions.
    render(
      <FilterBar
        filters={filters}
        options={options}
        onChange={vi.fn()}
        onReset={vi.fn()}
      />,
    )

    expect(screen.getByRole('option', { name: 'Parked' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Ready For Pickup' })).toBeInTheDocument()
    expect(
      screen.getByRole('option', { name: /UP 78 AB 1234 — Maruti Swift/ }),
    ).toBeInTheDocument()
  })

  it('always offers an unfiltered choice for each dropdown', () => {
    render(
      <FilterBar filters={filters} options={options} onChange={vi.fn()} onReset={vi.fn()} />,
    )
    expect(screen.getByRole('option', { name: 'All statuses' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'All severities' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'All vehicles' })).toBeInTheDocument()
  })

  it('reports a change as the full filter object, not just the one field', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(
      <FilterBar filters={filters} options={options} onChange={onChange} onReset={vi.fn()} />,
    )

    await user.selectOptions(screen.getByLabelText(/custody status/i), 'PARKED')

    expect(onChange).toHaveBeenCalledTimes(1)
    // The caller replaces its whole filter state, so the other keys must survive.
    expect(vi.mocked(onChange).mock.calls[0][0]).toEqual({
      ...filters,
      status: 'PARKED',
    })
  })

  it('reports a date change the same way', () => {
    const onChange = vi.fn()
    render(
      <FilterBar filters={filters} options={options} onChange={onChange} onReset={vi.fn()} />,
    )

    // Driven with a change event rather than typed: this is a type="date" input,
    // and jsdom will not accumulate individual keystrokes into a valid
    // YYYY-MM-DD, so every intermediate change would carry an empty value. What
    // is under test is that a change reports the whole filter object, not the
    // browser's date-segment editing.
    fireEvent.change(screen.getByLabelText(/^from$/i), {
      target: { value: '2026-09-01' },
    })

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(vi.mocked(onChange).mock.calls[0][0]).toEqual({ ...filters, dateFrom: '2026-09-01' })
  })

  it('resets through the callback', async () => {
    const onReset = vi.fn()
    const user = userEvent.setup()
    render(
      <FilterBar
        filters={filters}
        options={options}
        onChange={vi.fn()}
        onReset={onReset}
      />,
    )

    await user.click(screen.getByRole('button', { name: /^reset$/i }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('disables reset while a query is in flight', () => {
    render(
      <FilterBar
        filters={filters}
        options={options}
        onChange={vi.fn()}
        onReset={vi.fn()}
        busy
      />,
    )
    expect(screen.getByRole('button', { name: /^reset$/i })).toBeDisabled()
  })

  it('tolerates absent options rather than crashing', () => {
    render(
      <FilterBar filters={filters} options={undefined} onChange={vi.fn()} onReset={vi.fn()} />,
    )
    expect(screen.getByRole('option', { name: 'All statuses' })).toBeInTheDocument()
  })
})

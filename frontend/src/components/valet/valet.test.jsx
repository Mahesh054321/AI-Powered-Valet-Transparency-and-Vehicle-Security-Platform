import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SectionShell } from './SectionShell'
import { IncomingTable, VehicleTable } from './VehicleTable'
import { AlertList, InspectionTable, QueueList, ZoneStrip } from './Sections'

const LABELS = {
  IN_CUSTODY: 'In custody',
  MOVING: 'Moving',
  READY: 'Ready',
  ALERT: 'Alert',
  COMPLETED: 'Completed',
}

function row(overrides = {}) {
  return {
    custody_session_id: 'custody-1',
    ticket_id: 'ticket-1',
    custody_status: 'PARKED',
    display_status: 'IN_CUSTODY',
    vehicle: { plate_number: 'UP85CA1234', make: 'Maruti', model: 'Swift' },
    customer: { name: 'Aisha Verma', phone: '+91 90000 00001' },
    zone: { code: 'ZONE-A' },
    gps: null,
    gps_has_data: false,
    open_alert_count: 0,
    alarming: false,
    ...overrides,
  }
}

describe('SectionShell', () => {
  it('renders children when the section is ok', () => {
    render(
      <SectionShell section={{ state: 'ok' }} title="Active vehicles">
        <p>content</p>
      </SectionShell>
    )
    expect(screen.getByText('content')).toBeInTheDocument()
  })

  it('renders an empty section as an answer, in its own style', () => {
    const { container } = render(
      <SectionShell
        section={{ state: 'empty', reason: 'No vehicles are in custody.' }}
        title="Active vehicles"
      >
        <p>content</p>
      </SectionShell>
    )

    expect(screen.getByText('No vehicles are in custody.')).toBeInTheDocument()
    expect(screen.queryByText('content')).not.toBeInTheDocument()
    // Not styled as a fault: a quiet morning must not look like an outage.
    expect(container.querySelector('.section__empty')).toBeInTheDocument()
    expect(container.querySelector('.section__fault')).not.toBeInTheDocument()
  })

  it('renders a failed sub-query as a fault and says what failed', () => {
    const { container } = render(
      <SectionShell
        section={{ state: 'unavailable', reason: 'Zone occupancy could not be read.' }}
        title="Parking zones"
      >
        <p>content</p>
      </SectionShell>
    )

    expect(screen.getByText('Zone occupancy could not be read.')).toBeInTheDocument()
    expect(container.querySelector('.section__fault')).toBeInTheDocument()
  })

  it('does not render a blank box when a section has no state at all', () => {
    // Blank reads as "nothing wrong". Anything else is safer.
    const { container } = render(<SectionShell section={undefined} title="Active vehicles" />)

    expect(screen.getByText('Nothing to show.')).toBeInTheDocument()
    expect(container.querySelector('.section__empty')).toBeInTheDocument()
  })

  it('says so rather than going blank on a state it does not recognise', () => {
    // A future backend state must not silently render as an empty panel: the
    // operator would read "nothing to report" when the truth is "not handled".
    render(
      <SectionShell section={{ state: 'PARTIAL' }} title="Active vehicles">
        <p>content</p>
      </SectionShell>
    )

    expect(screen.getByText(/Unrecognised section state: PARTIAL/)).toBeInTheDocument()
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })
})

describe('VehicleTable', () => {
  it('shows plate, customer, zone and the underlying custody state', () => {
    render(<VehicleTable rows={[row()]} labels={LABELS} />)

    expect(screen.getByText('UP85CA1234')).toBeInTheDocument()
    expect(screen.getByText('Aisha Verma')).toBeInTheDocument()
    expect(screen.getByText('ZONE-A')).toBeInTheDocument()
    // The badge is a summary; the state it summarises stays visible so a wrong
    // summary can still be caught.
    expect(screen.getByText('PARKED')).toBeInTheDocument()
    expect(screen.getByText('In custody')).toBeInTheDocument()
  })

  it('distinguishes "no fix recorded" from a clean last position', () => {
    render(<VehicleTable rows={[row()]} labels={LABELS} />)
    expect(screen.getByText('No fix recorded')).toBeInTheDocument()
  })

  it('shows the movement verdict and the fix source when there is one', () => {
    render(
      <VehicleTable
        rows={[
          row({
            gps_has_data: true,
            gps: {
              movement_status: 'NORMAL',
              recorded_at: new Date(Date.now() - 3 * 60000).toISOString(),
              source: 'SIMULATED',
            },
          }),
        ]}
        labels={LABELS}
      />
    )

    expect(screen.getByText('NORMAL')).toBeInTheDocument()
    // A simulated track must never read as a hardware position.
    expect(screen.getByText(/simulated/)).toBeInTheDocument()
  })

  it('marks an alarming row and shows how many alerts are open', () => {
    const { container } = render(
      <VehicleTable rows={[row({ alarming: true, open_alert_count: 2 })]} labels={LABELS} />
    )

    expect(container.querySelector('.valet-table__row--alert')).toBeInTheDocument()
    expect(screen.getByText('2 open')).toBeInTheDocument()
  })

  it('invokes the action callback for the row it was clicked on', () => {
    const actionsFor = vi.fn((r) => (
      <button type="button" onClick={() => actionsFor.clicked(r.custody_session_id)}>
        act
      </button>
    ))
    actionsFor.clicked = vi.fn()

    render(<VehicleTable rows={[row()]} labels={LABELS} actionsFor={actionsFor} />)
    fireEvent.click(screen.getByRole('button', { name: 'act' }))

    expect(actionsFor.clicked).toHaveBeenCalledWith('custody-1')
  })

  it('prints the empty message rather than an empty table', () => {
    render(<VehicleTable rows={[]} labels={LABELS} emptyMessage="Nothing in custody." />)
    expect(screen.getByText('Nothing in custody.')).toBeInTheDocument()
  })
})

describe('IncomingTable', () => {
  it('lists arrivals by ticket, vehicle and customer', () => {
    render(
      <IncomingTable
        rows={[
          {
            ticket_id: 't1',
            ticket_code: 'VT-202609-ARRIVING',
            issued_at: new Date(Date.now() - 20 * 60000).toISOString(),
            vehicle: { plate_number: 'UP85XY9876', make: 'Hyundai', model: 'i20' },
            customer: { name: 'Rohit Shah' },
          },
        ]}
      />
    )

    expect(screen.getByText('VT-202609-ARRIVING')).toBeInTheDocument()
    expect(screen.getByText('UP85XY9876')).toBeInTheDocument()
    expect(screen.getByText('Rohit Shah')).toBeInTheDocument()
  })

  it('renders nothing when there are no arrivals', () => {
    const { container } = render(<IncomingTable rows={[]} />)
    expect(container.firstChild).toBeNull()
  })
})

describe('AlertList', () => {
  const alert = {
    id: 'alert-1',
    alert_type: 'GEOFENCE_BREACH',
    severity: 'HIGH',
    status: 'OPEN',
    title: 'Vehicle left its zone',
    reason: 'Last fix was outside the configured boundary.',
    occurred_at: new Date(Date.now() - 6 * 60000).toISOString(),
  }

  it('shows severity, title and reason', () => {
    render(<AlertList alerts={[alert]} />)

    expect(screen.getByText('HIGH')).toBeInTheDocument()
    expect(screen.getByText('Vehicle left its zone')).toBeInTheDocument()
    expect(screen.getByText('Last fix was outside the configured boundary.')).toBeInTheDocument()
  })

  it('hides triage buttons when the actor may not triage', () => {
    // A button that 403s tells an operator the action is available and then
    // refuses them in front of a customer.
    render(<AlertList alerts={[alert]} canTriage={false} />)

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers a location map link to an alert without exposing triage controls', () => {
    render(
      <MemoryRouter>
        <AlertList alerts={[{ ...alert, custody_session_id: 'custody-1' }]} canTriage={false} />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'View location' })).toHaveAttribute(
      'href',
      '/tracking/custody-1',
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers triage to a role that may perform it', () => {
    render(<AlertList alerts={[alert]} canTriage onAcknowledge={() => {}} onResolve={() => {}} />)

    expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Resolve/ })).toBeInTheDocument()
  })

  it('does not offer to acknowledge an alert that is already acknowledged', () => {
    render(
      <AlertList
        alerts={[{ ...alert, status: 'ACKNOWLEDGED' }]}
        canTriage
        onAcknowledge={() => {}}
        onResolve={() => {}}
      />
    )

    expect(screen.queryByRole('button', { name: /Acknowledge/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Resolve/ })).toBeInTheDocument()
  })
})

describe('InspectionTable', () => {
  it('flags a vehicle that still needs pickup photos', () => {
    render(
      <InspectionTable
        rows={[
          {
            custody_session_id: 'c1',
            vehicle: { plate_number: 'UP85CA1234' },
            drop_off_captured: true,
            drop_off_photo_count: 4,
            pickup_captured: false,
            pickup_photo_count: 0,
            comparison_ready: false,
            unverified_detections: 0,
          },
        ]}
      />
    )

    expect(screen.getByText('4 photos')).toBeInTheDocument()
    expect(screen.getByText('Needs photos')).toBeInTheDocument()
    expect(screen.getByText('Both sets required')).toBeInTheDocument()
  })

  it('calls potential damage potential, not confirmed', () => {
    // An unverified finding is what the detector thought it saw. Calling it
    // damage would overstate what the algorithm established.
    render(
      <InspectionTable
        rows={[
          {
            custody_session_id: 'c1',
            vehicle: { plate_number: 'UP85CA1234' },
            drop_off_captured: true,
            drop_off_photo_count: 4,
            pickup_captured: true,
            pickup_photo_count: 4,
            comparison_ready: true,
            unverified_detections: 2,
          },
        ]}
        onView={() => {}}
      />
    )

    expect(screen.getByText('2 potential')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /View comparison/ })).toBeInTheDocument()
  })
})

describe('QueueList', () => {
  const item = {
    queue_position: 1,
    custody_session_id: 'c2',
    custody_status: 'RETRIEVAL_REQUESTED',
    requested_at: new Date(Date.now() - 4 * 60000).toISOString(),
    vehicle: { plate_number: 'UP85QR5555', make: 'Tata', model: 'Nexon' },
    zone: { code: 'ZONE-B' },
    estimated_minutes: 9,
    in_progress: false,
  }

  it('shows position, vehicle, zone and estimate', () => {
    render(<QueueList items={[item]} onDispatch={() => {}} />)

    expect(screen.getByText('UP85QR5555')).toBeInTheDocument()
    expect(screen.getByText('ZONE-B')).toBeInTheDocument()
    expect(screen.getByText('9 min')).toBeInTheDocument()
  })

  it('replaces the dispatch button once a retrieval has started', () => {
    render(<QueueList items={[{ ...item, in_progress: true }]} onDispatch={() => {}} />)

    expect(screen.getByText('Being retrieved')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Start retrieval/ })).not.toBeInTheDocument()
  })
})

describe('ZoneStrip', () => {
  it('shows occupancy and whether a zone can take another car', () => {
    render(
      <ZoneStrip
        zones={[
          { id: 'z1', code: 'ZONE-A', name: 'Main yard', capacity: 20, current_occupancy: 7, available_slots: 13, occupancy_percent: 35, parkable: true },
          { id: 'z2', code: 'ZONE-B', name: 'Overflow', capacity: 10, current_occupancy: 10, available_slots: 0, occupancy_percent: 100, parkable: false },
        ]}
      />
    )

    expect(screen.getByText('7 / 20')).toBeInTheDocument()
    expect(screen.getByText('13 free')).toBeInTheDocument()
    expect(screen.getByText('Not parkable')).toBeInTheDocument()
  })
})

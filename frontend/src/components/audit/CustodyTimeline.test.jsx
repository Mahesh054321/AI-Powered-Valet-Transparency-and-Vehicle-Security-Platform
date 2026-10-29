/**
 * Tests for the custody timeline component.
 *
 * The properties that matter are the ones the whole feature rests on:
 *
 * * **oldest-first rendering** — the timeline is a narrative; a reversed list
 *   makes the reader reconstruct the order mentally;
 * * **gaps are stated** — a custody with no pickup photographs must say so,
 *   because a timeline that only renders what happened lets a missing step
 *   pass for a step that was never required;
 * * **scope is labelled** — provenance and login entries are not stamped with
 *   this custody, and must not look like they are;
 * * **degradation is honest** — a loading spinner, an error notice, and an
 *   empty state, rather than a blank panel that looks like "nothing happened".
 */

import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import CustodyTimeline, { LifecycleProgress, TimelineEvent } from './CustodyTimeline'

const TIMELINE = {
  custody_session_id: 'c-1',
  status: 'CHECKED_OUT',
  vehicle: { id: 'v-1', plate_number: 'UP78AB1234', make: 'Maruti', model: 'Swift' },
  zone: { id: 'z-1', code: 'ZONE-A' },
  dropped_off_at: '2026-09-09T10:00:00+00:00',
  picked_up_at: '2026-09-09T14:30:00+00:00',
  event_count: 3,
  had_security_incident: false,
  scope_note: 'Includes ticket and vehicle registration events.',
  lifecycle_progress: {
    VEHICLE_CHECKED_IN: true,
    CUSTODY_STARTED: true,
    PICKUP_IMAGE_CAPTURED: false,
  },
  lifecycle_missing: ['PICKUP_IMAGE_CAPTURED'],
  events: [
    {
      id: 'e1',
      seq: 1,
      action: 'TICKET_CREATED',
      label: 'Valet ticket issued',
      scope: 'provenance',
      outcome: 'SUCCESS',
      actor_email: 'valet@example.com',
      entity_type: 'valet_tickets',
      occurred_at: '2026-09-09T09:50:00+00:00',
      description: 'Ticket VT-1 issued',
    },
    {
      id: 'e2',
      seq: 2,
      action: 'VEHICLE_CHECKED_IN',
      label: 'Vehicle checked in',
      scope: 'custody',
      outcome: 'SUCCESS',
      actor_email: 'valet@example.com',
      actor_role: 'VALET_OPERATOR',
      entity_type: 'custody_sessions',
      occurred_at: '2026-09-09T10:00:00+00:00',
      ip_address: '203.0.113.9',
      request_id: 'abcdef1234567890',
      metadata: { zone_code: 'ZONE-A' },
      description: 'Checked in to ZONE-A',
    },
    {
      id: 'e3',
      seq: 3,
      action: 'VEHICLE_PICKED_UP',
      label: 'Vehicle handed back',
      scope: 'custody',
      outcome: 'FAILURE',
      actor_email: 'customer@example.com',
      entity_type: 'custody_sessions',
      occurred_at: '2026-09-09T14:30:00+00:00',
      failure_reason: 'Condition not accepted at handover',
      description: 'Handover attempted',
    },
  ],
}

describe('CustodyTimeline', () => {
  it('renders events in the order the API returned them', () => {
    const { container } = render(<CustodyTimeline timeline={TIMELINE} />)
    // Scoped to the event list: the lifecycle coverage strip is also a <ul>,
    // and an unscoped listitem query mixes the two together.
    const list = container.querySelector('.timeline__events')
    const items = within(list).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining('Valet ticket issued'),
      expect.stringContaining('Vehicle checked in'),
      expect.stringContaining('Vehicle handed back'),
    ])
  })

  it('names the vehicle and zone', () => {
    render(<CustodyTimeline timeline={TIMELINE} />)
    expect(screen.getByText(/UP78AB1234/)).toBeTruthy()
    expect(screen.getByText('ZONE-A')).toBeTruthy()
  })

  it('reports the missing lifecycle step explicitly', () => {
    render(<CustodyTimeline timeline={TIMELINE} />)
    expect(screen.getByText(/1 lifecycle step not recorded/i)).toBeTruthy()
  })

  it('marks a provenance entry as outside the custody', () => {
    render(<CustodyTimeline timeline={TIMELINE} />)
    expect(screen.getByText('before custody')).toBeTruthy()
  })

  it('shows the scope note explaining why the timeline reaches further back', () => {
    render(<CustodyTimeline timeline={TIMELINE} />)
    expect(screen.getByText(TIMELINE.scope_note)).toBeTruthy()
  })

  it('surfaces a failure reason rather than only a red badge', () => {
    render(<CustodyTimeline timeline={TIMELINE} />)
    expect(screen.getByText(/Condition not accepted at handover/)).toBeTruthy()
  })

  it('shows entry metadata behind a disclosure', () => {
    render(<CustodyTimeline timeline={TIMELINE} />)
    expect(screen.getByText('metadata')).toBeTruthy()
  })

  it('flags a custody that had a breach', () => {
    render(<CustodyTimeline timeline={{ ...TIMELINE, had_security_incident: true }} />)
    expect(screen.getByText(/geofence breach was recorded/i)).toBeTruthy()
  })

  it('says "not yet" for a pickup that has not happened', () => {
    render(<CustodyTimeline timeline={{ ...TIMELINE, picked_up_at: null }} />)
    expect(screen.getByText('not yet')).toBeTruthy()
  })

  it('shows a spinner while loading', () => {
    render(<CustodyTimeline isLoading />)
    expect(screen.getByText(/Loading timeline/i)).toBeTruthy()
  })

  it('shows an error notice with retry rather than an empty panel', () => {
    render(<CustodyTimeline error={{ message: 'boom' }} onRetry={() => {}} />)
    expect(screen.getByText(/boom/)).toBeTruthy()
  })

  it('renders nothing for a null timeline rather than crashing', () => {
    const { container } = render(<CustodyTimeline timeline={null} />)
    expect(container.innerHTML).toBe('')
  })

  it('handles a custody with no events', () => {
    render(<CustodyTimeline timeline={{ ...TIMELINE, events: [], event_count: 0 }} />)
    expect(screen.getByText(/No audit entries are recorded/i)).toBeTruthy()
  })
})

describe('LifecycleProgress', () => {
  it('renders a line per lifecycle action', () => {
    render(
      <LifecycleProgress
        progress={{ VEHICLE_CHECKED_IN: true, CUSTODY_STARTED: false }}
        missing={['CUSTODY_STARTED']}
      />,
    )
    expect(screen.getByText(/vehicle checked in/i)).toBeTruthy()
    expect(screen.getByText(/custody started/i)).toBeTruthy()
  })

  it('states how many steps are missing', () => {
    render(
      <LifecycleProgress
        progress={{ A: true, B: false, C: false }}
        missing={['B', 'C']}
      />,
    )
    expect(screen.getByText(/2 lifecycle steps not recorded/i)).toBeTruthy()
  })

  it('omits the missing note when nothing is missing', () => {
    render(<LifecycleProgress progress={{ A: true }} missing={[]} />)
    expect(screen.queryByText(/not recorded/i)).toBeNull()
  })

  it('renders nothing without progress data', () => {
    const { container } = render(<LifecycleProgress progress={null} />)
    expect(container.innerHTML).toBe('')
  })
})

describe('TimelineEvent', () => {
  // TimelineEvent renders an <li>, so the harness must be a list. Wrapping it
  // in a table produced a real DOM-nesting warning that would otherwise be
  // dismissed as test noise.
  const renderEvent = (event) =>
    render(
      <ul>
        <TimelineEvent event={event} />
      </ul>,
    )

  it('prefers the human label over the raw action name', () => {
    renderEvent(TIMELINE.events[1])
    expect(screen.getByText('Vehicle checked in')).toBeTruthy()
    expect(screen.queryByText('VEHICLE_CHECKED_IN')).toBeNull()
  })

  it('truncates the request id to a correlatable prefix', () => {
    renderEvent(TIMELINE.events[1])
    expect(screen.getByText('abcdef123456')).toBeTruthy()
  })

  it('renders nothing for a missing event', () => {
    const { container } = render(<TimelineEvent event={null} />)
    expect(container.innerHTML).toBe('')
  })
})

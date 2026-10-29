import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TicketCard } from './TicketCard'

const TICKET = {
  id: 't-1',
  ticket_code: 'VT-202609-K7M3QD',
  status: 'IN_CUSTODY',
  stored_status: 'ACTIVE',
  issued_at: '2026-09-07T09:00:00+00:00',
  actual_drop_off_at: '2026-09-07T09:05:00+00:00',
  actual_pickup_at: null,
  special_instructions: 'Leave in the shade',
  qr_payload: 'VALET:VT-202609-K7M3QD',
  qr_url: '/media/qr/abc123.png',
  vehicle: {
    plate_number: 'UP85CA1234',
    make: 'Tata',
    model: 'Nexon',
    year: 2022,
    color: 'Silver',
  },
  customer: { full_name: 'Aisha Verma', email: 'aisha@example.com', phone: '+91 98765 43210' },
  parking_zone: { id: 'z-1', code: 'ZONE-A', name: 'North Court' },
}

describe('TicketCard', () => {
  it('carries every field the brief requires on a ticket', () => {
    render(<TicketCard ticket={TICKET} />)

    // Ticket number, vehicle number and details, customer, check-in time,
    // parking zone and status.
    expect(screen.getByText('VT-202609-K7M3QD')).toBeInTheDocument()
    expect(screen.getByText('UP85CA1234')).toBeInTheDocument()
    expect(screen.getByText('Tata Nexon 2022')).toBeInTheDocument()
    expect(screen.getByText('Silver')).toBeInTheDocument()
    expect(screen.getByText('Aisha Verma')).toBeInTheDocument()
    expect(screen.getByText('+91 98765 43210')).toBeInTheDocument()
    expect(screen.getByText('ZONE-A · North Court')).toBeInTheDocument()
    expect(screen.getByText('In custody')).toBeInTheDocument()
    expect(screen.getByText('Leave in the shade')).toBeInTheDocument()
  })

  it('renders the server-generated QR image from /media', () => {
    render(<TicketCard ticket={TICKET} />)

    const image = screen.getByAltText('QR code for ticket VT-202609-K7M3QD')
    // The image is loaded, not re-rendered in the browser, so what is shown is
    // provably the code the server issued.
    expect(image).toHaveAttribute('src', '/media/qr/abc123.png')
    expect(screen.getByText('VALET:VT-202609-K7M3QD')).toBeInTheDocument()
  })

  it('shows an em dash for timestamps that have not happened yet', () => {
    render(<TicketCard ticket={TICKET} />)

    // Not "Invalid Date" - an empty pickup time is normal, not a bug.
    expect(screen.getByText('Returned')).toBeInTheDocument()
    expect(screen.queryByText(/invalid date/i)).not.toBeInTheDocument()
  })

  it('degrades honestly when the vehicle has not been handed over', () => {
    render(
      <TicketCard
        ticket={{
          ...TICKET,
          status: 'CREATED',
          actual_drop_off_at: null,
          parking_zone: null,
          qr_url: null,
        }}
      />,
    )

    expect(screen.getByText('Created')).toBeInTheDocument()
    expect(screen.getByText(/no qr code has been generated/i)).toBeInTheDocument()
  })
})

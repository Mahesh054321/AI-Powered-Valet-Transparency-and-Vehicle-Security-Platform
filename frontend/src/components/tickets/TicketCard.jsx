/**
 * The digital valet ticket.
 *
 * Everything the brief asks a ticket to carry: ticket number, vehicle number
 * and details, customer, check-in time, parking zone, status and the QR code.
 *
 * The QR image is loaded from `/media` by URL rather than being re-rendered in
 * the browser. The backend generated it once at issue time and stored its
 * SHA-256, so what is displayed here is provably the code that was issued -
 * not a fresh rendering that might differ.
 */

import { DataRow } from '../ui'
import { StatusBadge } from '../vehicles/StatusBadge'
import { TICKET_STATUS_LABELS } from '../../utils/constants'
import { formatDateTime } from '../../utils/format'

export function TicketCard({ ticket }) {
  const vehicle = ticket.vehicle ?? {}
  const customer = ticket.customer ?? {}
  const zone = ticket.parking_zone

  return (
    <div className="ticket">
      <div className="ticket__head">
        <div>
          <div className="muted">Valet ticket</div>
          <div className="ticket__code">{ticket.ticket_code}</div>
        </div>
        <StatusBadge status={ticket.status} labels={TICKET_STATUS_LABELS} />
      </div>

      <div className="ticket__body">
        <dl className="data-list">
          <DataRow label="Vehicle" value={vehicle.plate_number} mono />
          <DataRow
            label="Make / model"
            value={[vehicle.make, vehicle.model, vehicle.year].filter(Boolean).join(' ') || '—'}
          />
          <DataRow label="Colour" value={vehicle.color} />
          <DataRow label="Customer" value={customer.full_name || customer.email} />
          <DataRow label="Contact" value={customer.phone} />
          <DataRow label="Issued" value={formatDateTime(ticket.issued_at)} />
          <DataRow label="Checked in" value={formatDateTime(ticket.actual_drop_off_at)} />
          <DataRow label="Parking zone" value={zone ? `${zone.code} · ${zone.name}` : null} />
          <DataRow label="Returned" value={formatDateTime(ticket.actual_pickup_at)} />
          {ticket.special_instructions && (
            <DataRow label="Instructions" value={ticket.special_instructions} />
          )}
        </dl>
      </div>

      <div className="ticket__qr">
        {ticket.qr_url ? (
          <>
            <img src={ticket.qr_url} alt={`QR code for ticket ${ticket.ticket_code}`} />
            <div>
              <div className="muted">Scan at the gate</div>
              <div className="mono">{ticket.qr_payload}</div>
              <div className="muted">
                Rendered by the server at issue time; the stored digest is on the audit trail.
              </div>
            </div>
          </>
        ) : (
          <p className="muted">No QR code has been generated for this ticket.</p>
        )}
      </div>
    </div>
  )
}

export default TicketCard

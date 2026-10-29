/**
 * The vehicle table, shared by ACTIVE VEHICLES and PENDING PICKUPS.
 *
 * One component because the two sections show the same rows with different
 * actions. Duplicating the row markup would mean a change to how a plate or a
 * GPS state is rendered has to be made twice, and the two would drift.
 *
 * **GPS is labelled per row, not per page.** A custody session can have no
 * fixes at all, which is not the same as a clean last position: one means "we
 * have no idea where this car is". A track can also mix real and simulated
 * fixes, so the source is shown on the row an operator is about to act on.
 */

import { Badge } from '../ui'
import { StatusBadge } from '../vehicles/StatusBadge'
import { relativeTime, vehicleLabel } from '../../services/staffDashboardService'

function GpsCell({ row }) {
  if (!row.gps_has_data) {
    // Distinct from "parked and fine". Absence of data is its own state.
    return <span className="valet-table__muted">No fix recorded</span>
  }
  const fix = row.gps
  return (
    <span className="valet-table__gps">
      <StatusBadge status={fix.movement_status} />
      <span className="valet-table__muted">
        {relativeTime(fix.recorded_at) ?? 'unknown age'}
        {fix.source ? ` · ${fix.source.toLowerCase()}` : ''}
      </span>
    </span>
  )
}

export function VehicleTable({ rows, labels, actionsFor, emptyMessage }) {
  if (!rows?.length) {
    return <p className="section__empty">{emptyMessage}</p>
  }

  return (
    <div className="valet-table" role="table">
      <div className="valet-table__row valet-table__row--head" role="row">
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Customer</span>
        <span role="columnheader">Zone</span>
        <span role="columnheader">Status</span>
        <span role="columnheader">GPS</span>
        <span role="columnheader">Actions</span>
      </div>

      {rows.map((row) => (
        <div
          key={row.custody_session_id}
          className={`valet-table__row ${row.alarming ? 'valet-table__row--alert' : ''}`}
          role="row"
        >
          <span role="cell" className="valet-table__vehicle">
            <strong>{row.vehicle?.plate_number ?? '—'}</strong>
            <span className="valet-table__muted">
              {[row.vehicle?.make, row.vehicle?.model].filter(Boolean).join(' ') || '—'}
            </span>
          </span>

          <span role="cell">
            {row.customer?.name ?? '—'}
            <span className="valet-table__muted">{row.customer?.phone ?? ''}</span>
          </span>

          <span role="cell">{row.zone?.code ?? '—'}</span>

          <span role="cell">
            <StatusBadge status={row.display_status} labels={labels} />
            {/* The underlying custody state stays visible. The badge is a
                summary; hiding what it summarised would make a wrong summary
                impossible to catch. */}
            <span className="valet-table__muted">{row.custody_status}</span>
          </span>

          <span role="cell">
            <GpsCell row={row} />
          </span>

          <span role="cell" className="valet-table__actions">
            {row.open_alert_count > 0 && (
              <Badge tone="badge--danger">{row.open_alert_count} open</Badge>
            )}
            {actionsFor?.(row)}
          </span>
        </div>
      ))}
    </div>
  )
}

/** The arrival list: a ticket exists, the car has not been handed over yet. */
export function IncomingTable({ rows }) {
  if (!rows?.length) return null

  return (
    <div className="valet-table" role="table">
      <div className="valet-table__row valet-table__row--head" role="row">
        <span role="columnheader">Ticket</span>
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Customer</span>
        <span role="columnheader">Issued</span>
      </div>

      {rows.map((row) => (
        <div key={row.ticket_id} className="valet-table__row" role="row">
          <span role="cell" className="mono">
            {row.ticket_code}
          </span>
          <span role="cell">
            <strong>{row.vehicle?.plate_number ?? '—'}</strong>
            <span className="valet-table__muted">{vehicleLabel(row.vehicle)}</span>
          </span>
          <span role="cell">{row.customer?.name ?? '—'}</span>
          <span role="cell" className="valet-table__muted">
            {relativeTime(row.issued_at) ?? '—'}
          </span>
        </div>
      ))}
    </div>
  )
}

export default VehicleTable

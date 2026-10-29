/**
 * Manager dashboard tables and the filter bar.
 *
 * These are read-only. There is deliberately no action column: changing a
 * custody state belongs on the operator dashboard, behind the confirmation
 * dialog and the state machine that already owns that transition. A second
 * place to mutate the same row would be a second place to get it wrong.
 */

import { Badge } from '../ui'
import { statusLabel } from '../../services/managerService'
import { Link } from 'react-router-dom'

const SEVERITY_TONE = {
  LOW: 'badge--muted',
  MEDIUM: 'badge--info',
  HIGH: 'badge--warning',
  CRITICAL: 'badge--danger',
}

const VERIFICATION_TONE = {
  UNVERIFIED: 'badge--warning',
  CONFIRMED: 'badge--danger',
  REJECTED: 'badge--muted',
}

function shortTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.toISOString().slice(5, 10)} ${d.toISOString().slice(11, 16)}`
}

function EmptyRow({ columns, message }) {
  return (
    <p className="section__empty" style={{ gridColumn: `1 / ${columns + 1}` }}>
      {message}
    </p>
  )
}

/** Recent tickets in the selected window. */
export function RecentTicketsTable({ rows }) {
  return (
    <div className="mgr-table mgr-table--7" role="table">
      <div className="mgr-table__row mgr-table__row--head" role="row">
        <span role="columnheader">Ticket</span>
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Customer</span>
        <span role="columnheader">Issued</span>
        <span role="columnheader">Zone</span>
        <span role="columnheader">Status</span>
        <span role="columnheader">Duration</span>
      </div>

      {rows?.length ? (
        rows.map((row) => (
          <div className="mgr-table__row" role="row" key={row.ticket_id}>
            <span role="cell" className="mono">
              {row.ticket_code}
            </span>
            <span role="cell">{row.vehicle?.plate_number ?? '—'}</span>
            <span role="cell">{row.customer?.name ?? '—'}</span>
            <span role="cell" className="mgr-table__muted">
              {shortTime(row.issued_at)}
            </span>
            <span role="cell">{row.zone_code ?? '—'}</span>
            <span role="cell">
              <Badge
                tone={
                  row.is_open ? 'badge--info' : 'badge--muted'
                }
              >
                {statusLabel(row.custody_status)}
              </Badge>
            </span>
            <span role="cell">
              {row.duration_hours != null ? `${row.duration_hours}h` : '—'}
            </span>
          </div>
        ))
      ) : (
        <EmptyRow columns={7} message="No tickets were issued in the selected range." />
      )}
    </div>
  )
}

/** Vehicles in custody right now. */
export function ActiveVehiclesTable({ rows }) {
  return (
    <div className="mgr-table" role="table">
      <div className="mgr-table__row mgr-table__row--head" role="row">
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Customer</span>
        <span role="columnheader">Zone</span>
        <span role="columnheader">Status</span>
        <span role="columnheader">Held for</span>
        <span role="columnheader">Alerts</span>
      </div>

      {rows?.length ? (
        rows.map((row) => (
          <div
            className={`mgr-table__row ${row.open_alerts > 0 ? 'mgr-table__row--alert' : ''}`}
            role="row"
            key={row.custody_session_id}
          >
            <span role="cell">
              <strong>{row.vehicle?.plate_number ?? '—'}</strong>
              <span className="mgr-table__muted">
                {[row.vehicle?.make, row.vehicle?.model].filter(Boolean).join(' ')}
              </span>
            </span>
            <span role="cell">{row.customer?.name ?? '—'}</span>
            <span role="cell">{row.zone_code ?? '—'}</span>
            <span role="cell">
              <Badge
                tone={
                  row.custody_status === 'READY_FOR_PICKUP'
                    ? 'badge--success'
                    : 'badge--info'
                }
              >
                {statusLabel(row.custody_status)}
              </Badge>
            </span>
            <span role="cell">
              {row.hours_in_custody != null ? `${row.hours_in_custody}h` : '—'}
            </span>
            <span role="cell">
              {row.open_alerts > 0 ? (
                <Badge tone="badge--danger">{row.open_alerts} open</Badge>
              ) : (
                <span className="mgr-table__muted">none</span>
              )}
            </span>
          </div>
        ))
      ) : (
        <EmptyRow columns={6} message="No vehicles are in custody right now." />
      )}
    </div>
  )
}

/** Security alerts, worst first. */
export function AlertsTable({ rows }) {
  return (
    <div className="mgr-table" role="table">
      <div className="mgr-table__row mgr-table__row--head" role="row">
        <span role="columnheader">Severity</span>
        <span role="columnheader">Type</span>
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Occurred</span>
        <span role="columnheader">Status</span>
        <span role="columnheader">Handled by</span>
      </div>

      {rows?.length ? (
        rows.map((row) => (
          <div className="mgr-table__row" role="row" key={row.id}>
            <span role="cell">
              <Badge tone={SEVERITY_TONE[row.severity] ?? 'badge--muted'}>
                {row.severity}
              </Badge>
            </span>
            <span role="cell">
              {row.alert_type.replace(/_/g, ' ')}
              <span className="mgr-table__muted">{row.reason}</span>
            </span>
            <span role="cell">{row.vehicle?.plate_number ?? '—'}</span>
            <span role="cell" className="mgr-table__muted">
              {shortTime(row.occurred_at)}
            </span>
            <span role="cell">
              <Badge tone={row.is_open ? 'badge--warning' : 'badge--muted'}>
                {statusLabel(row.status)}
              </Badge>
            </span>
            <span role="cell" className="mgr-table__muted">
              {row.resolved_by ?? row.acknowledged_by ?? '—'}
            </span>
          </div>
        ))
      ) : (
        <EmptyRow columns={6} message="No alerts in the selected range." />
      )}
    </div>
  )
}

/**
 * Damage cases, unverified first.
 *
 * An unverified finding is labelled *potential*, never damage. The detector
 * flagged it; nobody has confirmed it. Calling it damage would overstate what
 * the algorithm established, and this table is where a manager decides.
 */
export function DamageTable({ rows }) {
  return (
    <div className="mgr-table" role="table">
      <div className="mgr-table__row mgr-table__row--head" role="row">
        <span role="columnheader">Finding</span>
        <span role="columnheader">Area</span>
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Confidence</span>
        <span role="columnheader">Verification</span>
        <span role="columnheader">Reviewer</span>
      </div>

      {rows?.length ? (
        rows.map((row) => (
          <div className="mgr-table__row" role="row" key={row.id}>
            <span role="cell">
              <strong>
                {row.damage_type.replace(/_/g, ' ')}
                {row.verification_status === 'UNVERIFIED' ? ' (potential)' : ''}
              </strong>
              <span className="mgr-table__muted">
                {row.severity} · captured {shortTime(row.captured_at)}
              </span>
            </span>
            <span role="cell">{(row.region ?? '').replace(/_/g, ' ')}</span>
            <span role="cell">{row.vehicle?.plate_number ?? '—'}</span>
            <span role="cell">
              {row.confidence != null ? `${Math.round(row.confidence * 100)}%` : '—'}
            </span>
            <span role="cell">
              <Badge
                tone={VERIFICATION_TONE[row.verification_status] ?? 'badge--muted'}
              >
                {statusLabel(row.verification_status)}
              </Badge>
            </span>
            <span role="cell" className="mgr-table__muted">
              {row.verified_by ?? 'awaiting review'}
            </span>
          </div>
        ))
      ) : (
        <EmptyRow columns={6} message="No damage findings in the selected range." />
      )}
    </div>
  )
}

/** Disputed custody sessions awaiting a manager decision. */
export function PendingDisputesTable({ rows }) {
  return (
    <div className="mgr-table mgr-table--5" role="table">
      <div className="mgr-table__row mgr-table__row--head" role="row">
        <span role="columnheader">Ticket</span>
        <span role="columnheader">Vehicle</span>
        <span role="columnheader">Customer</span>
        <span role="columnheader">Status</span>
        <span role="columnheader">Action</span>
      </div>

      {rows?.length ? (
        rows.map((row) => (
          <div className="mgr-table__row" role="row" key={row.custody_session_id}>
            <span role="cell" className="mono">{row.ticket_code ?? '—'}</span>
            <span role="cell">{row.vehicle?.plate_number ?? '—'}</span>
            <span role="cell">{row.customer?.name ?? '—'}</span>
            <span role="cell">
              <Badge tone="badge--warning">Disputed</Badge>
            </span>
            <span role="cell">
              <Link className="btn btn--ghost btn--sm" to={`/condition/${row.custody_session_id}`}>
                Review dispute
              </Link>
            </span>
          </div>
        ))
      ) : (
        <EmptyRow columns={5} message="No custody disputes are awaiting review." />
      )}
    </div>
  )
}

/**
 * The filter bar.
 *
 * Options come from the backend rather than a local constant, so the dropdowns
 * cannot offer a status the API would reject, and a vehicle list cannot drift
 * from the vehicles that actually have sessions.
 */
export function FilterBar({ filters, options, onChange, onReset, busy }) {
  const set = (key) => (event) => onChange({ ...filters, [key]: event.target.value })

  return (
    <div className="filter-bar">
      <label className="filter-bar__field">
        <span>From</span>
        <input type="date" value={filters.dateFrom} onChange={set('dateFrom')} />
      </label>

      <label className="filter-bar__field">
        <span>To</span>
        <input type="date" value={filters.dateTo} onChange={set('dateTo')} />
      </label>

      <label className="filter-bar__field">
        <span>Custody status</span>
        <select value={filters.status} onChange={set('status')}>
          <option value="">All statuses</option>
          {(options?.statuses ?? []).map((status) => (
            <option key={status} value={status}>
              {statusLabel(status)}
            </option>
          ))}
        </select>
      </label>

      <label className="filter-bar__field">
        <span>Alert severity</span>
        <select value={filters.severity} onChange={set('severity')}>
          <option value="">All severities</option>
          {(options?.severities ?? []).map((severity) => (
            <option key={severity} value={severity}>
              {statusLabel(severity)}
            </option>
          ))}
        </select>
      </label>

      <label className="filter-bar__field filter-bar__field--wide">
        <span>Vehicle</span>
        <select value={filters.vehicleId} onChange={set('vehicleId')}>
          <option value="">All vehicles</option>
          {(options?.vehicles ?? []).map((vehicle) => (
            <option key={vehicle.id} value={vehicle.id}>
              {vehicle.label}
            </option>
          ))}
        </select>
      </label>

      <button type="button" className="btn btn--ghost" onClick={onReset} disabled={busy}>
        Reset
      </button>
    </div>
  )
}

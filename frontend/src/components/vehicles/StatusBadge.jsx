/**
 * Status badge for domain statuses.
 *
 * One component rather than a tone map per page, so a status is coloured the
 * same way everywhere. Unknown values fall back to a muted badge and show the
 * raw key: guessing a colour for a status the UI has not been told about would
 * be worse than admitting it does not recognise it.
 */

import { Badge } from '../ui'

const TONES = {
  // Operator display vocabulary (Phase 10). Coarser than the custody statuses
  // below; the labels travel with the payload from the backend, so this map
  // only decides colour. `ALERT` is not a custody state at all — it is a
  // security override the server applies on top of whichever state the car is
  // in. `IN_CUSTODY` and `COMPLETED` are shared with the vehicle and ticket
  // vocabularies below and are defined once, further down.
  MOVING: 'badge--warning',
  READY: 'badge--success',
  ALERT: 'badge--danger',
  // Vehicles
  REGISTERED: 'badge--success',
  IN_CUSTODY: 'badge--info',
  FLAGGED: 'badge--warning',
  ARCHIVED: 'badge--muted',
  // Tickets (projected vocabulary)
  CREATED: 'badge--muted',
  READY_FOR_PICKUP: 'badge--warning',
  COMPLETED: 'badge--success',
  CANCELLED: 'badge--muted',
  EXPIRED: 'badge--warning',
  // Custody sessions
  CHECKED_IN: 'badge--info',
  PARKED: 'badge--info',
  IN_TRANSIT: 'badge--info',
  RETRIEVAL_REQUESTED: 'badge--warning',
  RETRIEVAL_IN_PROGRESS: 'badge--warning',
  CHECKED_OUT: 'badge--success',
  DISPUTED: 'badge--danger',
}

export function StatusBadge({ status, labels = {} }) {
  if (!status) return <Badge tone="badge--muted">—</Badge>
  return (
    <Badge tone={TONES[status] ?? 'badge--muted'}>{labels[status] ?? status}</Badge>
  )
}

export default StatusBadge

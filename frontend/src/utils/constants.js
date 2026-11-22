/**
 * Shared constants: roles, labels, and the landing route per role.
 *
 * These mirror the backend's role enum in `backend/utils/security.py`. The
 * backend remains authoritative - this list only drives what the UI shows.
 */

export const ROLES = {
  CUSTOMER: 'CUSTOMER',
  VALET_OPERATOR: 'VALET_OPERATOR',
  VALET_MANAGER: 'VALET_MANAGER',
  ADMIN: 'ADMIN',
}

export const ROLE_LABELS = {
  CUSTOMER: 'Customer',
  VALET_OPERATOR: 'Valet Operator',
  VALET_MANAGER: 'Valet Manager',
  ADMIN: 'Administrator',
}

export const ROLE_HOME = {
  CUSTOMER: '/dashboard',
  VALET_OPERATOR: '/valet',
  VALET_MANAGER: '/manager',
  ADMIN: '/manager',
}

/**
 * Custody-session statuses.
 *
 * These must match `backend/models/enums.py::CustodyStatus` exactly. The list
 * that was here before this module was written (PENDING / IN_RETRIEVAL /
 * COMPLETED / FLAGGED) matched nothing the backend can emit, so any badge
 * driven by it would have rendered a bare key instead of a label. The backend
 * remains authoritative; this drives presentation only.
 */
export const CUSTODY_STATUS = {
  CHECKED_IN: 'CHECKED_IN',
  PARKED: 'PARKED',
  IN_TRANSIT: 'IN_TRANSIT',
  RETRIEVAL_REQUESTED: 'RETRIEVAL_REQUESTED',
  RETRIEVAL_IN_PROGRESS: 'RETRIEVAL_IN_PROGRESS',
  READY_FOR_PICKUP: 'READY_FOR_PICKUP',
  CHECKED_OUT: 'CHECKED_OUT',
  DISPUTED: 'DISPUTED',
}

export const CUSTODY_STATUS_LABELS = {
  CHECKED_IN: 'Checked in',
  PARKED: 'Parked',
  IN_TRANSIT: 'In transit',
  RETRIEVAL_REQUESTED: 'Retrieval requested',
  RETRIEVAL_IN_PROGRESS: 'Being retrieved',
  READY_FOR_PICKUP: 'Ready for pickup',
  CHECKED_OUT: 'Checked out',
  DISPUTED: 'Disputed',
}

/**
 * Ticket statuses as the API reports them.
 *
 * This is the *projected* vocabulary, not the stored one: the database keeps
 * ISSUED / ACTIVE, and the backend maps them to CREATED / IN_CUSTODY /
 * READY_FOR_PICKUP based on the custody session. See
 * `backend/services/tickets.py` for the mapping and why it is derived rather
 * than stored.
 */
export const TICKET_STATUS = {
  CREATED: 'CREATED',
  IN_CUSTODY: 'IN_CUSTODY',
  READY_FOR_PICKUP: 'READY_FOR_PICKUP',
  COMPLETED: 'COMPLETED',
  CANCELLED: 'CANCELLED',
  EXPIRED: 'EXPIRED',
}

export const TICKET_STATUS_LABELS = {
  CREATED: 'Created',
  IN_CUSTODY: 'In custody',
  READY_FOR_PICKUP: 'Ready for pickup',
  COMPLETED: 'Completed',
  CANCELLED: 'Cancelled',
  EXPIRED: 'Expired',
}

/** Maps a ticket status onto the badge tone used across the UI. */
export const TICKET_STATUS_TONES = {
  CREATED: 'badge--muted',
  IN_CUSTODY: 'badge--info',
  READY_FOR_PICKUP: 'badge--warning',
  COMPLETED: 'badge--success',
  CANCELLED: 'badge--muted',
  EXPIRED: 'badge--warning',
}

/** Vehicle statuses, matching `backend/models/enums.py::VehicleStatus`. */
export const VEHICLE_STATUS = {
  REGISTERED: 'REGISTERED',
  IN_CUSTODY: 'IN_CUSTODY',
  FLAGGED: 'FLAGGED',
  ARCHIVED: 'ARCHIVED',
}

export const VEHICLE_STATUS_LABELS = {
  REGISTERED: 'Registered',
  IN_CUSTODY: 'In custody',
  FLAGGED: 'Flagged',
  ARCHIVED: 'Archived',
}

/** The drop-off photo angles the check-in wizard asks for. */
export const CHECK_IN_PHOTO_ANGLES = [
  { label: 'Front', key: 'front' },
  { label: 'Rear', key: 'rear' },
  { label: 'Left side', key: 'left_side' },
  { label: 'Right side', key: 'right_side' },
]

/**
 * Damage-finding types, matching `backend/models/damage_detection.py::DamageType`.
 *
 * These are *labels a heuristic guessed*, not classifications from a trained
 * model. The UI always pairs them with a confidence and a verification status.
 */
export const DAMAGE_TYPES = [
  'SCRATCH',
  'DENT',
  'CRACK',
  'PAINT_CHIP',
  'RUST',
  'GLASS_DAMAGE',
  'TYRE_DAMAGE',
  'MISSING_PART',
  'DIRT_STAIN',
  'OTHER',
]

export const DAMAGE_TYPE_LABELS = {
  SCRATCH: 'Scratch',
  DENT: 'Dent',
  CRACK: 'Crack',
  PAINT_CHIP: 'Paint chip',
  RUST: 'Rust',
  GLASS_DAMAGE: 'Glass damage',
  TYRE_DAMAGE: 'Tyre damage',
  MISSING_PART: 'Missing part',
  DIRT_STAIN: 'Dirt / stain',
  OTHER: 'Unclassified change',
}

/** Severities, matching `DamageSeverity`. Used for ordering and colour only. */
export const DAMAGE_SEVERITY_LABELS = {
  COSMETIC: 'Cosmetic',
  MINOR: 'Minor',
  MODERATE: 'Moderate',
  SEVERE: 'Severe',
}

export const DAMAGE_SEVERITY_TONES = {
  COSMETIC: 'badge--muted',
  MINOR: 'badge--info',
  MODERATE: 'badge--warning',
  SEVERE: 'badge--danger',
}

/**
 * Verification lifecycle for a machine-produced finding.
 *
 * `UNVERIFIED` is the state every finding starts in, and it is the important
 * one: the UI must present those as *potential* damage. Only `CONFIRMED` may be
 * shown as settled fact, and only a manager or administrator can set it.
 */
export const VERIFICATION_STATUS = {
  UNVERIFIED: 'UNVERIFIED',
  CONFIRMED: 'CONFIRMED',
  REJECTED: 'REJECTED',
}

export const VERIFICATION_STATUS_LABELS = {
  UNVERIFIED: 'Awaiting review',
  CONFIRMED: 'Confirmed',
  REJECTED: 'Rejected',
}

export const VERIFICATION_STATUS_TONES = {
  UNVERIFIED: 'badge--warning',
  CONFIRMED: 'badge--danger',
  REJECTED: 'badge--muted',
}

/** Camera angles the capture screen offers, matching the backend's viewpoints. */
export const INSPECTION_ANGLES = [
  { key: 'FRONT', label: 'Front' },
  { key: 'REAR', label: 'Rear' },
  { key: 'LEFT_SIDE', label: 'Left side' },
  { key: 'RIGHT_SIDE', label: 'Right side' },
  { key: 'TOP', label: 'Top / roof' },
  { key: 'WHEEL', label: 'Wheel' },
  { key: 'INTERIOR', label: 'Interior' },
  { key: 'GENERAL', label: 'Unspecified' },
]

export function angleLabel(key) {
  if (!key) return 'Unspecified'
  const match = INSPECTION_ANGLES.find((angle) => angle.key === String(key).toUpperCase())
  return match ? match.label : String(key).replace(/_/g, ' ').toLowerCase()
}

/** Alert severities (Phase 8 onward). */
export const ALERT_SEVERITY = {
  LOW: 'LOW',
  MEDIUM: 'MEDIUM',
  HIGH: 'HIGH',
  CRITICAL: 'CRITICAL',
}

export const ALERT_SEVERITY_LABELS = {
  LOW: 'Low',
  MEDIUM: 'Medium',
  HIGH: 'High',
  CRITICAL: 'Critical',
}

export const ALERT_SEVERITY_TONES = {
  LOW: 'badge--muted',
  MEDIUM: 'badge--warning',
  HIGH: 'badge--danger',
  CRITICAL: 'badge--danger',
}

/**
 * Geofence verdict for a single GPS fix.
 *
 * Produced by a stated rule set, not a trained model - see
 * `docs/AI-INTEGRITY.md`. The labels say what was observed rather than what was
 * concluded, so `SUSPICIOUS` does not read as an accusation.
 */
export const MOVEMENT_STATUS = {
  NORMAL: 'NORMAL',
  WARNING: 'WARNING',
  SUSPICIOUS: 'SUSPICIOUS',
  BREACH: 'BREACH',
}

export const MOVEMENT_STATUS_LABELS = {
  NORMAL: 'Normal',
  WARNING: 'Warning',
  SUSPICIOUS: 'Suspicious',
  BREACH: 'Geofence breach',
}

export const MOVEMENT_STATUS_TONES = {
  NORMAL: 'badge--success',
  WARNING: 'badge--warning',
  SUSPICIOUS: 'badge--warning',
  BREACH: 'badge--danger',
}

/** Map colours for the geofence ring, keyed by the latest verdict. */
export const MOVEMENT_STATUS_COLORS = {
  NORMAL: '#2f9e6f',
  WARNING: '#d99a1f',
  SUSPICIOUS: '#e07b39',
  BREACH: '#d14343',
}

export function movementStatusLabel(status) {
  if (!status) return 'Unknown'
  return MOVEMENT_STATUS_LABELS[String(status).toUpperCase()] ?? String(status)
}

export function movementStatusColor(status) {
  return MOVEMENT_STATUS_COLORS[String(status).toUpperCase()] ?? '#7a8699'
}

/** What the geofence engine can flag. Mirrors the backend's `AlertType`. */
export const ALERT_TYPE_LABELS = {
  GEOFENCE_BREACH: 'Geofence breach',
  ROUTE_DEVIATION: 'Route deviation',
  UNUSUAL_SPEED: 'Unusual speed',
  ABNORMAL_MOVEMENT: 'Abnormal movement',
  UNAUTHORIZED_ACCESS: 'Unauthorised access',
  TAMPER_DETECTED: 'Tamper detected',
  TELEMETRY_LOSS: 'Telemetry loss',
}

export function alertTypeLabel(type) {
  if (!type) return 'Alert'
  return ALERT_TYPE_LABELS[String(type).toUpperCase()] ?? String(type).replace(/_/g, ' ').toLowerCase()
}

/**
 * Prototype ML security classification.
 *
 * A separate vocabulary from `MOVEMENT_STATUS` on purpose. `MOVEMENT_STATUS` is
 * a measurement against a known boundary made by the rule engine; this is an
 * estimate from a prototype classifier trained on synthetic telemetry. Keeping
 * two vocabularies means the UI can never present a prototype's guess as a
 * measurement, and the backend cannot let one silently overwrite the other.
 */
export const ML_CLASSIFICATION = {
  NORMAL: 'NORMAL',
  SUSPICIOUS: 'SUSPICIOUS',
}

export const ML_CLASSIFICATION_LABELS = {
  NORMAL: 'Classifier: normal',
  SUSPICIOUS: 'Classifier: suspicious',
}

export const ML_CLASSIFICATION_TONES = {
  NORMAL: 'badge--muted',
  SUSPICIOUS: 'badge--warning',
}

/**
 * Shown next to every classifier output in the UI.
 *
 * The disclaimer lives in the client too, not only in the API payload, so that
 * a screenshot of the tracking dashboard cannot be read as a production claim
 * once the response body is cropped out.
 */
export const ML_PROTOTYPE_NOTICE =
  'Prototype classifier, trained and evaluated on synthetic telemetry. Advisory only — ' +
  'rule-based geofence checks remain authoritative.'

/**
 * The standing notice for retrieval-time estimates.
 *
 * Kept as a constant rather than inlined in the card, for the same reason as
 * `ML_PROTOTYPE_NOTICE`: if a reviewer screenshots the estimate on its own, the
 * caveat has to still be in frame. The backend sends `data_basis` and
 * `disclaimer` on every response too, and the UI prefers those - this is the
 * fallback wording, not a duplicate claim.
 */
export const RETRIEVAL_PROTOTYPE_NOTICE =
  'Estimate from a baseline model trained on synthetic historical data for this MVP. ' +
  'Indicative only — not an operational forecast.'

export function mlClassificationLabel(classification) {
  if (!classification) return 'Classifier: not run'
  return ML_CLASSIFICATION_LABELS[String(classification).toUpperCase()] ?? String(classification)
}

export function mlClassificationTone(classification) {
  return ML_CLASSIFICATION_TONES[String(classification).toUpperCase()] ?? 'badge--muted'
}

/** Format P(SUSPICIOUS) as a percentage the way the backend reasons do. */
export function mlProbabilityLabel(probability) {
  if (probability === null || probability === undefined) return '—'
  return `${Math.round(Number(probability) * 100)}%`
}

/** Alert lifecycle. `DISMISSED` is feedback about the detector, not a fix. */
export const ALERT_STATUS = {
  OPEN: 'OPEN',
  ACKNOWLEDGED: 'ACKNOWLEDGED',
  INVESTIGATING: 'INVESTIGATING',
  RESOLVED: 'RESOLVED',
  DISMISSED: 'DISMISSED',
}

export const ALERT_STATUS_LABELS = {
  OPEN: 'Open',
  ACKNOWLEDGED: 'Acknowledged',
  INVESTIGATING: 'Investigating',
  RESOLVED: 'Resolved',
  DISMISSED: 'Dismissed',
}

export const ALERT_STATUS_TONES = {
  OPEN: 'badge--danger',
  ACKNOWLEDGED: 'badge--warning',
  INVESTIGATING: 'badge--warning',
  RESOLVED: 'badge--success',
  DISMISSED: 'badge--muted',
}

/**
 * Provenance of a GPS fix.
 *
 * Surfaced on every reading, never inferred from a deployment-wide setting: the
 * project mandate is that simulated telemetry must be impossible to mistake for
 * hardware telemetry.
 */
export const TELEMETRY_SOURCE = {
  SIMULATED: 'SIMULATED',
  HARDWARE: 'HARDWARE',
  MANUAL: 'MANUAL',
}

export const TELEMETRY_SOURCE_LABELS = {
  SIMULATED: 'Simulated',
  HARDWARE: 'Legacy hardware source',
  MANUAL: 'Entered by staff',
  MOBILE_GPS: 'Authorized mobile GPS',
  REAL_GPS: 'Authenticated hardware GPS',
  VEHICLE_GPS: 'Legacy vehicle source',
  CAR_GPS: 'Legacy vehicle source',
}

export function telemetrySourceLabel(source) {
  if (!source) return 'Unknown source'
  return TELEMETRY_SOURCE_LABELS[String(source).toUpperCase()] ?? String(source)
}

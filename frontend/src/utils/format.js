/** Presentation helpers shared by pages and components. */

/** Format an uptime duration in seconds as a compact human string. */
export function formatUptime(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return '—'
  if (seconds < 60) return `${Number(seconds).toFixed(1)} s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

/** Format bytes as MB with one decimal place. */
export function formatMegabytes(bytes) {
  if (bytes == null) return '—'
  return `${bytes / 1024 / 1024} MB`
}

/** Truncate long identifiers (UUIDs, hashes) for display. */
export function shortId(value, length = 8) {
  if (!value) return '—'
  return String(value).slice(0, length)
}

/** Title-case a status enum value: `IN_RETRIEVAL` -> `In Retrieval`. */
export function humaniseStatus(value) {
  if (!value) return '—'
  return String(value)
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/**
 * Format an ISO-8601 timestamp for display.
 *
 * Returns an em dash for null/undefined rather than "Invalid Date", because
 * most of the timeline fields on a ticket are legitimately empty until that
 * step has happened - and "Invalid Date" reads like a bug to a reviewer.
 */
export function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Time only, for compact timeline rows. */
export function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

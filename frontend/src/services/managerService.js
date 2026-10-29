/**
 * Manager analytics service.
 *
 * Reads come from one composed endpoint, filtered by query string. The report
 * endpoint is the same backend query path in a downloadable shape, so a filed
 * report cannot disagree with the screen it summarised.
 *
 * There are no write calls here at all: this screen is read-only. Every action
 * on a vehicle still happens on the operator dashboard, through the endpoint
 * that owns that state transition.
 */

import { apiClient, apiGet } from './apiClient'
import { ENDPOINTS } from './endpoints'

/** Build the query string from the current filter state. */
export function buildFilterParams(filters = {}) {
  const params = new URLSearchParams()
  if (filters.dateFrom) params.set('date_from', filters.dateFrom)
  if (filters.dateTo) params.set('date_to', filters.dateTo)
  if (filters.status) params.set('status', filters.status)
  if (filters.severity) params.set('severity', filters.severity)
  if (filters.vehicleId) params.set('vehicle_id', filters.vehicleId)
  return params.toString()
}

/** The whole manager screen for one set of filters. */
export function fetchManagerDashboard(filters = {}) {
  const qs = buildFilterParams(filters)
  return apiGet(qs ? `${ENDPOINTS.managerDashboard}?${qs}` : ENDPOINTS.managerDashboard)
}

/**
 * The report as a JSON object, for previewing before download.
 *
 * Kept separate from the download so the two cannot drift: both hit the same
 * endpoint, one parses and one streams.
 */
export function fetchManagerReport(filters = {}) {
  const params = buildFilterParams(filters)
  const qs = params ? `${params}&format=json` : 'format=json'
  return apiGet(`${ENDPOINTS.managerReport}?${qs}`)
}

/**
 * Download the report as a file.
 *
 * Goes through the raw axios client rather than `apiGet`, because the response
 * is a CSV attachment and not the JSON envelope every other call unwraps. The
 * filename comes from `Content-Disposition` when the server supplies one, so
 * the timestamp the backend stamped is the one the user sees.
 */
export async function downloadManagerReport(filters = {}, format = 'csv') {
  const params = buildFilterParams(filters)
  const qs = params ? `${params}&format=${format}` : `format=${format}`

  const response = await apiClient.get(`${ENDPOINTS.managerReport}?${qs}`, {
    responseType: 'blob',
  })

  const disposition = response.headers?.['content-disposition'] ?? ''
  const match = /filename="?([^";]+)"?/.exec(disposition)
  const filename =
    match?.[1] ?? `valet-operations.${format === 'csv' ? 'csv' : 'json'}`

  const url = URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  // Revoking immediately can cancel the download in some browsers, so it is
  // deferred to the next turn.
  setTimeout(() => URL.revokeObjectURL(url), 1000)

  return {
    filename,
    // Surfaced so the UI can warn that the file contains generated history. A
    // CSV cannot carry the banner the screen shows, so this is the one place it
    // can still be stated.
    containsGeneratedHistory:
      response.headers?.['x-report-contains-generated-history'] === 'true',
  }
}

/**
 * Whether a chart has enough data to be drawn as a trend.
 *
 * This is the whole point of the backend sending `min_points`: a two-point line
 * is not a trend, and drawing one anyway would present a coincidence as a
 * finding. The decision is kept here so no component can quietly forget it.
 */
export function chartIsDrawable(chart) {
  if (!chart) return false
  if (!Array.isArray(chart.series)) return false
  // Defer to the backend's verdict when it supplies one: only the backend knows
  // the underlying record count. The length check is a fallback for a payload
  // that omits the flag, not a second opinion that can override it — a
  // histogram with one bucket can still rest on hundreds of records.
  if (chart.sufficient != null) return chart.sufficient !== false
  return chart.series.length >= (chart.min_points ?? 1)
}

/** A short human label for a filter value. */
export function statusLabel(status) {
  if (!status) return 'All statuses'
  return status
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/** Format a metric value with its unit, handling a null measurement. */
export function formatMetric(metric) {
  if (!metric) return '—'
  if (metric.value == null) return 'No data'
  const value =
    typeof metric.value === 'number' && !Number.isInteger(metric.value)
      ? metric.value.toFixed(1)
      : metric.value
  return `${value} ${metric.unit}`
}

/** The default filter state: the last 30 days, matching the backend default. */
export function defaultFilters() {
  const to = new Date()
  const from = new Date()
  from.setDate(from.getDate() - 29)
  const iso = (d) => d.toISOString().slice(0, 10)
  return { dateFrom: iso(from), dateTo: iso(to), status: '', severity: '', vehicleId: '' }
}

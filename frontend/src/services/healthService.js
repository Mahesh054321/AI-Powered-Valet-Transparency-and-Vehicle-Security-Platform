/**
 * Service-level API calls: liveness and readiness.
 *
 * Each service module owns one backend resource and is the only place that
 * knows its URLs and response shape.
 */

import { apiGet } from './apiClient'
import { ENDPOINTS } from './endpoints'

/** GET /api/health - process state plus a database check. */
export function fetchHealth() {
  return apiGet(ENDPOINTS.health)
}

/** GET /api/readyz - readiness only; 503 when a dependency is down. */
export function fetchReadiness() {
  return apiGet(ENDPOINTS.readiness)
}

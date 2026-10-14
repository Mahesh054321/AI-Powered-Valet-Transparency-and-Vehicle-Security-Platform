/**
 * Live service-health polling.
 *
 * Wraps React Query so any screen can show backend and database state without
 * re-implementing the polling interval or the error shape.
 *
 * Note on loading state: React Query's `isLoading` is `isPending && isFetching`,
 * which stays true across retries after a failure. That would pin the UI on a
 * spinner while the backend is actually down, so the page is given
 * `isInitialising` (no data and no error yet) and the raw query objects, and
 * decides between spinner / data / error itself.
 */

import { useQuery } from '@tanstack/react-query'
import { fetchHealth, fetchReadiness } from '../services/healthService'

const POLL_INTERVAL_MS = 15_000

export function useHealthStatus({ refetchInterval = POLL_INTERVAL_MS } = {}) {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval,
    retry: 0,
  })

  const readiness = useQuery({
    queryKey: ['readiness'],
    queryFn: fetchReadiness,
    refetchInterval,
    retry: 0,
  })

  const database = readiness.data?.checks?.database ?? null

  return {
    health: health.data ?? null,
    readiness: readiness.data ?? null,
    database,
    telemetrySource: readiness.data?.telemetry_source ?? health.data?.telemetry_source ?? null,
    /** True only while the very first response is still outstanding. */
    isInitialising: !health.data && !health.isError,
    isBackendReachable: !health.isError,
    backendError: health.error?.apiError ?? null,
    refresh: () => {
      health.refetch()
      readiness.refetch()
    },
    queries: { health, readiness },
  }
}

export default useHealthStatus

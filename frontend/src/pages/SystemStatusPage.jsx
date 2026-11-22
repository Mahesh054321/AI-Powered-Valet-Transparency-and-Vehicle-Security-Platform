import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { Badge, DataRow, ErrorNotice, Panel, Spinner } from '../components/ui'
import { useHealthStatus } from '../hooks/useHealthStatus'
import { formatUptime } from '../utils/format'

dayjs.extend(relativeTime)

/**
 * Live service status.
 *
 * Every value on this page comes from the Flask API - it polls `/api/health` and
 * `/api/readyz` every 15 seconds. Stop the backend and this page visibly
 * degrades, which is the point: it proves the two halves are really connected
 * rather than two separate mock-ups.
 */
export function SystemStatusPage() {
  const { health, readiness, database, isInitialising, backendError, refresh, queries } =
    useHealthStatus()

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>System Status</h1>
          <p className="muted">
            Check service availability and connections. Updates automatically every 15 seconds.
          </p>
        </div>
        <Badge tone={health?.status === 'up' ? 'up' : 'down'}>
          Service {health?.status ?? 'unavailable'}
        </Badge>
      </header>

      {backendError && <ErrorNotice error={backendError} onRetry={refresh} />}

      <div className="grid grid--2">
        <Panel title="Application service" subtitle="Availability and uptime">
          {isInitialising && !health ? (
            <Spinner label="Contacting API…" />
          ) : (
            <dl className="data-list">
              <DataRow
                label="Status"
                value={<Badge tone={health?.status}>{health?.status ?? 'n/a'}</Badge>}
              />
              <DataRow label="Uptime" value={formatUptime(health?.uptime_seconds)} />
              <DataRow label="Server time" value={formatTime(health?.server_time)} mono />
            </dl>
          )}
        </Panel>

        <Panel title="Connected services" subtitle="Health of the services the platform depends on">
          {isInitialising && !readiness ? (
            <Spinner label="Probing dependencies…" />
          ) : queries.readiness.isError ? (
            <div className="notice notice--error" role="alert">
              <strong>Service temporarily unavailable</strong>
              <p>
                We could not check the connected services. Please try again in a moment. If the
                problem continues, contact your administrator.
              </p>
              <button type="button" className="btn btn--ghost" onClick={refresh}>
                Retry now
              </button>
            </div>
          ) : (
            <dl className="data-list">
              <DataRow
                label="Overall"
                value={<Badge tone={readiness?.status}>{readiness?.status ?? 'n/a'}</Badge>}
              />
              <DataRow
                label="Database"
                value={<Badge tone={database?.status}>{database?.status ?? 'unknown'}</Badge>}
              />
              <DataRow
                label="Response time"
                value={database?.latency_ms != null ? `${database.latency_ms} ms` : undefined}
                mono
              />
              <DataRow
                label="Telemetry source"
                value={
                  readiness?.telemetry_source ? (
                    <Badge tone={readiness.telemetry_source}>{readiness.telemetry_source}</Badge>
                  ) : undefined
                }
              />
            </dl>
          )}
        </Panel>
      </div>

    </div>
  )
}

function formatTime(value) {
  if (!value) return undefined
  const parsed = dayjs(value)
  return parsed.isValid() ? `${parsed.format('HH:mm:ss')} (${parsed.fromNow()})` : String(value)
}

export default SystemStatusPage

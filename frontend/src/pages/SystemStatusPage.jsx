import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { Badge, DataRow, ErrorNotice, Panel, Spinner } from '../components/ui'
import { APP_CONFIG } from '../config/appConfig'
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
            Live probe of the Flask REST API and its PostgreSQL connection. Auto-refreshes every 15
            seconds.
          </p>
        </div>
        <Badge tone={health?.status === 'up' ? 'up' : 'down'}>
          API {health?.status ?? 'unreachable'}
        </Badge>
      </header>

      {backendError && <ErrorNotice error={backendError} onRetry={refresh} />}

      <div className="grid grid--2">
        <Panel title="API service" subtitle="GET /api/health">
          {isInitialising && !health ? (
            <Spinner label="Contacting API…" />
          ) : (
            <dl className="data-list">
              <DataRow
                label="Status"
                value={<Badge tone={health?.status}>{health?.status ?? 'n/a'}</Badge>}
              />
              <DataRow label="Service" value={health?.service} mono />
              <DataRow label="API version" value={health?.api_version} mono />
              <DataRow label="Environment" value={health?.environment} mono />
              <DataRow label="Uptime" value={formatUptime(health?.uptime_seconds)} />
              <DataRow label="Server time" value={formatTime(health?.server_time)} mono />
            </dl>
          )}
        </Panel>

        <Panel title="Dependencies" subtitle="GET /api/readyz">
          {isInitialising && !readiness ? (
            <Spinner label="Probing dependencies…" />
          ) : queries.readiness.isError ? (
            <div className="notice notice--error" role="alert">
              <strong>Backend unreachable</strong>
              <p>
                Start the Flask API (<code>python utils/scripts/run_api.py</code> in{' '}
                <code>backend/</code>) and this panel will recover on the next poll.
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
                label="PostgreSQL"
                value={<Badge tone={database?.status}>{database?.status ?? 'unknown'}</Badge>}
              />
              <DataRow label="Server version" value={database?.server_version} mono />
              <DataRow
                label="Round-trip latency"
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

      <Panel title="How this page proves the integration" tone="panel--info">
        <ol className="prose-list">
          <li>
            The browser calls <code>/api/health</code> — a relative URL, so Vite proxies it to
            Flask and no CORS configuration is needed in development.
          </li>
          <li>
            Flask answers with the standard envelope <code>{'{ success, data, meta }'}</code>; the
            axios interceptor unwraps it.
          </li>
          <li>
            <code>/api/readyz</code> executes a real <code>SELECT 1</code> against PostgreSQL and
            reports latency, so a stopped database is visible here immediately.
          </li>
          <li>
            Map tiles for the tracking screens come from OpenStreetMap via Leaflet — no paid API
            key required. Current tile host:{' '}
            <code>{APP_CONFIG.mapTileUrl.split('/').slice(0, 3).join('/')}</code>.
          </li>
        </ol>
      </Panel>
    </div>
  )
}

function formatTime(value) {
  if (!value) return undefined
  const parsed = dayjs(value)
  return parsed.isValid() ? `${parsed.format('HH:mm:ss')} (${parsed.fromNow()})` : String(value)
}

export default SystemStatusPage

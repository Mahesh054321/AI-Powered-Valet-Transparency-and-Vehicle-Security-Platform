/**
 * Live vehicle tracking and security dashboard.
 *
 * Shows where a vehicle in custody is, the geofence it is supposed to stay
 * inside, how the engine classified its latest fix, and every alert raised
 * during the custody window.
 *
 * Three things are deliberately unavoidable on this screen:
 *
 *  1. **The simulated-telemetry banner.** Development data must not be
 *     mistakable for hardware data, so the banner is rendered from the
 *     response's own provenance flags rather than a build-time setting, and
 *     each fix in the log repeats its source.
 *  2. **The verdict's reasoning.** `NORMAL` is not shown as "all clear" - the
 *     reasons the engine gave are listed, so a reader can see what was checked.
 *  3. **Who may act.** Triage controls appear only for managers and
 *     administrators. A valet operator can watch; they cannot close an alert
 *     about a vehicle they may have been driving.
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { useToast } from '../context/ToastContext'

import { AlertCard } from '../components/tracking/AlertCard'
import { MlVerdictCard } from '../components/tracking/MlVerdictCard'
import { SecurityModelPanel } from '../components/tracking/SecurityModelPanel'
import { TrackingMap } from '../components/tracking/TrackingMap'
import { Badge, DataRow, ErrorNotice, Panel, Spinner } from '../components/ui'
import { normaliseError } from '../services/apiClient'
import {
  acknowledgeAlert,
  fetchAlerts,
  fetchSimulatorScenarios,
  fetchTracking,
  resolveAlert,
  simulateTrack,
} from '../services/geofenceService'
import { fetchModelInfo } from '../services/securityMlService'
import { fetchTickets } from '../services/ticketService'
import { useAuth, useHasRole } from '../hooks/useAuth'
import {
  MOVEMENT_STATUS,
  MOVEMENT_STATUS_LABELS,
  MOVEMENT_STATUS_TONES,
  ROLES,
  movementStatusLabel,
  telemetrySourceLabel,
} from '../utils/constants'
import { formatDateTime } from '../utils/format'

/** Triage is narrower than viewing, for the same reason verification is. */
const TRIAGE_ROLES = [ROLES.VALET_MANAGER, ROLES.ADMIN]

/** The dashboard is a live view; poll rather than require a manual refresh. */
const REFRESH_MS = 10_000

function SimulatedBanner({ view }) {
  if (!view?.has_simulated_telemetry) return null
  return (
    <div className="notice notice--warning" role="note">
      <strong>Simulated telemetry</strong>
      <p>{view.telemetry_note}</p>
      {view.has_hardware_telemetry && (
        <p className="muted">
          Hardware fixes are present too; each entry in the log below states its
          own source.
        </p>
      )}
    </div>
  )
}

function StatusCounts({ counts }) {
  if (!counts) return null
  return (
    <div className="status-counts">
      {Object.entries(MOVEMENT_STATUS_LABELS).map(([key, label]) => (
        <div key={key} className={`status-counts__item status-counts__item--${key.toLowerCase()}`}>
          <span className="status-counts__value">{counts[key] ?? 0}</span>
          <span className="status-counts__label">{label}</span>
        </div>
      ))}
    </div>
  )
}

function SessionPicker() {
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ['tickets', 'tracking-picker'],
    queryFn: () => fetchTickets({ perPage: 50 }),
    retry: 1,
  })

  const sessions = useMemo(() => {
    const items = data?.items ?? data ?? []
    return (Array.isArray(items) ? items : [])
      .filter((ticket) => ticket.custody_session)
      .map((ticket) => ({
        custodyId: ticket.custody_session.id,
        ticketCode: ticket.ticket_code,
        status: ticket.custody_session.status,
        plate: ticket.vehicle?.plate_number,
        droppedOffAt: ticket.custody_session.dropped_off_at,
      }))
  }, [data])

  if (isPending) return <Spinner label="Loading custody sessions…" />
  if (error) return <ErrorNotice error={error.apiError ?? normaliseError(error)} onRetry={refetch} />

  if (!sessions.length) {
    return (
      <Panel title="No vehicles in custody">
        <p className="muted">
          Tracking follows a custody session. Check a vehicle in first and it will
          appear here.
        </p>
      </Panel>
    )
  }

  return (
    <Panel title="Choose a vehicle to track">
      <ul className="picker-list">
        {sessions.map((session) => (
          <li key={session.custodyId}>
            <a className="picker-list__link" href={`/tracking/${session.custodyId}`}>
              <strong>{session.plate ?? 'Vehicle'}</strong>
              <span className="muted">
                {session.ticketCode} · {session.status}
                {session.droppedOffAt ? ` · dropped off ${formatDateTime(session.droppedOffAt)}` : ''}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function SimulatorPanel({ custodyId, onDone }) {
  const queryClient = useQueryClient()
  const [scenario, setScenario] = useState('unauthorised')
  const [seed, setSeed] = useState('42')

  const scenarios = useQuery({
    queryKey: ['gps', 'simulator-scenarios'],
    queryFn: fetchSimulatorScenarios,
    retry: 1,
  })

  const toast = useToast()


  const run = useMutation({
    mutationFn: () =>
      simulateTrack(custodyId, { scenario, seed: seed === '' ? null : Number(seed) }),
    onSuccess: (data) => {
      // The simulation raises alerts as a side effect, and those land in a
      // different section of the page, so the count is reported here rather than
      // left for the reader to notice.
      toast.success(
        `Track generated — ${data.fix_count ?? 0} fixes`,
        data.alerts_raised
          ? `${data.alerts_raised} security alert${data.alerts_raised === 1 ? '' : 's'} raised by the geofence check.`
          : 'No geofence breaches on this track.',
      )
      queryClient.invalidateQueries({ queryKey: ['gps', 'tracking', custodyId] })
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      onDone?.()
    },
    onError: (error) => toast.error('Could not generate a track', normaliseError(error).message),
  })

  const result = run.data

  return (
    <Panel
      title="Development telemetry simulator"
      subtitle="Generates a realistic track and runs it through the real geofence engine. Every fix is recorded as SIMULATED."
    >
      <div className="form-row">
        <label className="field">
          <span className="field__label">Scenario</span>
          <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
            {(scenarios.data?.scenarios ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">Seed</span>
          <input
            value={seed}
            onChange={(event) => setSeed(event.target.value)}
            inputMode="numeric"
            aria-describedby="seed-hint"
          />
          <span className="field__hint" id="seed-hint">
            The same seed always produces the same track, so a demo is repeatable.
          </span>
        </label>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => run.mutate()}
          disabled={run.isPending}
        >
          {run.isPending ? 'Generating…' : 'Generate and ingest'}
        </button>
      </div>

      <ErrorNotice error={run.error ? normaliseError(run.error) : null} />

      {result && (
        <div className="notice notice--info">
          <strong>
            Ingested {result.fix_count} simulated fixes · {result.status_counts
              ? Object.entries(result.status_counts)
                  .map(([key, value]) => `${value} ${movementStatusLabel(key)}`)
                  .join(', ')
              : ''}
          </strong>
          <p>{result.notes?.[0]}</p>
          {result.alerts_raised?.length > 0 && (
            <p className="muted">
              Raised {result.alerts_raised.length} alert
              {result.alerts_raised.length === 1 ? '' : 's'}:{' '}
              {[...new Set(result.alerts_raised.map((a) => a.alert_type))].join(', ')}
            </p>
          )}
        </div>
      )}
    </Panel>
  )
}

export function TrackingPage() {
  const { custodyId } = useParams()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const canTriage = useHasRole(TRIAGE_ROLES)
  const isStaff = useHasRole([ROLES.VALET_OPERATOR, ROLES.VALET_MANAGER, ROLES.ADMIN])

  const tracking = useQuery({
    queryKey: ['gps', 'tracking', custodyId],
    queryFn: () => fetchTracking(custodyId),
    enabled: Boolean(custodyId),
    refetchInterval: REFRESH_MS,
    retry: 1,
  })

  const alerts = useQuery({
    queryKey: ['alerts', custodyId ?? 'all'],
    queryFn: () => fetchAlerts(custodyId ? { custodySessionId: custodyId } : {}),
    enabled: Boolean(custodyId),
    refetchInterval: REFRESH_MS,
    retry: 1,
  })

  // Model provenance for staff. It changes only when the artifact is retrained,
  // so it is fetched once and not polled with the live telemetry.
  const modelInfo = useQuery({
    queryKey: ['ml', 'model'],
    queryFn: fetchModelInfo,
    enabled: isStaff,
    staleTime: 5 * 60 * 1000,
    retry: 0,
  })

  const toast = useToast()


  const triage = useMutation({
    mutationFn: ({ alert, action, notes, dismiss }) =>
      action === 'acknowledge'
        ? acknowledgeAlert(alert.id)
        : resolveAlert(alert.id, { notes, dismiss }),
    onSuccess: (_data, variables) => {
      toast.success(
        variables.action === 'acknowledge' ? 'Alert acknowledged' : 'Alert closed',
        variables.action === 'acknowledge'
          ? 'Recorded as seen. It stays open until it is resolved.'
          : 'Resolved and recorded in the audit log with your name against it.',
      )
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      queryClient.invalidateQueries({ queryKey: ['gps', 'tracking', custodyId] })
    },
    onError: (error) => toast.error('Could not update the alert', normaliseError(error).message),
  })

  if (!custodyId) {
    return (
      <div className="page">
        <header className="page__header">
          <h1>Live tracking</h1>
          <p className="muted">
            Position, geofence and security alerts for a vehicle in custody.
          </p>
        </header>
        <SessionPicker />
      </div>
    )
  }

  if (tracking.isPending) return <Spinner label="Loading tracking data…" />

  const view = tracking.data
  const latest = view?.latest_fix

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>{view?.vehicle?.plate_number ?? 'Vehicle'}</h1>
          <p className="muted">
            {view?.vehicle?.make} {view?.vehicle?.model} · custody{' '}
            <span className="mono">{custodyId.slice(0, 8)}…</span>
            {view?.zone ? ` · zone ${view.zone.code}` : ' · no zone assigned'}
          </p>
        </div>
        {latest && (
          <Badge tone={MOVEMENT_STATUS_TONES[latest.movement_status] ?? 'badge--muted'}>
            {movementStatusLabel(latest.movement_status)}
          </Badge>
        )}
      </header>

      <ErrorNotice error={tracking.error ? normaliseError(tracking.error) : null} onRetry={tracking.refetch} />

      <SimulatedBanner view={view} />

      <div className="tracking-layout">
        <Panel
          title="Position and geofence"
          subtitle={
            view?.zone
              ? `${view.zone.name} · boundary ${Math.round(view.zone.effective_radius_metres)} m · tolerance ${view.zone.buffer_metres} m`
              : 'No geofence assigned to this session'
          }
        >
          <TrackingMap
            zone={view?.zone}
            latestFix={latest}
            track={view?.track ?? []}
            status={latest?.movement_status ?? MOVEMENT_STATUS.NORMAL}
          />
        </Panel>

        <div className="tracking-layout__side">
          <Panel title="Current reading">
            {latest ? (
              <>
                <DataRow label="Movement status" value={movementStatusLabel(latest.movement_status)} />
                <DataRow
                  label="Speed"
                  value={latest.speed_kmph != null ? `${Number(latest.speed_kmph).toFixed(0)} km/h` : 'not reported'}
                />
                <DataRow
                  label="Distance from boundary"
                  value={
                    latest.distance_from_zone_metres != null
                      ? `${Math.round(latest.distance_from_zone_metres)} m ${
                          latest.is_within_zone ? 'inside' : 'outside'
                        }`
                      : 'not measured'
                  }
                />
                <DataRow label="Within zone" value={latest.is_within_zone ? 'Yes' : 'No'} />
                <DataRow label="Fix recorded" value={formatDateTime(latest.recorded_at)} />
                <DataRow label="Source" value={telemetrySourceLabel(latest.source)} />
                {latest.is_simulated && (
                  <p className="notice notice--warning notice--inline">
                    This reading is <strong>simulated</strong> telemetry, not hardware data.
                  </p>
                )}
              </>
            ) : (
              <p className="muted">No position has been recorded yet.</p>
            )}
          </Panel>

          {/* Shown beneath the measurement rather than replacing it: the rule
              verdict is what was stored, and a prototype's opinion is context. */}
          {isStaff && (
            <Panel title="Prototype classifier" subtitle="Advisory only">
              <MlVerdictCard
                ml={latest?.ml ?? null}
                ruleStatus={latest?.movement_status ?? null}
                escalated={Boolean(view?.escalated_by_ml)}
              />
            </Panel>
          )}

          <Panel title="Track summary">
            <StatusCounts counts={view?.status_counts} />
            <DataRow label="Fixes recorded" value={view?.fix_count ?? 0} />
            <DataRow label="Open alerts" value={view?.open_alert_count ?? 0} />
            <DataRow label="Last updated" value={view?.last_updated_at ? formatDateTime(view.last_updated_at) : 'never'} />
            <DataRow label="Sources seen" value={(view?.sources ?? []).map(telemetrySourceLabel).join(', ') || 'none'} />
            <p className="muted tracking-layout__note">
              Verdicts come from a stated rule set over distance, speed and
              movement pattern. A prototype classifier may raise a verdict but
              never lower it, so a geofence breach always alerts. Every fix
              keeps the inputs that produced it.
            </p>
          </Panel>

          {isStaff && <SimulatorPanel custodyId={custodyId} />}
        </div>
      </div>

      <Panel
        title="Security alerts"
        subtitle={
          canTriage
            ? 'Acknowledge, resolve, or dismiss as a false positive.'
            : 'Only a manager or administrator can act on an alert.'
        }
        actions={
          <button
            type="button"
            className="btn btn--ghost btn--small"
            onClick={() => alerts.refetch()}
            disabled={alerts.isFetching}
          >
            Refresh
          </button>
        }
      >
        <ErrorNotice error={alerts.error ? normaliseError(alerts.error) : null} onRetry={alerts.refetch} />
        {triage.error && <ErrorNotice error={normaliseError(triage.error)} />}

        {alerts.isPending ? (
          <Spinner label="Loading alerts…" />
        ) : (alerts.data?.alerts ?? []).length === 0 ? (
          <p className="muted">No alerts have been raised for this custody session.</p>
        ) : (
          <div className="alert-list">
            {(alerts.data?.alerts ?? []).map((alert) => (
              <AlertCard
                key={alert.id}
                alert={alert}
                canTriage={canTriage}
                busy={triage.isPending}
                onAcknowledge={(item) => triage.mutate({ alert: item, action: 'acknowledge' })}
                onResolve={(item, { notes, dismiss }) =>
                  triage.mutate({ alert: item, action: 'resolve', notes, dismiss })
                }
              />
            ))}
          </div>
        )}
      </Panel>

      {/* Provenance for the classifier whose opinion appears above. Rendered for
          staff only: it is an internal detail about the detector, and showing
          training metrics to a customer would invite them to be read as a
          warranty about their vehicle. */}
      {isStaff && (
        <SecurityModelPanel
          info={modelInfo.data ?? null}
          loading={modelInfo.isPending}
          error={modelInfo.error ? normaliseError(modelInfo.error) : null}
        />
      )}

      {user && (
        <p className="muted page__footnote">
          Signed in as {user.email} ({user.role}). The map tiles come from
          OpenStreetMap and need network access; the boundary, track and markers
          are drawn locally.
        </p>
      )}
    </div>
  )
}

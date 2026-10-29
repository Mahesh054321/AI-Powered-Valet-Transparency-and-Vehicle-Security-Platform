/**
 * Live vehicle tracking, smart geofencing and security dashboard.
 *
 * Shows where a vehicle in custody is, the geofence it is supposed to stay
 * inside, how the engine classified its latest fix, and every alert raised
 * during the custody window.
 *
 * Real-time SSE live updates, real GPS sources (Mobile Browser GPS vs Vehicle Tracker),
 * hardware sensor telemetry, and user-defined Safe Zone Builder.
 */

import { useCallback, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { useToast } from '../context/ToastContext'

import { AlertCard } from '../components/tracking/AlertCard'
import { MlVerdictCard } from '../components/tracking/MlVerdictCard'
import { SafeZoneBuilder } from '../components/tracking/SafeZoneBuilder'
import { SecurityModelPanel } from '../components/tracking/SecurityModelPanel'
import { TrackingMap } from '../components/tracking/TrackingMap'
import { Badge, DataRow, EmptyState, ErrorNotice, Panel, Spinner } from '../components/ui'
import { normaliseError } from '../services/apiClient'
import {
  acknowledgeAlert,
  fetchAlerts,
  fetchSimulatorScenarios,
  fetchTracking,
  investigateAlert,
  reopenAlert,
  resolveAlert,
  simulateTrack,
} from '../services/geofenceService'
import { fetchModelInfo } from '../services/securityMlService'
import { fetchTickets } from '../services/ticketService'
import { useAuth, useHasRole } from '../hooks/useAuth'
import { useLiveGpsStream } from '../hooks/useLiveGpsStream'
import { useMobileGeolocation } from '../hooks/useMobileGeolocation'
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

function gpsStatusLabel(view, latest) {
  const freshness = view?.freshness
  const state = {
    LIVE: 'Connected',
    STALE: 'Stale',
    OFFLINE: 'Offline',
    LOCATION_UNCERTAIN: 'Error — location accuracy is uncertain',
  }[freshness] ?? 'Offline'

  if (view?.gps_device?.connection_status === 'ERROR') return 'Error'
  if (!latest) {
    if (view?.tracking_method === 'NOT_CONFIGURED') return 'Not configured'
    if (view?.gps_device?.connection_status === 'PENDING_VERIFICATION') {
      return 'Pending verification'
    }
    return 'Offline'
  }
  if (latest.source === 'SIMULATED') return `Simulated demo data · ${state.toLowerCase()}`
  if (latest.source === 'MANUAL') return 'Manual reading'
  return state
}

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
      .filter(
        (ticket) =>
          ticket.custody_session?.id &&
          ticket.custody_session.status !== 'CHECKED_OUT',
      )
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
      <ul className="picker-list tracking-session-list">
        {sessions.map((session) => (
          <li className="tracking-session-list__item" key={session.custodyId}>
            <Link className="picker-list__link" to={`/tracking/${session.custodyId}`}>
              <strong>{session.plate ?? 'Vehicle'}</strong>
              <span className="muted">
                {session.ticketCode} · {session.status}
                {session.droppedOffAt ? ` · dropped off ${formatDateTime(session.droppedOffAt)}` : ''}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function MobileTrackingPanel({ custodyId, onNewFix, onPositionChange }) {
  const toast = useToast()
  const {
    isSupported,
    isTracking,
    permissionState,
    currentPosition,
    accuracyMetres,
    lastTransmittedAt,
    lastError,
    startTracking,
    stopTracking,
    disclosure,
  } = useMobileGeolocation(custodyId, {
    onFixSuccess: (result) => {
      onNewFix?.(result)
    },
    onPositionChange,
    onFixError: (err) => {
      toast.error('Mobile GPS upload failed', err.message)
    },
  })

  if (!isSupported) {
    return (
      <Panel title="Mobile Browser Tracking" subtitle="Location sensor unavailable">
        <p className="muted">Geolocation is not supported in this browser.</p>
      </Panel>
    )
  }

  return (
    <Panel
      title="Mobile GPS Tracking"
      subtitle="Authorized live geolocation from your mobile device"
      actions={
        <button
          type="button"
          className={`btn btn--small ${isTracking ? 'btn--secondary' : 'btn--primary'}`}
          onClick={isTracking ? stopTracking : startTracking}
        >
          {isTracking ? 'Stop Tracking Phone' : 'Start Phone Tracking'}
        </button>
      }
    >
      <div className="notice notice--info">
        <strong>Important Disclosure:</strong>
        <p>{disclosure}</p>
      </div>

      <div className="data-row-group">
        <DataRow label="Phone GPS Status" value={isTracking ? 'Actively Transmitting' : 'Idle / Off'} />
        <DataRow label="Browser Permission" value={permissionState} />
        {accuracyMetres != null && (
          <DataRow
            label="Location Accuracy"
            value={`±${Math.round(accuracyMetres)} m (${accuracyMetres > 35 ? 'UNCERTAIN' : 'ACCURATE'})`}
          />
        )}
        {currentPosition && (
          <DataRow
            label="Current phone location"
            value={`${currentPosition.latitude.toFixed(5)}, ${currentPosition.longitude.toFixed(5)}`}
          />
        )}
        {lastTransmittedAt && (
          <DataRow label="Last Transmission" value={lastTransmittedAt.toLocaleTimeString()} />
        )}
      </div>

      {lastError && <p className="notice notice--warning notice--inline">{lastError}</p>}
    </Panel>
  )
}

function HardwareSensorsPanel({ sensors }) {
  if (!sensors) return null

  const sensorEntries = sensors.sensors
    ? Object.entries(sensors.sensors)
    : [
        ['tamper', { name: 'Enclosure Tamper', state: sensors.tamper_triggered ? 'TAMPER_ALERT' : 'SECURE', supported: true }],
        ['power', { name: 'Power Line', state: sensors.power_connected ? 'MAIN_POWER' : 'BATTERY_DISCONNECT', supported: true }],
        ['ignition', { name: 'Ignition', state: sensors.ignition ? 'ON' : 'OFF', supported: false }],
        ['shock', { name: 'Vibration & Shock', state: sensors.shock_detected ? 'SHOCK_DETECTED' : 'NORMAL', supported: false }],
        ['door', { name: 'Door Contact', state: sensors.door_open ? 'DOOR_OPEN' : 'CLOSED', supported: false }],
        ['mount', { name: 'Device Mount', state: 'ATTACHED', supported: false }],
      ]

  return (
    <Panel
      title="Hardware Tampering & Sensors"
      subtitle={sensors.diagnostic_message || 'Telemetry diagnostics for physical GPS tracker'}
    >
      <div className="hardware-sensors-grid">
        {sensorEntries.map(([key, sensor]) => (
          <div key={key} className={`sensor-item sensor-item--${(sensor.state || '').toLowerCase()}`}>
            <span className="sensor-item__name">{sensor.name}</span>
            <span className="sensor-item__state">{sensor.state || 'UNAVAILABLE'}</span>
            {!sensor.supported && (
              <span className="sensor-item__note muted">
                Requires: {sensor.required_hardware || 'Compatible physical tracker'}
              </span>
            )}
          </div>
        ))}
      </div>
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
  const isCustomer = useHasRole(ROLES.CUSTOMER)
  const canManageGeofence = useHasRole([ROLES.VALET_MANAGER, ROLES.ADMIN, ROLES.CUSTOMER])

  const [showZoneBuilder, setShowZoneBuilder] = useState(false)
  const [mobileFix, setMobileFix] = useState(null)
  const handleMobilePosition = useCallback(
    (position) => setMobileFix({ custodyId, position }),
    [custodyId],
  )
  const currentPhoneFix =
    mobileFix && mobileFix.custodyId === custodyId ? mobileFix.position : null

  // Real-time SSE streaming hook
  const { streamStatus, liveView, reconnect } = useLiveGpsStream(custodyId, {
    enabled: Boolean(custodyId),
    onAlert: (newAlert) => {
      toast.error(`Security Alert: ${newAlert.title}`, newAlert.reason)
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
    onFix: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
  })

  // Regular query acting as initial source or fallback polling
  const tracking = useQuery({
    queryKey: ['gps', 'tracking', custodyId],
    queryFn: () => fetchTracking(custodyId),
    enabled: Boolean(custodyId),
    refetchInterval: streamStatus === 'connected' ? false : REFRESH_MS,
    retry: 1,
  })

  const alerts = useQuery({
    queryKey: ['alerts', custodyId ?? 'all'],
    queryFn: () => fetchAlerts(custodyId ? { custodySessionId: custodyId } : {}),
    enabled: Boolean(custodyId),
    refetchInterval: REFRESH_MS,
    retry: 1,
  })

  const modelInfo = useQuery({
    queryKey: ['ml', 'model'],
    queryFn: fetchModelInfo,
    enabled: isStaff,
    staleTime: 5 * 60 * 1000,
    retry: 0,
  })

  const toast = useToast()

  const triage = useMutation({
    mutationFn: ({ alert, action, notes, dismiss, reason }) => {
      if (action === 'acknowledge') return acknowledgeAlert(alert.id)
      if (action === 'investigate') return investigateAlert(alert.id, { notes })
      if (action === 'reopen') return reopenAlert(alert.id, { reason })
      return resolveAlert(alert.id, { notes, dismiss })
    },
    onSuccess: (_data, variables) => {
      const msg =
        variables.action === 'acknowledge'
          ? 'Alert acknowledged'
          : variables.action === 'investigate'
          ? 'Alert marked as investigating'
          : variables.action === 'reopen'
          ? 'Alert reopened'
          : 'Alert closed'
      toast.success(msg, 'Updated and recorded in the audit log.')
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

  if (tracking.isPending && !liveView) return <Spinner label="Loading tracking data…" />

  // Merge liveView from SSE with tracking.data from REST
  const view = liveView || tracking.data
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
        <div className="header-actions">
          {latest && (
            <Badge tone={MOVEMENT_STATUS_TONES[latest.movement_status] ?? 'badge--muted'}>
              {movementStatusLabel(latest.movement_status)}
            </Badge>
          )}
          {canManageGeofence && (
            <button
              type="button"
              className="btn btn--secondary btn--small"
              onClick={() => setShowZoneBuilder((prev) => !prev)}
            >
              {showZoneBuilder ? 'Close Safe Zone Builder' : 'Configure Safe Zone'}
            </button>
          )}
        </div>
      </header>

      <ErrorNotice error={tracking.error ? normaliseError(tracking.error) : null} onRetry={tracking.refetch} />

      <SimulatedBanner view={view} />

      {/* Safe Zone Builder Interactive Drawer */}
      {showZoneBuilder && (
        <SafeZoneBuilder
          initialCenter={view?.zone?.centre || latest}
          vehicleId={view?.vehicle?.id}
          custodyId={custodyId}
          onCreated={() => setShowZoneBuilder(false)}
          onCancel={() => setShowZoneBuilder(false)}
        />
      )}

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
            phoneFix={currentPhoneFix ?? view?.phone_fix}
            track={view?.track ?? []}
            status={latest?.movement_status ?? MOVEMENT_STATUS.NORMAL}
            freshness={view?.freshness ?? 'OFFLINE'}
            boundaryState={view?.boundary_state}
            streamStatus={streamStatus}
          />
        </Panel>

        <div className="tracking-layout__side">
          <Panel title="Current reading">
            {custodyId && ['disconnected', 'reconnecting'].includes(streamStatus) && (
              <button
                type="button"
                className="btn btn--secondary btn--small"
                onClick={reconnect}
              >
                Reconnect live updates
              </button>
            )}
            <DataRow label="GPS status" value={gpsStatusLabel(view, latest)} />
            {view?.gps_device && (
              <DataRow
                label="Hardware connection"
                value={view.gps_device.connection_status}
              />
            )}
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
                <DataRow label="Last successful fix" value={formatDateTime(latest.recorded_at)} />
                <DataRow label="GPS source" value={telemetrySourceLabel(latest.source)} />
                {latest.is_simulated && (
                  <p className="notice notice--warning notice--inline">
                    This reading is <strong>simulated</strong> telemetry, not hardware data.
                  </p>
                )}
              </>
            ) : (
              <EmptyState
                icon="◎"
                title="No position has been recorded yet"
                body="The vehicle has not reported a GPS fix. Simulated telemetry can be generated from the panel above."
              />
            )}
          </Panel>

          {/* Real Mobile GPS Tracking Panel */}
          {isCustomer && (
            <MobileTrackingPanel
              custodyId={custodyId}
              onPositionChange={handleMobilePosition}
              onNewFix={() => {
                queryClient.invalidateQueries({ queryKey: ['gps', 'tracking', custodyId] })
              }}
            />
          )}

          {/* Physical Tracker Hardware Sensors */}
          {view?.sensors && <HardwareSensorsPanel sensors={view.sensors} />}

          {/* Shown beneath the measurement rather than replacing it */}
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
          <EmptyState
            icon="✓"
            title="No alerts for this session"
            body="No geofence breach or security event has been recorded against this custody session."
          />
        ) : (
          <div className="alert-list">
            {(alerts.data?.alerts ?? []).map((alert) => (
              <AlertCard
                key={alert.id}
                alert={alert}
                canTriage={canTriage}
                busy={triage.isPending}
                onAcknowledge={(item) => triage.mutate({ alert: item, action: 'acknowledge' })}
                onInvestigate={(item) => triage.mutate({ alert: item, action: 'investigate' })}
                onResolve={(item, { notes, dismiss }) =>
                  triage.mutate({ alert: item, action: 'resolve', notes, dismiss })
                }
                onReopen={(item) => triage.mutate({ alert: item, action: 'reopen' })}
              />
            ))}
          </div>
        )}
      </Panel>

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

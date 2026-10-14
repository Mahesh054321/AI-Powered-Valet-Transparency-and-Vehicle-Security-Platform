/**
 * Valet Operator Dashboard.
 *
 * The screen a valet works from: what is arriving, what we are holding, what
 * needs fetching, what is waiting to be handed back, and anything that needs
 * attention.
 *
 * Three decisions shape this file:
 *
 * **One composed read, polled.** Seven panels fetched separately would be read
 * at seven moments, and an operator acts on this data — a vehicle list and an
 * alert list that disagree is worse than either alone. It polls rather than
 * being pushed because there is no websocket channel in this project, and the
 * page says so instead of implying it is live.
 *
 * **No writes are performed here.** Every action calls the endpoint that
 * already exists for it (`PATCH /custody-sessions`, `POST /tickets/:id/complete`,
 * `POST /inspections/pickup`, the alert triage routes). That keeps one state
 * machine and one audit path. A dashboard with its own write logic would be a
 * second, less-tested copy of those rules, and the audit trail would depend on
 * which copy happened to run.
 *
 * **Buttons come from `capabilities`.** A valet operator may move a car but may
 * not close a security alert; acknowledging and resolving are manager acts. The
 * flags are published by the backend from the same role tuples the endpoints
 * enforce, so a button is shown exactly when pressing it would succeed. A
 * button that 403s is worse than no button — it tells an operator the action is
 * available and then refuses them in front of a customer.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertBanner, Badge, ConfirmDialog, ErrorNotice, Spinner } from '../components/ui'
import { IncomingTable, VehicleTable } from '../components/valet/VehicleTable'
import { AlertList, InspectionTable, QueueList, ZoneStrip } from '../components/valet/Sections'
import { SectionShell } from '../components/valet/SectionShell'
import {
  acknowledgeAlert,
  completePickup,
  fetchStaffDashboard,
  moveVehicle,
  resolveAlert,
} from '../services/staffDashboardService'
import { capturePickup } from '../services/inspectionService'

/** How often the screen re-reads. Short enough to feel prompt, long enough
 *  that a row does not reorder while an operator is reading it. */
const POLL_MS = 5_000

/** The angles the condition pipeline expects, used for the pickup upload. */
const PICKUP_ANGLES = ['FRONT', 'REAR', 'LEFT', 'RIGHT']

export function ValetDashboardPage() {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState(null)
  const [notice, setNotice] = useState(null)
  const [pickupFiles, setPickupFiles] = useState({})

  const query = useQuery({
    queryKey: ['staff-dashboard'],
    queryFn: fetchStaffDashboard,
    refetchInterval: POLL_MS,
    // Keep the last good screen visible while a refetch is in flight, so the
    // tables do not flash empty every five seconds.
    placeholderData: (previous) => previous,
  })

  const data = query.data
  const caps = data?.capabilities ?? {}
  const labels = data?.display_status_labels ?? {}

  /** Every mutation refreshes the whole snapshot: one action can move a row
   *  between sections, free a zone slot and clear a badge at once. */
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['staff-dashboard'] })

  const move = useMutation({
    mutationFn: ({ custodyId, status }) => moveVehicle(custodyId, status),
    onSuccess: (_result, variables) => {
      setPending(null)
      setNotice({
        tone: 'success',
        title: 'Status updated',
        body: `Custody session moved to ${variables.status}. Recorded in the audit log.`,
      })
      refresh()
    },
  })

  const complete = useMutation({
    mutationFn: ({ ticketId, odometer, conditionAccepted }) =>
      completePickup(ticketId, { odometer, conditionAccepted }),
    onSuccess: () => {
      setPending(null)
      setNotice({
        tone: 'success',
        title: 'Pickup completed',
        body: 'Custody closed, zone slot freed and the ticket completed. Recorded in the audit log.',
      })
      refresh()
    },
  })

  const uploadPickup = useMutation({
    mutationFn: ({ custodyId, photos }) => capturePickup({ custodyId, photos }),
    onSuccess: () => {
      setPending(null)
      setPickupFiles({})
      setNotice({
        tone: 'success',
        title: 'Pickup photos captured',
        body:
          'Each angle was compared against its drop-off counterpart. ' +
          'Any finding is marked potential until a manager verifies it.',
      })
      refresh()
    },
  })

  const acknowledge = useMutation({
    mutationFn: (alertId) => acknowledgeAlert(alertId),
    onSuccess: () => {
      setPending(null)
      setNotice({
        tone: 'info',
        title: 'Alert acknowledged',
        body: 'Recorded as seen. It stays open until it is resolved.',
      })
      refresh()
    },
  })

  const resolve = useMutation({
    mutationFn: ({ alertId, notes }) => resolveAlert(alertId, { notes }),
    onSuccess: () => {
      setPending(null)
      setNotice({
        tone: 'success',
        title: 'Alert resolved',
        body: 'Closed and recorded in the audit log with your note.',
      })
      refresh()
    },
  })

  const busy = move.isPending || complete.isPending || uploadPickup.isPending ||
    acknowledge.isPending || resolve.isPending
  const mutationError =
    move.error || complete.error || uploadPickup.error || acknowledge.error || resolve.error

  const confirm = () => {
    if (!pending) return
    switch (pending.kind) {
      case 'move':
        move.mutate({ custodyId: pending.custodyId, status: pending.status })
        return
      case 'complete':
        complete.mutate({
          ticketId: pending.ticketId,
          odometer: pending.odometer,
          conditionAccepted: true,
        })
        return
      case 'upload':
        uploadPickup.mutate({ custodyId: pending.custodyId, photos: pending.photos })
        return
      case 'acknowledge':
        acknowledge.mutate(pending.alertId)
        return
      case 'resolve':
        resolve.mutate({ alertId: pending.alertId, notes: pending.notes })
        return
      default:
        setPending(null)
    }
  }

  /** Actions offered for a row, derived from where the car currently is.
   *  The backend still enforces the transitions; this only avoids offering
   *  jumps it would refuse. */
  const rowActions = (row, { readyForHandover = false } = {}) => {
    if (!caps.can_move_vehicle) return null
    const status = row.custody_status

    if (readyForHandover) {
      return (
        <button
          type="button"
          className="btn btn--sm"
          onClick={() =>
            setPending({
              kind: 'complete',
              ticketId: row.ticket_id,
              title: 'Complete this pickup?',
              plate: row.vehicle?.plate_number,
              customer: row.customer?.name,
            })
          }
        >
          Complete pickup
        </button>
      )
    }

    return (
      <>
        {status === 'RETRIEVAL_REQUESTED' && (
          <button
            type="button"
            className="btn btn--sm"
            onClick={() =>
              setPending({
                kind: 'move',
                custodyId: row.custody_session_id,
                status: 'RETRIEVAL_IN_PROGRESS',
                title: 'Start this retrieval?',
                plate: row.vehicle?.plate_number,
                detail: 'The car will be marked as being brought to the handover point.',
              })
            }
          >
            Start retrieval
          </button>
        )}
        {(status === 'PARKED' || status === 'CHECKED_IN') && (
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() =>
              setPending({
                kind: 'move',
                custodyId: row.custody_session_id,
                status: 'IN_TRANSIT',
                title: 'Move this vehicle?',
                plate: row.vehicle?.plate_number,
                detail: 'Marks the car as being driven within the site.',
              })
            }
          >
            Move
          </button>
        )}
        {status !== 'READY_FOR_PICKUP' && (
          <button
            type="button"
            className="btn btn--sm"
            onClick={() =>
              setPending({
                kind: 'move',
                custodyId: row.custody_session_id,
                status: 'READY_FOR_PICKUP',
                title: 'Mark this vehicle ready?',
                plate: row.vehicle?.plate_number,
                customer: row.customer?.name,
                detail: 'The customer will see it as ready for collection.',
              })
            }
          >
            Mark ready
          </button>
        )}
      </>
    )
  }

  if (query.isPending) {
    return (
      <div className="page">
        <Spinner label="Loading the operator dashboard…" />
      </div>
    )
  }

  if (query.isError) {
    return (
      <div className="page">
        <ErrorNotice error={query.error} onRetry={() => query.refetch()} />
      </div>
    )
  }

  const active = data.active_vehicles
  const pickups = data.pending_pickups
  const queue = data.retrieval_queue
  const alerts = data.security_alerts
  const inspections = data.inspections

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Valet Operator Dashboard</h1>
          <p className="muted">
            Vehicles in your custody, what needs fetching, and what needs attention.
          </p>
        </div>
        <div className="page__actions">
          <Badge tone="badge--info">{active?.count ?? 0} in custody</Badge>
          {(queue?.count ?? 0) > 0 && <Badge tone="badge--warning">{queue.count} to fetch</Badge>}
          {(alerts?.count ?? 0) > 0 && <Badge tone="badge--danger">{alerts.count} alerts</Badge>}
          {caps.can_check_in && (
            <Link className="btn btn--ghost" to="/checkin">
              Check in a vehicle
            </Link>
          )}
        </div>
      </header>

      {notice && (
        <AlertBanner tone={notice.tone} title={notice.title} onDismiss={() => setNotice(null)}>
          <p>{notice.body}</p>
        </AlertBanner>
      )}

      {/* Stated rather than implied. There is no push channel in this project,
          and an operator needs to know how stale the screen can be. */}
      <AlertBanner tone="info" title="Refreshed automatically">
        <p>
          This screen re-reads every {POLL_MS / 1000} seconds. It is polled, not
          pushed — there is no realtime channel, so a change elsewhere appears
          here within one refresh.
        </p>
      </AlertBanner>

      {mutationError && <ErrorNotice error={mutationError} />}

      {/* ---------------------------- ARRIVALS ---------------------------- */}
      <SectionShell
        section={data.incoming}
        title="Incoming vehicles"
        subtitle="Tickets issued, vehicle not yet handed over"
        loading={query.isFetching && !data}
      >
        <IncomingTable rows={data.incoming?.items} />
      </SectionShell>

      {/* ------------------------- ACTIVE VEHICLES ------------------------ */}
      <SectionShell
        section={active}
        title="Active vehicles"
        subtitle="Everything currently in custody, with GPS and security state"
      >
        <VehicleTable
          rows={active?.items}
          labels={labels}
          actionsFor={(row) => rowActions(row)}
          emptyMessage="No vehicles are in custody."
        />
      </SectionShell>

      {/* ------------------------- PENDING PICKUPS ------------------------ */}
      <SectionShell
        section={pickups}
        title="Pending pickups"
        subtitle="Staged at the handover point, waiting for the customer to collect"
      >
        <VehicleTable
          rows={pickups?.items}
          labels={labels}
          actionsFor={(row) => rowActions(row, { readyForHandover: true })}
          emptyMessage="No vehicles are staged for handover."
        />
      </SectionShell>

      {/* -------------------------- RETRIEVAL QUEUE ----------------------- */}
      <SectionShell
        section={queue}
        title="Retrieval queue"
        subtitle="Cars a valet still has to go and fetch, oldest request first"
        actions={
          queue?.delivery_note ? <Badge tone="badge--muted">polled</Badge> : null
        }
      >
        <QueueList
          items={queue?.items}
          onDispatch={(item) =>
            setPending({
              kind: 'move',
              custodyId: item.custody_session_id,
              status: 'RETRIEVAL_IN_PROGRESS',
              title: 'Start this retrieval?',
              plate: item.vehicle?.plate_number,
              detail: 'The car will be marked as being brought to the handover point.',
            })
          }
        />
      </SectionShell>

      {/* ------------------------- SECURITY ALERTS ------------------------ */}
      <SectionShell
        section={alerts}
        title="Security alerts"
        subtitle="Open alerts across every vehicle in custody"
        actions={
          caps.can_triage_alerts ? null : (
            <Badge tone="badge--muted">read-only for your role</Badge>
          )
        }
      >
        <AlertList
          alerts={alerts?.items}
          canTriage={caps.can_triage_alerts}
          onAcknowledge={(alert) =>
            setPending({
              kind: 'acknowledge',
              alertId: alert.id,
              title: 'Acknowledge this alert?',
              detail: `${alert.severity} — ${alert.title}. It stays open until resolved.`,
            })
          }
          onResolve={(alert) =>
            setPending({
              kind: 'resolve',
              alertId: alert.id,
              title: 'Resolve this alert?',
              notes: 'Resolved from the operator dashboard.',
              detail: `${alert.severity} — ${alert.title}. This closes it permanently.`,
            })
          }
        />
      </SectionShell>

      {/* ----------------------- VEHICLE INSPECTIONS ---------------------- */}
      <SectionShell
        section={inspections}
        title="Vehicle inspections"
        subtitle="Drop-off and pickup photo sets, and any findings awaiting review"
      >
        <InspectionTable
          rows={inspections?.items}
          onView={(row) => {
            // The full comparison lives on the existing condition page, which
            // already renders both photo sets and the evidence crops.
            window.location.assign(`/condition/${row.custody_session_id}`)
          }}
        />

        {caps.can_capture_inspection && (
          <PickupUpload
            rows={inspections?.items}
            files={pickupFiles}
            onFiles={setPickupFiles}
            onSubmit={(custodyId, photos) =>
              setPending({
                kind: 'upload',
                custodyId,
                photos,
                title: 'Upload these pickup photos?',
                detail:
                  'Each angle will be compared against its drop-off photo. ' +
                  'Anything the detector flags is marked potential, not confirmed.',
              })
            }
          />
        )}
      </SectionShell>

      {/* ------------------------------ ZONES ----------------------------- */}
      <SectionShell section={data.zones} title="Parking zones" subtitle="Occupancy across the site">
        <ZoneStrip zones={data.zones?.items} />
      </SectionShell>

      <p className="dash-footnote">
        Every action on this screen is recorded in the audit log by the endpoint
        that performs it — including who did it, what changed, and when.
      </p>

      <ConfirmDialog
        open={Boolean(pending)}
        title={pending?.title ?? 'Confirm'}
        confirmLabel={
          pending?.kind === 'complete'
            ? 'Complete pickup'
            : pending?.kind === 'upload'
              ? 'Upload photos'
              : 'Confirm'
        }
        tone={pending?.kind === 'resolve' ? 'btn--danger' : 'btn--primary'}
        busy={busy}
        onConfirm={confirm}
        onCancel={() => !busy && setPending(null)}
      >
        {/* Named on the dialog itself: an operator confirming a handover needs
            to see which car and which customer, not just "are you sure?". */}
        {pending?.plate && (
          <p className="dialog__fact">
            <span>Vehicle</span> <strong>{pending.plate}</strong>
          </p>
        )}
        {pending?.customer && (
          <p className="dialog__fact">
            <span>Customer</span> <strong>{pending.customer}</strong>
          </p>
        )}
        {pending?.status && (
          <p className="dialog__fact">
            <span>New status</span> <strong>{pending.status}</strong>
          </p>
        )}
        {pending?.detail && <p>{pending.detail}</p>}
        {pending?.kind === 'complete' && (
          <p className="dialog__note">
            This closes the custody session, frees the zone slot and completes
            the ticket. It cannot be undone from this screen.
          </p>
        )}
      </ConfirmDialog>
    </div>
  )
}

/**
 * Pickup photo upload.
 *
 * Kept as its own component because it holds file state that has nothing to do
 * with the rest of the screen, and because an operator should be able to stage
 * photos for one car while the queue refreshes around them.
 */
function PickupUpload({ rows, files, onFiles, onSubmit }) {
  const needing = (rows ?? []).filter((row) => row.needs_pickup_photos)
  if (!needing.length) return null

  return (
    <div className="pickup-upload">
      <h3>Upload pickup photos</h3>
      <p className="muted">
        Choose one photo per angle. Each is compared against the matching
        drop-off photo in the same request.
      </p>

      {needing.map((row) => (
        <div key={row.custody_session_id} className="pickup-upload__row">
          <strong>{row.vehicle?.plate_number ?? '—'}</strong>
          <div className="pickup-upload__angles">
            {PICKUP_ANGLES.map((angle) => (
              <label key={angle} className="pickup-upload__angle">
                <span>{angle}</span>
                <input
                  type="file"
                  accept="image/*"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    onFiles({
                      ...files,
                      [`${row.custody_session_id}:${angle}`]: file,
                    })
                  }}
                />
              </label>
            ))}
          </div>
          <button
            type="button"
            className="btn btn--sm"
            disabled={!PICKUP_ANGLES.every((angle) => files[`${row.custody_session_id}:${angle}`])}
            onClick={() =>
              onSubmit(
                row.custody_session_id,
                PICKUP_ANGLES.map((angle) => ({
                  angle,
                  file: files[`${row.custody_session_id}:${angle}`],
                }))
              )
            }
          >
            Compare and capture
          </button>
        </div>
      ))}
    </div>
  )
}

export default ValetDashboardPage

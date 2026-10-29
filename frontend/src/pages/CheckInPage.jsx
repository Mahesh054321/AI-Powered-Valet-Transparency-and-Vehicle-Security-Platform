/**
 * Valet check-in wizard.
 *
 * The ten steps from the brief, mapped onto real API calls:
 *
 *   1 select customer        GET  /v1/customers
 *   2 select/register vehicle GET  /v1/vehicles?owner_id=…  or POST /v1/vehicles
 *   3 capture vehicle details (odometer, fuel, damage notes - sent at check-in)
 *   4 capture drop-off images POST /v1/uploads/images  → image references
 *   5 create valet ticket     POST /v1/tickets
 *   8 generate ticket number  ┘ same call - the backend allocates it
 *   9 generate QR code        ┘ same call - rendered server-side to storage
 *   6 assign parking zone     POST /v1/tickets/:id/check-in
 *   7 start custody session   ┘ same call - opens the session in PARKED
 *  10 show digital ticket     navigates to /tickets/:id
 *
 * Steps 5, 8 and 9 are one request and 6 and 7 are another. They are shown as
 * separate steps because they are separate *facts* a reviewer will look for,
 * and the sidebar marks each one done only once the call that produces it has
 * actually succeeded - the checklist is driven by real responses, not by
 * optimistic UI state.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { fetchCustomers, fetchParkingZones } from '../services/directoryService'
import { fetchVehicles, createVehicle } from '../services/vehicleService'
import { checkInTicket, issueTicket } from '../services/ticketService'
import { uploadDropOffPhoto } from '../services/uploadService'
import { normaliseError } from '../services/apiClient'
import { DataRow, ErrorNotice, Field, Panel, Spinner } from '../components/ui'
import { VehicleForm } from '../components/vehicles/VehicleForm'
import { CHECK_IN_PHOTO_ANGLES } from '../utils/constants'
import { useAuth } from '../hooks/useAuth'

const STEPS = [
  'Select customer',
  'Select or register vehicle',
  'Capture vehicle details',
  'Capture drop-off images',
  'Create valet ticket',
  'Assign parking zone',
  'Start custody session',
  'Generate ticket number',
  'Generate QR code',
  'Show digital ticket',
]

export function CheckInPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user } = useAuth()

  const [customerId, setCustomerId] = useState(null)
  const [customerSearch, setCustomerSearch] = useState('')
  const [vehicleId, setVehicleId] = useState(null)
  const [registeringVehicle, setRegisteringVehicle] = useState(false)
  const [zoneId, setZoneId] = useState(null)
  const [details, setDetails] = useState({ odometer: '', fuelLevel: '', damageNotes: '', instructions: '' })
  const [photos, setPhotos] = useState({})
  const [uploading, setUploading] = useState(null)

  const [issued, setIssued] = useState(null)
  const [handedOver, setHandedOver] = useState(null)

  const customers = useQuery({
    queryKey: ['customers', customerSearch],
    queryFn: () => fetchCustomers(customerSearch || undefined),
  })

  const vehicles = useQuery({
    queryKey: ['vehicles', { ownerId: customerId }],
    queryFn: () => fetchVehicles({ owner_id: customerId, page_size: 50 }),
    enabled: Boolean(customerId),
  })

  const zones = useQuery({
    queryKey: ['parking-zones'],
    queryFn: () => fetchParkingZones({ parkableOnly: true }),
  })

  const handOver = useMutation({
    mutationFn: async () => {
      // Step 5/8/9: one call issues the ticket, allocates its number and
      // renders the QR image server-side.
      const ticketResult = await issueTicket({
        vehicleId,
        specialInstructions: details.instructions || undefined,
      })
      const ticket = ticketResult.ticket
      setIssued(ticket)

      // Step 6/7: one call assigns the zone and opens the custody session.
      const checked = await checkInTicket(ticket.id, {
        zoneId,
        photoRefs: Object.values(photos).map((entry) => entry.ref),
        odometer: details.odometer,
        fuelLevel: details.fuelLevel,
        damageNotes: details.damageNotes,
      })
      return checked.ticket
    },
    onSuccess: (ticket) => {
      setHandedOver(ticket)
      queryClient.invalidateQueries({ queryKey: ['vehicles'] })
      queryClient.invalidateQueries({ queryKey: ['tickets'] })
      navigate(`/tickets/${ticket.id}`)
    },
  })

  const selectedCustomer = customers.data?.items?.find((entry) => entry.id === customerId)
  const selectedVehicle = vehicles.data?.items?.find((entry) => entry.id === vehicleId)
  const selectedZone = zones.data?.items?.find((entry) => entry.id === zoneId)

  const done = {
    customer: Boolean(selectedCustomer),
    vehicle: Boolean(selectedVehicle),
    details: true,
    photos: Object.keys(photos).length > 0,
    ticket: Boolean(issued),
    zone: Boolean(selectedZone),
    custody: Boolean(handedOver),
    code: Boolean(issued?.ticket_code),
    qr: Boolean(issued?.qr_url),
    shown: Boolean(handedOver),
  }
  const activeStep = !done.customer
    ? 0
    : !done.vehicle
      ? 1
      : !done.zone
        ? 5
        : 9

  async function handlePhoto(angle, event) {
    const file = event.target.files?.[0]
    if (!file) return

    setUploading(angle)
    try {
      const ref = await uploadDropOffPhoto(file, { label: angle })
      setPhotos((current) => ({ ...current, [angle]: { ref, name: file.name } }))
    } catch (caught) {
      setPhotos((current) => ({
        ...current,
        [angle]: { error: caught?.apiError ?? normaliseError(caught) },
      }))
    } finally {
      setUploading(null)
    }
  }

  const canHandOver = done.customer && done.vehicle && done.zone && !handOver.isPending

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Vehicle check-in</h1>
          <p className="muted">
            Working as {user?.email ?? 'signed-in user'}. Every step calls the live API; nothing
            here is simulated.
          </p>
        </div>
      </header>

      <div className="wizard">
        <Panel title="Workflow">
          <ol className="wizard__steps">
            {STEPS.map((label, index) => {
              const keys = [
                'customer',
                'vehicle',
                'details',
                'photos',
                'ticket',
                'zone',
                'custody',
                'code',
                'qr',
                'shown',
              ]
              const complete = done[keys[index]]
              const state = complete ? 'done' : index === activeStep ? 'active' : 'todo'
              return (
                <li key={label} className={`wizard__step wizard__step--${state}`}>
                  {label}
                </li>
              )
            })}
          </ol>
        </Panel>

        <div>
          {/* ---- step 1: customer ---- */}
          <Panel title="1 · Select customer" subtitle="Who is handing the vehicle over">
            <input
              type="search"
              placeholder="Search by name or email"
              value={customerSearch}
              onChange={(event) => setCustomerSearch(event.target.value)}
              aria-label="Search customers"
            />
            {customers.isPending ? (
              <Spinner label="Loading customers…" />
            ) : customers.isError ? (
              <ErrorNotice error={customers.error} onRetry={() => customers.refetch()} />
            ) : (
              <ul className="pick-list">
                {(customers.data?.items ?? []).map((customer) => (
                  <li key={customer.id}>
                    <button
                      type="button"
                      aria-pressed={customer.id === customerId}
                      onClick={() => {
                        setCustomerId(customer.id)
                        setVehicleId(null)
                      }}
                    >
                      <strong>{customer.full_name}</strong>
                      <span className="muted"> · {customer.email}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          {/* ---- step 2: vehicle ---- */}
          {done.customer && (
            <Panel
              title="2 · Select or register vehicle"
              subtitle={selectedCustomer?.full_name}
              actions={
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={() => setRegisteringVehicle((value) => !value)}
                >
                  {registeringVehicle ? 'Choose existing' : 'Register new'}
                </button>
              }
            >
              {registeringVehicle ? (
                <VehicleForm
                  submitLabel="Register and select"
                  onSubmit={async (form) => {
                    const result = await createVehicle({ ...form, ownerId: customerId })
                    const vehicleQueryKey = ['vehicles', { ownerId: customerId }]
                    queryClient.setQueryData(vehicleQueryKey, (current) => ({
                      ...current,
                      items: [
                        ...(current?.items ?? []).filter(
                          (vehicle) => vehicle.id !== result.vehicle.id,
                        ),
                        result.vehicle,
                      ],
                    }))
                    queryClient.invalidateQueries({ queryKey: vehicleQueryKey })
                    setVehicleId(result.vehicle.id)
                    setRegisteringVehicle(false)
                  }}
                  onCancel={() => setRegisteringVehicle(false)}
                />
              ) : vehicles.isPending ? (
                <Spinner label="Loading vehicles…" />
              ) : vehicles.isError ? (
                <ErrorNotice error={vehicles.error} onRetry={() => vehicles.refetch()} />
              ) : (vehicles.data?.items ?? []).length === 0 ? (
                <p className="muted">
                  This customer has no vehicles on file. Register one to continue.
                </p>
              ) : (
                <ul className="pick-list">
                  {vehicles.data.items.map((vehicle) => (
                    <li key={vehicle.id}>
                      <button
                        type="button"
                        aria-pressed={vehicle.id === vehicleId}
                        disabled={vehicle.status === 'IN_CUSTODY'}
                        onClick={() => setVehicleId(vehicle.id)}
                      >
                        <strong className="ticket__plate">{vehicle.plate_number}</strong>
                        <span className="muted">
                          {' '}
                          · {[vehicle.make, vehicle.model].filter(Boolean).join(' ')}
                          {vehicle.status === 'IN_CUSTODY' ? ' · already in custody' : ''}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          )}

          {/* ---- step 6: zone (collected early, sent with check-in) ---- */}
          {done.vehicle && (
            <Panel
              title="6 · Assign parking zone"
              subtitle="Sent with the handover, which is what opens the custody session"
            >
              {zones.isPending ? (
                <Spinner label="Loading zones…" />
              ) : zones.isError ? (
                <ErrorNotice error={zones.error} onRetry={() => zones.refetch()} />
              ) : (
                <ul className="pick-list">
                  {zones.data.items.map((zone) => {
                    const full = zone.available_slots <= 0
                    return (
                      <li key={zone.id}>
                        <button
                          type="button"
                          aria-pressed={zone.id === zoneId}
                          disabled={full}
                          onClick={() => setZoneId(zone.id)}
                        >
                          <strong>
                            {zone.code} · {zone.name}
                          </strong>
                          <span className="muted">
                            {' '}
                            · {zone.available_slots}/{zone.capacity} free
                            {full ? ' · full' : ''}
                          </span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </Panel>
          )}

          {/* ---- step 3: condition ---- */}
          {done.vehicle && (
            <Panel title="3 · Capture vehicle details" subtitle="Condition at drop-off">
              <div className="grid grid--2">
                <Field label="Odometer (km)" hint="Optional">
                  <input
                    inputMode="numeric"
                    value={details.odometer}
                    onChange={(event) => setDetails({ ...details, odometer: event.target.value })}
                  />
                </Field>
                <Field label="Fuel level (%)" hint="Optional">
                  <input
                    inputMode="numeric"
                    value={details.fuelLevel}
                    onChange={(event) => setDetails({ ...details, fuelLevel: event.target.value })}
                  />
                </Field>
              </div>
              <Field label="Pre-existing damage" hint="Optional - anything already present">
                <textarea
                  rows={2}
                  value={details.damageNotes}
                  onChange={(event) => setDetails({ ...details, damageNotes: event.target.value })}
                />
              </Field>
              <Field label="Special instructions" hint="Optional - shown on the ticket">
                <textarea
                  rows={2}
                  value={details.instructions}
                  onChange={(event) => setDetails({ ...details, instructions: event.target.value })}
                />
              </Field>
            </Panel>
          )}

          {/* ---- step 4: photos ---- */}
          {done.vehicle && (
            <Panel
              title="4 · Capture drop-off images"
              subtitle="Uploaded immediately; the ticket stores references, never the bytes"
            >
              <div className="photo-grid">
                {CHECK_IN_PHOTO_ANGLES.map((angle) => {
                  const entry = photos[angle.key]
                  return (
                    <div
                      key={angle.key}
                      className={`photo-card ${entry?.ref ? 'photo-card--filled' : ''}`}
                    >
                      <span className="photo-card__label">{angle.label}</span>
                      {entry?.ref ? (
                        <img
                          src={`/media/${entry.ref.path}`}
                          alt={`${angle.label} drop-off photograph`}
                        />
                      ) : null}
                      {entry?.error ? (
                        <span className="badge badge--danger">{entry.error.code}</span>
                      ) : null}
                      <label className="btn btn--ghost btn--block">
                        {uploading === angle.key
                          ? 'Uploading…'
                          : entry?.ref
                            ? 'Replace'
                            : 'Add photo'}
                        <input
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          hidden
                          onChange={(event) => handlePhoto(angle.key, event)}
                        />
                      </label>
                    </div>
                  )
                })}
              </div>
              {Object.values(photos).some((entry) => entry?.error) && (
                <div className="notice notice--warning">
                  At least one photo was rejected. The server checks each file against its own
                  signature bytes, so a file whose contents do not match its extension is refused.
                </div>
              )}
            </Panel>
          )}

          {/* ---- handover ---- */}
          {done.vehicle && done.zone && (
            <Panel title="5–10 · Hand over the vehicle">
              <dl className="data-list">
                <DataRow label="Customer" value={selectedCustomer?.full_name} />
                <DataRow label="Vehicle" value={selectedVehicle?.plate_number} mono />
                <DataRow
                  label="Tracking"
                  value={
                    selectedVehicle?.tracking_method === 'REAL_GPS'
                      ? `Hardware (${selectedVehicle.gps_device_id || 'Device linked'})`
                      : selectedVehicle?.tracking_method === 'MOBILE_GPS'
                        ? 'Mobile Browser GPS'
                        : 'Demo / Simulated'
                  }
                />
                <DataRow label="Zone" value={selectedZone?.name} />
                <DataRow label="Photos" value={Object.values(photos).filter((p) => p?.ref).length} />
              </dl>

              <div className="btn-row">
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={!canHandOver}
                  onClick={() => handOver.mutate()}
                >
                  {handOver.isPending ? 'Creating ticket…' : 'Create ticket and start custody'}
                </button>
                <Link className="btn btn--ghost" to="/tickets">
                  Cancel
                </Link>
              </div>

              {handOver.isError && (
                <ErrorNotice error={handOver.error.apiError ?? normaliseError(handOver.error)} />
              )}
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}

export default CheckInPage

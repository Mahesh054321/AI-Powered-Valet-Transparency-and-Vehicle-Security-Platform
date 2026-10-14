/**
 * Vehicle details and its valet history.
 *
 * Two panels: the vehicle as it stands, and every previous custody session with
 * the ticket it belongs to. The history is read-only here - a session is closed
 * by completing its ticket, not by editing it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useToast } from '../context/ToastContext'
import {
  archiveVehicle,
  fetchVehicle,
  fetchVehicleSessions,
  updateVehicle,
} from '../services/vehicleService'
import { normaliseError } from '../services/apiClient'
import { DataRow, ErrorNotice, Panel, Spinner } from '../components/ui'
import { VehicleForm } from '../components/vehicles/VehicleForm'
import { StatusBadge } from '../components/vehicles/StatusBadge'
import { CUSTODY_STATUS_LABELS, VEHICLE_STATUS_LABELS } from '../utils/constants'
import { formatDateTime } from '../utils/format'

export function VehicleDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)

  const vehicleQuery = useQuery({
    queryKey: ['vehicle', id],
    queryFn: () => fetchVehicle(id),
  })

  const sessionsQuery = useQuery({
    queryKey: ['vehicle-sessions', id],
    queryFn: () => fetchVehicleSessions(id),
  })

  const toast = useToast()


  const archive = useMutation({
    mutationFn: () => archiveVehicle(id),
    onSuccess: () => {
      toast.success('Vehicle archived', 'It no longer appears in your active list.')
      queryClient.invalidateQueries({ queryKey: ['vehicles'] })
      navigate('/vehicles', { replace: true })
    },
    onError: (error) => toast.error('Could not archive the vehicle', normaliseError(error).message),
  })

  const edit = useMutation({
    mutationFn: (form) => updateVehicle(id, form),
    onSuccess: () => {
      toast.success('Vehicle updated', 'Your changes have been saved.')
      queryClient.invalidateQueries({ queryKey: ['vehicle', id] })
      queryClient.invalidateQueries({ queryKey: ['vehicles'] })
      setEditing(false)
    },
    onError: (error) => toast.error('Could not save your changes', normaliseError(error).message),
  })

  if (vehicleQuery.isPending) return <Spinner label="Loading vehicle…" />
  if (vehicleQuery.isError) {
    return <ErrorNotice error={vehicleQuery.error} onRetry={() => vehicleQuery.refetch()} />
  }

  const vehicle = vehicleQuery.data.vehicle
  const sessions = sessionsQuery.data?.sessions ?? []
  const inCustody = vehicle.status === 'IN_CUSTODY'

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1 className="ticket__plate">{vehicle.plate_number}</h1>
          <p className="muted">
            {[vehicle.make, vehicle.model].filter(Boolean).join(' ')}
            {vehicle.year ? ` · ${vehicle.year}` : ''}
          </p>
        </div>
        <div className="btn-row">
          {!editing && (
            <button type="button" className="btn" onClick={() => setEditing(true)}>
              Edit
            </button>
          )}
          <Link className="btn btn--ghost" to="/vehicles">
            Back to list
          </Link>
        </div>
      </header>

      {editing ? (
        <Panel title="Edit vehicle">
          <VehicleForm
            initialValues={{
              plateNumber: vehicle.plate_number,
              make: vehicle.make,
              model: vehicle.model ?? '',
              year: vehicle.year ? String(vehicle.year) : '',
              color: vehicle.color ?? '',
              notes: vehicle.notes ?? '',
            }}
            submitLabel="Save changes"
            onSubmit={(form) => edit.mutateAsync(form)}
            onCancel={() => setEditing(false)}
          />
        </Panel>
      ) : (
        <Panel
          title="Details"
          actions={<StatusBadge status={vehicle.status} labels={VEHICLE_STATUS_LABELS} />}
        >
          <dl className="data-list">
            <DataRow label="Registration" value={vehicle.plate_number} mono />
            <DataRow label="Normalised plate" value={vehicle.normalized_plate} mono />
            <DataRow label="Make" value={vehicle.make} />
            <DataRow label="Model" value={vehicle.model} />
            <DataRow label="Year" value={vehicle.year} />
            <DataRow label="Colour" value={vehicle.color} />
            <DataRow label="Notes" value={vehicle.notes} />
            <DataRow label="Registered" value={formatDateTime(vehicle.created_at)} />
          </dl>

          {inCustody && (
            <div className="notice notice--info">
              This vehicle is currently in custody. It cannot be archived until the session is
              completed or cancelled.
            </div>
          )}

          {vehicle.status !== 'ARCHIVED' && (
            <div className="btn-row">
              <button
                type="button"
                className="btn btn--danger"
                disabled={archive.isPending || inCustody}
                onClick={() => archive.mutate()}
              >
                {archive.isPending ? 'Archiving…' : 'Archive vehicle'}
              </button>
            </div>
          )}
          {archive.isError && (
            <ErrorNotice error={archive.error.apiError ?? normaliseError(archive.error)} />
          )}
        </Panel>
      )}

      <Panel
        title="Previous valet sessions"
        subtitle={
          sessionsQuery.isError
            ? undefined
            : `${sessions.length} session${sessions.length === 1 ? '' : 's'} on record`
        }
      >
        {sessionsQuery.isPending ? (
          <Spinner label="Loading history…" />
        ) : sessionsQuery.isError ? (
          <ErrorNotice error={sessionsQuery.error} onRetry={() => sessionsQuery.refetch()} />
        ) : sessions.length === 0 ? (
          <p className="muted">
            No valet sessions yet. Once this vehicle is checked in, its custody history appears
            here.
          </p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Dropped off</th>
                <th>Custody status</th>
                <th>Returned</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(({ ticket, custody_session: session }) => (
                <tr key={session.id}>
                  <td>
                    <Link className="table__link mono" to={`/tickets/${ticket.id}`}>
                      {ticket.ticket_code}
                    </Link>
                  </td>
                  <td>{formatDateTime(session.dropped_off_at)}</td>
                  <td>
                    <StatusBadge status={session.status} labels={CUSTODY_STATUS_LABELS} />
                  </td>
                  <td>{formatDateTime(session.picked_up_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}

export default VehicleDetailPage

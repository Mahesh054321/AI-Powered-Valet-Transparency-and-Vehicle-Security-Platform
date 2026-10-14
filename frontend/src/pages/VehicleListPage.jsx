/**
 * Vehicle list.
 *
 * The backend scopes this to the caller, so a customer sees their own vehicles
 * and valet staff see every vehicle they may check in.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchVehicles } from '../services/vehicleService'
import { ErrorNotice, Panel, Spinner } from '../components/ui'
import { VEHICLE_STATUS_LABELS } from '../utils/constants'
import { StatusBadge } from '../components/vehicles/StatusBadge'

export function VehicleListPage() {
  const [search, setSearch] = useState('')
  const [submitted, setSubmitted] = useState('')

  const query = useQuery({
    queryKey: ['vehicles', { search: submitted }],
    queryFn: () => fetchVehicles({ search: submitted || undefined, page_size: 50 }),
  })

  const vehicles = query.data?.items ?? []

  function handleSearch(event) {
    event.preventDefault()
    setSubmitted(search.trim())
  }

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>My Vehicles</h1>
          <p className="muted">
            Vehicles registered to your account. A vehicle must be on file before it can be handed
            over at check-in.
          </p>
        </div>
        <Link className="btn btn--primary" to="/vehicles/new">
          Register a vehicle
        </Link>
      </header>

      <Panel>
        <form onSubmit={handleSearch} className="btn-row">
          <input
            type="search"
            placeholder="Search by plate, make or model"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search vehicles"
          />
          <button type="submit" className="btn">
            Search
          </button>
        </form>
      </Panel>

      {query.isPending ? (
        <Spinner label="Loading vehicles…" />
      ) : query.isError ? (
        <ErrorNotice error={query.error} onRetry={() => query.refetch()} />
      ) : vehicles.length === 0 ? (
        <Panel tone="panel--muted">
          <p className="muted">
            {submitted
              ? `No vehicle matches “${submitted}”.`
              : 'No vehicles registered yet. Add the first one to get started.'}
          </p>
        </Panel>
      ) : (
        <Panel subtitle={`${query.data?.pagination?.total_items ?? vehicles.length} on file`}>
          <table className="table">
            <thead>
              <tr>
                <th>Registration</th>
                <th>Make / model</th>
                <th>Colour</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {vehicles.map((vehicle) => (
                <tr key={vehicle.id}>
                  <td>
                    <Link className="table__link ticket__plate" to={`/vehicles/${vehicle.id}`}>
                      {vehicle.plate_number}
                    </Link>
                  </td>
                  <td>
                    {[vehicle.make, vehicle.model].filter(Boolean).join(' ')}
                    {vehicle.year ? ` · ${vehicle.year}` : ''}
                  </td>
                  <td>{vehicle.color ?? '—'}</td>
                  <td>
                    <StatusBadge
                      status={vehicle.status}
                      labels={VEHICLE_STATUS_LABELS}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  )
}

export default VehicleListPage

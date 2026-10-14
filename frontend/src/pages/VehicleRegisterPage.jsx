/**
 * Register a vehicle.
 *
 * A customer registers to their own account. Valet staff reach this same form
 * from the check-in wizard with a customer already chosen, in which case the
 * owner is sent explicitly.
 */

import { useNavigate } from 'react-router-dom'
import { createVehicle } from '../services/vehicleService'
import { Panel } from '../components/ui'
import { VehicleForm } from '../components/vehicles/VehicleForm'

export function VehicleRegisterPage() {
  const navigate = useNavigate()

  async function handleSubmit(form) {
    const result = await createVehicle(form)
    navigate(`/vehicles/${result.vehicle.id}`, { replace: true })
  }

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Register a vehicle</h1>
          <p className="muted">
            The registration number must be unique across active vehicles. Archiving a vehicle frees
            its number for re-use.
          </p>
        </div>
      </header>

      <Panel title="Vehicle details" subtitle="Fields marked optional can be added later">
        <VehicleForm
          submitLabel="Register vehicle"
          onSubmit={handleSubmit}
          onCancel={() => navigate('/vehicles')}
        />
      </Panel>
    </div>
  )
}

export default VehicleRegisterPage

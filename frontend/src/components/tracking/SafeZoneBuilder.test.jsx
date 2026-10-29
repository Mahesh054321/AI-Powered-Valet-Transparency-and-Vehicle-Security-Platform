import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { SafeZoneBuilder } from './SafeZoneBuilder'
import { createGeofence, previewGeofence } from '../../services/geofenceService'
import { renderWithProviders } from '../../test/utils'

vi.mock('../../services/geofenceService', () => ({
  createGeofence: vi.fn(),
  previewGeofence: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(createGeofence).mockReset()
  vi.mocked(previewGeofence).mockReset()
  vi.mocked(previewGeofence).mockResolvedValue({
    zone_type: 'CIRCLE',
    effective_radius_metres: 100,
    estimated_area_sq_metres: 31415.9,
    geofence_polygon: [[77.0, 28.0], [77.1, 28.0], [77.1, 28.1], [77.0, 28.1], [77.0, 28.0]],
    buffer_metres: 20,
  })
})

describe('SafeZoneBuilder', () => {
  it('renders boundary shape options and previews metrics on change', async () => {
    renderWithProviders(
      <SafeZoneBuilder
        initialCenter={{ latitude: 28.4595, longitude: 77.0266 }}
        vehicleId="v-1"
        custodyId="c-1"
      />,
    )

    expect(screen.getByText('Safe Zone Builder')).toBeInTheDocument()
    expect(screen.getByText(/Circular Geofence/)).toBeInTheDocument()

    await waitFor(() => {
      expect(previewGeofence).toHaveBeenCalled()
    })
    expect(screen.getByText('100 m')).toBeInTheDocument()
  })

  it('switches to rectangular yard shape with length and width controls', async () => {
    vi.mocked(previewGeofence).mockResolvedValue({
      zone_type: 'RECTANGLE',
      effective_radius_metres: 67.1,
      estimated_area_sq_metres: 7200,
      geofence_polygon: [[77.0, 28.0], [77.1, 28.0], [77.1, 28.1], [77.0, 28.1], [77.0, 28.0]],
      buffer_metres: 20,
    })

    renderWithProviders(<SafeZoneBuilder />)

    fireEvent.change(screen.getByLabelText('Boundary Shape'), {
      target: { value: 'RECTANGLE' },
    })

    expect(screen.getByLabelText('Length (metres)')).toBeInTheDocument()
    expect(screen.getByLabelText('Width (metres)')).toBeInTheDocument()
  })

  it('submits safe zone payload on form submit', async () => {
    vi.mocked(createGeofence).mockResolvedValue({
      id: 'sz-1',
      code: 'SZ-VIP',
      name: 'VIP Safe Bay',
      effective_radius_metres: 100,
      status: 'ACTIVE',
    })

    const onCreated = vi.fn()
    renderWithProviders(<SafeZoneBuilder vehicleId="v-1" custodyId="c-1" onCreated={onCreated} />)

    fireEvent.change(screen.getByLabelText('Zone Code'), { target: { value: 'SZ-VIP' } })
    fireEvent.change(screen.getByLabelText('Zone Label / Name'), { target: { value: 'VIP Safe Bay' } })
    fireEvent.submit(screen.getByTestId('safe-zone-form'))

    await waitFor(() => {
      expect(createGeofence).toHaveBeenCalledWith(
        expect.objectContaining({
          code: 'SZ-VIP',
          name: 'VIP Safe Bay',
          zone_type: 'CIRCLE',
          vehicle_id: 'v-1',
        }),
      )
    })
    expect(onCreated).toHaveBeenCalled()
  })
})

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiDelete, apiGet, apiPatch, apiPost } from './apiClient'
import {
  archiveVehicle,
  createVehicle,
  fetchVehicleSessions,
  fetchVehicles,
  updateVehicle,
} from './vehicleService'

vi.mock('./apiClient', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}))

describe('vehicleService', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
    vi.mocked(apiPost).mockReset()
    vi.mocked(apiPatch).mockReset()
    vi.mocked(apiDelete).mockReset()
  })

  it('translates the form fields into the backend payload', async () => {
    vi.mocked(apiPost).mockResolvedValue({ vehicle: {} })

    await createVehicle({
      plateNumber: 'UP85CA1234',
      make: 'Tata',
      model: 'Nexon',
      year: '2022',
      color: 'Silver',
      notes: 'Roof rack',
    })

    expect(apiPost).toHaveBeenCalledWith('/v1/vehicles', {
      plate_number: 'UP85CA1234',
      make: 'Tata',
      model: 'Nexon',
      year: 2022,
      color: 'Silver',
      notes: 'Roof rack',
    })
  })

  it('omits optional fields the visitor left blank', async () => {
    vi.mocked(apiPost).mockResolvedValue({ vehicle: {} })

    await createVehicle({ plateNumber: 'UP85CA1234', make: 'Tata', model: '', year: '', color: '', notes: '' })

    // Sending empty strings would fail the backend's length validation, so they
    // must be dropped rather than forwarded.
    expect(apiPost.mock.calls[0][1]).toEqual({ plate_number: 'UP85CA1234', make: 'Tata' })
  })

  it('sends owner_id only when registering on a customer\'s behalf', async () => {
    vi.mocked(apiPost).mockResolvedValue({ vehicle: {} })

    await createVehicle({ plateNumber: 'UP85CA1234', make: 'Tata' })
    expect(apiPost.mock.calls[0][1]).not.toHaveProperty('owner_id')

    await createVehicle({ plateNumber: 'UP85CA1235', make: 'Tata', ownerId: 'owner-1' })
    expect(apiPost.mock.calls[1][1].owner_id).toBe('owner-1')
  })

  it('sends the selected tracking method and hardware device ID', async () => {
    vi.mocked(apiPost).mockResolvedValue({ vehicle: {} })

    await createVehicle({
      plateNumber: 'UP85CA1236',
      make: 'Tata',
      ownerId: 'owner-1',
      tracking_method: 'REAL_GPS',
      gps_device_id: 'TRACKER-123',
    })

    expect(apiPost.mock.calls[0][1]).toMatchObject({
      owner_id: 'owner-1',
      tracking_method: 'REAL_GPS',
      gps_device_id: 'TRACKER-123',
    })
  })

  it('passes filters through to the list endpoint', async () => {
    vi.mocked(apiGet).mockResolvedValue({ items: [], pagination: {} })

    await fetchVehicles({ search: 'nexon', include_archived: true, page: 2 })

    expect(apiGet).toHaveBeenCalledWith('/v1/vehicles', {
      params: { search: 'nexon', include_archived: true, page: 2 },
    })
  })

  it('reads a single vehicle and its session history', async () => {
    vi.mocked(apiGet).mockResolvedValue({})

    await fetchVehicleSessions('v-1')

    // One argument: no filters are sent for a detail read.
    expect(apiGet).toHaveBeenCalledWith('/v1/vehicles/v-1/sessions')
  })

  it('sends only the fields that changed', async () => {
    vi.mocked(apiPatch).mockResolvedValue({ vehicle: {} })

    await updateVehicle('v-1', { color: 'Red', notes: undefined })

    expect(apiPatch).toHaveBeenCalledWith('/v1/vehicles/v-1', { color: 'Red' })
  })

  it('clears a field when it is explicitly set to an empty string', async () => {
    vi.mocked(apiPatch).mockResolvedValue({ vehicle: {} })

    await updateVehicle('v-1', { notes: '' })

    expect(apiPatch.mock.calls[0][1]).toEqual({ notes: '' })
  })

  it('persists tracking settings when editing a vehicle', async () => {
    vi.mocked(apiPatch).mockResolvedValue({ vehicle: {} })

    await updateVehicle('v-1', {
      tracking_method: 'MOBILE_GPS',
      gps_device_id: '',
    })

    expect(apiPatch).toHaveBeenCalledWith('/v1/vehicles/v-1', {
      tracking_method: 'MOBILE_GPS',
      gps_device_id: null,
    })
  })

  it('archives through DELETE and tolerates the empty 204 body', async () => {
    vi.mocked(apiDelete).mockResolvedValue(null)

    await expect(archiveVehicle('v-1')).resolves.toBeNull()
    expect(apiDelete).toHaveBeenCalledWith('/v1/vehicles/v-1')
  })
})

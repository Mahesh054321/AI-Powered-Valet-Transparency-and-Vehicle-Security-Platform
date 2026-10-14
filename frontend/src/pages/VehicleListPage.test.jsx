/**
 * The vehicle list.
 *
 * The list is scoped server-side, so the tests here are about presentation and
 * the search interaction rather than about who may see what:
 *
 * - **Search only fires on submit.** Typing must not issue a request per
 *   keystroke; the query key is keyed on the *submitted* term, and the page
 *   distinguishes the two so an abandoned edit does not change the results.
 * - **The two empty states are different sentences.** "No vehicles registered
 *   yet" and "No vehicle matches X" are different facts, and collapsing them
 *   would tell a user with a typo'd search that they own no cars.
 * - **The row is a link to the vehicle,** not plain text, so the list is
 *   navigable without a separate control.
 * - **A missing colour renders an em dash** rather than the string "undefined".
 * - **The count comes from the pagination envelope,** not from the length of the
 *   page that happens to have loaded.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { VehicleListPage } from './VehicleListPage'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

vi.mock('../services/vehicleService', async (original) => {
  const actual = await original()
  return { ...actual, fetchVehicles: vi.fn() }
})

import { fetchVehicles } from '../services/vehicleService'

function vehicle(overrides = {}) {
  return {
    id: 'vehicle-1',
    plate_number: 'UP 78 AB 1234',
    make: 'Maruti',
    model: 'Swift',
    year: 2019,
    color: 'Red',
    status: 'REGISTERED',
    ...overrides,
  }
}

function stubList(items, totalItems = items.length) {
  vi.mocked(fetchVehicles).mockResolvedValue({
    items,
    pagination: { total_items: totalItems, page: 1, page_size: 50 },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  stubList([vehicle()])
})

function renderPage(role = 'CUSTOMER') {
  clearSession()
  return renderWithProviders(<VehicleListPage />, {
    route: '/vehicles',
    token: buildToken({ role }),
  })
}

describe('VehicleListPage', () => {
  it('renders the vehicles with make, model and year composed in one cell', async () => {
    renderPage()

    expect(await screen.findByRole('link', { name: 'UP 78 AB 1234' })).toBeInTheDocument()
    const row = screen.getByRole('link', { name: 'UP 78 AB 1234' }).closest('tr')
    expect(within(row).getByText('Maruti Swift · 2019')).toBeInTheDocument()
    expect(within(row).getByText('Red')).toBeInTheDocument()
  })

  it('links each plate to the vehicle detail page', async () => {
    renderPage()

    const link = await screen.findByRole('link', { name: 'UP 78 AB 1234' })
    expect(link).toHaveAttribute('href', '/vehicles/vehicle-1')
  })

  it('shows the labelled status rather than the raw enum', async () => {
    renderPage()

    await screen.findByRole('link', { name: 'UP 78 AB 1234' })
    expect(screen.getByText('Registered')).toBeInTheDocument()
    expect(screen.queryByText('REGISTERED')).toBeNull()
  })

  it('renders an em dash for a vehicle with no colour', async () => {
    vi.clearAllMocks()
    stubList([vehicle({ color: null })])
    renderPage()

    const row = (await screen.findByRole('link', { name: 'UP 78 AB 1234' })).closest('tr')
    expect(within(row).getByText('—')).toBeInTheDocument()
    expect(within(row).queryByText(/undefined/)).toBeNull()
  })

  it('omits the year separator when no year is recorded', async () => {
    vi.clearAllMocks()
    stubList([vehicle({ year: null, model: 'Swift' })])
    renderPage()

    const row = (await screen.findByRole('link', { name: 'UP 78 AB 1234' })).closest('tr')
    expect(within(row).getByText('Maruti Swift')).toBeInTheDocument()
  })

  it('reports the total from the pagination envelope, not the loaded page', async () => {
    vi.clearAllMocks()
    // Two rows on this page, but the server says there are 57 in total.
    stubList([vehicle(), vehicle({ id: 'vehicle-2', plate_number: 'UP 78 CD 5678' })], 57)
    renderPage()

    await screen.findByRole('link', { name: 'UP 78 AB 1234' })
    expect(screen.getByText('57 on file')).toBeInTheDocument()
  })

  it('does not query until the search is submitted', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('link', { name: 'UP 78 AB 1234' })
    expect(fetchVehicles).toHaveBeenCalledTimes(1)

    await user.type(screen.getByLabelText(/search vehicles/i), 'Swift')
    // Typing alone must not refetch - that would be a request per keystroke.
    expect(fetchVehicles).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: /^search$/i }))
    await waitFor(() => expect(fetchVehicles).toHaveBeenCalledTimes(2))
    expect(vi.mocked(fetchVehicles).mock.calls[1][0]).toMatchObject({ search: 'Swift' })
  })

  it('trims the submitted search term', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('link', { name: 'UP 78 AB 1234' })
    await user.type(screen.getByLabelText(/search vehicles/i), '  Nexon  ')
    await user.click(screen.getByRole('button', { name: /^search$/i }))

    await waitFor(() => expect(fetchVehicles).toHaveBeenCalledTimes(2))
    expect(vi.mocked(fetchVehicles).mock.calls[1][0]).toMatchObject({ search: 'Nexon' })
  })

  it('does not refetch when a blank search is submitted over an unfiltered list', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('link', { name: 'UP 78 AB 1234' })
    expect(vi.mocked(fetchVehicles).mock.calls[0][0]).toMatchObject({ search: undefined })

    await user.click(screen.getByRole('button', { name: /^search$/i }))

    // The query key is ['vehicles', { search: '' }] before and after, so React
    // Query correctly serves the cache. Refetching identical parameters would
    // be a wasted round trip on every empty submit.
    await waitFor(() => expect(fetchVehicles).toHaveBeenCalledTimes(1))
  })

  it('distinguishes "nothing registered" from "nothing matched"', async () => {
    vi.clearAllMocks()
    stubList([])
    renderPage()

    expect(
      await screen.findByText(/no vehicles registered yet/i),
    ).toBeInTheDocument()
  })

  it('names the term that matched nothing', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('link', { name: 'UP 78 AB 1234' })
    vi.mocked(fetchVehicles).mockResolvedValue({
      items: [],
      pagination: { total_items: 0, page: 1, page_size: 50 },
    })

    await user.type(screen.getByLabelText(/search vehicles/i), 'Ferrari')
    await user.click(screen.getByRole('button', { name: /^search$/i }))

    // Quoting the term matters: it is what tells the user the search was the
    // problem rather than their account being empty.
    expect(await screen.findByText(/no vehicle matches “Ferrari”/i)).toBeInTheDocument()
  })

  it('shows a retryable error when the list fails to load', async () => {
    vi.clearAllMocks()
    const failure = new Error('network down')
    failure.response = {
      status: 503,
      data: { error: { code: 'service_unavailable', message: 'Backend offline' } },
      headers: {},
    }
    vi.mocked(fetchVehicles).mockRejectedValue(failure)
    renderPage()

    // The query client retries once before surfacing the error.
    expect(
      await screen.findByRole('button', { name: /try again/i }, { timeout: 4000 }),
    ).toBeInTheDocument()
  })

  it('offers the registration route from the list header', async () => {
    renderPage()

    expect(
      await screen.findByRole('link', { name: /register a vehicle/i }),
    ).toHaveAttribute('href', '/vehicles/new')
  })
})

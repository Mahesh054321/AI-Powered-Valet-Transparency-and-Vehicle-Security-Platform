/**
 * The ticket detail page - the pickup request flow.
 *
 * This is the page a customer or valet uses to ask for the car back, so the
 * tests concentrate on the retrieval request:
 *
 * - **An estimate is requested, not assumed.** With no stored estimate the card
 *   says "No estimate yet" and offers a button. It must never render a bare
 *   "0 minutes", because an empty card reads as an immediate pickup.
 * - **The request carries the custody session id**, not the ticket id. The two
 *   are different UUIDs, and the retrieval endpoints are keyed on the session.
 * - **The synthetic-data caveat survives the render.** The estimate card always
 *   prints the prototype notice; it is the most screenshot-able number in the
 *   product and the caveat must not be croppable.
 * - **The number is arguable.** The contributing factors are listed so "12
 *   minutes" can be checked against the yard rather than taken on trust.
 * - **A refusal is reported.** An estimate failure must not silently leave the
 *   card looking unchanged.
 * - **Completing the pickup sends the odometer and condition acceptance.**
 * - **Role gating.** A customer sees the ticket but no valet handover controls.
 *
 * Errors are built the way the axios response interceptor builds them
 * (`error.response.data.error`), because `normaliseError` reads that path.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { TicketDetailPage } from './TicketDetailPage'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

vi.mock('../services/ticketService', async (original) => {
  const actual = await original()
  return {
    ...actual,
    fetchTicket: vi.fn(),
    updateCustodyStatus: vi.fn(),
    completeTicket: vi.fn(),
    cancelTicket: vi.fn(),
  }
})

vi.mock('../services/retrievalService', async (original) => {
  const actual = await original()
  return {
    ...actual,
    fetchRetrievalPrediction: vi.fn(),
    requestRetrievalPrediction: vi.fn(),
  }
})

import {
  fetchTicket,
  updateCustodyStatus,
  completeTicket,
} from '../services/ticketService'
import {
  fetchRetrievalPrediction,
  requestRetrievalPrediction,
} from '../services/retrievalService'

const SESSION_ID = 'custody-session-1'
const TICKET_ID = 'ticket-1'

/**
 * Build a failure in the shape the real interceptor produces, so the page's
 * error handling is exercised on the path it actually sees in production.
 */
function apiFailure(status, code, message) {
  const error = new Error(message)
  error.response = { status, data: { error: { code, message } }, headers: {} }
  return error
}

const ESTIMATE = {
  estimated_minutes: 12,
  queue_position: 3,
  estimated_range_minutes: [9, 16],
  // A feature -> minutes map, which is what topContributions() consumes via
  // Object.entries(). An array of objects would silently yield no factors.
  contributions: {
    queue_size: 3.4,
    zone_occupancy_percent: 1.2,
  },
  baseline_mae: 2.69,
  mae: 1.73,
  r2: 0.57,
  model_id: 'retrieval-baseline/1.0.0',
  trained_on_synthetic_data: true,
}

function stubTicket(overrides = {}) {
  const ticket = {
    id: TICKET_ID,
    ticket_code: 'TKT-0001',
    status: 'IN_CUSTODY',
    stored_status: 'ACTIVE',
    qr_payload: 'valet:TKT-0001',
    qr_url: '/api/media/qr/ticket-1',
    issued_at: '2026-09-11T09:00:00Z',
    customer_name: 'Aisha Verma',
    vehicle: { plate_number: 'UP 78 AB 1234', make: 'Maruti', model: 'Swift', colour: 'Red' },
    custody_session: {
      id: SESSION_ID,
      status: 'PARKED',
      dropped_off_at: '2026-09-11T09:05:00Z',
      zone_assigned_at: '2026-09-11T09:06:00Z',
      retrieval_requested_at: null,
      ready_for_pickup_at: null,
      picked_up_at: null,
      odometer_at_drop_off: 41200,
    },
    ...overrides,
  }
  vi.mocked(fetchTicket).mockResolvedValue({ ticket })
  return ticket
}

/** The backend answers 404 when nobody has asked for an estimate yet. */
function stubNoEstimate() {
  vi.mocked(fetchRetrievalPrediction).mockRejectedValue(
    apiFailure(404, 'not_found', 'No estimate recorded yet'),
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  stubTicket()
  stubNoEstimate()
})

function renderPage(role = 'VALET_MANAGER') {
  clearSession()
  // The page reads the ticket id from the URL, so it has to be mounted on a
  // matching route rather than rendered bare.
  return renderWithProviders(
    <Routes>
      <Route path="/tickets/:id" element={<TicketDetailPage />} />
    </Routes>,
    { route: `/tickets/${TICKET_ID}`, token: buildToken({ role }) },
  )
}

describe('TicketDetailPage pickup request', () => {
  it('loads the ticket and shows both vocabularies side by side', async () => {
    renderPage()

    expect(await screen.findByText('TKT-0001')).toBeInTheDocument()
    // The projected status is what a person reads; the stored status is shown
    // deliberately so the "keep_map" projection is visible, not taken on trust.
    expect(screen.getByText(/in custody/i)).toBeInTheDocument()
    expect(screen.getByText('ACTIVE')).toBeInTheDocument()
  })

  it('does not imply a zero wait when no estimate exists yet', async () => {
    renderPage()

    expect(await screen.findByText(/no estimate yet/i)).toBeInTheDocument()
    // The button is offered precisely because the number is absent.
    expect(
      screen.getByRole('button', { name: /estimate retrieval time/i }),
    ).toBeInTheDocument()
  })

  it('requests an estimate against the custody session, not the ticket', async () => {
    vi.mocked(requestRetrievalPrediction).mockResolvedValue(ESTIMATE)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/no estimate yet/i)
    await user.click(screen.getByRole('button', { name: /estimate retrieval time/i }))

    // The retrieval endpoints are keyed on the custody session.
    await waitFor(() => expect(requestRetrievalPrediction).toHaveBeenCalledWith(SESSION_ID))
    expect(requestRetrievalPrediction).not.toHaveBeenCalledWith(TICKET_ID)
  })

  it('renders a stored estimate with its factors and the caveat', async () => {
    vi.clearAllMocks()
    stubTicket()
    vi.mocked(fetchRetrievalPrediction).mockResolvedValue(ESTIMATE)
    renderPage()

    // Anchored on the prototype notice, which is unique to this page and sits
    // inside the estimate card. The heading "Retrieval estimate" is not usable
    // as an anchor - the Panel title repeats it - and a bare /12/ would also
    // match the 1.2-minute contribution.
    const section = (await screen.findByText(/baseline model trained on synthetic/i)).closest(
      'section',
    )
    expect(within(section).getByText(/12 minutes/i)).toBeInTheDocument()
    // And the number must be arguable.
    // The two largest contributors are named, so the figure can be argued with.
    expect(within(section).getByText(/queue size/i)).toBeInTheDocument()
    expect(within(section).getByText(/zone occupancy/i)).toBeInTheDocument()
  })

  it('reports a failure to estimate instead of leaving the card silent', async () => {
    vi.mocked(requestRetrievalPrediction).mockRejectedValue(
      apiFailure(503, 'service_unavailable', 'Estimator offline'),
    )
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/no estimate yet/i)
    await user.click(screen.getByRole('button', { name: /estimate retrieval time/i }))

    expect(await screen.findByText(/could not generate an estimate/i)).toBeInTheDocument()
    expect(await screen.findByText(/estimator offline/i)).toBeInTheDocument()
  })

  it('completes the pickup with the odometer and condition acceptance', async () => {
    vi.mocked(completeTicket).mockResolvedValue({ ticket: { id: TICKET_ID, status: 'COMPLETED' } })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('TKT-0001')
    await user.type(screen.getByLabelText(/odometer at pickup/i), '41320')
    await user.click(screen.getByRole('button', { name: /complete ticket/i }))

    await waitFor(() => expect(completeTicket).toHaveBeenCalled())
    expect(vi.mocked(completeTicket).mock.calls[0][0]).toBe(TICKET_ID)
    expect(vi.mocked(completeTicket).mock.calls[0][1]).toEqual({
      odometer: '41320',
      conditionAccepted: true,
    })
    expect(await screen.findByText(/pickup completed/i)).toBeInTheDocument()
  })

  it('sends a disputed condition when the customer does not accept', async () => {
    vi.mocked(completeTicket).mockResolvedValue({ ticket: { id: TICKET_ID } })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('TKT-0001')
    await user.selectOptions(
      screen.getByLabelText(/condition accepted by customer/i),
      'no',
    )
    await user.click(screen.getByRole('button', { name: /complete ticket/i }))

    await waitFor(() => expect(completeTicket).toHaveBeenCalled())
    expect(vi.mocked(completeTicket).mock.calls[0][1]).toMatchObject({
      conditionAccepted: false,
    })
  })

  it('advances the custody session and says the write was recorded', async () => {
    vi.mocked(updateCustodyStatus).mockResolvedValue({
      custody_session: { id: SESSION_ID, status: 'RETRIEVAL_REQUESTED' },
    })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('TKT-0001')
    // From PARKED the offered next states include requesting retrieval.
    const panel = screen.getByText('Move the custody session').closest('section')
    await user.click(within(panel).getByRole('button', { name: /retrieval requested/i }))

    await waitFor(() =>
      expect(updateCustodyStatus).toHaveBeenCalledWith(SESSION_ID, 'RETRIEVAL_REQUESTED'),
    )
    expect(await screen.findByText(/status updated/i)).toBeInTheDocument()
    expect(await screen.findByText(/recorded in the audit log/i)).toBeInTheDocument()
  })

  it('hides the valet handover controls from a customer', async () => {
    renderPage('CUSTOMER')

    await screen.findByText('TKT-0001')
    // The customer still sees the estimate panel and can ask for a number.
    expect(await screen.findByText(/no estimate yet/i)).toBeInTheDocument()
    // But not the controls that move custody or close the ticket.
    expect(screen.queryByText('Move the custody session')).toBeNull()
    expect(screen.queryByText('Complete the ticket')).toBeNull()
    expect(screen.queryByText('Cancel ticket')).toBeNull()
  })

  it('shows a retryable error when the ticket itself fails to load', async () => {
    vi.clearAllMocks()
    vi.mocked(fetchTicket).mockRejectedValue(
      apiFailure(503, 'service_unavailable', 'Backend offline'),
    )
    renderPage()

    // The query client retries once before surfacing an error.
    expect(
      await screen.findByRole('button', { name: /try again/i }, { timeout: 4000 }),
    ).toBeInTheDocument()
  })
})

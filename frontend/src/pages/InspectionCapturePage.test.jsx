/**
 * The inspection capture page - drop-off/pickup photo capture and the demo.
 *
 * This is the screen integration flow 5.3 exercises only at the API level, so
 * the tests here are about what the operator actually sees and sends:
 *
 * - **The payload is multipart with parallel `images` and `angles` lists.** The
 *   backend rejects a count mismatch rather than guessing, because applying one
 *   angle to a mixed set would compare unrelated viewpoints and flag the whole
 *   vehicle. A UI that lost that pairing would be a silent correctness bug.
 * - **Pickup goes to the pickup endpoint.** The stage toggle decides which of
 *   the two capture routes is called, and only pickup triggers comparison.
 * - **Client-side validation runs before the network.** No custody id or no
 *   photographs produces a local message, not a failed request.
 * - **The queue is capped** at `MAX_PHOTOS` rather than growing without bound.
 * - **The demo never claims to be real.** It labels its inputs synthetic while
 *   asserting the findings are genuine pipeline output.
 * - **Unverified findings stay labelled "Potential Damage".** This page renders
 *   findings with `canVerify={false}`: confirming damage is a manager action and
 *   is not offered here.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InspectionCapturePage } from './InspectionCapturePage'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

vi.mock('../services/inspectionService', async (original) => {
  const actual = await original()
  return {
    ...actual,
    captureDropOff: vi.fn(),
    capturePickup: vi.fn(),
    fetchDemoScenarios: vi.fn(),
    runDemoComparison: vi.fn(),
  }
})

import {
  captureDropOff,
  capturePickup,
  fetchDemoScenarios,
  runDemoComparison,
} from '../services/inspectionService'

const CUSTODY_ID = 'custody-session-1'

/** A detection shaped like a stored finding the report endpoints return. */
const DETECTION = {
  id: 'detection-1',
  damage_type: 'SCRATCH',
  severity: 'MINOR',
  region: 'front_bumper',
  angle: 'FRONT',
  confidence: 0.82,
  type_confidence: 0.41,
  presentation_label: 'Potential Damage',
  verification_status: 'UNVERIFIED',
  detection_reasoning: 'Pixel difference above threshold in the front bumper region.',
  evidence_url: '/api/media/evidence/detection-1.png',
  algorithm_params: { region_statistics: { mean_delta: 0.18 } },
}

const CAPTURE_RESULT = {
  stage: 'PICKUP',
  custody_session_id: CUSTODY_ID,
  photo_count: 3,
  damage_count: 1,
  confirmed_count: 0,
  unverified_count: 1,
  rejected_count: 0,
  outcome: 'PENDING_REVIEW',
  is_new_damage: true,
  unmatched_angles: ['Wheel: no drop-off photo at this angle'],
  detections: [DETECTION],
}

const DEMO_RESULT = {
  synthetic: true,
  scenario: 'scratch_on_door',
  scenario_label: 'Scratch on the door',
  disclaimer:
    'Synthetic photographs; real detector. Findings are candidates for review, not confirmed damage.',
  planted_count: 2,
  detected_count: 2,
  drop_off_url: '/api/media/demo/dropoff.png',
  pickup_url: '/api/media/demo/pickup.png',
  comparison_url: '/api/media/demo/comparison.png',
  warnings: [],
  detections: [DETECTION],
  ground_truth: {
    planted_defects: [
      { kind: 'scratch', centre: [120, 240], expected_region: 'door_panel' },
      { kind: 'dent', centre: [40, 90], expected_region: 'rear_quarter' },
    ],
  },
}

const SCENARIOS = {
  scenarios: [
    { name: 'multiple', label: 'Multiple defects', description: 'Two planted defects.' },
    { name: 'clean', label: 'Clean vehicle', description: 'Nothing planted.' },
  ],
}

/** A real File, because the form is multipart and jsdom needs a Blob. */
function photoFile(name) {
  return new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], name, { type: 'image/png' })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchDemoScenarios).mockResolvedValue(SCENARIOS)
})

function renderPage() {
  clearSession()
  return renderWithProviders(<InspectionCapturePage />, {
    route: '/inspect',
    token: buildToken({ role: 'VALET_OPERATOR' }),
  })
}

/** Read the multipart body a capture call was made with. */
function capturePayload(mock) {
  expect(mock).toHaveBeenCalledTimes(1)
  const form = vi.mocked(mock).mock.calls[0][0]
  return {
    custodyId: form.custodyId,
    notes: form.notes,
    images: form.photos.map((photo) => photo.file.name),
    angles: form.photos.map((photo) => photo.angle),
  }
}

describe('InspectionCapturePage', () => {
  it('states that findings are candidates, never a confirmed assessment', async () => {
    renderPage()

    expect(
      await screen.findByText(/candidates for human review, never a confirmed assessment/i),
    ).toBeInTheDocument()
  })

  it('refuses to submit with no custody session id, before any request', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.upload(screen.getByLabelText(/^photographs/i), photoFile('front.png'))
    await user.click(screen.getByRole('button', { name: /upload drop-off set/i }))

    expect(
      await screen.findByText(/enter the custody session id/i),
    ).toBeInTheDocument()
    expect(captureDropOff).not.toHaveBeenCalled()
    expect(capturePickup).not.toHaveBeenCalled()
  })

  it('keeps submit disabled until a photograph is queued, even with a custody id', async () => {
    const user = userEvent.setup()
    renderPage()

    // A custody id alone is not enough: the guard lives on the button's
    // `disabled` attribute, so the form cannot be submitted at all. The
    // "choose at least one photograph" message in submitCapture is a
    // belt-and-braces branch that the UI cannot currently reach.
    await user.type(screen.getByLabelText(/custody session id/i), CUSTODY_ID)
    const submit = screen.getByRole('button', { name: /upload drop-off set/i })
    expect(submit).toBeDisabled()

    await user.click(submit)
    expect(captureDropOff).not.toHaveBeenCalled()
    expect(capturePickup).not.toHaveBeenCalled()
  })

  it('disables submit until a photograph is queued', async () => {
    renderPage()

    expect(
      await screen.findByRole('button', { name: /upload drop-off set/i }),
    ).toBeDisabled()
  })

  it('sends a drop-off set as parallel images and angles lists', async () => {
    vi.mocked(captureDropOff).mockResolvedValue({ ...CAPTURE_RESULT, stage: 'DROP_OFF' })
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText(/custody session id/i), CUSTODY_ID)
    await user.upload(screen.getByLabelText(/^photographs/i), [
      photoFile('front.png'),
      photoFile('rear.png'),
    ])
    await user.type(screen.getByLabelText(/inspector notes/i), 'Customer present at drop-off.')

    // Set the angle on the second photo; the first keeps its default.
    await user.selectOptions(
      screen.getByLabelText(/angle for rear\.png/i),
      'REAR',
    )

    await user.click(screen.getByRole('button', { name: /upload drop-off set/i }))

    await waitFor(() => expect(captureDropOff).toHaveBeenCalled())
    expect(capturePayload(captureDropOff)).toEqual({
      custodyId: CUSTODY_ID,
      notes: 'Customer present at drop-off.',
      images: ['front.png', 'rear.png'],
      // One angle per image, in the same order. A mismatch would make the
      // backend reject the set rather than guess.
      angles: ['GENERAL', 'REAR'],
    })
    // Drop-off must not be sent to the pickup endpoint - that triggers compare.
    expect(capturePickup).not.toHaveBeenCalled()
  })

  it('routes a pickup set to the pickup endpoint', async () => {
    vi.mocked(capturePickup).mockResolvedValue(CAPTURE_RESULT)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /^pickup$/i }))
    expect(
      await screen.findByText(/compares each angle against its drop-off counterpart/i),
    ).toBeInTheDocument()

    await user.type(screen.getByLabelText(/custody session id/i), CUSTODY_ID)
    await user.upload(screen.getByLabelText(/^photographs/i), photoFile('front.png'))
    await user.click(screen.getByRole('button', { name: /upload pickup set/i }))

    await waitFor(() => expect(capturePickup).toHaveBeenCalled())
    expect(capturePayload(capturePickup).images).toEqual(['front.png'])
    expect(captureDropOff).not.toHaveBeenCalled()
  })

  it('caps the queue at twelve photographs', async () => {
    const user = userEvent.setup()
    renderPage()

    const files = Array.from({ length: 15 }, (_, index) => photoFile(`photo-${index}.png`))
    await user.upload(screen.getByLabelText(/^photographs/i), files)

    // MAX_PHOTOS is 12; the overflow is dropped rather than sent.
    expect(screen.getAllByRole('listitem')).toHaveLength(12)
  })

  it('removes a queued photograph', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.upload(screen.getByLabelText(/^photographs/i), [
      photoFile('front.png'),
      photoFile('rear.png'),
    ])
    expect(screen.getAllByRole('listitem')).toHaveLength(2)

    const item = screen.getByLabelText(/angle for rear\.png/i).closest('li')
    await user.click(within(item).getByRole('button', { name: /remove/i }))

    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.queryByLabelText(/angle for rear\.png/i)).toBeNull()
  })

  it('reports the recorded set and links to the full report', async () => {
    vi.mocked(capturePickup).mockResolvedValue(CAPTURE_RESULT)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /^pickup$/i }))
    await user.type(screen.getByLabelText(/custody session id/i), CUSTODY_ID)
    await user.upload(screen.getByLabelText(/^photographs/i), photoFile('front.png'))
    await user.click(screen.getByRole('button', { name: /upload pickup set/i }))

    expect(await screen.findByText(/pickup set recorded/i)).toBeInTheDocument()
    expect(await screen.findByText(/3 photo\(s\) stored/)).toBeInTheDocument()
    expect(screen.getByText(/pending review/)).toBeInTheDocument()
    // Angles with no drop-off counterpart are named, not silently dropped.
    expect(screen.getByText(/no drop-off photo at this angle/i)).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /open full report/i }),
    ).toHaveAttribute('href', `/condition/${CUSTODY_ID}`)
  })

  it('labels an unverified finding "Potential Damage" and offers no verify control', async () => {
    vi.mocked(capturePickup).mockResolvedValue(CAPTURE_RESULT)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /^pickup$/i }))
    await user.type(screen.getByLabelText(/custody session id/i), CUSTODY_ID)
    await user.upload(screen.getByLabelText(/^photographs/i), photoFile('front.png'))
    await user.click(screen.getByRole('button', { name: /upload pickup set/i }))

    expect(await screen.findByText('Potential Damage')).toBeInTheDocument()
    // Verification is a manager action; this operator screen must not offer it.
    expect(screen.queryByRole('button', { name: /confirm damage/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /reject as false positive/i })).toBeNull()
  })

  it('surfaces a backend refusal instead of pretending the set was stored', async () => {
    const failure = new Error('rejected')
    failure.apiError = {
      status: 422,
      code: 'validation_error',
      message: 'Angle count does not match the number of images.',
    }
    vi.mocked(captureDropOff).mockRejectedValue(failure)
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText(/custody session id/i), CUSTODY_ID)
    await user.upload(screen.getByLabelText(/^photographs/i), photoFile('front.png'))
    await user.click(screen.getByRole('button', { name: /upload drop-off set/i }))

    expect(
      await screen.findByText(/angle count does not match the number of images/i),
    ).toBeInTheDocument()
  })

  it('loads demo scenarios only when the demo mode is open', async () => {
    renderPage()

    await screen.findByText(/candidates for human review/i)
    // Capture mode is the default; the scenario list is not fetched for it.
    expect(fetchDemoScenarios).not.toHaveBeenCalled()
  })

  it('labels the demo synthetic while asserting the findings are real', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /synthetic demo/i }))

    // Both halves of the honesty claim are on the panel.
    expect(
      await screen.findByText(/photographs are generated by OpenCV/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/not canned results/i)).toBeInTheDocument()
    expect(await screen.findByRole('option', { name: /multiple defects/i })).toBeInTheDocument()
  })

  it('runs the demo and reports planted against detected with the ground truth', async () => {
    vi.mocked(runDemoComparison).mockResolvedValue(DEMO_RESULT)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /synthetic demo/i }))
    await screen.findByRole('option', { name: /multiple defects/i })
    await user.selectOptions(screen.getByLabelText(/^scenario$/i), 'clean')
    await user.click(screen.getByRole('button', { name: /run comparison/i }))

    await waitFor(() => expect(runDemoComparison).toHaveBeenCalledWith('clean'))
    expect(await screen.findByText(/scratch on the door/i)).toBeInTheDocument()
    expect(screen.getByText(/planted 2, detected 2/)).toBeInTheDocument()
    // The synthetic-input badge travels with the result.
    expect(screen.getByText(/synthetic input/i)).toBeInTheDocument()
    expect(screen.getByText(DEMO_RESULT.disclaimer)).toBeInTheDocument()
  })

  it('shows the planted ground truth so a detection can be checked against it', async () => {
    vi.mocked(runDemoComparison).mockResolvedValue(DEMO_RESULT)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /synthetic demo/i }))
    await screen.findByRole('option', { name: /multiple defects/i })
    await user.click(screen.getByRole('button', { name: /run comparison/i }))

    await screen.findByText(/planted 2, detected 2/)
    await user.click(screen.getByText(/planted ground truth/i))

    expect(await screen.findByText(/scratch at \(120, 240\)/)).toBeInTheDocument()
    expect(screen.getByText(/dent at \(40, 90\)/)).toBeInTheDocument()
  })

  it('reports a demo failure', async () => {
    const failure = new Error('boom')
    failure.apiError = { status: 503, code: 'service_unavailable', message: 'Pipeline offline' }
    vi.mocked(runDemoComparison).mockRejectedValue(failure)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: /synthetic demo/i }))
    await screen.findByRole('option', { name: /multiple defects/i })
    await user.click(screen.getByRole('button', { name: /run comparison/i }))

    expect(await screen.findByText(/pipeline offline/i)).toBeInTheDocument()
  })
})

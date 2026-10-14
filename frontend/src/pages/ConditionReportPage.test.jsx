import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { ConditionReportPage } from './ConditionReportPage'
import { fetchConditionReport, verifyDetection } from '../services/inspectionService'
import { renderWithProviders, buildToken } from '../test/utils'

vi.mock('../services/inspectionService', () => ({
  fetchConditionReport: vi.fn(),
  verifyDetection: vi.fn(),
}))

const CUSTODY_ID = 'custody-1'

function report(overrides = {}) {
  return {
    custody_session_id: CUSTODY_ID,
    ticket_code: 'VT-202609-ABC123',
    vehicle: { plate_number: 'UP85CA1234', make: 'Tata', model: 'Nexon', color: 'Silver', year: 2021 },
    comparison_ready: true,
    requires_review: true,
    total_detections: 1,
    unverified_count: 1,
    confirmed_count: 0,
    detector_disclaimer:
      'Findings are produced by classical OpenCV image differencing, not a trained damage classifier.',
    drop_off: {
      stage: 'DROP_OFF',
      photo_count: 1,
      captured_at: '2026-09-07T09:00:00+00:00',
      photos: [{ url: '/media/inspections/drop_off/a.png', angle: 'LEFT_SIDE' }],
      detections: [],
    },
    pickup: {
      stage: 'PICKUP',
      photo_count: 1,
      captured_at: '2026-09-07T11:00:00+00:00',
      photos: [{ url: '/media/inspections/pickup/b.png', angle: 'LEFT_SIDE' }],
      unmatched_angles: [],
      unverified_count: 1,
      comparisons: [
        {
          angle: 'LEFT_SIDE',
          comparison_url: '/media/comparisons/c.png',
          alignment: { applied: true, quality: 0.94, matches: 30 },
          warnings: [],
          findings: [],
        },
      ],
      detections: [
        {
          id: 'det-1',
          damage_type: 'SCRATCH',
          region: 'LEFT_FRONT_DOOR',
          severity: 'MINOR',
          confidence: 0.82,
          type_confidence: 0.6,
          angle: 'LEFT_SIDE',
          bounding_box: { x: 0.4, y: 0.6, width: 0.2, height: 0.03 },
          detection_reasoning: 'Thin stretched region with new edges.',
          verification_status: 'UNVERIFIED',
          is_verified: false,
          presentation_label: 'Potential Damage',
          evidence_url: '/media/evidence/d.png',
          comparison_url: '/media/comparisons/c.png',
        },
      ],
    },
    ...overrides,
  }
}

function renderReport(role = 'VALET_MANAGER') {
  return renderWithProviders(
    <Routes>
      <Route path="/condition/:custodyId" element={<ConditionReportPage />} />
    </Routes>,
    { route: `/condition/${CUSTODY_ID}`, token: buildToken({ role }) },
  )
}

describe('ConditionReportPage', () => {
  beforeEach(() => {
    vi.mocked(fetchConditionReport).mockReset()
    vi.mocked(verifyDetection).mockReset()
  })

  it('shows the drop-off image, the pickup image and the comparison', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(report())
    renderReport()

    await waitFor(() => expect(screen.getByText('UP85CA1234')).toBeTruthy())

    const sources = screen.getAllByRole('img').map((image) => image.getAttribute('src'))
    expect(sources).toContain('/media/inspections/drop_off/a.png')
    expect(sources).toContain('/media/inspections/pickup/b.png')
    expect(sources).toContain('/media/comparisons/c.png')
  })

  it('shows the detected region, damage type, confidence and verification status', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(report())
    renderReport()

    await waitFor(() => expect(screen.getByText('Potential Damage')).toBeTruthy())
    expect(screen.getByText(/Scratch/)).toBeTruthy()
    expect(screen.getByText(/LEFT FRONT DOOR/i)).toBeTruthy()
    expect(screen.getByText('82%')).toBeTruthy()
    expect(screen.getByText('Awaiting review')).toBeTruthy()
  })

  it('carries the detector disclaimer on the page, not only in the docs', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(report())
    renderReport()

    // It appears more than once on purpose: once as the report-level
    // disclaimer and again on the unverified finding itself.
    await waitFor(() =>
      expect(screen.getAllByText(/not a trained damage classifier/i).length).toBeGreaterThan(0),
    )
  })

  it('offers verification controls to a manager', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(report())
    renderReport('VALET_MANAGER')

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /confirm damage/i })).toBeTruthy(),
    )
  })

  it('withholds verification controls from an operator', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(report())
    renderReport('VALET_OPERATOR')

    await waitFor(() => expect(screen.getByText('Potential Damage')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /confirm damage/i })).toBeNull()
  })

  it('withholds verification controls from the customer', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(report())
    renderReport('CUSTOMER')

    await waitFor(() => expect(screen.getByText('Potential Damage')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /confirm damage/i })).toBeNull()
  })

  it('reports when nothing was detected without claiming the car is fine', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(
      report({
        requires_review: false,
        pickup: {
          stage: 'PICKUP',
          photo_count: 1,
          photos: [],
          unmatched_angles: [],
          unverified_count: 0,
          comparisons: [],
          detections: [],
        },
      }),
    )
    renderReport()

    await waitFor(() =>
      expect(screen.getByText(/not a guarantee the vehicle is undamaged/i)).toBeTruthy(),
    )
  })

  it('explains that a comparison needs both photo sets', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(
      report({ comparison_ready: false, pickup: null, requires_review: false }),
    )
    renderReport()

    await waitFor(() =>
      expect(screen.getByText(/needs both a drop-off and a pickup photo set/i)).toBeTruthy(),
    )
  })

  it('lists angles that could not be compared', async () => {
    vi.mocked(fetchConditionReport).mockResolvedValue(
      report({
        pickup: {
          ...report().pickup,
          unmatched_angles: ["'REAR' was photographed at pickup but not at drop-off."],
        },
      }),
    )
    renderReport()

    await waitFor(() =>
      expect(
        screen.getByText(/'REAR' was photographed at pickup but not at drop-off\./),
      ).toBeTruthy(),
    )
  })

  it('shows an error notice when the report cannot be loaded', async () => {
    // The interceptor's shape: `.apiError` carries the backend's own message.
    vi.mocked(fetchConditionReport).mockRejectedValue(
      Object.assign(new Error('rejected'), {
        apiError: {
          status: 404,
          code: 'NOT_FOUND',
          message: 'No custody session was found with that id.',
        },
      }),
    )
    renderReport()

    // ErrorNotice renders the error's own code and message. The window is
    // widened because QueryProvider retries a failed query once with the
    // default backoff, so the error state lands a little over a second in.
    await waitFor(
      () =>
        expect(screen.getByText('No custody session was found with that id.')).toBeTruthy(),
      { timeout: 4000 },
    )
    expect(screen.getByText('NOT_FOUND')).toBeTruthy()
  })
})

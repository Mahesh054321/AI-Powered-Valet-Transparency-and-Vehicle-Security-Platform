import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { DamageCard } from './DamageCard'
import { renderWithProviders } from '../../test/utils'

/** A finding exactly as the API returns it: unverified by default. */
function potentialFinding(overrides = {}) {
  return {
    id: 'det-1',
    damage_type: 'SCRATCH',
    region: 'LEFT_FRONT_DOOR',
    severity: 'MINOR',
    confidence: 0.82,
    type_confidence: 0.6,
    angle: 'LEFT_SIDE',
    bounding_box: { x: 0.4, y: 0.6, width: 0.2, height: 0.03 },
    detection_reasoning:
      'Region flagged as a potential change: mean difference 59.4. Labelled ' +
      'SCRATCH because the region is thin and stretched. This label is a ' +
      'heuristic reading, not a classification by a trained model.',
    method: 'CLASSICAL_CV',
    verification_status: 'UNVERIFIED',
    is_verified: false,
    presentation_label: 'Potential Damage',
    evidence_url: '/media/evidence/a.png',
    comparison_url: '/media/comparisons/b.png',
    algorithm_params: { region_statistics: { mean_difference: 59.4, fill_ratio: 0.62 } },
    ...overrides,
  }
}

describe('DamageCard', () => {
  it('labels an unreviewed finding as potential damage, never as fact', () => {
    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify={false} onVerify={() => {}} />,
    )

    expect(screen.getByText('Potential Damage')).toBeTruthy()
    expect(screen.getByText(/not a trained damage classifier/i)).toBeTruthy()
  })

  it('shows the damage type, region and both confidences', () => {
    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify={false} onVerify={() => {}} />,
    )

    expect(screen.getByText(/Scratch/)).toBeTruthy()
    expect(screen.getByText(/LEFT FRONT DOOR/i)).toBeTruthy()
    // Region confidence and the weaker label confidence, as percentages.
    expect(screen.getByText('82%')).toBeTruthy()
    expect(screen.getByText('60%')).toBeTruthy()
    expect(screen.getByText(/weaker claim than detecting the change/i)).toBeTruthy()
  })

  it('renders the detected region and the comparison image', () => {
    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify={false} onVerify={() => {}} />,
    )

    const images = screen.getAllByRole('img')
    const sources = images.map((image) => image.getAttribute('src'))
    expect(sources).toContain('/media/evidence/a.png')
    expect(sources).toContain('/media/comparisons/b.png')
  })

  it('hides the verification controls from anyone who may not verify', () => {
    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify={false} onVerify={() => {}} />,
    )

    expect(screen.queryByRole('button', { name: /confirm damage/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /reject as false positive/i })).toBeNull()
  })

  it('offers confirm and reject to a verifier', () => {
    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify onVerify={() => {}} />,
    )

    expect(screen.getByRole('button', { name: /confirm damage/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /reject as false positive/i })).toBeTruthy()
  })

  it('sends the chosen status and the reviewer note', async () => {
    const onVerify = vi.fn().mockResolvedValue({})

    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify onVerify={onVerify} />,
    )

    fireEvent.change(screen.getByLabelText(/verification note/i), {
      target: { value: 'Scuff confirmed with the customer' },
    })
    fireEvent.click(screen.getByRole('button', { name: /confirm damage/i }))

    await waitFor(() => expect(onVerify).toHaveBeenCalled())
    expect(onVerify).toHaveBeenCalledWith('det-1', {
      status: 'CONFIRMED',
      notes: 'Scuff confirmed with the customer',
    })
  })

  it('omits the note when the reviewer left it blank', async () => {
    const onVerify = vi.fn().mockResolvedValue({})

    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify onVerify={onVerify} />,
    )

    fireEvent.click(screen.getByRole('button', { name: /reject as false positive/i }))

    await waitFor(() => expect(onVerify).toHaveBeenCalled())
    expect(onVerify).toHaveBeenCalledWith('det-1', {
      status: 'REJECTED',
      notes: undefined,
    })
  })

  it('shows a confirmed finding as settled and offers to reopen it', () => {
    renderWithProviders(
      <DamageCard
        detection={potentialFinding({
          verification_status: 'CONFIRMED',
          is_verified: true,
          presentation_label: 'Confirmed Damage',
          verification_notes: 'Customer charged.',
        })}
        canVerify
        onVerify={() => {}}
      />,
    )

    expect(screen.getByText('Confirmed Damage')).toBeTruthy()
    // The "not a classifier" caveat belongs to unverified findings only.
    expect(screen.queryByText(/not a trained damage classifier/i)).toBeNull()
    expect(screen.getByRole('button', { name: /reopen/i })).toBeTruthy()
    expect(screen.getByText(/Customer charged\./)).toBeTruthy()
  })

  it('shows a rejected finding as dismissed rather than deleting it', () => {
    renderWithProviders(
      <DamageCard
        detection={potentialFinding({
          verification_status: 'REJECTED',
          is_verified: true,
          presentation_label: 'Dismissed - Not Damage',
        })}
        canVerify
        onVerify={() => {}}
      />,
    )

    expect(screen.getByText('Dismissed - Not Damage')).toBeTruthy()
  })

  it('surfaces a verification failure instead of failing silently', async () => {
    const onVerify = vi.fn().mockRejectedValue(new Error('Only a manager can verify'))

    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify onVerify={onVerify} />,
    )

    fireEvent.click(screen.getByRole('button', { name: /confirm damage/i }))

    await waitFor(() =>
      expect(screen.getByText('Only a manager can verify')).toBeTruthy(),
    )
  })

  it('explains why the region was flagged', () => {
    renderWithProviders(
      <DamageCard detection={potentialFinding()} canVerify={false} onVerify={() => {}} />,
    )

    expect(screen.getByText('Why this was flagged')).toBeTruthy()
    expect(screen.getByText(/mean difference 59.4/)).toBeTruthy()
  })
})

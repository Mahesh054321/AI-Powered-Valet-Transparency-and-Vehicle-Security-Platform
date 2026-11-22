import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InteractiveComparison } from './InteractiveComparison'

describe('InteractiveComparison', () => {
  const mockComparison = {
    angle: 'FRONT',
    drop_off_url: '/media/inspections/dropoff/front.jpg',
    pickup_url: '/media/inspections/pickup/front.jpg',
    comparison_url: '/media/inspections/comparisons/front.png',
    overlay_url: '/media/inspections/overlays/front.png',
    damage_detected: true,
    alignment: {
      applied: true,
      quality: 0.94,
      matches: 120,
    },
  }

  const mockFindings = [
    {
      id: 'det-1',
      angle: 'FRONT',
      damage_type: 'SCRATCH',
      confidence: 0.82,
      bounding_box: { x: 0.2, y: 0.3, width: 0.15, height: 0.05 },
    },
  ]

  it('renders comparison tabs and controls', () => {
    render(
      <InteractiveComparison
        comparison={mockComparison}
        allComparisons={[mockComparison, { ...mockComparison, angle: 'REAR', damage_detected: false }]}
        findings={mockFindings}
      />
    )

    expect(screen.getByRole('tab', { name: /Front/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Rear/i })).toBeInTheDocument()
    expect(screen.getByText('↔ Before/After Slider')).toBeInTheDocument()
    expect(screen.getByText('⧉ Side-by-Side')).toBeInTheDocument()
    expect(screen.getByText('◎ Difference Heatmap')).toBeInTheDocument()
    expect(screen.getByText('▦ Full 3-Panel')).toBeInTheDocument()
  })

  it('switches between view modes', () => {
    render(
      <InteractiveComparison
        comparison={mockComparison}
        allComparisons={[mockComparison]}
        findings={mockFindings}
      />
    )

    // Side by side
    fireEvent.click(screen.getByText('⧉ Side-by-Side'))
    expect(screen.getByText('Drop-off Baseline')).toBeInTheDocument()
    expect(screen.getByText('Pickup Return')).toBeInTheDocument()

    // Difference Heatmap
    fireEvent.click(screen.getByText('◎ Difference Heatmap'))
    expect(screen.getByText(/Color Spectrum = OpenCV Differencing Heatmap/i)).toBeInTheDocument()

    // Full 3-panel
    fireEvent.click(screen.getByText('▦ Full 3-Panel'))
    expect(screen.getByAltText(/Full three-panel comparison/i)).toBeInTheDocument()
  })

  it('shows alignment quality statistics', () => {
    render(
      <InteractiveComparison
        comparison={mockComparison}
        allComparisons={[mockComparison]}
        findings={mockFindings}
      />
    )

    expect(screen.getByText(/94% \(120 features matched\)/i)).toBeInTheDocument()
  })
})

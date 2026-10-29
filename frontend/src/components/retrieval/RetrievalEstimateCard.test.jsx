import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RetrievalEstimateCard } from './RetrievalEstimateCard'

function estimate(overrides = {}) {
  return {
    estimated_minutes: 8,
    min_wait_minutes: 6,
    max_wait_minutes: 10,
    queue_position: 2,
    recommended_dispatch: 'Now',
    reason: 'Baseline linear model estimates 8 minutes.',
    confidence: 0.62,
    contributions: {
      queue_position: 1.57,
      zone_capacity: -0.79,
      vehicle_flagged: 0.01,
    },
    model_version: 'retrieval-baseline/1.0.0',
    method: 'linear-ridge-v1',
    trained_on_synthetic_data: true,
    data_basis: 'synthetic historical retrieval records generated for this MVP',
    disclaimer: 'Trained on synthetic data. Indicative planning number.',
    ...overrides,
  }
}

describe('RetrievalEstimateCard', () => {
  it('shows the three figures the brief requires', () => {
    render(<RetrievalEstimateCard estimate={estimate()} />)

    expect(screen.getByText('Estimated Retrieval')).toBeInTheDocument()
    expect(screen.getByText('Queue Position')).toBeInTheDocument()
    expect(screen.getByText('Recommended Dispatch')).toBeInTheDocument()
    // "8 minutes" reads as "Estimated Retrieval: 8 minutes" once the range is
    // appended, which is what the card renders for these bounds.
    expect(screen.getByText(/6–10 minutes/)).toBeInTheDocument()
    expect(screen.getByText('Now')).toBeInTheDocument()
  })

  it('shows the queue position as a number, not a badge', () => {
    render(<RetrievalEstimateCard estimate={estimate({ queue_position: 4 })} />)

    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('shows a deferred dispatch rather than "Now" when the queue demands it', () => {
    render(
      <RetrievalEstimateCard
        estimate={estimate({ queue_position: 4, recommended_dispatch: 'In 4 minutes' })}
      />
    )

    expect(screen.getByText('In 4 minutes')).toBeInTheDocument()
    expect(screen.queryByText('Now')).not.toBeInTheDocument()
  })

  it('always renders the synthetic-data notice', () => {
    // This card is the most screenshot-able number in the product. If the
    // caveat can be cropped out, the estimate becomes an accuracy claim about a
    // model that has never seen a real yard.
    render(<RetrievalEstimateCard estimate={estimate()} />)

    expect(
      screen.getByText('Trained on synthetic data. Indicative planning number.')
    ).toBeInTheDocument()
  })

  it('still renders a notice when the response omits one', () => {
    render(
      <RetrievalEstimateCard
        estimate={estimate({ disclaimer: null, data_basis: null })}
      />
    )

    const notices = screen.getAllByText(/synthetic/i)
    expect(notices.length).toBeGreaterThan(0)
  })

  it('labels the estimate as coming from a synthetic-data model', () => {
    render(<RetrievalEstimateCard estimate={estimate()} />)

    expect(screen.getByText('Synthetic-data model')).toBeInTheDocument()
  })

  it('lists the contributions that moved the estimate', () => {
    render(<RetrievalEstimateCard estimate={estimate()} />)

    expect(screen.getByText('What moved the estimate')).toBeInTheDocument()
    expect(screen.getByText('Queue position')).toBeInTheDocument()
    expect(screen.getByText('+1.6 min')).toBeInTheDocument()
    expect(screen.getByText('-0.8 min')).toBeInTheDocument()
  })

  it('omits the factor list when nothing moved the estimate materially', () => {
    render(
      <RetrievalEstimateCard
        estimate={estimate({ contributions: { vehicle_flagged: 0.01 } })}
      />
    )

    expect(screen.queryByText('What moved the estimate')).not.toBeInTheDocument()
  })

  it('explains that nothing has been estimated yet, rather than showing zero', () => {
    // An empty card would read as an immediate pickup.
    render(<RetrievalEstimateCard estimate={null} />)

    // Substring match: getByText compares the whole text node, and this
    // sentence continues after the first clause.
    expect(screen.getByText(/No estimate yet/)).toBeInTheDocument()
    expect(screen.queryByText('Queue Position')).not.toBeInTheDocument()
    expect(screen.getByText(/synthetic/i)).toBeInTheDocument()
  })

  it('offers a way to request an estimate only when a handler is supplied', () => {
    const { rerender } = render(<RetrievalEstimateCard estimate={null} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    rerender(<RetrievalEstimateCard estimate={null} onRequest={() => {}} />)
    expect(screen.getByRole('button', { name: /Estimate retrieval time/i })).toBeInTheDocument()
  })

  it('disables the button while a request is in flight', () => {
    render(<RetrievalEstimateCard estimate={null} onRequest={() => {}} busy />)

    expect(screen.getByRole('button', { name: /Estimating/i })).toBeDisabled()
  })

  it('calls the handler when asked to recalculate', () => {
    const onRequest = vi.fn()
    render(<RetrievalEstimateCard estimate={estimate()} onRequest={onRequest} />)

    screen.getByRole('button', { name: /Recalculate estimate/i }).click()

    expect(onRequest).toHaveBeenCalledTimes(1)
  })

  it('renders a placeholder while loading', () => {
    render(<RetrievalEstimateCard loading />)

    expect(screen.getByText(/Working out how long/)).toBeInTheDocument()
  })

  it('shows an em dash rather than a fabricated figure for a missing field', () => {
    render(
      <RetrievalEstimateCard
        estimate={estimate({ queue_position: null, estimated_minutes: null })}
      />
    )

    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })
})

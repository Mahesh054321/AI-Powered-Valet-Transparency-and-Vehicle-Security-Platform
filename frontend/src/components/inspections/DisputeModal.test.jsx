import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DisputeModal } from './DisputeModal'

describe('DisputeModal', () => {
  it('does not render when closed', () => {
    const { container } = render(
      <DisputeModal isOpen={false} onClose={vi.fn()} onSubmit={vi.fn()} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders and submits dispute when opened', () => {
    const handleSubmit = vi.fn()
    const handleClose = vi.fn()

    render(
      <DisputeModal
        isOpen={true}
        onClose={handleClose}
        onSubmit={handleSubmit}
        findings={[
          {
            id: 'det-1',
            damage_type: 'DENT',
            region: 'HOOD',
            confidence: 0.88,
          },
        ]}
      />
    )

    expect(screen.getByText('File Condition Report Dispute')).toBeInTheDocument()

    // Select reason
    const select = screen.getByLabelText(/Dispute Reason/i)
    fireEvent.change(select, { target: { value: 'Pre-existing damage prior to drop-off' } })

    // Type notes
    const textarea = screen.getByLabelText(/Additional Explanation/i)
    fireEvent.change(textarea, { target: { value: 'Small ding was noted at check-in.' } })

    // Submit
    fireEvent.click(screen.getByText('Confirm and Submit Dispute'))

    expect(handleSubmit).toHaveBeenCalledWith({
      reason: 'Pre-existing damage prior to drop-off',
      notes: 'Small ding was noted at check-in.',
      disputedDetectionIds: undefined,
    })
  })
})

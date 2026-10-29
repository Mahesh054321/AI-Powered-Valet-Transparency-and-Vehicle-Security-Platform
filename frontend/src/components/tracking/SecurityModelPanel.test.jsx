import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SecurityModelPanel } from './SecurityModelPanel'

function info(overrides = {}) {
  return {
    available: true,
    model_version: 'security-prototype/1.0.0',
    algorithm: 'logistic',
    escalation_threshold: 0.7,
    feature_columns: [{ name: 'speed_kmph' }, { name: 'zone_breach' }],
    metrics: {
      accuracy: 0.9635,
      precision: 0.9772,
      recall: 0.9353,
      f1: 0.9558,
      baseline_accuracy: 0.5778,
      confusion_matrix: {
        labels: ['NORMAL', 'SUSPICIOUS'],
        matrix: [
          [1666, 27],
          [80, 1157],
        ],
        reading: 'rows are the true label, columns the predicted label',
      },
    },
    disclaimer: 'Prototype trained on synthetic telemetry. Advisory only.',
    ...overrides,
  }
}

describe('SecurityModelPanel', () => {
  it('reports the metrics so a reviewer can judge the model', () => {
    render(<SecurityModelPanel info={info()} />)

    expect(screen.getByText('Accuracy')).toBeInTheDocument()
    expect(screen.getByText('96.4%')).toBeInTheDocument()
    expect(screen.getByText('Recall')).toBeInTheDocument()
    expect(screen.getByText('93.5%')).toBeInTheDocument()
  })

  it('shows the baseline the metrics must beat', () => {
    // Accuracy alone is meaningless without it: a model that always answered
    // "normal" would score 57.8% on this dataset.
    render(<SecurityModelPanel info={info()} />)

    expect(screen.getByText('Baseline')).toBeInTheDocument()
    expect(screen.getByText('57.8%')).toBeInTheDocument()
    expect(screen.getByText(/always answering/i)).toBeInTheDocument()
  })

  it('renders the confusion matrix with its labels', () => {
    render(<SecurityModelPanel info={info()} />)

    expect(
      screen.getByText('rows are the true label, columns the predicted label'),
    ).toBeInTheDocument()
    expect(screen.getByText('1666')).toBeInTheDocument()
    expect(screen.getByText('1157')).toBeInTheDocument()
  })

  it('attaches the disclaimer to the numbers', () => {
    render(<SecurityModelPanel info={info()} />)

    expect(
      screen.getByText('Prototype trained on synthetic telemetry. Advisory only.'),
    ).toBeInTheDocument()
  })

  it('states that the model can never lower a verdict', () => {
    render(<SecurityModelPanel info={info()} />)

    expect(screen.getByText(/never lower it/i)).toBeInTheDocument()
    expect(screen.getByText(/always raises its alert/i)).toBeInTheDocument()
  })

  it('treats an untrained deployment as an ordinary state, not an error', () => {
    // Rules-only is supported. A red banner here would wrongly suggest the
    // security module is broken when it is merely untrained.
    render(
      <SecurityModelPanel
        info={info({ available: false, reason: 'No model artifact at /models/x.json.' })}
      />,
    )

    expect(screen.getByText('Not trained')).toBeInTheDocument()
    expect(screen.getByText('No model artifact at /models/x.json.')).toBeInTheDocument()
    expect(screen.getByText(/train_security_model\.py/)).toBeInTheDocument()
    expect(screen.getByText(/rule engine/)).toBeInTheDocument()
  })

  it('renders a loading state', () => {
    render(<SecurityModelPanel loading />)

    expect(screen.getByText('Loading model status…')).toBeInTheDocument()
  })

  it('says the rules are unaffected when the status query fails', () => {
    // A failed metadata fetch must not read as "security is down": the rule
    // engine does not depend on this endpoint.
    render(<SecurityModelPanel error={{ message: 'boom' }} />)

    expect(screen.getByText('Status unavailable')).toBeInTheDocument()
    expect(screen.getByText(/Rule-based geofence checks are unaffected/)).toBeInTheDocument()
  })

  it('handles no info at all without throwing', () => {
    render(<SecurityModelPanel />)

    expect(screen.getByText('Not loaded')).toBeInTheDocument()
  })
})

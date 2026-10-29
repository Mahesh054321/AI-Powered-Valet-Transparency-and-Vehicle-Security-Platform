import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MlVerdictCard } from './MlVerdictCard'

function ml(overrides = {}) {
  return {
    classification: 'SUSPICIOUS',
    probability: 0.9659,
    severity: 'HIGH',
    reason: 'Prototype classifier: 97% chance of suspicious behaviour.',
    model_version: 'security-prototype/1.0.0',
    top_factors: [
      {
        feature: 'movement_duration_seconds',
        label: 'time spent moving',
        unit: 's',
        value: 900,
        contribution: 4.12,
        direction: 'towards SUSPICIOUS',
      },
    ],
    disclaimer: 'Prototype trained on synthetic telemetry. Advisory only.',
    ...overrides,
  }
}

describe('MlVerdictCard', () => {
  it('always shows the probability next to the label', () => {
    // "Suspicious at 51%" and "suspicious at 99%" are not the same claim. A
    // badge alone would hide the difference, so the number is asserted here.
    render(<MlVerdictCard ml={ml()} />)

    expect(screen.getByText('Classifier: suspicious')).toBeInTheDocument()
    expect(screen.getByText('97%')).toBeInTheDocument()
  })

  it('renders the prototype disclaimer from the payload', () => {
    render(<MlVerdictCard ml={ml()} />)

    expect(
      screen.getByText('Prototype trained on synthetic telemetry. Advisory only.'),
    ).toBeInTheDocument()
  })

  it('shows the model version so a reviewer can tell artifacts apart', () => {
    render(<MlVerdictCard ml={ml()} />)

    expect(screen.getByText('security-prototype/1.0.0')).toBeInTheDocument()
  })

  it('passes the reason through verbatim', () => {
    render(<MlVerdictCard ml={ml()} />)

    expect(
      screen.getByText('Prototype classifier: 97% chance of suspicious behaviour.'),
    ).toBeInTheDocument()
  })

  it('states when the classifier raised the verdict', () => {
    render(<MlVerdictCard ml={ml()} ruleStatus="NORMAL" escalated />)

    // Scoped by class: the note's text is split across elements, so a bare
    // text matcher would resolve to the innermost node and lose the sentence.
    const note = document.querySelector('.ml-verdict__escalation')
    expect(note).not.toBeNull()
    expect(note.textContent).toContain('raised the verdict from NORMAL')
    expect(note.textContent).toContain('can never lower one')
  })

  it('states when the classifier dissented against a breach', () => {
    // The most important case for honesty: the rules said BREACH, the model
    // said benign, and the breach stood. The UI must not hide that the model
    // dissented, nor suggest the dissent changed anything.
    render(
      <MlVerdictCard ml={ml({ probability: 0.02, classification: 'NORMAL' })} ruleStatus="BREACH" />,
    )

    const note = document.querySelector('.ml-verdict__disagreement')
    expect(note).not.toBeNull()
    expect(note.textContent).toContain('BREACH')
    expect(note.textContent).toContain('disagreed')
    expect(note.textContent).toContain('2% suspicious')
    expect(note.textContent).toContain('not grounds to quiet a measurement')
    expect(note.textContent).toContain('still stands')
  })

  it('explains a concerned-but-under-threshold opinion', () => {
    // The mirror case: the model wanted to escalate but was not confident
    // enough. Silence here would look like agreement.
    render(
      <MlVerdictCard
        ml={ml({ probability: 0.6, classification: 'SUSPICIOUS' })}
        ruleStatus="NORMAL"
      />,
    )

    const note = document.querySelector('.ml-verdict__disagreement')
    expect(note).not.toBeNull()
    expect(note.textContent).toContain('60%')
    expect(note.textContent).toContain('escalation threshold')
    expect(note.textContent).toContain('NORMAL')
  })

  it('does not claim an escalation happened when it did not', () => {
    render(
      <MlVerdictCard ml={ml({ classification: 'NORMAL', probability: 0.02 })} ruleStatus="NORMAL" />,
    )

    expect(document.querySelector('.ml-verdict__escalation')).toBeNull()
  })

  it('says nothing extra when the classifier and the rules agree', () => {
    render(
      <MlVerdictCard
        ml={ml({ classification: 'NORMAL', probability: 0.02 })}
        ruleStatus="NORMAL"
      />,
    )

    // An agreeing verdict needs no explanation; rendering one would train
    // reviewers to skim past the notes that do matter.
    expect(document.querySelector('.ml-verdict__disagreement')).toBeNull()
  })

  it('explains the absence of a verdict instead of implying a clean result', () => {
    render(<MlVerdictCard ml={null} />)

    expect(screen.getByText(/No classifier verdict for this fix/i)).toBeInTheDocument()
    expect(screen.getByText(/rule-based verdict above stands on its own/i)).toBeInTheDocument()
  })

  it('lists what pushed the estimate behind a disclosure', () => {
    render(<MlVerdictCard ml={ml()} />)

    expect(screen.getByText('What pushed the estimate')).toBeInTheDocument()
    expect(screen.getByText('time spent moving')).toBeInTheDocument()
    expect(screen.getByText('+4.12 · towards SUSPICIOUS')).toBeInTheDocument()
  })

  it('falls back to the shared notice when the payload omits a disclaimer', () => {
    render(<MlVerdictCard ml={ml({ disclaimer: undefined })} />)

    expect(screen.getByText(/Prototype classifier, trained and evaluated on synthetic telemetry/))
      .toBeInTheDocument()
  })
})

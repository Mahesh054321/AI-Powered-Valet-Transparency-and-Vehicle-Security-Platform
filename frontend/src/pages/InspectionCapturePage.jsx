/**
 * Capture a drop-off or pickup photo set, and run the demo.
 *
 * Two modes share one screen because they answer the same question - "what does
 * this vehicle look like now?" - for different situations:
 *
 * **Capture** uploads real photographs against a custody session. Pickup
 * triggers the comparison in the same request, so the report is ready the moment
 * the photos land.
 *
 * **Demo** runs the same pipeline over photographs OpenCV generated. There are
 * no vehicle cameras in an evaluation environment, and rather than fake results
 * the demo synthesises the *input* and lets the real detector run on it. Both
 * images and the response say so plainly.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, ErrorNotice, Field, Panel, Spinner } from '../components/ui'
import { DamageCard } from '../components/inspections/DamageCard'
import {
  captureDropOff,
  capturePickup,
  fetchDemoScenarios,
  runDemoComparison,
} from '../services/inspectionService'
import { normaliseError } from '../services/apiClient'
import { INSPECTION_ANGLES, angleLabel } from '../utils/constants'

const MAX_PHOTOS = 12

/** A photo the operator has queued, paired with the angle it was taken from. */
function QueuedPhoto({ photo, onAngle, onRemove }) {
  const preview = useMemo(() => URL.createObjectURL(photo.file), [photo.file])

  return (
    <li className="queued-photo">
      <img src={preview} alt={`Preview of ${photo.file.name}`} />
      <div className="queued-photo__controls">
        <select
          value={photo.angle}
          onChange={(event) => onAngle(event.target.value)}
          aria-label={`Angle for ${photo.file.name}`}
        >
          {INSPECTION_ANGLES.map((angle) => (
            <option key={angle.key} value={angle.key}>
              {angle.label}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn--ghost" onClick={onRemove}>
          Remove
        </button>
      </div>
    </li>
  )
}

export function InspectionCapturePage() {
  const [mode, setMode] = useState('capture')
  const [stage, setStage] = useState('dropoff')
  const [custodyId, setCustodyId] = useState('')
  const [photos, setPhotos] = useState([])
  const [notes, setNotes] = useState('')
  const [scenario, setScenario] = useState('multiple')
  const [result, setResult] = useState(null)
  const [formError, setFormError] = useState(null)

  const scenarios = useQuery({
    queryKey: ['demo-scenarios'],
    queryFn: fetchDemoScenarios,
    enabled: mode === 'demo',
  })

  const capture = useMutation({
    mutationFn: (payload) =>
      stage === 'dropoff' ? captureDropOff(payload) : capturePickup(payload),
    onSuccess: (data) => {
      setResult({ kind: 'capture', data })
      setPhotos([])
      setFormError(null)
    },
    onError: (error) => setFormError(error.apiError ?? normaliseError(error)),
  })

  const demo = useMutation({
    mutationFn: () => runDemoComparison(scenario),
    onSuccess: (data) => {
      setResult({ kind: 'demo', data })
      setFormError(null)
    },
    onError: (error) => setFormError(error.apiError ?? normaliseError(error)),
  })

  function addFiles(fileList) {
    const incoming = Array.from(fileList ?? []).map((file) => ({
      file,
      angle: 'GENERAL',
    }))
    setPhotos((current) => [...current, ...incoming].slice(0, MAX_PHOTOS))
  }

  function submitCapture(event) {
    event.preventDefault()
    setFormError(null)

    if (!custodyId.trim()) {
      setFormError({ message: 'Enter the custody session id to attach these photos to.' })
      return
    }
    if (photos.length === 0) {
      setFormError({ message: 'Choose at least one photograph.' })
      return
    }

    capture.mutate({ custodyId: custodyId.trim(), photos, notes })
  }

  const busy = capture.isPending || demo.isPending
  const captureResult = result?.kind === 'capture' ? result.data : null
  const demoResult = result?.kind === 'demo' ? result.data : null

  return (
    <div className="stack">
      <Panel title="Vehicle condition assessment">
        <p className="muted">
          Photographs are compared with classical OpenCV image differencing. Findings
          are candidates for human review, never a confirmed assessment.
        </p>
        <div className="segmented">
          <button
            type="button"
            className={mode === 'capture' ? 'segmented__active' : ''}
            onClick={() => setMode('capture')}
          >
            Capture photos
          </button>
          <button
            type="button"
            className={mode === 'demo' ? 'segmented__active' : ''}
            onClick={() => setMode('demo')}
          >
            Synthetic demo
          </button>
        </div>
      </Panel>

      {mode === 'capture' && (
        <Panel title={stage === 'dropoff' ? 'Drop-off capture' : 'Pickup capture'}>
          <form className="stack" onSubmit={submitCapture}>
            <div className="segmented">
              <button
                type="button"
                className={stage === 'dropoff' ? 'segmented__active' : ''}
                onClick={() => setStage('dropoff')}
              >
                Drop-off
              </button>
              <button
                type="button"
                className={stage === 'pickup' ? 'segmented__active' : ''}
                onClick={() => setStage('pickup')}
              >
                Pickup
              </button>
            </div>

            <Field
              label="Custody session id"
              hint="From the ticket's check-in step. Both photo sets must use the same id."
            >
              <input
                type="text"
                value={custodyId}
                onChange={(event) => setCustodyId(event.target.value)}
                placeholder="00000000-0000-0000-0000-000000000000"
              />
            </Field>

            <Field label="Photographs" hint={`Up to ${MAX_PHOTOS}. Set the angle for each one.`}>
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                multiple
                onChange={(event) => addFiles(event.target.files)}
              />
            </Field>

            {photos.length > 0 && (
              <ul className="queued-photos">
                {photos.map((photo, index) => (
                  <QueuedPhoto
                    key={`${photo.file.name}-${index}`}
                    photo={photo}
                    onAngle={(angle) =>
                      setPhotos((current) =>
                        current.map((item, i) => (i === index ? { ...item, angle } : item)),
                      )
                    }
                    onRemove={() =>
                      setPhotos((current) => current.filter((_, i) => i !== index))
                    }
                  />
                ))}
              </ul>
            )}

            <Field label="Inspector notes" hint="Optional. Stored with the inspection, up to 1000 characters.">
              <textarea
                rows={3}
                maxLength={1000}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </Field>

            {stage === 'pickup' && (
              <p className="muted">
                Submitting a pickup set compares each angle against its drop-off
                counterpart and returns the findings immediately.
              </p>
            )}

            {formError && <ErrorNotice error={formError} />}

            <button type="submit" className="btn btn--primary" disabled={busy || photos.length === 0}>
              {capture.isPending ? 'Uploading…' : `Upload ${stage === 'dropoff' ? 'drop-off' : 'pickup'} set`}
            </button>
          </form>
        </Panel>
      )}

      {mode === 'demo' && (
        <Panel title="Run the pipeline on synthetic photographs">
          <p className="muted">
            The photographs are generated by OpenCV. The findings are real pipeline
            output computed from those pixels, not canned results.
          </p>

          {scenarios.isLoading && <Spinner label="Loading scenarios" />}
          {scenarios.isError && <ErrorNotice error={scenarios.error.apiError ?? normaliseError(scenarios.error)} />}

          <Field label="Scenario">
            <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
              {(scenarios.data?.scenarios ?? []).map((item) => (
                <option key={item.name} value={item.name}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>

          <p className="muted">
            {(scenarios.data?.scenarios ?? []).find((item) => item.name === scenario)
              ?.description ?? ''}
          </p>

          {formError && <ErrorNotice error={formError} />}

          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={() => demo.mutate()}
          >
            {demo.isPending ? 'Running pipeline…' : 'Run comparison'}
          </button>
        </Panel>
      )}

      {demoResult && (
        <Panel
          title="Demo result"
          actions={<Badge tone="badge--info">Synthetic input</Badge>}
        >
          <p className="report__disclaimer">{demoResult.disclaimer}</p>
          <p>
            <strong>{demoResult.scenario_label}</strong> — planted{' '}
            {demoResult.planted_count}, detected {demoResult.detected_count}.
          </p>

          <div className="photo-grid">
            <figure>
              <img src={demoResult.drop_off_url} alt="Synthetic drop-off photograph" />
              <figcaption>Drop-off (synthetic)</figcaption>
            </figure>
            <figure>
              <img src={demoResult.pickup_url} alt="Synthetic pickup photograph" />
              <figcaption>Pickup (synthetic)</figcaption>
            </figure>
            {demoResult.comparison_url && (
              <figure className="photo-grid__wide">
                <img src={demoResult.comparison_url} alt="Before, after and difference" />
                <figcaption>Comparison</figcaption>
              </figure>
            )}
          </div>

          {demoResult.warnings?.length > 0 && (
            <ul className="comparison__warnings">
              {demoResult.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}

          <div className="findings">
            {demoResult.detections.map((detection, index) => (
              <DamageCard
                key={`${detection.damage_type}-${index}`}
                detection={detection}
                canVerify={false}
                onVerify={() => {}}
              />
            ))}
          </div>

          <details>
            <summary>Planted ground truth</summary>
            <ul>
              {demoResult.ground_truth.planted_defects.map((defect) => (
                <li key={`${defect.kind}-${defect.centre.join(',')}`}>
                  {defect.kind} at ({defect.centre[0]}, {defect.centre[1]}) — expected region{' '}
                  {defect.expected_region ?? 'n/a'}
                </li>
              ))}
              {demoResult.ground_truth.planted_defects.length === 0 && (
                <li>Nothing planted — a correct run reports nothing.</li>
              )}
            </ul>
          </details>
        </Panel>
      )}

      {captureResult && (
        <Panel
          title={`${captureResult.stage === 'DROP_OFF' ? 'Drop-off' : 'Pickup'} set recorded`}
          actions={
            <Link className="btn" to={`/condition/${captureResult.custody_session_id}`}>
              Open full report
            </Link>
          }
        >
          <p>
            {captureResult.photo_count} photo(s) stored · {captureResult.damage_count} candidate
            region(s) · outcome <strong>{captureResult.outcome.replace(/_/g, ' ').toLowerCase()}</strong>
          </p>
          {captureResult.unmatched_angles?.length > 0 && (
            <ul className="report__unmatched">
              {captureResult.unmatched_angles.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          <div className="findings">
            {(captureResult.detections ?? []).map((detection) => (
              <DamageCard
                key={detection.id}
                detection={{ ...detection, angle: detection.angle ?? angleLabel(detection.angle) }}
                canVerify={false}
                onVerify={() => {}}
              />
            ))}
          </div>
        </Panel>
      )}
    </div>
  )
}

export default InspectionCapturePage

/**
 * Vehicle condition report.
 *
 * The screen the whole module exists for: drop-off photo, pickup photo, the
 * rendered comparison, and every region the pipeline flagged - with its damage
 * type, confidence, and verification status.
 *
 * Two things are deliberately prominent. First, the disclaimer: these findings
 * come from classical OpenCV image differencing, not a trained classifier, and
 * an unverified one is a candidate rather than a fact. Second, the verification
 * controls, because a heuristic without a human in the loop is just a guess
 * with a number attached.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { useToast } from '../context/ToastContext'
import { Badge, ErrorNotice, Panel, Spinner } from '../components/ui'
import { DamageCard } from '../components/inspections/DamageCard'
import { fetchConditionReport, verifyDetection } from '../services/inspectionService'
import { normaliseError } from '../services/apiClient'
import { useHasRole } from '../hooks/useAuth'
import { ROLES, angleLabel } from '../utils/constants'
import { formatDateTime } from '../utils/format'

/** Verification is narrower than capture: managers and administrators only. */
const VERIFIERS = [ROLES.VALET_MANAGER, ROLES.ADMIN]

function PhotoStrip({ title, inspection }) {
  if (!inspection) {
    return (
      <Panel title={title}>
        <p className="muted">Not captured.</p>
      </Panel>
    )
  }

  return (
    <Panel
      title={title}
      actions={
        <span className="muted">
          {inspection.photo_count} photo{inspection.photo_count === 1 ? '' : 's'}
        </span>
      }
    >
      {inspection.captured_at && (
        <p className="muted">Captured {formatDateTime(inspection.captured_at)}</p>
      )}
      <div className="photo-grid">
        {inspection.photos.map((photo) => (
          <figure key={photo.url}>
            <img src={photo.url} alt={`${title} - ${angleLabel(photo.angle)}`} loading="lazy" />
            <figcaption>{angleLabel(photo.angle)}</figcaption>
          </figure>
        ))}
      </div>
      {inspection.inspector_notes && <p className="muted">{inspection.inspector_notes}</p>}
    </Panel>
  )
}

export function ConditionReportPage() {
  const { custodyId } = useParams()
  const queryClient = useQueryClient()
  const canVerify = useHasRole(VERIFIERS)

  const query = useQuery({
    queryKey: ['condition-report', custodyId],
    queryFn: () => fetchConditionReport(custodyId),
  })

  const toast = useToast()


  const verify = useMutation({
    mutationFn: ({ detectionId, status, notes }) =>
      verifyDetection(detectionId, { status, notes }),
    onSuccess: (_data, variables) => {
      // Confirming or rejecting a finding is a judgement call recorded against
      // the reviewer's name, so it is worth confirming explicitly.
      toast.success(
        variables.status === 'CONFIRMED' ? 'Finding confirmed as damage' : 'Finding rejected',
        'Your decision is recorded in the audit log with your name against it.',
      )
      queryClient.invalidateQueries({ queryKey: ['condition-report', custodyId] })
    },
    onError: (error) =>
      toast.error('Could not record that decision', normaliseError(error).message),
  })

  if (query.isLoading) return <Spinner label="Loading condition report" />
  if (query.isError) {
    return <ErrorNotice error={query.error.apiError ?? normaliseError(query.error)} />
  }

  const report = query.data
  const dropOff = report.drop_off
  const pickup = report.pickup
  const comparisons = pickup?.comparisons ?? []
  const findings = pickup?.detections ?? []

  return (
    <div className="stack">
      <Panel
        title="Vehicle condition report"
        actions={
          <div className="panel__actions">
            {report.requires_review && <Badge tone="badge--warning">Review outstanding</Badge>}
            {report.confirmed_count > 0 && (
              <Badge tone="badge--danger">{report.confirmed_count} confirmed</Badge>
            )}
          </div>
        }
      >
        <div className="report__identity">
          <div>
            <h3>{report.vehicle?.plate_number ?? 'Unknown vehicle'}</h3>
            <p className="muted">
              {[report.vehicle?.year, report.vehicle?.make, report.vehicle?.model, report.vehicle?.color]
                .filter(Boolean)
                .join(' · ') || 'No vehicle details recorded'}
            </p>
          </div>
          {report.ticket_code && (
            <p className="muted">
              Ticket <strong>{report.ticket_code}</strong>
            </p>
          )}
        </div>

        <p className="report__disclaimer">{report.detector_disclaimer}</p>

        {!report.comparison_ready && (
          <p className="report__incomplete">
            A comparison needs both a drop-off and a pickup photo set. Capture
            both to see the before/after analysis.
          </p>
        )}
      </Panel>

      <div className="report__stages">
        <PhotoStrip title="Drop-off" inspection={dropOff} />
        <PhotoStrip title="Pickup" inspection={pickup} />
      </div>

      {comparisons.length > 0 && (
        <Panel title="Comparison">
          {comparisons.map((comparison) => (
            <section key={comparison.angle} className="comparison">
              <h4>{angleLabel(comparison.angle)}</h4>
              {comparison.comparison_url ? (
                <img
                  className="comparison__image"
                  src={comparison.comparison_url}
                  alt={`Before, after and difference for the ${angleLabel(comparison.angle)} view`}
                />
              ) : (
                <p className="muted">No comparison image stored for this angle.</p>
              )}
              {comparison.alignment && (
                <p className="muted comparison__meta">
                  Frame alignment {comparison.alignment.applied ? 'applied' : 'not applied'} ·
                  quality {(comparison.alignment.quality ?? 0).toFixed(2)} · {comparison.alignment.matches} feature
                  matches
                </p>
              )}
              {comparison.warnings?.length > 0 && (
                <ul className="comparison__warnings">
                  {comparison.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              )}
            </section>
          ))}
        </Panel>
      )}

      <Panel
        title={`Detected regions (${findings.length})`}
        actions={
          pickup?.unverified_count > 0 ? (
            <Badge tone="badge--warning">{pickup.unverified_count} awaiting review</Badge>
          ) : null
        }
      >
        {findings.length === 0 ? (
          <p className="muted">
            {report.comparison_ready
              ? 'No region exceeded the detection thresholds. That is not a guarantee the vehicle is undamaged - it means nothing stood out against these parameters.'
              : 'Nothing has been compared yet.'}
          </p>
        ) : (
          <div className="findings">
            {findings.map((detection) => (
              <DamageCard
                key={detection.id}
                detection={detection}
                canVerify={canVerify}
                busy={verify.isPending}
                onVerify={(detectionId, payload) =>
                  verify.mutateAsync({ detectionId, ...payload })
                }
              />
            ))}
          </div>
        )}
        {verify.isError && <ErrorNotice error={verify.error.apiError ?? normaliseError(verify.error)} />}
      </Panel>

      {pickup?.unmatched_angles?.length > 0 && (
        <Panel title="Angles that could not be compared">
          <ul className="report__unmatched">
            {pickup.unmatched_angles.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}

export default ConditionReportPage

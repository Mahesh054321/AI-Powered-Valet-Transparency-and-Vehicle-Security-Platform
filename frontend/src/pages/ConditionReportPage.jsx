/**
 * Vehicle condition report.
 *
 * Drop-off photo, pickup photo, rendered OpenCV comparison, and candidate regions
 * the pipeline flagged - with damage type, confidence, and verification status.
 *
 * Includes interactive before/after split slider, side-by-side view, difference
 * heatmap with opacity slider, candidate bounding box overlays, customer dispute
 * submission, manager re-comparison, and printable summary.
 */

import React, { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { useToast } from '../context/ToastContext'
import { Badge, ConfirmDialog, ErrorNotice, Panel, Spinner } from '../components/ui'
import { DamageCard } from '../components/inspections/DamageCard'
import { InteractiveComparison } from '../components/inspections/InteractiveComparison'
import { DisputeModal } from '../components/inspections/DisputeModal'
import {
  fetchConditionReport,
  verifyDetection,
  disputeInspection,
  recompareInspection,
  resolveDispute,
} from '../services/inspectionService'
import { normaliseError } from '../services/apiClient'
import { useHasRole } from '../hooks/useAuth'
import { ROLES, angleLabel } from '../utils/constants'
import { formatDateTime } from '../utils/format'

/** Verification is narrower than capture: managers and administrators only. */
const VERIFIERS = [ROLES.VALET_MANAGER, ROLES.ADMIN]

function PhotoStrip({ title, inspection, onSelectPhoto }) {
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
          <figure
            key={photo.url}
            onClick={() => onSelectPhoto && onSelectPhoto(photo)}
            style={{ cursor: 'pointer' }}
          >
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
  const [selectedAngle, setSelectedAngle] = useState(null)
  const [highlightedFindingId, setHighlightedFindingId] = useState(null)
  const [isDisputeOpen, setIsDisputeOpen] = useState(false)
  const [isResolveDisputeOpen, setIsResolveDisputeOpen] = useState(false)
  const [resolutionNotes, setResolutionNotes] = useState('')
  const [resolutionError, setResolutionError] = useState('')

  const query = useQuery({
    queryKey: ['condition-report', custodyId],
    queryFn: () => fetchConditionReport(custodyId),
  })

  const toast = useToast()

  const verify = useMutation({
    mutationFn: ({ detectionId, status, notes }) =>
      verifyDetection(detectionId, { status, notes }),
    onSuccess: (_data, variables) => {
      toast.success(
        variables.status === 'CONFIRMED' ? 'Finding confirmed as damage' : 'Finding rejected',
        'Your decision is recorded in the audit log with your name against it.',
      )
      queryClient.invalidateQueries({ queryKey: ['condition-report', custodyId] })
    },
    onError: (error) =>
      toast.error('Could not record that decision', normaliseError(error).message),
  })

  const dispute = useMutation({
    mutationFn: (payload) => disputeInspection(custodyId, payload),
    onSuccess: () => {
      toast.success(
        'Dispute submitted successfully',
        'Custody status has been set to DISPUTED and flagged for management review.',
      )
      setIsDisputeOpen(false)
      queryClient.invalidateQueries({ queryKey: ['condition-report', custodyId] })
    },
    onError: (error) =>
      toast.error('Failed to submit dispute', normaliseError(error).message),
  })

  const recompare = useMutation({
    mutationFn: () => recompareInspection(custodyId),
    onSuccess: () => {
      toast.success(
        'Comparison pipeline re-run completed',
        'Image differences and candidate regions refreshed from high-resolution originals.',
      )
      queryClient.invalidateQueries({ queryKey: ['condition-report', custodyId] })
    },
    onError: (error) =>
      toast.error('Could not re-run comparison', normaliseError(error).message),
  })

  const resolveDisputeMutation = useMutation({
    mutationFn: (notes) => resolveDispute(custodyId, { notes }),
    onSuccess: () => {
      setIsResolveDisputeOpen(false)
      setResolutionNotes('')
      setResolutionError('')
      toast.success(
        'Dispute resolved and custody completed',
        'The ticket is complete, the custody session is closed, and the parking space is free.',
      )
      queryClient.invalidateQueries({ queryKey: ['condition-report', custodyId] })
      queryClient.invalidateQueries({ queryKey: ['manager-dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['staff-dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['customer-dashboard'] })
    },
    onError: (error) =>
      toast.error('Could not resolve the dispute', normaliseError(error).message),
  })

  if (query.isLoading) return <Spinner label="Loading condition report" />
  if (query.isError) {
    return <ErrorNotice error={query.error.apiError ?? normaliseError(query.error)} />
  }

  const report = query.data
  const dropOff = report.drop_off
  const pickup = report.pickup
  const comparisons = pickup?.comparisons ?? report?.comparisons ?? []
  const findings = pickup?.detections ?? []

  // Active comparison viewpoint
  const activeComparison =
    comparisons.find((c) => c.angle === selectedAngle) ||
    comparisons[0] ||
    null

  const handlePrint = () => {
    window.print()
  }

  return (
    <div className="stack condition-report-page">
      {/* Report Header Panel */}
      <Panel
        title="Vehicle condition report"
        actions={
          <div className="panel__actions" style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button type="button" className="btn btn--sm btn--ghost" onClick={handlePrint}>
              ⎙ Print Report
            </button>

            {canVerify && (
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => recompare.mutate()}
                disabled={recompare.isPending}
              >
                {recompare.isPending ? 'Comparing...' : '↺ Re-run CV Pipeline'}
              </button>
            )}

            {canVerify && report.custody_status === 'DISPUTED' && (
              <button
                type="button"
                className="btn btn--sm btn--primary"
                onClick={() => setIsResolveDisputeOpen(true)}
              >
                Resolve dispute & complete custody
              </button>
            )}

            {report.custody_status === 'DISPUTED' ? (
              <Badge tone="badge--warning">Dispute awaiting manager review</Badge>
            ) : report.custody_status !== 'CHECKED_OUT' ? (
              <button
                type="button"
                className="btn btn--sm btn--danger-outline"
                onClick={() => setIsDisputeOpen(true)}
              >
                ⚠ File Condition Dispute
              </button>
            ) : null}

            {report.requires_review && <Badge tone="badge--warning">Review outstanding</Badge>}
            {report.confirmed_count > 0 && (
              <Badge tone="badge--danger">{report.confirmed_count} confirmed</Badge>
            )}
            {report.confirmed_count === 0 && !report.requires_review && report.comparison_ready && (
              <Badge tone="badge--success">Clean Report</Badge>
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
              Ticket <strong>{report.ticket_code}</strong> · Custody <code>{custodyId.slice(0, 8)}...</code>
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
        {report.custody_status === 'CHECKED_OUT' && (
          <p className="report__incomplete">
            This custody period is complete, so a new dispute cannot be filed
            from this report. Contact valet management if you need further help.
          </p>
        )}
      </Panel>

      {/* Interactive Before/After Visual Comparison Suite */}
      {comparisons.length > 0 && (
        <Panel
          title="Interactive Visual Comparison"
          actions={
            <span className="muted">
              {comparisons.length} angle{comparisons.length === 1 ? '' : 's'} registered
            </span>
          }
        >
          <InteractiveComparison
            comparison={activeComparison}
            allComparisons={comparisons}
            onSelectAngle={(angle) => setSelectedAngle(angle)}
            findings={findings}
            onFindingClick={(finding) => {
              setHighlightedFindingId(finding.id)
              const el = document.getElementById(`finding-${finding.id}`)
              if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
            }}
          />
        </Panel>
      )}

      {/* Baseline and Return Photo Sets */}
      <div className="report__stages">
        <PhotoStrip
          title="Drop-off"
          inspection={dropOff}
          onSelectPhoto={(p) => setSelectedAngle(p.angle)}
        />
        <PhotoStrip
          title="Pickup"
          inspection={pickup}
          onSelectPhoto={(p) => setSelectedAngle(p.angle)}
        />
      </div>

      {/* Findings and Region Candidates */}
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
              <div
                key={detection.id}
                id={`finding-${detection.id}`}
                className={highlightedFindingId === detection.id ? 'finding--highlight' : ''}
              >
                <DamageCard
                  detection={detection}
                  canVerify={canVerify}
                  busy={verify.isPending}
                  onVerify={(detectionId, payload) =>
                    verify.mutateAsync({ detectionId, ...payload })
                  }
                />
              </div>
            ))}
          </div>
        )}
        {verify.isError && <ErrorNotice error={verify.error.apiError ?? normaliseError(verify.error)} />}
      </Panel>

      {/* Unmatched Angles Warning */}
      {pickup?.unmatched_angles?.length > 0 && (
        <Panel title="Angles that could not be compared">
          <ul className="report__unmatched">
            {pickup.unmatched_angles.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Panel>
      )}

      {/* Dispute Modal */}
      <DisputeModal
        isOpen={isDisputeOpen}
        onClose={() => setIsDisputeOpen(false)}
        onSubmit={(payload) => dispute.mutate(payload)}
        findings={findings}
        isSubmitting={dispute.isPending}
      />

      <ConfirmDialog
        open={isResolveDisputeOpen}
        title="Resolve dispute and complete custody?"
        confirmLabel="Resolve and complete"
        busy={resolveDisputeMutation.isPending}
        onConfirm={() => {
          const notes = resolutionNotes.trim()
          if (!notes) {
            setResolutionError('Add a short note describing the resolution.')
            return
          }
          resolveDisputeMutation.mutate(notes)
        }}
        onCancel={() => {
          setIsResolveDisputeOpen(false)
          setResolutionNotes('')
          setResolutionError('')
        }}
      >
        <p>
          This closes the custody session, completes the ticket, and frees the
          parking space. The action is recorded under your manager account.
        </p>
        <label className="field">
          <span>Resolution note (required)</span>
          <textarea
            rows={3}
            value={resolutionNotes}
            maxLength={500}
            onChange={(event) => {
              setResolutionNotes(event.target.value)
              setResolutionError('')
            }}
            placeholder="Summarize how the dispute was reviewed and resolved."
          />
        </label>
        {resolutionError && <p className="field__error" role="alert">{resolutionError}</p>}
      </ConfirmDialog>
    </div>
  )
}

export default ConditionReportPage

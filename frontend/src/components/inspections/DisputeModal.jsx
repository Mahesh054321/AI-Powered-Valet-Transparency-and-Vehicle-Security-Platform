import React, { useState } from 'react'

export function DisputeModal({
  isOpen,
  onClose,
  onSubmit,
  findings = [],
  isSubmitting = false,
}) {
  const [reason, setReason] = useState('')
  const [notes, setNotes] = useState('')
  const [selectedFindings, setSelectedFindings] = useState([])
  const [error, setError] = useState(null)

  if (!isOpen) return null

  const handleToggleFinding = (id) => {
    setSelectedFindings((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!reason.trim()) {
      setError('Please provide a reason for the dispute.')
      return
    }
    setError(null)
    onSubmit({
      reason: reason.trim(),
      notes: notes.trim() || undefined,
      disputedDetectionIds: selectedFindings.length > 0 ? selectedFindings : undefined,
    })
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>File Condition Report Dispute</h3>
          <button type="button" className="btn btn--ghost btn--icon" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="modal-body stack">
          <p className="muted">
            If you believe any flagged damage was pre-existing or a false positive caused by
            reflections/lighting, please submit your dispute. Management will review the high-resolution
            drop-off baseline photos.
          </p>

          {error && <div className="notice notice--error">{error}</div>}

          <div className="form-group">
            <label htmlFor="dispute-reason">
              Dispute Reason <span className="text-danger">*</span>
            </label>
            <select
              id="dispute-reason"
              className="select"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
            >
              <option value="">Select a reason...</option>
              <option value="Pre-existing damage prior to drop-off">
                Pre-existing damage prior to drop-off
              </option>
              <option value="Lighting, shadow, or reflection artifact">
                Lighting, shadow, or reflection artifact
              </option>
              <option value="Dirt, water drop, or removable surface mark">
                Dirt, water drop, or removable surface mark
              </option>
              <option value="Damage severity or classification incorrect">
                Damage severity or classification incorrect
              </option>
              <option value="Other customer concern">Other customer concern</option>
            </select>
          </div>

          {findings.length > 0 && (
            <div className="form-group">
              <label>Select Specific Findings Disputed (Optional)</label>
              <div className="dispute-findings-list">
                {findings.map((f, idx) => (
                  <label key={f.id || idx} className="dispute-finding-item">
                    <input
                      type="checkbox"
                      checked={selectedFindings.includes(f.id)}
                      onChange={() => handleToggleFinding(f.id)}
                    />
                    <span>
                      #{idx + 1} <strong>{f.damage_type}</strong> on {f.region} ({(f.confidence * 100).toFixed(0)}% confidence)
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}

          <div className="form-group">
            <label htmlFor="dispute-notes">Additional Explanation / Customer Notes</label>
            <textarea
              id="dispute-notes"
              className="textarea"
              rows={3}
              placeholder="Provide any details, timestamps, or prior service history..."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={500}
            />
            <small className="muted">{notes.length}/500 characters</small>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn--ghost" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn--danger" disabled={isSubmitting}>
              {isSubmitting ? 'Submitting Dispute...' : 'Confirm and Submit Dispute'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default DisputeModal

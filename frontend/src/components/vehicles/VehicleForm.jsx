/**
 * Vehicle form, shared by registration and editing.
 *
 * The fields are the ones the brief calls for: registration number, make,
 * model, colour and optional notes. Year is offered too because the schema
 * already carries it and it costs one input.
 *
 * Validation here is a courtesy. The backend is authoritative - it checks the
 * plate against the Indian registration formats and enforces uniqueness through
 * a partial unique index - so a mismatch surfaces as a field error from the
 * response rather than as a broken form.
 */

import { useState } from 'react'
import { normaliseError } from '../../services/apiClient'
import { ErrorNotice, Field } from '../ui'

const EMPTY = { plateNumber: '', make: '', model: '', year: '', color: '', notes: '' }

export function VehicleForm({ initialValues, submitLabel, onSubmit, onCancel }) {
  const [form, setForm] = useState({ ...EMPTY, ...initialValues })
  const [fieldErrors, setFieldErrors] = useState({})
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)

  function update(field) {
    return (event) => setForm({ ...form, [field]: event.target.value })
  }

  function validate() {
    const errors = {}
    // Loose shape check only: separators optional, because the backend accepts
    // "UP 85 CA 1234", "UP85CA1234" and the Bharat "25BH0987AB" alike.
    const plate = form.plateNumber.replace(/[\s-]/g, '').toUpperCase()
    if (plate.length < 8 || plate.length > 12 || !/^[A-Z0-9]+$/.test(plate)) {
      errors.plateNumber = 'Enter the registration number, e.g. UP85CA1234.'
    }
    if (form.make.trim().length < 2) errors.make = 'Enter the make.'
    if (form.year && (!/^\d{4}$/.test(form.year) || Number(form.year) < 1950 || Number(form.year) > 2100)) {
      errors.year = 'Enter a year between 1950 and 2100.'
    }
    if (form.notes.length > 500) errors.notes = 'Notes must be 500 characters or fewer.'
    return errors
  }

  async function handleSubmit(event) {
    event.preventDefault()
    const errors = validate()
    setFieldErrors(errors)
    setError(null)
    if (Object.keys(errors).length > 0) return

    setPending(true)
    try {
      await onSubmit(form)
    } catch (caught) {
      const apiError = caught?.apiError ?? normaliseError(caught)
      setError(apiError)
      // Put a per-field backend error next to the field it belongs to.
      const field = apiError?.details?.field
      if (field) {
        const local = { plate_number: 'plateNumber' }[field] ?? field
        setFieldErrors((current) => ({ ...current, [local]: apiError.message }))
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div className="grid grid--2">
        <Field
          label="Registration number"
          hint="Indian format, separators optional"
          error={fieldErrors.plateNumber}
        >
          <input
            name="plateNumber"
            autoComplete="off"
            placeholder="UP85CA1234"
            value={form.plateNumber}
            onChange={update('plateNumber')}
          />
        </Field>

        <Field label="Make" error={fieldErrors.make}>
          <input
            name="make"
            placeholder="Tata"
            value={form.make}
            onChange={update('make')}
          />
        </Field>

        <Field label="Model" hint="Optional" error={fieldErrors.model}>
          <input name="model" placeholder="Nexon" value={form.model} onChange={update('model')} />
        </Field>

        <Field label="Year" hint="Optional" error={fieldErrors.year}>
          <input
            name="year"
            inputMode="numeric"
            placeholder="2022"
            value={form.year}
            onChange={update('year')}
          />
        </Field>

        <Field label="Colour" hint="Optional" error={fieldErrors.color}>
          <input name="color" placeholder="Silver" value={form.color} onChange={update('color')} />
        </Field>
      </div>

      <Field label="Notes" hint="Optional - anything the valet should know" error={fieldErrors.notes}>
        <textarea
          name="notes"
          rows={3}
          placeholder="Roof rack fitted. Spare key in the glovebox."
          value={form.notes}
          onChange={update('notes')}
        />
      </Field>

      <ErrorNotice error={error} />

      <div className="btn-row">
        <button type="submit" className="btn btn--primary" disabled={pending}>
          {pending ? 'Saving…' : submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="btn btn--ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}

export default VehicleForm

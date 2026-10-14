/**
 * The shared vehicle form, used by both registration and editing.
 *
 * The behaviour worth pinning is where the client-side validation stops and the
 * backend's authority begins:
 *
 * - **Local validation is a courtesy, not a gate.** It checks plate shape,
 *   make, year range and note length, and blocks the request when they fail.
 *   It must not be so strict that it rejects a plate the backend accepts:
 *   Indian registrations come as "UP 85 CA 1234", "UP85CA1234" and the Bharat
 *   series "25BH0987AB", and separators are optional.
 * - **A backend field error lands next to the field it belongs to.** The
 *   response's `details.field` is snake_case (`plate_number`); the form maps it
 *   to its own camelCase field name. Without that mapping a duplicate-plate
 *   rejection would show a generic banner and leave the user guessing which
 *   input to change.
 * - **The error banner still appears.** The field note and the banner are
 *   deliberately redundant, so the failure is visible even when the field is
 *   scrolled out of view.
 * - **Submitting is idempotent while in flight.** The button disables and
 *   relabels, so a double click cannot register the same vehicle twice.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { VehicleForm } from './VehicleForm'

const VALID = { plateNumber: 'UP85CA1234', make: 'Tata', model: 'Nexon', year: '2022', color: 'Silver', notes: '' }

function renderForm(overrides = {}) {
  const onSubmit = vi.fn().mockResolvedValue({})
  const onCancel = vi.fn()
  render(
    <VehicleForm
      initialValues={{}}
      submitLabel="Register vehicle"
      onSubmit={onSubmit}
      onCancel={onCancel}
      {...overrides}
    />,
  )
  return { onSubmit, onCancel }
}

/**
 * The form's state keys are camelCase; the on-screen labels are sentences. Map
 * one to the other so a test can say "fill the plate" rather than repeating the
 * label wording, which would break every test if the copy is ever reworded.
 */
const LABELS = {
  plateNumber: 'registration number',
  make: 'make',
  model: 'model',
  year: 'year',
  color: 'colour',
  notes: 'notes',
}

async function fill(user, values) {
  for (const [field, value] of Object.entries(values)) {
    const input = screen.getByLabelText(new RegExp(`^${LABELS[field]}`, 'i'))
    await user.clear(input)
    if (value) await user.type(input, value)
  }
}

beforeEach(() => vi.clearAllMocks())

describe('VehicleForm', () => {
  it('submits the whole form when every field is valid', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, VALID)
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(vi.mocked(onSubmit).mock.calls[0][0]).toEqual(VALID)
  })

  it('rejects an empty plate without calling the API', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, { ...VALID, plateNumber: '' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(
      await screen.findByText(/enter the registration number/i),
    ).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it.each([
    ['UP 85 CA 1234', 'spaced'],
    ['UP-85-CA-1234', 'hyphenated'],
    ['UP85CA1234', 'compact'],
    ['25BH0987AB', 'Bharat series'],
  ])('accepts a %s plate (%s), because the backend accepts all of these', async (plate) => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, { ...VALID, plateNumber: plate })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/enter the registration number/i)).toBeNull()
  })

  it('rejects a plate that is too short', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, { ...VALID, plateNumber: 'UP85CA' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(await screen.findByText(/enter the registration number/i)).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('rejects a plate containing characters outside the alphanumeric set', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, { ...VALID, plateNumber: 'UP85CA12@4' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(await screen.findByText(/enter the registration number/i)).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('rejects a make shorter than two characters', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, { ...VALID, make: 'T' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(await screen.findByText(/enter the make/i)).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it.each([
    ['1949', 'before the accepted range'],
    ['2101', 'after the accepted range'],
    ['22ab', 'not four digits'],
  ])('rejects the year %s (%s)', async (year) => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, { ...VALID, year })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(
      await screen.findByText(/enter a year between 1950 and 2100/i),
    ).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('accepts a blank year, since the field is optional', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, { ...VALID, year: '' })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  })

  it('rejects notes longer than 500 characters', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderForm()

    await fill(user, { ...VALID, notes: 'x'.repeat(501) })
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(
      await screen.findByText(/notes must be 500 characters or fewer/i),
    ).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('prefills from initialValues when editing', async () => {
    render(
      <VehicleForm
        initialValues={{ plateNumber: 'UP78AB1234', make: 'Maruti', model: 'Swift' }}
        submitLabel="Save changes"
        onSubmit={vi.fn().mockResolvedValue({})}
      />,
    )

    expect(screen.getByLabelText(/^registration number/i)).toHaveValue('UP78AB1234')
    expect(screen.getByLabelText(/^make/i)).toHaveValue('Maruti')
  })

  it('puts a backend field error next to the field it belongs to', async () => {
    const failure = Object.assign(new Error('duplicate'), {
      apiError: {
        status: 409,
        code: 'conflict',
        message: 'A vehicle with this registration number already exists.',
        details: { field: 'plate_number' },
      },
    })
    const onSubmit = vi.fn().mockRejectedValue(failure)
    const user = userEvent.setup()
    renderForm({ onSubmit })

    await fill(user, VALID)
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    // The snake_case field name from the envelope is mapped to plateNumber.
    const field = screen.getByLabelText(/^registration number/i).closest('.field')
    expect(within(field).getByText(/already exists/i)).toBeInTheDocument()
  })

  it('also shows the banner, so the failure is visible when the field is off-screen', async () => {
    const failure = Object.assign(new Error('duplicate'), {
      apiError: {
        status: 409,
        code: 'conflict',
        message: 'A vehicle with this registration number already exists.',
        details: { field: 'plate_number' },
      },
    })
    const onSubmit = vi.fn().mockRejectedValue(failure)
    const user = userEvent.setup()
    renderForm({ onSubmit })

    await fill(user, VALID)
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    // Deliberately redundant with the field note: one is local, one is global.
    expect(await screen.findAllByText(/already exists/i)).toHaveLength(2)
  })

  it('shows a generic notice when the backend names no field', async () => {
    const failure = Object.assign(new Error('boom'), {
      apiError: { status: 500, code: 'internal_error', message: 'Unexpected server error.' },
    })
    const onSubmit = vi.fn().mockRejectedValue(failure)
    const user = userEvent.setup()
    renderForm({ onSubmit })

    await fill(user, VALID)
    await user.click(screen.getByRole('button', { name: /register vehicle/i }))

    expect(await screen.findByText(/unexpected server error/i)).toBeInTheDocument()
  })

  it('disables and relabels submit while the request is in flight', async () => {
    let release
    const onSubmit = vi.fn(() => new Promise((resolve) => { release = resolve }))
    const user = userEvent.setup()
    renderForm({ onSubmit })

    await fill(user, VALID)
    const submit = screen.getByRole('button', { name: /register vehicle/i })
    await user.click(submit)

    const saving = await screen.findByRole('button', { name: /saving/i })
    expect(saving).toBeDisabled()
    // Cancel is disabled too, so the form cannot be abandoned mid-write.
    expect(screen.getByRole('button', { name: /cancel/i })).toBeDisabled()

    release({})
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /register vehicle/i })).toBeEnabled(),
    )
  })

  it('calls onCancel and omits the button entirely when none is supplied', async () => {
    const user = userEvent.setup()
    const { onCancel } = renderForm()

    await user.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)

    // Rendered on its own, because the first form still has its Cancel button.
    const { unmount } = render(
      <VehicleForm initialValues={{}} submitLabel="Save" onSubmit={vi.fn()} />,
    )
    const second = screen.getAllByRole('button', { name: /^save$/i })
    expect(second).toHaveLength(1)
    unmount()
  })
})

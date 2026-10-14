import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { normaliseError } from '../services/apiClient'
import { register } from '../services/authService'
import { APP_CONFIG } from '../config/appConfig'
import { ErrorNotice, Field, Panel } from '../components/ui'
import { Logo } from '../components/ui/Logo'

/**
 * Customer registration.
 *
 * The form cannot ask for a role: a public visitor always gets a CUSTOMER
 * account, and the backend enforces that independently. Staff accounts are
 * created by an administrator from within the app.
 *
 * Validation mirrors the backend's rules so the visitor gets feedback before a
 * round trip, but the backend remains the authority - a mismatch shows up as a
 * field error from the response, not as a broken form.
 */

const PASSWORD_RULES = [
  { test: (value) => value.length >= 8, message: 'at least 8 characters' },
  { test: (value) => /[A-Za-z]/.test(value), message: 'a letter' },
  { test: (value) => /\d/.test(value), message: 'a digit' },
]

export function RegisterPage() {
  const navigate = useNavigate()

  const [form, setForm] = useState({ email: '', fullName: '', phone: '', password: '', confirm: '' })
  const [fieldErrors, setFieldErrors] = useState({})

  const mutation = useMutation({
    mutationFn: register,
    onSuccess: () => navigate('/login', { replace: true, state: { registered: true } }),
    onError: (caught) => {
      // Surface a per-field backend error inline (an email that is already
      // registered, for instance) rather than only in the banner below.
      // The functional updater is used deliberately: this callback closes over
      // the render's state, so reading `fieldErrors` here would be stale.
      const apiError = caught?.apiError ?? normaliseError(caught)
      const field = apiError?.details?.field
      if (field) {
        setFieldErrors((current) => ({ ...current, [field]: apiError.message }))
      }
    },
  })

  const error = mutation.error ? (mutation.error.apiError ?? normaliseError(mutation.error)) : null

  function validate() {
    const errors = {}

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
      errors.email = 'Enter a valid email address.'
    }
    if (form.fullName.trim().length < 2) {
      errors.fullName = 'Enter your full name.'
    }
    if (form.phone && !/^\+?[0-9][0-9 \-()]{6,18}$/.test(form.phone)) {
      errors.phone = 'Enter a valid phone number, or leave it blank.'
    }

    const unmet = PASSWORD_RULES.filter((rule) => !rule.test(form.password)).map(
      (rule) => rule.message,
    )
    if (unmet.length > 0) {
      errors.password = `Password needs ${unmet.join(', ')}.`
    }
    if (form.password !== form.confirm) {
      errors.confirm = 'The two passwords do not match.'
    }

    return errors
  }

  function handleSubmit(event) {
    event.preventDefault()
    const errors = validate()
    setFieldErrors(errors)
    if (Object.keys(errors).length === 0) {
      mutation.mutate({
        email: form.email.trim().toLowerCase(),
        password: form.password,
        fullName: form.fullName.trim(),
        phone: form.phone.trim() || undefined,
      })
    }
  }

  function update(field) {
    return (event) => setForm({ ...form, [field]: event.target.value })
  }

  return (
    <div className="auth-page">
      <div className="auth-page__panel">
        <div className="auth-page__intro">
          <Logo size={54} />
          <h1>{APP_CONFIG.appName}</h1>
          <p>
            Create a customer account to register your vehicles, follow a custody session live and
            review the condition report when you collect your car.
          </p>
          <ul className="prose-list prose-list--tight">
            <li>Digital ticket with QR handover</li>
            <li>Live location while your vehicle is parked</li>
            <li>Before/after condition comparison</li>
            <li>Predicted retrieval time at pickup</li>
          </ul>
        </div>

        <div className="auth-page__form">
          <Panel title="Create your account" subtitle="Takes under a minute">
            <form onSubmit={handleSubmit} noValidate>
              <Field label="Full name" error={fieldErrors.fullName}>
                <input
                  type="text"
                  name="fullName"
                  autoComplete="name"
                  placeholder="Aisha Verma"
                  value={form.fullName}
                  onChange={update('fullName')}
                />
              </Field>

              <Field label="Email address" error={fieldErrors.email}>
                <input
                  type="email"
                  name="email"
                  autoComplete="username"
                  placeholder="you@example.com"
                  value={form.email}
                  onChange={update('email')}
                />
              </Field>

              <Field label="Phone number" hint="Optional" error={fieldErrors.phone}>
                <input
                  type="tel"
                  name="phone"
                  autoComplete="tel"
                  placeholder="+91 98765 43210"
                  value={form.phone}
                  onChange={update('phone')}
                />
              </Field>

              <Field
                label="Password"
                hint="At least 8 characters, with a letter and a digit."
                error={fieldErrors.password}
              >
                <input
                  type="password"
                  name="password"
                  autoComplete="new-password"
                  placeholder="••••••••"
                  value={form.password}
                  onChange={update('password')}
                />
              </Field>

              <Field label="Confirm password" error={fieldErrors.confirm}>
                <input
                  type="password"
                  name="confirm"
                  autoComplete="new-password"
                  placeholder="••••••••"
                  value={form.confirm}
                  onChange={update('confirm')}
                />
              </Field>

              <button
                type="submit"
                className="btn btn--primary btn--block"
                disabled={mutation.isPending}
              >
                {mutation.isPending ? 'Creating account…' : 'Create account'}
              </button>
            </form>

            <ErrorNotice error={error} />

            <p className="muted auth-page__note">
              Already registered? <Link to="/login">Sign in instead</Link>.
            </p>
            <p className="muted auth-page__note">
              Public registration creates a <strong>customer</strong> account. Valet and manager
              accounts are created by an administrator, so nobody can grant themselves staff
              privileges here.
            </p>
          </Panel>
        </div>
      </div>
    </div>
  )
}

export default RegisterPage

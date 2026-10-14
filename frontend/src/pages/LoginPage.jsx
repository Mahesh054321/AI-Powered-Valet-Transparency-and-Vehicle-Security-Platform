import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { normaliseError } from '../services/apiClient'
import { fetchCurrentUser, login } from '../services/authService'
import { APP_CONFIG } from '../config/appConfig'
import { ErrorNotice, Field, Panel } from '../components/ui'
import { Logo } from '../components/ui/Logo'
import { useAuth } from '../hooks/useAuth'
import { ROLE_HOME } from '../utils/constants'

/**
 * Sign-in form.
 *
 * The form is fully wired to `POST /api/v1/auth/login`: it validates, posts,
 * stores the returned access and refresh tokens, then fetches the account from
 * `/auth/me` so the UI reflects what the database says rather than only what
 * the token claims.
 *
 * On success the visitor is sent to the landing route for their role, or back
 * to the page they were redirected away from.
 */
export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { signIn, signedOutReason, setProfileFromServer } = useAuth()

  const [form, setForm] = useState({ email: '', password: '' })
  const [fieldErrors, setFieldErrors] = useState({})

  const signInMutation = useMutation({
    mutationFn: login,
    onSuccess: async (data) => {
      signIn({ token: data.access_token, refreshToken: data.refresh_token })
      const target = location.state?.from ?? ROLE_HOME[data.user?.role] ?? '/status'
      navigate(target, { replace: true })

      // Authoritative identity. Non-blocking: navigation already happened, and
      // a failure here must not sign the user straight back out.
      try {
        const me = await fetchCurrentUser()
        setProfileFromServer(me?.user ?? null)
      } catch {
        /* The token identity is enough to keep going. */
      }
    },
  })

  const error = signInMutation.error
    ? (signInMutation.error.apiError ?? normaliseError(signInMutation.error))
    : null

  function handleSubmit(event) {
    event.preventDefault()
    const errors = {}
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
      errors.email = 'Enter a valid email address.'
    }
    if (form.password.length < 8) {
      errors.password = 'Passwords are at least 8 characters.'
    }
    setFieldErrors(errors)
    if (Object.keys(errors).length === 0) {
      signInMutation.mutate({ email: form.email.trim().toLowerCase(), password: form.password })
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-page__panel">
        <div className="auth-page__intro">
          <Logo size={54} />
          <h1>{APP_CONFIG.appName}</h1>
          <p>
            Turn the valet custody window into a transparent, traceable and auditable process:
            condition capture, live location, geofence monitoring and damage comparison.
          </p>
          <ul className="prose-list prose-list--tight">
            <li>Digital valet ticket with QR handover</li>
            <li>Before/after vehicle condition comparison</li>
            <li>Geofence and suspicious-movement alerts</li>
            <li>Predicted retrieval time at pickup</li>
          </ul>
        </div>

        <div className="auth-page__form">
          <Panel title="Sign in" subtitle="Access your dashboard">
            {signedOutReason && (
              <div className="notice notice--warning">
                You were signed out because the API rejected the session token
                <span className="mono"> ({signedOutReason})</span>.
              </div>
            )}

            <form onSubmit={handleSubmit} noValidate>
              <Field label="Email address" error={fieldErrors.email}>
                <input
                  type="email"
                  name="email"
                  autoComplete="username"
                  placeholder="you@example.com"
                  value={form.email}
                  onChange={(event) => setForm({ ...form, email: event.target.value })}
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
                  autoComplete="current-password"
                  placeholder="••••••••"
                  value={form.password}
                  onChange={(event) => setForm({ ...form, password: event.target.value })}
                />
              </Field>

              <button type="submit" className="btn btn--primary btn--block" disabled={signInMutation.isPending}>
                {signInMutation.isPending ? 'Signing in…' : 'Sign in'}
              </button>
            </form>

            <ErrorNotice error={error} />

            <p className="muted auth-page__note">
              New here? <Link to="/register">Create a customer account</Link>.
            </p>
            <p className="muted auth-page__note">
              Passwords are stored as PBKDF2-SHA256 hashes and are never sent back to the browser.
              Sign-in issues a short-lived JWT that the server can revoke on sign-out.
            </p>
          </Panel>

        </div>
      </div>
    </div>
  )
}

export default LoginPage

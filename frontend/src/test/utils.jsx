/**
 * Test helpers: render inside the providers the app expects, and build tokens
 * with the same claim shape the backend issues.
 */

import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../context/AuthContext'
import { QueryProvider } from '../context/QueryProvider'
import { ToastProvider } from '../context/ToastContext'
import { writeStoredToken } from '../utils/tokenStorage'

/**
 * Render `ui` inside QueryProvider + ToastProvider + AuthProvider + MemoryRouter.
 *
 * `AuthProvider` initialises from localStorage, so tests establish a session by
 * passing `token` (or by calling `signInAs` before rendering).
 *
 * `ToastProvider` mirrors the order in main.jsx: it sits above `AuthProvider` so
 * anything in the tree can report an outcome. Keeping the harness and the real
 * entry point in the same order means a component that works in a test also has
 * the provider it expects in the app.
 */
export function renderWithProviders(ui, { route = '/', token = null, ...options } = {}) {
  if (token) writeStoredToken(token)

  return render(
    <QueryProvider>
      <ToastProvider>
        <AuthProvider>
          <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
        </AuthProvider>
      </ToastProvider>
    </QueryProvider>,
    options,
  )
}

/**
 * Build a token whose payload matches the backend's claims.
 *
 * `alg: none` is fine here: the frontend only decodes claims, it never verifies
 * a signature - verification is the backend's job.
 */
export function buildToken({ role = 'CUSTOMER', email = 'user@valet.local', expiresIn = 3600 } = {}) {
  const now = Math.floor(Date.now() / 1000)
  const encode = (value) => btoa(JSON.stringify(value)).replace(/=+$/, '')
  const header = encode({ alg: 'none', typ: 'JWT' })
  const payload = encode({
    sub: `test-${role.toLowerCase()}`,
    role,
    email,
    typ: 'access',
    iat: now,
    nbf: now,
    exp: now + expiresIn,
  })
  return `${header}.${payload}.test`
}

/** Clear any session so a test starts signed out. */
export function clearSession() {
  writeStoredToken(null)
}

/** Respond with the backend's success envelope. */
export function jsonResponse(data, { status = 200, meta = {} } = {}) {
  return new Response(
    JSON.stringify({ success: status < 400, data, meta: { request_id: 'test-rid', ...meta } }),
    { status, headers: { 'Content-Type': 'application/json' } },
  )
}

/** Respond with the backend's error envelope. */
export function errorResponse(code, message, { status = 422, details = null } = {}) {
  const body = { success: false, error: { code, message }, meta: { request_id: 'test-rid' } }
  if (details) body.error.details = details
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Authentication API.
 *
 * Every call goes through `apiClient`, so the envelope is unwrapped and errors
 * are normalised into `{ status, code, message, details, requestId }` before
 * they reach a component. No component parses an error shape itself.
 *
 * The backend serves auth at both `/api/auth/*` and `/api/v1/auth/*`; the
 * versioned path is used here so the client follows the same versioning
 * convention as every other domain endpoint.
 */

import { apiGet, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

/** Create a customer account. The backend ignores any `role` field. */
export function register({ email, password, fullName, phone }) {
  return apiPost(ENDPOINTS.authRegister, {
    email,
    password,
    full_name: fullName,
    ...(phone ? { phone } : {}),
  })
}

/** Exchange credentials for an access/refresh token pair. */
export function login({ email, password }) {
  return apiPost(ENDPOINTS.authLogin, { email, password })
}

/**
 * Revoke the current access token on the server.
 *
 * The client discarding its copy is not sufficient: the token stays valid
 * until its own expiry unless the backend denylists it. Failures here are not
 * fatal - the local session is cleared either way - so the caller decides how
 * much to care.
 */
export function logout() {
  return apiPost(ENDPOINTS.authLogout)
}

/** The signed-in account, as the database records it (not from token claims). */
export function fetchCurrentUser() {
  return apiGet(ENDPOINTS.authMe)
}

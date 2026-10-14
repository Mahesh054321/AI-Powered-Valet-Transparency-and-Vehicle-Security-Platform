/**
 * Token persistence.
 *
 * This module is deliberately free of React imports so that
 * `services/apiClient.js` can read the token without importing a component -
 * which would create a cycle (the auth context needs the API client, and the
 * client needs the token).
 *
 * WHY localStorage, AND WHAT WOULD BE BETTER
 * ------------------------------------------
 * These tokens are credentials issued *to* this browser, so persisting them
 * client-side is expected. The honest trade-off:
 *
 *  - localStorage is readable by any script on this origin, so a cross-site
 *    scripting bug would expose it. The mitigations actually in place are a
 *    short access-token TTL (30 minutes by default), a strict CSP-friendly
 *    dependency set with no runtime CDN scripts, and revocation on the server
 *    so a leaked token can be killed.
 *  - The stronger design is an httpOnly, SameSite=Strict cookie for the refresh
 *    token, which no script can read at all. That needs the backend to set
 *    cookies and the dev proxy to forward them; it is the recommended next
 *    step before any real deployment.
 *
 * What must never appear in frontend code is a signing secret, a database
 * password, or any server-side key. Nothing here writes one.
 */

const ACCESS_TOKEN_KEY = 'valet.accessToken'
const REFRESH_TOKEN_KEY = 'valet.refreshToken'

function safeGet(key) {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function safeSet(key, value) {
  try {
    if (value) localStorage.setItem(key, value)
    else localStorage.removeItem(key)
  } catch {
    /* Storage disabled (private mode): the session simply won't persist. */
  }
}

export function readStoredToken() {
  return safeGet(ACCESS_TOKEN_KEY)
}

export function writeStoredToken(token) {
  safeSet(ACCESS_TOKEN_KEY, token)
}

export function readStoredRefreshToken() {
  return safeGet(REFRESH_TOKEN_KEY)
}

export function writeStoredRefreshToken(token) {
  safeSet(REFRESH_TOKEN_KEY, token)
}

/** Store or clear both halves of the session in one call. */
export function writeStoredTokens({ accessToken, refreshToken } = {}) {
  writeStoredToken(accessToken ?? null)
  writeStoredRefreshToken(refreshToken ?? null)
}

export function clearStoredToken() {
  writeStoredToken(null)
  writeStoredRefreshToken(null)
}

/**
 * JWT decoding.
 *
 * The frontend only *reads* a token's claims to drive the UI - it never
 * verifies a signature. Verification is the backend's job; anything the browser
 * can check, an attacker can forge.
 */

/** Decode the payload of a JWT without a library (it is base64url JSON). */
export function decodeJwt(token) {
  if (!token || typeof token !== 'string') return null
  const parts = token.split('.')
  if (parts.length !== 3) return null

  try {
    const normalised = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const json = decodeURIComponent(
      atob(normalised)
        .split('')
        .map((char) => `%${char.charCodeAt(0).toString(16).padStart(2, '0')}`)
        .join(''),
    )
    const claims = JSON.parse(json)
    return claims && typeof claims === 'object' ? claims : null
  } catch {
    return null
  }
}

/** Turn raw claims into the identity shape the UI consumes. */
export function identityFromToken(token) {
  const claims = decodeJwt(token)
  if (!claims) return null
  return {
    id: claims.sub ?? null,
    role: claims.role ?? null,
    email: claims.email ?? null,
    expiresAt: claims.exp ? new Date(claims.exp * 1000) : null,
  }
}

/** True when a token exists and has not expired. */
export function isTokenUsable(identity) {
  if (!identity) return false
  if (identity.expiresAt && identity.expiresAt.getTime() <= Date.now()) return false
  return true
}

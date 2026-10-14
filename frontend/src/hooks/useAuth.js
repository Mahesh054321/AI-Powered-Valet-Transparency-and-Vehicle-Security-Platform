/**
 * Auth convenience hooks built on `context/AuthContext`.
 *
 * These read the context's resolved `role` rather than the raw token identity,
 * so a role change recorded on the server takes effect as soon as `/auth/me`
 * answers instead of waiting for the access token to expire.
 */

import { useCallback } from 'react'
import { useAuthContext } from '../context/AuthContext'
import { logout } from '../services/authService'
import { ROLE_HOME } from '../utils/constants'

// Importing `authService` here does not create a cycle: the chain is
// useAuth -> authService -> apiClient, and `apiClient` never imports React or
// any hook. The auth context reads the token straight from tokenStorage.


/** Current identity plus the actions the UI needs. */
export function useAuth() {
  return useAuthContext()
}

/** The landing route for the signed-in role, or `/status` as a fallback. */
export function useHomeRoute() {
  const { role, isAuthenticated } = useAuthContext()
  return (isAuthenticated && role && ROLE_HOME[role]) || '/status'
}

/** Capability-style check used to hide actions a role cannot perform.
 *  The backend still enforces the real rules. */
export function useHasRole(roles) {
  const { role, isAuthenticated } = useAuthContext()
  const allowed = Array.isArray(roles) ? roles : [roles]
  return Boolean(isAuthenticated && role && allowed.includes(role))
}

/**
 * Sign out, telling the backend to revoke the token first.
 *
 * The revocation call is best-effort: if the network is down the local session
 * is still cleared, because leaving a token in localStorage would be worse
 * than an unrevoked one on a server the client cannot reach.
 */
export function useSignOut() {
  const { signOut, token } = useAuthContext()
  return useCallback(
    async ({ reason = null } = {}) => {
      if (token) {
        try {
          await logout()
        } catch {
          /* Best effort - see the note above. */
        }
      }
      signOut({ reason })
    },
    [signOut, token],
  )
}

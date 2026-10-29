/**
 * Authentication context.
 *
 * Single source of truth for "who is signed in". Components read it through the
 * `useAuth()` hook rather than importing a store, so the implementation can
 * change without touching callers.
 *
 * Two sources of identity, deliberately kept distinct:
 *
 *  - `user` - decoded from the access token, so it is available instantly on
 *    first paint without a request. Enough to drive role-based navigation.
 *  - `profile` - fetched from `/auth/me` after sign-in. This is what the
 *    database actually says, so a role change or a suspension shows up without
 *    waiting for the token to expire.
 *
 * The API client cannot read React state, so it reads the token from
 * `utils/tokenStorage.js` and reports session rejection through
 * `onUnauthorized()`, which this provider wires to `signOut()`.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { identityFromToken, isTokenUsable } from '../utils/jwt'
import {
  clearStoredToken,
  readStoredRefreshToken,
  readStoredToken,
  writeStoredRefreshToken,
  writeStoredToken,
} from '../utils/tokenStorage'
import { onUnauthorized } from '../services/apiClient'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => readStoredToken())
  const [refreshToken, setRefreshToken] = useState(() => readStoredRefreshToken())
  const [profile, setProfile] = useState(null)
  const [signedOutReason, setSignedOutReason] = useState(null)

  const user = useMemo(() => identityFromToken(token), [token])
  const isAuthenticated = isTokenUsable(user)

  const signIn = useCallback(({ token: nextToken, refreshToken: nextRefresh = null }) => {
    writeStoredToken(nextToken)
    writeStoredRefreshToken(nextRefresh)
    setToken(nextToken)
    setRefreshToken(nextRefresh)
    setProfile(null)
    setSignedOutReason(null)
  }, [])

  const signOut = useCallback(({ reason = null } = {}) => {
    clearStoredToken()
    setToken(null)
    setRefreshToken(null)
    setProfile(null)
    setSignedOutReason(reason)
  }, [])

  /**
   * Record the authoritative profile from `/auth/me`.
   *
   * Only accepted while a session is live, so a late-arriving response cannot
   * resurrect the identity of someone who has just been signed out.
   */
  const setProfileFromServer = useCallback(
    (nextProfile) => {
      if (!readStoredToken()) return
      setProfile(nextProfile ?? null)
    },
    [],
  )

  // Let the API client tell us when the backend rejects our token.
  useEffect(() => onUnauthorized((reason) => signOut({ reason })), [signOut])

  const value = useMemo(
    () => ({
      token,
      refreshToken,
      user,
      profile,
      // The role drives navigation: prefer the server's answer, fall back to
      // the token so the first paint is not blocked on a request.
      role: profile?.role ?? user?.role ?? null,
      isAuthenticated,
      signedOutReason,
      signIn,
      signOut,
      setProfileFromServer,
    }),
    [
      token,
      refreshToken,
      user,
      profile,
      isAuthenticated,
      signedOutReason,
      signIn,
      signOut,
      setProfileFromServer,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Access the current auth state. Throws if used outside `AuthProvider`. */
export function useAuthContext() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuthContext must be used inside <AuthProvider>.')
  }
  return context
}

export default AuthContext

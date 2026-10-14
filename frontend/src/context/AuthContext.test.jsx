import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { AuthProvider } from './AuthContext'
import { useAuth, useHomeRoute } from '../hooks/useAuth'
import { readStoredToken } from '../utils/tokenStorage'
import { buildToken, clearSession } from '../test/utils.jsx'
import { ROLE_HOME } from '../utils/constants'

const TOKEN_KEY = 'valet.accessToken'

/** Render a hook inside the real provider. */
function renderAuthHook(hook) {
  function Wrapper({ children }) {
    return <AuthProvider>{children}</AuthProvider>
  }
  return renderHook(hook, { wrapper: Wrapper })
}

function storeToken(token) {
  window.localStorage.setItem(TOKEN_KEY, token)
}

describe('AuthContext', () => {
  it('starts signed out when no token is stored', () => {
    clearSession()
    const { result } = renderAuthHook(() => useAuth())

    expect(result.current.token).toBeNull()
    expect(result.current.user).toBeNull()
    expect(result.current.isAuthenticated).toBe(false)
  })

  it('restores a persisted session on mount', () => {
    clearSession()
    storeToken(buildToken({ role: 'VALET_MANAGER', email: 'm@v.local' }))

    const { result } = renderAuthHook(() => useAuth())

    expect(result.current.isAuthenticated).toBe(true)
    expect(result.current.user.role).toBe('VALET_MANAGER')
    expect(result.current.user.email).toBe('m@v.local')
  })

  it('signIn persists the token so a refresh keeps the session', () => {
    clearSession()
    const { result } = renderAuthHook(() => useAuth())
    const token = buildToken({ role: 'ADMIN' })

    act(() => result.current.signIn({ token }))

    expect(readStoredToken()).toBe(token)
    expect(result.current.user.role).toBe('ADMIN')
  })

  it('signOut clears storage and records the reason', () => {
    clearSession()
    const { result } = renderAuthHook(() => useAuth())
    act(() => result.current.signIn({ token: buildToken() }))

    act(() => result.current.signOut({ reason: 'AUTHENTICATION_ERROR' }))

    expect(readStoredToken()).toBeNull()
    expect(result.current.token).toBeNull()
    expect(result.current.signedOutReason).toBe('AUTHENTICATION_ERROR')
  })

  it('reports an expired persisted token as unauthenticated', () => {
    clearSession()
    storeToken(buildToken({ expiresIn: -60 }))

    const { result } = renderAuthHook(() => useAuth())

    expect(result.current.token).toBeTruthy()
    expect(result.current.isAuthenticated).toBe(false)
  })

  it('throws a helpful error when useAuth is used outside the provider', () => {
    clearSession()
    // Silence React's expected error logging for this intentional failure.
    const original = console.error
    console.error = () => {}
    try {
      expect(() => renderHook(() => useAuth())).toThrow(/inside <AuthProvider>/)
    } finally {
      console.error = original
    }
  })
})

describe('useHomeRoute', () => {
  it('routes each role to its own landing page', () => {
    clearSession()
    storeToken(buildToken({ role: 'VALET_OPERATOR' }))

    const { result } = renderAuthHook(() => useHomeRoute())

    expect(result.current).toBe(ROLE_HOME.VALET_OPERATOR)
  })

  it('falls back to /status when nobody is signed in', () => {
    clearSession()
    const { result } = renderAuthHook(() => useHomeRoute())

    expect(result.current).toBe('/status')
  })
})

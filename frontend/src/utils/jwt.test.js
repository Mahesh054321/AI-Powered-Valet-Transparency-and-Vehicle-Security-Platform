import { describe, expect, it } from 'vitest'
import { decodeJwt, identityFromToken, isTokenUsable } from './jwt'
import { buildToken } from '../test/utils.jsx'

describe('jwt utils', () => {
  it('decodes the claims of a well-formed token', () => {
    const claims = decodeJwt(buildToken({ role: 'VALET_MANAGER', email: 'm@v.local' }))

    expect(claims.role).toBe('VALET_MANAGER')
    expect(claims.email).toBe('m@v.local')
    expect(claims.typ).toBe('access')
  })

  it('maps claims onto the identity shape the UI consumes', () => {
    const identity = identityFromToken(buildToken({ role: 'ADMIN' }))

    expect(identity).toMatchObject({
      id: 'test-admin',
      role: 'ADMIN',
      email: 'user@valet.local',
    })
    expect(identity.expiresAt).toBeInstanceOf(Date)
  })

  it('returns null rather than throwing on malformed input', () => {
    expect(decodeJwt(null)).toBeNull()
    expect(decodeJwt('')).toBeNull()
    expect(decodeJwt('not-a-jwt')).toBeNull()
    expect(decodeJwt('a.b')).toBeNull()
    expect(decodeJwt('a.!!!notbase64!!!.c')).toBeNull()
    expect(identityFromToken('garbage')).toBeNull()
  })

  it('treats an expired token as unusable', () => {
    const expired = identityFromToken(buildToken({ expiresIn: -60 }))
    const fresh = identityFromToken(buildToken({ expiresIn: 3600 }))

    expect(isTokenUsable(expired)).toBe(false)
    expect(isTokenUsable(fresh)).toBe(true)
  })

  it('treats a missing identity as unusable', () => {
    expect(isTokenUsable(null)).toBe(false)
  })
})

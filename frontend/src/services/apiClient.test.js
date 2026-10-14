import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import { apiClient, apiGet, normaliseError, onUnauthorized } from './apiClient'
import { buildToken, clearSession } from '../test/utils.jsx'
import { writeStoredToken } from '../utils/tokenStorage'

/**
 * Install a stub transport adapter.
 *
 * axios's convenience methods (`get`, `post`, …) call `Axios.prototype.request`
 * and rebind, so spying on the *instance's* `request` never intercepts them.
 * Replacing the adapter is the reliable seam: the request still passes through
 * every interceptor, so the token injection and envelope unwrapping under test
 * are the real ones.
 */
function stubTransport({ status = 200, body = null } = {}) {
  const calls = []
  apiClient.defaults.adapter = async (config) => {
    calls.push({
      url: config.url,
      baseURL: config.baseURL,
      method: config.method,
      headers: AxiosHeaders.from(config.headers),
    })
    return { data: body, status, statusText: 'OK', headers: {}, config, request: {} }
  }
  return calls
}

/** Adapters signal HTTP failure by throwing an AxiosError carrying the response. */
function stubFailure(status, body) {
  apiClient.defaults.adapter = async (config) => {
    const response = { data: body, status, statusText: 'ERR', headers: {}, config, request: {} }
    throw new AxiosError(`Request failed with status code ${status}`, String(status), config, {}, response)
  }
}

describe('api client', () => {
  beforeEach(() => {
    clearSession()
    vi.restoreAllMocks()
  })

  it('unwraps the backend envelope so callers receive `data`', async () => {
    stubTransport({ body: { success: true, data: { status: 'up' }, meta: { request_id: 'abc' } } })

    await expect(apiGet('/health')).resolves.toEqual({ status: 'up' })
  })

  it('attaches the bearer token and a request id once signed in', async () => {
    const token = buildToken({ role: 'ADMIN' })
    writeStoredToken(token)
    const calls = stubTransport({ body: { success: true, data: null, meta: {} } })

    await apiGet('/v1/vehicles')

    expect(calls).toHaveLength(1)
    expect(calls[0].headers.get('Authorization')).toBe(`Bearer ${token}`)
    expect(calls[0].headers.get('X-Request-Id')).toBeTruthy()
  })

  it('omits the Authorization header when signed out', async () => {
    const calls = stubTransport({ body: { success: true, data: null, meta: {} } })

    await apiGet('/health')

    expect(calls[0].headers.get('Authorization')).toBeFalsy()
  })

  it('is configured to send every request through the API base URL', async () => {
    const calls = stubTransport({ body: { success: true, data: null, meta: {} } })

    await apiGet('/health')

    // axios joins baseURL and url inside the adapter, not in the config object.
    expect(calls[0].baseURL).toBe('/api')
    expect(calls[0].url).toBe('/health')
  })

  it('normalises a backend error into a predictable shape', () => {
    const error = {
      response: {
        status: 422,
        headers: { 'x-request-id': 'rid-1' },
        data: {
          success: false,
          error: {
            code: 'VALIDATION_ERROR',
            message: 'Missing required field(s).',
            details: { missing_fields: ['password'] },
          },
          meta: { request_id: 'rid-1' },
        },
      },
    }

    const normalised = normaliseError(error)
    expect(normalised).toMatchObject({
      status: 422,
      code: 'VALIDATION_ERROR',
      message: 'Missing required field(s).',
      requestId: 'rid-1',
    })
    expect(normalised.details.missing_fields).toEqual(['password'])
  })

  it('explains a network failure instead of showing a raw axios message', () => {
    const normalised = normaliseError({ message: 'Network Error' })
    expect(normalised.code).toBe('NETWORK_ERROR')
    expect(normalised.message).toMatch(/Cannot reach the API/)
  })

  it('explains a timeout distinctly', () => {
    expect(normaliseError({ code: 'ECONNABORTED' }).code).toBe('TIMEOUT')
  })

  it('surfaces the backend message and the snake_case request id', async () => {
    stubFailure(404, {
      success: false,
      error: { code: 'NOT_FOUND', message: 'Vehicle not found.' },
      meta: { request_id: 'rid-404' },
    })

    const error = await apiGet('/v1/vehicles/nope').catch((caught) => caught)

    expect(error.apiError).toMatchObject({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Vehicle not found.',
      requestId: 'rid-404',
    })
  })

  it('notifies listeners on 401 so the auth context can sign the user out', async () => {
    writeStoredToken(buildToken({ role: 'CUSTOMER' }))
    stubFailure(401, {
      success: false,
      error: { code: 'AUTHENTICATION_ERROR', message: 'expired' },
      meta: { request_id: 'rid-401' },
    })

    const seen = []
    const unsubscribe = onUnauthorized((code) => seen.push(code))

    await expect(apiGet('/v1/vehicles')).rejects.toBeTruthy()

    expect(seen).toEqual(['AUTHENTICATION_ERROR'])

    unsubscribe()
    await expect(apiGet('/v1/vehicles')).rejects.toBeTruthy()
    expect(seen).toHaveLength(1) // unsubscribed listener is not called again
  })
})

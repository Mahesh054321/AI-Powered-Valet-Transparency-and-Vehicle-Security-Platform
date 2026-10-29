/**
 * Axios instance for every backend call.
 *
 * Responsibilities:
 *  - prefix every path with the API base URL (proxied to Flask in development);
 *  - attach the bearer token from the auth store;
 *  - unwrap the backend envelope so components see `data` directly;
 *  - normalise errors into a predictable `{ code, message, details, status }`;
 *  - sign the user out when the API rejects the token as expired/invalid.
 */

import axios from 'axios'
import { APP_CONFIG } from '../config/appConfig'
import { readStoredToken } from '../utils/tokenStorage'

/**
 * Session-rejection callbacks.
 *
 * The client cannot import the auth context (that would be a cycle: the context
 * uses this client). Instead the provider registers a callback with
 * `onUnauthorized()` and the client invokes it on a 401.
 */
const unauthorizedListeners = new Set()

export function onUnauthorized(listener) {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}

function notifyUnauthorized(code) {
  for (const listener of unauthorizedListeners) {
    listener(code)
  }
}

export const apiClient = axios.create({
  baseURL: APP_CONFIG.apiBaseUrl,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  const token = readStoredToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  // Let the backend echo this back so a UI action can be traced to a log line.
  config.headers['X-Request-Id'] = crypto.randomUUID?.() ?? `web-${Date.now()}`
  return config
})

/** Shape any failure into one object the UI can render. */
export function normaliseError(error) {
  if (error.response) {
    const payload = error.response.data || {}
    const apiError = payload.error || {}
    // The backend envelope uses snake_case keys; accept both spellings so the
    // request id is never silently dropped from an error notice.
    const requestId =
      payload.meta?.request_id ??
      payload.meta?.requestId ??
      error.response.headers?.['x-request-id'] ??
      null
    return {
      status: error.response.status,
      code: apiError.code || 'HTTP_ERROR',
      message: apiError.message || error.message || 'Request failed.',
      details: apiError.details || null,
      requestId,
    }
  }
  if (error.code === 'ECONNABORTED') {
    return {
      status: 408,
      code: 'TIMEOUT',
      message: 'The server took too long to respond.',
      details: null,
      requestId: null,
    }
  }
  return {
    status: 0,
    code: 'NETWORK_ERROR',
    message:
      'Cannot reach the API. Confirm the Flask backend is running on the configured port.',
    details: null,
    requestId: null,
  }
}

apiClient.interceptors.response.use(
  (response) => {
    // Success envelope -> hand the caller the payload, keep meta on the side.
    if (response.data && typeof response.data === 'object' && 'success' in response.data) {
      response.payload = response.data.data
      response.meta = response.data.meta
      return response
    }
    return response
  },
  (error) => {
    const normalised = normaliseError(error)
    if (normalised.status === 401 && readStoredToken()) {
      notifyUnauthorized(normalised.code)
    }
    error.apiError = normalised
    return Promise.reject(error)
  },
)

/* ------------------------------------------------------------------ */
/* Thin verb helpers: unwrap the envelope so callers get plain data.   */
/* ------------------------------------------------------------------ */
export async function apiGet(path, { params, ...options } = {}) {
  const response = await apiClient.get(path, { params, ...options })
  return response.payload ?? response.data
}

export async function apiPost(path, body, options = {}) {
  const response = await apiClient.post(path, body, options)
  return response.payload ?? response.data
}

export async function apiPut(path, body, options = {}) {
  const response = await apiClient.put(path, body, options)
  return response.payload ?? response.data
}

export async function apiPatch(path, body, options = {}) {
  const response = await apiClient.patch(path, body, options)
  return response.payload ?? response.data
}

export async function apiDelete(path, options = {}) {
  const response = await apiClient.delete(path, options)
  return response.status === 204 ? null : (response.payload ?? response.data)
}

export async function apiUpload(path, formData, { onProgress } = {}) {
  const response = await apiClient.post(path, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (event) => {
      if (onProgress && event.total) {
        onProgress(Math.round((event.loaded * 100) / event.total))
      }
    },
  })
  return response.payload ?? response.data
}

/**
 * Vitest setup: jest-dom matchers plus the browser APIs jsdom does not provide.
 */

import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.restoreAllMocks()
})

// jsdom implements neither matchMedia (used by some component libs) nor the
// structured-clone helpers. Provide the minimum the app touches.
if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}

// jsdom implements neither of the object-URL methods. InspectionCapturePage
// previews every queued photograph through createObjectURL and managerService
// turns an audit CSV download into one, so without this any test that queues a
// file or exports a report dies on an undefined function rather than on the
// behaviour under test.
if (!window.URL.createObjectURL) {
  let objectUrlCounter = 0
  window.URL.createObjectURL = () => `blob:mock/${(objectUrlCounter += 1)}`
  window.URL.revokeObjectURL = () => {}
}

if (!window.crypto?.randomUUID) {
  window.crypto = {
    ...window.crypto,
    randomUUID: () => 'test-request-id-0000',
  }
}

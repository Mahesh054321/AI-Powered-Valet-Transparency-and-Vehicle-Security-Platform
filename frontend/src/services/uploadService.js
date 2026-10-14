/**
 * Image upload.
 *
 * Uploads are separate from check-in on purpose: a valet takes several photos
 * over a couple of minutes, and each one is acknowledged as it lands rather
 * than held in the browser until the whole form is submitted. The response is
 * the image *reference* (path, size, dimensions, SHA-256) that check-in then
 * persists - the schema never stores image bytes.
 */

import { apiUpload } from './apiClient'
import { ENDPOINTS } from './endpoints'

/**
 * Upload one or more drop-off photographs.
 *
 * `files` is a list of `File` objects. The server validates each file's own
 * signature bytes against its extension, so a renamed non-image is rejected
 * with 415 rather than stored.
 *
 * @param {File[]} files
 * @param {{ label?: string, subdir?: string, onProgress?: (pct: number) => void }} options
 */
export function uploadImages(files, { label, subdir = 'inspections', onProgress } = {}) {
  const form = new FormData()
  files.forEach((file) => form.append('images', file))
  if (label) form.append('label', label)
  form.append('subdir', subdir)

  return apiUpload(ENDPOINTS.uploadImages, form, { onProgress })
}

/**
 * Upload a single photo and return just its reference.
 *
 * Convenience wrapper for the check-in wizard, which captures one angle at a
 * time and needs the reference back to attach to that angle.
 */
export async function uploadDropOffPhoto(file, { label, onProgress } = {}) {
  const result = await uploadImages([file], { label, subdir: 'inspections', onProgress })
  return result.images[0]
}

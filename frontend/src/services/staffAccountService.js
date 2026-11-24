/** Administrative user directory: backend enforces ADMIN for every call. */
import { apiDelete, apiGet, apiPatch, apiPost } from './apiClient'
import { ENDPOINTS } from './endpoints'

const EDITABLE_ROLES = new Set(['CUSTOMER', 'VALET_MANAGER', 'VALET_OPERATOR'])

export function listAccounts({ page = 1, search = '', role = '', status = '' } = {}) {
  return apiGet(ENDPOINTS.authUsers, {
    params: {
      page, page_size: 20,
      ...(search ? { search } : {}),
      ...(role ? { role } : {}),
      ...(status ? { status } : {}),
    },
  })
}

export function createStaffAccount({ fullName, email, password, role, phone }) {
  if (!EDITABLE_ROLES.has(role)) throw new Error('Select Customer, Manager or Valet.')
  return apiPost(ENDPOINTS.authUsers, {
    full_name: fullName.trim(),
    email: email.trim().toLowerCase(),
    password,
    role,
    phone: phone?.trim() || null,
  })
}

export function editAccount(id, { fullName, email, phone, role }) {
  if (!EDITABLE_ROLES.has(role)) throw new Error('Invalid account role.')
  return apiPatch(ENDPOINTS.authUser(id), {
    full_name: fullName.trim(), email: email.trim().toLowerCase(),
    phone: phone?.trim() || null, role,
  })
}

export function resetAccountPassword(id, password) {
  return apiPost(ENDPOINTS.authUserPassword(id), { password })
}

/** Reversible soft-delete, never removes historical custody/audit records. */
export function deactivateAccount(id) { return apiDelete(ENDPOINTS.authUser(id)) }
export function reactivateAccount(id) { return apiPost(ENDPOINTS.authUserReactivate(id), {}) }

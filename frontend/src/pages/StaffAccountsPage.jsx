import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { AlertBanner, Badge, Button, ConfirmDialog, ErrorNotice, Field, Panel, Spinner } from '../components/ui'
import { normaliseError } from '../services/apiClient'
import {
  createStaffAccount, deactivateAccount, editAccount, listAccounts,
  reactivateAccount, resetAccountPassword,
} from '../services/staffAccountService'

const ROLE_NAMES = {
  CUSTOMER: 'Customer', VALET_OPERATOR: 'Valet Operator',
  VALET_MANAGER: 'Valet Manager', ADMIN: 'Administrator',
}
const EMPTY = { fullName: '', email: '', phone: '', role: 'VALET_OPERATOR', password: '', confirm: '' }
const ROLE_OPTIONS = [
  { value: 'VALET_OPERATOR', label: 'Valet Operator' },
  { value: 'VALET_MANAGER', label: 'Valet Manager' },
  { value: 'CUSTOMER', label: 'Customer' },
]
const statusTone = { ACTIVE: 'badge--success', DEACTIVATED: 'badge--danger', SUSPENDED: 'badge--warning', PENDING: 'badge--muted' }

function validateDetails(form) {
  const errors = {}
  if (form.fullName.trim().length < 2) errors.fullName = 'Full name needs at least 2 characters.'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) errors.email = 'Enter a valid email.'
  if (form.phone.trim() && !/^\+?[0-9][0-9 \-()]{6,18}$/.test(form.phone.trim())) errors.phone = 'Enter a valid phone number.'
  if (!ROLE_OPTIONS.some((item) => item.value === form.role)) errors.role = 'Choose a valid role.'
  return errors
}
function validatePassword(form) {
  const errors = {}
  if (form.password.length < 8 || !/[A-Za-z]/.test(form.password) || !/\d/.test(form.password)) {
    errors.password = 'At least 8 characters with a letter and a number.'
  }
  if (form.password !== form.confirm) errors.confirm = 'Passwords do not match.'
  return errors
}
function AccountFields({ form, onChange, errors = {} }) {
  return (
    <div className="account-fields">
      <Field label="Full name" error={errors.fullName}>
        <input value={form.fullName} onChange={(e) => onChange('fullName', e.target.value)} required autoComplete="off" />
      </Field>
      <Field label="Login email (ID)" error={errors.email}>
        <input type="email" value={form.email} onChange={(e) => onChange('email', e.target.value)} required autoComplete="off" />
      </Field>
      <Field label="Phone (optional)" error={errors.phone}>
        <input type="tel" value={form.phone} onChange={(e) => onChange('phone', e.target.value)} autoComplete="off" />
      </Field>
      <Field label="Account role" error={errors.role}>
        <select value={form.role} onChange={(e) => onChange('role', e.target.value)}>
          {ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </Field>
    </div>
  )
}
function PasswordFields({ form, onChange, errors = {} }) {
  return (
    <div className="account-fields">
      <Field label="New password" hint="At least 8 characters, including a letter and a number." error={errors.password}>
        <input type="password" autoComplete="new-password" value={form.password}
          onChange={(e) => onChange('password', e.target.value)} required />
      </Field>
      <Field label="Confirm password" error={errors.confirm}>
        <input type="password" autoComplete="new-password" value={form.confirm}
          onChange={(e) => onChange('confirm', e.target.value)} required />
      </Field>
    </div>
  )
}

/** ADMIN-only page: server validates the same permissions on every endpoint. */
export function StaffAccountsPage() {
  const queryClient = useQueryClient()
  const [searchInput, setSearchInput] = useState('')
  const [filters, setFilters] = useState({ search: '', role: '', status: '', page: 1 })
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState(EMPTY)
  const [edit, setEdit] = useState(null)
  const [passwordFor, setPasswordFor] = useState(null)
  const [passwordForm, setPasswordForm] = useState({ password: '', confirm: '' })
  const [confirmUser, setConfirmUser] = useState(null)
  const [errors, setErrors] = useState({})
  const [feedback, setFeedback] = useState('')

  const directory = useQuery({
    queryKey: ['admin-accounts', filters],
    queryFn: () => listAccounts(filters),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['admin-accounts'] })
  const mutation = useMutation({
    mutationFn: async ({ action, id, payload }) => {
      if (action === 'create') return createStaffAccount(payload)
      if (action === 'edit') return editAccount(id, payload)
      if (action === 'password') return resetAccountPassword(id, payload.password)
      if (action === 'deactivate') return deactivateAccount(id)
      if (action === 'reactivate') return reactivateAccount(id)
      throw new Error('Unsupported action.')
    },
    onSuccess: (_result, variables) => {
      refresh()
      const labels = {
        create: 'Account created. The new user can sign in.',
        edit: 'Account details saved.', password: 'Password changed. Share it privately.',
        deactivate: 'Account deactivated; its records are preserved.',
        reactivate: 'Account reactivated.',
      }
      setFeedback(labels[variables.action] ?? 'Saved.')
      setErrors({})
      setCreateOpen(false)
      setCreateForm(EMPTY)
      setEdit(null)
      setPasswordFor(null)
      setPasswordForm({ password: '', confirm: '' })
      setConfirmUser(null)
    },
  })

  const changeCreate = (key, value) => { setCreateForm((old) => ({ ...old, [key]: value })); setErrors({}); setFeedback(''); mutation.reset() }
  const changeEdit = (key, value) => { setEdit((old) => ({ ...old, [key]: value })); setErrors({}); mutation.reset() }
  const changePassword = (key, value) => { setPasswordForm((old) => ({ ...old, [key]: value })); setErrors({}); mutation.reset() }
  const closeEditors = () => {
    setEdit(null); setPasswordFor(null); setConfirmUser(null)
    setPasswordForm({ password: '', confirm: '' }); setErrors({}); mutation.reset()
  }
  function startEdit(account) {
    closeEditors(); setCreateOpen(false); setFeedback('')
    setEdit({ id: account.id, fullName: account.full_name, email: account.email, phone: account.phone ?? '', role: account.role })
  }
  function startReset(account) {
    closeEditors(); setCreateOpen(false); setFeedback(''); setPasswordFor(account)
  }
  function submitCreate(event) {
    event.preventDefault()
    const found = { ...validateDetails(createForm), ...validatePassword(createForm) }
    setErrors(found)
    if (Object.keys(found).length) return
    mutation.mutate({ action: 'create', payload: createForm })
  }
  function submitEdit(event) {
    event.preventDefault()
    const found = validateDetails(edit)
    setErrors(found)
    if (Object.keys(found).length) return
    mutation.mutate({ action: 'edit', id: edit.id, payload: edit })
  }
  function submitPassword(event) {
    event.preventDefault()
    const found = validatePassword(passwordForm)
    setErrors(found)
    if (Object.keys(found).length) return
    mutation.mutate({ action: 'password', id: passwordFor.id, payload: passwordForm })
  }
  const queryError = directory.error ? (directory.error.apiError ?? normaliseError(directory.error)) : null
  const mutationError = mutation.error ? (mutation.error.apiError ?? normaliseError(mutation.error)) : null
  const accounts = directory.data?.users ?? []
  const page = directory.data?.page ?? filters.page
  const pages = directory.data?.pages ?? 0

  return (
    <div className="page stack account-manager">
      <header className="page__header account-manager__heading">
        <div>
          <h1>Manage Accounts</h1>
          <p className="muted">Add, update, reset passwords, deactivate and reactivate user accounts. Administrators only.</p>
        </div>
        <Button variant="primary" onClick={() => { closeEditors(); setCreateOpen((open) => !open); setFeedback('') }}>
          {createOpen ? 'Close Form' : '+ Add Account'}
        </Button>
      </header>

      {feedback && <AlertBanner tone="success" title="Saved" onDismiss={() => setFeedback('')}>{feedback}</AlertBanner>}
      <p className="muted account-manager__note">Email is the login ID and can be edited. Internal account UUIDs cannot be changed. Deactivate is a reversible delete that preserves audit and vehicle history.</p>

      {createOpen && (
        <Panel title="Add account" subtitle="Create a Valet, Manager or Customer login. Each email must be unique.">
          <form onSubmit={submitCreate} className="stack" noValidate>
            <AccountFields form={createForm} onChange={changeCreate} errors={errors} />
            <PasswordFields form={createForm} onChange={changeCreate} errors={errors} />
            <ErrorNotice error={mutationError} />
            <div className="account-manager__actions">
              <Button type="submit" variant="primary" busy={mutation.isPending} busyLabel="Creating…">Create Account</Button>
              <Button variant="ghost" onClick={() => { setCreateOpen(false); setCreateForm(EMPTY); setErrors({}); mutation.reset() }}>Cancel</Button>
            </div>
          </form>
        </Panel>
      )}

      <Panel title="All accounts" subtitle="Search by name or email; administrators are visible but protected from edits.">
        <form className="account-manager__filters" onSubmit={(e) => { e.preventDefault(); setFilters((old) => ({ ...old, search: searchInput.trim(), page: 1 })) }}>
          <Field label="Search accounts">
            <input aria-label="Search accounts" placeholder="Search name or email" value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)} />
          </Field>
          <Field label="Role">
            <select aria-label="Filter role" value={filters.role} onChange={(e) => setFilters((old) => ({ ...old, role: e.target.value, page: 1 }))}>
              <option value="">All roles</option>
              {Object.entries(ROLE_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </Field>
          <Field label="Status">
            <select aria-label="Filter status" value={filters.status} onChange={(e) => setFilters((old) => ({ ...old, status: e.target.value, page: 1 }))}>
              <option value="">All statuses</option>
              {['ACTIVE', 'DEACTIVATED', 'PENDING', 'SUSPENDED'].map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </Field>
          <Button type="submit" variant="secondary">Search</Button>
          <Button variant="ghost" onClick={() => { setSearchInput(''); setFilters({ search: '', role: '', status: '', page: 1 }) }}>Clear</Button>
        </form>
        {directory.isPending && <Spinner label="Loading accounts…" />}
        <ErrorNotice error={queryError} onRetry={() => directory.refetch()} />
        {directory.isSuccess && (
          <>
            <p className="muted">{directory.data.total} account(s) found.</p>
            <div className="account-manager__table-scroll">
              <table className="account-manager__table">
                <thead><tr><th>Name / login ID</th><th>Role</th><th>Status</th><th>Account ID</th><th>Actions</th></tr></thead>
                <tbody>
                  {accounts.map((account) => (
                    <tr key={account.id}>
                      <td><strong>{account.full_name}</strong><div className="muted">{account.email}</div></td>
                      <td>{ROLE_NAMES[account.role] ?? account.role}</td>
                      <td><Badge tone={statusTone[account.status] ?? 'badge--muted'}>{account.status}</Badge></td>
                      <td><code title={account.id}>{account.id.slice(0, 8)}…</code></td>
                      <td>
                        {account.role === 'ADMIN' ? <span className="muted">Protected</span> : (
                          <div className="account-manager__row-actions">
                            <Button size="sm" onClick={() => startEdit(account)}>Edit</Button>
                            <Button size="sm" variant="ghost" onClick={() => startReset(account)}>Password</Button>
                            {account.status === 'DEACTIVATED' ? (
                              <Button size="sm" onClick={() => mutation.mutate({ action: 'reactivate', id: account.id })}
                                disabled={mutation.isPending}>Reactivate</Button>
                            ) : (
                              <Button size="sm" variant="danger-quiet" onClick={() => { closeEditors(); setConfirmUser(account) }} disabled={mutation.isPending}>Delete</Button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                  {!accounts.length && <tr><td colSpan="5" className="muted">No accounts match your filters.</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="account-manager__pagination">
              <Button size="sm" disabled={page <= 1} onClick={() => setFilters((old) => ({ ...old, page: old.page - 1 }))}>Previous</Button>
              <span className="muted">Page {page} of {Math.max(1, pages)}</span>
              <Button size="sm" disabled={page >= pages} onClick={() => setFilters((old) => ({ ...old, page: old.page + 1 }))}>Next</Button>
            </div>
          </>
        )}
      </Panel>

      {edit && (
        <Panel title={`Edit account — ${edit.email}`} subtitle={`Account ID: ${edit.id} (read-only)`}>
          <form onSubmit={submitEdit} className="stack" noValidate>
            <AccountFields form={edit} onChange={changeEdit} errors={errors} />
            <ErrorNotice error={mutationError} />
            <div className="account-manager__actions">
              <Button variant="primary" type="submit" busy={mutation.isPending} busyLabel="Saving…">Save Changes</Button>
              <Button variant="ghost" onClick={closeEditors}>Cancel</Button>
            </div>
          </form>
        </Panel>
      )}
      {passwordFor && (
        <Panel title={`Reset password — ${passwordFor.email}`} subtitle="You cannot view an existing password; you can only replace it.">
          <form onSubmit={submitPassword} className="stack" noValidate>
            <PasswordFields form={passwordForm} onChange={changePassword} errors={errors} />
            <ErrorNotice error={mutationError} />
            <div className="account-manager__actions">
              <Button variant="primary" type="submit" busy={mutation.isPending} busyLabel="Updating…">Update Password</Button>
              <Button variant="ghost" onClick={closeEditors}>Cancel</Button>
            </div>
          </form>
        </Panel>
      )}
      <ConfirmDialog open={Boolean(confirmUser)} title="Deactivate this account?" confirmLabel="Delete / Deactivate"
        tone="btn--danger" busy={mutation.isPending} onCancel={closeEditors}
        onConfirm={() => mutation.mutate({ action: 'deactivate', id: confirmUser.id })}>
        <p><strong>{confirmUser?.full_name}</strong> ({confirmUser?.email}) will lose access to the platform.</p>
        <p>The account is not erased: its tickets and audit history are preserved. You can reactivate it later.</p>
        <ErrorNotice error={mutationError} />
      </ConfirmDialog>
    </div>
  )
}

export default StaffAccountsPage

import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { ROLE_HOME, ROLE_LABELS } from '../utils/constants'

/**
 * Client-side route guard.
 *
 * This is a UX convenience only - it hides screens a role cannot use. The
 * authoritative check is the backend's role/capability decorator, which rejects
 * the API call regardless of what the browser renders.
 */
export function RequireRole({ roles, children }) {
  const location = useLocation()
  const { isAuthenticated, user } = useAuth()

  if (!isAuthenticated || !user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  if (roles && roles.length > 0 && !roles.includes(user.role)) {
    return (
      <section className="panel panel--alert">
        <header className="panel__header">
          <h2>Not permitted</h2>
        </header>
        <p>
          Your role is <strong>{ROLE_LABELS[user.role] ?? user.role}</strong>. This screen requires{' '}
          {roles.map((role) => ROLE_LABELS[role] ?? role).join(', ')}.
        </p>
        <a className="btn" href={ROLE_HOME[user.role] ?? '/status'}>
          Go to my dashboard
        </a>
      </section>
    )
  }

  return children
}

export default RequireRole

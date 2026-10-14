import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

/**
 * Route guard: the visitor must be signed in.
 *
 * Redirects to `/login` and remembers where they were trying to go, so a
 * successful sign-in lands them back on the screen they asked for rather than
 * on a default dashboard.
 *
 * This is UX only. The authoritative check is the backend's
 * `require_authentication` decorator, which rejects the API call regardless of
 * what the browser renders - hiding a link is not a security control, and this
 * component does not claim to be one.
 */
export function RequireAuth({ children }) {
  const location = useLocation()
  const { isAuthenticated } = useAuth()

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return children
}

export default RequireAuth

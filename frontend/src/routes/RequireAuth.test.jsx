import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { RequireAuth } from './RequireAuth'
import { ROLES } from '../utils/constants'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

/**
 * `RequireAuth` renders `<Navigate>` on the signed-out path, so the test tree
 * must contain a real route table with somewhere to land. Rendering a bare
 * `<Navigate>` with no matching route leaves the router with nothing to
 * resolve and spins the heap (the same trap `RequireRole.test.jsx` documents).
 */
function renderGuarded({ role = null, route = '/protected' } = {}) {
  clearSession()
  return renderWithProviders(
    <Routes>
      <Route
        path="/protected"
        element={
          <RequireAuth>
            <p>protected content</p>
          </RequireAuth>
        }
      />
      <Route path="/login" element={<p>login screen</p>} />
    </Routes>,
    { route, token: role ? buildToken({ role }) : null },
  )
}

describe('RequireAuth guard', () => {
  it('redirects a signed-out visitor to the login page', () => {
    renderGuarded({ role: null })

    expect(screen.getByText('login screen')).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('renders the child route for every signed-in role', () => {
    for (const role of Object.values(ROLES)) {
      const { unmount } = renderGuarded({ role })

      expect(screen.getByText('protected content')).toBeInTheDocument()
      unmount()
    }
  })

  it('treats an expired token as signed out', () => {
    clearSession()
    renderWithProviders(
      <Routes>
        <Route
          path="/protected"
          element={
            <RequireAuth>
              <p>protected content</p>
            </RequireAuth>
          }
        />
        <Route path="/login" element={<p>login screen</p>} />
      </Routes>,
      { route: '/protected', token: buildToken({ expiresIn: -60 }) },
    )

    expect(screen.getByText('login screen')).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('redirects to login rather than denying by role', () => {
    // Role enforcement is `RequireRole`'s job. This guard must not start
    // making role decisions, or the two guards will drift apart.
    renderGuarded({ role: 'CUSTOMER', route: '/protected' })

    expect(screen.getByText('protected content')).toBeInTheDocument()
    expect(screen.queryByText('Not permitted')).not.toBeInTheDocument()
  })
})

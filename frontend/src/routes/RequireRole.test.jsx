import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { RequireRole } from './RequireRole'
import { buildToken, clearSession, renderWithProviders } from '../test/utils.jsx'

/**
 * `RequireRole` renders `<Navigate>` on the unauthenticated path, so the test
 * tree has to contain a real route table with somewhere to land. Rendering a
 * bare `<Navigate>` with no matching route leaves the router with nothing to
 * resolve and spins the heap.
 */
function renderGuarded({ roles, role = null, route = '/protected' } = {}) {
  clearSession()
  return renderWithProviders(
    <Routes>
      <Route
        path="/protected"
        element={
          <RequireRole roles={roles}>
            <p>protected content</p>
          </RequireRole>
        }
      />
      <Route path="/login" element={<p>login screen</p>} />
    </Routes>,
    { route, token: role ? buildToken({ role }) : null },
  )
}

describe('RequireRole guard', () => {
  it('renders the child content for an allowed role', () => {
    renderGuarded({ roles: ['VALET_MANAGER', 'ADMIN'], role: 'VALET_MANAGER' })

    expect(screen.getByText('protected content')).toBeInTheDocument()
  })

  it('explains the denial instead of silently rendering nothing', () => {
    renderGuarded({ roles: ['VALET_MANAGER'], role: 'CUSTOMER' })

    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
    expect(screen.getByText('Not permitted')).toBeInTheDocument()
    expect(screen.getByText('Customer')).toBeInTheDocument()
    // Roles render as their own text nodes inside a sentence (the trailing
    // period is a separate node), so match with a substring matcher.
    expect(screen.getByText(/Valet Manager/)).toBeInTheDocument()
  })

  it('lists every role that would have been allowed', () => {
    renderGuarded({ roles: ['VALET_MANAGER', 'ADMIN'], role: 'VALET_OPERATOR' })

    expect(screen.getByText(/Valet Manager, Administrator/)).toBeInTheDocument()
  })

  it('redirects to the login page when there is no session', () => {
    renderGuarded({ roles: ['CUSTOMER'], role: null })

    expect(screen.getByText('login screen')).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })
})

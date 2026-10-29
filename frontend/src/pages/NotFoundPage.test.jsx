/**
 * The 404 fallback.
 *
 * Small, but it is the screen a user lands on when a deep link rots or a route
 * is renamed, so it has to say what happened and offer a way out rather than
 * dead-ending. The link target matters: it points at a route that exists for
 * every role, not at a dashboard a customer may not be allowed to see.
 */

import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { NotFoundPage } from './NotFoundPage'
import { renderWithProviders } from '../test/utils.jsx'

function renderPage() {
  return renderWithProviders(<NotFoundPage />, { route: '/no-such-route' })
}

describe('NotFoundPage', () => {
  it('states that the route does not exist', () => {
    renderPage()

    expect(
      screen.getByRole('heading', { name: /404 — page not found/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/that route does not exist in this build/i)).toBeInTheDocument()
  })

  it('offers a way out to a route every role may see', () => {
    renderPage()

    // System status is public to any authenticated user; a dashboard link here
    // would send a customer somewhere they are not allowed.
    const link = screen.getByRole('link', { name: /back to system status/i })
    expect(link).toHaveAttribute('href', '/status')
  })
})

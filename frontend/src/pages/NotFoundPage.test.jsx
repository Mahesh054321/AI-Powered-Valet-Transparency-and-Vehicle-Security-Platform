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
  it('explains that the page could not be found in user-friendly language', () => {
    renderPage()

    expect(
      screen.getByRole('heading', { name: /404 — page not found/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/page may have moved/i)).toBeInTheDocument()
  })

  it('offers a way back to the signed-in user’s dashboard', () => {
    renderPage()

    const link = screen.getByRole('link', { name: /go to your dashboard/i })
    expect(link).toHaveAttribute('href', '/')
  })
})

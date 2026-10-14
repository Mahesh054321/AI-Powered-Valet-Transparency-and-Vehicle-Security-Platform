import { useCallback, useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { NotificationBell } from '../components/alerts/NotificationBell'
import { Badge } from '../components/ui'
import { Logo } from '../components/ui/Logo'
import { APP_CONFIG } from '../config/appConfig'
import { useAuth, useSignOut } from '../hooks/useAuth'
import { ROLE_LABELS } from '../utils/constants'

/**
 * Application chrome: sidebar navigation, header, and the routed page outlet.
 *
 * The menu is role-filtered so a customer never sees operator screens, and
 * grouped by what the user is trying to do rather than by the order the routes
 * were written. Grouping matters more as the menu grows: a flat list of nine
 * links is scanned, a grouped list of two is read.
 */
export function MainLayout() {
  const navigate = useNavigate()
  const { user, role } = useAuth()
  const signOut = useSignOut()
  const [menuOpen, setMenuOpen] = useState(false)

  const sidebarRef = useRef(null)
  const toggleRef = useRef(null)

  /**
   * Sign out, then return to the login screen.
   *
   * `useSignOut` revokes the token on the server *before* clearing it locally;
   * calling the raw context action would discard the token here while leaving
   * it valid until its own expiry, which is not really a sign-out. The call is
   * awaited so the revoked token is gone before the login page renders.
   */
  async function handleSignOut() {
    await signOut()
    navigate('/login', { replace: true })
  }

  const closeMenu = useCallback(() => setMenuOpen(false), [])

  /**
   * Move focus into the drawer when it opens and back to the toggle when it
   * closes.
   *
   * Without this, opening the drawer on a phone leaves focus on the toggle
   * behind a translucent overlay, so the first Tab lands somewhere the user
   * cannot see. Restoring focus on close is the other half: a keyboard user who
   * pressed Escape should not have to hunt for the toggle again.
   */
  useEffect(() => {
    if (!menuOpen) return undefined

    const firstLink = sidebarRef.current?.querySelector('a, button')
    firstLink?.focus()

    function onKeyDown(event) {
      if (event.key === 'Escape') {
        setMenuOpen(false)
        toggleRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [menuOpen])

  const groups = buildMenu(role ?? user?.role)
  const initials = (user?.email ?? '?')
    .replace(/[^a-z0-9]/gi, '')
    .slice(0, 2)
    .toUpperCase()

  return (
    <>
      {/* A keyboard user should not have to Tab through the whole menu to reach
          the page. Visually hidden until focused. */}
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>

      <div className="shell">
        <aside
          ref={sidebarRef}
          className={`shell__sidebar ${menuOpen ? 'is-open' : ''}`}
          aria-label="Sidebar"
        >
          <div className="brand">
            <Logo />
            <span className="brand__text">
              <strong>Valet</strong> Transparency
            </span>
          </div>

          <nav className="nav" aria-label="Primary">
            {groups.map((group) => (
              <div className="nav__group" key={group.label}>
                <p className="nav__group-label" id={`nav-group-${group.id}`}>
                  {group.label}
                </p>
                <ul role="list" aria-labelledby={`nav-group-${group.id}`}>
                  {group.links.map((link) => (
                    <li key={link.to}>
                      <NavLink
                        to={link.to}
                        end={link.end}
                        onClick={closeMenu}
                        className={({ isActive }) => `nav__link ${isActive ? 'is-active' : ''}`}
                      >
                        <span className="nav__icon" aria-hidden="true">
                          {link.icon}
                        </span>
                        <span>{link.label}</span>
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>

          <div className="shell__sidebar-footer">
            <p className="muted">Telemetry</p>
            <Badge
              tone={
                APP_CONFIG.telemetryLabel.toLowerCase().includes('simulated')
                  ? 'simulated'
                  : 'hardware'
              }
            >
              {APP_CONFIG.telemetryLabel}
            </Badge>
          </div>
        </aside>

        {/* A real element rather than a CSS pseudo-element, so clicking outside
            the drawer closes it — the behaviour a phone user expects. */}
        {menuOpen && (
          <button
            type="button"
            className="shell__backdrop"
            aria-label="Close navigation"
            onClick={closeMenu}
            tabIndex={-1}
          />
        )}

        <div className="shell__main">
          <header className="topbar">
            <button
              ref={toggleRef}
              type="button"
              className="topbar__toggle"
              aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
              aria-expanded={menuOpen}
              aria-controls="main-content"
              onClick={() => setMenuOpen((open) => !open)}
            >
              ☰
            </button>

            <div className="topbar__title">{APP_CONFIG.appName}</div>

            {/* The bell sits outside `topbar__user` so it keeps its place when
                the identity block is replaced by a sign-in link. */}
            <NotificationBell />

            <div className="topbar__user">
              {user ? (
                <>
                  <div className="topbar__avatar" aria-hidden="true">
                    {initials || '?'}
                  </div>
                  <div className="topbar__identity">
                    <strong>{user.email ?? 'Signed in'}</strong>
                    <span className="muted">{ROLE_LABELS[user.role] ?? user.role}</span>
                  </div>
                  <button type="button" className="btn btn--ghost" onClick={handleSignOut}>
                    Sign out
                  </button>
                </>
              ) : (
                <NavLink className="btn" to="/login">
                  Sign in
                </NavLink>
              )}
            </div>
          </header>

          <main className="content" id="main-content" tabIndex={-1}>
            <Outlet />
          </main>

          <footer className="footer">
            <span>AI-Powered Valet Transparency and Vehicle Security Platform</span>
            <span className="muted">Academic MVP · React + Flask + PostgreSQL + OpenCV</span>
          </footer>
        </div>
      </div>
    </>
  )
}

/**
 * Build the navigation for a role, grouped by intent.
 *
 * Previously every link carried `soon: true`, which stamped a "soon" pill on
 * screens that are fully implemented — a customer reading it would reasonably
 * conclude the dashboard did not work yet. The flag is gone entirely; if a route
 * is ever genuinely unfinished, that belongs on the route, not in the menu.
 */
function buildMenu(role) {
  const groups = [
    {
      id: 'platform',
      label: 'Platform',
      links: [{ to: '/status', label: 'System Status', icon: '◉', end: true }],
    },
  ]

  if (role === 'CUSTOMER') {
    groups.push({
      id: 'custody',
      label: 'My custody',
      links: [
        { to: '/dashboard', label: 'Customer Dashboard', icon: '▤' },
        { to: '/tracking', label: 'Live Tracking', icon: '📍' },
        { to: '/alerts', label: 'Security Alerts', icon: '⚠' },
      ],
    })
    groups.push({
      id: 'records',
      label: 'Records',
      links: [
        { to: '/vehicles', label: 'My Vehicles', icon: '🚗' },
        { to: '/tickets', label: 'Valet Tickets', icon: '🎫' },
      ],
    })
  }

  if (role === 'VALET_OPERATOR' || role === 'VALET_MANAGER' || role === 'ADMIN') {
    groups.push({
      id: 'operations',
      label: 'Operations',
      links: [
        { to: '/valet', label: 'Valet Dashboard', icon: '⚙' },
        { to: '/checkin', label: 'Vehicle Check-in', icon: '📷' },
      ],
    })
  }

  if (role === 'VALET_MANAGER' || role === 'ADMIN') {
    groups.push({
      id: 'oversight',
      label: 'Oversight',
      links: [
        { to: '/manager', label: 'Manager Dashboard', icon: '📊' },
        { to: '/reports', label: 'Reports', icon: '📈' },
        { to: '/audit', label: 'Audit Log', icon: '🗒' },
      ],
    })
  }

  return groups
}

export default MainLayout

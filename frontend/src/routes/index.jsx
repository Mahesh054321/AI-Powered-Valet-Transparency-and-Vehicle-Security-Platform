/**
 * Route table.
 *
 * Keeping routes in one module means the navigation menu, the guards and the
 * pages cannot drift apart. Each entry states the role that may see it, so
 * `MainLayout` and `RequireRole` read from the same source.
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { MainLayout } from '../layouts/MainLayout'
import { CheckInPage } from '../pages/CheckInPage'
import { CustomerDashboardPage } from '../pages/CustomerDashboardPage'
import { ConditionReportPage } from '../pages/ConditionReportPage'
import { InspectionCapturePage } from '../pages/InspectionCapturePage'
import { LoginPage } from '../pages/LoginPage'
import { NotFoundPage } from '../pages/NotFoundPage'
import { ProfilePage } from '../pages/ProfilePage'
import { RegisterPage } from '../pages/RegisterPage'
import { SystemStatusPage } from '../pages/SystemStatusPage'
import { TicketDetailPage } from '../pages/TicketDetailPage'
import { TrackingPage } from '../pages/TrackingPage'
import { AlertsPage } from '../pages/AlertsPage'
import { AuditLogPage } from '../pages/AuditLogPage'
import { ManagerDashboardPage } from '../pages/ManagerDashboardPage'
import { ValetDashboardPage } from '../pages/ValetDashboardPage'
import { TicketListPage } from '../pages/TicketListPage'
import { VehicleDetailPage } from '../pages/VehicleDetailPage'
import { VehicleListPage } from '../pages/VehicleListPage'
import { VehicleRegisterPage } from '../pages/VehicleRegisterPage'
import { ROLES } from '../utils/constants'
import { useAuth } from '../hooks/useAuth'
import { RequireAuth } from './RequireAuth'
import { RequireRole } from './RequireRole'

/** Declarative route definitions: path, element, and which roles may see it. */
export const ROUTES = [
  {
    path: '/status',
    label: 'System Status',
    icon: '◉',
    roles: null, // visible to every signed-in role
    element: <SystemStatusPage />,
    phase: null,
  },
  {
    path: '/dashboard',
    label: 'Customer Dashboard',
    icon: '▤',
    roles: [ROLES.CUSTOMER],
    element: <CustomerDashboardPage />,
    phase: null,
  },
  {
    path: '/vehicles',
    label: 'My Vehicles',
    icon: '🚗',
    roles: [ROLES.CUSTOMER],
    element: <VehicleListPage />,
    phase: null,
  },
  {
    path: '/tickets',
    label: 'Valet Tickets',
    icon: '🎫',
    roles: null, // a valet needs to find the ticket they just issued
    element: <TicketListPage />,
    phase: null,
  },
  {
    path: '/tracking',
    label: 'Live Tracking',
    icon: '📍',
    // Not role-gated: a customer tracks their own vehicle, staff track any, and
    // the backend is what actually decides. Triage controls inside the page are
    // gated separately, so a valet can watch without being able to close an
    // alert about a vehicle they may have driven.
    roles: null,
    element: <TrackingPage />,
    phase: null,
  },
  {
    path: '/valet',
    label: 'Valet Dashboard',
    icon: '⚙',
    roles: [ROLES.VALET_OPERATOR, ROLES.VALET_MANAGER, ROLES.ADMIN],
    element: <ValetDashboardPage />,
    phase: null,
  },
  {
    path: '/checkin',
    label: 'Vehicle Check-in',
    icon: '📷',
    roles: [ROLES.VALET_OPERATOR, ROLES.VALET_MANAGER, ROLES.ADMIN],
    element: <CheckInPage />,
    phase: null,
  },
  {
    path: '/condition',
    label: 'Condition Assessment',
    icon: '🔍',
    roles: [ROLES.VALET_OPERATOR, ROLES.VALET_MANAGER, ROLES.ADMIN],
    element: <InspectionCapturePage />,
    phase: null,
  },
  {
    path: '/alerts',
    label: 'Alert Centre',
    icon: '🔔',
    // Not role-gated: every role has alerts, and the backend is what decides
    // which ones. Gating the route would only hide the page, not the data.
    roles: null,
    element: <AlertsPage />,
    phase: null,
  },
  {
    path: '/manager',
    label: 'Manager Dashboard',
    icon: '📊',
    roles: [ROLES.VALET_MANAGER, ROLES.ADMIN],
    element: <ManagerDashboardPage />,
    phase: null,
  },
  {
    path: '/reports',
    label: 'Reports',
    icon: '📈',
    roles: [ROLES.VALET_MANAGER, ROLES.ADMIN],
    element: null,
    phase: 'Phase 10',
  },
  {
    path: '/audit',
    label: 'Audit Log',
    icon: '🗒',
    roles: [ROLES.VALET_MANAGER, ROLES.ADMIN],
    element: <AuditLogPage />,
    phase: 'Phase 12',
  },
]

/** True when a route should appear in the menu for the given role. */
export function isRouteVisibleTo(route, role) {
  return route.roles === null || (role != null && route.roles.includes(role))
}

function RoleHome() {
  const { role } = useAuth()
  if (role === ROLES.CUSTOMER) return <Navigate to="/dashboard" replace />
  if (role === ROLES.VALET_OPERATOR) return <Navigate to="/valet" replace />
  if (role === ROLES.VALET_MANAGER || role === ROLES.ADMIN) return <Navigate to="/manager" replace />
  return <Navigate to="/status" replace />
}

export function AppRoutes() {
  return (
    <Routes>
      {/* Public: no session needed to sign in, register, or check the API is up. */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/status" element={<SystemStatusPage />} />

      {/* Everything else sits inside the app shell and needs a session.
          `RequireAuth` only decides whether to render the shell; the backend
          still authorises every API call independently. */}
      <Route
        element={
          <RequireAuth>
            <MainLayout />
          </RequireAuth>
        }
      >
        <Route index element={<RoleHome />} />

        {/* Profile / Account Settings */}
        <Route path="/profile" element={<ProfilePage />} />

        {/* Detail routes are not menu entries, so they live outside the
            ROUTES table but still behind the same guards. */}
        <Route
          path="/vehicles/new"
          element={
            <RequireRole roles={[ROLES.CUSTOMER]}>
              <VehicleRegisterPage />
            </RequireRole>
          }
        />
        <Route
          path="/vehicles/:id"
          element={
            <RequireRole
              roles={[
                ROLES.CUSTOMER,
                ROLES.VALET_OPERATOR,
                ROLES.VALET_MANAGER,
                ROLES.ADMIN,
              ]}
            >
              <VehicleDetailPage />
            </RequireRole>
          }
        />
        <Route path="/tickets/:id" element={<TicketDetailPage />} />
        {/* The report is readable by the customer who owns the vehicle as well
            as by staff, so it is not role-gated here; the backend decides. */}
        <Route path="/condition/:custodyId" element={<ConditionReportPage />} />
        <Route path="/inspections/:custodyId" element={<ConditionReportPage />} />
        {/* Tracking a specific custody session. */}
        <Route path="/tracking/:custodyId" element={<TrackingPage />} />

        {ROUTES.filter((route) => route.path !== '/status').map((route) => (
          <Route
            key={route.path}
            path={route.path}
            element={
              <RequireRole roles={route.roles}>
                {route.element ?? <ComingSoon title={route.label} phase={route.phase} />}
              </RequireRole>
            }
          />
        ))}

        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

/**
 * Honest placeholder for a screen whose module has not been built yet.
 * It never renders fake data.
 */
function ComingSoon({ title, phase }) {
  return (
    <section className="panel">
      <header className="panel__header">
        <h2>{title}</h2>
        {phase && <span className="badge badge--muted">{phase}</span>}
      </header>
      <p className="muted">
        This screen is on the roadmap but is not implemented yet. The platform is delivered
        incrementally, module by module, so navigation only exposes screens that actually talk to
        the API.
      </p>
    </section>
  )
}

export default AppRoutes

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { QueryProvider } from './context/QueryProvider.jsx'
import { ToastProvider } from './context/ToastContext.jsx'

/* Stylesheet load order is load-bearing. tokens → base → shell → components →
   patterns → pages: a later sheet may compose an earlier primitive, but a
   same-specificity duplicate would silently win, so nothing downstream may
   redefine a primitive. If a screen needs a different button, that is a new
   variant in components.css, not an override in a page sheet. */
import './styles/tokens.css'
import './styles/base.css'
import './styles/shell.css'
import './styles/components.css'
import './styles/patterns.css'
import './styles/pages/auth.css'
import './styles/pages/customer.css'
import './styles/pages/valet.css'
import './styles/pages/manager.css'
import './styles/pages/tracking.css'
import './styles/pages/alerts.css'
import './styles/pages/inspections.css'
import './styles/pages/audit.css'
import './styles/pages/tickets.css'
import './styles/pages/retrieval.css'

/**
 * Provider order matters:
 *   BrowserRouter  - routing context, needed by the auth guard
 *   QueryProvider  - server-state cache
 *   ToastProvider  - outermost of the UI providers, so any component below it
 *                    (including the layout and the sign-in screen) can report an
 *                    outcome. A toast provider nested under the thing that needs
 *                    it cannot be reached by that thing.
 *   AuthProvider   - identity; reads the persisted token on mount
 */
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <QueryProvider>
        <ToastProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </ToastProvider>
      </QueryProvider>
    </BrowserRouter>
  </StrictMode>,
)

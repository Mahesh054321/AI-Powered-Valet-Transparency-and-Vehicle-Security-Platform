import { AppRoutes } from './routes'

/**
 * Application root. All routing lives in `src/routes`; this component exists so
 * the provider tree in `main.jsx` has a single child.
 */
export default function App() {
  return <AppRoutes />
}

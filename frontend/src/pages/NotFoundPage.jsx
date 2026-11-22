import { Link } from 'react-router-dom'
import { Panel } from '../components/ui'

export function NotFoundPage() {
  return (
    <div className="page">
      <Panel title="404 — page not found">
        <p className="muted">The page may have moved, or the address may be incorrect.</p>
        <Link className="btn" to="/">
          Go to your dashboard
        </Link>
      </Panel>
    </div>
  )
}

export default NotFoundPage

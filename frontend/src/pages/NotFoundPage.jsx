import { Link } from 'react-router-dom'
import { Panel } from '../components/ui'

export function NotFoundPage() {
  return (
    <div className="page">
      <Panel title="404 — page not found">
        <p className="muted">That route does not exist in this build.</p>
        <Link className="btn" to="/status">
          Back to system status
        </Link>
      </Panel>
    </div>
  )
}

export default NotFoundPage

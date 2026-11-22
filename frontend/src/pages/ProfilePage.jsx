import React, { useState } from 'react'
import { useAuth, useSignOut } from '../hooks/useAuth'
import { Badge, Panel } from '../components/ui'
import { ROLE_LABELS } from '../utils/constants'
import { useToast } from '../context/ToastContext'

export function ProfilePage() {
  const { user, role } = useAuth()
  const signOut = useSignOut()
  const toast = useToast()

  const [mobileGpsSharing, setMobileGpsSharing] = useState(() => {
    return localStorage.getItem('valet_mobile_gps_sharing') !== 'false'
  })

  const [emailAlerts, setEmailAlerts] = useState(true)
  const [conditionAlerts, setConditionAlerts] = useState(true)
  const [retrievalAlerts, setRetrievalAlerts] = useState(true)

  const handleToggleGpsSharing = (e) => {
    const enabled = e.target.checked
    setMobileGpsSharing(enabled)
    localStorage.setItem('valet_mobile_gps_sharing', enabled ? 'true' : 'false')
    toast.success(
      enabled ? 'Mobile GPS Sharing enabled' : 'Mobile GPS Sharing disabled',
      enabled
        ? 'Your browser will request location permission when tracking active custody.'
        : 'Browser geolocation updates are paused. You can re-enable anytime.'
    )
  }

  return (
    <div className="page stack">
      <header className="page__header">
        <div>
          <h1>My Account &amp; Preferences</h1>
          <p className="muted">
            Manage your profile details, mobile location sharing, and notification preferences.
          </p>
        </div>
      </header>

      {/* Profile Overview Card */}
      <Panel title="Account Details">
        <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '1.5rem', alignItems: 'center' }}>
          <div
            style={{
              width: '64px',
              height: '64px',
              borderRadius: '50%',
              background: '#0284c7',
              color: '#fff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '1.5rem',
              fontWeight: 'bold',
            }}
          >
            {(user?.email ?? '?').slice(0, 2).toUpperCase()}
          </div>
          <div>
            <h3 style={{ margin: 0 }}>{user?.full_name || user?.email?.split('@')[0] || 'Valet User'}</h3>
            <p className="muted" style={{ margin: '0.25rem 0' }}>{user?.email}</p>
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
              <Badge tone="badge--info">{ROLE_LABELS[role] ?? role}</Badge>
              <Badge tone="badge--success">Active Account</Badge>
            </div>
          </div>
        </div>

        <dl className="data-list" style={{ marginTop: '1.5rem' }}>
          <div className="data-row">
            <dt>User Identifier</dt>
            <dd><code>{user?.id || user?.user_id || 'Current Session'}</code></dd>
          </div>
          <div className="data-row">
            <dt>Account Role</dt>
            <dd>{ROLE_LABELS[role] ?? role}</dd>
          </div>
          <div className="data-row">
            <dt>Phone Contact</dt>
            <dd>{user?.phone_number || 'None registered'}</dd>
          </div>
        </dl>
      </Panel>

      {/* GPS Tracking Source & Privacy Controls */}
      <Panel
        title="Mobile GPS Tracking &amp; Privacy"
        subtitle="Manage whether this device shares coordinates during active vehicle custody"
      >
        <div className="stack" style={{ gap: '1rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
            <div>
              <strong>Mobile GPS Sharing</strong>
              <p className="muted" style={{ margin: '0.25rem 0 0 0', maxWidth: '600px' }}>
                When enabled and location permission is granted in your browser, this phone can provide
                device-level position updates when dedicated physical car GPS hardware is offline.
              </p>
            </div>
            <label className="checkbox-toggle" style={{ transform: 'scale(1.2)' }}>
              <input
                type="checkbox"
                aria-label="Toggle Mobile GPS Sharing"
                checked={mobileGpsSharing}
                onChange={handleToggleGpsSharing}
              />
              <span style={{ fontWeight: 'bold' }}>{mobileGpsSharing ? 'ON' : 'OFF'}</span>
            </label>
          </div>

          <div className="notice notice--info" style={{ marginTop: '0.5rem' }}>
            <strong>Important Tracking Disclosure:</strong> Mobile GPS tracking measures the location of
            your phone, not dedicated onboard vehicle hardware. If your phone is not inside the vehicle,
            its coordinates do not represent the vehicle's position.
          </div>
        </div>
      </Panel>

      {/* Notification Preferences */}
      <Panel title="Notification Settings" subtitle="Choose alerts delivered to your email and in-app feed">
        <div className="stack" style={{ gap: '1rem' }}>
          <label className="checkbox-toggle">
            <input
              type="checkbox"
              checked={conditionAlerts}
              onChange={(e) => setConditionAlerts(e.target.checked)}
            />
            <span>Vehicle Condition Reports &amp; Inspection Findings</span>
          </label>

          <label className="checkbox-toggle">
            <input
              type="checkbox"
              checked={emailAlerts}
              onChange={(e) => setEmailAlerts(e.target.checked)}
            />
            <span>Geofence Boundary Breaches &amp; Suspicious Movement Alerts</span>
          </label>

          <label className="checkbox-toggle">
            <input
              type="checkbox"
              checked={retrievalAlerts}
              onChange={(e) => setRetrievalAlerts(e.target.checked)}
            />
            <span>Vehicle Retrieval Ready &amp; Handover Updates</span>
          </label>
        </div>
      </Panel>

      {/* Security Actions */}
      <Panel title="Session Management">
        <p className="muted">
          Sign out revokes your current JWT session on the server and clears stored tokens.
        </p>
        <button
          type="button"
          className="btn btn--danger"
          onClick={async () => {
            await signOut()
            window.location.href = '/login'
          }}
        >
          Sign Out of Account
        </button>
      </Panel>
    </div>
  )
}

export default ProfilePage

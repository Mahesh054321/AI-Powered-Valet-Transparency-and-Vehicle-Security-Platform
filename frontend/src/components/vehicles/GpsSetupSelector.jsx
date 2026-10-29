import React from 'react'
import { Badge, Field } from '../ui'

export const TRACKING_OPTIONS = [
  {
    id: 'REAL_GPS',
    title: 'Option A: Real Hardware Tracker (Recommended for Live Tracking)',
    badge: 'Hardware Telemetry',
    tone: 'badge--success',
    description:
      'Direct telemetry from installed vehicle GPS hardware (Teltonika, Traccar, Geotab, OBD-II or Webhook). Eliminates phone battery drain and spoofing.',
    deviceRequired: true,
  },
  {
    id: 'MOBILE_GPS',
    title: 'Option B: Mobile Browser GPS',
    badge: 'Device Geolocation',
    tone: 'badge--info',
    description:
      'Stream position updates directly from the phone browser. Note: The smartphone must physically remain in the vehicle during custody to track the vehicle’s location.',
    disclosure:
      'Phone location tracking disclosure: Geolocation coordinates reflect this mobile device. If the device leaves the car, tracking reflects the person, not the vehicle.',
    deviceRequired: false,
  },
  {
    id: 'SIMULATED',
    title: 'Option C: Demo & Simulation Mode',
    badge: 'Simulated',
    tone: 'badge--muted',
    description:
      'Generates synthetic parking yard maneuvers and geofence tests for demonstration purposes without requiring physical tracker hardware.',
    deviceRequired: false,
  },
]

export const GPS_PROVIDERS = [
  { id: 'generic_webhook', name: 'Generic Webhook / HTTP POST' },
  { id: 'teltonika', name: 'Teltonika (FMB / FMT / FMC Series)' },
  { id: 'traccar', name: 'Traccar GPS Server (Open Source)' },
  { id: 'geotab', name: 'Geotab MyGeotab API' },
  { id: 'mock', name: 'Virtual Test Device' },
]

export function GpsSetupSelector({
  value = 'SIMULATED',
  onChange,
  deviceId = '',
  onDeviceIdChange,
  provider = 'generic_webhook',
  onProviderChange,
  deviceSecret = '',
  onDeviceSecretChange,
  error,
}) {
  return (
    <div className="gps-setup-selector" style={{ display: 'grid', gap: '1rem', marginTop: '0.5rem' }}>
      <label className="field__label" style={{ fontWeight: 600, fontSize: '0.95rem' }}>
        How would you like to track this vehicle?
      </label>

      <div style={{ display: 'grid', gap: '0.75rem' }}>
        {TRACKING_OPTIONS.map((option) => {
          const isSelected = value === option.id
          return (
            <div
              key={option.id}
              onClick={() => onChange(option.id)}
              style={{
                border: isSelected ? '2px solid var(--primary, #3b82f6)' : '1px solid var(--border-default)',
                borderRadius: 'var(--radius-md, 8px)',
                padding: '1rem',
                background: isSelected ? 'var(--surface-muted, #f8fafc)' : 'var(--surface-raised, #ffffff)',
                cursor: 'pointer',
                transition: 'border-color 0.15s ease, background-color 0.15s ease',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.5rem', marginBottom: '0.25rem' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', fontWeight: 600 }}>
                  <input
                    type="radio"
                    name="tracking_method"
                    value={option.id}
                    checked={isSelected}
                    onChange={() => onChange(option.id)}
                  />
                  <span>{option.title}</span>
                </label>
                <Badge tone={option.tone}>{option.badge}</Badge>
              </div>

              <p style={{ margin: '0.25rem 0 0 1.5rem', fontSize: '0.875rem', color: 'var(--text-secondary, #64748b)' }}>
                {option.description}
              </p>

              {option.disclosure && isSelected && (
                <div
                  style={{
                    margin: '0.75rem 0 0 1.5rem',
                    padding: '0.5rem 0.75rem',
                    borderRadius: '6px',
                    background: '#eff6ff',
                    borderLeft: '3px solid #3b82f6',
                    fontSize: '0.8rem',
                    color: '#1e40af',
                  }}
                >
                  ⚠ <strong>Important Notice:</strong> {option.disclosure}
                </div>
              )}

              {option.deviceRequired && isSelected && (
                <div
                  style={{
                    margin: '1rem 0 0 1.5rem',
                    padding: '1rem',
                    border: '1px solid var(--border-subtle, #e2e8f0)',
                    borderRadius: '6px',
                    background: 'var(--surface-raised, #ffffff)',
                    display: 'grid',
                    gap: '0.75rem',
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="grid grid--2">
                    <Field label="Tracker Device ID / IMEI" hint="Required for hardware linkage" error={error?.deviceId}>
                      <input
                        type="text"
                        placeholder="e.g. TELTONIKA-987654 or IMEI"
                        value={deviceId}
                        onChange={(e) => onDeviceIdChange?.(e.target.value)}
                      />
                    </Field>

                    <Field label="GPS Provider / Protocol">
                      <select value={provider} onChange={(e) => onProviderChange?.(e.target.value)}>
                        {GPS_PROVIDERS.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </div>

                  <Field label="Device Secret / API Key (Optional)" hint="For tamper-resistant authenticated push updates">
                    <input
                      type="password"
                      placeholder="Tracker authentication token"
                      value={deviceSecret}
                      onChange={(e) => onDeviceSecretChange?.(e.target.value)}
                      autoComplete="off"
                    />
                  </Field>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default GpsSetupSelector

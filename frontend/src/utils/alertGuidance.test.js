import { describe, expect, it } from 'vitest'
import { alertGuidance } from './alertGuidance'

describe('alertGuidance', () => {
  it('avoids claiming geofence alert is proven theft', () => {
    expect(alertGuidance('GEOFENCE_BREACH')).toMatch(/review the vehicle track/i)
    expect(alertGuidance('GEOFENCE_BREACH')).not.toMatch(/theft confirmed/i)
  })
  it('requires human verification for suspected damage', () => {
    expect(alertGuidance('POTENTIAL_DAMAGE')).toMatch(/manager must verify/i)
  })
  it('does not invent guidance for unfamiliar types', () => {
    expect(alertGuidance('CUSTOM')).toBeNull()
  })
})

/** Recommended *next actions*, not facts about the incident. */
const GUIDANCE = {
  GEOFENCE_BREACH: 'Review the vehicle track, assigned boundary and GPS accuracy before confirming a security incident.',
  SUSPICIOUS_MOVEMENT: 'Review location evidence and check with on-duty valet staff before escalating.',
  OVERSPEED: 'Check the speed reading and GPS accuracy; confirm the driving context with staff.',
  POTENTIAL_DAMAGE: 'Open the vehicle condition report. A manager must verify any potential image-based damage.',
  RETRIEVAL_READY: 'Check the handover point and arrange vehicle collection.',
  RETRIEVAL_REQUESTED: 'Check the retrieval queue, locate the vehicle and acknowledge the pickup request.',
}

export function alertGuidance(type) {
  return GUIDANCE[type] ?? null
}

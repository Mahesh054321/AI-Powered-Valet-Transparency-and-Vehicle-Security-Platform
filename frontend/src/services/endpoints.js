/**
 * Endpoint catalogue.
 *
 * Keeping every URL in one module means a rename on the backend is a one-line
 * change here, and a reviewer can read the whole API surface in a minute.
 * Entries are added as their backend blueprints land.
 */

export const ENDPOINTS = {
  // --- service level (implemented, Phase 1) ---
  // Unversioned on purpose: uptime monitors depend on these paths.
  health: '/health',
  readiness: '/readyz',

  // --- auth (implemented, Phase 2) ---
  // The backend also serves these unversioned at /api/auth/*; the versioned
  // path is used so auth follows the same convention as every other endpoint.
  authRegister: '/v1/auth/register',
  authLogin: '/v1/auth/login',
  authLogout: '/v1/auth/logout',
  authRefresh: '/v1/auth/refresh',
  authMe: '/v1/auth/me',
  authUsers: '/v1/auth/users',

  // --- vehicles (implemented, Phase 3) ---
  vehicles: '/v1/vehicles',
  vehicle: (id) => `/v1/vehicles/${id}`,
  vehicleSessions: (id) => `/v1/vehicles/${id}/sessions`,

  // --- tickets & custody (implemented, Phase 4) ---
  tickets: '/v1/tickets',
  ticket: (id) => `/v1/tickets/${id}`,
  // There is no `/tickets/:id/qr` endpoint on purpose. The QR image is rendered
  // once at issue time and the ticket payload carries `qr_url`, which points at
  // /media. One copy of the bytes, one SHA-256 to audit against.
  ticketCheckIn: (ticketId) => `/v1/tickets/${ticketId}/check-in`,
  ticketComplete: (ticketId) => `/v1/tickets/${ticketId}/complete`,
  ticketCancel: (ticketId) => `/v1/tickets/${ticketId}/cancel`,
  custodySessions: '/v1/custody-sessions',
  custodySession: (id) => `/v1/custody-sessions/${id}`,

  // --- check-in support (implemented, Phase 4) ---
  customers: '/v1/customers',
  uploadImages: '/v1/uploads/images',

  // --- inspections & condition assessment (implemented, Phase 5-6) ---
  // The path parameter is the *custody session* id, not an inspection id: a
  // report covers both stages of one custody window.
  inspectionReport: (custodyId) => `/v1/inspections/${custodyId}`,
  inspectionDropOff: '/v1/inspections/dropoff',
  inspectionPickup: '/v1/inspections/pickup',
  inspectionVerify: (detectionId) =>
    `/v1/inspections/detections/${detectionId}/verification`,
  inspectionAnnotate: (detectionId) =>
    `/v1/inspections/detections/${detectionId}/notes`,
  inspectionDispute: (custodyId) =>
    `/v1/inspections/${custodyId}/dispute`,
  inspectionResolveDispute: (custodyId) =>
    `/v1/inspections/${custodyId}/resolve-dispute`,
  inspectionRecompare: (custodyId) =>
    `/v1/inspections/${custodyId}/recompare`,
  inspectionDemo: '/v1/inspections/demo',
  inspectionDemoScenarios: '/v1/inspections/demo/scenarios',
  // The backend also serves all of the above unversioned at
  // /api/inspections/*. The versioned paths are used here so this module
  // follows the same convention as every other endpoint.

  // --- GPS telemetry & live tracking (implemented, Phase 7) ---
  // `gpsTracking` is the dashboard endpoint: one call returns the vehicle, the
  // zone, the geofence polygon, the latest fix, the movement status and the
  // alerts, so the map does not have to stitch four responses together.
  gpsEvents: '/v1/gps/events',
  gpsMobile: '/v1/gps/mobile',
  gpsVehicle: '/v1/gps/vehicle',
  gpsStream: (custodyId) => `/v1/gps/stream/${custodyId}`,
  gpsTracking: (custodyId) => `/v1/gps/${custodyId}`,
  gpsFixes: (custodyId) => `/v1/gps/${custodyId}/fixes`,
  gpsSimulate: '/v1/gps/simulate',
  gpsSimulatorScenarios: '/v1/gps/simulate/scenarios',

  // --- geofences (implemented, Phase 7) ---
  geofences: '/v1/geofences',
  geofencePreview: '/v1/geofences/preview',
  geofence: (zoneId) => `/v1/geofences/${zoneId}`,
  geofenceActivate: (zoneId) => `/v1/geofences/${zoneId}/activate`,
  geofenceDeactivate: (zoneId) => `/v1/geofences/${zoneId}/deactivate`,
  // The check-in flow still reads zones from the directory endpoint; both are
  // served by the same table.
  parkingZones: '/v1/parking-zones',

  // --- security alerts (implemented, Phase 7) ---
  alerts: '/v1/alerts',
  alertAcknowledge: (id) => `/v1/alerts/${id}/acknowledge`,
  alertInvestigate: (id) => `/v1/alerts/${id}/investigate`,
  alertResolve: (id) => `/v1/alerts/${id}/resolve`,
  alertReopen: (id) => `/v1/alerts/${id}/reopen`,

  // --- prototype security classifier (implemented, Phase 9) ---
  // Both read-only. Classification itself happens inline during telemetry
  // ingest; `mlClassify` exists so the model can be exercised and its reasoning
  // inspected without fabricating a GPS fix.
  mlModel: '/v1/ml/model',
  mlClassify: '/v1/ml/classify',

  // --- retrieval prediction (Phase 9b) ---
  // The backend also mounts these unversioned at /api/retrieval/*, which is the
  // path the module's contract was specified against. The versioned prefix is
  // canonical here for consistency with the rest of this file.
  retrievalPredict: '/v1/retrieval/predict',
  retrievalRequest: '/v1/retrieval/request',
  retrievalQueue: '/v1/retrieval/queue',
  customerDashboard: '/v1/customers/me/dashboard',
  retrievalModel: '/v1/retrieval/model',
  retrievalPrediction: (custodyId) => `/v1/retrieval/${custodyId}`,

  // --- valet operator dashboard (Phase 10) ---
  // One composed call for the whole operator screen. Seven parallel reads
  // would be taken at seven moments, and an operator acts on this data: a
  // vehicle list and an alert list that disagree is worse than either alone.
  staffDashboard: '/v1/staff/dashboard',
  staffCondition: (custodyId) => `/v1/staff/custody/${custodyId}/condition`,

  // --- manager analytics & reporting (Phase 10) ---
  // One composed call for the whole screen, filtered by query string. The
  // report endpoint is the same query path in a downloadable shape, so a filed
  // report cannot disagree with the screen it summarised.
  managerDashboard: '/v1/manager/dashboard',
  managerReport: '/v1/manager/report',

  // --- centralized alert feed (Phase 11) ---
  // One feed for every role, scoped server-side. `summary` exists so the
  // notification badge costs one indexed count instead of a full fetch on
  // every poll.
  alertFeed: '/v1/feed',
  alertSummary: '/v1/feed/summary',
  alertReadAll: '/v1/feed/read-all',

  // --- reports (Phase 10) ---
  reports: '/v1/reports',

  // --- audit trail (Phase 12) ---
  // Read-only. There is no POST/PATCH/DELETE counterpart on the backend and
  // the table rejects both at the database level, so this catalogue has no
  // write entry to offer -- which is the point of the log viewer being able to
  // state plainly that it cannot edit anything.
  audit: '/v1/audit',
  auditFilters: '/v1/audit/filters',
  // The story of one custody, oldest first.
  auditTimeline: (custodyId) => `/v1/audit/timeline/${custodyId}`,
  // Every custody timeline for one vehicle, most recent visit first.
  auditVehicle: (vehicleId) => `/v1/audit/vehicles/${vehicleId}`,
}

export default ENDPOINTS

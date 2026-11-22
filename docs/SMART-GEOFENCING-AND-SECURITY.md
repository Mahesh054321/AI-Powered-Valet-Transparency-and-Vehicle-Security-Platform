# Smart Geofencing and Intelligent Vehicle Security Alert System

## 1. Architectural Overview

The AI-Powered Valet Transparency and Vehicle Security Platform has been upgraded with an enterprise-grade Smart Geofencing and Real-Time Vehicle Security Subsystem. This system provides end-to-end custody tracking, meter-accurate multi-geometry boundary enforcement, explainable anomalous movement detection, hardware-backed tamper monitoring, and multi-channel customer security notifications.

```
+--------------------------------------------------------------------------------------------------+
|                                    TELEMETRY INGESTION                                           |
|                                                                                                  |
|   +------------------------------------+        +--------------------------------------------+   |
|   |   Authorized Mobile Browser GPS    |        |       Physical Tracker Hardware            |   |
|   |         (`MOBILE_GPS`)             |        |              (`REAL_GPS`)                  |   |
|   |  - Driver/Customer consent modal   |        |  - Compatible OBD-II / Hardwired Tracker   |   |
|   |  - Distinct phone vs vehicle mark  |        |  - Ignition status, door sensors, shock    |   |
|   |  - Client-side watchPosition       |        |  - Device disconnect & tamper switches     |   |
|   +-----------------+------------------+        +--------------------+-----------------------+   |
+---------------------|------------------------------------------------|---------------------------+
                      | POST /v1/gps/mobile                            | POST /v1/gps/devices/{id}/telemetry
                      v                                                v
+--------------------------------------------------------------------------------------------------+
|                                  FLASK / POSTGRESQL BACKEND                                      |
|                                                                                                  |
|  [ Authentication & RBAC ] ──> [ Anti-Replay & Timestamp Validation ]                            |
|                                         │                                                        |
|                                         ▼                                                        |
|  [ Geofence & Anomaly Engine ] ───────────────────────────────────────────────────────────────┐   |
|    • Multi-Geometry Containment (Circle, Polygon, Oriented Rectangle)                         │   |
|    • Geospatial Meter Calculations (Vincenty/Haversine geodesics)                             │   |
|    • Hysteresis Buffers & Consecutive-Reading Debounce                                        │   |
|    • Movement Anomaly Checks (Unexpected parked motion, jump teleportation, prolonged loss)  │   |
|    • Hardware Sensor Validation (Ignition, door open, vibration/shock, power-loss)            │   |
|                                         │                                                        │   |
|                                         ▼                                                        ▼   |
|  [ Alert Center & Email Dispatcher ]               [ Server-Sent Events (SSE) Broadcaster ]      |
|    • Canonical Alert Feed Integration                • Channel per custody / vehicle             |
|    • In-App Live Notifications                       • 1-way low-latency browser push            |
|    • Customer HTML Email Templates                   • Automatic 15s heartbeats                  |
|    • Triage Workflow (Acknowledge,                   • Token query param authentication          |
|      Investigate, Resolve, Reopen)                                                               |
+--------------------------------------------------------------------------------------------------+
                      │ SSE /v1/gps/stream?custody_session_id=...&token=...
                      v
+--------------------------------------------------------------------------------------------------+
|                                      REACT / VITE FRONTEND                                       |
|                                                                                                  |
|  • Live SSE Status Indicators (Green pulsating Live badge, heartbeats, auto-reconnect)           |
|  • Freshness State Machine: LIVE (<=5m), STALE (5-15m), OFFLINE (>15m)                            |
|  • Safe Zone Builder (Circle, Polygon, Oriented Rectangle, dynamic live preview metrics)        |
|  • Hardware Sensor Status Matrix (Active signals vs greyed-out unequipped markers)               |
|  • Phone vs Vehicle Dual Pin Visualization with custody awareness disclosures                    |
|  • Full Alert Triage Drawer: Acknowledge, Investigate, Resolve, Reopen with audit notes          |
+--------------------------------------------------------------------------------------------------+
```

---

## 2. Real GPS Ingestion & Disclosures

### 2.1 Supported Telemetry Sources
1. **`MOBILE_GPS` (Mobile Browser Telemetry)**:
   - Captured via `navigator.geolocation.watchPosition` on mobile browsers.
   - Requires explicit driver or customer consent.
   - Telemetry packets are tagged with `source: MOBILE_GPS`.
   - **User Transparency Disclosure**: The UI explicitly clarifies that the smartphone itself is being tracked, not the physical chassis. When using a smartphone as the vehicle's tracking proxy, the phone must remain inside the vehicle.
2. **`REAL_GPS` (Authenticated Physical Tracker Hardware)**:
   - Used only by telemetry from a registered device authenticated with its configured secret.
   - Transmits telemetry packets tagged with `source: REAL_GPS`.
   - Registering a device identifier alone does not mean it is connected or supplying fixes.
   - Real vehicle coordinates are rendered distinctly from the driver's phone location on the interactive map.

`SIMULATED` fixes are for development/demo use only. Staff-entered observations are
tagged `MANUAL` through `POST /v1/gps/vehicle`; they are not represented as a
connected hardware tracker.

### 2.2 Replay Protection and Time Drift Validation
- GPS points must carry an ISO-8601 UTC timestamp.
- Telemetry timestamps drifting more than 5 minutes into the future or more than 24 hours into the past are rejected with `400 Bad Request`.
- GPS coordinates are validated against standard geographic ranges (`-90 <= lat <= 90`, `-180 <= lon <= 180`).

---

## 3. Freshness and Accuracy State Machine

Tracking telemetry is evaluated against deterministic freshness and accuracy thresholds:

| Status | Condition | Map UI Presentation |
|---|---|---|
| **`LIVE`** | Timestamp age <= 5 minutes and accuracy <= 35 meters | Connected status, live SSE badge |
| **`STALE`** | Timestamp age > 5 minutes and <= 15 minutes | Amber warning badge, last known position |
| **`OFFLINE`** | Timestamp age > 15 minutes or prolonged connection drop | Offline status, last known position |
| **`LOCATION UNCERTAIN`** | GPS horizontal accuracy > 35 meters (regardless of recency) | Yellow dashed uncertainty circle |

---

## 4. Safe Zone Builder & Meter-Based Calculations

The Safe Zone Builder allows customers, managers, and valets to define geographic safety enclosures across three geometries:

### 4.1 Supported Geometries
- **CIRCULAR**: Center latitude/longitude + radius in meters.
- **POLYGON**: Arbitrary vertices in GeoJSON format.
- **RECTANGLE**: Center latitude/longitude, length in meters, width in meters, and rotation/heading in degrees (0–360°). Converted server-side into a rotated rectangular bounding polygon using geodesic Vincenty projections.

### 4.2 Interactive Previews & Safety Parameters
- **Live Preview (`POST /v1/geofences/preview`)**: Evaluates zone perimeter, surface area ($m^2$), and sample coordinate containment before saving to the database.
- **Configurable Tolerances**:
  - `tolerance_meters`: Accuracy buffer before an exit is confirmed.
  - `consecutive_readings`: Minimum consecutive violations (e.g., 2) before triggering an alert.
  - `dwell_time_seconds`: Temporal requirement before confirming outside state.
  - `cooldown_seconds`: Rate-limits repetitive exit/entry notifications.

---

## 5. Movement and Violation Detection Engine

The anomaly engine executes a deterministic evaluation pipeline on every ingested fix:

1. **Boundary State Transition**:
   - `INSIDE`: Distance from boundary > `tolerance_meters`.
   - `NEAR_BOUNDARY`: Fix within tolerance margin of the perimeter.
   - `OUTSIDE`: Fix definitively outside perimeter + tolerance.
   - State transitions (`INSIDE -> OUTSIDE` or `OUTSIDE -> INSIDE`) trigger confirmed departure or return-to-zone events.
2. **Unexpected Parked Movement**:
   - Triggers when speed exceeds $5\text{ km/h}$ or displacement exceeds $25\text{ m}$ while the vehicle or custody ticket is marked `is_parked=True`.
3. **Plausibility & Teleportation Detection**:
   - Detects physically impossible jumps ($>180\text{ km/h}$ displacement between consecutive fixes) and flags `IMPLAUSIBLE_JUMP` rather than false theft.
4. **Prolonged Telemetry Loss**:
   - Detects when an active vehicle goes dark for more than the configured session timeout.

---

## 6. Physical Vehicle Tamper Signals & Hardware Support Matrix

Tamper detection relies solely on hardware telemetry signals. GPS coordinate shifts alone are never fabricated as physical tampering.

| Hardware Sensor | Payload Key | Anomaly Detected | State When Unsupported |
|---|---|---|---|
| **Tamper Switch** | `tracker_tamper` | `TAMPER_ALARM` | Marked `Unavailable` in UI |
| **Ignition Status** | `ignition_status` | `UNEXPECTED_IGNITION` | Marked `Unavailable` in UI |
| **Door Switch** | `door_open` | `DOOR_BREACH` | Marked `Unavailable` in UI |
| **Vibration / Shock** | `vibration_shock` | `IMPACT_DETECTED` | Marked `Unavailable` in UI |
| **Battery / Disconnect**| `power_loss` / `device_removed` | `DEVICE_DISCONNECTED` | Marked `Unavailable` in UI |

Unsupported sensors are surfaced with a neutral dashed badge (`Unavailable (Tracker unequipped)`), ensuring users are never misled.

---

## 7. Customer Email Alerts and Alert Triage Workflow

### 7.1 Security Alerts Integration
Alerts generated by the geofence engine are automatically inserted into the centralized `security_alerts` table and projected into the canonical `alerts` feed under category `SUSPICIOUS_MOVEMENT`.

### 7.2 Full Triage Workflow
- **`ACKNOWLEDGE` (`POST /v1/alerts/<id>/acknowledge`)**: Valet or manager marks alert seen.
- **`INVESTIGATE` (`POST /v1/alerts/<id>/investigate`)**: Security staff initiates investigation and logs inquiry notes.
- **`RESOLVE` (`POST /v1/alerts/<id>/resolve`)**: Manager or customer resolves issue with final disposition.
- **`REOPEN` (`POST /v1/alerts/<id>/reopen`)**: Reopens a prematurely closed alert if telemetry indicates continuing violation.

### 7.3 Customer Notifications (`EmailChannel`)
- Automatic customer email notifications sent via `backend/services/notifications/email.py`.
- Features responsive HTML formatting, Google Maps link to vehicle location, severity styling, and anti-spam rate limiting.
- Gracefully falls back to structured logging when SMTP credentials are not configured.

---

## 8. Real-Time Streaming Architecture (SSE)

### 8.1 Server-Sent Events Endpoint
- **URL**: `GET /v1/gps/stream?custody_session_id=<id>&vehicle_id=<id>&token=<jwt>`
- **Transport**: Standard HTTP streaming (`text/event-stream`).
- **Authentication**: Supports standard `Authorization: Bearer <token>` header or `?token=<jwt>` query parameter for native browser `EventSource` compatibility.

### 8.2 Event Types
1. **`initial`**: Emitted immediately upon connection with current vehicle position, phone position, safe zone geometry, freshness state, and active hardware sensors.
2. **`location_update`**: Emitted immediately when a new telemetry fix is ingested.
3. **`security_alert`**: Emitted when an anomaly or boundary violation is detected.
4. **`heartbeat`**: Emitted every 15 seconds to keep intermediary proxies and NAT firewalls alive.

---

## 9. Test Suite Verification

### Backend Pytest Suite: 1,080 / 1,080 Passed
- `tests/test_smart_geofence_security.py`: 6 passed (Safe zone preview, customer permissions, mobile vs vehicle ingest, hardware sensors, alert lifecycle, SSE streaming)
- `tests/test_geofence_engine.py`: 54 passed (Math calculations, polygon & circle containment, hysteresis, debouncing)
- `tests/test_geofence_endpoints.py`: 77 passed (REST CRUD, batch ingest, validation)
- `tests/test_alert_center.py`: 34 passed (Triage workflows, canonical projection, email dispatcher)
- Full regression test suites (auth, audit, tickets, vehicles, inspections, ML): 909 passed.

### Frontend Vitest Suite: 642 / 642 Passed
- `src/components/tracking/SafeZoneBuilder.test.jsx`: 3 passed (Polygon, circle, oriented rectangle preview & save)
- `src/hooks/useMobileGeolocation.test.jsx`: 3 passed (Disclosure consent, fix transmission, cleanup)
- `src/hooks/useLiveGpsStream.test.jsx`: 3 passed (SSE connection, initial payload, stream updates)
- `src/components/tracking/AlertCard.test.jsx`: 14 passed (Acknowledge, investigate, resolve, reopen buttons)
- Full frontend component and page suites: 619 passed.

# Architecture

## 1. Design principles

1. **Two deployables, one contract.** The React SPA and the Flask API are
   independent processes that communicate only over REST/JSON. Neither imports
   the other; neither shares a filesystem requirement in production.
2. **One composition root.** `create_app()` in `backend/app/__init__.py` is the
   only place where extensions, blueprints, hooks and error handlers are
   assembled. Scripts, the WSGI server and the test suite all call it, so
   behaviour cannot drift between them.
3. **Layering is enforced by import direction.** `routes` → `services` →
   `database` / `models`. `utils/` and `config/` are importable from anywhere
   but import nothing from `routes/` or `services/`, and `utils/` contains no
   Flask imports at all. No service imports from `routes/`.
4. **Configuration comes from the environment.** No secret appears in source.
   `config/environment.py` is the only module that reads `os.environ`; every
   other module reads the `Settings` object.
5. **One response envelope.** Success and failure both return a predictable
   shape, so the frontend has a single error path.
6. **Explainability over magic.** Every AI-derived value is stored alongside
   the inputs and reasoning that produced it.

## 2. Request lifecycle

```
Browser
  │
  │  GET /api/health                (relative URL → same origin)
  ▼
Vite dev server (dev) / Nginx (prod)
  │  proxy /api → 127.0.0.1:5000
  ▼
Flask
  │  before_request : assign/propagate X-Request-Id, start timer (middleware/)
  │  route          : blueprint handler (routes/)
  │  decorators     : @require_auth → JWT verify → AuthContext on flask.g
  │                   @roles_required / @capability_required
  │  handler        : validate input (utils/validation) → call service
  │  service        : business rules → SQLAlchemy model → PostgreSQL
  │  response       : utils/responses.ok(...) → { success, data, meta }
  │  after_request  : add X-Request-Id + security headers, log the request
  ▼
axios interceptor (frontend/src/services/apiClient.js)
  │  unwraps envelope → component receives `data`
  │  on 401 → notifies onUnauthorized listeners → AuthProvider signs out
  ▼
React component (TanStack Query cache)
```

Any exception on that path is converted by `app/error_handlers.py` into the
same envelope. `IntegrityError` becomes a `409 CONFLICT` with the constraint
name; unexpected exceptions become `500` with only the request id exposed — the
traceback stays in the server log.

Health is the one route that bypasses the versioned surface: `GET /api/health`
and `GET /api/readyz` are mounted unversioned so a load balancer or an
evaluator can call them without knowing the API version, and so they keep
answering while `/api/v1/...` is under construction.

## 3. Response envelope

Success:

```json
{
  "success": true,
  "data": { "status": "up" },
  "meta": { "request_id": "4a475a69…", "server_time": 1788762839.09 }
}
```

Paginated list:

```json
{
  "success": true,
  "data": { "items": [], "pagination": { "page": 1, "page_size": 20, "total_items": 0, "total_pages": 0, "has_next": false, "has_prev": false } },
  "meta": { "request_id": "…" }
}
```

Failure:

```json
{
  "success": false,
  "error": { "code": "VALIDATION_ERROR", "message": "Missing required field(s).", "details": { "missing_fields": ["password"] } },
  "meta": { "request_id": "…" }
}
```

Error codes are stable strings, not prose: `VALIDATION_ERROR`,
`AUTHENTICATION_ERROR`, `AUTHORIZATION_ERROR`, `NOT_FOUND`, `CONFLICT`,
`UNSUPPORTED_MEDIA_TYPE`, `PAYLOAD_TOO_LARGE`, `RATE_LIMITED`,
`DATABASE_ERROR`, `INTERNAL_SERVER_ERROR`.

## 4. Authorization model

Four roles, ordered by privilege:

```
CUSTOMER  →  VALET_OPERATOR  →  VALET_MANAGER  →  ADMIN
```

Rather than scattering `if role == "ADMIN"` through handlers, `utils/security.py`
declares an explicit **capability matrix** and a single inheritance map:

| Capability | CUSTOMER | OPERATOR | MANAGER | ADMIN |
| --- | :-: | :-: | :-: | :-: |
| `vehicle:create`, `vehicle:read_own` | ✔ | | | |
| `custody:check_in`, `custody:check_out` | | ✔ | ✔ | ✔ |
| `inspection:create` | | ✔ | ✔ | ✔ |
| `inspection:review`, `alert:acknowledge`, `zone:manage` | | | ✔ | ✔ |
| `user:manage`, `audit:read_all`, `config:manage` | | | | ✔ |

A manager inherits every operator capability; an admin inherits every manager
capability. Adding a role grants nothing unless it is listed, which is the
property that matters for an academic security review.

Three guards are available to handlers, all in `utils/security.py`:

```python
@require_authentication                # any valid, unrevoked access token
@require_role(Role.VALET_MANAGER)      # explicit roles (inheritance applied)
@capability_required("zone:manage")    # capability-level
```

`require_auth` and `roles_required` remain as shorter aliases; each pair shares
one implementation, so the two names cannot drift apart in behaviour.

`require_authentication` is deliberately more than a signature check. It
resolves the token's `sub` against the `users` table and refuses the request if
the `jti` has been revoked, if the account no longer exists, or if the account
has been suspended — so suspending a user or changing their role takes effect
on the next request rather than when the token expires. If the database is
unreachable it degrades to the cryptographic checks only rather than failing
every request.

The frontend mirrors the role rules only to hide menu items (`RequireAuth` and
`RequireRole`). The backend decision is authoritative: hiding a link is not a
security control, and neither component claims to be one.

### How a session is issued and ended

`POST /auth/login` returns an access token and a refresh token, both HS256 JWTs
carrying `sub`, `role`, `typ`, `jti`, `iss`, `aud`, `iat`, `nbf`, `exp`.
`alg: none` is rejected, and a refresh token presented where an access token is
expected is rejected on its `typ` claim.

Logout writes the token's `jti` to the `revoked_tokens` denylist, because a
token the client discards is still valid until its own expiry. The table is
append-only and its rows outlive the token they revoke only until
`expires_at`, so `prune_revoked_tokens()` can delete expired entries without
weakening anything.

**Registration cannot escalate privilege.** `POST /auth/register` hard-codes
`Role.CUSTOMER` and accepts no `role` parameter; a caller who sends
`"role": "ADMIN"` gets `403`, not a silently downgraded account. Staff accounts
are created only through `POST /auth/users`, which is guarded by
`@require_role(Role.ADMIN)`.

## 4a. Derived state: the ticket status projection

Some state is a *consequence* of other state, and storing it means two rows can
disagree. The digital ticket's status is exactly that: whether a car is waiting
for its owner depends on both the ticket and its custody session.

The platform therefore keeps one stored vocabulary and presents a derived one:

```
ISSUED    ────────────────────────────────►  CREATED
ACTIVE    + PARKED / IN_TRANSIT / RETRIEVAL ►  IN_CUSTODY
ACTIVE    + READY_FOR_PICKUP ───────────────►  READY_FOR_PICKUP
COMPLETED ────────────────────────────────►  COMPLETED
CANCELLED ────────────────────────────────►  CANCELLED
EXPIRED   ────────────────────────────────►  EXPIRED
```

`READY_FOR_PICKUP` needs no new column because it is the *session* that reaches
that state while the ticket stays `ACTIVE`. `project_ticket_status()` is a pure
function, tested against every combination, and every API response carries
`stored_status` alongside `status` so a reviewer can see the mapping instead of
trusting it.

`EXPIRED` is deliberately passed through rather than folded into `CREATED`.
Reporting an expired ticket as freshly created would tell a customer their car
is waiting when the platform has stopped tracking it - a small lie in an
application whose entire purpose is not lying about where cars are.

The same rule applies to vehicle status: `IN_CUSTODY` is written by the check-in
workflow and cannot be set through `PATCH /vehicles/<id>`, so the UI can never
claim a car is parked when no custody session says so.

## 4b. Binary assets: references, and an honest trade-off

No table stores image bytes. `services/media.py` is the only module that
touches the filesystem, and it enforces four invariants: the stored filename is
generated server-side and never taken from the client; the declared extension
is checked against the file's own signature bytes; dimensions come from decoding
the pixels with OpenCV rather than a client-supplied header; and a reference is
re-validated and containment-checked before anything is served.

Serving is the weak point and is documented as such rather than papered over:
`/media/<path>` is unauthenticated, because a browser rendering `<img src>`
cannot attach a bearer token. The access control is the unguessability of the
UUID4 filename (122 bits) plus the absence of directory listing. Anyone holding
a full URL can fetch that image. That is acceptable for condition photographs in
an academic MVP and is not acceptable for identity documents; the production
answer is a short-lived signed URL, and the README lists it as pre-deployment
work.

## 5. Data layer

- **SQLAlchemy 2.0** declarative models with a shared naming convention so
  Alembic generates stable constraint names (`uq_users_email`,
  `fk_valet_tickets_vehicle_id_vehicles`, …).
- **Alembic** owns the schema. `migrations/env.py` derives the DSN from
  `config`, so `alembic.ini` contains no credentials. Every revision has a
  working `downgrade()`.
- **`database/checks.py`** holds schema assertions used both by
  migrations and by tests, so a migration cannot silently produce a schema the
  tests do not expect.
- Migrations are transactional (PostgreSQL supports transactional DDL), so a
  failed revision rolls back completely.

### Schema

Eleven domain tables, created by revision `0002`, plus the authentication
support table added by revision `0003` — fourteen tables in total once
`alembic_version` and `schema_meta` are counted:

| Table | Purpose | Cardinality |
| --- | --- | --- |
| `users` | identities, hashed passwords, roles, activation state | 1 user → N vehicles, N tickets |
| `vehicles` | customer vehicles, plate, make, colour | 1 vehicle → N custody sessions |
| `parking_zones` | named zones with geofence polygons | 1 zone → N custody sessions |
| `valet_tickets` | the digital ticket, ticket code, QR payload | 1 ticket → 1 custody session |
| `custody_sessions` | drop-off → pickup lifecycle | parent of everything below |
| `gps_events` | telemetry fixes, each labelled by provenance | 1 session → N fixes |
| `vehicle_inspections` | photo sets at check-in and check-out | 1 session → 2 (one per stage) |
| `damage_detections` | findings with region, confidence, evidence | 1 inspection → N findings |
| `security_alerts` | geofence/anomaly findings, acknowledgement | 1 session → N alerts |
| `retrieval_predictions` | predicted wait, queue position, confidence | 1 session → N predictions |
| `audit_logs` | append-only record of every state change | references any table by value |
| `revoked_tokens` | JWT denylist: `jti`s invalidated by logout or an admin action | 1 user → N revoked tokens |

Conventions across all of them: UUID surrogate primary keys from
`gen_random_uuid()`, `created_at` / `updated_at` with `server_default=now()`,
`VARCHAR` status columns backed by `CHECK` constraints, and an index on every
foreign key.

#### Four decisions worth stating explicitly

**Status columns are `VARCHAR` + `CHECK`, not native enums.** Adding a status is
a constraint swap rather than `ALTER TYPE ... ADD VALUE`, and a value can also
be removed. The permitted values are declared once in `models/enums.py`; the
migration writes them as literals so it stays deterministic, and
`test_database_models.py` fails if the two ever drift.

**Business rules live in the database, not in application code.** Two partial
unique indexes carry rules that are easy to get wrong in a service:
`uq_custody_sessions_vehicle_open` (a vehicle cannot be in two *open* sessions,
which is what actually enforces one-custody-at-a-time) and
`uq_vehicle_inspections_session_stage` (exactly one drop-off and one pickup
inspection). Because they are partial, closing a session releases the vehicle
and archiving a vehicle releases its plate.

**No table stores image bytes.** Photographs, QR codes and annotated evidence
live in `storage/media/` (or object storage). The schema stores a validated
reference — path, byte size, dimensions, MIME type and a SHA-256 digest — and
`utils/image_ref.py` rejects `bytes` outright. The digest lets an auditor prove
the bytes behind a finding have not been swapped since it was recorded. There
is no `BYTEA` column anywhere, asserted by test.

**The audit trail outlives the rows it describes.** `audit_logs` is append-only
(no `updated_at`), references entities polymorphically by `entity_type` +
`entity_id` rather than by foreign key, and uses `ON DELETE SET NULL` for its
actor. A `BEFORE DELETE` trigger on `users` then writes `actor_deleted_at`,
which is what lets a retained snapshot be distinguished from one that was never
attributed — without it, `ON DELETE SET NULL` alone would make those two cases
indistinguishable. It must be `BEFORE`, not `AFTER`: the referential `SET NULL`
is an immediate FK action that runs before any `AFTER` trigger, so the `CHECK`
would reject the row first.

#### Explainability enforced by constraint

Two `CHECK` constraints make honesty structural rather than aspirational:
`ck_damage_detections_reasoning_required` means a finding cannot be written
without a human-readable reason, and
`ck_damage_detections_ml_requires_model_version` means nothing may claim
`method = 'ML_MODEL'` without naming a version. The seeded findings are all
`CLASSICAL_CV`, because that is what the pipeline actually is. See
[`AI-INTEGRITY.md`](AI-INTEGRITY.md).

## 6. Frontend architecture

| Concern | Implementation |
| --- | --- |
| Routing | React Router 7; a single `ROUTES` table in `routes/index.jsx` drives both the route tree and the sidebar, so they cannot disagree |
| Server state | TanStack Query — caching, retries, polling for live tracking |
| Client state | `context/AuthContext.jsx` for token + identity; Zustand reserved for client-only UI state in later phases |
| HTTP | One axios instance (`services/apiClient.js`); request interceptor injects the bearer token and a request id; response interceptor unwraps the envelope and, on 401, notifies listeners so `AuthProvider` signs the user out |
| URLs | Centralised in `services/endpoints.js` |
| Environment | Read only in `config/appConfig.js`; only `VITE_*` variables reach the browser |
| Styling | Plain CSS with custom properties in `styles/global.css` — no CDN, no runtime font fetch, so the UI renders offline |
| Maps | Leaflet via `react-leaflet`, OpenStreetMap tiles (no key) |
| Tests | Vitest 4 + jsdom + Testing Library, run against a real DOM |

`services/` deliberately contains no React imports. If `apiClient` imported
`AuthContext`, the module graph would cycle (`context → services → context`).
The token is read through the plain `utils/tokenStorage.js` helper instead, and
sign-out is pushed to subscribers via `onUnauthorized(cb)`.

The dev proxy means the browser makes **same-origin** requests. In production,
serve `frontend/dist` statically and reverse-proxy `/api` to Flask.

## 7. Vision pipeline (design for Phases 5–6)

```
upload → validate (extension + signature + size + pixel bounds)
       → normalise (EXIF orientation, resize to a canonical width)
       → align (ORB/SIFT feature match + homography where the pair permits it)
       → differencing (LAB colour difference, gradient/edge comparison)
       → region analysis (contours → bounding boxes → mapped to body zones)
       → evidence (annotated before/after/side-by-side image + numeric scores)
       → persist (vehicle_inspections, damage_detections)
```

Every detection is stored with the parameters that produced it and a rendered
evidence image, so a reviewer can see *why* a region was flagged. See
[`AI-INTEGRITY.md`](AI-INTEGRITY.md).

## 7a. Geofence and telemetry pipeline (Phase 7)

```
POST /api/gps/events  → validate (coords, speed, timestamp, clock skew)
                      → resolve zone (explicit override → session's zone → none)
                      → gather the preceding fixes (movement-pattern rules)
                      → evaluate (ai/anomaly/geofence.py: pure maths, no Flask)
                      → persist (gps_events, with the verdict AND its inputs)
                      → raise alerts (throttled per type) + audit
```

The layering mirrors the vision pipeline: `ai/anomaly/geofence.py` imports
neither Flask nor SQLAlchemy, so every rule is unit-testable without a
database; `services/geofence.py` owns the workflow; the routes only translate
HTTP into service calls. Because `ai.*` must stay ignorant of `models`, the
engine restates the status and alert vocabularies as plain strings -
`tests/test_geofence_engine.py` holds the drift guards that keep those mirrors
honest.

Three decisions are worth stating explicitly.

**The verdict is stored, with the inputs beside it.** `gps_events` is
append-only and high-volume, so reclassifying on every read would make the
dashboard's "show me the fixes that needed attention" a full table scan.
Storing it is safe *because* `zone_id`, `is_within_zone`,
`distance_from_zone_metres` and `speed_kmph` stay on the same row: the verdict
can always be re-derived and compared rather than trusted. The
`ck_gps_events_zone_fields_consistent` constraint keeps the columns mutually
honest - without a zone to evaluate against, a fix cannot claim to be inside
anything and has no boundary to measure from.

**Alerts are throttled, observations are not.** A vehicle driving away produces
many consecutive out-of-zone fixes. A finding becomes an alert only when no
open alert of the same type exists for that session within
`ALERT_COOLDOWN`; the rest are recorded on their fixes and reported as
`alerts_suppressed`. Nothing is hidden - only the notification is rate-limited.

**There is no `POST /api/alerts`.** Alerts are created by the engine, never by
a client. A security finding is a machine observation that a human then
judges, so letting a caller write one directly would put unverified claims into
a table whose whole purpose is to be trusted.

Alert triage is restricted to `VALET_MANAGER` and `ADMIN` - the same deliberate
narrowness as damage verification. The person who drove the vehicle is not the
one who dismisses the alert about it.

```
browser ── TrackingPage ──→ GET /api/gps/{custody_id}   (one call)
        ├─ TrackingMap (Leaflet): polygon from the stored ring, tolerance
        │                        band, track polyline, status-coloured markers
        ├─ current reading, with per-fix provenance
        ├─ SimulatedBanner, driven by the response's own provenance flags
        └─ AlertCard list, triage controls gated by role
```

The map draws the **stored polygon**, never a circle recomputed from
`radius_metres`: the boundary the engine evaluated against is the ring, and a
map that disagreed with the verdict would be the one thing a security view must
never be. Markers are CSS `divIcon`s rather than Leaflet's stock PNG icons,
whose asset paths bundlers routinely break.

## 7b. Prototype ML classification layer (Phase 9)

A learned model was added over the rule engine, and the architecture question
was not "can it classify" but "what is it allowed to change". The answer is
encoded structurally rather than as a comment.

```
        GPS fix arrives
              │
              ▼
   ai/anomaly/geofence.evaluate_fix()      ← measurement against a known boundary
              │  MovementVerdict (status, findings, evidence)
              ▼
   ai/ml/features.extract_features()       ← reuses the verdict's own distance
              │  8 features                  and containment, never recomputes them
              ▼
   services/security_ml.SecurityModelService.classify()
              │  MlOpinion (label, probability, severity, reason, version)
              ▼
   services/security_ml.combine()          ← THE GATE
              │
              │   rule is a FLOOR: may raise, may never lower
              │   BREACH + ML NORMAL → BREACH, breach finding preserved
              │   escalation only above ML_ESCALATION_THRESHOLD (0.70)
              │   ML can never produce BREACH — it measured no boundary
              ▼
   CombinedVerdict → GpsEvent
              │
              ├─ movement_status          ← what the rules measured
              └─ ml_classification /
                 ml_probability /
                 ml_model_version         ← what the prototype estimated
```

**Why the two verdicts are separate columns.** If the ML result overwrote
`movement_status`, an escalation would be indistinguishable from a rule finding
after the fact, and the measurement would no longer be auditable. Migration
`0006` stores them side by side with CHECK constraints that make the three ML
columns stand or fall together — a verdict without the probability that produced
it, or without the model version, is a number nobody can interpret.

**Why features come from the verdict.** `features_from_verdict` reads
`verdict.distance_from_boundary_metres` and `verdict.is_within_zone` rather than
recomputing them. If the classifier ran its own geometry, a disagreement between
the two layers could be an artefact of two different calculations rather than a
genuine difference of opinion, and the disagreement would mean nothing.

**Why it is fenced in rather than trusted.** The classifier is a prototype
trained on synthetic telemetry with hand-written labels. Escalating on a false
positive costs a reviewer a glance; de-escalating on a false negative costs the
vehicle. Those are not comparable errors, so only the cheap one is permitted.
The asymmetry is asserted exhaustively in `tests/test_security_ml.py`, and all
five deliberate mutations of the combination logic were caught.

**Why loading is lazy and failure is silent.** The artifact is read once and
cached. A missing, corrupt or version-mismatched artifact logs a warning and
returns the rule verdict untouched — a GPS fix that could not be stored because
a prototype model choked on it would be a far worse outcome than a fix stored
without an ML opinion. Rules-only is a supported deployment state, and the
tracking dashboard renders it as an ordinary state rather than an error.

**Where the explainability comes from.** For logistic regression the
contributions are exact: coefficient × scaled value, which sum with the
intercept to the log-odds. For a random forest they are approximations from the
global importances, and the reason string says so rather than implying a
precision it does not have.

## 7c. Retrieval-time prediction (Phase 9b)

```
                    offline, once                    live, per request
  ┌────────────────────────────────────┐   ┌──────────────────────────────────┐
  │ generate_retrieval_history.py      │   │ POST /api/retrieval/predict      │
  │   ai/retrieval/dataset.py          │   │   services/retrieval.py          │
  │   4000 synthetic labelled rows     │   │                                  │
  │   split by TIME, last 25% held out │   │   1. count the live queue        │
  │              ↓                     │   │      (RETRIEVAL_REQUESTED /      │
  │ train_retrieval_model.py           │   │       _IN_PROGRESS, ordered by   │
  │   ridge regression, alpha=1.0      │   │       requested_at)              │
  │              ↓                     │   │   2. count valets on duty        │
  │ storage/models/retrieval_model.json│──▶│   3. read zone capacity/occupancy│
  │   plain JSON, no pickle            │   │   4. mean ACTUAL time for the    │
  │              ↓                     │   │      zone from completed rows    │
  │ evaluate_retrieval_model.py        │   │   5. model.predict(features)     │
  │   MAE vs median/mean/naive-rule    │   │   6. counterfactual: queue of 1  │
  │   exits 1 if it loses              │   │      → dispatch delay            │
  └────────────────────────────────────┘   │   7. store + audit + commit      │
                                           └──────────────────────────────────┘
```

**The measured/estimated seam is the design.** Steps 1–4 read real state from
this database: how many retrievals are queued, in what order, how many operator
accounts are active, how full the zone is, whether the vehicle is flagged, and
the mean *actual* retrieval time recorded for that zone. Step 5 is the only
estimated part. Keeping the two apart is what makes the honest half visibly
honest.

Note step 4 deliberately averages `actual_ready_at - requested_at` over rows
that were *fulfilled*. Averaging the predicted value instead would feed the
model's own output back in as an input — a feedback loop that would look
accurate while measuring nothing.

**The dispatch recommendation is derived, not predicted.** The model outputs
minutes, nothing else. The service scores the same features a second time with
the queue forced to one, and the difference is the portion of the wait caused by
waiting one's turn:

```
  estimated_minutes  13   ── total wait
  fetch_minutes       9   ── the counterfactual (queue of one)
  dispatch_delay      4   ── 13 − 9, so dispatch at now + 4 min
```

It is a decomposition of one linear prediction. It is not a second opinion and
carries no independent information.

**Why ridge and not something stronger.** The coefficients are the explanation
shown to users, so they must be stable between fits; with correlated features
(queue size and queue position move together) OLS coefficients swing enough that
the reason string would change for no real reason. A gradient-boosted model
would score better on synthetic data and explain nothing, and an estimate staff
cannot interrogate will not survive first contact with a real queue.

**Why MAE, and why it is never shown alone.** Mean squared error is dominated by
the rare long retrieval, which is not how a customer experiences an estimate.
But any MAE is meaningless without a comparison, so the evaluator always prints
the median predictor, the mean predictor and a hand-written queue rule beside
it, and exits 1 if the model does not win. On the default seed: 1.725 min
against a 2.691 min best baseline.

**The artifact is plain JSON** — coefficients, intercept, scaler mean and scale,
metrics. Loading it cannot execute anything, and it can be read and diffed by
hand. `RetrievalModel.load` refuses an artifact whose feature order differs from
the code, because applying the right coefficients to the wrong columns produces
a confident, meaningless number.

**Two bugs this layer produced, worth recording.** Both were found by checking
the live behaviour rather than the tests:

- *Single-row inference skipped the scaler.* `predict_minutes` applied the
  coefficients to raw features while the batch path scaled them, so a live
  request returned 67 minutes where the correct answer was 6.3 — and every
  batch-scored metric in the evaluator stayed correct, because the evaluator
  only ever scored batches. Fixed by collapsing to one inference path.
- *The service flushed without committing.* The endpoint returned `201` with a
  `prediction_id` while the row was discarded at the end of the request. The
  pytest suite could not see this, because it shares a connection with the
  request and an uncommitted row is visible on the same connection. Only a live
  HTTP walk and a second database connection revealed it; both now assert it.

**Honesty.** Every response carries `trained_on_synthetic_data: true`, a
`data_basis` naming the generator, and a disclaimer. The stored row keeps an
`inputs_snapshot` of everything the model was told. See
[`AI-INTEGRITY.md`](AI-INTEGRITY.md) §5.

## 7d. The customer dashboard (Phase 10)

Three endpoints carry the whole feature:

| Endpoint | Actor | Purpose |
| --- | --- | --- |
| `GET /api/v1/customers/me/dashboard` | customer | the composed snapshot behind all five cards |
| `POST /api/v1/retrieval/request` | customer | request the car; returns the fresh estimate |
| `GET /api/v1/retrieval/queue` | staff | the pending-retrieval queue the valet polls |

### Why the server composes the snapshot

`build_dashboard(actor)` in `services/customer_dashboard.py` runs the queries
for custody, vehicle, zone, location, security, condition, retrieval and
actions and returns one object. The alternative — ten endpoints the browser
calls in parallel — is rejected for a concrete reason: ten reads happen at ten
moments, so the page can show a custody status captured before a valet moved
the car next to a queue position captured after. A single snapshot cannot
disagree with itself. It also means authorization is applied once, in one
place, instead of being re-derived by whichever card happens to render.

Authorization is **reused, never reimplemented**: the security, condition and
location sections delegate to `geofence_service.list_alerts`,
`inspections_service.get_inspection_report` and `tracking_service.tracking_view`,
each of which already scopes its results to the actor. Ownership violations
therefore surface as `404` exactly as they do on the individual endpoints.

### Every section reports its own state

Each section carries `state ∈ {ok, empty, unavailable}` plus a `reason`:

- **`empty`** is a real answer from the database. A customer with no vehicle in
  custody gets `200` and `has_active_custody: false`. A dashboard that rendered
  five blank cards instead would be indistinguishable from one that had failed.
- **`unavailable`** means a sub-query raised. Each section is wrapped so a
  broken dependency degrades one card and logs why, rather than turning the
  whole dashboard into a `500`.

Nothing in the response is static. `actions.can_request` is computed from the
same `ALLOWED_CUSTODY_TRANSITIONS` table the custody state machine enforces, so
the UI disables the button for precisely the reason the API would return `409`
and can print that reason.

### The retrieval request

`request_vehicle(actor, custody_session_id)` performs, in order:

1. the status change through `update_custody_status` — reusing the tested state
   machine and its audit trail rather than writing a parallel one;
2. a `RETRIEVAL_REQUESTED` audit row;
3. a fresh prediction, generated *after* the status change so that this request
   is already counted in its own queue position.

An untrained predictor does not block the request. `ServiceUnavailableError` is
caught and returned as `estimate_available: false`, because the customer did
ask and a valet was told — a missing model artifact is not a failed request.

**A permission rule was narrowed here deliberately.** `update_custody_status`
previously required a valet role unconditionally, which meant a customer could
not request their own car — the one action the feature exists for. The guard is
now per-target: a customer may move **their own** session to
`RETRIEVAL_REQUESTED` and nothing else, and every other transition stays
staff-only. A shared state-machine function reused by a new actor type has to
have its permissions re-examined per transition rather than inherited wholesale.

### Which prediction is "current"

`retrieval_service.prediction_recency_order()` is the single ordering used by
both the customer's card and the valet's queue, and it exists because they once
disagreed. Ordering by `requested_at` descending is wrong for two reasons:

- `requested_at` is a **modelling input** — when the customer asked inside the
  timeline being modelled. The demo seed writes future-dated rows, so ordering
  "latest" by it is ordering by fiction.
- Two rows can share it **exactly**. That happened, `limit(1)` then returned
  whichever row PostgreSQL reached first, and the valet was shown a stale
  14-minute estimate while the customer's own card showed the fresh 9-minute
  one.

The ordering is therefore: PENDING first (a superseded, dispatched or fulfilled
row is history), then `created_at` descending (write order, which cannot be
backdated), then `id` descending so a tie can never yield different answers on
two reads. Three regression tests in `tests/test_customer_dashboard.py` pin
this, including the tie.

### The valet queue polls

`GET /api/v1/retrieval/queue` is a plain query, staff-only, ordered oldest
first. It returns `delivery: "polled"` and a `delivery_note` explaining that
there is no websocket channel, and the UI prints it. A 5-second poll that
claims to be real-time would overstate the system in exactly the way the rest
of this codebase avoids.

## 7e. The valet operator dashboard (Phase 10)

`GET /api/v1/staff/dashboard` composes seven sections in one snapshot:
`incoming`, `active_vehicles`, `pending_pickups`, `retrieval_queue`,
`security_alerts`, `inspections`, `zones`. Staff-only — a customer gets `403`,
not an empty `200`, because an empty dashboard reads as "you have no vehicles"
rather than "you may not look".

### Why this one matters more than the customer dashboard

The same composition argument applies, with more force: **an operator acts on
this screen.** If the vehicle list and the alert list were fetched separately,
a car could read calm in one and breaching in the other, and the operator would
act on whichever they happened to read last. One snapshot cannot disagree with
itself.

### Three distinctions the data model has to make

**"Incoming" means a ticket exists and custody has not started.** There is no
separate expected-arrival record in this schema, so inventing one would have
meant showing vehicles that were never coming.

**The retrieval queue and pending pickups are different jobs.** The queue is
`RETRIEVAL_REQUESTED` / `RETRIEVAL_IN_PROGRESS` — work a valet still has to do.
Pending pickups is `READY_FOR_PICKUP` — work already done, waiting on the
customer. Collapsing them would mean an operator either fetches a car twice or
leaves one standing.

**`display_status` is derived, not stored.** One of `IN_CUSTODY`, `MOVING`,
`READY`, `ALERT`, `COMPLETED`. `ALERT` is not a custody state; it is a security
override applied on top of whichever state the car is in. A badge reading
`IN_CUSTODY` with a small warning icon beside it would be scanned as fine,
which is the wrong failure mode for a security screen. Labels ship with the
payload so the UI cannot keep a second copy of the vocabulary and drift.

### Capabilities are published, not inferred

The payload carries a `capabilities` block derived from the same role tuples
the endpoints enforce. A valet operator may move a car and photograph it, but
acknowledging or resolving an alert is a manager act (`ALERT_TRIAGE_ROLES`),
and verifying a damage finding is deliberately narrower than capturing one
(`VERIFICATION_ROLES` vs `INSPECTION_ROLES`).

Importing those tuples rather than restating them means a future narrowing of a
role shows up in the payload automatically instead of leaving a button that
403s. A button that refuses is worse than no button: it tells an operator the
action is available and then contradicts itself in front of a customer.
`tests/test_staff_dashboard.py` asserts the flags match the endpoints by
actually calling them.

### The dashboard writes nothing

Every operator action calls the endpoint that already exists for it. One state
machine, one audit path. `CHECKED_OUT` in particular is not reachable by a
status write: completion has to settle the ticket, free the zone slot and
update the vehicle, so it is refused with a `422` that names the ticket
endpoint rather than a bare `409` — telling the caller where to go beats
telling them "not allowed".

The audit trail is therefore inherited rather than reimplemented, and the tests
assert the row behind each action: `STATUS_CHANGE`, `CHECK_OUT`,
`ALERT_ACKNOWLEDGED`, `ALERT_RESOLVED`.

### A bug this module exposed

Writing the alerts section surfaced a defect in shipped code. `list_alerts`
returns its rows under the key `alerts`; three call sites read
`.get("items", [])`, which does not raise — it silently yields `[]`. The
customer dashboard's Security Status card had been reporting
`open_alert_count: 0` beside a real HIGH breach alert. The headline still read
`BREACH`, but only because the movement verdict happened to be `BREACH` too;
the alert list and the count were simply wrong.

Both reads are now indexed rather than defaulted. A wrong key now raises
instead of reporting "no alerts", because a security count that is wrong in the
reassuring direction is the worst kind of wrong.

## 7f. Manager analytics (Phase 10a)

`GET /api/v1/manager/dashboard` composes eight metrics, five chart series, four
tables and the filter vocabulary in one snapshot, filtered server-side.
`GET /api/v1/manager/report` returns the same figures as CSV or JSON.

### Who may read it

`VALET_MANAGER` and `ADMIN`. This was tightened during the security audit: the
gate originally tested `is_staff()`, which admits `VALET_OPERATOR`, so an
operator could read cross-customer analytics and download the report.

`is_staff()` is the right predicate for "may this person act on a vehicle that
is not theirs", which is most of the operator's job. It is the wrong predicate
for "may this person see the aggregate picture of the whole operation" — that
is an oversight function, and the capability matrix has said so all along by
granting `report:read_all` to manager and admin only. The React router also
restricts `/manager` and `/reports` to those two roles, but hiding a nav link
is presentation; the API is the boundary that holds.

`build_report` delegates to `build_dashboard`, so the one gate closes both the
screen and the download. See [`SECURITY-AUDIT.md`](SECURITY-AUDIT.md) §1.4.

### The rule: a chart must say what it is made of

The brief's constraint was "do not create meaningless charts". That is not a
matter of taste that can be settled by looking at the result — it is a rule
with a testable shape, so it is encoded rather than promised:

- every series carries `n` (records summarised), `min_points` (how few is too
  few) and `sufficient`;
- the backend computes `sufficient`, so the threshold lives in one place;
- `ChartFrame` on the client refuses to draw below it and says what is needed;
- a test asserts every chart in a payload declares all three.

A two-point line is a coincidence with axes. The endpoint would rather return
`"Not enough data to chart"` than imply a finding.

### What is charted, and why in that form

Two decisions follow from the data rather than from convention:

- **Alerts and damage are bucketed weekly.** Both are sparse; a daily series
  would be mostly zeros with occasional spikes, which reads as a pattern where
  there are only a handful of events.
- **Custody duration is a histogram, not a trend.** The question is how long we
  hold cars, and the operationally interesting answer is the long tail that
  occupies bays — which an average hides completely.

Days with no arrivals are returned as zero rather than omitted, so a line
cannot join two distant points and invent a slope between them. Retrieval times
report `records` separately from `n`, because a series can have many daily
points each resting on one or two cars.

### Point-in-time versus windowed

Each metric carries `windowed`. Cars in custody and alerts currently open are
*not* scoped by the date filter — "how many cars do we have right now" has no
date range. Retrieval times, custody durations and damage counts are. Nothing
on the card row distinguishes them visually, so the payload has to; otherwise a
monthly total gets read as today's count.

### One code path for screen and report

`build_report` calls `build_dashboard`. A report that disagreed with the screen
it summarised would be worse than no report, because the report is the artefact
someone files and the screen is not. Exports are audited with `EXPORT`, and the
CSV carries an `X-Report-Contains-Generated-History` header because a
spreadsheet cannot display the banner the page shows.

### Provenance of the history itself

The dashboard reports whether its figures include rows written by
`generate_operational_history.py`. That script exists because a freshly seeded
database holds two tickets and one alert, which is not enough to chart; it
writes through the application's own models with a weekday demand curve,
rush-hour retrieval pressure and a realistic verification split. Every row is
marked, the run is recorded in `schema_meta`, and the payload says so — a
reviewer seeing ninety days of history is entitled to know whether it records
real operations.

## 7g. Centralized alerts and the notification abstraction (Phase 11)

`GET /api/v1/feed` serves one alert stream to every role, scoped in SQL. Five
categories span three domains: geofence events, damage findings, and retrieval
readiness.

### Layered on `security_alerts`, not merged into it

The existing table is the geofence engine's own record — GPS fix, zone, vision
evidence — written in exactly one place. Merging three domains into it would
have meant nullable `inspection_id` and `prediction_id` columns, turning a
geofence record into a grab-bag. So `alerts` is the feed users read and
`security_alerts` stays authoritative for what the detector saw, with
`source_entity_type`/`source_id` linking back.

The five categories fold the detector's seven types together. That is lossy, so
`source_type` keeps the original and `category_for()` raises rather than
defaulting on an unmapped type — a new detector type silently becoming
"suspicious movement" would mislabel security events.

### Read state is per recipient

The requirement "mark as read" sounds like a status value. It is not: a single
`READ` flag would mean whoever opened the app first cleared the alert for
everyone, so a manager could miss a CRITICAL breach because a customer glanced
at their phone. `alerts.status` therefore tracks only the lifecycle, and
`alert_reads` holds one row per (alert, user).

`read-all` is scoped by the same predicate as the list, so it cannot clear
alerts the actor was never shown. Fetching by id applies the same predicate and
returns 404 rather than 403 — confirming a hidden alert exists would leak an
incident about someone else's car.

### Publishers fire where the fact is established

Geofence, inspection, and custody-transition each raise into the feed at the
moment the event happens, rather than a poller guessing what it had already
seen. Each publisher is best-effort: the domain record is authoritative and
already committed, so a notification failure must not roll it back.

Potential damage is published for UNVERIFIED findings only, addressed to
managers, and worded as "potential" all the way to the UI.

### The notification abstraction

`services/notifications/` separates two decisions that are easy to tangle:

1. **Who should hear about this?** Policy, answered from the alert's
   `audiences`. Knows nothing about transports.
2. **How do we reach them?** Transport, answered by iterating registered
   channels. Knows nothing about alerts.

Business logic calls `dispatch(alert)` and nothing else. `EMAIL`, `SMS` and
`PUSH` are complete `BaseChannel` implementations with no provider and
`enabled = False`; wiring one means implementing `send` and flipping the flag.

Two details worth stating because they are easy to get wrong:

- **Stubs refuse rather than pretend.** A stub returning success would write
  `DELIVERED` rows for messages nobody received, which is worse than no email —
  it turns "we could not reach you" into a lie on the ledger.
- **Delivery is a value, not an exception.** `send` returns a `DeliveryResult`,
  so a broken push cannot roll back the alert that caused it. The alert is the
  fact; the notification is an attempt to surface it.

Managers are always recipients of a staff alert even when the publisher named
only operators. Otherwise every publisher has to remember the manager role, and
the first one that forgets silently hides incidents from the person accountable
for them.

## 7h. The audit trail (Phase 10b)

The other modules in this document describe things the platform *does*. This one
describes the record of it having been done — which is the artefact an examiner,
a dispute, or a court would actually ask for.

### Evidence has to be unchangeable, and saying so is not enough

An `audit_logs` table that a developer can `UPDATE` is a log with an eraser. The
guard therefore lives in the database rather than in the application, because
the application is the thing being audited.

The naive version of that guard is wrong, and the wrongness is worth recording
because it is easy to reintroduce. A blanket `BEFORE UPDATE` trigger blocks two
writes the design depends on:

* PostgreSQL implements `ON DELETE SET NULL` **as an UPDATE**, so a trigger that
  refuses all updates also refuses `actor_id` and `custody_session_id` being
  released when their target row is deleted;
* the `0002` tombstone trigger sets `audit_logs.actor_deleted_at` when a user is
  deleted, which is how the trail outlives the account it describes.

Blocking those breaks account deletion and its cascade tests. The correct rule
is to guard **rewriting history**, not the SQL verb. One function backs two
triggers:

* `trg_audit_logs_append_only` (`BEFORE UPDATE … FOR EACH ROW`) compares `OLD`
  and `NEW` with `IS NOT DISTINCT FROM` and permits only `actor_deleted_at`
  NULL→set, `actor_id`→NULL, `custody_session_id`→NULL. Everything else raises.
  A tombstone cannot be cleared — that transition is one-way.
* `trg_audit_logs_no_delete` (`BEFORE DELETE OR TRUNCATE … FOR EACH STATEMENT`)
  refuses outright.

The route layer adds a second, weaker lock: no `POST`/`PATCH`/`PUT`/`DELETE` is
registered under `/audit`. Two locks is not belt-and-braces here — the first
means the UI cannot offer a control for an operation with no route, and the
second means a caller who reaches the database by another path still cannot
rewrite it.

### Timestamps cannot order an append-only log

A single request routinely writes several rows in one transaction. A check-in
writes `VEHICLE_CHECKED_IN` **and** `CUSTODY_STARTED`; they receive the same
`occurred_at`. Ordering by `(occurred_at, id)` then falls back to a random UUID,
which rendered a real timeline as *"custody started"* before *"vehicle checked
in"* — a narrative that reads backwards, produced by a sort that looks
deterministic.

Migration `0008` adds `seq BIGINT GENERATED BY DEFAULT AS IDENTITY`, and every
read orders by it. Insertion order is the only ordering an append-only log can
promise, so it is the one the schema should carry.

### The vocabulary is a decision, not a naming exercise

`AuditAction` distinguishes fourteen **lifecycle events** from fifteen
supporting ones. The lifecycle set is what a custody timeline is checked
against; the supporting set (`EXPORT`, `ALERT_ACKNOWLEDGED`, `LOGIN_FAILED`)
matters but is not a custody stage.

Eight names were rewritten to describe *what happened to the vehicle* rather
than *which endpoint was called* — `CHECK_OUT`→`VEHICLE_PICKED_UP`,
`RETRIEVAL_REQUESTED`→`PICKUP_REQUESTED`, and so on. The distinction that forced
it: `RETRIEVAL_REQUESTED` survives as a **`CustodyStatus`**, because a state and
an event are different things. The old names used one word for both, so a reader
could not tell whether a row described a transition or a position.

`0008` rewrites existing rows to the new names, and the downgrade reverses the
mapping explicitly — including six actions that fold down onto a shared old name.

### The timeline reaches past the custody row

Three groups, each labelled with a `scope` so the reader can tell an event *of*
the custody from context *about* it:

* **`custody`** — rows stamped with the session's id.
* **`provenance`** — `TICKET_CREATED` and `VEHICLE_CREATED` cannot carry a
  `custody_session_id`, because no session exists yet when they are written.
  They are reached through the ticket and vehicle instead. Without them the
  timeline would begin at check-in and could not answer "when was this car
  registered?".
* **`login`** — `USER_LOGIN` belongs to a person, so it is scoped twice: the
  most recent login at or before the custody opened, plus any login by a
  participant during it. An unbounded login list would bury the custody under
  every unrelated session those people ever opened.

### Gaps are reported, not implied

The timeline returns `lifecycle_progress` — all fourteen, boolean each — and
`lifecycle_missing`. A custody with no pickup photographs says so explicitly.

This is the same principle as the manager dashboard's `sufficient` flag and the
classifier's "Potential Damage" label: the system states what it does not know
in the same voice as what it does. A timeline that rendered only what happened
would let a missing step pass for a step that was never required.

### Read-side restrictions

`VALET_MANAGER` and `ADMIN` only. The log holds email addresses, IP addresses
and the internal state of other people's vehicles. An operator has no oversight
role and a customer must never see another customer's custody, so both get
`403` — which is a true statement about the caller's role, unlike the existence
of somebody else's session, which stays `404`.

The response carries `mutable: false` and a `mutability_note`, so the viewer
states its own immutability from the API rather than from a hardcoded sentence
that could drift.

## 8. Deployment shape

```
            ┌──────────────┐
            │  Nginx / CDN │  static: frontend/dist
            └──────┬───────┘
                   │ /api → 127.0.0.1:5000
            ┌──────▼───────┐        ┌────────────┐
            │ waitress /   │───────►│ PostgreSQL │
            │ gunicorn     │        └────────────┘
            │  (Flask app) │        ┌────────────┐
            └──────────────┘───────►│  storage/  │  vehicle photos
                                    └────────────┘
```

Windows: `waitress`. Linux: `gunicorn`. Both are declared in
`requirements.txt` behind platform markers, so a single `pip install` works
everywhere.

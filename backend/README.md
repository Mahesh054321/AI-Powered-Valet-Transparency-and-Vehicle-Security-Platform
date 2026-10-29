# Backend — Flask REST API

Flask 3 application factory, SQLAlchemy 2.0 + PostgreSQL, Alembic migrations,
JWT authentication with role-based authorization, and a uniform JSON response
envelope.

Full installation steps live in the [root README](../README.md). This file is
the working reference for the backend itself.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-dev.txt

Copy-Item .env.example .env                # then set POSTGRES_PASSWORD and JWT_SECRET_KEY
python utils/scripts/init_db.py --seed
python utils/scripts/verify_setup.py       # 7-step pre-flight check
python utils/scripts/run_api.py            # http://127.0.0.1:5000
```

The working directory matters: the CLI scripts live in `utils/scripts/`, so all
commands are run from `backend/`.

## Layout

```
backend/
├── app/                  HTTP composition: factory, extensions, error handlers, CORS, logging
│   ├── __init__.py       create_app() — the only place the pieces are wired together
│   ├── extensions.py     Flask-SQLAlchemy instance
│   ├── error_handlers.py maps exceptions → the JSON envelope
│   ├── cors_config.py    origin allow-list from the environment
│   └── logging_config.py human or JSON logs
│
├── config/               Configuration — the only code that reads the environment
│   ├── environment.py    typed env readers (env_str/env_bool/env_int/env_float/env_list)
│   └── settings.py       Settings + Development/Testing/Production; build_database_url()
│
├── routes/               HTTP surface
│   ├── __init__.py       register_routes() — mounts everything onto the app
│   ├── health.py         GET /api/health, GET /api/readyz
│   └── v1_registry.py    fresh Blueprint for /api/<API_VERSION> per app
│
├── services/             Domain logic — one concern per module (health.py today)
├── database/             Session management, schema assertions, seeding
│   ├── __init__.py       get_session(), ping()
│   ├── checks.py         schema assertions used by verify_setup.py and tests
│   └── seed.py           reference-data seeding
│
├── models/               SQLAlchemy models
│   └── base.py           declarative Base, UUIDPrimaryKeyMixin, TimestampMixin
│
├── schemas/              Response contracts
│   └── common.py         MetaSchema, ErrorSchema, SuccessEnvelope, ListEnvelope
│
├── middleware/           Cross-cutting request handling (request id, security headers)
├── utils/                Framework-agnostic primitives — no Flask imports
│   ├── security.py       hashing, JWT, roles, RBAC decorators, redaction
│   ├── validation.py     field validators, plate parsing, upload sniffing
│   ├── responses.py      ok()/created()/error envelope builders
│   ├── exceptions.py     typed domain errors
│   ├── pagination.py     limit/offset helpers
│   └── scripts/          developer CLI (init_db, run_api, verify_setup, show_config)
│
├── ai/                   AI modules (Phase 3+); roadmap in the module docstring
├── migrations/           Alembic revisions — the only thing that changes the schema
│   └── versions/
│       ├── 0001_baseline.py     extensions + schema_meta anchor
│       └── 0002_core_domain.py  the 11 domain tables, indexes, CHECKs
├── storage/media/        Uploaded photos (git-ignored)
├── tests/                pytest suite
├── wsgi.py               WSGI entry point
└── alembic.ini           no DSN; migrations/env.py injects it from config
```

### Import direction

This is the rule that keeps the package acyclic:

```
routes  →  services  →  database / models
utils and config are importable from anywhere
utils imports nothing from routes or services
```

`utils/` deliberately contains no Flask imports, so `security.py` and
`validation.py` can be unit-tested without building an app. A service never
imports from `routes/`.

## Scripts

Run from `backend/`. Equivalent root-level `npm run …` aliases exist — see the
root README.

| Command | Purpose |
| --- | --- |
| `python utils/scripts/verify_setup.py` | Python version, dependencies, `.env`, config, DB connectivity, migration state, app boot |
| `python utils/scripts/init_db.py` | Apply migrations and print a schema report |
| `python utils/scripts/init_db.py --reset --seed` | Drop all tables, rebuild, seed reference data |
| `python utils/scripts/init_db.py --check-only` | Verify the schema, change nothing |
| `python utils/scripts/run_api.py` | Flask dev server on `:5000` |
| `python utils/scripts/run_api.py --production` | `waitress` (Windows) / `gunicorn` (Linux) |
| `python utils/scripts/show_config.py` | Resolved configuration with secrets redacted |
| `python utils/scripts/train_security_model.py` | Train the prototype security classifier on synthetic telemetry and print its metrics |
| `python utils/scripts/train_security_model.py --algorithm random_forest` | Same, using a random forest instead of logistic regression |
| `python utils/scripts/evaluate_security_model.py` | Re-score a saved artifact, with accuracy/precision/recall/F1 and the confusion matrix |
| `python utils/scripts/evaluate_security_model.py --fresh-seed 999` | Also score against a newly generated dataset the model never saw |

## Endpoints implemented so far

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/` | public | Service banner |
| `GET` | `/api/health` | public | Liveness and status snapshot — always answers, even when a dependency is down |
| `GET` | `/api/readyz` | public | Readiness — executes `SELECT 1`, reports PostgreSQL version and latency; `503` when a dependency is unreachable |
| `GET` | `/media/<path>` | public | A stored image (upload or QR render). Unguessable UUID filenames are the access control; see the note below |

Health endpoints are **unversioned on purpose**: a load balancer or an
evaluator should be able to call them without knowing the API version, and they
must keep working while `/api/v1/...` is still being built out.

### Authentication and authorisation

Every auth endpoint is mounted twice — at `/api/auth/*` and at
`/api/v1/auth/*` — so it works before and after a caller adopts versioning.

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/auth/register` | public | Create a **customer** account. `201`, or `409` if the email is taken |
| `POST` | `/auth/login` | public | Exchange credentials for an access + refresh token pair |
| `POST` | `/auth/logout` | any signed-in role | Revoke the presented token server-side |
| `GET` | `/auth/me` | any signed-in role | The account as the **database** records it |
| `POST` | `/auth/users` | `ADMIN` | The only path that can create staff accounts |

**A public visitor can never obtain a staff role.** `register` hard-codes
`Role.CUSTOMER` and takes no `role` parameter; sending `"role": "ADMIN"` is
rejected with `403` rather than silently downgraded, so the caller learns the
rule instead of getting a surprise. `POST /auth/users` is decorated with
`@require_role(Role.ADMIN)` and is the sole way a `VALET_OPERATOR`,
`VALET_MANAGER` or `ADMIN` comes into existence.

**Passwords are never stored or returned as plain text.** They are hashed with
Werkzeug's PBKDF2-SHA256 at 600 000 iterations
(`pbkdf2:sha256:600000$<salt>$<hash>`) and verified in constant time. `public_profile()`
is the only serialiser used by auth routes, and it has no `password_hash`
field, so no code path can leak one by accident.

**Login does not leak which accounts exist.** An unknown email and a wrong
password return the identical `401` body, and the unknown-email path still runs
a hash comparison against `_DECOY_PASSWORD_HASH` so both cost the same time.
Suspended and deactivated accounts are refused with a message that does not
confirm the password was right.

**Logout really logs out.** Discarding a token client-side leaves it valid
until its own expiry, so `logout` writes the token's `jti` into the
`revoked_tokens` denylist and `load_current_user()` checks it on every request.
Signing out twice returns `401`, not a unique-constraint violation.

### Vehicles, tickets and custody

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/vehicles` | auth | The caller's vehicles; staff see all. Paginated, searchable |
| `POST` | `/vehicles` | auth | Register a vehicle. Staff may name an `owner_id` |
| `GET` / `PATCH` | `/vehicles/<id>` | auth | Read / edit. A customer cannot reach another's vehicle |
| `DELETE` | `/vehicles/<id>` | auth | **Archive**, never a hard delete. `204` |
| `GET` | `/vehicles/<id>/sessions` | auth | Previous valet sessions, each paired with its ticket |
| `POST` | `/tickets` | valet | Issue a ticket: allocates the number, renders the QR |
| `GET` | `/tickets` · `/tickets/<id>` | auth | List (filter on projected status) / the digital ticket |
| `POST` | `/tickets/<id>/check-in` | valet | Assign a zone and open the custody session |
| `POST` | `/tickets/<id>/complete` | valet | Return the vehicle, free the slot, close the ticket |
| `POST` | `/tickets/<id>/cancel` | valet | Void a ticket |
| `GET` / `PATCH` | `/custody-sessions/<id>` | auth / valet | Read the status / move it to a new state |
| `GET` | `/customers` | valet | The customer directory check-in searches |
| `GET` | `/parking-zones` | auth | Zones; `?parkable_only=true` hides unusable ones |
| `POST` | `/uploads/images` | auth | Store photographs, returning validated references |

**Ticket status is projected, not stored.** The database keeps the vocabulary
migration `0002` created; the API presents a coarser one derived from the
ticket's stored status plus its custody session's state:

| Stored (`valet_tickets.status`) | Custody session | API `status` |
| --- | --- | --- |
| `ISSUED` | — | `CREATED` |
| `ACTIVE` | `PARKED`, `IN_TRANSIT`, `RETRIEVAL_*`, `DISPUTED` | `IN_CUSTODY` |
| `ACTIVE` | `READY_FOR_PICKUP` | `READY_FOR_PICKUP` |
| `COMPLETED` | `CHECKED_OUT` | `COMPLETED` |
| `CANCELLED` | — | `CANCELLED` |
| `EXPIRED` | — | `EXPIRED` |

`project_ticket_status()` in `services/tickets.py` is a pure function and is
tested exhaustively. `READY_FOR_PICKUP` needs no new column because it is the
*session* that reaches that state while the ticket stays `ACTIVE`. `EXPIRED` is
passed through rather than hidden: reporting an expired ticket as `CREATED`
would tell a customer their car is waiting when the platform stopped tracking
it. Every response also carries `stored_status`, so the mapping is visible
rather than taken on trust.

**Stored images are served from `/media` unauthenticated.** A browser rendering
`<img src>` cannot send an `Authorization` header, so the access control is the
unguessability of the path: filenames are random UUID4 hex generated server-side
(122 bits), and directories are not listable. This is a real trade-off — anyone
holding a full URL can fetch that image — and the production answer is a
short-lived signed URL. Path traversal is refused twice: the reference is
re-validated, then the resolved path is confirmed to sit inside the storage root.

**Decorators.** `@require_authentication` insists on a valid, unrevoked access
token; `@require_role(...)` additionally insists on the role. Both answer in
the standard error envelope (`AUTHENTICATION_ERROR` / `AUTHORIZATION_ERROR`).
A refresh token presented as an access token is rejected on its `typ` claim,
and `alg: none` is rejected outright.

`GET /api/health` returns:

```json
{
  "success": true,
  "data": {
    "status": "up",
    "service": "valet-transparency-api",
    "api_version": "v1",
    "environment": "development",
    "uptime_seconds": 13.65,
    "server_time": "2026-09-07T09:15:15.594743+00:00",
    "checks": {
      "database": {
        "status": "up",
        "latency_ms": 17.96,
        "server_version": "17.11 (Debian 17.11-0+deb13u1)"
      }
    },
    "telemetry_source": "simulated",
    "database_healthy": true
  },
  "meta": { "request_id": "5be8c291fd0c4724a6b5920061dd2e4b" }
}
```

`telemetry_source` is `simulated` unless `TELEMETRY_SOURCE=hardware` is set, so
the UI can always tell the user whether GPS data came from real hardware.

Domain blueprints mount at `/api/<API_VERSION>` via `routes/v1_registry.py`.
The full catalogue arrives with each phase.

### Vehicle condition assessment

Mounted at both `/api/v1/inspections` and, as an unversioned alias,
`/api/inspections`. Both are served by the same handlers.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/inspections/dropoff` | valet staff | Store the drop-off photo set (multipart) |
| `POST` | `/inspections/pickup` | valet staff | Store pickup photos and compare them against drop-off |
| `GET` | `/inspections/<custody_id>` | owner or staff | Full condition report: both stages, findings, evidence |
| `PATCH` | `/inspections/detections/<id>/verification` | manager/admin | Confirm, reject, or reopen one finding |
| `GET` | `/inspections/demo/scenarios` | staff | The synthetic demo catalogue |
| `POST` | `/inspections/demo` | staff | Run the real pipeline over synthetic photographs |

`<custody_id>` is the **custody session** id, not an inspection id: one report
covers both stages of a single custody window.

Capture is multipart. Each photo carries an `angle`, sent either as a parallel
`angles` list or as one `angle` applied to all files; a count mismatch is
rejected rather than guessed, because silently applying one angle to a mixed set
would compare unrelated viewpoints and flag the whole vehicle. Angles are stored
in the platform's enum casing (`LEFT_SIDE`), like every other status.

**Pickup compares; drop-off does not.** A drop-off set is the baseline - there is
nothing to compare it against yet. Pickup triggers the comparison in the same
request, so the response already carries the candidate findings, the rendered
three-panel composite, and a per-finding evidence crop.

**Outcome follows verification, not detection.** This is deliberate and is the
difference between an audit trail and a rumour:

| Findings | `outcome` | `is_new_damage` |
| --- | --- | --- |
| none | `CLEAN` | false |
| some, none reviewed | `PENDING_REVIEW` | false |
| some, at least one confirmed | `DAMAGE_FOUND` | true |
| some, all reviewed, none confirmed | `CLEAN` | false |

Recording `DAMAGE_FOUND` the moment a heuristic fires would put an unverified
guess into a column that reads like a fact - and that column is what a billing
dispute would turn on. Every finding is stored `UNVERIFIED` with
`presentation_label: "Potential Damage"` until a manager or administrator acts.
Verification is deliberately narrower than capture: the person who photographed
the car is not the only one judging the algorithm's output.

The demo endpoint stores no inspection rows, so it can be run repeatedly without
polluting custody history. Its photographs are synthetic; its findings are not -
they are the same `ai.vision.compare_images` the production path calls. See
`docs/AI-INTEGRITY.md` for what the pipeline does and where it fails.

### Vehicle security and geofencing

Each parking zone carries a geofence. Every GPS fix is classified against the
zone it was evaluated with, and a security alert is raised when a fix warrants
one. All of these are also served unversioned at `/api/gps/*`, `/api/geofences`
and `/api/alerts/*`.

| Method | Path | Who | Purpose |
| ------ | ---- | --- | ------- |
| `POST` | `/v1/gps/events` | operator+ | Record one fix, or a batch under `{"fixes": [...]}` |
| `GET` | `/v1/gps/<custody_id>` | owner or staff | The tracking view: position, zone, geofence, status, alerts |
| `GET` | `/v1/gps/<custody_id>/fixes` | owner or staff | Paginated raw telemetry; `?status=BREACH` filters |
| `GET` | `/v1/gps/simulate/scenarios` | operator+ | What the simulator can generate |
| `POST` | `/v1/gps/simulate` | operator+ | Generate a track and ingest it as `SIMULATED` |
| `GET` | `/v1/geofences` | authenticated | Zones with the polygon the map needs |
| `POST` | `/v1/geofences` | manager/admin | Create a zone from `centre + radius` (or an explicit polygon) |
| `PATCH` | `/v1/geofences/<zone_id>` | manager/admin | Move or reconfigure a boundary |
| `GET` | `/v1/alerts` | authenticated | Alerts, newest first; staff see all, customers see their own |
| `POST` | `/v1/alerts/<id>/acknowledge` | manager/admin | Record that it was seen. Does not close it |
| `POST` | `/v1/alerts/<id>/resolve` | manager/admin | Close it; `{"dismiss": true}` records a false positive |

`POST /api/gps/events` accepts the brief's field names (`custody_id`, `speed`,
`timestamp`) alongside the schema's own (`custody_session_id`, `speed_kmph`,
`recorded_at`).

Movement status, from `ai/anomaly/geofence.py` — a rule engine, not a model:

| Status | Meaning |
| ------ | ------- |
| `NORMAL` | Inside the geofence, moving as expected |
| `WARNING` | At the edge of the boundary (inside the tolerance band), or above the yard's speed limit |
| `SUSPICIOUS` | Outside the tolerance band, or an implausible movement pattern |
| `BREACH` | Far outside, or leaving in a direction that increases the distance |

Every fix stores the verdict **and** the inputs that produced it — `zone_id`,
`is_within_zone`, `distance_from_zone_metres`, `speed_kmph` — so a reviewer can
re-derive it rather than trust it.

Three behaviours worth knowing:

- **Alerts are throttled per type.** A vehicle driving away produces many
  consecutive out-of-zone fixes; only the first raises an alert within
  `ALERT_COOLDOWN` (10 minutes). The rest are recorded on their fixes and
  returned as `alerts_suppressed`, so the observation is never lost — only the
  notification is rate-limited.
- **There is no `POST /alerts`.** Alerts are created by the engine, never by a
  client. Triage is restricted to managers and administrators, so the person who
  drove the vehicle is not the one dismissing the alert about it.
- **A fix more than five minutes ahead of the server clock is refused (422).**
  Acknowledgement must come after occurrence, so a future-dated fix would
  produce an alert nobody could act on until the clock caught up.

The simulator generates the *movement* a receiver would have reported; every
generated fix then passes through the same classification path as a hardware
fix. Nothing writes a verdict directly, so a simulated track exercises real code
and its findings are genuinely computed. Provenance is stamped `SIMULATED` on
each row and surfaced in the API response.

### Prototype ML security classifier

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /api/v1/ml/model` | any signed-in user | Loaded artifact: version, algorithm, training metrics, confusion matrix, escalation threshold, disclaimer |
| `POST /api/v1/ml/classify` | any signed-in user | Score an ad-hoc feature set; returns classification, probability, severity, reason and per-feature contributions. Persists nothing |

Both are also mounted unversioned at `/api/ml/*`. Neither writes to the
database: classification of real fixes happens inline during telemetry ingest,
and `POST /ml/classify` exists so the reasoning can be inspected without
fabricating a GPS fix.

`POST /ml/classify` **requires all eight features**. Omitting one is a `422`
that names the missing field rather than defaulting it to zero, because a
confident prediction from partly-guessed input is exactly what the endpoint
exists to prevent.

| Module | Role |
| --- | --- |
| `ai/ml/features.py` | The eight features, derived from the rule engine's own measurements |
| `ai/ml/dataset.py` | Synthetic episode generator; hand-written labels; split grouped by episode |
| `ai/ml/model.py` | `SecurityClassifier`: fit, score, explain, save/load. Logistic artifacts are plain JSON; random forest uses `joblib` |
| `services/security_ml.py` | Loads and caches the artifact, scores a fix, and applies the combination rule |
| `migrations/versions/0006_ml_classification.py` | `ml_classification`, `ml_probability`, `ml_model_version` on `gps_events` |

**The combination rule, which is the part that matters:**

> The rule engine is a floor. The classifier may raise a verdict and may never
> lower it.

A `BREACH` stays a `BREACH` and raises its alert whatever the model concludes.
An ML escalation only fires above `ML_ESCALATION_THRESHOLD` (default 0.70, and
never below 0.5 — escalating on a coin flip would fill the alert queue with
noise). The rule verdict is always reported alongside the final one, so a
reviewer can see which of the two produced what was stored.

With no trained artifact the service reports itself unavailable, logs why, and
every fix is classified by the rules alone. Nothing in the security path depends
on the model being present.

**This is a prototype trained on synthetic data and must not be represented as a
production model.** See [`../docs/AI-INTEGRITY.md`](../docs/AI-INTEGRITY.md) §4b.

### Retrieval-time prediction

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `POST /api/v1/retrieval/predict` | any signed-in user | Estimate a retrieval. With `custody_session_id` the live queue is measured and the estimate stored; with `features` a what-if is scored and nothing written |
| `GET /api/v1/retrieval/<custody_id>` | any signed-in user | The estimate stored for a session — what the customer was told, not a fresh number |
| `GET /api/v1/retrieval/model` | any signed-in user | Artifact metadata: held-out MAE, the baseline it had to beat, feature list, disclaimer |

All three are also mounted unversioned at `/api/retrieval/*`, which is the path
the module's contract was specified against.

Every response carries `estimated_retrieval_time`, `estimated_minutes`,
`queue_position` and `recommended_dispatch_time`, plus
`trained_on_synthetic_data: true`, a `data_basis` string and a `disclaimer`.

**The dispatch recommendation is a decomposition, not a second opinion.** The
model does not output a dispatch time. The service asks the same model a
counterfactual — what the wait would be if this were the only request — and
treats the difference as the part of the wait caused by waiting one's turn. A
13-minute estimate with a 9-minute fetch therefore recommends dispatch in 4
minutes. It inherits every limitation of the prediction it is decomposed from.

Supplying `custody_session_id` **and** `features` is a `422`: for a real session
the inputs are measured from the database, so caller-supplied features would be
silently ignored, and an estimate whose inputs the caller believes they chose is
worse than no estimate. A partial feature set is likewise a `422` that names the
missing field.

| Module | Role |
| --- | --- |
| `ai/retrieval/features.py` | The eleven features and their human labels |
| `ai/retrieval/dataset.py` | Synthetic labelled history, split by time; `load_history` reads back an exported file |
| `ai/retrieval/model.py` | `RetrievalModel`: ridge regression, MAE scored against the baselines, plain-JSON artifact |
| `services/retrieval.py` | Measures the live queue/staffing/zone state, calls the model, stores the row |
| `routes/retrieval.py` | The three endpoints, at both prefixes |
| `utils/scripts/generate_retrieval_history.py` | Writes the synthetic history for inspection |
| `utils/scripts/train_retrieval_model.py` | Fits the model and saves `storage/models/retrieval_model.json` |
| `utils/scripts/evaluate_retrieval_model.py` | MAE against actual times, by zone/queue depth/time of day; exits 1 if it fails to beat the baseline |

The `retrieval_predictions` table was created by revision `0002`; no new
migration was needed. It stores the point estimate, the interval, the queue
position, the confidence, the model version, the explanation and an
`inputs_snapshot` of everything the model was told — so a later reviewer can see
what produced a given number rather than having to take it on trust.

**This is a baseline trained on synthetic data and must not be represented as an
operational forecast.** See [`../docs/AI-INTEGRITY.md`](../docs/AI-INTEGRITY.md) §5.

### Centralized alert feed

| Method | Path | Actor | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/feed` | any | the alerts this actor may see |
| `GET` | `/api/v1/feed/summary` | any | unread count for the notification badge |
| `POST` | `/api/v1/feed` | staff | raise an alert |
| `GET` | `/api/v1/feed/<id>` | any | one alert, if in scope |
| `POST` | `/api/v1/feed/<id>/read` | any | mark it read **for the calling user** |
| `POST` | `/api/v1/feed/read-all` | any | mark everything visible as read |
| `POST` | `/api/v1/feed/<id>/resolve` | manager/admin | close it |

Five categories — `GEOFENCE_BREACH`, `SUSPICIOUS_MOVEMENT`, `OVERSPEED`,
`POTENTIAL_DAMAGE`, `RETRIEVAL_READY` — each carrying `alert_id`, `custody_id`,
`vehicle_id`, `type`, `severity`, `message`, `created_at` and `status`.

**This sits on top of `security_alerts` rather than replacing it.** That table is
the geofence engine's own record: it points at a GPS fix, a zone, and a vision
evidence blob. The feed is the unified view users read, spanning geofence
events, damage findings and retrieval readiness. Each alert points back at its
origin through `source_entity_type`/`source_id`, so the evidence chain survives.

**The five categories fold the geofence detector's seven types together.** That
projection is lossy by design, so the original is preserved in `source_type` and
`CATEGORY_BY_SOURCE_TYPE` maps `TAMPER_DETECTED` and `TELEMETRY_LOSS` to
`SUSPICIOUS_MOVEMENT`. `category_for()` raises on an unmapped type rather than
defaulting: a new detector type silently becoming "suspicious movement" would
mislabel security events.

#### Read state is per recipient

`alerts.status` tracks only the lifecycle (`OPEN` / `RESOLVED` / `DISMISSED`).
"Have I seen this" is a separate row per person in `alert_reads`. A single `READ`
flag would mean whoever opened the app first cleared it for everyone — a manager
could miss a CRITICAL breach because a customer glanced at their phone.

`mark_read` never changes the alert, and `read-all` is scoped by the same
predicate as the list, so it cannot clear alerts the actor was never shown.

#### Scoping happens in the query

Each role sees a different slice, decided in SQL rather than filtered after
fetching:

- **manager/admin** — the whole feed;
- **valet operator** — alerts addressed to `VALET_OPERATOR`;
- **customer** — alerts addressed to `CUSTOMER` about a vehicle they own.

Fetching by id applies the same predicate, and returns **404 rather than 403** —
confirming that a hidden alert exists would leak the existence of an incident
about someone else's car.

#### Resolution is manager-only

An operator can read and act, but closing is attributed to someone accountable.
`RESOLVED` and `DISMISSED` are recorded separately: "we dealt with it" and "we
judged it needed no action" are different claims. Closing a feed alert mirrors
onto the underlying `security_alerts` row so the two cannot disagree.

#### Publishers

Alerts are raised at the point the fact is established, not by a poller that
would have to guess what it had already seen:

- `services.geofence` → `raise_from_security_alert`
- `services.inspections` → `raise_potential_damage`, for UNVERIFIED findings only
- `services.tickets` → `raise_retrieval_ready`, on the `READY_FOR_PICKUP` transition

Each is best-effort: the domain record is authoritative and already committed, so
a notification failure must not roll it back.

**Potential damage is staff-only until verified.** An unconfirmed detector
finding is a task for a manager, not news for a customer. The wording stays
"potential" all the way to the UI.

#### Notification channels

`services/notifications/` holds the transport abstraction. Business logic calls
`dispatch(alert)` and never names a channel; recipients are resolved from the
alert's `audiences`, and each enabled channel is tried in turn.

Only `IN_APP` is wired up. `EMAIL`, `SMS` and `PUSH` are complete
`BaseChannel` implementations with `enabled = False` and no provider — adding
one means implementing `send` and flipping the flag, with no change to the alert
logic. They refuse rather than pretend: a stub returning success would write
`DELIVERED` rows for messages nobody received.

Delivery returns a `DeliveryResult` rather than raising, so a broken push cannot
roll back the alert that caused it. `notifications` is a delivery ledger — a row
is written on failure too, with a reason, so a notification that went nowhere is
visible instead of lost.

### Manager dashboard and reports

| Method | Path | Actor | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/manager/dashboard` | staff | metrics, chart series, tables and filter options |
| `GET` | `/api/v1/manager/report?format=csv\|json` | staff | the same figures as a download |

Filters are query parameters: `date_from`, `date_to`, `status`, `severity`,
`vehicle_id`. All are validated, and an invalid value is a `422` naming the
allowed set — an ignored filter would be worse than a rejected one, because the
caller would receive a full aggregate believing it was scoped. The window is
capped at 366 days and defaults to the last 30.

**The report is the dashboard's own query path**, not a parallel
implementation. A filed report that contradicted the screen it summarised would
be worse than no report, because the report is the artefact someone keeps.
Exports are audited with the `EXPORT` action.

The CSV carries an `X-Report-Contains-Generated-History` header. A spreadsheet
cannot show the banner the screen shows, so the provenance travels in the
header instead of being lost on download.

#### Metrics are labelled by what they cover

Each metric carries `windowed`. "Vehicles checked in today" and "active custody
sessions" are point-in-time and are **not** scoped by the date filter, because
"how many cars do we have right now" has no date range. Retrieval times,
custody durations and damage counts are. Nothing on the card row distinguishes
them visually, so the payload has to, or a monthly total gets read as today's
count.

#### Charts declare their sample size

Every series carries `n`, `min_points`, `sufficient` and a `note`. This is the
mechanism behind the requirement not to draw meaningless charts: a two-point
trend is a coincidence with axes, and the endpoint says so rather than letting
the client decide. Retrieval times additionally report `records`, because a
series can have many daily points each resting on one or two cars.

Two deliberate choices about *what* to chart:

- **Alerts and damage are bucketed weekly**, not daily. They are sparse, and a
  daily series would be mostly zeros with occasional spikes — which reads as a
  pattern where there are only a handful of events.
- **Custody duration is a histogram**, not a trend. The question is "how long do
  we hold cars", and the answer a manager needs is the long tail that occupies
  bays, which an average hides completely.

Days with no arrivals are returned as zero rather than omitted, so a line
cannot join two distant points and invent a slope between them.

#### Provenance

The payload reports whether the figures include rows written by
`generate_operational_history.py`, with a count and a note. A reviewer seeing
ninety days of history is entitled to know whether it records real operations
or was written by a script.

### Valet operator dashboard

| Method | Path | Actor | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/staff/dashboard` | staff | the composed operator screen |
| `GET` | `/api/v1/staff/custody/<id>/condition` | staff | before/after comparison for one car |

Staff-only; a customer asking for it gets `403` rather than an empty `200`,
because an empty dashboard reads as "you have no vehicles" rather than "you may
not look".

Returns seven sections — `incoming`, `active_vehicles`, `pending_pickups`,
`retrieval_queue`, `security_alerts`, `inspections` and `zones` — each carrying
`state ∈ {ok, empty, unavailable}`. An empty retrieval queue is a good outcome
and must not look like a failed request.

**"Incoming" means a ticket exists and custody has not started.** That is the
only arrival state the data model has; there is no separate expected-arrival
record, so inventing one would have meant showing vehicles that were never
coming.

**The retrieval queue and pending pickups are different jobs.** The queue is
`RETRIEVAL_REQUESTED` / `RETRIEVAL_IN_PROGRESS` — work a valet still has to do.
Pending pickups is `READY_FOR_PICKUP` — work already done, waiting on the
customer to sign for it. An operator who could not tell those apart would
either fetch a car twice or leave one standing.

**`display_status` is derived, not stored.** One of `IN_CUSTODY`, `MOVING`,
`READY`, `ALERT`, `COMPLETED`, coarser than `CustodyStatus` because an operator
scanning twenty rows needs to know whether a car is sitting still, being moved,
or ready to hand back. `ALERT` is not a custody state at all — it is a security
override applied on top of whichever state the car is in, because a badge
reading `IN_CUSTODY` with a small icon beside it would be scanned as fine,
which is the wrong failure mode for a security screen. Labels ship with the
payload so the UI cannot keep a second copy of the vocabulary.

**`capabilities` publishes what this actor may actually do**, derived from the
same role tuples the endpoints enforce. A valet operator may move a car and
photograph it, but acknowledging or resolving an alert is a manager act, and
verifying a damage finding is deliberately narrower than capturing one. A
button that returns `403` is worse than no button: it tells an operator the
action is available and then refuses them in front of a customer.

**This service performs no writes.** Every action the operator takes calls the
endpoint that already exists for it, so there is one state machine and one
audit path. `tests/test_staff_dashboard.py` asserts the audit row behind each
one — `STATUS_CHANGE`, `CHECK_OUT`, `ALERT_ACKNOWLEDGED`, `ALERT_RESOLVED` —
and asserts that the published capability flags match what the endpoints
actually enforce, by calling them.

### Customer dashboard

| Method | Path | Actor | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/customers/me/dashboard` | customer | the composed snapshot behind all five cards |
| `POST` | `/api/v1/retrieval/request` | customer | request the car; returns the fresh estimate |
| `GET` | `/api/v1/retrieval/queue` | staff | the pending-retrieval queue the valet polls |
| `GET` | `/api/v1/audit` | manager/admin | paginated audit log, newest first, server-side filters |
| `GET` | `/api/v1/audit/filters` | manager/admin | distinct actions/entity types/outcomes present, for the dropdowns |
| `GET` | `/api/v1/audit/timeline/<custody_id>` | manager/admin | one custody's events, oldest first, with lifecycle gaps marked |
| `GET` | `/api/v1/audit/vehicles/<vehicle_id>` | manager/admin | every custody timeline for one vehicle |

Also served under the unversioned `/api/...` aliases, like the rest of the
domain API.

`GET /customers/me/dashboard` returns one object with `has_active_custody`,
`custody_session_id` and eight sections — `custody`, `vehicle`, `zone`,
`location`, `security`, `condition`, `retrieval`, `actions`. **Every section
carries `state ∈ {ok, empty, unavailable}` plus a `reason`.** `empty` is a real
answer from the database: a customer with no vehicle in custody gets `200` and
`has_active_custody: false`, not an error and not five blank cards.
`unavailable` means a sub-query raised and says why, so one broken dependency
degrades one card instead of the whole page.

The service **reuses** existing authorization rather than reimplementing it —
the security, condition and location sections delegate to
`geofence_service.list_alerts`, `inspections_service.get_inspection_report` and
`tracking_service.tracking_view`, each of which already scopes to the actor, so
ownership violations surface as `404` exactly as they do on those endpoints.

`actions.can_request` is derived from the same `ALLOWED_CUSTODY_TRANSITIONS`
table the custody state machine enforces, so the UI can disable the button for
precisely the reason the API would return `409`, and print that reason.

`POST /retrieval/request` performs the status change through
`update_custody_status` (reusing the tested state machine and its audit trail),
writes a `RETRIEVAL_REQUESTED` audit row, and only then generates the
prediction — so the request is already counted in its own queue position. An
untrained predictor does not block it: `estimate_available: false` is returned
instead, because the customer did ask and a valet was told.

**A permission was narrowed deliberately.** `update_custody_status` used to
require a valet role unconditionally, which meant a customer could not request
their own car. The guard is now per-target: a customer may move **their own**
session to `RETRIEVAL_REQUESTED` and nothing else; every other transition stays
staff-only.

`retrieval_service.prediction_recency_order()` is the single ordering used by
both the customer's card and the valet's queue. Ordering by `requested_at` is
wrong — it is a modelling input that the demo seed writes in the future, and
two rows can share it exactly, which once made the queue show a stale estimate
beside a fresh one on the customer's own screen. PENDING sorts first, then
`created_at` descending, then `id`, so a tie cannot return different answers on
two reads.

No new migration was required: the dashboard reads tables that already exist.

## Audit trail

`audit_logs` is the platform's evidence table. It answers one question — *what
happened, to what, by whom, and when* — and is built so the answer cannot be
changed afterwards.

### The vocabulary

`AuditAction` has 29 members. Fourteen are **lifecycle events**, the sequence a
vehicle passes through; the rest are supporting events (`EXPORT`,
`ALERT_ACKNOWLEDGED`, `LOGIN_FAILED`, ...) that matter but are not custody
stages.

```
USER_LOGIN, VEHICLE_CREATED, TICKET_CREATED, VEHICLE_CHECKED_IN,
IMAGE_UPLOADED, CUSTODY_STARTED, GPS_BREACH, SECURITY_ALERT_CREATED,
PICKUP_REQUESTED, RETRIEVAL_PREDICTED, PICKUP_IMAGE_CAPTURED,
DAMAGE_DETECTED, VEHICLE_PICKED_UP, CUSTODY_COMPLETED
```

Eight names were renamed to read as *what happened to the vehicle* rather than
*which button was pressed*: `LOGIN`→`USER_LOGIN`, `CHECK_IN`→`VEHICLE_CHECKED_IN`,
`CHECK_OUT`→`VEHICLE_PICKED_UP`, `PHOTO_UPLOADED`→`IMAGE_UPLOADED`,
`ALERT_RAISED`→`SECURITY_ALERT_CREATED`, `DAMAGE_FLAGGED`→`DAMAGE_DETECTED`,
`RETRIEVAL_REQUESTED`→`PICKUP_REQUESTED`, `PREDICTION_GENERATED`→`RETRIEVAL_PREDICTED`.
`RETRIEVAL_REQUESTED` survives as a **`CustodyStatus`** — it is a state, not an
event — which is the distinction the old names blurred.

Migration `0008` rewrites existing rows to the new names and the downgrade
reverses that mapping explicitly, so the vocabulary change is not destructive.

### Append-only, enforced by the database

A blanket `BEFORE UPDATE` trigger is wrong here, because PostgreSQL implements
`ON DELETE SET NULL` **as an UPDATE**. Two writes are load-bearing:

* the `0002` actor-tombstone trigger sets `audit_logs.actor_deleted_at` when a
  user is deleted, so the trail outlives the account;
* `actor_id` and `custody_session_id` are de-referenced when their target goes.

So `0008` guards **rewriting history**, not the SQL verb. One function backs
two triggers:

* `trg_audit_logs_append_only` (`BEFORE UPDATE … FOR EACH ROW`) compares
  `OLD`/`NEW` with `IS NOT DISTINCT FROM` and permits only `actor_deleted_at`
  NULL→set, `actor_id`→NULL and `custody_session_id`→NULL. Everything else
  raises.
* `trg_audit_logs_no_delete` (`BEFORE DELETE OR TRUNCATE … FOR EACH STATEMENT`)
  refuses outright.

Verified directly against PostgreSQL: editing `description`, backdating
`occurred_at`, reattributing `actor_email`, renaming `action` and clearing a
tombstone all raise `audit_logs is append-only: …`; the tombstone write and both
FK releases succeed; `DELETE` and `TRUNCATE` are refused.

### Ordering

`occurred_at` cannot order this table. One request routinely writes several rows
inside a single transaction — a check-in writes `VEHICLE_CHECKED_IN` **and**
`CUSTODY_STARTED` — and they share a timestamp. An `(occurred_at, id)` tiebreak
falls back to a random UUID, which once rendered a timeline as *"custody
started"* before *"vehicle checked in"*.

`0008` therefore adds `seq BIGINT GENERATED … AS IDENTITY`, and every read
orders by it. It is the only ordering an append-only log can promise.

### Endpoints

```
GET /api/v1/audit                          paginated log, newest first
GET /api/v1/audit/filters                  distinct values present, for dropdowns
GET /api/v1/audit/timeline/<custody_id>    one custody, oldest first
GET /api/v1/audit/vehicles/<vehicle_id>    every custody timeline for a vehicle
```

Read-only: no `POST`/`PATCH`/`PUT`/`DELETE` is registered, so the UI cannot
offer a control for an operation that has no route. `VALET_MANAGER` and `ADMIN`
only — the log holds email addresses, IP addresses and other people's custody,
so an operator or customer gets `403`. That is a statement about the caller's
role, unlike the existence of someone else's session, which stays `404`.

Filters combine with `AND`: `action`, `entity_type`, `entity_id`, `actor_id`,
`custody_session_id`, `outcome`, `date_from`/`date_to`, `lifecycle_only`. Each
returns `422` rather than `500` for a malformed UUID or timestamp.

The response carries `mutable: false` and a `mutability_note`, so the viewer can
state its own immutability from the API rather than from a hardcoded sentence
that could drift.

### The timeline reaches past the custody row

Three groups are gathered, each labelled with a `scope`:

* **`custody`** — rows stamped with this session's id.
* **`provenance`** — `TICKET_CREATED` and `VEHICLE_CREATED` *cannot* carry a
  `custody_session_id`, because no session exists when they are written. They
  are reached through the ticket and vehicle instead.
* **`login`** — `USER_LOGIN` belongs to a person, so it is scoped twice: the
  most recent login at or before the custody opened, plus any login by a
  participant during it. An unbounded login list would bury the custody under
  every unrelated session those people ever opened.

The timeline also returns `lifecycle_progress` (all fourteen, boolean each) and
`lifecycle_missing`, so a custody with no pickup photographs says so explicitly
instead of leaving the reader to infer it from what is present.

`tests/test_audit_trail.py` (51 tests) covers the vocabulary, the guard, access
control, filters, paging and the timeline scopes.
`scratch/verify_audit_events.py` drives a real custody over HTTP and asserts all
fourteen events are emitted by the real code paths — something unit tests that
insert log rows directly cannot prove.

## Database schema

Eleven domain tables created by revision `0002`, plus the `revoked_tokens`
denylist added by revision `0003` — fourteen tables in total once
`alembic_version` and `schema_meta` are counted:

| Table | Purpose |
| --- | --- |
| `users` | All four roles. Passwords are PBKDF2 hashes; accounts are soft-deleted |
| `vehicles` | A customer's vehicles. Plates stored as entered *and* normalised |
| `parking_zones` | Geofence polygons as JSONB `[lon, lat]` rings, plus capacity |
| `valet_tickets` | Ticket code, QR payload and QR image *path* |
| `custody_sessions` | The drop-off → pickup window everything else hangs off |
| `gps_events` | Position fixes. Append-only, provenance per row |
| `vehicle_inspections` | One `DROP_OFF` and one `PICKUP` photo set per session |
| `damage_detections` | Region, confidence, reasoning, evidence path, method |
| `security_alerts` | Anomaly findings with severity and attributed acknowledgement |
| `retrieval_predictions` | Wait estimate, confidence, queue position, inputs snapshot |
| `audit_logs` | Append-only trail; references entities by value |

**Relationships.** A customer has many vehicles; a vehicle has many custody
sessions; a custody session belongs to one valet ticket and has many GPS
events, inspections, alerts and predictions; an inspection has many damage
detections.

**Two rules are enforced by partial unique index, not by application code**,
because they are the ones a service layer gets wrong:

- `uq_custody_sessions_vehicle_open` — a vehicle cannot be in two *open*
  custody sessions. This is what actually enforces one-custody-at-a-time;
  closing a session releases the vehicle.
- `uq_vehicle_inspections_session_stage` — exactly one inspection per stage per
  session.

`uq_vehicles_normalized_plate_active` works the same way: archiving a vehicle
frees its plate for re-registration.

**No table stores image bytes.** `photo_refs`, `qr_code_path`,
`evidence_image_path` and friends hold validated references — path, byte size,
dimensions, MIME type, SHA-256 — produced by `utils/image_ref.py`, which
rejects `bytes` outright. There is no `BYTEA` column anywhere, and a test
asserts it.

**Honesty enforced by `CHECK`.** `ck_damage_detections_reasoning_required`
means a finding cannot be saved without a reason.
`ck_damage_detections_ml_requires_model_version` means nothing may claim
`method = 'ML_MODEL'` without naming a version.

**Status vocabularies live in `models/enums.py`**, the single source of truth.
Columns are `VARCHAR` + `CHECK` rather than native enums so a status can be
added *or removed* without `ALTER TYPE`. The migration writes the permitted
values as literals to stay deterministic; `tests/test_database_models.py` fails
if the two ever drift.

### Demonstration data

`python utils/scripts/init_db.py --seed` creates one admin plus a complete
demo dataset: 5 accounts across all roles, 3 zones, 3 vehicles, 2 tickets, one
open and one closed custody session, 8 GPS fixes, 4 inspections, 6 damage
findings, 1 geofence alert, 1 retrieval prediction and 4 audit entries.

It is **idempotent** — running it again creates nothing, which a test asserts by
comparing exact row counts. Identifiers are derived from a fixed UUID5
namespace, so they are stable across runs and machines.

Two honesty rules the seeder follows: every GPS fix is labelled `SIMULATED`
with `device_id = SIM-TELEMETRY-001`, and every damage finding is
`CLASSICAL_CV` with its reasoning and thresholds recorded. Nothing claims to be
hardware telemetry or a trained model.

## Response envelope

Every response — success or failure — has this shape:

```json
{ "success": true,  "data": {}, "meta": { "request_id": "…", "server_time": 1788772515.59 } }
{ "success": false, "error": { "code": "…", "message": "…", "details": {} }, "meta": { "request_id": "…" } }
```

Send `X-Request-Id` and it is echoed back, so a browser action can be matched to
a server log line. Every response also carries `X-Content-Type-Options:
nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.

Error codes: `VALIDATION_ERROR`, `AUTHENTICATION_ERROR`, `AUTHORIZATION_ERROR`,
`NOT_FOUND`, `CONFLICT`, `UNSUPPORTED_MEDIA_TYPE`, `PAYLOAD_TOO_LARGE`,
`RATE_LIMITED`, `DATABASE_ERROR`, `INTERNAL_SERVER_ERROR`.

## Configuration

`config/environment.py` is the **only** module that touches `os.environ`.
Everything else reads from the `Settings` object, so no secret is ever read
twice and nothing can silently fall back to a hardcoded default.

`ProductionSettings.validate()` refuses to boot with a `JWT_SECRET_KEY` under
32 characters, the algorithm `none`, or `*` as a CORS origin.

```powershell
python utils/scripts/show_config.py     # resolved values, secrets redacted
```

## Migrations

```powershell
# after adding models:
alembic revision --autogenerate -m "add users table"
alembic upgrade head
alembic downgrade -1
alembic history
```

`alembic.ini` contains **no** DSN; `migrations/env.py` reads it from `config`,
which reads it from the environment.

## Testing

```powershell
python -m pytest                        # full suite (835 tests)
python -m pytest -q -m "not postgres"   # skip database-backed tests
python -m pytest tests/test_security.py -v
python -m pytest tests/test_security_ml.py -v   # ML layer + the escalation-only rule
python -m pytest --cov=app --cov=config --cov=routes --cov=utils --cov=database --cov=services --cov-report=term-missing
python -m ruff check .
```

Database-backed tests need `valet_db_test`:

```powershell
psql -U postgres -c "CREATE DATABASE valet_db_test OWNER valet_user;"
```

If it is unreachable those tests are skipped, not failed — the rest of the
suite still runs on a machine with no PostgreSQL installed.

## Security notes

- Passwords: PBKDF2-SHA256, 600 000 iterations, per-password salt, constant-time
  comparison.
- JWT: `HS256` with `iss`, `aud`, `exp`, `nbf`, `jti`, `typ` enforced.
  Refresh tokens are rejected when presented as access tokens. `alg: none` is
  rejected.
- Roles: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`, enforced by
  the `require_auth`, `roles_required(...)`, and `capability_required(...)`
  decorators. Inheritance is explicit: `ADMIN → VALET_MANAGER → VALET_OPERATOR`,
  with `CUSTOMER` standing alone.
- Uploads: extension allow-list **plus** magic-byte sniffing **plus** size and
  pixel bounds. A PNG renamed `.jpg` is rejected; a RIFF/AVI file renamed
  `.webp` is rejected. Size and empty checks run before sniffing.
- SQL: ORM or bound parameters only.
- Secrets: environment only. `show_config.py` and `as_safe_dict()` redact them
  in exactly one place each; the DSN is masked to host-only.

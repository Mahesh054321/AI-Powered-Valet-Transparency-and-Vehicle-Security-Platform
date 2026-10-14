<<<<<<< HEAD
# AI-Powered-Valet-Transparency-and-Vehicle-Security-Platform
=======
# AI-Powered Valet Transparency and Vehicle Security Platform

A full-stack academic platform that converts the **valet custody window** — the
period between handing over a car and collecting it — into a transparent,
traceable and auditable process.

| Layer | Technology |
| --- | --- |
| Frontend | **React 19** (Vite 8, React Router 7, TanStack Query, Zustand, Leaflet) |
| Backend | **Flask 3** REST API (Python 3.10+), modular services |
| Database | **PostgreSQL 14+** via SQLAlchemy 2.0 + Alembic migrations |
| Vision | **OpenCV** (headless build), scikit-image, scikit-learn |
| Maps | **Leaflet** + OpenStreetMap tiles (no API key required) |
| Auth | PBKDF2-SHA256 password hashing + JWT (access/refresh) with RBAC |
| Contract | REST/JSON with a single response envelope; frontend and backend are separate deployables |

> **Deliberately not used:** Streamlit, Gradio, Dash or any Python UI toolkit.
> The interface is React, and every interaction crosses a real HTTP boundary.

---

## Table of contents

1. [The problem](#1-the-problem)
2. [What is built so far](#2-what-is-built-so-far-phase-1)
3. [Project structure](#3-project-structure)
4. [Prerequisites](#4-prerequisites)
5. [Setup on Windows](#5-setup-on-windows)
6. [Setup on macOS / Linux](#6-setup-on-macos--linux)
7. [Running the platform](#7-running-the-platform)
8. [Verifying the installation](#8-verifying-the-installation)
9. [Testing and linting](#9-testing-and-linting)
10. [Configuration reference](#10-configuration-reference)
11. [Security model](#11-security-model)
12. [Honesty statement: what is real and what is simulated](#12-honesty-statement-what-is-real-and-what-is-simulated)
13. [Build roadmap](#13-build-roadmap)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. The problem

Valet parking creates a custody gap in which the customer cannot see:

- **where** the vehicle is,
- **how** it is being handled,
- **what condition** it was in at drop-off versus pickup,
- **whether** it moved without authorisation,
- **when** it will actually be ready.

The platform records every custody event, monitors the vehicle's location
against a geofence, compares before/after imagery with OpenCV, flags suspicious
movement, predicts retrieval time, and produces a complete audit trail per
vehicle.

## 2. What is built so far (Phase 1)

This repository is delivered **incrementally**. Phase 1 is the foundation; the
domain modules follow in the order listed in the [roadmap](#13-build-roadmap).

Implemented and verified in Phase 1:

- **Modular project structure** for a separated React SPA and Flask API.
- **Environment-driven configuration** (`backend/config.py`) with three
  profiles — `development`, `testing`, `production`. Production refuses to boot
  with a weak JWT secret or wildcard CORS.
- **PostgreSQL integration** with SQLAlchemy 2.0, a naming convention for
  deterministic constraints, and **Alembic migrations** (baseline revision
  applied and reversibility-tested).
- **Flask application factory** with a single composition root, request-id
  propagation, security headers, access logging and a **uniform JSON error
  envelope** for every failure path.
- **Security primitives**: PBKDF2-SHA256 password hashing, JWT issuing and
  strict verification (signature, expiry, issuer, audience, token type),
  four roles, an explicit **capability matrix** with role inheritance, and
  `@require_auth` / `@roles_required` / `@capability_required` guards.
- **Input validation library**, including signature-sniffed image upload
  validation (a PNG renamed to `.jpg` is rejected).
- **Service-level API**: `GET /api/health` (liveness and status snapshot) and
  `GET /api/readyz` (real database round trip with latency; `503` when a
  dependency is down). Both unversioned, both public.
- **React application shell**: role-filtered sidebar, responsive layout,
  React Query data layer, axios client with token injection and error
  normalisation, context-based auth, and a **live System Status page** that
  polls the API.
- **Developer tooling**: `init_db.py`, `run_api.py`, `show_config.py`,
  `verify_setup.py`, plus **206 pytest** and **40 Vitest** tests with Ruff and
  ESLint configurations.

## 3. Project structure

```
.
├── README.md                     ← this file
├── Makefile                      ← optional task shortcuts (npm scripts work on Windows)
├── package.json                  ← workspace scripts: dev, setup, test, lint, build
├── .gitignore                    ← .env and media uploads are never committed
│
├── backend/                      ← Flask REST API
│   ├── .env.example              ← every secret lives here, never in code
│   ├── requirements.txt          ← pinned runtime dependencies
│   ├── requirements-dev.txt      ← pytest, ruff
│   ├── pyproject.toml            ← ruff + pytest configuration
│   ├── alembic.ini               ← no credentials in this file
│   ├── wsgi.py                   ← WSGI entry point (waitress / gunicorn)
│   │
│   ├── config/                   ← env → Settings objects (dev / testing / production)
│   │   ├── environment.py        ← the ONLY reader of os.environ; typed accessors
│   │   └── settings.py           ← Settings, build_database_url(), masking
│   │
│   ├── app/                      ← application factory + HTTP composition
│   │   ├── __init__.py           ← create_app(): the single composition root
│   │   ├── extensions.py         ← db, naming convention
│   │   ├── error_handlers.py     ← error handlers → JSON envelope
│   │   ├── cors_config.py        ← origin allow-list from the environment
│   │   └── logging_config.py     ← human or JSON logs
│   │
│   ├── routes/                   ← HTTP surface
│   │   ├── __init__.py           ← register_routes(): mounts everything
│   │   ├── health.py             ← GET /api/health, GET /api/readyz
│   │   └── v1_registry.py        ← fresh Blueprint for /api/<API_VERSION>
│   │
│   ├── services/                 ← domain logic (one concern per module)
│   │   └── health.py             ← liveness_report(), readiness_report()
│   │
│   ├── database/                 ← session + ping, schema checks, seeding
│   │   ├── __init__.py           ← get_session(), ping()
│   │   ├── checks.py             ← schema assertions shared by migrations + tests
│   │   └── seed.py               ← idempotent reference-data seeding
│   │
│   ├── models/                   ← SQLAlchemy models
│   │   └── base.py               ← Base, UUIDPrimaryKeyMixin, TimestampMixin
│   │
│   ├── schemas/                  ← response contracts
│   │   └── common.py             ← envelope + pagination schemas
│   │
│   ├── middleware/               ← request id, security headers, 404/405 fallback
│   │
│   ├── utils/                    ← framework-agnostic primitives (no Flask imports)
│   │   ├── security.py           ← hashing, JWT, roles, capabilities, guards
│   │   ├── validation.py         ← field + upload validation
│   │   ├── pagination.py         ← list endpoint pagination
│   │   ├── responses.py          ← success/error envelope builders
│   │   ├── exceptions.py         ← typed API errors
│   │   └── scripts/              ← developer CLI
│   │       ├── init_db.py        ← migrate / reset / seed / verify
│   │       ├── run_api.py        ← dev server or waitress/gunicorn
│   │       ├── show_config.py    ← resolved config, secrets redacted
│   │       └── verify_setup.py   ← 7-step pre-flight check
│   │
│   ├── ai/                       ← AI modules (Phase 3+); roadmap in docstring
│   ├── migrations/               ← Alembic (env.py reads the DSN from config)
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/           ← 0001_baseline, 0002_core_domain, 0003_revoked_tokens
│   │
│   ├── storage/media/            ← uploaded photos (git-ignored)
│   └── tests/                    ← 658 tests, incl. real-PostgreSQL schema + CRUD
│
├── frontend/                     ← React SPA (separate deployable)
│   ├── .env.example              ← public VITE_* values only
│   ├── vite.config.js            ← dev proxy to Flask, bundle splitting
│   ├── index.html
│   └── src/
│       ├── main.jsx              ← React root: Router > QueryProvider > AuthProvider
│       ├── App.jsx               ← renders <AppRoutes/>
│       ├── config/appConfig.js   ← the only reader of import.meta.env
│       ├── services/             ← apiClient, endpoints, auth/vehicle/ticket/upload/directory services
│       ├── context/              ← AuthContext, QueryProvider
│       ├── hooks/                ← useAuth, useHealthStatus
│       ├── utils/                ← jwt, tokenStorage, constants, format
│       ├── layouts/MainLayout.jsx← sidebar + topbar + outlet
│       ├── routes/               ← ROUTES table, AppRoutes, RequireRole
│       ├── components/ui/        ← Panel, Badge, Field, DataRow, Spinner, Logo
│       ├── pages/                ← LoginPage, SystemStatusPage, NotFoundPage
│       ├── styles/global.css     ← design system (plain CSS, no CDN)
│       └── test/                 ← Vitest setup + render/token helpers
│
│   40 frontend tests live beside the code they cover (*.test.js / *.test.jsx)
│
└── docs/
    ├── ARCHITECTURE.md           ← layering, request lifecycle, data flow
    ├── AI-INTEGRITY.md           ← what is and is not a trained model
    ├── SECURITY-AUDIT.md         ← what was tested, found and accepted
    ├── TEST-REPORT.md            ← suite results, coverage and known gaps
    ├── DEMO-DATA.md              ← what the demo seeds, and how it is labelled
    └── api/
        ├── API-REFERENCE.md      ← every endpoint, grouped by domain
        └── openapi.yaml          ← OpenAPI 3.0.3, generated and verified
```

### The import rule that keeps it acyclic

```
backend :  routes → services → database / models
           utils and config importable from anywhere; utils has no Flask imports
frontend:  services/* must NOT import React context (would cycle)
           → token via utils/tokenStorage.js, sign-out via onUnauthorized()
```

## 4. Prerequisites

| Requirement | Version | Check |
| --- | --- | --- |
| Python | 3.10 – 3.13 (verified on 3.13.14) | `python --version` |
| Node.js | ≥ 20.19 (20 LTS or newer; required by Vite 8) | `node --version` |
| PostgreSQL | 14+ (verified on 17.11) | `psql --version` |

You do **not** need Docker, and you do **not** need any paid map or AI API key.

## 5. Setup on Windows

All commands run in **PowerShell**. Replace `<...>` with your own values.

### 5.1 Install PostgreSQL

Download the installer from <https://www.postgresql.org/download/windows/> and
remember the superuser (`postgres`) password you set. Then create the project
database and role — open **SQL Shell (psql)** or run:

```powershell
psql -U postgres -c "CREATE ROLE valet_user WITH LOGIN PASSWORD '<your-password>';"
psql -U postgres -c "CREATE DATABASE valet_db  OWNER valet_user;"
psql -U postgres -c "CREATE DATABASE valet_db_test OWNER valet_user;"
```

> `valet_db_test` is used only by `pytest`, so the test suite can never touch
> your demo data.

If `psql` is not recognised, add PostgreSQL to your PATH for the session:

```powershell
$env:Path += ";C:\Program Files\PostgreSQL\17\bin"
```

### 5.2 Get the code and create a virtual environment

```powershell
cd C:\path\to\project
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If activation is blocked by the execution policy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 5.3 Install backend dependencies

```powershell
cd backend
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
```

### 5.4 Configure backend secrets

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Open `backend\.env` in any editor and set:

```ini
POSTGRES_PASSWORD=<your-password>
JWT_SECRET_KEY=<the 64-character string printed above>
SEED_ADMIN_EMAIL=admin@valet.local
SEED_ADMIN_PASSWORD=<a strong password you will remember>
```

`.env` is git-ignored. Nothing sensitive is ever committed or sent to the
browser.

### 5.5 Create the schema

```powershell
python utils/scripts/init_db.py --seed
```

Expected output ends with:

```
[init_db] Schema report
  database      : valet_db
  server        : 17.x
  extensions    : pgcrypto, plpgsql
  baseline      : 0001-baseline
  tables (2)    : alembic_version, schema_meta
[init_db] OK
```

### 5.6 Install frontend dependencies

```powershell
cd ..\frontend
Copy-Item .env.example .env
npm install
```

### 5.7 (Optional) Train the prototype security classifier

The platform runs on rule-based checks alone until you do this — that is a
supported state, not a degraded one. Training takes a few seconds and writes a
regenerable artifact to `backend\storage\models\`, which is why it is not
committed: a checked-in model file would drift out of step with the code that
reads it.

```powershell
cd ..\backend
python utils\scripts\train_security_model.py
python utils\scripts\evaluate_security_model.py --fresh-seed 999
```

**Read the disclaimer this script prints before quoting any number it reports.**
The training data is synthetic and its labels were hand-written, so the accuracy
figures describe how well the model recovers that distinction and nothing about
real vehicles. See [`docs/AI-INTEGRITY.md`](docs/AI-INTEGRITY.md) §4b.

### 5.8 (Optional) Train the retrieval-time predictor

Same shape as the classifier above: the platform serves no retrieval estimate
until you train one, and says so plainly instead of guessing. This one takes
three scripts, because the brief separates data generation from training from
evaluation, and keeping them apart means you can inspect the exact rows a model
was fitted on.

```powershell
cd ..\backend
python utils\scripts\generate_retrieval_history.py    # synthetic labelled history
python utils\scripts\train_retrieval_model.py         # ridge regression -> JSON artifact
python utils\scripts\evaluate_retrieval_model.py      # MAE against actual retrieval times
```

**MAE is the headline metric, and it is meaningless on its own.** The evaluator
prints it next to three baselines — the training median, the training mean, and
a hand-written "3.2 minutes per place in the queue" rule — and exits with code 1
if the model fails to beat the best of them. On the default seed it reports
roughly **1.73 minutes MAE against a 2.69-minute median baseline** (≈36% lower),
evaluated on the latest 25% of the generated history by time, which training
never saw.

**What that number does and does not mean.** The generator in
`ai/retrieval/dataset.py` defines the relationship the model recovers, so the MAE
measures how well the model inverts a function written for this project. It is
evidence that the pipeline works end to end. It says nothing about any real
parking facility. Every API response carries `trained_on_synthetic_data: true`.
See [`docs/AI-INTEGRITY.md`](docs/AI-INTEGRITY.md) §5.

### 5.9 (Optional) Build the full demonstration dataset

One command resets the database and rebuilds the whole demo — curated rows plus
bulk history — then **re-reads the database and verifies what it produced**:

```powershell
cd backend
python utils/scripts/seed_demo.py            # reset + curated seed + 90 days of history
python utils/scripts/seed_demo.py --days 30  # shorter history, faster
python utils/scripts/seed_demo.py --no-history   # curated rows only
python utils/scripts/seed_demo.py --seed 7   # a different-but-equally-plausible history
```

It ends with a verification report and the sign-in accounts, and exits non-zero
if any check fails — including if a GPS fix lost its `SIMULATED` label. Full
details are in [`docs/DEMO-DATA.md`](docs/DEMO-DATA.md).

If you would rather run the layers by hand:

```powershell
python utils/scripts/generate_operational_history.py --days 90
python utils/scripts/generate_operational_history.py --seed 7 --reset   # a different plausible dataset
```

The history generator writes plausible custody, inspection, damage, alert,
retrieval **and GPS telemetry** records directly from the application's own
models, with a weekday/weekend demand curve, rush-hour retrieval pressure, and
a realistic verification split (roughly 57% confirmed, 23% rejected, 21% still
unverified). Alerts are correlated with the telemetry that triggered them, so
every generated alert cites a real GPS fix.

It is **synthetic and labelled as such**: every row carries an
`is_demo`/`origin` marker, every GPS fix is stored `source = SIMULATED`, and
the dashboard endpoint reports the provenance so the UI can say so. Skip this
step if you would rather see the dashboard state plainly that it has too little
data — which it does, per chart, rather than drawing a trend from nothing.

## 6. Setup on macOS / Linux

```bash
# PostgreSQL (Debian/Ubuntu)
sudo apt update && sudo apt install postgresql
sudo -u postgres createuser --login valet_user
sudo -u postgres createdb --owner=valet_user valet_db
sudo -u postgres createdb --owner=valet_user valet_db_test

python3 -m venv .venv && source .venv/bin/activate
cd backend
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # then edit the secrets
python utils/scripts/init_db.py --seed

cd ../frontend
cp .env.example .env
npm install
```

## 7. Running the platform

**Terminal 1 — API (port 5000):**

```powershell
cd backend
python utils/scripts/run_api.py
```

**Terminal 2 — React (port 5173):**

```powershell
cd frontend
npm run dev
```

Open <http://localhost:5173>. The **System Status** page immediately polls the
API and shows live database latency. If you stop Flask, the page degrades
visibly within 15 seconds — that is the frontend proving it is really connected.

One command instead of two (from the repository root):

```powershell
npm install          # installs `concurrently`
npm run dev
```

### How the two halves talk

```
Browser (localhost:5173)
   │  GET /api/health             ← relative URL, same origin
   ▼
Vite dev server ── proxy ──► Flask (localhost:5000)
                                  │  SQLAlchemy engine
                                  ▼
                             PostgreSQL (localhost:5432)
```

The SPA never learns the backend's origin, so there is no CORS problem in
development and no backend URL baked into the production bundle. In production
you serve `frontend/dist` from any static host (or Nginx) and reverse-proxy
`/api` to the Flask process.

## 8. Verifying the installation

```powershell
cd backend
python utils/scripts/verify_setup.py
```

Runs seven checks and prints a pass/fail line for each: Python version,
dependencies, `.env` presence and placeholder detection, configuration
resolution, PostgreSQL connectivity, migration state, and an actual
`GET /api/health` request. Exit code is non-zero on failure, so it is CI-ready.

Other useful commands:

```powershell
python utils/scripts/show_config.py          # resolved config, secrets redacted
python utils/scripts/init_db.py --check-only # schema report, changes nothing
```

## 8a. Walking the demo end to end

The platform is built to be demonstrated from drop-off to pickup. With both
servers running and the demo data seeded:

**1. Drop-off (staff).** Sign in as `valet.suresh@example.com` /
`Demo#Passw0rd`. Create a valet ticket for a customer's vehicle, check the
vehicle in to a zone, and photograph its condition. The ticket's QR code is
rendered server-side as a PNG.

**2. The custody window (customer).** Sign in as `customer.aisha@example.com`
(same password) — a customer lands on `/dashboard`, which reads
`GET /api/v1/customers/me/dashboard`. Five cards are populated from that one
call:

| Card | Source |
| --- | --- |
| Vehicle Status | the vehicle, its ticket code, custody status, check-in time |
| Security Status | the worse of the movement verdict and any open alert severity |
| Current Location | the latest GPS fix, its zone, and telemetry provenance |
| Estimated Retrieval | the stored prediction, as a range, with its caveat |
| Condition Status | the inspection report and its detector disclaimer |

**3. Requesting the car.** The **Request Vehicle** button posts to
`POST /api/v1/retrieval/request`, which in one transaction moves the custody
session `PARKED → RETRIEVAL_REQUESTED`, writes an audit row, generates a fresh
retrieval prediction, and queues a notification for the valet. The response
carries the estimated minutes, a ready-by time, the queue position and a
recommended dispatch delay.

**4. The valet side.** Sign in as a valet and open `/valet`. The queue
(`GET /api/v1/retrieval/queue`) lists pending retrievals oldest first with
zone, estimate and progress. **It polls on a 5-second interval and says so on
the page** — there is no websocket channel in this project, and the UI does not
claim otherwise.

**5. Pickup.** Staff move the session to `RETRIEVAL_IN_PROGRESS` and then
`READY_FOR_PICKUP`; the customer's dashboard reflects each change on its next
read.

Three things are worth pointing at during evaluation:

- **A customer with no vehicle in custody gets a 200, not an error.** Every
  section of the dashboard carries a `state` of `ok`, `empty` or `unavailable`,
  so "no car in custody" renders as a sentence rather than as five blank cards
  that read like "all clear".
- **The button disables for the same reason the API would refuse.**
  `actions.can_request` is derived from the same `ALLOWED_CUSTODY_TRANSITIONS`
  table the backend enforces, so the UI cannot offer a transition that would
  409.
- **`customer.rohit@example.com` is the empty-state account.** Aisha has a car
  in custody; Rohit does not. Both are useful to show.

## 9. Testing and linting

The full results, the coverage breakdown and the known gaps are in
[`docs/TEST-REPORT.md`](docs/TEST-REPORT.md).

The API documentation is generated and regression-tested rather than
hand-maintained — see [§9a](#9a-api-documentation).

### Backend — 1074 pytest tests

```powershell
cd backend
python -m pytest                       # 1074 tests
python -m pytest -q -m "not postgres"  # 497 that need no database
python -m pytest -q -m integration     # 87 end-to-end tests
python -m pytest tests/test_security_ml.py -v   # the ML layer, incl. the escalation-only rule
python -m ruff check .
```

Grouped deliberately:

- **No database required** — configuration, password hashing, JWT lifecycle,
  RBAC, validation, upload safety, HTTP envelope. A fresh clone with no
  PostgreSQL still gets a meaningful run.
- **`@pytest.mark.postgres`** — migration linearity, baseline application,
  extension installation, downgrade reversibility, and the readiness probe
  against a live database. Skipped automatically when no server is reachable.

### Frontend — 633 Vitest tests across 49 files

```powershell
cd frontend
npm test            # Vitest + jsdom + Testing Library
npx eslint .
npm run build
```

These run against a real DOM, not mocks of the DOM, and they cover:

- the auth store (sign-in, persistence, expiry, malformed tokens),
- the axios client (bearer-token injection, envelope unwrapping, error
  normalisation, automatic sign-out on `401`),
- role-filtered navigation in `MainLayout`,
- the `RequireRole` guard, including its redirect path,
- `SystemStatusPage` rendering live API data, a degraded database, and an
  unreachable backend,
- `LoginPage` validation, the request it sends, and how it surfaces a backend
  error envelope,
- the customer dashboard's five cards, its empty state, and the reasons the
  **Request Vehicle** button disables itself,
- the valet retrieval queue, including its insistence on labelling itself as a
  poll,
- the alert centre's read/resolve flow, including that resolution is refused to
  an operator and that the confirmation dialog states the closure is
  attributed,
- the ticket detail page's pickup request, including that an absent estimate
  never renders as zero and that the synthetic-data caveat survives alongside
  the figure,
- the inspection capture screen's multipart photo set, including that drop-off
  and pickup go to different endpoints and that unverified findings stay
  labelled *Potential Damage*.

Testing the frontend this way has already paid for itself: it caught a real bug
where the client read `meta.requestId` while the backend emits
`meta.request_id`, which silently dropped the request id from every error
notice.

### 9a. API documentation

The API reference is **generated**, not hand-written, because hand-written API
docs rot the moment a route changes — and stale docs are worse than none, since
a client built against them fails in production rather than at review time.

```powershell
cd backend

# Rebuild docs/api/openapi.yaml and docs/api/API-REFERENCE.md
python utils/scripts/api_docs/build_docs.py

# Prove the committed docs still match the running application
python utils/scripts/api_docs/verify_spec.py
```

Both artefacts render from one catalogue
(`backend/utils/scripts/api_docs/catalog.py`), so the OpenAPI spec and the
readable reference cannot disagree.

`tests/test_api_docs.py` runs the same check inside the suite and fails in
**both** directions:

- a documented route the application does not serve — the docs promise an
  endpoint that does not exist;
- a served route the docs omit — the docs are incomplete.

Alias prefixes (`/api/auth`, `/api/gps`, …) and trailing-slash variants are
folded onto their canonical `/api/v1` form rather than ignored, so an alias only
passes if its canonical path is documented. A further test regenerates the spec
in memory and asserts it is byte-identical to the committed file, so adding a
route without rebuilding fails the build.

The response examples in the reference are **captured payloads** from a running
server against a seeded database, not invented shapes.

To browse the spec interactively, load `docs/api/openapi.yaml` into
[Swagger Editor](https://editor.swagger.io) or any OpenAPI 3 tool.

## 10. Configuration reference

Every value comes from the environment. See
[`backend/.env.example`](backend/.env.example) for the annotated template.

| Variable | Purpose | Default |
| --- | --- | --- |
| `FLASK_ENV` | `development` / `testing` / `production` | `development` |
| `POSTGRES_HOST` `POSTGRES_PORT` `POSTGRES_DB` `POSTGRES_USER` `POSTGRES_PASSWORD` | Discrete DSN parts | `localhost`, `5432`, `valet_db`, `valet_user`, — |
| `DATABASE_URL` | Full DSN; wins over the parts above | unset |
| `JWT_SECRET_KEY` | Token signing key — **≥ 32 chars, mandatory in production** | generated ephemerally in development |
| `JWT_ACCESS_TOKEN_TTL_MINUTES` / `JWT_REFRESH_TOKEN_TTL_DAYS` | Token lifetimes | `30` / `7` |
| `PASSWORD_HASH_SCHEME` / `PASSWORD_HASH_ITERATIONS` | Hashing parameters | `pbkdf2:sha256` / `600000` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated browser origins | `http://localhost:5173,http://127.0.0.1:5173` |
| `MAX_CONTENT_LENGTH_MB` `ALLOWED_IMAGE_EXTENSIONS` `MIN_IMAGE_PIXELS` `MAX_IMAGE_PIXELS` | Upload policy | `15`, `jpg,jpeg,png,webp`, `64`, `4096` |
| `GEOFENCE_BUFFER_METRES` `SUSPICIOUS_SPEED_KMPH` | Detection thresholds | `25`, `12` |
| `TELEMETRY_SOURCE` | **`simulated` or `hardware`** — describes the *installation* and is reported by `/api/readyz`. Individual fixes carry their own `source` column, so a session can contain both | `simulated` |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | Bootstrap admin | — (empty password is generated and printed once) |

## 11. Security model

A full audit — what was tested, what was found, and what was accepted as a
documented risk — is in [`docs/SECURITY-AUDIT.md`](docs/SECURITY-AUDIT.md). The
94 regression tests it produced live in
`backend/tests/test_security_audit.py`.

- **Passwords** are stored only as salted PBKDF2-SHA256 digests
  (`600,000` iterations by default). Comparison is constant-time.
- **JWTs** carry `sub`, `role`, `typ`, `jti`, `iss`, `aud`, `iat`, `nbf`, `exp`.
  Verification rejects a wrong audience, a wrong issuer, an expired token, a
  tampered signature, an unknown role, `alg: none`, and a refresh token
  presented as an access token. All of these have tests.
- **Authorization** uses an explicit capability matrix with role inheritance
  (`ADMIN → VALET_MANAGER → VALET_OPERATOR`). Adding a role grants nothing
  unless it is listed. Note that the matrix is currently *descriptive*: the
  runtime checks are `require_role` decorators plus service-layer role tests.
  See [`docs/SECURITY-AUDIT.md`](docs/SECURITY-AUDIT.md) §2.1.
- **Ownership** is derived from the authenticated caller, never from a request
  parameter. A customer reading another customer's vehicle gets `404` rather
  than `403`, so the response does not confirm that the id exists.
- **Rate limiting** throttles `POST /auth/login` — 5 failed attempts per email
  per 15 minutes, then `429` with `retry_after_seconds`. Keyed on the address
  rather than the client IP, and cleared by a successful sign-in. The counter
  is in-process; see the note in `backend/.env.example` before running multiple
  workers.
- **Validation** is server-side for every field; uploads are checked by
  extension, by file signature, by byte size and by pixel dimensions, so a
  renamed executable is rejected and a 2×2 frame is not accepted as a
  condition photograph.
- **Queries** go through the SQLAlchemy ORM or bound parameters — string
  interpolation into SQL appears nowhere in the codebase.
- **Errors** never carry a traceback, a SQL fragment, or a database value. A
  unique-constraint conflict names the constraint but not the offending row,
  because PostgreSQL's `DETAIL` line quotes the row's values.
- **Secrets** live only in `backend/.env`. `show_config.py` and
  `as_safe_dict()` redact them, and the DSN is masked to show host but never
  credentials. The frontend receives only `VITE_*` values, which are public by
  definition.
- **Audit logging** of domain actions arrives with the modules that produce
  them (Phase 10); request-level access logs with a correlatable request id are
  already active.

## 12. Honesty statement: what is real and what is simulated

This project is built for academic evaluation, so the boundary between real and
simulated is stated explicitly rather than blurred.

| Component | Status |
| --- | --- |
| React ↔ Flask ↔ PostgreSQL communication | **Real** — verified by the live System Status page and the test suite |
| Password hashing, JWT, RBAC | **Real**, with adversarial tests |
| OpenCV image pipeline | **Real** — 13 steps, wired to `POST /api/inspections/*` and exercised end to end |
| Scratch/dent detection | **Classical computer-vision heuristic**, *not* a trained deep-learning model. Unverified findings are labelled "Potential Damage". See [`docs/AI-INTEGRITY.md`](docs/AI-INTEGRITY.md) |
| Geofence / suspicious-movement detection | **Rule engine** with stated thresholds, *not* a learned model. Every verdict stores the inputs that produced it. See [`docs/AI-INTEGRITY.md`](docs/AI-INTEGRITY.md) §4a |
| Security classifier (ML layer) | **Prototype trained on synthetic telemetry** — the one learned model here, and it is advisory only. It may *raise* a fix's verdict and may never lower it, so a geofence breach always alerts regardless of its confidence. Never represent it as a production model. See [`docs/AI-INTEGRITY.md`](docs/AI-INTEGRITY.md) §4b |
| Retrieval-time prediction | Will be a **statistical model fitted on synthetic historical data**, with its confidence interval reported |
| GPS coordinates | **Simulated** unless a hardware feed is configured — but provenance is stored *per fix* (`SIMULATED` / `HARDWARE` / `MANUAL`), not inferred from a deployment-wide setting, and the UI labels each reading |
| Vehicle photos | Real uploads when available; synthetic demo images otherwise, always flagged as demo data |

Nothing in the UI claims a capability that the backend does not implement.

## 13. Build roadmap

| Phase | Module(s) | Status |
| --- | --- | --- |
| 1 | Project structure, configuration, database, security core, tooling | ✅ **Complete** |
| 2 | Authentication (A), Customer Management (B), `users` table, audit logging for auth | ✅ **Complete** |
| 3 | Vehicle Management (C), `vehicles` table | ✅ **Complete** |
| 4 | Digital Valet Ticket (D), QR Code (E), Custody sessions, `valet_tickets`, `custody_sessions`, `parking_zones` | ✅ **Complete** |
| 5 | Vehicle Condition Capture (F), Before/After Comparison (G) — OpenCV | ✅ **Complete** |
| 6 | AI Scratch/Dent Detection (H) with explainable output | ✅ **Complete** |
| 7 | GPS/Telemetry (I), Geofencing (J), Suspicious Movement Detection (K) | ✅ **Complete** |
| 8 | Security Alerts (L) | ✅ **Complete** — delivered with Phase 7, since a geofence that cannot raise an alert is only half a feature |
| 9 | ML security classification layered over the geofence rules | ✅ **Complete** — prototype on synthetic data, escalate-only |
| 9b | Predictive Vehicle Retrieval (M) | ✅ **Complete** — ridge regression on synthetic history, MAE 1.73 min vs a 2.69 min baseline |
| 10 | Customer Dashboard (N) + Valet Operator Dashboard (O) | ✅ **Complete** — both server-composed; the operator screen adds arrivals, inspections, zones and confirmation-gated actions |
| 10a | Manager Dashboard (P) + Reports (Q) | ✅ **Complete** — server-side aggregates, five charts with declared sample sizes, four tables, four filters, CSV/JSON export |
| 11 | Centralized alert feed + notification abstraction | ✅ **Complete** — 5 categories, per-recipient read state, role-scoped feed, in-app delivery with email/SMS/push stubbed behind a channel interface |
| 10b | Audit log browser (R) | ✅ **Complete** — 14-event lifecycle vocabulary, database-enforced append-only trail, manager/admin viewer with server-side filters, per-custody timeline that reports its own gaps |
| 11b | API Documentation (S), expanded Testing (T), end-to-end demo script | ⬜ |

Each phase follows the same discipline: inspect existing code, state which
files change, implement, run tests and lint, fix, summarise.

## 14. Troubleshooting

**`psycopg2.OperationalError: connection refused`**
PostgreSQL is not running. On Windows: `services.msc` → start
*postgresql-x64-17*. Confirm with `pg_isready -h localhost -p 5432`.

**`permission denied for schema public`**
The role does not own the database. Recreate it with `OWNER valet_user`, or
`GRANT ALL ON SCHEMA public TO valet_user;`.

**`JWT_SECRET_KEY must be at least 32 characters`**
Production mode refuses to start with a weak secret by design. Generate one
with `python -c "import secrets; print(secrets.token_urlsafe(64))"`.

**The React page shows "Backend unreachable"**
Flask is not listening on the port in `VITE_PROXY_TARGET` (default
`http://127.0.0.1:5000`). Start `python utils/scripts/run_api.py`.

**`Activate.ps1 cannot be loaded`**
Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first.

**Port already in use**
`netstat -ano | findstr :5000` then `taskkill /PID <pid> /F`; or start with
`python utils/scripts/run_api.py --port 8000` and set
`VITE_PROXY_TARGET=http://127.0.0.1:8000` in `frontend/.env`.

---

### Academic disclosure

This is an academic MVP. It demonstrates a complete, honest architecture and a
working custody-transparency workflow. It is not hardened for real-world
deployment: production use additionally needs HTTPS termination, token
revocation, rate limiting, backup/restore procedures and independent security
review.
>>>>>>> bf0ec5c (Update project code)

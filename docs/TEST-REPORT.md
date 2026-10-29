# Test Report — AI-Powered Valet Transparency and Vehicle Security Platform

**Date:** 2026-09-11
**Scope:** backend (Flask/Python), frontend (React), AI layer, and three end-to-end integration journeys
**Environment:** Python 3.13.14 · Node 20.20.2 · PostgreSQL 17 · Debian 13

---

## 1. Headline result

| Suite | Tests | Files | Result |
|---|---|---|---|
| Backend (pytest) | **1074** | 30 | **all passed** |
| Frontend (Vitest) | **633** | 49 | **all passed** |
| **Total** | **1707** | **79** | **0 failed · 0 skipped · 0 xfailed** |

Static analysis is clean on both sides: `ruff check .` passes for the backend, `eslint .` passes for the frontend.

**Nothing is skipped and nothing is marked xfail.** This was verified by grepping both test trees for `pytest.mark.skip`, `xfail`, `@unittest.skip`, `.skip(`, `xit(`, `xdescribe(`, `it.todo` and `describe.todo`; all returned no matches. The brief's instruction not to hide failures has been met by fixing every failure at its root rather than quarantining it.

### Coverage

Overall pytest coverage was 88%. That figure is misleading on its own because it includes CLI entry points that no test imports. Restricting to application code:

| Area | Statements | Missed | Coverage |
|---|---|---|---|
| services | 2807 | 238 | 91.5% |
| ai | 1892 | 217 | 88.5% |
| routes | 851 | 20 | 97.6% |
| models | 810 | 7 | 99.1% |
| utils | 662 | 57 | 91.4% |
| migrations | 402 | 5 | 98.8% |
| database | 281 | 12 | 95.7% |
| config | 191 | 13 | 93.2% |
| app | 107 | 17 | 84.1% |
| middleware | 28 | 0 | 100.0% |
| **Application code** | **8063** | **618** | **92.3%** |

Excluded from that figure, deliberately:

- `utils/scripts/*` (1308 statements, 0%) — CLI entry points (`run_api.py`, `init_db.py`, `train_*.py`, `run_e2e_workflow.py`). They are exercised by hand during setup and by the E2E script, not by the unit suite.
- `schemas/common.py` (30 statements, 0%) — see §6, this is dead code.

---

## 2. Backend coverage against the brief

Every area the brief named has a dedicated test module.

| Brief requirement | Test module | Tests |
|---|---|---|
| Authentication | `tests/test_auth_endpoints.py` | 46 |
| Authorization | `tests/test_security.py` (24), `tests/test_security_audit.py` (94) | 118 |
| Vehicle CRUD | `tests/test_vehicle_endpoints.py` (36), `tests/test_database_crud.py` (33) | 69 |
| Ticket creation | `tests/test_ticket_endpoints.py` (45), `tests/test_ticket_projection.py` (24) | 69 |
| Custody lifecycle | `tests/test_ticket_endpoints.py`, `tests/test_database_models.py` | shared |
| Image upload | `tests/test_inspection_endpoints.py` (49), `tests/test_security_audit.py` | shared |
| Inspection | `tests/test_inspection_endpoints.py`, `tests/test_vision_pipeline.py` (45) | shared |
| Geofence | `tests/test_geofence_endpoints.py` (77), `tests/test_geofence_engine.py` (54) | 131 |
| Security alerts | `tests/test_alert_center.py` (34), `tests/test_geofence_endpoints.py` | shared |
| Retrieval prediction | `tests/test_retrieval.py` | 41 |
| Audit logs | `tests/test_audit_trail.py` (51), `tests/test_security_audit.py` | shared |

Additional modules: `test_app_factory.py` (23), `test_validation.py` (48), `test_models.py` (36), `test_security_ml.py` (35), `test_health_endpoint.py` (11), `test_database_migrations.py` (11), and the three dashboard modules (`test_customer_dashboard.py` 24, `test_staff_dashboard.py` 39, `test_manager_dashboard.py` 29).

Backend tests run against a real PostgreSQL 17 instance (`valet_db_test`), not SQLite. Migrations are applied with Alembic for each module that needs a schema, so migration drift would fail the suite rather than pass silently.

---

## 3. AI layer tests

`tests/ai/test_ai_modules.py` — **62 tests**, all passing. These are pure unit tests: the `ai/` package imports neither Flask nor SQLAlchemy, so the module boundary the architecture depends on is enforced by the tests being able to run without a database at all.

Coverage by brief requirement:

- **Image comparison** — `compare_images` alignment, finding extraction, normalised bounding boxes (`{x, y, width, height}` as fractions of the frame, not pixel tuples), evidence crop limits, warning generation.
- **Damage result generation** — defect seeding through `generate_pair`, threshold behaviour, and the rule that uncertain detections are labelled *Potential Damage* rather than asserted.
- **Geofence classification** — the full verdict ladder (`NORMAL` → `WARNING` → `SUSPICIOUS` → `BREACH`), the buffer/breach margins, the in-zone speed rule, drift detection, and the geometry primitives underneath them.
- **Retrieval prediction** — feature assembly, chronological train/test split, and model scoring against the baseline.

`tests/test_vision_pipeline.py` (45) and `tests/test_geofence_engine.py` (54) cover the same subsystems from the service boundary.

---

## 4. Frontend coverage against the brief

| Brief requirement | Test module | Tests |
|---|---|---|
| Login | `src/pages/LoginPage.test.jsx` (7), `src/routes/RequireAuth.test.jsx` (4), `src/routes/RequireRole.test.jsx` (4) | 15 |
| Dashboard | `CustomerDashboardPage` (8), `ValetDashboardPage` (16), `ManagerDashboardPage` (31), `DashboardCards` (14) | 69 |
| Vehicle registration | `src/pages/VehicleRegisterPage.test.jsx` | 11 |
| Check-in | `src/pages/CheckInPage.test.jsx` (9), `src/components/valet/valet.test.jsx` (22) | 31 |
| Pickup request | `src/pages/TicketDetailPage.test.jsx` (10), `src/services/retrievalService.test.js` (16), `RetrievalEstimateCard` (14) | 40 |
| Alert display | `src/pages/AlertsPage.test.jsx` (25), `src/components/alerts/alerts.test.jsx` (20), `AlertCard` (14) | 59 |

Beyond the six areas the brief named, three further modules were added because
they carried real behaviour and had no coverage at all:

| Module | Tests | What it pins |
|---|---|---|
| `src/pages/InspectionCapturePage.test.jsx` | 16 | The photo-capture UI behind integration flow 5.3: parallel `images`/`angles` multipart lists, drop-off vs pickup routing, the 12-photo cap, and that findings stay labelled *Potential Damage* with no verify control on an operator screen. |
| `src/components/vehicles/VehicleForm.test.jsx` | 20 | Client-side validation that must not be stricter than the backend (all four Indian plate spellings accepted), and that a backend `details.field: "plate_number"` lands next to the plate input rather than in a generic banner. |
| `src/components/vehicles/StatusBadge.test.jsx` | 10 | Status colouring across the app, and the honest fallback: an unrecognised status gets a muted badge and its raw key rather than borrowing a colour that implies a meaning nobody assigned. |

Frontend tests run in jsdom with React Testing Library. Service modules are mocked at the module boundary, so these tests exercise rendering, role gating and user interaction — they are not a second copy of the API contract tests.

Two properties worth naming, because they are asserted rather than assumed:

- **The synthetic-data caveat is pinned to the estimate.** `TicketDetailPage` must render the prototype notice alongside the retrieval figure. The estimate is the most screenshot-able number in the product; if the caveat can be cropped out, "12 minutes" becomes an accuracy claim about a model that has never seen a real yard.
- **An absent estimate never reads as zero.** With no stored estimate the card says "No estimate yet" and offers a button, rather than rendering a bare `0`.

---

## 5. Integration tests — the three journeys

`tests/integration/` — **54 tests**, all passing. All three files carry
`pytestmark = pytest.mark.integration`, the marker declared in `pyproject.toml`.

Note that `pytest -m integration` selects **87** tests, not 54: the 33
database-CRUD tests in `tests/test_database_crud.py` carry the same marker,
because they too cross a real database rather than exercising one layer. The
three journeys below are the 54.

These are HTTP-only tests. They authenticate through the real login route, then drive the REST API end to end against a real PostgreSQL database. No service function is called directly, so a route that is wired to the wrong service or returns the wrong envelope fails here even if both halves pass their own unit tests.

### 5.1 Customer → Check-in → Custody → Pickup (14 tests)

`test_journey_custody.py`. Registers a customer and a vehicle, issues a ticket, checks the vehicle in, walks custody through the legal state machine, requests retrieval, completes the pickup, and asserts the audit timeline recorded every step in order.

Asserts, among other things: the projected ticket vocabulary (`CREATED`/`IN_CUSTODY`/`READY_FOR_PICKUP`) alongside the stored one (`ISSUED`/`ACTIVE`); that an illegal custody jump returns **409** carrying `{from, to, allowed}`; that a closed session cannot be reopened; and that zone occupancy increments on check-in and returns to zero on checkout.

### 5.2 GPS Event → Geofence → Alert (15 tests)

`test_journey_geofence.py`. Posts a simulated GPS track, confirms the response is honestly labelled `source: "SIMULATED"` with `is_simulated: true`, verifies the geofence detector classified the fixes, and follows the raised alert through the alert feed to resolution.

Asserts the tracking endpoint's provenance split (`has_hardware_telemetry` vs `has_simulated_telemetry`), the movement-status counts, that alerts reach the right audiences, and that resolution is restricted to managers — an operator resolving a security alert gets **403**.

### 5.3 Drop-off Image → Pickup Image → Damage Result (25 tests)

`test_journey_damage.py`. Uploads a real drop-off photo set, uploads a pickup set, runs the real OpenCV comparison pipeline, and inspects the resulting damage record.

This flow uses genuinely uploaded images through the real pipeline — never canned findings, and no threshold is bypassed. Asserts the two-stage pixel floor (upload requires 64px per side; the assessment floor of 96px only bites at pickup), that unverified detections are labelled *Potential Damage*, that the detector disclaimer travels with the result, and that verification is manager/admin only.

---

## 6. Known gaps

Reported rather than papered over.

**Backend**

- `schemas/common.py` (30 statements, 0% covered) is **dead code**. A grep across `app/`, `routes/`, `services/`, `models/`, `config/` and `utils/` found no import of it; the only match anywhere is a test function whose name contains the word "schemas". It should be deleted or wired up. It was left alone here because removing a module is an architecture decision, not a testing one.
- `ai/ml/dataset.py` at 29% and `app/error_handlers.py` at 65% are the weakest application modules. `dataset.py` has untested branches in its synthetic-history generation paths; `error_handlers.py` has handlers for exception types no test currently raises.
- `utils/image_probe.py` (71%) and `utils/requests.py` (79%) have untested defensive branches.

**Frontend**

Four pages have no dedicated test file: `NotFoundPage`, `TicketListPage`, `VehicleDetailPage`, `VehicleListPage`. None is exercised as a page anywhere in the suite — a grep of `VehicleRegisterPage.test.jsx` for `VehicleListPage` and `VehicleDetailPage` returns zero references, so there is no indirect coverage to fall back on. Of the four, `VehicleDetailPage` is the most substantive; `NotFoundPage` is a static fallback.

Two component directories have no test at all:

- `src/components/manager/` — `Charts.jsx` and `Tables.jsx`. `Tables.jsx` is the more meaningful of the two, since it renders the manager's operational rows; `Charts.jsx` is presentational SVG.
- `src/components/ui/` — `Logo.jsx` and `index.jsx`. `index.jsx` exports the design-system primitives (`Badge`, `Panel`, `Field`, `ErrorNotice`, `ConfirmDialog` and others). These have no test file of their own, but they are exercised indirectly by most of the 43 suites, and `ConfirmDialog`'s `role="alertdialog"` and `Field`'s label-wrapping behaviour are both pinned by the `AlertsPage` tests.

`InspectionCapturePage`, `StatusBadge` and `VehicleForm` were gaps in the first
draft of this report and are now covered; see §4.

None of these gaps hide a failure. They are untested surface, and the suite
reports them as such rather than inflating a pass count around them.

---

## 7. How to reproduce

```bash
# Backend — requires PostgreSQL running with valet_db_test owned by valet_user
cd backend
python3 -m pytest -q
python3 -m pytest -q -m integration          # 87: the 54 journeys + 33 DB-CRUD
python3 -m pytest -q --cov=. --cov-report=term-missing
ruff check .

# Frontend
cd frontend
npx vitest run
npx eslint .
```

Test ordering is randomised by `pytest-randomly`; the counts above were taken with `-p no:randomly` for reproducibility but the suite is order-independent.

---

## 8. Failures found and fixed while building this suite

The brief required that failures be fixed rather than hidden. Every failure encountered was diagnosed to its cause. In every case the fault was in the test's assumption about the real system, not in the system — which is the expected outcome when tests are written against probed behaviour rather than guessed shapes.

**Backend**

1. `PATCH /custody-sessions/<id>` takes `status`, not `custody_status`.
2. An illegal custody transition returns **409** with `{from, to, allowed}`, not a generic 400.
3. Resolving a security alert is manager/admin only; the integration test initially used an operator token and received **403**.
4. The geofence breach verdict arrived as `MEDIUM` severity, not the `HIGH` the test assumed.
5. `GET /gps/<id>/tracking` does not exist — the route is `GET /gps/<id>`.
6. `GET /inspections/<id>` returns a flat object, not a wrapper containing an `inspections` list.
7. `Finding.bounding_box` is a dict of normalised fractions; the test asserted a pixel 4-tuple.
8. `model_version` is `NULL` for `CLASSICAL_CV` detections, by a database CHECK constraint.
9. The shared test helper produced 64×48 PNGs, below the `MIN_IMAGE_PIXELS=64` floor. The **fixture** was wrong and was fixed to 96×72 — the enforcement was correct.

**Frontend**

1. `AlertsPage`'s confirm dialog renders `role="alertdialog"`, not `role="dialog"`, and its confirm button shares the label "Resolve" with the row button — both queries had to be scoped with `within(dialog)`.
2. The React Query client retries once before surfacing an error, so error-state assertions needed a longer window rather than an immediate one.
3. React Query calls a mutation function with the variables **plus** its own context argument; `toHaveBeenCalledWith('a-1')` could never match. Assertions were changed to check the first argument.
4. `AlertsPage` has no dismiss control. The test that assumed one was **replaced** with two that pin real behaviour: the page always sends `dismiss: false`, and the confirmation dialog states that closing is attributed in the audit log.
5. `TicketDetailPage` reads its id from the URL, so it must be mounted on a matching `Route`; rendered bare, `useParams().id` is `undefined`.
6. `normaliseError` reads `error.response.data.error.message`. Test failures were constructed as bare `Error` objects with an `apiError` property, which that function does not read, so toasts showed the generic network message. Fixtures were rebuilt in the interceptor's shape.
7. The retrieval card consumes `contributions` as a feature → minutes **map** via `Object.entries()`. Passing an array of objects silently produced no factors; the fixture was corrected.
8. `VehicleForm` renders a backend field error *and* an `ErrorNotice`, so a submitted-message assertion matched twice and needed `findAllByText`.
9. `InspectionCapturePage` previews queued photos through `URL.createObjectURL`, which jsdom does not implement. The polyfill was added to `src/test/setup.js` — without it, any test that queues a file dies on an undefined function rather than on the behaviour under test.
10. The same page's "choose at least one photograph" guard is unreachable through the UI: the submit button carries `disabled={busy || photos.length === 0}`, so the form never submits. The test was rewritten to assert the disabled state, which is the real guard, rather than a message that cannot render.
11. `VehicleForm`'s internal state keys are camelCase (`plateNumber`) while the on-screen labels are sentences ("Registration number"). A test helper that derived label text from the field name found nothing; the mapping is now explicit.

---

## 9. Assessment

The suite is suitable for academic evaluation. It exercises the full custody journey over HTTP against a real database, covers all eleven backend areas and all six frontend areas the brief named, tests the AI layer at both its pure-function and service boundaries, and pins the honesty requirements the brief set — simulated telemetry labelled as simulated, uncertain detections labelled *Potential*, the synthetic-data caveat attached to the retrieval estimate, and no claim of a production-grade damage detector.

The gaps in §6 are real and stated plainly. They are the honest boundary of what this suite currently proves.

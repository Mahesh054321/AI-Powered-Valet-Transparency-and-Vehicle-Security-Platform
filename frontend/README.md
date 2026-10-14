# Frontend — React SPA

React 19 + Vite 8, React Router 7, TanStack Query 5, Leaflet for maps, Recharts
for reporting, Vitest + jsdom + Testing Library for tests. Plain CSS design
system — no CSS framework, no CDN fonts, no runtime network dependency for
styling.

Full installation steps live in the [root README](../README.md).

## Quick start

```powershell
Copy-Item .env.example .env
npm install
npm run dev            # http://localhost:5173
```

The dev server proxies `/api` to the Flask backend (`VITE_PROXY_TARGET`,
default `http://127.0.0.1:5000`), so the browser only ever makes same-origin
requests. No CORS configuration is needed in development.

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Vite dev server with HMR on `:5173` |
| `npm run build` | Production bundle in `dist/` |
| `npm run preview` | Serve the built bundle locally |
| `npm test` | 40 Vitest tests (jsdom + Testing Library) |
| `npm run test:watch` | Watch mode |
| `npx eslint .` | Lint |

## Layout

```
src/
├── main.jsx              React root: BrowserRouter > QueryProvider > AuthProvider
├── App.jsx               renders <AppRoutes/>
│
├── config/
│   └── appConfig.js      the ONLY reader of import.meta.env
│
├── services/             HTTP — no React imports, so no import cycles
│   ├── apiClient.js      axios instance, token injection, envelope unwrapping,
│   │                     onUnauthorized() listener registry
│   ├── endpoints.js      every backend URL in one module
│   ├── healthService.js  fetchHealth(), fetchReadiness()
│   ├── vehicleService.js vehicles CRUD
│   ├── ticketService.js  tickets, custody sessions
│   ├── uploadService.js  image upload
│   ├── directoryService.js customers + parking zones
│   ├── inspectionService.js condition capture, report, verification, demo
│   ├── geofenceService.js GPS telemetry, geofences, alerts, the simulator
│   ├── securityMlService.js prototype classifier: model status + ad-hoc scoring
│   └── retrievalService.js  retrieval estimates, what-if scoring, provenance
│
├── context/
│   ├── AuthContext.jsx   AuthProvider — token, identity, signIn, signOut
│   └── QueryProvider.jsx React Query client + defaults
│
├── hooks/
│   ├── useAuth.js        useAuth, useHomeRoute, useHasRole, useSignOut
│   └── useHealthStatus.js 15 s poll of /health and /readyz
│
├── utils/                pure functions, no React
│   ├── jwt.js            decodeJwt, identityFromToken, isTokenUsable
│   ├── tokenStorage.js   read/write/clear the token (localStorage)
│   ├── constants.js      ROLES, status vocabularies, damage and movement
│   │                     labels/tones, telemetry provenance labels
│   └── format.js         formatUptime, formatMegabytes, shortId, humaniseStatus,
│                         formatDateTime, formatTime
│
├── layouts/
│   └── MainLayout.jsx    sidebar + topbar + outlet (role-filtered menu)
│
├── routes/
│   ├── index.jsx         ROUTES table, isRouteVisibleTo(), AppRoutes
│   └── RequireRole.jsx   route guard (UX only; the API is authoritative)
│
├── components/
│   ├── ui/index.jsx      Panel, Badge, Field, DataRow, Spinner, ErrorNotice
│   ├── ui/Logo.jsx       inline SVG mark (no binary asset, no network)
│   ├── vehicles/         StatusBadge, VehicleForm
│   ├── tickets/          TicketCard
│   ├── inspections/      DamageCard
│   ├── retrieval/        RetrievalEstimateCard (Phase 9b)
│   └── tracking/         TrackingMap (Leaflet), AlertCard, MlVerdictCard,
│                         SecurityModelPanel
│
├── pages/
│   ├── LoginPage.jsx         wired sign-in form (posts to /auth/login)
│   ├── RegisterPage.jsx      customer registration (posts to /auth/register)
│   ├── SystemStatusPage.jsx  live probe of /api/health and /api/readyz
│   └── NotFoundPage.jsx
│
├── styles/               design system — see "The design system" below
│   ├── tokens.css        custom properties: palette, type scale, spacing,
│   │                     radii, shadows, motion, z-index, layout metrics
│   ├── base.css          reset, heading scale, links, form inheritance,
│   │                     utilities, focus-visible, skip link, print
│   ├── shell.css         sidebar / topbar / content frame, responsive rail→drawer
│   ├── components.css    the primitives: buttons, badges, panels, fields,
│   │                     tables, dialogs, toasts, notices, states, skeletons
│   ├── patterns.css      cross-page compositions (grids, pickers, meters,
│   │                     segmented controls, status counts)
│   └── pages/            one sheet per screen domain: auth, customer, valet,
│                         manager, tracking, alerts, inspections, audit,
│                         tickets, retrieval
├── assets/               static assets; README explains the inline-SVG policy
└── test/
    ├── setup.js          jest-dom matchers
    └── utils.jsx         renderWithProviders, buildToken, clearSession, adapters
```

Tests live next to the code they cover (`*.test.js[x]`).

### Why `services/` must not import React context

`apiClient.js` needs the token and needs to sign the user out on a 401, but it
must not import `AuthContext` — that would make `context → services → context`
a cycle. Instead:

- the token is read through `utils/tokenStorage.js` (a plain function), and
- a 401 is broadcast to listeners registered via `onUnauthorized(cb)`;
  `AuthProvider` is what subscribes and calls `signOut({ reason })`.

Keep it that way when adding new services.

## Testing

```powershell
npm test                # 423 tests across 36 files
```

Vitest runs against **jsdom**, so components are rendered into a real DOM.
Helpers live in `src/test/utils.jsx`:

- `renderWithProviders(ui, { route, token })` — wraps a component in the React
  Query provider, `AuthProvider`, and a `MemoryRouter` at the given route;
  `token` seeds localStorage so the app starts signed in.
- `buildToken({ role, email, expiresIn })` — builds a token with the same
  claims the backend issues, so role behaviour can be exercised without a
  server.
- `clearSession()`, `jsonResponse()`, `errorResponse()` — fixtures.

Four things worth knowing, each learned the hard way:

- To intercept HTTP, replace `apiClient.defaults.adapter` rather than spying on
  `apiClient.request`. axios's convenience methods rebind
  `Axios.prototype.request`, so an instance-level spy never fires.
- A stub adapter returning `{ status: 401 }` **resolves**, because `validateStatus`
  accepts it. To simulate an HTTP failure the adapter must **throw** an
  `AxiosError` carrying the response.
- `vi.spyOn` on an ES-module namespace does not affect a consumer holding a
  direct binding. Mock the module instead:
  `vi.mock(path, async (importActual) => ({ ...await importActual(), fn: vi.fn() }))`.
- Rendering `<Navigate>` requires a `<Routes>` tree with a matching route,
  otherwise the router has nothing to resolve and the worker runs out of memory.

React Query's `isLoading` is `isPending && isFetching`, which stays `true`
across retries after a failure. Any hook that exposes it should also expose a
real "still waiting for the first response" flag, or the UI pins itself on a
spinner while the backend is down. `useHealthStatus` handles this with
`isInitialising`.

## The design system

Styling is layered, and the layer order is load-bearing. `main.jsx` imports the
sheets in this sequence:

```
tokens → base → shell → components → patterns → pages/*
```

A later sheet may *compose* an earlier primitive. It must never *redefine* one:
a same-specificity duplicate later in the cascade silently wins, which is how a
design system quietly stops being one. If a screen needs a different button,
that is a new variant in `components.css`, not an override in a page sheet.

**`tokens.css`** is the only place a raw colour, size or duration appears. Every
other sheet refers to a custom property. Two consequences worth knowing:

- **Breakpoints cannot be tokens.** A custom property resolves too late to be
  used inside `@media`, so media queries repeat literal pixel values. This is
  deliberate, not an oversight.
- **An undefined custom property fails silently.** `var(--primary-stong)`
  produces nothing and raises no error, so a typo is invisible until the layout
  looks wrong. The check below guards against it.

**`components.css`** holds the primitives — `.btn`, `.badge`, `.panel`, `.field`,
`.table`, `.dialog`, `.toast`, `.notice`, `.skeleton`, `.empty-state`,
`.state-error`. Each has exactly one definition. **`patterns.css`** holds
compositions reused across pages (grids, pickers, meters, segmented controls).
**`pages/*.css`** holds layout for one screen domain and nothing else.

**Feedback is a component, not a convention.** `ToastProvider` renders a single
`aria-live` region and `useToast()` is how any mutation reports its outcome. The
region mounts with the app rather than with the first message, because a live
region that does not yet exist cannot announce the message that creates it.
Errors get a longer lifetime than confirmations: a failure that vanishes on the
same clock as a success is easy to miss, and a missed failure is
indistinguishable from a success nobody read.

### Verifying the stylesheet layer

Two checks are worth running after any styling change. Neither is automated yet;
both take a moment by hand.

Coverage — every static class used in JSX must be defined somewhere:

```bash
python3 - <<'PY'
import re, pathlib

used = set()
for p in pathlib.Path('src').rglob('*.jsx'):
    if p.name.endswith('.test.jsx'):
        continue
    for m in re.findall(r'className="([^"${}]+)"', p.read_text()):
        used |= {c for c in m.split() if c and not c.startswith('--')}

covered = set()
for p in pathlib.Path('src/styles').rglob('*.css'):
    covered |= set(re.findall(r'\.([A-Za-z][A-Za-z0-9_-]*)', p.read_text()))

print("uncovered:", sorted(used - covered))
PY
```

This scans literal `className="…"` strings only, so a class built by template
interpolation (`className={`finding--${tone}`}`) is invisible to it. Those are
covered by reading the variant set the component can produce, which is why each
such component names its variants in a comment.

Resolution — every `var()` must reference a defined property:

```bash
python3 - <<'PY'
import re, pathlib
defined, used = set(), set()
for p in pathlib.Path('src/styles').rglob('*.css'):
    t = p.read_text()
    defined |= set(re.findall(r'(--[A-Za-z0-9-]+)\s*:', t))
    used |= set(re.findall(r'var\(\s*(--[A-Za-z0-9-]+)', t))
print("undefined:", sorted(used - defined))
PY
```

Both should print an empty list. `vite build` parses every sheet but will not
flag either problem: an unknown class is simply unmatched, and an unknown custom
property simply resolves to nothing.

## Conventions

**Never read `import.meta.env` outside `config/appConfig.js`.** That keeps the
public surface of browser configuration in one auditable place.

**Never put a secret in `.env`.** Vite inlines every `VITE_*` value into the
bundle, so those values are public by definition. Backend secrets live only in
`backend/.env`.

**Call the API through `services/apiClient.js`.** The helpers (`apiGet`,
`apiPost`, `apiUpload`, …) unwrap the backend envelope and normalise errors
into `{ status, code, message, details, requestId }`, so components never parse
error shapes themselves.

**Server state goes in TanStack Query; client-only UI state goes in Zustand.**
Live tracking screens use `refetchInterval` rather than hand-rolled
`setInterval`. Authentication is deliberately *not* Zustand — it lives in
`AuthContext` so it is observable in tests and cannot drift from the token on
disk. Zustand is still a dependency for client-only UI state (wizard steps,
panel layout) in later phases; no module imports it yet.

## Screens

| Route | Screen | Status |
| --- | --- | --- |
| `/login` | Sign in | **Live** — `POST /api/v1/auth/login`, stores both tokens |
| `/register` | Create account | **Live** — `POST /api/v1/auth/register` (customer only) |
| `/vehicles` | My Vehicles | **Live** — list, search, paginate |
| `/vehicles/new` | Register a vehicle | **Live** — `POST /api/v1/vehicles` |
| `/vehicles/:id` | Vehicle details + history | **Live** — edit, archive, previous sessions |
| `/tickets` | Valet tickets | **Live** — filter by projected status |
| `/tickets/:id` | Digital ticket + QR | **Live** — timeline, custody moves, complete, cancel |
| `/checkin` | Check-in wizard | **Live** — the ten-step handover, staff only |
| `/condition` | Condition capture | **Live** — photo sets per angle, staff only |
| `/condition/:custodyId` | Condition report | **Live** — before/after, findings, manager verification |
| `/tracking` | Live tracking | **Live** — session picker |
| `/tracking/:custodyId` | Live tracking dashboard | **Live** — Leaflet geofence map, movement status, alerts |
| `/alerts` | Security alerts | **Live** — opens the tracking dashboard, where alerts live |
| `/status` | System Status | **Live** — polls `/api/health` and `/api/readyz` every 15 s |
| `/dashboard` | Customer Dashboard | **Live** — the default landing page for customers |
| `/valet` | Valet Operator Dashboard | **Live** — seven sections, confirmation-gated actions |
| `/manager` | Manager Dashboard | **Live** — metrics, five charts, four tables, filters, CSV/JSON export |
| `/audit` | Audit Log | **Live** — read-only log viewer and per-custody timeline, manager/admin only |
| `/reports` | — | Roadmap |

Unimplemented routes render an honest placeholder stating their phase; they
never fake data. The sidebar marks them `soon`.

## The notification bell and alert centre

A bell in the top bar shows the unread count; `/alerts` is the full feed with
filters and actions.

**The count comes from `/feed/summary`, not from the feed.** Fetching every
alert to render one number would be wasteful, so the badge costs one indexed
count. The panel fetches the feed only once it is open.

**It polls, and says so.** `refetchInterval` is a timer, not a push transport.
The panel prints "polled every 15s" rather than implying the badge is live,
because a number that updates on an interval is not real-time and should not be
mistaken for one during a demonstration.

**Read and resolved are separate controls.** Marking read is a personal
acknowledgement — it changes nothing about the alert, so clearing your own badge
cannot clear a colleague's queue. Resolving is attributed and audited. Keeping
them visually distinct stops a quick "clear my badge" from becoming an
unreviewed closure of a security incident.

**The resolve button comes from `can_resolve`, not from the role string.** The
summary endpoint reports whether this actor may close alerts, so the UI cannot
drift from the backend's rule. A button that returns 403 is worse than no
button.

Resolving goes through `ConfirmDialog`, which names the category, severity,
message and vehicle. A bare "are you sure?" on a security incident is decoration
rather than a check.

There is no client-side role filtering anywhere in this feature. The backend
decides what an actor may see; hiding rows that were already sent would be
cosmetic, and a client-side filter is one refactor away from becoming the only
filter.

## The manager dashboard

`/manager` is read-only analytics over the whole operation, for
`VALET_MANAGER` and `ADMIN`. One composed read
(`GET /api/v1/manager/dashboard`) supplies eight metric cards, five charts and
four tables, all filtered by the same query string.

**Charts refuse to draw when the data cannot support them.** The backend sends
`n`, `min_points` and `sufficient` for every series, and `ChartFrame` honours
them — rendering "not enough data to chart, at least N are needed" in place of
a plot. The decision lives in one shared frame rather than in each chart, so
the next chart written cannot quietly forget it. Recharts will happily draw
anything it is handed; the judgement about *whether* to draw is ours.

The sample size is printed beside every chart rather than hidden behind a
tooltip. "Average retrieval time: 14.7 min" and "…computed from 3 retrievals"
are different claims, and only the second can be evaluated.

`ChartFrame` defers to the backend's `sufficient` verdict when present. An
earlier version also required `series.length >= min_points`, which silently
refused a custody-duration histogram resting on 320 records but falling in a
single two-hour bucket — conflating bucket count with sample size.

**The screen is read-only by design.** There is no action column. Changing a
custody state belongs on the operator dashboard, behind its confirmation dialog
and the state machine that owns the transition; a second place to mutate the
same row would be a second place to get it wrong.

**Reports come from the same endpoint.** CSV and JSON both hit
`/manager/report`, which is the dashboard's own query path, so a downloaded
report cannot contradict the screen it summarised. When the dataset includes
generated history the download says so, because a CSV cannot carry the banner
the page shows.

Recharts is stubbed in tests: like Leaflet it measures real DOM geometry and
draws nothing under jsdom. The tests therefore assert the draw/don't-draw
decision, which is plain logic and the part this codebase is responsible for.

## The valet operator dashboard

`/valet` is where a valet operator lands. It is one composed read
(`GET /api/v1/staff/dashboard`) polled every 5 seconds, rendered as seven
sections: incoming vehicles, active vehicles, pending pickups, retrieval queue,
security alerts, vehicle inspections and parking zones.

**The page performs no writes of its own.** Every action calls the endpoint
that already exists for it — `PATCH /custody-sessions/:id` to move a car,
`POST /tickets/:id/complete` to finish a handover, `POST /inspections/pickup`
for photos, the alert routes for triage. That keeps one state machine and one
audit path; a dashboard with its own write logic would be a second,
less-tested copy of those rules.

**Buttons come from `capabilities`, not from the role.** The backend publishes
what this actor may do, derived from the same role tuples the endpoints
enforce. A valet operator may move a car but may not close a security alert.
Showing a button that returns `403` is worse than showing none: it tells an
operator the action is available and then refuses them in front of a customer.
`ValetDashboardPage.test.jsx` asserts both directions.

**Important actions go through `ConfirmDialog`**, which names the vehicle and
customer on the dialog itself. A bare "are you sure?" on a handover is
decoration rather than a check. It is a real component rather than
`window.confirm` because a native confirm cannot show *what* is about to
happen, cannot be focused, and blocks the thread. Escape and a backdrop click
cancel rather than confirm: the safe default when someone dismisses a dialog
without reading it is to do nothing.

**Three section states, rendered differently.** `empty` is a real answer from
the database and is styled as one — a quiet morning must not look like an
outage. `unavailable` means a sub-query failed and is styled as a fault that
says what could not be read. `SectionShell` centralises this so no section can
quietly forget its empty state and render as a blank box, which reads as
"nothing wrong". An unrecognised state is treated as empty and says so, rather
than falling through to blank.

**GPS is labelled per row.** A session with no fixes says "No fix recorded"
rather than implying a clean last position, and each row shows its fix source,
because a track can mix real and simulated fixes and an operator needs to know
which row is which before acting on a movement verdict.

## The customer dashboard

`/dashboard` is where a customer lands after signing in (`ROLE_HOME` maps
`CUSTOMER` to it). It renders five cards — Vehicle Status, Security Status,
Current Location, Estimated Retrieval and Condition Status — plus a **Request
Vehicle** action.

**One call, composed on the server.** Everything the page shows comes from
`GET /api/v1/customers/me/dashboard`. The browser does not fan out to ten
endpoints: ten parallel reads are taken at ten different moments, so a
dashboard could show a custody status from before a valet moved the car beside
a queue position from after. One snapshot cannot disagree with itself, and the
ownership rules are applied once rather than being re-derived per card.

**Every section states its own condition.** Each of `custody`, `vehicle`,
`zone`, `location`, `security`, `condition`, `retrieval` and `actions` carries
a `state` of `ok`, `empty` or `unavailable`, plus a `reason`. `empty` is a real
answer from the database, not a failure — a customer with no vehicle in custody
gets a `200` and a sentence, never five blank cards that read as "all clear".
`unavailable` means a sub-query failed and says why, so one broken dependency
degrades one card instead of the page.

**The button cannot offer an impossible action.** `actions.can_request` is
derived from the same `ALLOWED_CUSTODY_TRANSITIONS` table the backend enforces,
so the button greys out for exactly the reason the API would return `409`, and
prints that reason.

**The retrieval estimate is always captioned.** `retrieval.disclaimer` is
rendered in the card's footer unconditionally, alongside a `Synthetic-data
model` badge. It comes from a model trained on generated history and must never
be cropped into an accuracy claim.

**The valet side polls, and says so.** `/valet` reads
`GET /api/v1/retrieval/queue` every 5 s. The page states plainly that it polls
rather than being pushed to, because this project has no websocket channel and
a valet needs to know how stale the queue can be. Calling a 5-second poll
"real-time" would be the kind of overclaim this codebase is otherwise careful
to avoid.

## Live tracking and security

`/tracking/:custodyId` is the Phase 7 dashboard. One call —
`GET /api/gps/{custody_id}` — returns everything it renders, so the map is never
briefly drawn from a half-loaded combination of responses. It polls every 10 s.

**The map draws the stored polygon, never a circle recomputed from
`radius_metres`.** The boundary the backend evaluated against is the ring; a map
that disagreed with the verdict would be the one thing a security view must
never be. The radius-derived circle is drawn only as the dashed GPS tolerance
band, and only when the zone really is a circle.

Leaflet stores `[lat, lng]`; the API stores GeoJSON `[lon, lat]`. That swap
happens exactly once, inside `TrackingMap`.

Markers are CSS `divIcon`s rather than Leaflet's stock PNG icons, whose asset
paths bundlers routinely break — and because they are CSS, the colour can follow
the movement status.

**Simulated telemetry is labelled three ways**, all driven by the response's
own flags rather than a build-time setting:

- a persistent banner, from `has_simulated_telemetry` and `telemetry_note`;
- the current reading, labelled individually when `is_simulated`;
- the source list in the track summary, and each fix's own `source`.

A session can legitimately contain both hardware and simulated fixes, so a
page-level flag alone would be a lie about at least one of them.

**Triage controls are gated by role.** Managers and administrators can
acknowledge, resolve, or dismiss an alert. A valet operator can watch but not
act — the person who drove the vehicle is not the one who dismisses the alert
about it. This mirrors the backend's own rule; the guard is UX only, and the API
is authoritative.

Dismissal is a separate button from resolution on purpose. "We checked and the
vehicle was fine" and "this alert should not have fired" are different claims,
and the second is feedback about the detector.

The development simulator panel appears only for staff who may post telemetry.
It sends a scenario and a seed; the same seed always produces the same track, so
a demo is repeatable.

Map tiles come from OpenStreetMap and need network access. The boundary, track
and markers are drawn locally, so the dashboard still works — without the street
map underneath — on an offline machine.

### The prototype classifier on the tracking page

Two components, both staff-only, both styled as visually subordinate to the rule
verdict they sit under — a prototype trained on synthetic data should not be able
to shout over a measurement.

- `RetrievalEstimateCard` (Phase 9b) shows **Estimated Retrieval**, **Queue
  Position** and **Recommended Dispatch** on the ticket detail page. Two rules it
  does not break: the synthetic-data notice is always rendered, because this is
  the most screenshot-able number in the product and a cropped caveat turns the
  estimate into an accuracy claim; and the estimate is shown as a range when the
  backend supplies bounds, because a single figure implies a precision the model
  does not have. Per-feature contributions are listed underneath in minutes so
  the number can be argued with.
- `MlVerdictCard` renders the classifier's opinion on the latest fix. The
  probability always sits next to the label, because "suspicious at 51%" and
  "suspicious at 99%" are not the same claim and one badge would hide the
  difference.
- `SecurityModelPanel` shows what the model *is*: version, algorithm, training
  metrics, the baseline those metrics must beat, and the confusion matrix, with
  the synthetic-data disclaimer attached to the numbers rather than in a footer
  somewhere.

Three states are rendered explicitly rather than collapsed:

- **the classifier dissented** — the rules flagged something and the model
  thought it was fine. The note says the rule verdict stands and that any alert
  still does, because a prototype's dissent is not grounds to quiet a
  measurement;
- **the classifier escalated** — it raised the verdict, and the note says so and
  says it can never lower one;
- **the classifier agreed** — no note at all. Explaining an agreement would
  train reviewers to skim past the notes that matter.

An untrained deployment renders as an ordinary state with the training command,
not as an error. Rules-only is a supported way to run the platform, and a red
banner would wrongly suggest the security module is broken.

The disclaimer lives in `utils/constants` as `ML_PROTOTYPE_NOTICE` as well as in
the API payload, so a cropped screenshot of the dashboard still cannot read as a
production model's verdict.

## The audit trail viewer

`/audit` is the one screen in the app that **cannot change anything**, and it says
so.

The backend registers no write route for the trail and the `audit_logs` table
rejects `UPDATE` and `DELETE` at the trigger level. Rather than simply omitting
edit controls — which leaves a reader wondering whether they are hidden or
absent — the page renders the `mutable: false` flag and `mutability_note` the
API returns. A log viewer that is quietly read-only is less trustworthy than one
that declares it.

`src/services/auditService.js` exposes reads only. There is deliberately no
`updateAuditEntry`: a service offering a call that can only fail would invite a
UI built on the assumption that the log is editable.

**Two modes, two orders.** The log runs newest-first because it is a worklist —
"what happened since I last looked". The timeline runs oldest-first because it
is a narrative — checked in, parked, requested, handed back. They read the same
table, and serving both in one order would make one of the two awkward.

**Server-side everything.** Filters, paging and the manager/admin gate all
happen in the query. There is no client-side role check and no client-side
filtering: hiding rows that were already sent is cosmetic, and a viewer that
filtered in the browser would still have transferred other people's email
addresses and IP addresses to a machine that should never have had them.

**Filter dropdowns come from the data.** `/audit/filters` returns the distinct
actions, entity types and outcomes actually present. A dropdown offering an
action that has never occurred is noise; one missing an action that has is a
bug.

**The timeline shows its gaps.** `lifecycle_progress` reports all fourteen
lifecycle events with a boolean each, and `lifecycle_missing` names the absent
ones. A custody with no pickup photographs says so explicitly, because a
timeline that only rendered what happened would let a missing step pass for a
step that was never required.

**Scope is labelled.** Ticket and vehicle registration predate the custody
session, and a login belongs to a person rather than a car. The backend labels
each entry `custody`, `provenance` or `login`, and the gutter shows it, so
nothing looks like part of the custody that is not.

Components live in `src/components/audit/` (`CustodyTimeline`, `TimelineEvent`,
`LifecycleProgress`); the page is `src/pages/AuditLogPage.jsx`.

## Authentication

Sign-in and registration are wired to the real API — there are no simulated
sessions any more. An earlier revision offered `alg: none` "demo session"
buttons so role navigation could be reviewed before the backend existed; they
were removed because the backend rejects unsigned tokens, so those buttons
could only ever produce a session that failed on the first API call.

To review role-based navigation, sign in with a seeded account (all share the
demo password documented in the root README):

| Email | Role |
| --- | --- |
| `customer.aisha@example.com` | Customer |
| `valet.suresh@example.com` | Valet operator |
| `manager.karan@example.com` | Valet manager |
| the address in `SEED_ADMIN_EMAIL` | Administrator |

**Token storage.** `utils/tokenStorage.js` keeps the access token and the
refresh token in `localStorage`. The honest trade-off: `localStorage` is
readable by any script on this origin, so an XSS bug would expose it. The
mitigations in place are a short access-token TTL, server-side revocation on
sign-out, and no runtime CDN scripts. The stronger design is an `httpOnly`,
`SameSite=Strict` cookie for the refresh token — nothing in the browser can
read that at all — and it is the recommended step before a real deployment.

**Two sources of identity.** `AuthContext` exposes both `user` (decoded from
the token, available on first paint with no request) and `profile` (fetched
from `GET /api/v1/auth/me`, which is what the database actually says). Route
guards and `useHasRole` prefer `profile`, so a role change or a suspension
takes effect without waiting for the token to expire.

**Guards.** `RequireAuth` decides whether the app shell renders at all;
`RequireRole` decides whether a given screen is available to this role. Both
are UX only — the authoritative check is the backend's `require_authentication`
and `require_role` decorators, which reject the API call no matter what the
browser renders.

## Production build

```powershell
npm run build      # → frontend/dist
```

Serve `dist/` from any static host and reverse-proxy `/api` to Flask:

```nginx
location /        { root /var/www/valet/dist; try_files $uri /index.html; }
location /api/    { proxy_pass http://127.0.0.1:5000; }
location /media/  { proxy_pass http://127.0.0.1:5000; }
```

Bundle splitting (`react`, `query`, `map`, `charts`, `vendor`) is configured in
`vite.config.js` so the first paint does not pay for Leaflet or Recharts.

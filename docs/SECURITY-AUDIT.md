# Security Audit — AI-Powered Valet Transparency Platform

**Date:** 2026-09-10
**Scope:** Flask/Python backend, React frontend, PostgreSQL schema and migrations
**Method:** static review of every route and service in the request path, followed by
dynamic verification — live HTTP requests against a real PostgreSQL database using
genuinely signed tokens. Every finding below was reproduced before it was fixed and
re-tested after.

**Result:** 5 real vulnerabilities found and fixed. Final state: **929 backend tests
pass** (835 pre-existing + 94 new security tests), **434 frontend tests pass**, ruff and
eslint clean.

---

## 1. Vulnerabilities found and fixed

### 1.1 IDOR — any customer could enumerate any other customer's vehicles

**Severity: High.**

`services/vehicles.py::list_vehicles` built its ownership clause like this:

```python
if owner_id is not None:
    clauses.append(Vehicle.owner_id == owner_id)
elif not is_staff(actor):
    clauses.append(Vehicle.owner_id == uuid.UUID(actor.user_id))
```

A caller-supplied `owner_id` was applied **instead of** the actor's own scope, not
intersected with it. Any authenticated customer could list anyone's fleet:

```
GET /api/v1/vehicles?owner_id=<victim>
→ 200, rows_returned=1, plates=['UP85JP7551']   # the victim's car
```

`owner_id` is a legitimate filter for valet staff registering a vehicle on a customer's
behalf, so it could not simply be removed.

**Fix:** the filter is now intersected with the actor's scope. A customer is always pinned
to their own id; only staff may narrow to a named owner.

```python
if not is_staff(actor):
    clauses.append(Vehicle.owner_id == uuid.UUID(actor.user_id))
elif owner_id is not None:
    clauses.append(Vehicle.owner_id == owner_id)
```

Verified: the same request now returns only the caller's own vehicle, and the staff path
still returns exactly the named owner's vehicles.

### 1.2 No brute-force protection on login

**Severity: High.**

`RateLimitError` was declared in `utils/exceptions.py`, exported from `utils/__init__.py`,
and **never raised anywhere**. Twenty consecutive bad passwords all returned 401 with no
slowdown:

```
20 bad logins → {401: 20}   429 present? False
```

**Fix:** new `utils/rate_limit.py` (198 lines), wired into `services.auth.authenticate`.

- 5 attempts per address per 15 minutes, both configurable via env vars.
- Checked **before** the PBKDF2 verification, so the throttle also stops an attacker from
  making the server repeat expensive hash work.
- Keyed on the **email, not the client IP**. Behind a reverse proxy every caller shares one
  peer address, so an IP key would let one attacker lock out an entire office. The
  documented trade-off is that someone who knows your address can lock *you* out for one
  window — the lesser harm.
- Case-insensitive keying, so an attacker cannot double their budget with a shift key.
- A successful login clears the counter; earlier typos are forgiven.
- Behaves identically for real and fictional addresses, so it cannot be used as an
  account-existence oracle.

Verified:

```
8 bad logins → {401: 5, 429: 3}
429 body → {"code": "RATE_LIMITED", "details": {"retry_after_seconds": 900}}
different user unaffected → 200
```

**Limitation, stated plainly:** the counter is in-process. Running N worker processes gives
an attacker N × the budget. A multi-worker deployment should move it behind the same two
public functions to Redis or the database. Documented in `.env.example`.

### 1.3 Conflict errors leaked another customer's data

**Severity: Medium.**

The `IntegrityError` handler returned a slice of the raw driver text:

```python
details={"constraint_hint": detail.split("DETAIL:")[-1].strip()[:200]}
```

On a duplicate-key violation, PostgreSQL's `DETAIL` line **quotes the offending row**:

```
Key (email)=(someone@example.com) already exists.
```

So registering an address that already existed echoed that address straight back —
confirming which emails are registered and handing back another customer's data. The
in-code comment claimed the constraint name "contains schema, never data", which was true
of the name but not of the slice actually being returned.

**Fix:** the handler now parses out only the quoted constraint identifier and logs the full
text server-side. Verified: `details={'field': 'email'}`, no email, no `DETAIL:` in the
response.

### 1.4 Operators could read the manager dashboard and the report export

**Severity: Medium.**

`GET /api/v1/manager/dashboard` returned **200** to a `VALET_OPERATOR`, as did
`/api/v1/manager/report?format=json`. The cause was that `build_dashboard` gated on
`is_staff()`, which admits operators.

Three parts of the project disagreed about whether that was correct:

| Source | Said |
| --- | --- |
| `utils/security.py` capability matrix | `report:read_all` is granted to `VALET_MANAGER` and `ADMIN` **only** |
| `frontend/src/routes/index.jsx` | `roles: [VALET_MANAGER, ADMIN]` on both `/manager` and `/reports` |
| `tests/test_manager_dashboard.py` | parametrised `[VALET_OPERATOR, VALET_MANAGER, ADMIN]` and asserted **200** |

Two of the three said operators should not have this, and the API is the boundary that
actually holds — hiding a nav link is presentation, not authorisation.

**Fix:** the gate is now `MANAGER_VIEW_ROLES = (VALET_MANAGER, ADMIN)`. Because
`build_report` delegates to `build_dashboard`, tightening that one gate closes both the
screen and the CSV/JSON download. The pre-existing test was split in two:
`test_every_manager_role_may_read_it` (manager, admin → 200) and
`test_an_operator_may_not_read_it` (operator → 403). The five other dashboard tests that
borrowed the ticket-issuing operator's token now use a manager token; their assertions are
unchanged.

### 1.5 `MIN_IMAGE_PIXELS` was dead configuration

**Severity: Low.**

`MIN_IMAGE_PIXELS` was declared in `config/settings.py`, documented in `.env.example`,
listed in the root README's configuration table — and **never read by any code**. A 2×2
pixel file was accepted as a vehicle condition photograph. `MAX_IMAGE_PIXELS` was likewise
unused in settings; the real upper bounds live in `utils/image_probe.py` and are enforced
correctly (they guard memory allocation, so they must run on the `ai/vision` path too,
which cannot import Flask).

**Fix:** the lower bound is now enforced in `services/media.py::_image_dimensions`, the one
place that both has Flask config access and sits on the upload path. The upper bounds were
left in `image_probe.py` where they belong. This also surfaced that the shared test fixture
`png_bytes()` defaulted to 64×48 — itself below the documented minimum — so the fixture was
raised to 96×72 and the one test asserting exact dimensions was updated.

---

## 2. Design debt worth recording

### 2.1 The capability matrix is documentation, not enforcement

`capability_required` is defined in `utils/security.py` and used in **zero** of the 19
route files. Every check verified here is enforced instead by `require_role` decorators plus
service-layer `is_staff` / role checks, which do work correctly.

The risk is drift: adding a capability to the matrix changes nothing at runtime, which is
precisely how finding 1.4 arose. Nothing is broken today, but the matrix should either
become the enforcement mechanism or be labelled as descriptive.

---

## 3. Accepted risks (documented, not fixed)

| Item | Position |
| --- | --- |
| `/media/<path>` is unauthenticated | By design, documented in the module docstring. Access control rests on unguessable UUID4-hex filenames. Traversal is blocked (see 4.4). |
| Tokens in `localStorage` | Documented trade-off in `tokenStorage.js`. XSS-resistant storage would need httpOnly cookies plus CSRF protection. |
| No CSP / HSTS headers | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy` and `Cache-Control: no-store` are present. CSP is a meaningful hardening gap for a browser app; HSTS is not applicable to local HTTP development. |

---

## 4. Controls verified as correct

Every item below was exercised with a live request, not read from source.

**4.1 Token forgery.** `alg:none` forged as ADMIN → 401. HS256 signed with an
attacker-chosen secret → 401. A refresh token replayed against a data endpoint → 401.

**4.2 Cross-tenant isolation.** A customer reading, `PATCH`ing or `DELETE`ing another
customer's vehicle → **404 in all three cases**, not 403 — a 403 would confirm the vehicle
exists. The rejected write was confirmed not to have landed.

**4.3 Privilege boundaries.** Operator creating an account → 403. Manager creating an
account → 403 (`user:manage` is ADMIN alone). Admin creating an account → 201. Operator
reading the audit log → 403. Manager reading the audit log → 200. Customer reading the
audit log → 403. Anonymous on six protected endpoints → 401. Customer on both staff
dashboards → 403. Operator on the manager dashboard and report → 403.

**4.4 Path traversal.** Seven payloads against `/media/`, including encoded, doubled and
backslash variants — none left the storage directory, none returned file contents. A
double-slash path is normalised by Werkzeug and still 404s.

**4.5 Upload validation.** Files are judged by signature bytes, not filename: an ELF binary
named `.jpg` → 415; a genuine PNG named `.php` → 415; HTML disguised as an image → 415.
Stored filenames are server-generated `uuid4().hex`, so a `../../evil.png` upload cannot
influence where the file lands. Oversized bodies → 413 **as JSON**, not Flask's default
HTML page. Per-request image count is capped at 8.

**4.6 Error hygiene.** No response in the request path contains `Traceback`, `sqlalchemy`
or `psycopg2`. Every response carries the standard envelope with a `request_id` and exactly
one of `data` / `error`.

**4.7 SQL injection.** No user input reaches a string-built query. The only f-string SQL in
the codebase is in migrations and `init_db.py`, interpolating migration-defined trigger
names and inspector-derived table names — never request data.

**4.8 CORS.** An `Origin: https://evil.example` preflight is not reflected back, and is not
combined with `Access-Control-Allow-Credentials: true`.

**4.9 Passwords.** PBKDF2-SHA256 at 600,000 iterations, salted, with a decoy hash so an
unknown email costs the same time as a known one. `to_dict()` omits `password_hash`; no
hash appears in any login response.

**4.10 Registration.** Public registration refuses a requested staff role with 403 rather
than silently downgrading it, and `services.auth.register` hard-codes `CUSTOMER` as a
second line of defence.

**4.11 Secret management.** `as_safe_dict()` returns `***REDACTED***` for
`JWT_SECRET_KEY` and `SEED_ADMIN_PASSWORD`, and masks the database password out of the DSN
while keeping scheme and host so an operator can still see the target. `.env`, `.env.*` and
`secrets.*` are gitignored. No hardcoded credentials in any Python module.

**4.12 Logging.** Neither a plaintext password, a wrong password, an access token, a
password hash, nor the database password reaches the log at DEBUG level — while the failed
attempt itself is still written to `audit_logs`, so hygiene has not cost visibility.

**4.13 CSRF.** The API authenticates with a bearer token, so the classic chain breaks: a
browser will not automatically attach credentials a malicious page cannot read. Verified
that login issues **no session cookie**, that a state-changing request without a token is
401, and that credentials are never paired with a reflected attacker origin. A future move
to cookie auth would reintroduce this — the tests pin the property so it cannot happen
silently.

**4.14 No configuration mutation surface.** There is no endpoint that changes auth or
security configuration, for any role. `config:manage` exists in the capability matrix as a
reservation for a future admin console. A parametrised test sweeps 5 plausible paths × 5
HTTP methods as both manager and admin and fails loudly if a config surface ever appears
without a guard.

---

## 5. New tests

`backend/tests/test_security_audit.py` — **94 tests**, all passing. Every one runs against
real PostgreSQL with genuinely signed tokens; no permission check is mocked, because a
mocked check proves nothing about the real one.

| Class | Covers |
| --- | --- |
| `TestCrossTenantIsolation` | 404-not-403 on read/patch/delete, the IDOR regression, staff filtering still works |
| `TestLoginThrottling` | 429 after the budget, per-account not global, case-insensitive, cleared on success, no existence oracle |
| `TestErrorLeakage` | no tracebacks, no SQL, conflicts name the constraint but not the row |
| `TestUploadValidation` | content sniffing beats the filename, server-generated names |
| `TestPathTraversal` | seven traversal payloads, absolute paths |
| `TestRoleEscalation` | alg:none, wrong secret, refresh replay, self-assignment, per-role endpoint access, manager-dashboard boundary, capability invariants |
| `TestTransportHardening` | CORS reflection, security headers, no server banner |
| `TestUploadSizeLimits` | 413 stays JSON, per-request count cap, minimum dimension |
| `TestSecretManagement` | redaction, DSN masking, nothing reachable via `/api/health` |
| `TestLoggingHygiene` | no password/token/hash in logs, attempt still audited |
| `TestCsrfPosture` | no session cookie, no ambient credentials, CORS+credentials never paired |
| `TestNoConfigMutationSurface` | 25 path×method combinations prove no config endpoint exists |

Three tests deserve a note because they assert something counter-intuitive on purpose:

- `test_reading_another_customers_vehicle_is_404` pins the 404. If someone "improves" it to
  a 403, the test fails — because a 403 tells an attacker the id was real.
- `test_the_owner_id_filter_still_works_for_staff` guards the IDOR fix from being
  over-corrected into breaking a legitimate staff query.
- `test_no_config_mutation_endpoint_exists` asserts *absence*. It is expected to pass by
  404/405, and its purpose is to fail the day someone adds `/api/v1/config` without a guard.

---

## 6. Verification

```
backend   python3 -m pytest -q                    929 passed in 254.20s
backend   python3 -m ruff check .                 All checks passed!
frontend  npx vitest run                          434 passed (37 files)
frontend  npx eslint .                            clean
```

Baseline before this audit was 835 backend tests; the 94 additions account for the
difference.

**Test changes made, and why.** No test was deleted, and no assertion was weakened to make
a fix pass.

- `test_every_staff_role_may_read_it` was split into
  `test_every_manager_role_may_read_it` (manager, admin → 200) and
  `test_an_operator_may_not_read_it` (operator → 403), reflecting fix 1.4.
- Five dashboard tests that borrowed the ticket-issuing operator's token now use a manager
  token. Their assertions about charts, filters and windows are unchanged.
- `png_bytes()` was raised from 64×48 to 96×72 because the old default violated the
  documented `MIN_IMAGE_PIXELS` policy, and the one test asserting exact dimensions was
  updated to match.

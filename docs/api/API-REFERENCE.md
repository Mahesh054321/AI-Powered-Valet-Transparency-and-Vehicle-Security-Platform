# API Reference

Every endpoint implemented by the platform, grouped by domain. This page is generated from the same catalogue as [`openapi.yaml`](openapi.yaml), and both were checked against the live application — see [`verify_api_docs.py`](../../backend/utils/scripts/api_docs/verify_spec.py), which is also run as a regression test.

**Nothing here is aspirational.** If a route is not in `app.url_map` it is not documented, and no route in the map is missing.

---

## Conventions

### Base URL

```
http://localhost:5000
```

`/api/v1/...` is the canonical versioned surface.

### Response envelope

Every JSON response is wrapped in the same envelope:

```json
{
  "success": true,
  "data": { "…": "…" },
  "meta": { "request_id": "2336852f…", "server_time": 1789225490.85 }
}
```

On failure, `data` is replaced by `error`:

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "'plate_number' does not look like a valid Indian registration number.",
    "details": { "field": "plate_number" }
  },
  "meta": { "request_id": "9cbb5045…", "server_time": 1789225630.95 }
}
```

`meta.request_id` is also returned as the `X-Request-Id` response header, and is written to the audit log. Quote it when reporting a problem — it is what ties a user's complaint to a line in the server log.

### Authentication

Sign in and send the access token on every subsequent request:

```bash
curl -X POST http://localhost:5000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"manager.karan@example.com","password":"Demo#Passw0rd"}'

curl http://localhost:5000/api/v1/auth/me \
  -H 'Authorization: Bearer <access_token>'
```

Tokens are HS256 JWTs carrying `sub`, `role`, `typ`, `jti`, `iss`, `aud`, `iat`, `nbf` and `exp`. A wrong audience, a wrong issuer, an expired token, a tampered signature, an unknown role, `alg: none`, or a refresh token presented as an access token are all rejected.

### Roles

| Role | Scope |
|---|---|
| `CUSTOMER` | Their own vehicles, tickets, custody and alerts. |
| `VALET_OPERATOR` | Check-in, custody transitions, telemetry, photo capture. |
| `VALET_MANAGER` | Everything an operator can do, plus alert triage, geofence edits, damage verification, reports and the audit trail. |
| `ADMIN` | Everything, plus creating staff accounts. |

Inheritance runs `ADMIN → VALET_MANAGER → VALET_OPERATOR`. A `CUSTOMER` is **not** part of that chain: customers hold capabilities staff do not (such as registering a vehicle to themselves), so the capability sets do not nest. Adding a role grants nothing unless it is listed.

### Pagination

List endpoints accept `?page=` (1-based) and `?page_size=` (capped at 200). Paginated responses echo `page`, `page_size` and `total` in `meta`.

### Unversioned aliases

Several modules are mounted twice. The handlers are identical; only the prefix differs. The unversioned paths exist because those contracts were originally specified without a version, and keeping them means an older client keeps working.

| Canonical | Alias |
|---|---|
| `/api/v1/auth/...` | `/api/auth/...` |
| `/api/v1/inspections/...` | `/api/inspections/...` |
| `/api/v1/gps/...` | `/api/gps/...` |
| `/api/v1/geofences/...` | `/api/geofences/...` |
| `/api/v1/alerts/...` | `/api/alerts/...` |
| `/api/v1/ml/...` | `/api/ml/...` |
| `/api/v1/retrieval/...` | `/api/retrieval/...` |

`/api/health`, `/api/readyz` and `/media/...` are unversioned by design and have no `/api/v1` counterpart.

### Error codes

| Status | `error.code` | Meaning |
|---|---|---|
| `401` | `AUTHENTICATION_ERROR` | Unauthorized — No token, a malformed token, an expired token, or a token with a bad signature, audience or issuer. |
| `403` | `AUTHORIZATION_ERROR` | Forbidden — The caller is authenticated but their role may not perform this action. |
| `404` | `NOT_FOUND` | Not Found — The resource does not exist, or it exists but is not visible to the caller. |
| `409` | `CONFLICT` | Conflict — The request conflicts with the current state — a duplicate unique value, an illegal state transition, or a repeat of an action already taken. |
| `413` | `PAYLOAD_TOO_LARGE` | Payload Too Large — The upload exceeds `MAX_CONTENT_LENGTH_MB` (15 MB by default). |
| `415` | `UNSUPPORTED_MEDIA_TYPE` | Unsupported Media Type — A file is not in the allowed image extension list. |
| `422` | `VALIDATION_ERROR` | Unprocessable Entity — The request is syntactically valid but semantically wrong — a missing required field, a malformed value, or a value outside its allowed set. |
| `429` | `RATE_LIMITED` | Too Many Requests — The login rate limiter refused the attempt after 5 consecutive failures for that email address. |
| `500` | `INTERNAL_ERROR` | Internal Server Error — An unexpected failure. |
| `503` | `SERVICE_UNAVAILABLE` | Service Unavailable — A dependency is unhealthy — for the health and readiness probes, that means a check such as the database did not pass. |

---

## Contents

- [Authentication](#authentication)
- [Users](#users)
- [Vehicles](#vehicles)
- [Valet Tickets](#valet-tickets)
- [Custody](#custody)
- [Inspections](#inspections)
- [GPS](#gps)
- [Geofencing](#geofencing)
- [Security Alerts](#security-alerts)
- [Retrieval Prediction](#retrieval-prediction)
- [Reports](#reports)
- [Audit Logs](#audit-logs)
- [System](#system)

---

## Authentication

Sign-in, sign-out and the signed-in identity.

### `POST /api/v1/auth/register`

**Self-register a customer account**

Creates a `CUSTOMER` account. This is the only public write route in the API.

A `role` field may be sent but **only `CUSTOMER` is accepted** — asking for any other role returns `403`, so public registration can never produce staff. Passwords are stored as salted PBKDF2-SHA256 digests (600,000 iterations by default), never as plain text.

Login is rate limited: after 5 failed attempts for an email address further attempts are refused with `429` for a cooldown window.

| | |
|---|---|
| **Authentication** | None — public route. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | `email` | **yes** | — |
| `password` | `string` | **yes** | (minLength 8) |
| `full_name` | `string` | **yes** | (maxLength 120) |
| `phone` | `string` | no | — |
| `role` | `enum` | no | Optional. Only `CUSTOMER` is permitted here. One of: `CUSTOMER` |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/auth/register \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"email": "aisha@example.com", "password": "Str0ng#Passw0rd", "full_name": "Aisha Verma", "phone": "+91 98100 10010"}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | A non-`CUSTOMER` role was requested. Staff accounts are created by an administrator via `POST /api/v1/auth/users`. |
| `409` | An account with that email already exists. |
| `422` | Missing or malformed field. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "message": "Account created. You can sign in now.",
    "user": {
      "created_at": "2026-09-12T16:01:46.071075+00:00",
      "email": "docprobe.799e6643@example.com",
      "full_name": "Doc Probe",
      "id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
      "is_active": true,
      "is_staff": false,
      "phone": "9876543210",
      "role": "CUSTOMER",
      "status": "ACTIVE",
      "updated_at": "2026-09-12T16:01:46.071075+00:00"
    }
  },
  "meta": {
    "request_id": "8ac1f8634d6f4129bc2ee547b1fff1c6",
    "server_time": 1789228906.249017
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/auth/login`

**Exchange credentials for tokens**

Returns a short-lived access token and a long-lived refresh token, plus the signed-in user.

The access token is a JWT (HS256) carrying `sub`, `role`, `typ`, `jti`, `iss`, `aud`, `iat`, `nbf` and `exp`. Send it as `Authorization: Bearer <access_token>`.

Failed attempts are throttled per email address; the 6th consecutive failure returns `429` rather than `401`.

| | |
|---|---|
| **Authentication** | None — public route. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | `email` | **yes** | — |
| `password` | `string` | **yes** | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/auth/login \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"email": "manager.karan@example.com", "password": "Demo#Passw0rd"}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `401` | The email or password is wrong. The message does not say which. |
| `429` | Too many failed attempts for this email address. Retry after the cooldown. |
| `422` | Missing field. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "user": {
      "created_at": "2026-09-12T15:04:30.151668+00:00",
      "email": "admin@valet.local",
      "full_name": "Platform Administrator",
      "id": "14254104-597c-59d0-9fd5-7320e446a3e9",
      "is_active": true,
      "is_staff": true,
      "phone": null,
      "role": "ADMIN",
      "status": "ACTIVE",
      "updated_at": "2026-09-12T15:07:10.088861+00:00"
    }
  },
  "meta": {
    "request_id": "4a34d99e9e6d4231bc3623008e0597dd",
    "server_time": 1789225630.9283738
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/auth/logout`

**Revoke the refresh token**

Revokes the presented refresh token by recording its `jti` in the `revoked_tokens` table, which is append-only. A revoked token cannot be used again even though it has not expired.

The access token is *not* revoked — it stays valid until its own `exp`. Its lifetime is short (30 minutes by default) for exactly this reason.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `refresh_token` | `string` | no | The refresh token to revoke. |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/auth/logout \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token": "<refresh token from login>"}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Missing, malformed or expired token. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "message": "Signed out. The access token has been revoked."
  },
  "meta": {
    "request_id": "7c492560da834508b64ad864598fc018",
    "server_time": 1789228907.7065299
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/auth/me`

**The signed-in user**

Returns the account the presented token belongs to. Clients call this on startup to restore a session from a stored token and to learn the role that decides which navigation to render.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Missing, malformed or expired token. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "user": {
      "created_at": "2026-09-12T15:04:30.151668+00:00",
      "email": "admin@valet.local",
      "full_name": "Platform Administrator",
      "id": "14254104-597c-59d0-9fd5-7320e446a3e9",
      "is_active": true,
      "is_staff": true,
      "phone": null,
      "role": "ADMIN",
      "status": "ACTIVE",
      "updated_at": "2026-09-12T15:07:10.088861+00:00"
    }
  },
  "meta": {
    "request_id": "4a34d99e9e6d4231bc3623008e0597dd",
    "server_time": 1789225630.9283738
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Users

Administrator-managed accounts and the customer directory.

### `POST /api/v1/auth/users`

**Create a staff account (administrator)**

Creates an account with an explicit role. This is the only way a `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN` account can come into existence — public registration refuses any non-customer role.

The `role` is validated against the role vocabulary and an unknown value returns `422` listing the permitted ones.

| | |
|---|---|
| **Authentication** | `ADMIN` only. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by the `require_role(ADMIN)` decorator on the route. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | `email` | **yes** | — |
| `password` | `string` | **yes** | (minLength 8) |
| `full_name` | `string` | **yes** | (maxLength 120) |
| `role` | `enum` | **yes** | One of: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN` |
| `phone` | `string` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/auth/users \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"email": "suresh@valet.local", "password": "Str0ng#Passw0rd", "full_name": "Suresh Yadav", "role": "VALET_OPERATOR"}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | The caller is not an administrator. |
| `409` | An account with that email already exists. |
| `422` | Unknown role, or a missing/malformed field. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "message": "VALET_OPERATOR account created.",
    "user": {
      "created_at": "2026-09-12T16:01:46.418284+00:00",
      "email": "docstaff.e26a78b4@example.com",
      "full_name": "Doc Staff",
      "id": "801e7fea-b303-43d5-bd95-01164967a458",
      "is_active": true,
      "is_staff": true,
      "phone": null,
      "role": "VALET_OPERATOR",
      "status": "ACTIVE",
      "updated_at": "2026-09-12T16:01:46.418284+00:00"
    }
  },
  "meta": {
    "request_id": "4c808aac25e14a1b81c5be34ae67df8f",
    "server_time": 1789228906.6376553
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/customers`

**Customer directory for check-in**

The customers a valet may hand a vehicle to. Exists so check-in can resolve a customer by name or email without exposing the full user table.

Returns `id`, `full_name`, `email`, `phone` and `status` only — never a password digest or any other credential material.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by the `require_role(...)` decorator on the route. |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `search` | string | `—` | Matches name or email. |
| `page` | integer | `1` | 1-based page number. |
| `page_size` | integer | `20` | Rows per page, capped at 200. Max 200. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is a customer. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "items": [
      {
        "email": "customer.aisha@example.com",
        "full_name": "Aisha Verma",
        "id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "phone": "+91 98100 100 10",
        "status": "ACTIVE"
      },
      {
        "email": "customer.rohit@example.com",
        "full_name": "Rohit Menon",
        "id": "7692e411-48ff-50e3-91b3-9b721978a47d",
        "phone": "+91 98200 200 20",
        "status": "ACTIVE"
      }
    ],
    "total": 2
  },
  "meta": {
    "request_id": "6ce4adb9f8954247a95cc89cb867e255",
    "server_time": 1789225630.7651982
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Vehicles

Customer vehicle records and their custody history.

### `GET /api/v1/vehicles`

**List the vehicles the caller may see**

Scoped server-side to the caller: a customer sees only their own vehicles, staff see every vehicle they may check in. Omitting a filter cannot widen the view.

Ownership is derived from the token, never from a request parameter. A customer asking for someone else's vehicle gets `404` rather than `403`, so the response does not confirm the id exists.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Scoped by role in the service layer; no role is excluded. |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `status` | `REGISTERED`, `IN_CUSTODY`, `FLAGGED`, `ARCHIVED` | `—` | — |
| `search` | string | `—` | Matches plate, make or model. |
| `owner_id` | uuid | `—` | Staff only. Filter to one customer's vehicles. |
| `include_archived` | boolean | `False` | — |
| `page` | integer | `1` | 1-based page number. |
| `page_size` | integer | `20` | Rows per page, capped at 200. Max 200. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `422` | Unknown status value or malformed UUID. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "items": [
      {
        "color": "Titan Grey",
        "created_at": "2026-09-12T15:04:30.151668+00:00",
        "id": "448c7417-3c19-5bb7-862c-3a8c981f95e2",
        "is_in_custody": false,
        "make": "Hyundai",
        "model": "Creta",
        "normalized_plate": "UP78CD5678",
        "notes": "[DEMO] Seeded demonstration vehicle.",
        "owner": {
          "email": "customer.aisha@example.com",
          "full_name": "Aisha Verma",
          "id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
          "phone": "+91 98100 100 10"
        },
        "owner_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "plate_number": "UP 78 CD 5678",
        "status": "REGISTERED",
        "updated_at": "2026-09-12T15:04:30.151668+00:00",
        "vin": null,
        "year": 2022
      },
      {
        "color": "Nexa Blue",
        "created_at": "2026-09-12T15:04:30.151668+00:00",
        "id": "41afc2ed-7d5d-5152-9f15-09a99d32fc47",
        "is_in_custody": false,
        "make": "Maruti Suzuki",
        "model": "Baleno",
        "normalized_plate": "DL01CA9012",
        "notes": "[DEMO] Seeded demonstration vehicle.",
        "owner": {
          "email": "customer.rohit@example.com",
          "full_name": "Rohit Menon",
          "id": "7692e411-48ff-50e3-91b3-9b721978a47d",
          "phone": "+91 98200 200 20"
        },
        "owner_id": "7692e411-48ff-50e3-91b3-9b721978a47d",
        "plate_number": "DL 01 CA 9012",
        "status": "FLAGGED",
        "updated_at": "2026-09-12T15:04:30.151668+00:00",
        "vin": null,
        "year": 2024
      },
      {
        "color": "Pearl White",
        "created_at": "2026-09-12T15:04:30.151668+00:00",
        "id": "b88418a6-7799-5f31-856f-fd424e58a176",
        "is_in_custody": true,
        "make": "Tata",
        "model": "Nexon",
        "normalized_plate": "UP78AB1234",
        "notes": "[DEMO] Seeded demonstration vehicle.",
        "owner": {
          "email": "customer.aisha@example.com",
          "full_name": "Aisha Verma",
          "id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
          "phone": "+91 98100 100 10"
        },
        "owner_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "plate_number": "UP 78 AB 1234",
        "status": "IN_CUSTODY",
        "updated_at": "2026-09-12T15:04:30.151668+00:00",
        "vin": null,
        "year": 2023
      }
    ],
    "pagination": {
      "has_next": false,
      "has_prev": false,
      "page": 1,
      "page_size": 20,
      "total_items": 3,
      "total_pages": 1
    }
  },
  "meta": {
    "request_id": "7c606132be5c40158e149333eb473d2c",
    "server_time": 1789225630.7025304
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/vehicles`

**Register a vehicle**

A customer registers to themselves. Valet staff may pass `owner_id` to register on a customer's behalf during check-in; a customer who sends `owner_id` is refused.

The plate is validated against Indian registration formats and uniqueness is enforced by a partial unique index on the normalised plate, so a duplicate returns `409` naming the field.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Any role may register; `owner_id` is restricted to staff. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `plate_number` | `string` | **yes** | Indian registration number. Separators are optional: `UP 85 CA 1234`, `UP85CA1234` and the Bharat series `25BH0987AB` are all accepted. |
| `make` | `string` | **yes** | (maxLength 60) (minLength 2) |
| `model` | `string` | no | (maxLength 60) |
| `year` | `integer` | no | (minimum 1950) (maximum 2100) |
| `color` | `string` | no | (maxLength 40) |
| `vin` | `string` | no | (maxLength 40) |
| `notes` | `string` | no | (maxLength 500) |
| `owner_id` | `uuid` | no | Staff only. A customer registering for themselves must omit it — the owner is taken from the token. |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/vehicles \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"plate_number": "UP85CA1234", "make": "Tata", "model": "Nexon", "year": 2022, "color": "Silver", "notes": "Roof rack fitted."}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | A customer supplied `owner_id`. |
| `409` | That registration number is already registered. `details.field` is `plate_number`. |
| `422` | The plate is not a valid Indian registration number, or a required field is missing. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "message": "UP98AB9533 registered.",
    "vehicle": {
      "color": "Silver",
      "created_at": "2026-09-12T16:01:46.642491+00:00",
      "id": "3120854d-1367-42a9-80cd-6742eb61e33a",
      "is_in_custody": false,
      "make": "Tata",
      "model": "Nexon",
      "normalized_plate": "UP98AB9533",
      "notes": "Documentation probe vehicle.",
      "owner": {
        "email": "docprobe.799e6643@example.com",
        "full_name": "Doc Probe",
        "id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
        "phone": "9876543210"
      },
      "owner_id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
      "plate_number": "UP98AB9533",
      "status": "REGISTERED",
      "updated_at": "2026-09-12T16:01:46.642491+00:00",
      "vin": null,
      "year": 2022
    }
  },
  "meta": {
    "request_id": "3db6dd8dbeca46abbed0826aee0b2511",
    "server_time": 1789228906.651453
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/vehicles/{vehicle_id}`

**Fetch one vehicle**

Returns `404` — not `403` — when the vehicle exists but belongs to another customer, so the response does not confirm that the id exists.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `vehicle_id` | `uuid` | The vehicle's UUID. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such vehicle, or it belongs to another customer. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "vehicle": {
      "color": "Titan Grey",
      "created_at": "2026-09-12T15:04:30.151668+00:00",
      "id": "448c7417-3c19-5bb7-862c-3a8c981f95e2",
      "is_in_custody": false,
      "make": "Hyundai",
      "model": "Creta",
      "normalized_plate": "UP78CD5678",
      "notes": "[DEMO] Seeded demonstration vehicle.",
      "owner": {
        "email": "customer.aisha@example.com",
        "full_name": "Aisha Verma",
        "id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "phone": "+91 98100 100 10"
      },
      "owner_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
      "plate_number": "UP 78 CD 5678",
      "status": "REGISTERED",
      "updated_at": "2026-09-12T15:04:30.151668+00:00",
      "vin": null,
      "year": 2022
    }
  },
  "meta": {
    "request_id": "ea37c59b1ee64c05a8cc89ec958460f6",
    "server_time": 1789225630.9409728
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `PATCH /api/v1/vehicles/{vehicle_id}`

**Update a vehicle**

Partial update: only the fields present in the body are changed.

Changing `plate_number` re-runs the format and uniqueness checks, so a collision returns `409` rather than silently overwriting another record.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | The owner, or staff. Anyone else gets `404`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `vehicle_id` | `uuid` | The vehicle's UUID. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `plate_number` | `string` | no | Indian registration number. Separators are optional: `UP 85 CA 1234`, `UP85CA1234` and the Bharat series `25BH0987AB` are all accepted. |
| `make` | `string` | no | (maxLength 60) (minLength 2) |
| `model` | `string` | no | (maxLength 60) |
| `year` | `integer` | no | (minimum 1950) (maximum 2100) |
| `color` | `string` | no | (maxLength 40) |
| `vin` | `string` | no | (maxLength 40) |
| `notes` | `string` | no | (maxLength 500) |
| `owner_id` | `uuid` | no | Staff only. A customer registering for themselves must omit it — the owner is taken from the token. |

*Example request*

```bash
curl -X PATCH http://localhost:5000/api/v1/vehicles/{vehicle_id} \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"color": "Graphite", "notes": "Colour corrected."}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `404` | No such vehicle, or it is not yours. |
| `409` | The new plate is already taken. |
| `422` | Malformed field. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "message": "Vehicle updated.",
    "vehicle": {
      "color": "Graphite",
      "created_at": "2026-09-12T16:01:46.642491+00:00",
      "id": "3120854d-1367-42a9-80cd-6742eb61e33a",
      "is_in_custody": false,
      "make": "Tata",
      "model": "Nexon",
      "normalized_plate": "UP98AB9533",
      "notes": "Colour corrected.",
      "owner": {
        "email": "docprobe.799e6643@example.com",
        "full_name": "Doc Probe",
        "id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
        "phone": "9876543210"
      },
      "owner_id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
      "plate_number": "UP98AB9533",
      "status": "REGISTERED",
      "updated_at": "2026-09-12T16:01:46.655549+00:00",
      "vin": null,
      "year": 2022
    }
  },
  "meta": {
    "request_id": "e87391bb73a24c85af320ce6815a5ce6",
    "server_time": 1789228906.6646714
  },
  "success": true
}
```

*Example `404` response*

```json
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "No vehicle was found with that id.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `DELETE /api/v1/vehicles/{vehicle_id}`

**Archive a vehicle**

Soft-deletes: the row moves to `ARCHIVED` and drops out of the active list, but the custody history that references it is preserved. Nothing is destroyed, because the audit trail must stay intact.

A vehicle that is currently in custody cannot be archived — close the custody session first.

Returns `204` with an empty body on success.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | The owner, or staff. Anyone else gets `404`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `vehicle_id` | `uuid` | The vehicle's UUID. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `204` | Success. |
| `404` | No such vehicle, or it is not yours. |
| `409` | The vehicle has an open custody session. |

*`204` returns no body.*

*Example `404` response*

```json
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "No vehicle was found with that id.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/vehicles/{vehicle_id}/sessions`

**Custody history for one vehicle**

Every previous custody session for the vehicle with the ticket it belongs to, newest first. Read-only: a session is closed by completing its ticket, not by editing it here.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `vehicle_id` | `uuid` | The vehicle's UUID. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such vehicle, or it is not yours. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "sessions": [],
    "total": 0,
    "vehicle": {
      "color": "Titan Grey",
      "created_at": "2026-09-12T15:04:30.151668+00:00",
      "id": "448c7417-3c19-5bb7-862c-3a8c981f95e2",
      "is_in_custody": false,
      "make": "Hyundai",
      "model": "Creta",
      "normalized_plate": "UP78CD5678",
      "notes": "[DEMO] Seeded demonstration vehicle.",
      "owner_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
      "plate_number": "UP 78 CD 5678",
      "status": "REGISTERED",
      "updated_at": "2026-09-12T15:04:30.151668+00:00",
      "vin": null,
      "year": 2022
    }
  },
  "meta": {
    "request_id": "2503036a4a974999a106b3b21b26c50b",
    "server_time": 1789225630.9483252
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Valet Tickets

The digital ticket lifecycle: issue, check in, complete, cancel.

### `GET /api/v1/tickets`

**List the tickets the caller may see**

Scoped server-side: a customer sees their own tickets, staff see the tickets they may act on.

`?status=` filters on the **projected** status, because that is the vocabulary the client was given. The stored value is an implementation detail of the backend.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `status` | `CREATED`, `IN_CUSTODY`, `READY_FOR_PICKUP`, `COMPLETED`, `CANCELLED` | `—` | — |
| `page` | integer | `1` | 1-based page number. |
| `page_size` | integer | `20` | Rows per page, capped at 200. Max 200. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `422` | Unknown status value. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "items": [
      {
        "actual_drop_off_at": "2026-09-12T13:24:30+00:00",
        "actual_pickup_at": null,
        "custody_session": {
          "assigned_valet_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
          "condition_accepted": null,
          "dropped_off_at": "2026-09-12T13:24:30+00:00",
          "id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
          "is_open": true,
          "picked_up_at": null,
          "retrieval_requested_at": "2026-09-12T17:34:30+00:00",
          "status": "PARKED",
          "ticket_id": "233e028c-6271-5a88-a32b-e63021d7c5fd",
          "vehicle_id": "b88418a6-7799-5f31-856f-fd424e58a176",
          "zone_assigned_at": "2026-09-12T13:30:30+00:00",
          "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
        },
        "customer": {
          "email": "customer.aisha@example.com",
          "full_name": "Aisha Verma",
          "id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
          "phone": "+91 98100 100 10"
        },
        "customer_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "expected_return_at": "2026-09-12T19:04:30+00:00",
        "id": "233e028c-6271-5a88-a32b-e63021d7c5fd",
        "issued_at": "2026-09-12T13:04:30+00:00",
        "parking_zone": {
          "code": "ZONE-A",
          "id": "5880e6e0-752c-5742-885b-72ef61090d8a",
          "name": "Zone A - Covered Parking"
        },
        "qr_code_path": "tickets/demo/demo-open.png",
        "qr_payload": "VALET:VT-O-OPEN",
        "qr_url": "/media/tickets/demo/demo-open.png",
        "special_instructions": "[DEMO] Please handle with care.",
        "status": "IN_CUSTODY",
        "stored_status": "ACTIVE",
        "ticket_code": "VT-O-OPEN",
        "vehicle": {
          "color": "Pearl White",
          "id": "b88418a6-7799-5f31-856f-fd424e58a176",
          "make": "Tata",
          "model": "Nexon",
          "plate_number": "UP 78 AB 1234",
          "status": "IN_CUSTODY",
          "year": 2023
        },
        "vehicle_id": "b88418a6-7799-5f31-856f-fd424e58a176"
      },
      {
        "actual_drop_off_at": "2026-09-10T15:24:30+00:00",
        "actual_pickup_at": "2026-09-10T20:04:30+00:00",
        "custody_session": {
          "assigned_valet_id": "64e7d621-9193-572b-943c-d103998c6daa",
          "condition_accepted": true,
          "dropped_off_at": "2026-09-10T15:24:30+00:00",
          "id": "0d65a744-f4dc-5229-86f7-1b79853ba4b6",
          "is_open": false,
          "picked_up_at": "2026-09-10T20:04:30+00:00",
          "retrieval_requested_at": "2026-09-10T19:34:30+00:00",
          "status": "CHECKED_OUT",
          "ticket_id": "d807b7c4-cf74-5dd1-9e84-e0ccc98e1397",
          "vehicle_id": "41afc2ed-7d5d-5152-9f15-09a99d32fc47",
          "zone_assigned_at": "2026-09-10T15:30:30+00:00",
          "zone_id": "1cc7f7a9-926b-5019-93a9-5be1486c16bb"
        },
        "customer": {
          "email": "customer.rohit@example.com",
          "full_name": "Rohit Menon",
          "id": "7692e411-48ff-50e3-91b3-9b721978a47d",
          "phone": "+91 98200 200 20"
        },
        "customer_id": "7692e411-48ff-50e3-91b3-9b721978a47d",
        "expected_return_at": "2026-09-10T21:04:30+00:00",
        "id": "d807b7c4-cf74-5dd1-9e84-e0ccc98e1397",
        "issued_at": "2026-09-10T15:04:30+00:00",
        "parking_zone": {
          "code": "ZONE-B",
          "id": "1cc7f7a9-926b-5019-93a9-5be1486c16bb",
          "name": "Zone B - Open Yard"
        },
        "qr_code_path": "tickets/demo/demo-closed.png",
        "qr_payload": "VALET:VT-CLOSED",
        "qr_url": "/media/tickets/demo/demo-closed.png",
        "special_instructions": "[DEMO] Please handle with care.",
        "status": "COMPLETED",
        "stored_status": "COMPLETED",
        "ticket_code": "VT-CLOSED",
        "vehicle": {
          "color": "Nexa Blue",
          "id": "41afc2ed-7d5d-5152-9f15-09a99d32fc47",
          "make": "Maruti Suzuki",
          "model": "Baleno",
          "plate_numbe
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/tickets`

**Issue a digital valet ticket**

Creates the ticket and renders its QR code server-side. Custody is **not** started here — that happens at `POST /tickets/{id}/check-in`, so issuing a ticket and physically taking the keys are separate recorded events.

The returned ticket carries both `status` (`CREATED`) and `stored_status` (`ISSUED`).

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_valet` in `services/tickets.py`. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `vehicle_id` | `uuid` | **yes** | — |
| `special_instructions` | `string` | no | (maxLength 500) |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/tickets \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"vehicle_id": "<vehicle uuid>", "special_instructions": "Handle with care."}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | The caller is a customer. |
| `404` | No such vehicle. |
| `409` | The vehicle already has an open ticket. |
| `422` | Missing `vehicle_id`. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "message": "Ticket VT-202609-UFRWWV issued.",
    "ticket": {
      "actual_drop_off_at": null,
      "actual_pickup_at": null,
      "custody_session": null,
      "customer": {
        "email": "docprobe.799e6643@example.com",
        "full_name": "Doc Probe",
        "id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
        "phone": "9876543210"
      },
      "customer_id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
      "expected_return_at": null,
      "id": "e99aeb57-d900-497a-9c75-638e53ec25bd",
      "issued_at": "2026-09-12T16:01:46.687251+00:00",
      "qr_code_path": "qr/8818cafe94d548fd8ea34f100ceb9f4c.png",
      "qr_payload": "VALET:VT-202609-UFRWWV",
      "qr_url": "/media/qr/8818cafe94d548fd8ea34f100ceb9f4c.png",
      "special_instructions": null,
      "status": "CREATED",
      "stored_status": "ISSUED",
      "ticket_code": "VT-202609-UFRWWV",
      "vehicle": {
        "color": "Graphite",
        "id": "3120854d-1367-42a9-80cd-6742eb61e33a",
        "make": "Tata",
        "model": "Nexon",
        "plate_number": "UP98AB9533",
        "status": "REGISTERED",
        "year": 2022
      },
      "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a"
    }
  },
  "meta": {
    "request_id": "7b575712235c45c39dfea2bff044c0b4",
    "server_time": 1789228906.714622
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/tickets/{ticket_id}`

**Fetch one ticket**

The digital ticket, including its custody session when one has been opened.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `ticket_id` | `uuid` | The ticket's UUID (not the human-readable `ticket_code`). |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such ticket, or it is not yours. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "ticket": {
      "actual_drop_off_at": "2026-09-12T13:24:30+00:00",
      "actual_pickup_at": null,
      "custody_session": {
        "assigned_valet_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
        "condition_accepted": null,
        "dropped_off_at": "2026-09-12T13:24:30+00:00",
        "id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "is_open": true,
        "picked_up_at": null,
        "retrieval_requested_at": "2026-09-12T17:34:30+00:00",
        "status": "PARKED",
        "ticket_id": "233e028c-6271-5a88-a32b-e63021d7c5fd",
        "vehicle_id": "b88418a6-7799-5f31-856f-fd424e58a176",
        "zone_assigned_at": "2026-09-12T13:30:30+00:00",
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      "customer": {
        "email": "customer.aisha@example.com",
        "full_name": "Aisha Verma",
        "id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "phone": "+91 98100 100 10"
      },
      "customer_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
      "expected_return_at": "2026-09-12T19:04:30+00:00",
      "id": "233e028c-6271-5a88-a32b-e63021d7c5fd",
      "issued_at": "2026-09-12T13:04:30+00:00",
      "parking_zone": {
        "code": "ZONE-A",
        "id": "5880e6e0-752c-5742-885b-72ef61090d8a",
        "name": "Zone A - Covered Parking"
      },
      "qr_code_path": "tickets/demo/demo-open.png",
      "qr_payload": "VALET:VT-O-OPEN",
      "qr_url": "/media/tickets/demo/demo-open.png",
      "special_instructions": "[DEMO] Please handle with care.",
      "status": "IN_CUSTODY",
      "stored_status": "ACTIVE",
      "ticket_code": "VT-O-OPEN",
      "vehicle": {
        "color": "Pearl White",
        "id": "b88418a6-7799-5f31-856f-fd424e58a176",
        "make": "Tata",
        "model": "Nexon",
        "plate_number": "UP 78 AB 1234",
        "status": "IN_CUSTODY",
        "year": 2023
      },
      "vehicle_id": "b88418a6-7799-5f31-856f-fd424e58a176"
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.957613
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/tickets/{ticket_id}/check-in`

**Hand the vehicle over and open custody**

Opens the custody session, assigns a parking zone and records the drop-off condition. Zone occupancy increments by one.

`zone_id` is required — the zone is chosen at handover, not at issue time, because that is when the valet knows where the car is actually going.

The session starts at `PARKED`. Both `status` and `stored_status` move: the ticket projects to `IN_CUSTODY` while the stored value becomes `ACTIVE`.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_valet` in `services/tickets.py`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `ticket_id` | `uuid` | The ticket's UUID (not the human-readable `ticket_code`). |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `zone_id` | `uuid` | **yes** | — |
| `odometer` | `integer` | no | (minimum 0) |
| `fuel_level` | `string` | no | — |
| `notes` | `string` | no | (maxLength 500) |
| `images` | `array` | no | Drop-off photo references returned by `POST /api/v1/uploads/images`. |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/tickets/{ticket_id}/check-in \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"zone_id": "<zone uuid>", "odometer": 41200, "notes": "Keys and RC received."}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is a customer. |
| `404` | No such ticket or zone. |
| `409` | The ticket already has an open custody session. |
| `422` | Missing `zone_id`. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "message": "Vehicle checked in. Custody session started.",
    "ticket": {
      "actual_drop_off_at": "2026-09-12T16:01:46.730231+00:00",
      "actual_pickup_at": null,
      "custody_session": {
        "assigned_valet_id": null,
        "condition_accepted": null,
        "dropped_off_at": "2026-09-12T16:01:46.730231+00:00",
        "id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
        "is_open": true,
        "picked_up_at": null,
        "retrieval_requested_at": null,
        "status": "PARKED",
        "ticket_id": "e99aeb57-d900-497a-9c75-638e53ec25bd",
        "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a",
        "zone_assigned_at": "2026-09-12T16:01:46.730231+00:00",
        "zone_id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf"
      },
      "customer": {
        "email": "docprobe.799e6643@example.com",
        "full_name": "Doc Probe",
        "id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
        "phone": "9876543210"
      },
      "customer_id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
      "expected_return_at": null,
      "id": "e99aeb57-d900-497a-9c75-638e53ec25bd",
      "issued_at": "2026-09-12T16:01:46.687251+00:00",
      "parking_zone": {
        "code": "DOC8F74F",
        "id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf",
        "name": "Documentation probe zone"
      },
      "qr_code_path": "qr/8818cafe94d548fd8ea34f100ceb9f4c.png",
      "qr_payload": "VALET:VT-202609-UFRWWV",
      "qr_url": "/media/qr/8818cafe94d548fd8ea34f100ceb9f4c.png",
      "special_instructions": null,
      "status": "IN_CUSTODY",
      "stored_status": "ACTIVE",
      "ticket_code": "VT-202609-UFRWWV",
      "vehicle": {
        "color": "Graphite",
        "id": "3120854d-1367-42a9-80cd-6742eb61e33a",
        "make": "Tata",
        "model": "Nexon",
        "plate_number": "UP98AB9533",
        "status": "IN_CUSTODY",
        "year": 2022
      },
      "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a"
    }
  },
  "meta": {
    "request_id": "9fe3105551a84f90a0157540993d59d3",
    "server_time": 1789228906.7502322
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/tickets/{ticket_id}/complete`

**Return the vehicle and close the ticket**

Closes the custody session, frees the parking slot and records the condition the customer accepted. Zone occupancy decrements and the vehicle returns to `REGISTERED`.

This is the **only** way to close custody. Sending `CHECKED_OUT` to `PATCH /custody-sessions/{id}` is refused with `422`, because completing a pickup has to settle the ticket, the zone occupancy and the vehicle status together — doing it in three calls would let them disagree.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_valet` in `services/tickets.py`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `ticket_id` | `uuid` | The ticket's UUID (not the human-readable `ticket_code`). |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `odometer_at_pickup` | `integer` | no | (minimum 0) |
| `condition_accepted` | `boolean` | no | Whether the customer accepted the vehicle's condition. `false` marks the session disputed. |
| `condition_notes` | `string` | no | (maxLength 500) |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/tickets/{ticket_id}/complete \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"odometer_at_pickup": 41320, "condition_accepted": true}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is a customer. |
| `404` | No such ticket. |
| `409` | The ticket has no open custody session to close. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "message": "Ticket VT-202609-UFRWWV completed.",
    "ticket": {
      "actual_drop_off_at": "2026-09-12T16:01:46.730231+00:00",
      "actual_pickup_at": "2026-09-12T16:01:47.648270+00:00",
      "custody_session": {
        "assigned_valet_id": null,
        "condition_accepted": true,
        "dropped_off_at": "2026-09-12T16:01:46.730231+00:00",
        "id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
        "is_open": false,
        "picked_up_at": "2026-09-12T16:01:47.648270+00:00",
        "retrieval_requested_at": "2026-09-12T16:01:46.762336+00:00",
        "status": "CHECKED_OUT",
        "ticket_id": "e99aeb57-d900-497a-9c75-638e53ec25bd",
        "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a",
        "zone_assigned_at": "2026-09-12T16:01:46.730231+00:00",
        "zone_id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf"
      },
      "customer": {
        "email": "docprobe.799e6643@example.com",
        "full_name": "Doc Probe",
        "id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
        "phone": "9876543210"
      },
      "customer_id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
      "expected_return_at": null,
      "id": "e99aeb57-d900-497a-9c75-638e53ec25bd",
      "issued_at": "2026-09-12T16:01:46.687251+00:00",
      "parking_zone": {
        "code": "DOC8F74F",
        "id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf",
        "name": "Documentation probe zone"
      },
      "qr_code_path": "qr/8818cafe94d548fd8ea34f100ceb9f4c.png",
      "qr_payload": "VALET:VT-202609-UFRWWV",
      "qr_url": "/media/qr/8818cafe94d548fd8ea34f100ceb9f4c.png",
      "special_instructions": null,
      "status": "COMPLETED",
      "stored_status": "COMPLETED",
      "ticket_code": "VT-202609-UFRWWV",
      "vehicle": {
        "color": "Graphite",
        "id": "3120854d-1367-42a9-80cd-6742eb61e33a",
        "make": "Tata",
        "model": "Nexon",
        "plate_number": "UP98AB9533",
        "status": "REGISTERED",
        "year": 2022
      },
      "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a"
    }
  },
  "meta": {
    "request_id": "f078040f97c84ca1beebb4891df37cc9",
    "server_time": 1789228907.6574728
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/tickets/{ticket_id}/cancel`

**Void a ticket**

Cancels the ticket and closes any open custody session, freeing the zone slot. The reason is recorded so a cancelled handover can be explained later rather than simply vanishing.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_valet` in `services/tickets.py`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `ticket_id` | `uuid` | The ticket's UUID (not the human-readable `ticket_code`). |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `reason` | `string` | no | (maxLength 500) |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/tickets/{ticket_id}/cancel \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"reason": "Customer changed plans."}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is a customer. |
| `404` | No such ticket. |
| `409` | The ticket is already completed or cancelled. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "message": "Ticket VT-202609-N43GGT cancelled.",
    "ticket": {
      "actual_drop_off_at": null,
      "actual_pickup_at": null,
      "custody_session": null,
      "customer": {
        "email": "docprobe.799e6643@example.com",
        "full_name": "Doc Probe",
        "id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
        "phone": "9876543210"
      },
      "customer_id": "a280053c-b2ae-45b6-8541-3dbc8d459166",
      "expected_return_at": null,
      "id": "526593fb-d12f-4019-8d69-0f41658b2921",
      "issued_at": "2026-09-12T16:01:47.660567+00:00",
      "qr_code_path": "qr/674e9ae129b5433eb52e11b73904300d.png",
      "qr_payload": "VALET:VT-202609-N43GGT",
      "qr_url": "/media/qr/674e9ae129b5433eb52e11b73904300d.png",
      "special_instructions": null,
      "status": "CANCELLED",
      "stored_status": "CANCELLED",
      "ticket_code": "VT-202609-N43GGT",
      "vehicle": {
        "color": "Graphite",
        "id": "3120854d-1367-42a9-80cd-6742eb61e33a",
        "make": "Tata",
        "model": "Nexon",
        "plate_number": "UP98AB9533",
        "status": "REGISTERED",
        "year": 2022
      },
      "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a"
    }
  },
  "meta": {
    "request_id": "8626f67c9255471b9d87089b9bb4d519",
    "server_time": 1789228907.685914
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Custody

The custody session state machine and parking zones.

### `GET /api/v1/custody-sessions/{session_id}`

**Fetch one custody session**

The session's current state and its lifecycle timestamps.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `session_id` | `uuid` | The custody session's UUID. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such session, or it is not yours. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "custody_session": {
      "assigned_valet_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
      "condition_accepted": null,
      "dropped_off_at": "2026-09-12T13:24:30+00:00",
      "id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
      "is_open": true,
      "picked_up_at": null,
      "retrieval_requested_at": "2026-09-12T17:34:30+00:00",
      "status": "PARKED",
      "ticket_id": "233e028c-6271-5a88-a32b-e63021d7c5fd",
      "vehicle_id": "b88418a6-7799-5f31-856f-fd424e58a176",
      "zone_assigned_at": "2026-09-12T13:30:30+00:00",
      "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
    }
  },
  "meta": {
    "request_id": "9f28f71f70d74eb5b3481ce35eaec621",
    "server_time": 1789225630.9639184
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `PATCH /api/v1/custody-sessions/{session_id}`

**Move a custody session to a new state**

Every write is checked against the permitted transition table, and an illegal jump is refused with **409** carrying `{from, to, allowed}` so a client can recover instead of guessing.

Permitted transitions:

```
CHECKED_IN            -> PARKED, IN_TRANSIT
PARKED                -> IN_TRANSIT, RETRIEVAL_REQUESTED, READY_FOR_PICKUP
IN_TRANSIT            -> PARKED, RETRIEVAL_IN_PROGRESS, READY_FOR_PICKUP
RETRIEVAL_REQUESTED   -> RETRIEVAL_IN_PROGRESS, READY_FOR_PICKUP
RETRIEVAL_IN_PROGRESS -> READY_FOR_PICKUP
READY_FOR_PICKUP      -> CHECKED_OUT, RETRIEVAL_IN_PROGRESS
DISPUTED              -> CHECKED_OUT
CHECKED_OUT           -> (terminal)
```

`CHECKED_OUT` is not reachable through this route at all: it returns `422` directing the caller to `POST /tickets/{id}/complete`.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_valet` in `services/tickets.py`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `session_id` | `uuid` | The custody session's UUID. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `status` | `enum` | **yes** | One of: `CHECKED_IN`, `PARKED`, `IN_TRANSIT`, `RETRIEVAL_REQUESTED`, `RETRIEVAL_IN_PROGRESS`, `READY_FOR_PICKUP`, `DISPUTED` |

*Example request*

```bash
curl -X PATCH http://localhost:5000/api/v1/custody-sessions/{session_id} \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"status": "RETRIEVAL_IN_PROGRESS"}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is a customer. |
| `404` | No such session. |
| `409` | Illegal transition. `details` carries `{from, to, allowed}`. |
| `422` | `status` is `CHECKED_OUT` — use the ticket completion endpoint instead. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "custody_session": {
      "assigned_valet_id": null,
      "condition_accepted": null,
      "dropped_off_at": "2026-09-12T16:01:46.730231+00:00",
      "id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
      "is_open": true,
      "picked_up_at": null,
      "retrieval_requested_at": "2026-09-12T16:01:46.762336+00:00",
      "status": "RETRIEVAL_IN_PROGRESS",
      "ticket_id": "e99aeb57-d900-497a-9c75-638e53ec25bd",
      "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a",
      "zone_assigned_at": "2026-09-12T16:01:46.730231+00:00",
      "zone_id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf"
    },
    "message": "Custody session is now RETRIEVAL_IN_PROGRESS."
  },
  "meta": {
    "request_id": "477c3a902f6a47a6ac679c0ffc9058d8",
    "server_time": 1789228906.8251157
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Inspections

Drop-off and pickup photo capture, comparison and verification.

### `POST /api/v1/uploads/images`

**Store images and get validated references back**

Accepts `multipart/form-data`. Send one or more files under the `images` field (a single `image` field also works).

Each file is validated before it is stored: extension allow-list (`jpg`, `jpeg`, `png`, `webp`), minimum 64 pixels per side, maximum 4096. The stored reference is returned with its SHA-256 digest, so an inspection can later prove which bytes it assessed.

The returned `path` values are what the inspection endpoints expect.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Request body** (`multipart/form-data`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `images` | `array` | no | One or more image files. |
| `image` | `binary` | no | Alternative single-file field. |
| `subdir` | `string` | no | — |
| `label` | `string` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/uploads/images \
  -H 'Authorization: Bearer <access_token>' \
  -F 'custody_id=<uuid>' \
  -F 'images=@front.png' \
  -F 'angles=FRONT'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `413` | A file exceeds `MAX_CONTENT_LENGTH_MB` (15 by default). |
| `415` | A file is not in the allowed extension list. |
| `422` | An image is smaller than 64 pixels on a side. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "images": [
      {
        "byte_size": 346,
        "captured_at": "2026-09-12T16:01:46.861776+00:00",
        "height": 120,
        "mime_type": "image/png",
        "path": "inspections/2026/09/12/8447673ffd1c44f8a782a2d48c242806.png",
        "sha256": "bd005d02a1d757cce387fadf4dd44d12f4d24b452b686836d3633880bee1b2ae",
        "width": 120
      }
    ],
    "message": "Stored 1 image(s).",
    "total": 1
  },
  "meta": {
    "request_id": "fc205c6a468f46438b40b4ec17ff5d18",
    "server_time": 1789228906.8618646
  },
  "success": true
}
```

*Example `413` response*

```json
{
  "success": false,
  "error": {
    "code": "PAYLOAD_TOO_LARGE",
    "message": "The request body is too large.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/inspections/dropoff`

**Capture the drop-off photo set**

Records a `DROP_OFF` inspection against a custody session.

This is the baseline every later comparison is measured against. Nothing is compared at this stage, because there is no *after* yet.

One angle is sent per photo as a **parallel `angles` list**. A count mismatch is rejected rather than guessed, because applying one angle to a mixed set would compare unrelated viewpoints and flag the whole vehicle.

Findings are candidates for human review, never a confirmed assessment.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_inspection_role` (`INSPECTION_ROLES`). |

**Request body** (`multipart/form-data`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `custody_id` | `string` | **yes** | The custody **session** id. |
| `images` | `array` | **yes** | — |
| `angles` | `array` | **yes** | One entry per image, in the same order. |
| `notes` | `string` | no | (maxLength 1000) |
| `odometer` | `integer` | no | — |
| `location` | `string` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/inspections/dropoff \
  -H 'Authorization: Bearer <access_token>' \
  -F 'custody_id=<uuid>' \
  -F 'images=@front.png' \
  -F 'angles=FRONT'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | The caller is a customer. |
| `404` | No such custody session. |
| `415` | A file is not an allowed image type. |
| `422` | The angle count does not match the image count, or an image is below the pixel floor. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "captured_at": "2026-09-12T16:01:46.871934+00:00",
    "comparisons": [],
    "confirmed_count": 0,
    "created": false,
    "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
    "damage_count": 0,
    "detections": [],
    "id": "a6456b3d-1558-4bdc-9ebf-36d5f03814b9",
    "inspector_notes": "Clean.",
    "is_new_damage": false,
    "location": null,
    "odometer_reading": null,
    "outcome": "CLEAN",
    "photo_count": 2,
    "photos": [
      {
        "angle": "FRONT",
        "byte_size": 407,
        "captured_at": "2026-09-12T16:01:46.870546+00:00",
        "height": 140,
        "sha256": "04953d98db28ab8abcc482bbea901164b21099c417bcbd916ff848ea46403669",
        "url": "/media/inspections/drop_off/2026/09/12/9e2f45d325ac4e45907ea2948f3ed5b9.png",
        "width": 140
      },
      {
        "angle": "REAR",
        "byte_size": 407,
        "captured_at": "2026-09-12T16:01:46.871048+00:00",
        "height": 140,
        "sha256": "04953d98db28ab8abcc482bbea901164b21099c417bcbd916ff848ea46403669",
        "url": "/media/inspections/drop_off/2026/09/12/63242fa95ffa4fe5a4a1bafd61937d5c.png",
        "width": 140
      }
    ],
    "rejected_count": 0,
    "reviewed_at": null,
    "stage": "DROP_OFF",
    "unmatched_angles": [],
    "unverified_count": 0
  },
  "meta": {
    "request_id": "93da34e99c664eabba35c8751ac4ade4",
    "server_time": 1789228906.8791866
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/inspections/pickup`

**Capture the pickup photo set**

Records a `PICKUP` inspection against a custody session.

Each angle is compared against its drop-off counterpart **in the same request**, so the response already carries the candidate findings, the rendered comparison composite and a per-finding evidence crop.

Angles with no drop-off counterpart are listed in `unmatched_angles` rather than silently dropped.

One angle is sent per photo as a **parallel `angles` list**. A count mismatch is rejected rather than guessed, because applying one angle to a mixed set would compare unrelated viewpoints and flag the whole vehicle.

Findings are candidates for human review, never a confirmed assessment.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_inspection_role` (`INSPECTION_ROLES`). |

**Request body** (`multipart/form-data`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `custody_id` | `string` | **yes** | The custody **session** id. |
| `images` | `array` | **yes** | — |
| `angles` | `array` | **yes** | One entry per image, in the same order. |
| `notes` | `string` | no | (maxLength 1000) |
| `odometer` | `integer` | no | — |
| `location` | `string` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/inspections/pickup \
  -H 'Authorization: Bearer <access_token>' \
  -F 'custody_id=<uuid>' \
  -F 'images=@front.png' \
  -F 'angles=FRONT'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | The caller is a customer. |
| `404` | No such custody session. |
| `415` | A file is not an allowed image type. |
| `422` | The angle count does not match the image count, or an image is below the pixel floor. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "captured_at": "2026-09-12T16:01:46.888981+00:00",
    "comparisons": [
      {
        "alignment": {
          "applied": false,
          "inliers": 0,
          "matches": 0,
          "quality": 0.0,
          "reason": "No features could be detected in one of the images. Plain, uniformly lit panels give ORB nothing to match."
        },
        "angle": "FRONT",
        "comparison_url": "/media/comparisons/2026/09/12/3007b79f81cc42fcb6b5ee8d5a6ede64.png",
        "damage_detected": false,
        "drop_off_url": "/media/inspections/drop_off/2026/09/12/9e2f45d325ac4e45907ea2948f3ed5b9.png",
        "findings": [],
        "parameters": {
          "bloblike_aspect_ratio": 2.5,
          "border_margin_fraction": 0.015,
          "chroma_weight": 1.0,
          "dent_bipolarity": 0.7,
          "difference_blur_kernel": 5,
          "difference_threshold": 22.0,
          "diffuse_mean_ceiling": 12.0,
          "diffuse_region_fraction": 0.35,
          "edge_floor_minimum": 3.0,
          "edge_floor_percentile": 99.0,
          "edge_weight": 0.8,
          "elongated_aspect_ratio": 3.0,
          "lighting_correction_strength": 0.85,
          "low_alignment_penalty": 0.6,
          "luminance_floor": 8.0,
          "luminance_weight": 1.0,
          "match_ratio": 0.75,
          "max_gain_deviation": 0.45,
          "merge_iou": 0.3,
          "min_alignment_quality": 0.25,
          "min_confidence": 0.05,
          "min_image_dimension": 96,
          "min_matches": 12,
          "min_region_area_fraction": 0.0006,
          "morph_kernel_size": 5,
          "new_edge_saturation": 2.0,
          "orb_features": 2000,
          "pre_existing_edge_ceiling": 40.0,
          "ransac_threshold": 4.0,
          "residue_chroma_fraction": 0.5,
          "saturation_difference": 70.0,
          "significant_chroma": 14.0,
          "significant_luminance_change": 10.0,
          "sobel_kernel": 3,
          "version": "classical-cv/1.0.0",
          "working_width": 1024
        },
        "pickup_url": "/media/inspections/pickup/2026/09/12/3209920f4e044adb847f9b6c95787e89.png",
        "statistics": {
          "after": {
            "height": 140,
            "orientation_applied": false,
            "source_height": 140,
            "source_width": 140,
            "width": 140
          },
          "alignment": {
            "applied": false,
            "inliers": 0,
            "matches": 0,
            "quality": 0.0,
            "reason": "No features could be detected in one of the images. Plain, uniformly lit panels give ORB nothing to match."
          },
          "before": {
            "height": 140,
            "orientation_applied": false,
            "source_height": 140,
            "source_width": 140,
            "width": 140
          },
          "changed_pixel_fraction": 0.0,
          "evidence_crops_rendered": 0,
          "evidence_crops_skipped": 0,
          "frame": {
            "height": 140,
            "width": 140
          },
          "luminance_gain_applied": 1.0,
          "viewpoint": "FRONT"
        },
        "warnings": [
          "Alignment was not applied: No features could be detected in one of the images. Plain, uniformly lit panels give ORB nothing to match. Regions may reflect camera movement rather than changes to the vehicle.",
          "No region exceeded the detection thresholds. This is not a guarantee that the vehicle is undamaged -- it means nothing stood out against these parameters."
        ]
      },
      {
        "alignment": {
          "applied": false,
          "inliers": 0,
          "matches": 0,
          "quality": 0.0,
          "reason": "No features could be detected in one of the images. Plain, uniformly lit panels give ORB nothing to match."
        },
        "angle": "REAR",
        "comparison_url": "/media/comparisons/2026/09/12/a27f710051fa4192802271e06e9cea3f.png",
        "damage_detected": false,
        "drop_off_url": 
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/inspections/{custody_id}`

**The full condition report for a custody session**

Both photo sets, every finding, and the rendered evidence. The response is **flat** — `drop_off` and `pickup` sit at the top level rather than inside a wrapper list.

`detector_disclaimer` travels with every report, so a client cannot render the findings without the caveat that produced them.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `custody_id` | `uuid` | The custody **session** UUID — not an inspection id or a ticket id. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such custody session, or it is not yours. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "comparison_ready": true,
    "confirmed_count": 0,
    "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
    "detector_disclaimer": "Findings are produced by classical OpenCV image differencing, not a trained damage classifier. Unverified findings are potential damage and require human confirmation.",
    "drop_off": {
      "captured_at": "2026-09-12T13:24:30+00:00",
      "comparisons": [],
      "confirmed_count": 0,
      "created": false,
      "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
      "damage_count": 0,
      "detections": [
        {
          "algorithm_params": {
            "canny_high": 150,
            "canny_low": 60,
            "lab_delta_threshold": 18.0,
            "min_contour_area_px": 420
          },
          "angle": null,
          "bounding_box": {
            "height": 0.09,
            "width": 0.14,
            "x": 0.31,
            "y": 0.58
          },
          "comparison_image_path": "inspections/demo/4fc8aafe-bf35-5649-a906-8affcdb6d3ce-comparison-0.jpg",
          "comparison_url": "/media/inspections/demo/4fc8aafe-bf35-5649-a906-8affcdb6d3ce-comparison-0.jpg",
          "confidence": 0.82,
          "damage_type": "SCRATCH",
          "detection_reasoning": "Hairline scratch, ~9 cm, visible under raking light.",
          "evidence_image_path": "inspections/demo/4fc8aafe-bf35-5649-a906-8affcdb6d3ce-evidence-0.jpg",
          "evidence_url": "/media/inspections/demo/4fc8aafe-bf35-5649-a906-8affcdb6d3ce-evidence-0.jpg",
          "id": "47682e83-c892-54c4-b2b1-61d5abc5cf7c",
          "inspection_id": "4fc8aafe-bf35-5649-a906-8affcdb6d3ce",
          "is_pre_existing": true,
          "is_verified": false,
          "method": "CLASSICAL_CV",
          "model_version": null,
          "presentation_label": "Potential Damage",
          "region": "LEFT_FRONT_DOOR",
          "requires_customer_attention": false,
          "severity": "COSMETIC",
          "type_confidence": null,
          "verification_notes": null,
          "verification_status": "UNVERIFIED",
          "verified_at": null,
          "verified_by_id": null
        }
      ],
      "id": "4fc8aafe-bf35-5649-a906-8affcdb6d3ce",
      "inspector_notes": "[DEMO] Condition recorded by the seeded demonstration dataset.",
      "is_new_damage": false,
      "location": "Inspection Bay 2",
      "odometer_reading": 24310,
      "outcome": "REVIEWED",
      "photo_count": 5,
      "photos": [
        {
          "angle": "5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-front_left",
          "byte_size": 248371,
          "captured_at": "2026-09-07T09:12:44+00:00",
          "height": 4032,
          "sha256": "1f2d724888480a934ef3a823243c150d7b883ce0a91a1b731eeb2e4b797aeec5",
          "url": "/media/inspections/demo/5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-front_left.jpg",
          "width": 3024
        },
        {
          "angle": "5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-front_right",
          "byte_size": 248371,
          "captured_at": "2026-09-07T09:12:44+00:00",
          "height": 4032,
          "sha256": "28f2c19bf7d157f494c7f80985361de63e9399b082fb798947974a56d2cce4bd",
          "url": "/media/inspections/demo/5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-front_right.jpg",
          "width": 3024
        },
        {
          "angle": "5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-rear_left",
          "byte_size": 248371,
          "captured_at": "2026-09-07T09:12:44+00:00",
          "height": 4032,
          "sha256": "b298697cdd1d23cc91b71f9de082f97ea41d682af9e49bab6a2f9562e48bac1d",
          "url": "/media/inspections/demo/5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-rear_left.jpg",
          "width": 3024
        },
        {
          "angle": "5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-rear_right",
          "byte_size": 248371,
          "captured_at": "2026-09-07T09:12:44+00:00",
          "height": 4032,
          "sha256": "b65abae
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `PATCH /api/v1/inspections/detections/{detection_id}/verification`

**Confirm or reject a damage finding**

Moves a finding between `UNVERIFIED`, `CONFIRMED` and `REJECTED`.

Deliberately narrower than capture: the person who photographed the car should not be the only person who judges whether the algorithm was right about it.

`presentation_label` changes with the status — `Potential Damage`, `Confirmed Damage`, `Dismissed - Not Damage`.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_verifier` (`VERIFICATION_ROLES`). |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `detection_id` | `uuid` | The damage finding's UUID. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `status` | `enum` | **yes** | One of: `UNVERIFIED`, `CONFIRMED`, `REJECTED` |
| `notes` | `string` | no | (maxLength 500) |

*Example request*

```bash
curl -X PATCH http://localhost:5000/api/v1/inspections/detections/{detection_id}/verification \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"status": "CONFIRMED", "notes": "Confirmed on review."}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is an operator or a customer. |
| `404` | No such detection. |
| `422` | Unknown status value. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "algorithm_params": {
      "alignment": {
        "applied": true,
        "inliers": 30,
        "matches": 30,
        "quality": 1.0,
        "reason": null
      },
      "angle": "FRONT",
      "pipeline": {
        "bloblike_aspect_ratio": 2.5,
        "border_margin_fraction": 0.015,
        "chroma_weight": 1.0,
        "dent_bipolarity": 0.7,
        "difference_blur_kernel": 5,
        "difference_threshold": 22.0,
        "diffuse_mean_ceiling": 12.0,
        "diffuse_region_fraction": 0.35,
        "edge_floor_minimum": 3.0,
        "edge_floor_percentile": 99.0,
        "edge_weight": 0.8,
        "elongated_aspect_ratio": 3.0,
        "lighting_correction_strength": 0.85,
        "low_alignment_penalty": 0.6,
        "luminance_floor": 8.0,
        "luminance_weight": 1.0,
        "match_ratio": 0.75,
        "max_gain_deviation": 0.45,
        "merge_iou": 0.3,
        "min_alignment_quality": 0.25,
        "min_confidence": 0.05,
        "min_image_dimension": 96,
        "min_matches": 12,
        "min_region_area_fraction": 0.0006,
        "morph_kernel_size": 5,
        "new_edge_saturation": 2.0,
        "orb_features": 2000,
        "pre_existing_edge_ceiling": 40.0,
        "ransac_threshold": 4.0,
        "residue_chroma_fraction": 0.5,
        "saturation_difference": 70.0,
        "significant_chroma": 14.0,
        "significant_luminance_change": 10.0,
        "sobel_kernel": 3,
        "version": "classical-cv/1.0.0",
        "working_width": 1024
      },
      "region_mapping": {
        "basis": "Mapped from the middle-right third of a front view, using a 3x3 grid. This is a coarse geometric mapping, not part segmentation.",
        "grid_cell": [
          1,
          2
        ],
        "mapped": true,
        "region": "HOOD",
        "viewpoint": "FRONT"
      },
      "region_statistics": {
        "area": 543,
        "area_fraction": 0.013575,
        "aspect_ratio": 1.769,
        "bbox": [
          99,
          93,
          69,
          39
        ],
        "fill_ratio": 0.2018,
        "fragments": 1,
        "mean_abs_delta_l": 39.042,
        "mean_chroma": 3.451,
        "mean_delta_a": 0.488,
        "mean_delta_b": 3.416,
        "mean_delta_l": 39.042,
        "mean_difference": 65.575,
        "mean_edge": 193.481,
        "mean_pre_existing_edge": 0.0,
        "normalised_bbox": {
          "height": 0.195,
          "width": 0.345,
          "x": 0.495,
          "y": 0.465
        },
        "peak_difference": 89.506
      }
    },
    "angle": "FRONT",
    "bounding_box": {
      "height": 0.195,
      "width": 0.345,
      "x": 0.495,
      "y": 0.465
    },
    "comparison_image_path": "comparisons/2026/09/12/cc3ddfdcb2a74d4982e9fa3efd3e7b25.png",
    "comparison_url": "/media/comparisons/2026/09/12/cc3ddfdcb2a74d4982e9fa3efd3e7b25.png",
    "confidence": 0.915,
    "damage_type": "DENT",
    "detection_reasoning": "Region flagged as a potential change: mean difference 65.6 (threshold 22.0), peak 89.5, covering 1.36% of the frame. Labelled DENT because a localised region changed brightness substantially (mean absolute delta L 39.0) with no colour change (chroma 3.5), which is how a depression reads: it alters how the panel catches light without altering the paint. Bipolar shading scores 0.00, which is lower than a textbook depression would give, so this label is weak. This label is a heuristic reading of shape and colour statistics, not a classification by a trained model, and needs human confirmation. Mapped from the middle-right third of a front view, using a 3x3 grid. This is a coarse geometric mapping, not part segmentation.",
    "evidence_image_path": "evidence/2026/09/12/a47cc03f25d6434b8ad639a243ebb19e.png",
    "evidence_url": "/media/evidence/2026/09/12/a47cc03f25d6434b8ad639a243ebb19e.png",
    "id": "102df4b4-fc1f-447f-85fa-1a1192d7bb71",
    "inspection_id": "feb48b3b-5c10-4c73-8740-f9536dffb363",
    "is_pre_existing": false,
    "is_v
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/inspections/demo/scenarios`

**The synthetic scenarios the demo can run**

The scenario list for `POST /api/v1/inspections/demo`, served by the API rather than hard-coded in the client so the two cannot drift.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "note": "Demo photographs are generated by OpenCV. Findings produced from them are real pipeline output, not canned results.",
    "scenarios": [
      {
        "description": "The same panel photographed twice with no change. The pipeline must report nothing; if it reports something here, it is producing false positives.",
        "label": "No damage - identical conditions",
        "name": "clean"
      },
      {
        "description": "The pickup photo is 18% brighter, as if a cloud moved. Nothing changed on the vehicle. This is the scenario the lighting normalisation and diffuse-region filters exist to survive.",
        "label": "Lighting change only - no damage",
        "name": "lighting_only"
      },
      {
        "description": "A thin scuff drawn along the lower door. Exercises the elongated, edge-dominant branch of the labeller.",
        "label": "Scratch along the front door",
        "name": "scratch"
      },
      {
        "description": "A shading depression with no colour change. Exercises the dark-and-achromatic branch.",
        "label": "Dent on the front wing",
        "name": "dent"
      },
      {
        "description": "A warm, mottled colour shift. Exercises the chroma channel, which is the only one that can see this at all.",
        "label": "Rust patch on the lower wing",
        "name": "rust"
      },
      {
        "description": "A scratch, a dent and a paint chip, with the pickup frame shifted and rotated so alignment has to do real work. The fullest demonstration of the pipeline.",
        "label": "Three defects, camera moved",
        "name": "multiple"
      }
    ]
  },
  "meta": {
    "request_id": "c18ee048282e45f9a3e7fdc5fcce09c8",
    "server_time": 1789225630.7779763
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/inspections/demo`

**Run the real pipeline over synthetic photographs**

There are no vehicle cameras in an evaluation environment, so rather than fake the results the demo synthesises the **input** and lets the real detector run on it.

Both the photographs and the response say so plainly: `synthetic: true` and a `disclaimer` string. `planted_count` and `ground_truth` are returned so a detection can be checked against what was actually planted.

**Nothing is stored.** No inspection rows are written, so this can be run repeatedly without polluting real custody history.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `scenario` | `string` | no | A name from `GET /api/v1/inspections/demo/scenarios`. |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/inspections/demo \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"scenario": "multiple"}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `422` | Unknown scenario name. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "alignment": {
      "applied": true,
      "inliers": 34,
      "matches": 37,
      "quality": 0.9189,
      "reason": null
    },
    "angle": "left_side",
    "available_scenarios": [
      {
        "description": "The same panel photographed twice with no change. The pipeline must report nothing; if it reports something here, it is producing false positives.",
        "label": "No damage - identical conditions",
        "name": "clean"
      },
      {
        "description": "The pickup photo is 18% brighter, as if a cloud moved. Nothing changed on the vehicle. This is the scenario the lighting normalisation and diffuse-region filters exist to survive.",
        "label": "Lighting change only - no damage",
        "name": "lighting_only"
      },
      {
        "description": "A thin scuff drawn along the lower door. Exercises the elongated, edge-dominant branch of the labeller.",
        "label": "Scratch along the front door",
        "name": "scratch"
      },
      {
        "description": "A shading depression with no colour change. Exercises the dark-and-achromatic branch.",
        "label": "Dent on the front wing",
        "name": "dent"
      },
      {
        "description": "A warm, mottled colour shift. Exercises the chroma channel, which is the only one that can see this at all.",
        "label": "Rust patch on the lower wing",
        "name": "rust"
      },
      {
        "description": "A scratch, a dent and a paint chip, with the pickup frame shifted and rotated so alignment has to do real work. The fullest demonstration of the pipeline.",
        "label": "Three defects, camera moved",
        "name": "multiple"
      }
    ],
    "comparison_url": "/media/demo/comparisons/2026/09/12/cc648bad539041d58ff11a7ceb4e305f.png",
    "damage_detected": true,
    "detected_count": 3,
    "detections": [
      {
        "bounding_box": {
          "height": 0.025,
          "width": 0.208333,
          "x": 0.475,
          "y": 0.589063
        },
        "comparison_url": "/media/demo/comparisons/2026/09/12/cc648bad539041d58ff11a7ceb4e305f.png",
        "confidence": 0.925,
        "damage_type": "SCRATCH",
        "detection_reasoning": "Region flagged as a potential change: mean difference 85.5 (threshold 22.0), peak 134.0, covering 0.39% of the frame. Labelled SCRATCH because the region is thin and stretched (aspect ratio 12.5, fill 0.75) and the change is concentrated in new edges rather than in colour. This label is a heuristic reading of shape and colour statistics, not a classification by a trained model, and needs human confirmation. Mapped from the middle-centre third of a left side view, using a 3x3 grid. This is a coarse geometric mapping, not part segmentation.",
        "evidence_url": "/media/demo/evidence/2026/09/12/c304ada2a6ac4f81bb080b73c478b28a.png",
        "is_verified": false,
        "method": "CLASSICAL_CV",
        "pixel_area": 2403,
        "presentation_label": "Potential Damage",
        "region": "LEFT_FRONT_DOOR",
        "region_mapping": {
          "basis": "Mapped from the middle-centre third of a left side view, using a 3x3 grid. This is a coarse geometric mapping, not part segmentation.",
          "grid_cell": [
            1,
            1
          ],
          "mapped": true,
          "region": "LEFT_FRONT_DOOR",
          "viewpoint": "LEFT_SIDE"
        },
        "severity": "SEVERE",
        "statistics": {
          "area": 2403,
          "area_fraction": 0.003911,
          "aspect_ratio": 12.5,
          "bbox": [
            456,
            377,
            200,
            16
          ],
          "fill_ratio": 0.7509,
          "fragments": 1,
          "mean_abs_delta_l": 48.877,
          "mean_chroma": 35.319,
          "mean_delta_a": -29.466,
          "mean_delta_b": -19.313,
          "mean_delta_l": 30.535,
          "mean_difference": 85.501,
          "mean_edge": 200.29,
          "mean_pre_existing_edge": 10.094,
          "
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## GPS

Telemetry ingestion, simulation and the live tracking view.

### `POST /api/v1/gps/events`

**Record position fixes**

Accepts either a single fix object or `{"fixes": [...]}`. Both shapes reach the same service path, so a batch is classified exactly as its members would be individually.

Every fix is passed through the real geofence classifier on the way in; the response reports the verdict, the rules that fired and any alerts raised.

The `gps_events` table is append-only: a recorded fix cannot be edited or deleted afterwards.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_role(TELEMETRY_ROLES)` in `services/geofence.py`. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `fixes` | `array` | no | — |
| `source` | `enum` | no | One of: `SIMULATED`, `HARDWARE`, `MANUAL` |
| `device_id` | `string` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/gps/events \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"source": "SIMULATED", "fixes": [{"custody_session_id": "<custody session uuid>", "latitude": 28.6139, "longitude": 77.209, "speed_kmph": 0.0}]}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | The caller is a customer. |
| `404` | No such custody session. |
| `422` | A coordinate is missing or out of range. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "accepted": 1,
    "alerts_raised": 0,
    "results": [
      {
        "alerts_raised": [],
        "alerts_suppressed": [],
        "escalated_by_ml": false,
        "evidence": {
          "breach_margin_metres": 150.0,
          "buffer_metres": 25.0,
          "distance_from_boundary_metres": -59.92,
          "movement_expected": true,
          "prior_fix_count": 0,
          "rules_fired": [],
          "speed_kmph": 0.0,
          "speed_limit_applies": true,
          "zone_speed_limit_kmph": 20.0
        },
        "gps_event": {
          "accuracy_metres": null,
          "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
          "device_id": "doc-probe-device",
          "distance_from_zone_metres": -59.92,
          "heading_degrees": 90.0,
          "id": "1df16ed0-d81f-4929-9e80-5a00540ee4ad",
          "is_simulated": true,
          "is_within_zone": true,
          "latitude": 28.6139,
          "longitude": 77.209,
          "ml": {
            "classification": "NORMAL",
            "model_version": "security-prototype/1.0.0",
            "probability": 0.00011052289786074517
          },
          "movement_status": "NORMAL",
          "recorded_at": "2026-09-12T10:00:00+00:00",
          "source": "SIMULATED",
          "speed_kmph": 0.0,
          "zone_id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf"
        },
        "ml": {
          "classification": "NORMAL",
          "disclaimer": "Prototype classifier trained and evaluated on synthetic telemetry whose labels were written by hand. It has never seen a real vehicle and must not be represented as a production model or relied on as the sole basis for a security decision. Rule-based geofence checks remain authoritative.",
          "model_version": "security-prototype/1.0.0",
          "probability": 0.00011052289786074517,
          "reason": "Prototype classifier: 0% chance of suspicious behaviour (logistic). Nothing in this fix stood out against the synthetic examples it was trained on. Contribution basis: exact log-odds contributions.",
          "severity": "LOW",
          "top_factors": [
            {
              "feature": "movement_duration_seconds",
              "weight": -2.5288484790253327
            },
            {
              "feature": "direction_change_degrees",
              "weight": -2.324606113215771
            },
            {
              "feature": "speed_kmph",
              "weight": -2.065015311567173
            },
            {
              "feature": "distance_travelled_metres",
              "weight": -1.8743660093322252
            },
            {
              "feature": "zone_breach",
              "weight": -1.4522150833428518
            }
          ]
        },
        "movement_status": "NORMAL",
        "reasons": [],
        "rule_movement_status": "NORMAL"
      }
    ]
  },
  "meta": {
    "request_id": "ca1ef2e486fe4babaf9c74ae870b8e1a",
    "server_time": 1789228907.505561
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/gps/simulate/scenarios`

**The tracks the simulator can generate**

The scenario identifiers accepted by `POST /api/v1/gps/simulate` (`arrival`, `jitter`, `parked`, `retrieval`, `telemetry_loss`, `unauthorised`), served by the API rather than duplicated in the client.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "scenarios": [
      {
        "id": "parked",
        "label": "Parked in bay"
      },
      {
        "id": "jitter",
        "label": "GPS noise near the boundary"
      },
      {
        "id": "arrival",
        "label": "Valet drives the vehicle in"
      },
      {
        "id": "retrieval",
        "label": "Legitimate retrieval"
      },
      {
        "id": "unauthorised",
        "label": "Unauthorised movement"
      },
      {
        "id": "telemetry_loss",
        "label": "Tracker goes quiet"
      }
    ]
  },
  "meta": {
    "request_id": "521feec6319149d5be264de46df5d943",
    "server_time": 1789225630.7742841
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/gps/simulate`

**Generate a simulated track and ingest it**

Generates a track and ingests it as `SIMULATED` telemetry. Every fix passes through the **real** classification path, so the verdicts this produces are computed rather than scripted.

The response is labelled honestly and repeatedly: `source: "SIMULATED"`, `is_simulated: true`, and a `notes` array stating that the track must not be presented as hardware data. The tracking view separates `has_hardware_telemetry` from `has_simulated_telemetry` for the same reason.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_role(TELEMETRY_ROLES)` in `services/geofence.py`. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `custody_id` | `uuid` | **yes** | Also accepted as `custody_session_id`. |
| `scenario` | `enum` | no | One of: `arrival`, `jitter`, `parked`, `retrieval`, `telemetry_loss`, `unauthorised` |
| `seed` | `integer` | no | Fix the seed for a reproducible track. |
| `zone_id` | `uuid` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/gps/simulate \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"custody_id": "<custody session uuid>", "scenario": "parked", "seed": 20260912}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | The caller is a customer. |
| `404` | No such custody session. |
| `422` | Unknown scenario name. `details.allowed` lists the valid ones. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "alerts_raised": [
      {
        "acknowledged_at": null,
        "acknowledged_by_email": null,
        "acknowledged_by_id": null,
        "alert_type": "TELEMETRY_LOSS",
        "confidence": 1.0,
        "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
        "description": "Detected at 2026-09-12T15:56:17.511231+00:00 for vehicle UP98AB9533.",
        "evidence": {
          "breach_margin_metres": 150.0,
          "buffer_metres": 25.0,
          "distance_from_boundary_metres": -22.33,
          "finding": "TELEMETRY_LOSS",
          "finding_status": "WARNING",
          "fix_id": "fbf4737b-8c95-4542-bdc7-cdefe2d5d534",
          "movement_expected": true,
          "prior_fix_count": 1,
          "rules_fired": [
            {
              "alert_type": "TELEMETRY_LOSS",
              "reason": "No position reported for 356 minutes; the threshold is 5 minutes.",
              "severity": "MEDIUM",
              "status": "WARNING"
            }
          ],
          "speed_kmph": 0.11,
          "speed_limit_applies": true,
          "zone_speed_limit_kmph": 20.0
        },
        "gps_event_id": "fbf4737b-8c95-4542-bdc7-cdefe2d5d534",
        "id": "fc0f04e2-1433-4757-9db5-a68c02005c24",
        "is_open": true,
        "occurred_at": "2026-09-12T15:56:17.511231+00:00",
        "reason": "No position reported for 356 minutes; the threshold is 5 minutes.",
        "resolution_notes": null,
        "resolved_at": null,
        "resolved_by_email": null,
        "resolved_by_id": null,
        "severity": "MEDIUM",
        "status": "OPEN",
        "title": "Telemetry gap detected",
        "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a",
        "zone_id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf"
      }
    ],
    "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
    "fix_count": 12,
    "is_simulated": true,
    "notes": [
      "Every fix in this track is SIMULATED telemetry generated for development. It is labelled as such on each row and in the dashboard, and it must not be presented as hardware data."
    ],
    "source": "SIMULATED",
    "status_counts": {
      "NORMAL": 11,
      "WARNING": 1
    },
    "track": {
      "description": "A parked vehicle with ordinary GPS wander. Every fix should classify as NORMAL; this is the baseline the other scenarios are compared against.",
      "ends_at": "2026-09-12T16:07:17.511231+00:00",
      "fix_count": 12,
      "movement_expected": false,
      "notes": [
        "parked inside the geofence"
      ],
      "scenario": "parked",
      "source": "SIMULATED",
      "starts_at": "2026-09-12T16:01:47.511231+00:00"
    },
    "zone": {
      "available_slots": 8,
      "buffer_metres": 25.0,
      "capacity": 10,
      "centre": {
        "latitude": 28.6139,
        "longitude": 77.209
      },
      "code": "DOC8F74F",
      "current_occupancy": 2,
      "description": null,
      "effective_radius_metres": 60.0,
      "geofence_polygon": [
        [
          77.209,
          28.6144396
        ],
        [
          77.2090602,
          28.614437
        ],
        [
          77.2091199,
          28.6144292
        ],
        [
          77.2091784,
          28.6144164
        ],
        [
          77.2092352,
          28.6143985
        ],
        [
          77.2092898,
          28.6143759
        ],
        [
          77.2093415,
          28.6143487
        ],
        [
          77.2093899,
          28.6143171
        ],
        [
          77.2094346,
          28.6142815
        ],
        [
          77.2094751,
          28.6142423
        ],
        [
          77.2095111,
          28.6141998
        ],
        [
          77.2095421,
          28.6141544
        ],
        [
          77.2095679,
          28.6141065
        ],
        [
          77.2095882,
          28.6140566
        ],
        [
          77.2096029,
          28.6140053
        ],
        [
          77.2096117,
       
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/gps/{custody_id}`

**The live tracking view for a custody session**

Position, zone, geofence verdict, status and alerts for one session.

Note the path: this is `GET /api/v1/gps/{custody_id}` — there is no `/tracking` suffix.

`has_hardware_telemetry` and `has_simulated_telemetry` are reported separately so a client can never present simulated data as hardware data by accident.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `custody_id` | `uuid` | The custody **session** UUID — not an inspection id or a ticket id. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such custody session, or it is not yours. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "alerts": [
      {
        "acknowledged_at": "2026-09-12T13:48:30+00:00",
        "acknowledged_by_email": "manager.karan@example.com",
        "acknowledged_by_id": "24758677-b3ca-581e-bc94-9dea4082aba2",
        "alert_type": "GEOFENCE_BREACH",
        "confidence": 0.94,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "description": "[DEMO] Two consecutive fixes fell outside the assigned zone while the custody session had no retrieval in progress.",
        "evidence": {
          "buffer_metres": 25.0,
          "custody_status_at_fix": "PARKED",
          "distance_metres": 333.83,
          "movement_status": "BREACH",
          "speed_kmph": 34.0,
          "telemetry_source": "SIMULATED",
          "zone_code": "ZONE-A"
        },
        "gps_event_id": "082dd5a5-84e9-5138-bdf4-71c81cd33f35",
        "id": "6064d352-c982-5c17-b68d-7e9103481b82",
        "is_open": true,
        "occurred_at": "2026-09-12T13:42:30+00:00",
        "reason": "Fix at 27.49575, 77.67825 is 333.8 m beyond the Zone A - Covered Parking boundary, exceeding the 25 m buffer, while custody status was PARKED.",
        "resolution_notes": null,
        "resolved_at": null,
        "resolved_by_email": null,
        "resolved_by_id": null,
        "severity": "HIGH",
        "status": "ACKNOWLEDGED",
        "title": "Vehicle left ZONE-A with no task in progress",
        "vehicle_id": "b88418a6-7799-5f31-856f-fd424e58a176",
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      }
    ],
    "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
    "custody_status": "PARKED",
    "fix_count": 8,
    "has_hardware_telemetry": false,
    "has_simulated_telemetry": true,
    "last_updated_at": "2026-09-12T13:46:30+00:00",
    "latest_fix": {
      "accuracy_metres": 4.5,
      "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
      "device_id": "SIM-TELEMETRY-001",
      "distance_from_zone_metres": 469.12,
      "heading_degrees": 85.0,
      "id": "514d1f5e-bc05-566b-8d7f-17480aea5719",
      "is_simulated": true,
      "is_within_zone": false,
      "latitude": 27.49635,
      "longitude": 77.67945,
      "ml": null,
      "movement_status": "BREACH",
      "recorded_at": "2026-09-12T13:46:30+00:00",
      "source": "SIMULATED",
      "speed_kmph": 41.0,
      "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
    },
    "movement_expected": false,
    "movement_status": "BREACH",
    "open_alert_count": 1,
    "retrieval_active": false,
    "sources": [
      "SIMULATED"
    ],
    "status_counts": {
      "BREACH": 2,
      "NORMAL": 6,
      "SUSPICIOUS": 0,
      "WARNING": 0
    },
    "telemetry_note": "This track contains SIMULATED telemetry generated for development and must not be presented as hardware data.",
    "track": [
      {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": -4.93,
        "heading_degrees": 85.0,
        "id": "d532605a-c170-5d90-bc90-03b977dfb6ea",
        "is_simulated": true,
        "is_within_zone": true,
        "latitude": 27.49385,
        "longitude": 77.67545,
        "ml": null,
        "movement_status": "NORMAL",
        "recorded_at": "2026-09-12T13:24:30+00:00",
        "source": "SIMULATED",
        "speed_kmph": 12.0,
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": -38.92,
        "heading_degrees": 85.0,
        "id": "fcb897f5-c101-5bbe-a857-adea41096463",
        "is_simulated": true,
        "is_within_zone": true,
        "latitude": 27.49365,
        "longitude": 77.67505,
        "ml": null,
        "movement_status": "NORMAL",
        "recorded_at": "2026-09-12T13:26:30+00:00",
   
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/gps/{custody_id}/fixes`

**The recorded fixes for a custody session**

Paginated, newest first. Each row carries its own `source` and `is_simulated`, so provenance is visible per fix rather than only in aggregate.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `custody_id` | `uuid` | The custody **session** UUID — not an inspection id or a ticket id. |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `status` | `NORMAL`, `WARNING`, `SUSPICIOUS`, `BREACH` | `—` | — |
| `page` | integer | `1` | — |
| `page_size` | integer | `100` | Max 200. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such custody session, or it is not yours. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
    "fixes": [
      {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": 469.12,
        "heading_degrees": 85.0,
        "id": "514d1f5e-bc05-566b-8d7f-17480aea5719",
        "is_simulated": true,
        "is_within_zone": false,
        "latitude": 27.49635,
        "longitude": 77.67945,
        "ml": null,
        "movement_status": "BREACH",
        "recorded_at": "2026-09-12T13:46:30+00:00",
        "source": "SIMULATED",
        "speed_kmph": 41.0,
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": 333.83,
        "heading_degrees": 85.0,
        "id": "082dd5a5-84e9-5138-bdf4-71c81cd33f35",
        "is_simulated": true,
        "is_within_zone": false,
        "latitude": 27.49575,
        "longitude": 77.67825,
        "ml": null,
        "movement_status": "BREACH",
        "recorded_at": "2026-09-12T13:42:30+00:00",
        "source": "SIMULATED",
        "speed_kmph": 34.0,
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": -83.4,
        "heading_degrees": 85.0,
        "id": "cce29f21-55fb-5a00-9d91-acb8e010e332",
        "is_simulated": true,
        "is_within_zone": true,
        "latitude": 27.49325,
        "longitude": 77.67425,
        "ml": null,
        "movement_status": "NORMAL",
        "recorded_at": "2026-09-12T13:38:30+00:00",
        "source": "SIMULATED",
        "speed_kmph": 0.0,
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": -83.4,
        "heading_degrees": 85.0,
        "id": "f4347225-675b-5a6e-a3a8-5aac487e10f5",
        "is_simulated": true,
        "is_within_zone": true,
        "latitude": 27.49325,
        "longitude": 77.67425,
        "ml": null,
        "movement_status": "NORMAL",
        "recorded_at": "2026-09-12T13:34:30+00:00",
        "source": "SIMULATED",
        "speed_kmph": 0.0,
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": -83.4,
        "heading_degrees": 85.0,
        "id": "cc474311-df9e-5b5c-a035-50bb85df2ba8",
        "is_simulated": true,
        "is_within_zone": true,
        "latitude": 27.49325,
        "longitude": 77.67425,
        "ml": null,
        "movement_status": "NORMAL",
        "recorded_at": "2026-09-12T13:30:30+00:00",
        "source": "SIMULATED",
        "speed_kmph": 0.0,
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": -61.16,
        "heading_degrees": 85.0,
        "id": "b415188f-62fb-5d16-a309-df1eb7fd4914",
        "is_simulated": true,
        "is_within_zone": true,
        "latitude": 27.49345,
        "longitude": 77.67465,
        "ml": null,
        "movement_status": "NORMAL",
        "recorded_at": "2026-09-12T13:28:30+00:00",
        "source": "SIMULATED",
        "speed_kmph": 5.0,
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      {
        "accuracy_metres": 4.5,
        "custody_s
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Geofencing

Zones, boundaries and the movement classifier.

### `GET /api/v1/geofences`

**List parking zones and their geofences**

Every zone with its boundary ring, capacity and current occupancy. `viewer_role` is echoed back so a client can see which projection of the data it was given.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `status` | `ACTIVE`, `INACTIVE` | `—` | — |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "total": 3,
    "viewer_role": "ADMIN",
    "zones": [
      {
        "available_slots": 39,
        "buffer_metres": 25.0,
        "capacity": 40,
        "centre": {
          "latitude": 27.49325,
          "longitude": 77.67425
        },
        "code": "ZONE-A",
        "current_occupancy": 1,
        "description": "[DEMO] Demonstration zone with a 4-sided geofence.",
        "effective_radius_metres": 148.9,
        "geofence_polygon": [
          [
            77.673,
            27.4925
          ],
          [
            77.6755,
            27.4925
          ],
          [
            77.6755,
            27.494
          ],
          [
            77.673,
            27.494
          ],
          [
            77.673,
            27.4925
          ]
        ],
        "id": "5880e6e0-752c-5742-885b-72ef61090d8a",
        "name": "Zone A - Covered Parking",
        "radius_metres": null,
        "status": "ACTIVE"
      },
      {
        "available_slots": 60,
        "buffer_metres": 25.0,
        "capacity": 60,
        "centre": {
          "latitude": 27.4935,
          "longitude": 77.6775
        },
        "code": "ZONE-B",
        "current_occupancy": 0,
        "description": "[DEMO] Demonstration zone with a 4-sided geofence.",
        "effective_radius_metres": 185.1,
        "geofence_polygon": [
          [
            77.676,
            27.4925
          ],
          [
            77.679,
            27.4925
          ],
          [
            77.679,
            27.4945
          ],
          [
            77.676,
            27.4945
          ],
          [
            77.676,
            27.4925
          ]
        ],
        "id": "1cc7f7a9-926b-5019-93a9-5be1486c16bb",
        "name": "Zone B - Open Yard",
        "radius_metres": null,
        "status": "ACTIVE"
      },
      {
        "available_slots": 25,
        "buffer_metres": 25.0,
        "capacity": 25,
        "centre": {
          "latitude": 27.49325,
          "longitude": 77.681
        },
        "code": "ZONE-C",
        "current_occupancy": 0,
        "description": "[DEMO] Demonstration zone with a 4-sided geofence.",
        "effective_radius_metres": 129.2,
        "geofence_polygon": [
          [
            77.68,
            27.4925
          ],
          [
            77.682,
            27.4925
          ],
          [
            77.682,
            27.494
          ],
          [
            77.68,
            27.494
          ],
          [
            77.68,
            27.4925
          ]
        ],
        "id": "a8f07f9e-2b35-565a-b929-1b17f3a1b7bc",
        "name": "Zone C - Long Stay",
        "radius_metres": null,
        "status": "MAINTENANCE"
      }
    ]
  },
  "meta": {
    "request_id": "5cd9a892bd074d41800f4dfb9dd04138",
    "server_time": 1789225630.7271128
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/geofences`

**Create a geofenced zone**

Accepts the brief's `centre + radius` description and generates a circular ring into `geofence_polygon`, so the API matches the specification while the stored shape stays a polygon — one point-in-polygon test then serves both this and an irregular real-world yard.

Pass an explicit `geofence_polygon` instead for a non-circular yard. `radius` is ignored when a polygon is supplied.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_role(GEOFENCE_ROLES)` in `services/geofence.py`. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `code` | `string` | **yes** | Upper-cased on store. Must be unique. (maxLength 32) |
| `name` | `string` | **yes** | (maxLength 80) |
| `latitude` | `number` | **yes** | (minimum -90) (maximum 90) |
| `longitude` | `number` | **yes** | (minimum -180) (maximum 180) |
| `radius` | `number` | no | Metres. Generates a circular ring. |
| `capacity` | `integer` | no | (minimum 1) |
| `buffer_metres` | `number` | no | — |
| `description` | `string` | no | — |
| `geofence_polygon` | `array` | no | Optional explicit `[longitude, latitude]` ring, for an irregular yard. |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/geofences \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"code": "ZONE-D", "name": "Zone D - Overflow", "latitude": 28.6139, "longitude": 77.209, "radius": 60, "capacity": 10}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | The caller is an operator or a customer. |
| `409` | A zone with that code already exists. |
| `422` | Missing or out-of-range field. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "available_slots": 10,
    "buffer_metres": 25.0,
    "capacity": 10,
    "centre": {
      "latitude": 28.6139,
      "longitude": 77.209
    },
    "code": "DOC8CD42",
    "current_occupancy": 0,
    "description": null,
    "effective_radius_metres": 60.0,
    "geofence_polygon": [
      [
        77.209,
        28.6144396
      ],
      [
        77.2090602,
        28.614437
      ],
      [
        77.2091199,
        28.6144292
      ],
      [
        77.2091784,
        28.6144164
      ],
      [
        77.2092352,
        28.6143985
      ],
      [
        77.2092898,
        28.6143759
      ],
      [
        77.2093415,
        28.6143487
      ],
      [
        77.2093899,
        28.6143171
      ],
      [
        77.2094346,
        28.6142815
      ],
      [
        77.2094751,
        28.6142423
      ],
      [
        77.2095111,
        28.6141998
      ],
      [
        77.2095421,
        28.6141544
      ],
      [
        77.2095679,
        28.6141065
      ],
      [
        77.2095882,
        28.6140566
      ],
      [
        77.2096029,
        28.6140053
      ],
      [
        77.2096117,
        28.6139529
      ],
      [
        77.2096147,
        28.6139
      ],
      [
        77.2096117,
        28.6138471
      ],
      [
        77.2096029,
        28.6137947
      ],
      [
        77.2095882,
        28.6137434
      ],
      [
        77.2095679,
        28.6136935
      ],
      [
        77.2095421,
        28.6136456
      ],
      [
        77.2095111,
        28.6136002
      ],
      [
        77.2094751,
        28.6135577
      ],
      [
        77.2094346,
        28.6135184
      ],
      [
        77.2093899,
        28.6134829
      ],
      [
        77.2093415,
        28.6134513
      ],
      [
        77.2092897,
        28.6134241
      ],
      [
        77.2092352,
        28.6134015
      ],
      [
        77.2091784,
        28.6133836
      ],
      [
        77.2091199,
        28.6133708
      ],
      [
        77.2090602,
        28.613363
      ],
      [
        77.209,
        28.6133604
      ],
      [
        77.2089398,
        28.613363
      ],
      [
        77.2088801,
        28.6133708
      ],
      [
        77.2088216,
        28.6133836
      ],
      [
        77.2087648,
        28.6134015
      ],
      [
        77.2087103,
        28.6134241
      ],
      [
        77.2086585,
        28.6134513
      ],
      [
        77.2086101,
        28.6134829
      ],
      [
        77.2085654,
        28.6135184
      ],
      [
        77.2085249,
        28.6135577
      ],
      [
        77.2084889,
        28.6136002
      ],
      [
        77.2084579,
        28.6136456
      ],
      [
        77.2084321,
        28.6136935
      ],
      [
        77.2084118,
        28.6137434
      ],
      [
        77.2083971,
        28.6137947
      ],
      [
        77.2083883,
        28.6138471
      ],
      [
        77.2083853,
        28.6139
      ],
      [
        77.2083883,
        28.6139529
      ],
      [
        77.2083971,
        28.6140053
      ],
      [
        77.2084118,
        28.6140566
      ],
      [
        77.2084321,
        28.6141065
      ],
      [
        77.2084579,
        28.6141544
      ],
      [
        77.2084889,
        28.6141998
      ],
      [
        77.2085249,
        28.6142423
      ],
      [
        77.2085654,
        28.6142815
      ],
      [
        77.2086101,
        28.6143171
      ],
      [
        77.2086585,
        28.6143487
      ],
      [
        77.2087102,
        28.6143759
      ],
      [
        77.2087648,
        28.6143985
      ],
      [
        77.2088216,
        28.6144164
      ],
      [
        77.2088801,
        28.6144292
      ],
      [
        77.2089398,
        28.614437
      ]
    ],
    "id": "2538322f-f6dc-47ea-95c1-9a9a2e301d86",
    "name": "Documentation probe zone",
    "radius_metres": 60.0,
    "st
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `PATCH /api/v1/geofences/{zone_id}`

**Modify a zone or its geofence**

Partial update. Every change is written to the audit log with its old and new values, because silently widening a geofence would hide a breach that the narrower boundary would have caught.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_role(GEOFENCE_ROLES)` in `services/geofence.py`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `zone_id` | `uuid` | The parking zone's UUID. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | `string` | no | (maxLength 80) |
| `description` | `string` | no | — |
| `capacity` | `integer` | no | (minimum 1) |
| `buffer_metres` | `number` | no | — |
| `radius` | `number` | no | — |
| `status` | `enum` | no | One of: `ACTIVE`, `INACTIVE` |
| `geofence_polygon` | `array` | no | — |

*Example request*

```bash
curl -X PATCH http://localhost:5000/api/v1/geofences/{zone_id} \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"name": "Zone D - Overflow (renamed)", "capacity": 12}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is an operator or a customer. |
| `404` | No such zone. |
| `422` | Malformed field. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "available_slots": 10,
    "buffer_metres": 25.0,
    "capacity": 10,
    "centre": {
      "latitude": 28.6139,
      "longitude": 77.209
    },
    "code": "DOC8CD42",
    "current_occupancy": 0,
    "description": null,
    "effective_radius_metres": 60.0,
    "geofence_polygon": [
      [
        77.209,
        28.6144396
      ],
      [
        77.2090602,
        28.614437
      ],
      [
        77.2091199,
        28.6144292
      ],
      [
        77.2091784,
        28.6144164
      ],
      [
        77.2092352,
        28.6143985
      ],
      [
        77.2092898,
        28.6143759
      ],
      [
        77.2093415,
        28.6143487
      ],
      [
        77.2093899,
        28.6143171
      ],
      [
        77.2094346,
        28.6142815
      ],
      [
        77.2094751,
        28.6142423
      ],
      [
        77.2095111,
        28.6141998
      ],
      [
        77.2095421,
        28.6141544
      ],
      [
        77.2095679,
        28.6141065
      ],
      [
        77.2095882,
        28.6140566
      ],
      [
        77.2096029,
        28.6140053
      ],
      [
        77.2096117,
        28.6139529
      ],
      [
        77.2096147,
        28.6139
      ],
      [
        77.2096117,
        28.6138471
      ],
      [
        77.2096029,
        28.6137947
      ],
      [
        77.2095882,
        28.6137434
      ],
      [
        77.2095679,
        28.6136935
      ],
      [
        77.2095421,
        28.6136456
      ],
      [
        77.2095111,
        28.6136002
      ],
      [
        77.2094751,
        28.6135577
      ],
      [
        77.2094346,
        28.6135184
      ],
      [
        77.2093899,
        28.6134829
      ],
      [
        77.2093415,
        28.6134513
      ],
      [
        77.2092897,
        28.6134241
      ],
      [
        77.2092352,
        28.6134015
      ],
      [
        77.2091784,
        28.6133836
      ],
      [
        77.2091199,
        28.6133708
      ],
      [
        77.2090602,
        28.613363
      ],
      [
        77.209,
        28.6133604
      ],
      [
        77.2089398,
        28.613363
      ],
      [
        77.2088801,
        28.6133708
      ],
      [
        77.2088216,
        28.6133836
      ],
      [
        77.2087648,
        28.6134015
      ],
      [
        77.2087103,
        28.6134241
      ],
      [
        77.2086585,
        28.6134513
      ],
      [
        77.2086101,
        28.6134829
      ],
      [
        77.2085654,
        28.6135184
      ],
      [
        77.2085249,
        28.6135577
      ],
      [
        77.2084889,
        28.6136002
      ],
      [
        77.2084579,
        28.6136456
      ],
      [
        77.2084321,
        28.6136935
      ],
      [
        77.2084118,
        28.6137434
      ],
      [
        77.2083971,
        28.6137947
      ],
      [
        77.2083883,
        28.6138471
      ],
      [
        77.2083853,
        28.6139
      ],
      [
        77.2083883,
        28.6139529
      ],
      [
        77.2083971,
        28.6140053
      ],
      [
        77.2084118,
        28.6140566
      ],
      [
        77.2084321,
        28.6141065
      ],
      [
        77.2084579,
        28.6141544
      ],
      [
        77.2084889,
        28.6141998
      ],
      [
        77.2085249,
        28.6142423
      ],
      [
        77.2085654,
        28.6142815
      ],
      [
        77.2086101,
        28.6143171
      ],
      [
        77.2086585,
        28.6143487
      ],
      [
        77.2087102,
        28.6143759
      ],
      [
        77.2087648,
        28.6143985
      ],
      [
        77.2088216,
        28.6144164
      ],
      [
        77.2088801,
        28.6144292
      ],
      [
        77.2089398,
        28.614437
      ]
    ],
    "id": "2538322f-f6dc-47ea-95c1-9a9a2e301d86",
    "name": "Documentation probe zone (renamed)",
    "radius_metres": 60.
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/parking-zones`

**Parking zones for the check-in picker**

A lighter projection than `/geofences`, intended for the check-in zone picker. Includes `available_slots` so a full zone can be shown as unavailable before the valet tries to assign it.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "items": [
      {
        "available_slots": 39,
        "buffer_metres": 25.0,
        "capacity": 40,
        "centre": {
          "latitude": 27.49325,
          "longitude": 77.67425
        },
        "code": "ZONE-A",
        "current_occupancy": 1,
        "description": "[DEMO] Demonstration zone with a 4-sided geofence.",
        "geofence_polygon": [
          [
            77.673,
            27.4925
          ],
          [
            77.6755,
            27.4925
          ],
          [
            77.6755,
            27.494
          ],
          [
            77.673,
            27.494
          ],
          [
            77.673,
            27.4925
          ]
        ],
        "id": "5880e6e0-752c-5742-885b-72ef61090d8a",
        "name": "Zone A - Covered Parking",
        "radius_metres": null,
        "status": "ACTIVE"
      },
      {
        "available_slots": 60,
        "buffer_metres": 25.0,
        "capacity": 60,
        "centre": {
          "latitude": 27.4935,
          "longitude": 77.6775
        },
        "code": "ZONE-B",
        "current_occupancy": 0,
        "description": "[DEMO] Demonstration zone with a 4-sided geofence.",
        "geofence_polygon": [
          [
            77.676,
            27.4925
          ],
          [
            77.679,
            27.4925
          ],
          [
            77.679,
            27.4945
          ],
          [
            77.676,
            27.4945
          ],
          [
            77.676,
            27.4925
          ]
        ],
        "id": "1cc7f7a9-926b-5019-93a9-5be1486c16bb",
        "name": "Zone B - Open Yard",
        "radius_metres": null,
        "status": "ACTIVE"
      },
      {
        "available_slots": 25,
        "buffer_metres": 25.0,
        "capacity": 25,
        "centre": {
          "latitude": 27.49325,
          "longitude": 77.681
        },
        "code": "ZONE-C",
        "current_occupancy": 0,
        "description": "[DEMO] Demonstration zone with a 4-sided geofence.",
        "geofence_polygon": [
          [
            77.68,
            27.4925
          ],
          [
            77.682,
            27.4925
          ],
          [
            77.682,
            27.494
          ],
          [
            77.68,
            27.494
          ],
          [
            77.68,
            27.4925
          ]
        ],
        "id": "a8f07f9e-2b35-565a-b929-1b17f3a1b7bc",
        "name": "Zone C - Long Stay",
        "radius_metres": null,
        "status": "MAINTENANCE"
      }
    ],
    "total": 3
  },
  "meta": {
    "request_id": "6c73e620029a40bca13fc836d9c6a8d6",
    "server_time": 1789225630.7214067
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/ml/model`

**Security classifier metadata**

Reports what the classifier actually is: the algorithm, its version, the features it was trained on, its held-out metrics, and a disclaimer.

`available: false` is not an error — it says plainly what is missing and which script to run. The model is a prototype trained on **synthetic** telemetry, and the response says so.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "algorithm": "logistic",
    "available": true,
    "disclaimer": "Prototype classifier trained and evaluated on synthetic telemetry whose labels were written by hand. It has never seen a real vehicle and must not be represented as a production model or relied on as the sole basis for a security decision. Rule-based geofence checks remain authoritative.",
    "enabled": true,
    "escalation_threshold": 0.7,
    "feature_columns": [
      {
        "label": "speed",
        "name": "speed_kmph",
        "unit": "km/h"
      },
      {
        "label": "distance from the zone boundary",
        "name": "distance_from_zone_metres",
        "unit": "m"
      },
      {
        "label": "time spent moving",
        "name": "movement_duration_seconds",
        "unit": "s"
      },
      {
        "label": "change of direction",
        "name": "direction_change_degrees",
        "unit": "\u00b0"
      },
      {
        "label": "distance travelled since the previous fix",
        "name": "distance_travelled_metres",
        "unit": "m"
      },
      {
        "label": "geofence breach",
        "name": "zone_breach",
        "unit": ""
      },
      {
        "label": "time of day",
        "name": "time_sin",
        "unit": ""
      },
      {
        "label": "time of day",
        "name": "time_cos",
        "unit": ""
      }
    ],
    "feature_importance": [
      [
        "movement_duration_seconds",
        4.1625
      ],
      [
        "zone_breach",
        3.1021
      ],
      [
        "distance_travelled_metres",
        2.3956
      ],
      [
        "speed_kmph",
        1.8939
      ],
      [
        "direction_change_degrees",
        1.6489
      ],
      [
        "distance_from_zone_metres",
        0.9816
      ],
      [
        "time_cos",
        0.8561
      ],
      [
        "time_sin",
        0.6064
      ]
    ],
    "metrics": {
      "accuracy": 0.9635,
      "baseline_accuracy_always_normal": 0.5778,
      "confusion_matrix": {
        "labels": [
          "NORMAL",
          "SUSPICIOUS"
        ],
        "matrix": [
          [
            1666,
            27
          ],
          [
            80,
            1157
          ]
        ],
        "reading": "rows are the true label, columns the predicted label"
      },
      "f1": 0.9558,
      "precision": 0.9772,
      "recall": 0.9353,
      "rows": 2930
    },
    "model_version": "security-prototype/1.0.0",
    "prototype_disclaimer": "Prototype classifier trained and evaluated on synthetic telemetry whose labels were written by hand. It has never seen a real vehicle and must not be represented as a production model or relied on as the sole basis for a security decision. Rule-based geofence checks remain authoritative.",
    "trained_at": "2026-09-09T16:34:08.475519+00:00",
    "trained_on_synthetic_data": true,
    "training_summary": {
      "algorithm": "logistic",
      "class_weight": "balanced",
      "classes": [
        "NORMAL",
        "SUSPICIOUS"
      ],
      "episodes": 1200,
      "features": [
        "speed_kmph",
        "distance_from_zone_metres",
        "movement_duration_seconds",
        "direction_change_degrees",
        "distance_travelled_metres",
        "zone_breach",
        "time_sin",
        "time_cos"
      ],
      "rows": 11269,
      "test_normal": 1693,
      "test_rows": 2930,
      "test_suspicious": 1237,
      "train_normal": 5343,
      "train_rows": 8339,
      "train_suspicious": 2996
    }
  },
  "meta": {
    "request_id": "864541c9e49b4e91b37a68afd6862c5a",
    "server_time": 1789225630.7816014
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/ml/classify`

**Score one movement sample**

An inspection tool, not a telemetry endpoint — **nothing is persisted**.

Every feature the model was trained on must be supplied. Omitting one is refused with `422` listing the missing features, because silently defaulting a feature to zero would produce a confident prediction from partly-guessed input.

The response explains itself: `reason`, `top_factors` with each feature's contribution and direction, and `severity_basis` stating that severity is graded from P(SUSPICIOUS) rather than from measured risk.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `speed_kmph` | `number` | **yes** | — |
| `distance_from_zone_metres` | `number` | **yes** | — |
| `movement_duration_seconds` | `number` | **yes** | — |
| `direction_change_degrees` | `number` | **yes** | — |
| `distance_travelled_metres` | `number` | **yes** | — |
| `zone_breach` | `enum` | **yes** | One of: `0`, `1` |
| `time_sin` | `number` | **yes** | — |
| `time_cos` | `number` | **yes** | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/ml/classify \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"speed_kmph": 14.0, "distance_from_zone_metres": 320.0, "movement_duration_seconds": 95.0, "direction_change_degrees": 140.0, "distance_travelled_metres": 380.0, "zone_breach": 1, "time_sin": 0.87, "time_cos": -0.49}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `422` | A trained feature is missing (`details.missing_features`), or no classifier is loaded. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "classification": "SUSPICIOUS",
    "confidence": 1.0,
    "disclaimer": "Prototype classifier trained and evaluated on synthetic telemetry whose labels were written by hand. It has never seen a real vehicle and must not be represented as a production model or relied on as the sole basis for a security decision. Rule-based geofence checks remain authoritative.",
    "inputs": {
      "direction_change_degrees": 140.0,
      "distance_from_zone_metres": 320.0,
      "distance_travelled_metres": 380.0,
      "movement_duration_seconds": 95.0,
      "speed_kmph": 14.0,
      "time_cos": -0.49,
      "time_sin": 0.87,
      "zone_breach": 1.0
    },
    "model_version": "security-prototype/1.0.0",
    "probability": 1.0,
    "reason": "Prototype classifier: 100% chance of suspicious behaviour (logistic). Leading factors: distance travelled since the previous fix = 380.0 m (+9.37), geofence breach = 1.0 (+6.63), change of direction = 140.0 \u00b0 (+4.63). Contribution basis: exact log-odds contributions.",
    "severity": "HIGH",
    "severity_bands": [
      {
        "min_probability": 0.9,
        "severity": "HIGH"
      },
      {
        "min_probability": 0.7,
        "severity": "MEDIUM"
      },
      {
        "min_probability": 0.0,
        "severity": "LOW"
      }
    ],
    "severity_basis": "graded from P(SUSPICIOUS), not from measured risk",
    "top_factors": [
      {
        "contribution": 9.3732,
        "direction": "towards SUSPICIOUS",
        "feature": "distance_travelled_metres",
        "label": "distance travelled since the previous fix",
        "unit": "m",
        "value": 380.0
      },
      {
        "contribution": 6.6265,
        "direction": "towards SUSPICIOUS",
        "feature": "zone_breach",
        "label": "geofence breach",
        "unit": "",
        "value": 1.0
      },
      {
        "contribution": 4.6299,
        "direction": "towards SUSPICIOUS",
        "feature": "direction_change_degrees",
        "label": "change of direction",
        "unit": "\u00b0",
        "value": 140.0
      },
      {
        "contribution": 1.8878,
        "direction": "towards SUSPICIOUS",
        "feature": "distance_from_zone_metres",
        "label": "distance from the zone boundary",
        "unit": "m",
        "value": 320.0
      },
      {
        "contribution": -1.8275,
        "direction": "towards NORMAL",
        "feature": "movement_duration_seconds",
        "label": "time spent moving",
        "unit": "s",
        "value": 95.0
      }
    ],
    "trained_on_synthetic_data": true
  },
  "meta": {
    "request_id": "67432fa3cf2341dba71be13d5eaa70fe",
    "server_time": 1789228907.572828
  },
  "success": true
}
```

*Example `422` response*

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "'plate_number' does not look like a valid Indian registration number.",
    "details": {
      "field": "plate_number"
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Security Alerts

Detector alerts and the centralized alert feed.

### `GET /api/v1/alerts`

**Geofence security alerts**

The alerts raised by the geofence detector, each linked to the GPS fix and the evidence that produced it.

This is the evidence-linked surface. The centralized cross-source feed lives at `/api/v1/feed`; both are kept because this one carries the detector's own `alert_type` and measurement evidence.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Non-staff callers are refused in `list_alerts`. |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `custody_session_id` | uuid | `—` | — |
| `status` | `OPEN`, `ACKNOWLEDGED`, `INVESTIGATING`, `RESOLVED`, `DISMISSED` | `—` | — |
| `severity` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` | `—` | — |
| `page` | integer | `1` | — |
| `page_size` | integer | `50` | Max 200. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is a customer. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "alerts": [
      {
        "acknowledged_at": "2026-09-12T13:48:30+00:00",
        "acknowledged_by_email": "manager.karan@example.com",
        "acknowledged_by_id": "24758677-b3ca-581e-bc94-9dea4082aba2",
        "alert_type": "GEOFENCE_BREACH",
        "confidence": 0.94,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "description": "[DEMO] Two consecutive fixes fell outside the assigned zone while the custody session had no retrieval in progress.",
        "evidence": {
          "buffer_metres": 25.0,
          "custody_status_at_fix": "PARKED",
          "distance_metres": 333.83,
          "movement_status": "BREACH",
          "speed_kmph": 34.0,
          "telemetry_source": "SIMULATED",
          "zone_code": "ZONE-A"
        },
        "gps_event_id": "082dd5a5-84e9-5138-bdf4-71c81cd33f35",
        "id": "6064d352-c982-5c17-b68d-7e9103481b82",
        "is_open": true,
        "occurred_at": "2026-09-12T13:42:30+00:00",
        "reason": "Fix at 27.49575, 77.67825 is 333.8 m beyond the Zone A - Covered Parking boundary, exceeding the 25 m buffer, while custody status was PARKED.",
        "resolution_notes": null,
        "resolved_at": null,
        "resolved_by_email": null,
        "resolved_by_id": null,
        "severity": "HIGH",
        "status": "ACKNOWLEDGED",
        "title": "Vehicle left ZONE-A with no task in progress",
        "vehicle_id": "b88418a6-7799-5f31-856f-fd424e58a176",
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      }
    ],
    "page": 1,
    "page_size": 50,
    "total": 1
  },
  "meta": {
    "page": 1,
    "page_size": 50,
    "request_id": "d76551c6f63d4ab8b770de07ae7276cb",
    "server_time": 1789225630.7439275
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/alerts/{alert_id}/acknowledge`

**Acknowledge a security alert**

Records that a person has seen the alert, without claiming it is resolved. Moves an `OPEN` alert to `ACKNOWLEDGED` and stamps who did it.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_role(ALERT_TRIAGE_ROLES)`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `alert_id` | `uuid` | The alert's UUID. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is an operator or a customer. |
| `404` | No such alert. |
| `409` | The alert is already acknowledged, resolved or dismissed. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "acknowledged_at": "2026-09-12T16:01:47.617621+00:00",
    "acknowledged_by_email": "manager.karan@example.com",
    "acknowledged_by_id": "24758677-b3ca-581e-bc94-9dea4082aba2",
    "alert_type": "TELEMETRY_LOSS",
    "confidence": 1.0,
    "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
    "description": "Detected at 2026-09-12T15:56:17.511231+00:00 for vehicle UP98AB9533.",
    "evidence": {
      "breach_margin_metres": 150.0,
      "buffer_metres": 25.0,
      "distance_from_boundary_metres": -22.33,
      "finding": "TELEMETRY_LOSS",
      "finding_status": "WARNING",
      "fix_id": "fbf4737b-8c95-4542-bdc7-cdefe2d5d534",
      "movement_expected": true,
      "prior_fix_count": 1,
      "rules_fired": [
        {
          "alert_type": "TELEMETRY_LOSS",
          "reason": "No position reported for 356 minutes; the threshold is 5 minutes.",
          "severity": "MEDIUM",
          "status": "WARNING"
        }
      ],
      "speed_kmph": 0.11,
      "speed_limit_applies": true,
      "zone_speed_limit_kmph": 20.0
    },
    "gps_event_id": "fbf4737b-8c95-4542-bdc7-cdefe2d5d534",
    "id": "fc0f04e2-1433-4757-9db5-a68c02005c24",
    "is_open": true,
    "occurred_at": "2026-09-12T15:56:17.511231+00:00",
    "reason": "No position reported for 356 minutes; the threshold is 5 minutes.",
    "resolution_notes": null,
    "resolved_at": null,
    "resolved_by_email": null,
    "resolved_by_id": null,
    "severity": "MEDIUM",
    "status": "ACKNOWLEDGED",
    "title": "Telemetry gap detected",
    "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a",
    "zone_id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf"
  },
  "meta": {
    "request_id": "9e0386101ed44560a511a0a590071731",
    "server_time": 1789228907.6215823
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/alerts/{alert_id}/resolve`

**Resolve or dismiss a security alert**

Closes the alert. `dismiss: true` records it as `DISMISSED` — judged to need no action — which is a different claim from `RESOLVED`, and the two are kept apart deliberately.

Resolution is attributed: the resolving user and any notes are stored and written to the audit log.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_role(ALERT_TRIAGE_ROLES)`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `alert_id` | `uuid` | The alert's UUID. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `notes` | `string` | no | (maxLength 500) |
| `dismiss` | `boolean` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/alerts/{alert_id}/resolve \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"notes": "Investigated; vehicle returned by the owner's driver."}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is an operator or a customer. |
| `404` | No such alert. |
| `409` | The alert is already closed. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "acknowledged_at": "2026-09-12T16:01:47.617621+00:00",
    "acknowledged_by_email": "manager.karan@example.com",
    "acknowledged_by_id": "24758677-b3ca-581e-bc94-9dea4082aba2",
    "alert_type": "TELEMETRY_LOSS",
    "confidence": 1.0,
    "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
    "description": "Detected at 2026-09-12T15:56:17.511231+00:00 for vehicle UP98AB9533.",
    "evidence": {
      "breach_margin_metres": 150.0,
      "buffer_metres": 25.0,
      "distance_from_boundary_metres": -22.33,
      "finding": "TELEMETRY_LOSS",
      "finding_status": "WARNING",
      "fix_id": "fbf4737b-8c95-4542-bdc7-cdefe2d5d534",
      "movement_expected": true,
      "prior_fix_count": 1,
      "rules_fired": [
        {
          "alert_type": "TELEMETRY_LOSS",
          "reason": "No position reported for 356 minutes; the threshold is 5 minutes.",
          "severity": "MEDIUM",
          "status": "WARNING"
        }
      ],
      "speed_kmph": 0.11,
      "speed_limit_applies": true,
      "zone_speed_limit_kmph": 20.0
    },
    "gps_event_id": "fbf4737b-8c95-4542-bdc7-cdefe2d5d534",
    "id": "fc0f04e2-1433-4757-9db5-a68c02005c24",
    "is_open": false,
    "occurred_at": "2026-09-12T15:56:17.511231+00:00",
    "reason": "No position reported for 356 minutes; the threshold is 5 minutes.",
    "resolution_notes": null,
    "resolved_at": "2026-09-12T16:01:47.625911+00:00",
    "resolved_by_email": "manager.karan@example.com",
    "resolved_by_id": "24758677-b3ca-581e-bc94-9dea4082aba2",
    "severity": "MEDIUM",
    "status": "RESOLVED",
    "title": "Telemetry gap detected",
    "vehicle_id": "3120854d-1367-42a9-80cd-6742eb61e33a",
    "zone_id": "d4333df6-decb-4e76-98b2-bebd01b6f7cf"
  },
  "meta": {
    "request_id": "fc023d01e56b43e5a3290fba14a433a0",
    "server_time": 1789228907.6296897
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/feed`

**The centralized alert feed**

Every alert this actor may see, newest and worst first, across all sources.

Role scoping is applied server-side to the query, so a customer cannot widen their view by omitting a parameter.

`delivery: "polled"` is part of the response: this is a polled feed, not a push channel, and the client labels it that way rather than implying real-time delivery.

`total` is in the body as well as in `meta`, because a client cannot render "page 2 of 9" from headers alone.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `status` | `OPEN`, `ACKNOWLEDGED`, `RESOLVED`, `DISMISSED` | `—` | — |
| `type` | `GEOFENCE_BREACH`, `SUSPICIOUS_MOVEMENT`, `OVERSPEED`, `POTENTIAL_DAMAGE`, `RETRIEVAL_READY` | `—` | — |
| `severity` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` | `—` | — |
| `vehicle_id` | uuid | `—` | — |
| `unread_only` | boolean | `—` | — |
| `page` | integer | `1` | — |
| `page_size` | integer | `25` | Max 200. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "alerts": [],
    "delivery": "polled",
    "total": 0,
    "unread": 0
  },
  "meta": {
    "page": 1,
    "page_size": 25,
    "request_id": "566d9548b96b4132936c34c355b119fa",
    "server_time": 1789225630.7358584,
    "total": 0
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/feed/summary`

**Unread and open counts for the notification bell**

A compact count payload for the bell badge. `can_resolve` tells the client whether to offer the resolve action at all, so the button is not rendered for a role that would only receive a `403`.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "can_resolve": true,
    "channels": [
      {
        "channel": "IN_APP",
        "enabled": true
      },
      {
        "channel": "EMAIL",
        "enabled": false
      },
      {
        "channel": "SMS",
        "enabled": false
      },
      {
        "channel": "PUSH",
        "enabled": false
      }
    ],
    "total": 0,
    "unread": 0
  },
  "meta": {
    "request_id": "9b88c4dc4c194b8e98861f525871727c",
    "server_time": 1789225630.7926326
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/feed`

**Raise an alert into the feed**

Staff-only. Lets a valet or manager raise something the detectors do not cover.

Creating the same alert twice is idempotent where the source allows it: the response reports whether a new row was created.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `is_staff(actor)` in the route. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `type` | `enum` | **yes** | One of: `GEOFENCE_BREACH`, `SUSPICIOUS_MOVEMENT`, `OVERSPEED`, `POTENTIAL_DAMAGE`, `RETRIEVAL_READY` |
| `message` | `string` | no | — |
| `severity` | `enum` | no | One of: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `custody_id` | `uuid` | no | Also accepted as `custody_session_id`. |
| `vehicle_id` | `uuid` | no | — |
| `audiences` | `array` | no | Role names. A bare `STAFF` is not accepted. |
| `source_entity_type` | `string` | no | — |
| `source_id` | `string` | no | — |
| `source_type` | `string` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/feed \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"type": "RETRIEVAL_READY", "severity": "LOW", "message": "Vehicle ready at the handover point.", "audiences": ["CUSTOMER", "VALET_MANAGER"]}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `403` | The caller is a customer. |
| `422` | Unknown `type`, unknown audience, or a malformed body. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "alert_id": "8c903c41-a1ba-4c83-bdfa-c6a7c1ea7617",
    "audiences": [
      "CUSTOMER",
      "VALET_MANAGER"
    ],
    "created_at": "2026-09-12T15:23:06.461154+00:00",
    "custody_id": null,
    "is_open": true,
    "is_read": false,
    "message": "Documentation probe alert (shapes pass).",
    "read_at": null,
    "resolution_notes": null,
    "resolved_at": null,
    "resolved_by": null,
    "severity": "LOW",
    "source": {
      "entity_type": null,
      "id": null,
      "type": null
    },
    "status": "OPEN",
    "type": "RETRIEVAL_READY",
    "vehicle": null,
    "vehicle_id": null
  },
  "meta": {
    "request_id": "14abf3c753954f60ab81f27db223964f",
    "server_time": 1789226586.4706626
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/feed/{alert_id}`

**Fetch one feed alert**

The alert with this actor's own read state attached.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `alert_id` | `uuid` | The alert's UUID. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such alert, or it is not addressed to you. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "alert_id": "8c903c41-a1ba-4c83-bdfa-c6a7c1ea7617",
    "audiences": [
      "CUSTOMER",
      "VALET_MANAGER"
    ],
    "created_at": "2026-09-12T15:23:06.461154+00:00",
    "custody_id": null,
    "is_open": true,
    "is_read": false,
    "message": "Documentation probe alert (shapes pass).",
    "read_at": null,
    "resolution_notes": null,
    "resolved_at": null,
    "resolved_by": null,
    "severity": "LOW",
    "source": {
      "entity_type": null,
      "id": null,
      "type": null
    },
    "status": "OPEN",
    "type": "RETRIEVAL_READY",
    "vehicle": null,
    "vehicle_id": null
  },
  "meta": {
    "request_id": "2bcc33c23d294e08a4c238971887cdd8",
    "server_time": 1789226586.4819002
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/feed/{alert_id}/read`

**Mark one alert read**

Read state is **per recipient**, stored in the append-only `alert_reads` table. One person reading an alert does not clear it for anyone else.

`already_read` in the response distinguishes a first read from a repeat, so a client can avoid counting the same alert twice.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `alert_id` | `uuid` | The alert's UUID. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No such alert, or it is not addressed to you. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "alert_id": "8c903c41-a1ba-4c83-bdfa-c6a7c1ea7617",
    "already_read": false,
    "is_read": true
  },
  "meta": {
    "request_id": "5a0fe94b39ac4d46962c44d369a6057c",
    "server_time": 1789226586.4951458
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/feed/read-all`

**Mark every visible alert read**

Marks all alerts addressed to this actor as read, in one write. Returns how many rows were affected.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "marked_read": 5
  },
  "meta": {
    "request_id": "1d81464a49d94cf4baa965a117fd6245",
    "server_time": 1789226586.513575
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/feed/{alert_id}/resolve`

**Resolve or dismiss a feed alert**

Closes the alert and attributes the closure. `dismiss: true` records `DISMISSED` rather than `RESOLVED` — "we judged this needed no action" is a different claim from "we dealt with it", and collapsing them would make the log useless for review.

Resolving again returns `409` naming the current status.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `TRIAGE_ROLES` in `services/alerts.py`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `alert_id` | `uuid` | The alert's UUID. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `notes` | `string` | no | (maxLength 500) |
| `dismiss` | `boolean` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/feed/{alert_id}/resolve \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"notes": "Closed after checking with the customer."}'
```

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `403` | The caller is an operator or a customer. |
| `404` | No such alert. |
| `409` | The alert is already resolved or dismissed. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "alert_id": "8c903c41-a1ba-4c83-bdfa-c6a7c1ea7617",
    "audiences": [
      "CUSTOMER",
      "VALET_MANAGER"
    ],
    "created_at": "2026-09-12T15:23:06.461154+00:00",
    "custody_id": null,
    "is_open": false,
    "is_read": true,
    "message": "Documentation probe alert (shapes pass).",
    "read_at": "2026-09-12T15:23:06.485763+00:00",
    "resolution_notes": "Closed during documentation pass.",
    "resolved_at": "2026-09-12T15:23:06.524960+00:00",
    "resolved_by": "manager.karan@example.com",
    "severity": "LOW",
    "source": {
      "entity_type": null,
      "id": null,
      "type": null
    },
    "status": "RESOLVED",
    "type": "RETRIEVAL_READY",
    "vehicle": null,
    "vehicle_id": null
  },
  "meta": {
    "request_id": "37998ca93c4e4ba3b0bde01294428907",
    "server_time": 1789226586.5305073
  },
  "success": true
}
```

*Example `403` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHORIZATION_ERROR",
    "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
    "details": {
      "role": "CUSTOMER",
      "required": [
        "VALET_MANAGER",
        "ADMIN"
      ]
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Retrieval Prediction

Retrieval-time estimates and the live queue.

### `POST /api/v1/retrieval/predict`

**Predict how long a retrieval will take**

Scores the queue length, staffing and zone occupancy for a custody session and returns an estimated wait with its reasoning.

The estimate is a **range as well as a point**. A single figure implies precision the model does not have.

Pass `persist: false` to score without writing a row.

The model is a baseline trained on **synthetic** historical records generated for this MVP. `trained_on_synthetic_data`, `data_basis` and `disclaimer` are always present so the figure is never presented as an operational forecast.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `custody_session_id` | `uuid` | no | Also accepted as `custody_id`. |
| `features` | `object` | no | Optional explicit feature overrides. Omitted features are derived from the session. |
| `persist` | `boolean` | no | — |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/retrieval/predict \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"custody_session_id": "<custody session uuid>"}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `404` | No such custody session. |
| `422` | Missing `custody_session_id`. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "confidence": 0.679,
    "contributions": {
      "active_valets": -0.13636422916568658,
      "dow_cos": -0.035013623720164144,
      "dow_sin": 0.5963032564167559,
      "historical_zone_mean_minutes": -0.1157916116712825,
      "hour_cos": 0.07149590919037511,
      "hour_sin": 0.9950187267063652,
      "queue_position": -0.8478208331784922,
      "queue_size": 0.042710593316796296,
      "vehicle_flagged": -0.42233442773821284,
      "zone_capacity": -1.9262798266062398,
      "zone_occupancy_percent": -0.6894154888626916
    },
    "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
    "data_basis": "synthetic historical retrieval records generated for this MVP by ai/retrieval/dataset.py; no real valet operation data was used",
    "disclaimer": "This estimate comes from a baseline model trained on synthetic historical data generated for this MVP. It has never seen a real valet operation and must not be treated as an operational forecast. Treat it as an indicative planning number.",
    "dispatch_delay_minutes": 0,
    "estimated_minutes": 6,
    "estimated_minutes_exact": 6.41,
    "estimated_retrieval_time": "Sat, 12 Sep 2026 16:07:46 GMT",
    "fetch_minutes": 6,
    "history_note": "This zone has no completed retrieval on record yet, so the historical component of the estimate is a model default rather than a measurement.",
    "inputs": {
      "active_valets": 7.0,
      "dow_cos": -0.2225209339563146,
      "dow_sin": -0.9749279121818236,
      "historical_zone_mean_minutes": 8.4,
      "hour_cos": -0.4932611971991734,
      "hour_sin": -0.8698812512852764,
      "queue_position": 1.0,
      "queue_size": 1.0,
      "vehicle_flagged": 0.0,
      "zone_capacity": 10.0,
      "zone_occupancy_percent": 20.0
    },
    "max_wait_minutes": 8,
    "method": "linear-ridge-v1",
    "min_wait_minutes": 4,
    "mode": "custody_session",
    "model_version": "retrieval-baseline/1.0.0",
    "persisted": true,
    "prediction_id": "8a7d9cab-3d5c-4791-8930-007db3ce8ecf",
    "queue_position": 1,
    "queue_size": 1,
    "reason": "Baseline linear model estimates 6 minutes. zone capacity 10.00 saves 1.9 min; time of day -0.87 adds 1.0 min; position in queue 1.00 saves 0.8 min. Basis: exact linear contributions in minutes. Trained on synthetic data - an indicative planning number, not an operational forecast.",
    "recommended_dispatch": "Now",
    "recommended_dispatch_time": "Sat, 12 Sep 2026 16:01:46 GMT",
    "requested_at": "Sat, 12 Sep 2026 16:01:46 GMT",
    "trained_on_synthetic_data": true
  },
  "meta": {
    "request_id": "e749e497e6234b678f55ad697e1378c3",
    "server_time": 1789228907.6066644
  },
  "success": true
}
```

*Example `404` response*

```json
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "No vehicle was found with that id.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `POST /api/v1/retrieval/request`

**Ask for the vehicle back**

Moves the custody session to `RETRIEVAL_REQUESTED`, produces an estimate and notifies the valet staff.

This is a **custody transition**, so it obeys the same transition table as `PATCH /custody-sessions/{id}`: from `IN_TRANSIT` the permitted targets no longer include `RETRIEVAL_REQUESTED`, and attempting it returns `409` with the allowed set.

`previous_status` is returned so a client can undo or explain the move. `estimate_available: false` is not an error — it means no estimate could be produced, and `estimate_error` says why.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | The customer who owns the vehicle, or staff. |

**Request body** (`application/json`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `custody_session_id` | `uuid` | no | Also accepted as `custody_id`. |

*Example request*

```bash
curl -X POST http://localhost:5000/api/v1/retrieval/request \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Content-Type: application/json' \
  -d '{"custody_session_id": "<custody session uuid>"}'
```

**Responses**

| Status | Meaning |
|---|---|
| `201` | Success. |
| `404` | No such custody session. |
| `409` | The transition is not permitted from the current status. `details.allowed` lists the valid targets. |

*Example `201` response* (captured from the running server)

```json
{
  "data": {
    "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
    "custody_status": "RETRIEVAL_REQUESTED",
    "data_basis": "synthetic historical retrieval records generated for this MVP by ai/retrieval/dataset.py; no real valet operation data was used",
    "disclaimer": "This estimate comes from a baseline model trained on synthetic historical data generated for this MVP. It has never seen a real valet operation and must not be treated as an operational forecast. Treat it as an indicative planning number.",
    "estimate": {
      "confidence": 0.679,
      "contributions": {
        "active_valets": -0.13636422916568658,
        "dow_cos": -0.035013623720164144,
        "dow_sin": 0.5963032564167559,
        "historical_zone_mean_minutes": -0.1157916116712825,
        "hour_cos": 0.07149590919037511,
        "hour_sin": 0.9950187267063652,
        "queue_position": -0.8478208331784922,
        "queue_size": 0.042710593316796296,
        "vehicle_flagged": -0.42233442773821284,
        "zone_capacity": -1.9262798266062398,
        "zone_occupancy_percent": -0.6894154888626916
      },
      "custody_session_id": "27d10e8e-68dd-4efe-a31c-a07f10f44249",
      "disclaimer": "This estimate comes from a baseline model trained on synthetic historical data generated for this MVP. It has never seen a real valet operation and must not be treated as an operational forecast. Treat it as an indicative planning number.",
      "dispatch_after": "Sat, 12 Sep 2026 16:01:46 GMT",
      "dispatch_delay_minutes": 0,
      "dispatch_label": "Now",
      "estimated_minutes": 6,
      "estimated_minutes_raw": 6.41,
      "estimated_ready_at": "Sat, 12 Sep 2026 16:07:46 GMT",
      "fetch_minutes": 6,
      "historical_sample_size": 0,
      "history_is_fallback": true,
      "inputs": {
        "active_valets": 7.0,
        "dow_cos": -0.2225209339563146,
        "dow_sin": -0.9749279121818236,
        "historical_zone_mean_minutes": 8.4,
        "hour_cos": -0.4932611971991734,
        "hour_sin": -0.8698812512852764,
        "queue_position": 1.0,
        "queue_size": 1.0,
        "vehicle_flagged": 0.0,
        "zone_capacity": 10.0,
        "zone_occupancy_percent": 20.0
      },
      "max_wait_minutes": 8,
      "method": "linear-ridge-v1",
      "min_wait_minutes": 4,
      "model_version": "retrieval-baseline/1.0.0",
      "persisted": true,
      "prediction_id": "d60c5132-e70e-42da-a8cc-e01e0772ae1f",
      "queue_position": 1,
      "queue_size": 1,
      "reason": "Baseline linear model estimates 6 minutes. zone capacity 10.00 saves 1.9 min; time of day -0.87 adds 1.0 min; position in queue 1.00 saves 0.8 min. Basis: exact linear contributions in minutes. Trained on synthetic data - an indicative planning number, not an operational forecast.",
      "requested_at": "Sat, 12 Sep 2026 16:01:46 GMT",
      "trained_on_synthetic_data": true,
      "zone_code": "DOC8F74F"
    },
    "estimate_available": true,
    "estimate_error": null,
    "estimated_minutes": 6,
    "estimated_retrieval_time": "Sat, 12 Sep 2026 16:07:46 GMT",
    "message": "Vehicle requested. A valet has been notified and your estimated retrieval time is below.",
    "previous_status": "PARKED",
    "queue_position": 1,
    "recommended_dispatch": "Now",
    "requested_at": "2026-09-12T16:01:46.762336+00:00",
    "trained_on_synthetic_data": true,
    "valet_notification": {
      "delivery": "polled",
      "note": "The valet dashboard reads this queue on a short poll. There is no websocket channel, so the request appears there within one poll interval rather than instantly.",
      "queued": true
    }
  },
  "meta": {
    "request_id": "cb1600a3159b4c7082ede9ed473c16fb",
    "server_time": 1789228906.799006
  },
  "success": true
}
```

*Example `404` response*

```json
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "No vehicle was found with that id.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/retrieval/queue`

**The live retrieval queue**

Every session waiting for or undergoing retrieval, in dispatch order.

`delivery: "polled"` and `delivery_note` are part of the response, so the UI can label itself as a poll rather than implying a live push.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by the `require_role(...)` decorator on the route. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is a customer. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "count": 0,
    "delivery": "polled",
    "delivery_note": "This queue is read on a short poll rather than pushed. There is no websocket channel, so a new request appears here within one poll interval, not instantly.",
    "items": [],
    "waiting": 0
  },
  "meta": {
    "request_id": "fecafabd509a4a9289a21583878977ad",
    "server_time": 1789225630.7703772
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/retrieval/{custody_id}`

**The stored estimate for a custody session**

Returns the most recent persisted prediction for the session.

A `404` here means **nobody has asked for an estimate yet**, not that something failed. Clients should render "no estimate yet" rather than a zero wait — an empty card reads as an immediate pickup.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `custody_id` | `uuid` | The custody **session** UUID — not an inspection id or a ticket id. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `404` | No prediction has been recorded for this session yet. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "actual_ready_at": "2026-09-12T17:59:30+00:00",
    "confidence": 0.71,
    "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
    "data_basis": "synthetic historical retrieval records generated for this MVP by ai/retrieval/dataset.py; no real valet operation data was used",
    "disclaimer": "This estimate comes from a baseline model trained on synthetic historical data generated for this MVP. It has never seen a real valet operation and must not be treated as an operational forecast. Treat it as an indicative planning number.",
    "dispatch_after": "2026-09-12T17:40:30+00:00",
    "dispatch_delay_minutes": 0,
    "dispatch_label": "Now",
    "explanation": "Estimated from the mean retrieval time for Zone A (11.4 min over synthetic history), adjusted for a queue of 3 and the current hour. The 10-22 minute range reflects the observed spread. This estimate is derived from demonstration data, not from real operational history.",
    "fetch_minutes": null,
    "id": "56591a9f-e1ff-56cd-ae01-d507d3fd3a71",
    "inputs_snapshot": {
      "avg_retrieval_minutes_zone": 11.4,
      "history_is_synthetic": true,
      "hour_of_day": 14,
      "queue_length": 3,
      "zone": "ZONE-A"
    },
    "max_wait_minutes": 22,
    "method": "heuristic-queue-v1",
    "min_wait_minutes": 10,
    "model_version": "retrieval-baseline/1.0.0",
    "predicted_ready_at": "2026-09-12T17:48:30+00:00",
    "predicted_wait_minutes": 14,
    "queue_position": 2,
    "requested_at": "2026-09-12T17:34:30+00:00",
    "status": "DISPATCHED",
    "trained_on_synthetic_data": true
  },
  "meta": {
    "request_id": "60702f4a77c549099abc1cc50774fb69",
    "server_time": 1789225630.9994354
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/retrieval/model`

**Retrieval model metadata**

The algorithm, its coefficients, the features it uses and its held-out metrics against a naive baseline.

The disclaimer states plainly that the model is trained on synthetic historical records generated for this MVP and is indicative only.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "algorithm": "ridge",
    "alpha": 1.0,
    "available": true,
    "coefficients": {
      "active_valets": -0.098,
      "dow_cos": 0.1367,
      "dow_sin": -0.442,
      "historical_zone_mean_minutes": 0.2038,
      "hour_cos": -0.1088,
      "hour_sin": -0.8105,
      "queue_position": 1.5723,
      "queue_size": -0.0576,
      "vehicle_flagged": 1.41,
      "zone_capacity": 1.4189,
      "zone_occupancy_percent": 0.6943
    },
    "disclaimer": "This estimate comes from a baseline model trained on synthetic historical data generated for this MVP. It has never seen a real valet operation and must not be treated as an operational forecast. Treat it as an indicative planning number.",
    "enabled": true,
    "feature_columns": [
      {
        "label": "retrieval queue length",
        "name": "queue_size",
        "unit": ""
      },
      {
        "label": "position in queue",
        "name": "queue_position",
        "unit": ""
      },
      {
        "label": "valets on duty",
        "name": "active_valets",
        "unit": ""
      },
      {
        "label": "zone capacity",
        "name": "zone_capacity",
        "unit": ""
      },
      {
        "label": "zone occupancy",
        "name": "zone_occupancy_percent",
        "unit": "%"
      },
      {
        "label": "time of day",
        "name": "hour_sin",
        "unit": ""
      },
      {
        "label": "time of day",
        "name": "hour_cos",
        "unit": ""
      },
      {
        "label": "day of week",
        "name": "dow_sin",
        "unit": ""
      },
      {
        "label": "day of week",
        "name": "dow_cos",
        "unit": ""
      },
      {
        "label": "historical average for this zone",
        "name": "historical_zone_mean_minutes",
        "unit": "min"
      },
      {
        "label": "vehicle flagged for review",
        "name": "vehicle_flagged",
        "unit": ""
      }
    ],
    "intercept": 8.8771,
    "method": "linear-ridge-v1",
    "metrics": {
      "baseline_mae_minutes": 2.691,
      "improvement_over_baseline": 0.359,
      "mae_minutes": 1.725,
      "mean_actual_minutes": 8.82,
      "mean_baseline_mae_minutes": 2.753,
      "mean_predicted_minutes": 8.8,
      "median_absolute_error_minutes": 1.286,
      "primary_metric": "mae_minutes",
      "r2": 0.5704,
      "rmse_minutes": 2.371,
      "rows": 1000,
      "within_10_minutes": 0.997,
      "within_5_minutes": 0.956,
      "worst_error_minutes": 12.85
    },
    "model_version": "retrieval-baseline/1.0.0",
    "primary_metric": "mae_minutes",
    "prototype_disclaimer": "This estimate comes from a baseline model trained on synthetic historical data generated for this MVP. It has never seen a real valet operation and must not be treated as an operational forecast. Treat it as an indicative planning number.",
    "train_median_minutes": 8.24,
    "trained_at": "2026-09-09T16:34:09+00:00",
    "trained_on_synthetic_data": true,
    "training_summary": {
      "generated_at": "2026-09-09T16:34:08",
      "max_minutes": 38.050078174838156,
      "min_minutes": 2.204509694538634,
      "rows": 4000,
      "source": "synthetic - see the module docstring",
      "test_mean_minutes": 8.824087575403851,
      "test_rows": 1000,
      "train_mean_minutes": 8.877118769164921,
      "train_rows": 3000
    }
  },
  "meta": {
    "request_id": "f3832d2819c347ddb01c99a5c23bb40d",
    "server_time": 1789225630.785333
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Reports

Composed dashboards for customers, staff and managers.

### `GET /api/v1/customers/me/dashboard`

**The signed-in customer's dashboard**

A single server-composed payload: the customer's vehicle, its custody session, live location, retrieval estimate, security posture and the actions available to them right now.

The path uses `/me` deliberately — there is no `customer_id` parameter to tamper with, so a customer cannot ask for somebody else's dashboard.

The dashboard **writes nothing**. It reads state that the workflow endpoints already own.

| | |
|---|---|
| **Authentication** | Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "actions": {
      "already_requested": true,
      "can_request": true,
      "custody_status": "PARKED",
      "state": "ok"
    },
    "condition": {
      "comparison_ready": true,
      "confirmed_count": 0,
      "detector_disclaimer": "Findings are produced by classical OpenCV image differencing, not a trained damage classifier. Unverified findings are potential damage and require human confirmation.",
      "pickup_completed": true,
      "requires_review": true,
      "state": "ok",
      "status": "REVIEW",
      "summary": "3 unverified finding(s) awaiting confirmation.",
      "total_detections": 3,
      "unverified_count": 3
    },
    "custody": {
      "checked_in_at": "2026-09-12T13:24:30+00:00",
      "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
      "expected_retrieval_minutes": 15,
      "handover_point": "Main Lobby - Bay 1",
      "projected_status": "IN_CUSTODY",
      "ready_for_pickup_at": "2026-09-12T17:59:30+00:00",
      "retrieval_requested_at": "2026-09-12T17:34:30+00:00",
      "state": "ok",
      "status": "PARKED",
      "stored_status": "ACTIVE",
      "ticket_code": "VT-O-OPEN",
      "ticket_id": "233e028c-6271-5a88-a32b-e63021d7c5fd"
    },
    "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
    "has_active_custody": true,
    "location": {
      "fix": {
        "accuracy_metres": 4.5,
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "device_id": "SIM-TELEMETRY-001",
        "distance_from_zone_metres": 469.12,
        "heading_degrees": 85.0,
        "id": "514d1f5e-bc05-566b-8d7f-17480aea5719",
        "is_simulated": true,
        "is_within_zone": false,
        "latitude": 27.49635,
        "longitude": 77.67945,
        "ml": null,
        "movement_status": "BREACH",
        "recorded_at": "2026-09-12T13:46:30+00:00",
        "source": "SIMULATED",
        "speed_kmph": 41.0,
        "zone_id": "5880e6e0-752c-5742-885b-72ef61090d8a"
      },
      "fix_count": 8,
      "has_simulated_telemetry": true,
      "latitude": 27.49635,
      "longitude": 77.67945,
      "movement_status": "BREACH",
      "open_alert_count": 1,
      "recorded_at": "2026-09-12T13:46:30+00:00",
      "state": "ok",
      "telemetry_note": "This track contains SIMULATED telemetry generated for development and must not be presented as hardware data.",
      "telemetry_sources": [
        "SIMULATED"
      ],
      "zone": {
        "available_slots": 39,
        "buffer_metres": 25.0,
        "capacity": 40,
        "centre": {
          "latitude": 27.49325,
          "longitude": 77.67425
        },
        "code": "ZONE-A",
        "current_occupancy": 1,
        "description": "[DEMO] Demonstration zone with a 4-sided geofence.",
        "effective_radius_metres": 148.9,
        "geofence_polygon": [
          [
            77.673,
            27.4925
          ],
          [
            77.6755,
            27.4925
          ],
          [
            77.6755,
            27.494
          ],
          [
            77.673,
            27.494
          ],
          [
            77.673,
            27.4925
          ]
        ],
        "id": "5880e6e0-752c-5742-885b-72ef61090d8a",
        "name": "Zone A - Covered Parking",
        "radius_metres": null,
        "status": "ACTIVE"
      }
    },
    "retrieval": {
      "confidence": 0.71,
      "disclaimer": "This estimate comes from a baseline model trained on synthetic historical data generated for this MVP. It has never seen a real valet operation and must not be treated as an operational forecast. Treat it as an indicative planning number.",
      "dispatch_after": "2026-09-12T17:40:30+00:00",
      "dispatch_delay_minutes": 0,
      "estimated_minutes": 14,
      "max_wait_minutes": 22,
      "min_wait_minutes": 10,
      "predicted_ready_at": "2026-09-12T17:48:30+00:00",
      "prediction": {
        "actual_ready_at": "2026-09-12T17:59:30+00:00",
        "confidence": 
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/staff/dashboard`

**The valet operations dashboard**

Active vehicles, incoming handovers, the retrieval queue, pending pickups and inspection state, composed server-side.

Includes `capabilities` and `display_status_labels` so the client renders the vocabulary the backend actually uses rather than keeping a second copy of the mapping that can drift.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_staff` in `services/staff_dashboard.py`. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is a customer. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "active_vehicles": {
      "alarming": 1,
      "count": 1,
      "items": [
        {
          "alarming": true,
          "assigned_valet_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
          "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
          "custody_status": "PARKED",
          "customer": {
            "email": "customer.aisha@example.com",
            "id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
            "name": "Aisha Verma",
            "phone": "+91 98100 100 10"
          },
          "display_label": "Alert",
          "display_status": "ALERT",
          "dropped_off_at": "2026-09-12T13:24:30+00:00",
          "gps": {
            "is_within_zone": false,
            "latitude": 27.49635,
            "longitude": 77.67945,
            "movement_status": "BREACH",
            "recorded_at": "2026-09-12T13:46:30+00:00",
            "source": "SIMULATED"
          },
          "gps_has_data": true,
          "handover_point": "Main Lobby - Bay 1",
          "open_alert_count": 1,
          "ready_for_pickup_at": "2026-09-12T17:59:30+00:00",
          "retrieval_requested_at": "2026-09-12T17:34:30+00:00",
          "ticket_code": "VT-O-OPEN",
          "ticket_id": "233e028c-6271-5a88-a32b-e63021d7c5fd",
          "vehicle": {
            "color": "Pearl White",
            "id": "b88418a6-7799-5f31-856f-fd424e58a176",
            "make": "Tata",
            "model": "Nexon",
            "plate_number": "UP 78 AB 1234",
            "status": "IN_CUSTODY"
          },
          "zone": {
            "code": "ZONE-A",
            "id": "5880e6e0-752c-5742-885b-72ef61090d8a",
            "name": "Zone A - Covered Parking"
          }
        }
      ],
      "moving": 0,
      "state": "ok"
    },
    "capabilities": {
      "can_capture_inspection": true,
      "can_check_in": true,
      "can_complete_pickup": true,
      "can_move_vehicle": true,
      "can_triage_alerts": true,
      "can_verify_damage": true
    },
    "counts": {
      "active": 1,
      "incoming": null,
      "open_alerts": null,
      "pending_pickups": 0
    },
    "delivery": "polled",
    "display_status_labels": {
      "ALERT": "Alert",
      "COMPLETED": "Completed",
      "IN_CUSTODY": "In custody",
      "MOVING": "Moving",
      "READY": "Ready",
      "UNKNOWN": "Unknown"
    },
    "generated_for": {
      "role": "ADMIN",
      "user_id": "14254104-597c-59d0-9fd5-7320e446a3e9"
    },
    "incoming": {
      "count": 0,
      "items": [],
      "reason": "No vehicles are waiting to be checked in.",
      "state": "empty"
    },
    "inspections": {
      "awaiting_pickup_photos": 0,
      "awaiting_review": 0,
      "count": 1,
      "items": [
        {
          "comparison_ready": true,
          "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
          "drop_off_captured": true,
          "drop_off_outcome": "REVIEWED",
          "drop_off_photo_count": 5,
          "needs_pickup_photos": false,
          "needs_review": false,
          "pickup_captured": true,
          "pickup_outcome": "CLEAN",
          "pickup_photo_count": 5,
          "unverified_detections": 0,
          "vehicle": {
            "color": "Pearl White",
            "id": "b88418a6-7799-5f31-856f-fd424e58a176",
            "make": "Tata",
            "model": "Nexon",
            "plate_number": "UP 78 AB 1234",
            "status": "IN_CUSTODY"
          }
        }
      ],
      "state": "ok"
    },
    "pending_pickups": {
      "count": 0,
      "items": [],
      "reason": "No vehicles are staged for handover.",
      "state": "empty"
    },
    "retrieval_queue": {
      "count": 0,
      "delivery": "polled",
      "delivery_note": "This queue is read on a short poll rather than pushed. There is no websocket channel, so a new request appears here within one poll interval, not instantly.",
      "items": [],
      "reason": "No retrieval requests are waiting.",
      "state": "empty",
      "
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/staff/custody/{custody_id}/condition`

**Condition report for one custody session**

The same shape as `GET /api/v1/inspections/{custody_id}`, scoped to the staff surface so an operator can reach it from the custody view without a second client code path.

| | |
|---|---|
| **Authentication** | `VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_staff` in `services/staff_dashboard.py`. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `custody_id` | `uuid` | The custody **session** UUID — not an inspection id or a ticket id. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is a customer. |
| `404` | No such custody session. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "comparison_ready": true,
    "confirmed_count": 0,
    "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
    "detector_disclaimer": "Findings are produced by classical OpenCV image differencing, not a trained damage classifier. Unverified findings are potential damage and require human confirmation.",
    "drop_off": {
      "captured_at": "2026-09-12T13:24:30+00:00",
      "comparisons": [],
      "confirmed_count": 0,
      "created": false,
      "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
      "damage_count": 0,
      "detections": [
        {
          "algorithm_params": {
            "canny_high": 150,
            "canny_low": 60,
            "lab_delta_threshold": 18.0,
            "min_contour_area_px": 420
          },
          "angle": null,
          "bounding_box": {
            "height": 0.09,
            "width": 0.14,
            "x": 0.31,
            "y": 0.58
          },
          "comparison_image_path": "inspections/demo/4fc8aafe-bf35-5649-a906-8affcdb6d3ce-comparison-0.jpg",
          "comparison_url": "/media/inspections/demo/4fc8aafe-bf35-5649-a906-8affcdb6d3ce-comparison-0.jpg",
          "confidence": 0.82,
          "damage_type": "SCRATCH",
          "detection_reasoning": "Hairline scratch, ~9 cm, visible under raking light.",
          "evidence_image_path": "inspections/demo/4fc8aafe-bf35-5649-a906-8affcdb6d3ce-evidence-0.jpg",
          "evidence_url": "/media/inspections/demo/4fc8aafe-bf35-5649-a906-8affcdb6d3ce-evidence-0.jpg",
          "id": "47682e83-c892-54c4-b2b1-61d5abc5cf7c",
          "inspection_id": "4fc8aafe-bf35-5649-a906-8affcdb6d3ce",
          "is_pre_existing": true,
          "is_verified": false,
          "method": "CLASSICAL_CV",
          "model_version": null,
          "presentation_label": "Potential Damage",
          "region": "LEFT_FRONT_DOOR",
          "requires_customer_attention": false,
          "severity": "COSMETIC",
          "type_confidence": null,
          "verification_notes": null,
          "verification_status": "UNVERIFIED",
          "verified_at": null,
          "verified_by_id": null
        }
      ],
      "id": "4fc8aafe-bf35-5649-a906-8affcdb6d3ce",
      "inspector_notes": "[DEMO] Condition recorded by the seeded demonstration dataset.",
      "is_new_damage": false,
      "location": "Inspection Bay 2",
      "odometer_reading": 24310,
      "outcome": "REVIEWED",
      "photo_count": 5,
      "photos": [
        {
          "angle": "5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-front_left",
          "byte_size": 248371,
          "captured_at": "2026-09-07T09:12:44+00:00",
          "height": 4032,
          "sha256": "1f2d724888480a934ef3a823243c150d7b883ce0a91a1b731eeb2e4b797aeec5",
          "url": "/media/inspections/demo/5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-front_left.jpg",
          "width": 3024
        },
        {
          "angle": "5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-front_right",
          "byte_size": 248371,
          "captured_at": "2026-09-07T09:12:44+00:00",
          "height": 4032,
          "sha256": "28f2c19bf7d157f494c7f80985361de63e9399b082fb798947974a56d2cce4bd",
          "url": "/media/inspections/demo/5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-front_right.jpg",
          "width": 3024
        },
        {
          "angle": "5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-rear_left",
          "byte_size": 248371,
          "captured_at": "2026-09-07T09:12:44+00:00",
          "height": 4032,
          "sha256": "b298697cdd1d23cc91b71f9de082f97ea41d682af9e49bab6a2f9562e48bac1d",
          "url": "/media/inspections/demo/5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-rear_left.jpg",
          "width": 3024
        },
        {
          "angle": "5514bf61-f332-5956-8e9f-aeb5062621b5-DROP_OFF-rear_right",
          "byte_size": 248371,
          "captured_at": "2026-09-07T09:12:44+00:00",
          "height": 4032,
          "sha256": "b65abae
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/manager/dashboard`

**The manager dashboard**

Metrics, charts and tables for a date range, composed server-side.

`filter_options` is part of the response, so the filter dropdowns are built from what the backend can actually serve rather than from a hard-coded list in the client. `provenance` records what the numbers were computed from.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `MANAGER_VIEW_ROLES` in `services/manager_dashboard.py`. |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `date_from` | date | `—` | — |
| `date_to` | date | `—` | — |
| `status` | string | `—` | — |
| `severity` | string | `—` | — |
| `vehicle_id` | uuid | `—` | — |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is an operator or a customer. |
| `422` | A malformed date. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "charts": {
      "custody_duration": {
        "kind": "bar",
        "min_points": 10,
        "n": 1,
        "note": "Distribution of hours between drop-off and pickup, in two-hour buckets. Shown as a distribution rather than an average because the long tail is what occupies bays.",
        "series": [
          {
            "bucket": "4-6h",
            "value": 1
          }
        ],
        "sufficient": false,
        "title": "Custody duration",
        "unit": "sessions"
      },
      "damage_detections": {
        "by_region": [],
        "kind": "bar",
        "min_points": 10,
        "n": 6,
        "note": "Findings per week by verification outcome. CONFIRMED means a manager stood behind the detector; REJECTED means it was a false positive; UNVERIFIED means nobody has looked yet.",
        "series": [
          {
            "CONFIRMED": 0,
            "REJECTED": 0,
            "UNVERIFIED": 6,
            "total": 6,
            "week": "2026-09-07"
          }
        ],
        "sufficient": false,
        "title": "Damage detections",
        "unit": "findings"
      },
      "retrieval_time": {
        "kind": "line",
        "min_points": 5,
        "n": 0,
        "note": "Mean measured minutes from retrieval request to ready-for-pickup. Days with fewer than three retrievals are omitted rather than plotted, because a one-car average is not a trend.",
        "records": 0,
        "series": [],
        "sufficient": false,
        "title": "Average retrieval time",
        "unit": "minutes"
      },
      "security_alerts": {
        "kind": "bar",
        "min_points": 10,
        "n": 1,
        "note": "Alerts per ISO week, split by severity, with how many are still open. Weekly because alerts are sparse; a daily series would be mostly zeros.",
        "series": [
          {
            "by_severity": {
              "HIGH": 1
            },
            "open": 1,
            "total": 1,
            "week": "2026-09-07"
          }
        ],
        "sufficient": false,
        "title": "Security alerts",
        "unit": "alerts"
      },
      "vehicles_per_day": {
        "kind": "bar",
        "min_points": 2,
        "n": 30,
        "note": "Tickets issued per calendar day. Days with no arrivals are zero, not gaps.",
        "series": [
          {
            "date": "2026-08-14",
            "value": 0
          },
          {
            "date": "2026-08-15",
            "value": 0
          },
          {
            "date": "2026-08-16",
            "value": 0
          },
          {
            "date": "2026-08-17",
            "value": 0
          },
          {
            "date": "2026-08-18",
            "value": 0
          },
          {
            "date": "2026-08-19",
            "value": 0
          },
          {
            "date": "2026-08-20",
            "value": 0
          },
          {
            "date": "2026-08-21",
            "value": 0
          },
          {
            "date": "2026-08-22",
            "value": 0
          },
          {
            "date": "2026-08-23",
            "value": 0
          },
          {
            "date": "2026-08-24",
            "value": 0
          },
          {
            "date": "2026-08-25",
            "value": 0
          },
          {
            "date": "2026-08-26",
            "value": 0
          },
          {
            "date": "2026-08-27",
            "value": 0
          },
          {
            "date": "2026-08-28",
            "value": 0
          },
          {
            "date": "2026-08-29",
            "value": 0
          },
          {
            "date": "2026-08-30",
            "value": 0
          },
          {
            "date": "2026-08-31",
            "value": 0
          },
          {
            "date": "2026-09-01",
            "value": 0
          },
          {
            "date": "2026-09-02",
            "value": 0
          },
          {
            "date
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/manager/report`

**Operational report for a date range**

Metrics, time series and tables for export or review. `generated_by` and `generated_at` are recorded so a printed report can be attributed.

Read-only: generating a report writes nothing.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `MANAGER_VIEW_ROLES` in `services/manager_dashboard.py`. |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `date_from` | date | `—` | — |
| `date_to` | date | `—` | — |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is an operator or a customer. |
| `422` | A malformed date. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "filters": {
      "date_from": "2026-08-14",
      "date_to": "2026-09-12",
      "severity": null,
      "status": null,
      "vehicle_id": null
    },
    "generated_at": "2026-09-12T15:07:10.920118+00:00",
    "generated_by": {
      "role": "ADMIN",
      "user_id": "14254104-597c-59d0-9fd5-7320e446a3e9"
    },
    "metrics": {
      "active_sessions": {
        "label": "Active custody sessions",
        "unit": "sessions",
        "value": 1,
        "windowed": false
      },
      "avg_custody_hours": {
        "label": "Average custody duration",
        "n": 1,
        "unit": "hours",
        "value": 4.7,
        "windowed": true
      },
      "avg_retrieval_minutes": {
        "label": "Average retrieval time",
        "n": 1,
        "note": "Measured request-to-ready on completed retrievals, not predicted.",
        "unit": "minutes",
        "value": 25.0,
        "windowed": true
      },
      "completed_sessions": {
        "label": "Completed sessions",
        "note": "Closed within the selected range.",
        "unit": "sessions",
        "value": 1,
        "windowed": true
      },
      "open_alerts": {
        "label": "Open security alerts",
        "note": "Open, acknowledged or investigating \u2014 not yet resolved.",
        "unit": "alerts",
        "value": 1,
        "windowed": false
      },
      "pending_pickups": {
        "label": "Pending pickups",
        "unit": "vehicles",
        "value": 0,
        "windowed": false
      },
      "potential_damage": {
        "label": "Potential damage cases",
        "note": "Findings no human has verified. Unverified means the detector flagged it, not that the damage is confirmed.",
        "unit": "findings",
        "value": 6,
        "windowed": true
      },
      "vehicles_today": {
        "label": "Vehicles checked in today",
        "unit": "vehicles",
        "value": 1,
        "windowed": false
      }
    },
    "provenance": {
      "contains_generated_history": false,
      "generated_tickets": 0,
      "generator_meta": null,
      "note": "No generated operational history is present; every figure comes from records created through the application."
    },
    "report": "valet_operations",
    "series": {
      "custody_duration": {
        "n": 1,
        "points": [
          {
            "bucket": "4-6h",
            "value": 1
          }
        ],
        "sufficient": false,
        "title": "Custody duration",
        "unit": "sessions"
      },
      "damage_detections": {
        "n": 6,
        "points": [
          {
            "CONFIRMED": 0,
            "REJECTED": 0,
            "UNVERIFIED": 6,
            "total": 6,
            "week": "2026-09-07"
          }
        ],
        "sufficient": false,
        "title": "Damage detections",
        "unit": "findings"
      },
      "retrieval_time": {
        "n": 0,
        "points": [],
        "sufficient": false,
        "title": "Average retrieval time",
        "unit": "minutes"
      },
      "security_alerts": {
        "n": 1,
        "points": [
          {
            "by_severity": {
              "HIGH": 1
            },
            "open": 1,
            "total": 1,
            "week": "2026-09-07"
          }
        ],
        "sufficient": false,
        "title": "Security alerts",
        "unit": "alerts"
      },
      "vehicles_per_day": {
        "n": 30,
        "points": [
          {
            "date": "2026-08-14",
            "value": 0
          },
          {
            "date": "2026-08-15",
            "value": 0
          },
          {
            "date": "2026-08-16",
            "value": 0
          },
          {
            "date": "2026-08-17",
            "value": 0
          },
          {
            "date": "2026-08-18",
            "value": 0
          },
          {
            "date": "2026-08-19",
            "value": 0
          },
          {
            "date": "2026-08-20",
            "value": 
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## Audit Logs

The append-only record of every significant action.

### `GET /api/v1/audit`

**Query the audit trail**

The append-only log of every significant action, newest first.

`mutable: false` and `mutability_note` are part of the response: the table has a database-level trigger that refuses `UPDATE` and `DELETE`, so immutability is enforced by PostgreSQL rather than by convention.

`vocabulary` lists the action values in use, so a client can build a filter from what actually exists.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_viewer` (`AUDIT_VIEW_ROLES`). |

**Query parameters**

| Name | Type | Default | Description |
|---|---|---|---|
| `action` | string | `—` | — |
| `entity_type` | string | `—` | — |
| `entity_id` | string | `—` | — |
| `actor_id` | uuid | `—` | — |
| `custody_session_id` | uuid | `—` | — |
| `outcome` | `SUCCESS`, `FAILURE` | `—` | — |
| `date_from` | date | `—` | — |
| `date_to` | date | `—` | — |
| `lifecycle_only` | boolean | `False` | Restrict to the custody lifecycle events. |
| `page` | integer | `1` | — |
| `page_size` | integer | `50` | Max 200. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is an operator or a customer. |
| `422` | A malformed date or UUID. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "filters": {
      "action": null,
      "actor_id": null,
      "custody_session_id": null,
      "date_from": null,
      "date_to": null,
      "entity_id": null,
      "entity_type": null,
      "lifecycle_only": false,
      "outcome": null
    },
    "items": [
      {
        "action": "USER_LOGIN",
        "actor_deleted_at": null,
        "actor_email": "manager.karan@example.com",
        "actor_id": "24758677-b3ca-581e-bc94-9dea4082aba2",
        "actor_role": "VALET_MANAGER",
        "created_at": "2026-09-12T15:07:10.545347+00:00",
        "custody_session_id": null,
        "description": "Signed in.",
        "entity_id": "24758677-b3ca-581e-bc94-9dea4082aba2",
        "entity_type": "users",
        "failure_reason": null,
        "id": "783d5736-96da-47b9-b245-da63cd343d5c",
        "ip_address": "127.0.0.1",
        "is_lifecycle_event": true,
        "label": "User logged in",
        "metadata": null,
        "new_values": {
          "role": "VALET_MANAGER"
        },
        "occurred_at": "2026-09-12T15:07:10.545347+00:00",
        "old_values": null,
        "outcome": "SUCCESS",
        "request_id": "9b87aaed9fbf43bda0e38b5a5b320119",
        "seq": 14,
        "user_agent": "python-requests/2.33.0"
      },
      {
        "action": "USER_LOGIN",
        "actor_deleted_at": null,
        "actor_email": "valet.suresh@example.com",
        "actor_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
        "actor_role": "VALET_OPERATOR",
        "created_at": "2026-09-12T15:07:10.400135+00:00",
        "custody_session_id": null,
        "description": "Signed in.",
        "entity_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
        "entity_type": "users",
        "failure_reason": null,
        "id": "2e9f567c-39f6-4b1d-86c6-b76f6a5df486",
        "ip_address": "127.0.0.1",
        "is_lifecycle_event": true,
        "label": "User logged in",
        "metadata": null,
        "new_values": {
          "role": "VALET_OPERATOR"
        },
        "occurred_at": "2026-09-12T15:07:10.400135+00:00",
        "old_values": null,
        "outcome": "SUCCESS",
        "request_id": "0911a057f5e04dd694afbc4ee41ae2ff",
        "seq": 13,
        "user_agent": "python-requests/2.33.0"
      },
      {
        "action": "USER_LOGIN",
        "actor_deleted_at": null,
        "actor_email": "customer.aisha@example.com",
        "actor_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "actor_role": "CUSTOMER",
        "created_at": "2026-09-12T15:07:10.234052+00:00",
        "custody_session_id": null,
        "description": "Signed in.",
        "entity_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "entity_type": "users",
        "failure_reason": null,
        "id": "e3ff5fb2-9274-4582-a1b4-26c76f07f6b4",
        "ip_address": "127.0.0.1",
        "is_lifecycle_event": true,
        "label": "User logged in",
        "metadata": null,
        "new_values": {
          "role": "CUSTOMER"
        },
        "occurred_at": "2026-09-12T15:07:10.234052+00:00",
        "old_values": null,
        "outcome": "SUCCESS",
        "request_id": "77d5bff20f3a4154a3bf503ba3c6fa98",
        "seq": 12,
        "user_agent": "python-requests/2.33.0"
      },
      {
        "action": "USER_LOGIN",
        "actor_deleted_at": null,
        "actor_email": "admin@valet.local",
        "actor_id": "14254104-597c-59d0-9fd5-7320e446a3e9",
        "actor_role": "ADMIN",
        "created_at": "2026-09-12T15:07:10.088861+00:00",
        "custody_session_id": null,
        "description": "Signed in.",
        "entity_id": "14254104-597c-59d0-9fd5-7320e446a3e9",
        "entity_type": "users",
        "failure_reason": null,
        "id": "c4a71390-317b-41be-b03d-4b085efb248c",
        "ip_address": "127.0.0.1",
        "is_lifecycle_event": true,
        "label": "User logged in",
        "metadata": null,
        "new_values": {
          "role": "ADMIN"
        },
        "occurred_at": "2026-09-12T15:07:10.088861+0
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/audit/filters`

**The values the audit filters can take**

The distinct actions, entity types, lifecycle actions and outcomes present in the log, plus human labels.

Served by the API so the filter dropdowns cannot offer a value that would return nothing.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_viewer` (`AUDIT_VIEW_ROLES`). |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is an operator or a customer. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "actions": [
      "CREATE",
      "DAMAGE_DETECTED",
      "EXPORT",
      "SECURITY_ALERT_CREATED",
      "USER_LOGIN",
      "VEHICLE_CHECKED_IN"
    ],
    "entity_types": [
      "custody_sessions",
      "damage_detections",
      "security_alerts",
      "users"
    ],
    "lifecycle_actions": [
      "USER_LOGIN",
      "VEHICLE_CREATED",
      "TICKET_CREATED",
      "VEHICLE_CHECKED_IN",
      "IMAGE_UPLOADED",
      "CUSTODY_STARTED",
      "GPS_BREACH",
      "SECURITY_ALERT_CREATED",
      "PICKUP_REQUESTED",
      "RETRIEVAL_PREDICTED",
      "PICKUP_IMAGE_CAPTURED",
      "DAMAGE_DETECTED",
      "VEHICLE_PICKED_UP",
      "CUSTODY_COMPLETED"
    ],
    "outcomes": [
      "SUCCESS"
    ]
  },
  "meta": {
    "request_id": "62965961fb6445a5be8b2955c04ff8f0",
    "server_time": 1789225630.7596455
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/audit/timeline/{custody_id}`

**The audit timeline for one custody session**

Every event for one session, ordered by `seq`.

`lifecycle_progress` and `lifecycle_missing` report which of the expected lifecycle events are present and which are not, so an incomplete handover is visible rather than merely short.

`had_security_incident` summarises whether any security alert touched this session.

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_viewer` (`AUDIT_VIEW_ROLES`). |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `custody_id` | `uuid` | The custody **session** UUID — not an inspection id or a ticket id. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is an operator or a customer. |
| `404` | No such custody session. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
    "dropped_off_at": "2026-09-12T13:24:30+00:00",
    "event_count": 6,
    "events": [
      {
        "action": "VEHICLE_CHECKED_IN",
        "actor_deleted_at": null,
        "actor_email": "valet.suresh@example.com",
        "actor_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
        "actor_role": "VALET_OPERATOR",
        "created_at": "2026-09-12T15:04:30.151668+00:00",
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "description": "[DEMO] Vehicle dropped off and custody opened by the seeder.",
        "entity_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "entity_type": "custody_sessions",
        "failure_reason": null,
        "id": "41c393d2-57dc-49b2-90ec-d4192089fcc5",
        "ip_address": null,
        "is_lifecycle_event": true,
        "label": "Vehicle checked in",
        "metadata": null,
        "new_values": {
          "status": "PARKED",
          "zone": "ZONE-A"
        },
        "occurred_at": "2026-09-12T15:04:30.151668+00:00",
        "old_values": null,
        "outcome": "SUCCESS",
        "request_id": "seed-fffac3ca337c",
        "scope": "custody",
        "seq": 2,
        "user_agent": null
      },
      {
        "action": "SECURITY_ALERT_CREATED",
        "actor_deleted_at": null,
        "actor_email": null,
        "actor_id": null,
        "actor_role": null,
        "created_at": "2026-09-12T15:04:30.151668+00:00",
        "custody_session_id": "5514bf61-f332-5956-8e9f-aeb5062621b5",
        "description": "[DEMO] Geofence breach raised from simulated telemetry.",
        "entity_id": "6064d352-c982-5c17-b68d-7e9103481b82",
        "entity_type": "security_alerts",
        "failure_reason": null,
        "id": "be002fb2-8efb-4cf7-9ff7-29e0ab3a027e",
        "ip_address": null,
        "is_lifecycle_event": true,
        "label": "Security alert raised",
        "metadata": null,
        "new_values": {
          "alert_type": "GEOFENCE_BREACH",
          "severity": "HIGH"
        },
        "occurred_at": "2026-09-12T15:04:30.151668+00:00",
        "old_values": null,
        "outcome": "SUCCESS",
        "request_id": "seed-621a9773a376",
        "scope": "custody",
        "seq": 4,
        "user_agent": null
      },
      {
        "action": "USER_LOGIN",
        "actor_deleted_at": null,
        "actor_email": "customer.aisha@example.com",
        "actor_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "actor_role": "CUSTOMER",
        "created_at": "2026-09-12T15:06:30.708892+00:00",
        "custody_session_id": null,
        "description": "Signed in.",
        "entity_id": "5d4776d4-18f7-5bbd-934a-db567bb48f9a",
        "entity_type": "users",
        "failure_reason": null,
        "id": "ec2efcae-1420-4583-a5b8-6287597aac68",
        "ip_address": "127.0.0.1",
        "is_lifecycle_event": true,
        "label": "User logged in",
        "metadata": null,
        "new_values": {
          "role": "CUSTOMER"
        },
        "occurred_at": "2026-09-12T15:06:30.708892+00:00",
        "old_values": null,
        "outcome": "SUCCESS",
        "request_id": "04e6674935e34abdac0c8ae52d72bf52",
        "scope": "login",
        "seq": 7,
        "user_agent": "python-requests/2.33.0"
      },
      {
        "action": "USER_LOGIN",
        "actor_deleted_at": null,
        "actor_email": "valet.suresh@example.com",
        "actor_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
        "actor_role": "VALET_OPERATOR",
        "created_at": "2026-09-12T15:06:30.859074+00:00",
        "custody_session_id": null,
        "description": "Signed in.",
        "entity_id": "2a9aca56-4bcd-5263-9454-27f20980716a",
        "entity_type": "users",
        "failure_reason": null,
        "id": "92728715-66f1-4dc0-888e-624a37806f47",
        "ip_address": "127.0.0.1",
        "is_lifecycle_event": true,
        "label": "User logged in",
        "metadata": null,
        "new
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/v1/audit/vehicles/{vehicle_id}`

**Every custody timeline for one vehicle**

The full history of a vehicle across all its custody sessions, with a timeline per session. Used to answer "what happened to this car every time we held it?"

| | |
|---|---|
| **Authentication** | `VALET_MANAGER` or `ADMIN`. |
| **Token** | `Authorization: Bearer <access_token>` |
| **Enforced by** | Enforced by `_require_viewer` (`AUDIT_VIEW_ROLES`). |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `vehicle_id` | `uuid` | The vehicle's UUID. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `401` | Unauthenticated. |
| `403` | The caller is an operator or a customer. |
| `404` | No such vehicle. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "custody_count": 0,
    "timelines": [],
    "vehicle": {
      "color": "Titan Grey",
      "id": "448c7417-3c19-5bb7-862c-3a8c981f95e2",
      "make": "Hyundai",
      "model": "Creta",
      "plate_number": "UP 78 CD 5678",
      "status": "REGISTERED"
    }
  },
  "meta": {
    "request_id": "e8c1435349584eb2b04a6debcdb8044c",
    "server_time": 1789225631.0451152
  },
  "success": true
}
```

*Example `401` response*

```json
{
  "success": false,
  "error": {
    "code": "AUTHENTICATION_ERROR",
    "message": "Authentication is required to access this resource.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

## System

Health, readiness and stored media.

### `GET /api/health`

**Liveness and dependency health**

The contract with load balancers and uptime monitors, so it lives outside the versioned surface and must not move when the API versions.

Reports the database check with its latency and server version, plus the configured `telemetry_source` for the installation.

| | |
|---|---|
| **Authentication** | None — public route. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `503` | A dependency is unhealthy. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "api_version": "v1",
    "checks": {
      "database": {
        "latency_ms": 0.96,
        "server_version": "17.10 (Debian 17.10-0+deb13u1)",
        "status": "up"
      }
    },
    "database_healthy": true,
    "environment": "development",
    "server_time": "2026-09-12T15:07:10.931969+00:00",
    "service": "valet-transparency-api",
    "status": "up",
    "telemetry_source": "simulated",
    "uptime_seconds": 153.31
  },
  "meta": {
    "request_id": "39c90fcbfef4433a9200b0565392272a",
    "server_time": 1789225630.931983
  },
  "success": true
}
```

*Example `503` response*

```json
{
  "success": false,
  "error": {
    "code": "SERVICE_UNAVAILABLE",
    "message": "Service unavailable: database check failed.",
    "details": {
      "checks": {
        "database": "down"
      }
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /api/readyz`

**Readiness probe**

Answers whether the service is ready to take traffic. Kept separate from `/api/health` so an orchestrator can distinguish "alive" from "able to serve".

| | |
|---|---|
| **Authentication** | None — public route. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `503` | Not ready — a dependency check failed. |

*Example `200` response* (captured from the running server)

```json
{
  "data": {
    "checks": {
      "database": {
        "latency_ms": 0.8,
        "server_version": "17.10 (Debian 17.10-0+deb13u1)",
        "status": "up"
      }
    },
    "status": "ready",
    "telemetry_source": "simulated"
  },
  "meta": {
    "request_id": "b7971ece61884fdd8b594650457b5b3a",
    "server_time": 1789225630.935153
  },
  "success": true
}
```

*Example `503` response*

```json
{
  "success": false,
  "error": {
    "code": "SERVICE_UNAVAILABLE",
    "message": "Service unavailable: database check failed.",
    "details": {
      "checks": {
        "database": "down"
      }
    }
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

### `GET /media/{reference}`

**Serve one stored image**

Serves a stored photograph, QR code or rendered comparison by the reference returned from the API.

**This route is unauthenticated**, because an `<img>` tag cannot send an `Authorization` header. The reference is a server-issued opaque path validated against traversal; a missing or invalid reference answers `404` rather than `422`, since "not here" is the useful answer to an image fetch.

The security audit records this as an accepted, documented risk, with signed URLs named as the remediation for a production deployment.

| | |
|---|---|
| **Authentication** | None — public route. |

**Path parameters**

| Name | Type | Description |
|---|---|---|
| `reference` | `string` | The stored path, e.g. `qr/bccfb577e36d488f8d8bf6977abb32a4.png`. |

*No request body.*

**Responses**

| Status | Meaning |
|---|---|
| `200` | Success. |
| `404` | No such stored object, or the reference is invalid. |

*Example `404` response*

```json
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "No vehicle was found with that id.",
    "details": null
  },
  "meta": {
    "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
    "server_time": 1789225630.95
  }
}
```

---

"""Endpoint catalogue: the single source of truth for the API documentation.

Every field here was taken from the running application, not inferred:

* paths and methods  -> ``app.url_map`` (dumped to ``urlmap.txt``)
* auth / roles       -> route decorators plus the service-layer gates
* request bodies     -> the ``require_fields`` / ``payload.get`` reads in the
                        route and service modules
* response examples  -> real captured payloads (``api_*_shapes.json``), pulled
                        in by ``example_from`` so an example cannot drift from
                        what the server actually returns

``build_docs.py`` renders both ``openapi.yaml`` and ``API-REFERENCE.md`` from
this module. ``verify_spec.py`` then cross-checks the rendered paths against the
live URL map and fails on any drift in either direction.
"""

from __future__ import annotations

UUID = {"type": "string", "format": "uuid"}
DATETIME = {"type": "string", "format": "date-time"}
STRING = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}

PAGINATION = [
    {
        "name": "page",
        "in": "query",
        "required": False,
        "schema": {"type": "integer", "minimum": 1, "default": 1},
        "description": "1-based page number.",
    },
    {
        "name": "page_size",
        "in": "query",
        "required": False,
        "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        "description": "Rows per page, capped at 200.",
    },
]

#: Human-readable role requirements, keyed for reuse.
ROLE_TEXT = {
    "public": "None. This route is public.",
    "auth": "Any authenticated role: `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`.",
    "staff": "`VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN`.",
    "manager": "`VALET_MANAGER` or `ADMIN`.",
    "admin": "`ADMIN` only.",
}

ENDPOINTS: list[dict] = []


def add(**kw) -> dict:
    kw.setdefault("params", [])
    kw.setdefault("request_body", None)
    kw.setdefault("example_request", None)
    kw.setdefault("example_from", None)
    kw.setdefault("role_note", "")
    ENDPOINTS.append(kw)
    return kw


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

add(
    group="Authentication",
    method="post",
    path="/api/v1/auth/register",
    operationId="register",
    summary="Self-register a customer account",
    description=(
        "Creates a `CUSTOMER` account. This is the only public write route in the "
        "API.\n\n"
        "A `role` field may be sent but **only `CUSTOMER` is accepted** — asking for "
        "any other role returns `403`, so public registration can never produce "
        "staff. Passwords are stored as salted PBKDF2-SHA256 digests "
        "(600,000 iterations by default), never as plain text.\n\n"
        "Login is rate limited: after 5 failed attempts for an email address "
        "further attempts are refused with `429` for a cooldown window."
    ),
    auth="none",
    roles="public",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["email", "password", "full_name"],
            "properties": {
                "email": {"type": "string", "format": "email"},
                "password": {"type": "string", "minLength": 8},
                "full_name": {"type": "string", "maxLength": 120},
                "phone": {"type": "string", "nullable": True},
                "role": {
                    "type": "string",
                    "enum": ["CUSTOMER"],
                    "description": "Optional. Only `CUSTOMER` is permitted here.",
                },
            },
        },
    },
    example_request={
        "email": "aisha@example.com",
        "password": "Str0ng#Passw0rd",
        "full_name": "Aisha Verma",
        "phone": "+91 98100 10010",
    },
    example_from=("api_write_shapes.json", "POST /auth/register"),
    errors=[
        ("403", "A non-`CUSTOMER` role was requested. Staff accounts are created by an administrator via `POST /api/v1/auth/users`."),
        ("409", "An account with that email already exists."),
        ("422", "Missing or malformed field."),
    ],
)

add(
    group="Authentication",
    method="post",
    path="/api/v1/auth/login",
    operationId="login",
    summary="Exchange credentials for tokens",
    description=(
        "Returns a short-lived access token and a long-lived refresh token, plus "
        "the signed-in user.\n\n"
        "The access token is a JWT (HS256) carrying `sub`, `role`, `typ`, `jti`, "
        "`iss`, `aud`, `iat`, `nbf` and `exp`. Send it as "
        "`Authorization: Bearer <access_token>`.\n\n"
        "Failed attempts are throttled per email address; the 6th consecutive "
        "failure returns `429` rather than `401`."
    ),
    auth="none",
    roles="public",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["email", "password"],
            "properties": {
                "email": {"type": "string", "format": "email"},
                "password": {"type": "string"},
            },
        },
    },
    example_request={"email": "manager.karan@example.com", "password": "Demo#Passw0rd"},
    example_from=("api_shapes.json", "GET /auth/me"),
    errors=[
        ("401", "The email or password is wrong. The message does not say which."),
        ("429", "Too many failed attempts for this email address. Retry after the cooldown."),
        ("422", "Missing field."),
    ],
)

add(
    group="Authentication",
    method="post",
    path="/api/v1/auth/logout",
    operationId="logout",
    summary="Revoke the refresh token",
    description=(
        "Revokes the presented refresh token by recording its `jti` in the "
        "`revoked_tokens` table, which is append-only. A revoked token cannot be "
        "used again even though it has not expired.\n\n"
        "The access token is *not* revoked — it stays valid until its own `exp`. "
        "Its lifetime is short (30 minutes by default) for exactly this reason."
    ),
    auth="bearer",
    roles="auth",
    request_body={
        "required": False,
        "schema": {
            "type": "object",
            "properties": {
                "refresh_token": {
                    "type": "string",
                    "description": "The refresh token to revoke.",
                }
            },
        },
    },
    example_request={"refresh_token": "<refresh token from login>"},
    example_from=("api_write_shapes.json", "POST /auth/logout"),
    errors=[("401", "Missing, malformed or expired token.")],
)

add(
    group="Authentication",
    method="get",
    path="/api/v1/auth/me",
    operationId="getCurrentUser",
    summary="The signed-in user",
    description=(
        "Returns the account the presented token belongs to. Clients call this on "
        "startup to restore a session from a stored token and to learn the role "
        "that decides which navigation to render."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_shapes.json", "GET /auth/me"),
    errors=[("401", "Missing, malformed or expired token.")],
)

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

add(
    group="Users",
    method="post",
    path="/api/v1/auth/users",
    operationId="createUser",
    summary="Create a staff account (administrator)",
    description=(
        "Creates an account with an explicit role. This is the only way a "
        "`VALET_OPERATOR`, `VALET_MANAGER` or `ADMIN` account can come into "
        "existence — public registration refuses any non-customer role.\n\n"
        "The `role` is validated against the role vocabulary and an unknown value "
        "returns `422` listing the permitted ones."
    ),
    auth="bearer",
    roles="admin",
    role_note="Enforced by the `require_role(ADMIN)` decorator on the route.",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["email", "password", "full_name", "role"],
            "properties": {
                "email": {"type": "string", "format": "email"},
                "password": {"type": "string", "minLength": 8},
                "full_name": {"type": "string", "maxLength": 120},
                "role": {
                    "type": "string",
                    "enum": ["CUSTOMER", "VALET_OPERATOR", "VALET_MANAGER", "ADMIN"],
                },
                "phone": {"type": "string", "nullable": True},
            },
        },
    },
    example_request={
        "email": "suresh@valet.local",
        "password": "Str0ng#Passw0rd",
        "full_name": "Suresh Yadav",
        "role": "VALET_OPERATOR",
    },
    example_from=("api_write_shapes.json", "POST /auth/users"),
    errors=[
        ("403", "The caller is not an administrator."),
        ("409", "An account with that email already exists."),
        ("422", "Unknown role, or a missing/malformed field."),
    ],
)

add(
    group="Users",
    method="get",
    path="/api/v1/customers",
    operationId="listCustomers",
    summary="Customer directory for check-in",
    description=(
        "The customers a valet may hand a vehicle to. Exists so check-in can "
        "resolve a customer by name or email without exposing the full user "
        "table.\n\n"
        "Returns `id`, `full_name`, `email`, `phone` and `status` only — never a "
        "password digest or any other credential material."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by the `require_role(...)` decorator on the route.",
    params=[
        {
            "name": "search",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "maxLength": 80},
            "description": "Matches name or email.",
        },
    ]
    + PAGINATION,
    example_from=("api_shapes.json", "GET /customers"),
    errors=[("401", "Unauthenticated."), ("403", "The caller is a customer.")],
)

# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------

VEHICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": UUID,
        "plate_number": {"type": "string", "example": "UP 78 CD 5678"},
        "normalized_plate": {
            "type": "string",
            "example": "UP78CD5678",
            "description": "Separators stripped and upper-cased. The uniqueness index is keyed on this.",
        },
        "make": STRING,
        "model": {"type": "string", "nullable": True},
        "year": {"type": "integer", "nullable": True},
        "color": {"type": "string", "nullable": True},
        "vin": {"type": "string", "nullable": True},
        "notes": {"type": "string", "nullable": True},
        "status": {
            "type": "string",
            "enum": ["REGISTERED", "IN_CUSTODY", "FLAGGED", "ARCHIVED"],
        },
        "is_in_custody": BOOL,
        "owner_id": UUID,
        "owner": {
            "type": "object",
            "properties": {
                "id": UUID,
                "full_name": STRING,
                "email": STRING,
                "phone": {"type": "string", "nullable": True},
            },
        },
        "created_at": DATETIME,
        "updated_at": DATETIME,
    },
}

VEHICLE_BODY = {
    "required": True,
    "schema": {
        "type": "object",
        "required": ["plate_number", "make"],
        "properties": {
            "plate_number": {
                "type": "string",
                "description": "Indian registration number. Separators are optional: `UP 85 CA 1234`, `UP85CA1234` and the Bharat series `25BH0987AB` are all accepted.",
            },
            "make": {"type": "string", "minLength": 2, "maxLength": 60},
            "model": {"type": "string", "maxLength": 60},
            "year": {"type": "integer", "minimum": 1950, "maximum": 2100},
            "color": {"type": "string", "maxLength": 40},
            "vin": {"type": "string", "maxLength": 40},
            "notes": {"type": "string", "maxLength": 500},
            "owner_id": {
                "allOf": [UUID],
                "description": "Staff only. A customer registering for themselves must omit it — the owner is taken from the token.",
            },
        },
    },
}

add(
    group="Vehicles",
    method="get",
    path="/api/v1/vehicles",
    operationId="listVehicles",
    summary="List the vehicles the caller may see",
    description=(
        "Scoped server-side to the caller: a customer sees only their own "
        "vehicles, staff see every vehicle they may check in. Omitting a filter "
        "cannot widen the view.\n\n"
        "Ownership is derived from the token, never from a request parameter. A "
        "customer asking for someone else's vehicle gets `404` rather than `403`, "
        "so the response does not confirm the id exists."
    ),
    auth="bearer",
    roles="auth",
    role_note="Scoped by role in the service layer; no role is excluded.",
    params=[
        {
            "name": "status",
            "in": "query",
            "required": False,
            "schema": {
                "type": "string",
                "enum": ["REGISTERED", "IN_CUSTODY", "FLAGGED", "ARCHIVED"],
            },
        },
        {
            "name": "search",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "maxLength": 80},
            "description": "Matches plate, make or model.",
        },
        {
            "name": "owner_id",
            "in": "query",
            "required": False,
            "schema": UUID,
            "description": "Staff only. Filter to one customer's vehicles.",
        },
        {
            "name": "include_archived",
            "in": "query",
            "required": False,
            "schema": {"type": "boolean", "default": False},
        },
    ]
    + PAGINATION,
    example_from=("api_shapes.json", "GET /vehicles"),
    errors=[("401", "Unauthenticated."), ("422", "Unknown status value or malformed UUID.")],
)

add(
    group="Vehicles",
    method="post",
    path="/api/v1/vehicles",
    operationId="createVehicle",
    summary="Register a vehicle",
    description=(
        "A customer registers to themselves. Valet staff may pass `owner_id` to "
        "register on a customer's behalf during check-in; a customer who sends "
        "`owner_id` is refused.\n\n"
        "The plate is validated against Indian registration formats and "
        "uniqueness is enforced by a partial unique index on the normalised "
        "plate, so a duplicate returns `409` naming the field."
    ),
    auth="bearer",
    roles="auth",
    role_note="Any role may register; `owner_id` is restricted to staff.",
    request_body=VEHICLE_BODY,
    example_request={
        "plate_number": "UP85CA1234",
        "make": "Tata",
        "model": "Nexon",
        "year": 2022,
        "color": "Silver",
        "notes": "Roof rack fitted.",
    },
    example_from=("api_write_shapes.json", "POST /vehicles"),
    errors=[
        ("403", "A customer supplied `owner_id`."),
        ("409", "That registration number is already registered. `details.field` is `plate_number`."),
        ("422", "The plate is not a valid Indian registration number, or a required field is missing."),
    ],
)

add(
    group="Vehicles",
    method="get",
    path="/api/v1/vehicles/{vehicle_id}",
    operationId="getVehicle",
    summary="Fetch one vehicle",
    description=(
        "Returns `404` — not `403` — when the vehicle exists but belongs to "
        "another customer, so the response does not confirm that the id exists."
    ),
    auth="bearer",
    roles="auth",
    params=[{"name": "vehicle_id", "in": "path", "required": True, "schema": UUID,
             "description": "The vehicle's UUID."}],
    example_from=("api_shapes.json", "GET /vehicles/<id>"),
    errors=[("401", "Unauthenticated."), ("404", "No such vehicle, or it belongs to another customer.")],
)

add(
    group="Vehicles",
    method="patch",
    path="/api/v1/vehicles/{vehicle_id}",
    operationId="updateVehicle",
    summary="Update a vehicle",
    description=(
        "Partial update: only the fields present in the body are changed.\n\n"
        "Changing `plate_number` re-runs the format and uniqueness checks, so a "
        "collision returns `409` rather than silently overwriting another record."
    ),
    auth="bearer",
    roles="auth",
    role_note="The owner, or staff. Anyone else gets `404`.",
    params=[{"name": "vehicle_id", "in": "path", "required": True, "schema": UUID,
             "description": "The vehicle's UUID."}],
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "properties": VEHICLE_BODY["schema"]["properties"],
        },
    },
    example_request={"color": "Graphite", "notes": "Colour corrected."},
    example_from=("api_write_shapes.json", "PATCH /vehicles/<id>"),
    errors=[
        ("404", "No such vehicle, or it is not yours."),
        ("409", "The new plate is already taken."),
        ("422", "Malformed field."),
    ],
)

add(
    group="Vehicles",
    method="delete",
    path="/api/v1/vehicles/{vehicle_id}",
    operationId="archiveVehicle",
    summary="Archive a vehicle",
    description=(
        "Soft-deletes: the row moves to `ARCHIVED` and drops out of the active "
        "list, but the custody history that references it is preserved. Nothing "
        "is destroyed, because the audit trail must stay intact.\n\n"
        "A vehicle that is currently in custody cannot be archived — close the "
        "custody session first.\n\n"
        "Returns `204` with an empty body on success."
    ),
    auth="bearer",
    roles="auth",
    role_note="The owner, or staff. Anyone else gets `404`.",
    params=[{"name": "vehicle_id", "in": "path", "required": True, "schema": UUID,
             "description": "The vehicle's UUID."}],
    example_from=("api_write_shapes.json", "DELETE /vehicles/<id>"),
    errors=[
        ("404", "No such vehicle, or it is not yours."),
        ("409", "The vehicle has an open custody session."),
    ],
)

add(
    group="Vehicles",
    method="get",
    path="/api/v1/vehicles/{vehicle_id}/sessions",
    operationId="listVehicleSessions",
    summary="Custody history for one vehicle",
    description=(
        "Every previous custody session for the vehicle with the ticket it "
        "belongs to, newest first. Read-only: a session is closed by completing "
        "its ticket, not by editing it here."
    ),
    auth="bearer",
    roles="auth",
    params=[{"name": "vehicle_id", "in": "path", "required": True, "schema": UUID,
             "description": "The vehicle's UUID."}],
    example_from=("api_shapes.json", "GET /vehicles/<id>/sessions"),
    errors=[("401", "Unauthenticated."), ("404", "No such vehicle, or it is not yours.")],
)


# ---------------------------------------------------------------------------
# Valet Tickets
# ---------------------------------------------------------------------------

CUSTODY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": UUID,
        "ticket_id": UUID,
        "vehicle_id": UUID,
        "status": {
            "type": "string",
            "enum": [
                "CHECKED_IN",
                "PARKED",
                "IN_TRANSIT",
                "RETRIEVAL_REQUESTED",
                "RETRIEVAL_IN_PROGRESS",
                "READY_FOR_PICKUP",
                "CHECKED_OUT",
                "DISPUTED",
            ],
        },
        "zone_id": {"type": "string", "format": "uuid", "nullable": True},
        "zone_assigned_at": {"allOf": [DATETIME], "nullable": True},
        "dropped_off_at": {"allOf": [DATETIME], "nullable": True},
        "retrieval_requested_at": {"allOf": [DATETIME], "nullable": True},
        "ready_for_pickup_at": {"allOf": [DATETIME], "nullable": True},
        "picked_up_at": {"allOf": [DATETIME], "nullable": True},
        "assigned_valet_id": {"allOf": [UUID], "nullable": True},
        "condition_accepted": {"type": "boolean", "nullable": True},
        "is_open": BOOL,
    },
}

TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "id": UUID,
        "ticket_code": {"type": "string", "example": "VT-202609-7UYMVJ"},
        "status": {
            "type": "string",
            "enum": ["CREATED", "IN_CUSTODY", "READY_FOR_PICKUP", "COMPLETED", "CANCELLED"],
            "description": "The **projected** vocabulary the client was given.",
        },
        "stored_status": {
            "type": "string",
            "enum": ["ISSUED", "ACTIVE", "COMPLETED", "CANCELLED", "EXPIRED"],
            "description": "The value actually in the database. Returned alongside the projection so the mapping is visible rather than taken on trust.",
        },
        "vehicle_id": UUID,
        "vehicle": {
            "type": "object",
            "properties": {
                "id": UUID,
                "plate_number": STRING,
                "make": STRING,
                "model": {"type": "string", "nullable": True},
                "year": {"type": "integer", "nullable": True},
                "color": {"type": "string", "nullable": True},
                "status": STRING,
            },
        },
        "customer_id": UUID,
        "customer": {
            "type": "object",
            "properties": {
                "id": UUID,
                "full_name": STRING,
                "email": STRING,
                "phone": {"type": "string", "nullable": True},
            },
        },
        "parking_zone": {
            "type": "object",
            "nullable": True,
            "properties": {"id": UUID, "code": STRING, "name": STRING},
        },
        "qr_payload": {"type": "string", "example": "VALET:VT-202609-7UYMVJ"},
        "qr_url": {
            "type": "string",
            "example": "/media/qr/bccfb577e36d488f8d8bf6977abb32a4.png",
            "description": "Server-rendered PNG. The QR encodes `qr_payload`, not a URL, so a forged code cannot point elsewhere.",
        },
        "qr_code_path": STRING,
        "special_instructions": {"type": "string", "nullable": True},
        "issued_at": DATETIME,
        "expected_return_at": {"allOf": [DATETIME], "nullable": True},
        "actual_drop_off_at": {"allOf": [DATETIME], "nullable": True},
        "actual_pickup_at": {"allOf": [DATETIME], "nullable": True},
        "custody_session": {"allOf": [CUSTODY_SCHEMA], "nullable": True},
    },
}

add(
    group="Valet Tickets",
    method="get",
    path="/api/v1/tickets",
    operationId="listTickets",
    summary="List the tickets the caller may see",
    description=(
        "Scoped server-side: a customer sees their own tickets, staff see the "
        "tickets they may act on.\n\n"
        "`?status=` filters on the **projected** status, because that is the "
        "vocabulary the client was given. The stored value is an implementation "
        "detail of the backend."
    ),
    auth="bearer",
    roles="auth",
    params=[
        {
            "name": "status",
            "in": "query",
            "required": False,
            "schema": {
                "type": "string",
                "enum": ["CREATED", "IN_CUSTODY", "READY_FOR_PICKUP", "COMPLETED", "CANCELLED"],
            },
        }
    ]
    + PAGINATION,
    example_from=("api_shapes.json", "GET /tickets"),
    errors=[("401", "Unauthenticated."), ("422", "Unknown status value.")],
)

add(
    group="Valet Tickets",
    method="post",
    path="/api/v1/tickets",
    operationId="issueTicket",
    summary="Issue a digital valet ticket",
    description=(
        "Creates the ticket and renders its QR code server-side. Custody is "
        "**not** started here — that happens at `POST /tickets/{id}/check-in`, so "
        "issuing a ticket and physically taking the keys are separate recorded "
        "events.\n\n"
        "The returned ticket carries both `status` (`CREATED`) and `stored_status` "
        "(`ISSUED`)."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_valet` in `services/tickets.py`.",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["vehicle_id"],
            "properties": {
                "vehicle_id": UUID,
                "special_instructions": {"type": "string", "maxLength": 500},
            },
        },
    },
    example_request={"vehicle_id": "<vehicle uuid>", "special_instructions": "Handle with care."},
    example_from=("api_write_shapes.json", "POST /tickets"),
    errors=[
        ("403", "The caller is a customer."),
        ("404", "No such vehicle."),
        ("409", "The vehicle already has an open ticket."),
        ("422", "Missing `vehicle_id`."),
    ],
)

add(
    group="Valet Tickets",
    method="get",
    path="/api/v1/tickets/{ticket_id}",
    operationId="getTicket",
    summary="Fetch one ticket",
    description="The digital ticket, including its custody session when one has been opened.",
    auth="bearer",
    roles="auth",
    params=[{"name": "ticket_id", "in": "path", "required": True, "schema": UUID,
             "description": "The ticket's UUID (not the human-readable `ticket_code`)."}],
    example_from=("api_shapes.json", "GET /tickets/<id>"),
    errors=[("401", "Unauthenticated."), ("404", "No such ticket, or it is not yours.")],
)

add(
    group="Valet Tickets",
    method="post",
    path="/api/v1/tickets/{ticket_id}/check-in",
    operationId="checkIn",
    summary="Hand the vehicle over and open custody",
    description=(
        "Opens the custody session, assigns a parking zone and records the "
        "drop-off condition. Zone occupancy increments by one.\n\n"
        "`zone_id` is required — the zone is chosen at handover, not at issue "
        "time, because that is when the valet knows where the car is actually "
        "going.\n\n"
        "The session starts at `PARKED`. Both `status` and `stored_status` move: "
        "the ticket projects to `IN_CUSTODY` while the stored value becomes "
        "`ACTIVE`."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_valet` in `services/tickets.py`.",
    params=[{"name": "ticket_id", "in": "path", "required": True, "schema": UUID,
             "description": "The ticket's UUID (not the human-readable `ticket_code`)."}],
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["zone_id"],
            "properties": {
                "zone_id": UUID,
                "odometer": {"type": "integer", "minimum": 0},
                "fuel_level": {"type": "string"},
                "notes": {"type": "string", "maxLength": 500},
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Drop-off photo references returned by `POST /api/v1/uploads/images`.",
                },
            },
        },
    },
    example_request={"zone_id": "<zone uuid>", "odometer": 41200, "notes": "Keys and RC received."},
    example_from=("api_write_shapes.json", "POST /tickets/<id>/check-in"),
    errors=[
        ("403", "The caller is a customer."),
        ("404", "No such ticket or zone."),
        ("409", "The ticket already has an open custody session."),
        ("422", "Missing `zone_id`."),
    ],
)

add(
    group="Valet Tickets",
    method="post",
    path="/api/v1/tickets/{ticket_id}/complete",
    operationId="completeTicket",
    summary="Return the vehicle and close the ticket",
    description=(
        "Closes the custody session, frees the parking slot and records the "
        "condition the customer accepted. Zone occupancy decrements and the "
        "vehicle returns to `REGISTERED`.\n\n"
        "This is the **only** way to close custody. Sending `CHECKED_OUT` to "
        "`PATCH /custody-sessions/{id}` is refused with `422`, because completing "
        "a pickup has to settle the ticket, the zone occupancy and the vehicle "
        "status together — doing it in three calls would let them disagree."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_valet` in `services/tickets.py`.",
    params=[{"name": "ticket_id", "in": "path", "required": True, "schema": UUID,
             "description": "The ticket's UUID (not the human-readable `ticket_code`)."}],
    request_body={
        "required": False,
        "schema": {
            "type": "object",
            "properties": {
                "odometer_at_pickup": {"type": "integer", "minimum": 0},
                "condition_accepted": {
                    "type": "boolean",
                    "description": "Whether the customer accepted the vehicle's condition. `false` marks the session disputed.",
                },
                "condition_notes": {"type": "string", "maxLength": 500},
            },
        },
    },
    example_request={"odometer_at_pickup": 41320, "condition_accepted": True},
    example_from=("api_write_shapes.json", "POST /tickets/<id>/complete"),
    errors=[
        ("403", "The caller is a customer."),
        ("404", "No such ticket."),
        ("409", "The ticket has no open custody session to close."),
    ],
)

add(
    group="Valet Tickets",
    method="post",
    path="/api/v1/tickets/{ticket_id}/cancel",
    operationId="cancelTicket",
    summary="Void a ticket",
    description=(
        "Cancels the ticket and closes any open custody session, freeing the "
        "zone slot. The reason is recorded so a cancelled handover can be "
        "explained later rather than simply vanishing."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_valet` in `services/tickets.py`.",
    params=[{"name": "ticket_id", "in": "path", "required": True, "schema": UUID,
             "description": "The ticket's UUID (not the human-readable `ticket_code`)."}],
    request_body={
        "required": False,
        "schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "maxLength": 500}},
        },
    },
    example_request={"reason": "Customer changed plans."},
    example_from=("api_write_shapes.json", "POST /tickets/<id>/cancel"),
    errors=[
        ("403", "The caller is a customer."),
        ("404", "No such ticket."),
        ("409", "The ticket is already completed or cancelled."),
    ],
)

# ---------------------------------------------------------------------------
# Custody
# ---------------------------------------------------------------------------

add(
    group="Custody",
    method="get",
    path="/api/v1/custody-sessions/{session_id}",
    operationId="getCustodySession",
    summary="Fetch one custody session",
    description="The session's current state and its lifecycle timestamps.",
    auth="bearer",
    roles="auth",
    params=[{"name": "session_id", "in": "path", "required": True, "schema": UUID,
             "description": "The custody session's UUID."}],
    example_from=("api_shapes.json", "GET /custody-sessions/<id>"),
    errors=[("401", "Unauthenticated."), ("404", "No such session, or it is not yours.")],
)

add(
    group="Custody",
    method="patch",
    path="/api/v1/custody-sessions/{session_id}",
    operationId="updateCustodySession",
    summary="Move a custody session to a new state",
    description=(
        "Every write is checked against the permitted transition table, and an "
        "illegal jump is refused with **409** carrying `{from, to, allowed}` so a "
        "client can recover instead of guessing.\n\n"
        "Permitted transitions:\n\n"
        "```\n"
        "CHECKED_IN            -> PARKED, IN_TRANSIT\n"
        "PARKED                -> IN_TRANSIT, RETRIEVAL_REQUESTED, READY_FOR_PICKUP\n"
        "IN_TRANSIT            -> PARKED, RETRIEVAL_IN_PROGRESS, READY_FOR_PICKUP\n"
        "RETRIEVAL_REQUESTED   -> RETRIEVAL_IN_PROGRESS, READY_FOR_PICKUP\n"
        "RETRIEVAL_IN_PROGRESS -> READY_FOR_PICKUP\n"
        "READY_FOR_PICKUP      -> CHECKED_OUT, RETRIEVAL_IN_PROGRESS\n"
        "DISPUTED              -> CHECKED_OUT\n"
        "CHECKED_OUT           -> (terminal)\n"
        "```\n\n"
        "`CHECKED_OUT` is not reachable through this route at all: it returns "
        "`422` directing the caller to `POST /tickets/{id}/complete`."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_valet` in `services/tickets.py`.",
    params=[{"name": "session_id", "in": "path", "required": True, "schema": UUID,
             "description": "The custody session's UUID."}],
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["status"],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "CHECKED_IN",
                        "PARKED",
                        "IN_TRANSIT",
                        "RETRIEVAL_REQUESTED",
                        "RETRIEVAL_IN_PROGRESS",
                        "READY_FOR_PICKUP",
                        "DISPUTED",
                    ],
                }
            },
        },
    },
    example_request={"status": "RETRIEVAL_IN_PROGRESS"},
    example_from=("api_write_shapes.json", "PATCH /custody-sessions/<id>"),
    errors=[
        ("403", "The caller is a customer."),
        ("404", "No such session."),
        ("409", "Illegal transition. `details` carries `{from, to, allowed}`."),
        ("422", "`status` is `CHECKED_OUT` — use the ticket completion endpoint instead."),
    ],
)


# ---------------------------------------------------------------------------
# Inspections
# ---------------------------------------------------------------------------

DETECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": UUID,
        "inspection_id": UUID,
        "damage_type": {
            "type": "string",
            "enum": [
                "SCRATCH", "DENT", "CRACK", "PAINT_CHIP", "RUST",
                "GLASS_DAMAGE", "TYRE_DAMAGE", "MISSING_PART", "DIRT_STAIN", "OTHER",
            ],
        },
        "severity": {"type": "string", "enum": ["COSMETIC", "MINOR", "MODERATE", "SEVERE"]},
        "region": {"type": "string", "example": "front_bumper"},
        "angle": {"type": "string", "nullable": True},
        "confidence": {
            "type": "number",
            "example": 0.82,
            "description": "How confident the pipeline is that *something changed* in this region.",
        },
        "type_confidence": {
            "type": "number",
            "example": 0.54,
            "description": "How confident it is about *naming* the defect. Deliberately reported separately, because naming a defect is a weaker claim than detecting a change.",
        },
        "bounding_box": {
            "type": "object",
            "description": "Normalised fractions of the frame, not pixel coordinates.",
            "properties": {"x": NUM, "y": NUM, "width": NUM, "height": NUM},
        },
        "presentation_label": {
            "type": "string",
            "example": "Potential Damage",
            "description": "`Potential Damage` until a manager confirms or rejects it.",
        },
        "verification_status": {
            "type": "string",
            "enum": ["UNVERIFIED", "CONFIRMED", "REJECTED"],
        },
        "verification_notes": {"type": "string", "nullable": True},
        "verified_by_id": {"allOf": [UUID], "nullable": True},
        "verified_at": {"allOf": [DATETIME], "nullable": True},
        "is_pre_existing": BOOL,
        "requires_customer_attention": BOOL,
        "detection_reasoning": {"type": "string"},
        "method": {"type": "string", "example": "CLASSICAL_CV"},
        "model_version": {
            "type": "string",
            "nullable": True,
            "description": "`null` for classical CV findings — there is no trained model behind them, and the database CHECK constraint enforces that.",
        },
        "evidence_url": {"type": "string", "nullable": True},
        "comparison_url": {"type": "string", "nullable": True},
        "algorithm_params": {"type": "object"},
    },
}

add(
    group="Inspections",
    method="post",
    path="/api/v1/uploads/images",
    operationId="uploadImages",
    summary="Store images and get validated references back",
    description=(
        "Accepts `multipart/form-data`. Send one or more files under the "
        "`images` field (a single `image` field also works).\n\n"
        "Each file is validated before it is stored: extension allow-list "
        "(`jpg`, `jpeg`, `png`, `webp`), minimum 64 pixels per side, maximum "
        "4096. The stored reference is returned with its SHA-256 digest, so an "
        "inspection can later prove which bytes it assessed.\n\n"
        "The returned `path` values are what the inspection endpoints expect."
    ),
    auth="bearer",
    roles="auth",
    request_body={
        "required": True,
        "content_type": "multipart/form-data",
        "schema": {
            "type": "object",
            "properties": {
                "images": {
                    "type": "array",
                    "items": {"type": "string", "format": "binary"},
                    "description": "One or more image files.",
                },
                "image": {
                    "type": "string",
                    "format": "binary",
                    "description": "Alternative single-file field.",
                },
                "subdir": {"type": "string", "default": "inspections"},
                "label": {"type": "string"},
            },
        },
    },
    example_request={"images": "<binary>", "subdir": "inspections"},
    example_from=("api_write_shapes.json", "POST /uploads/images"),
    errors=[
        ("413", "A file exceeds `MAX_CONTENT_LENGTH_MB` (15 by default)."),
        ("415", "A file is not in the allowed extension list."),
        ("422", "An image is smaller than 64 pixels on a side."),
    ],
)

for stage, slug, extra in (
    (
        "DROP_OFF",
        "dropoff",
        "This is the baseline every later comparison is measured against. Nothing "
        "is compared at this stage, because there is no *after* yet.",
    ),
    (
        "PICKUP",
        "pickup",
        "Each angle is compared against its drop-off counterpart **in the same "
        "request**, so the response already carries the candidate findings, the "
        "rendered comparison composite and a per-finding evidence crop.\n\n"
        "Angles with no drop-off counterpart are listed in `unmatched_angles` "
        "rather than silently dropped.",
    ),
):
    add(
        group="Inspections",
        method="post",
        path=f"/api/v1/inspections/{slug}",
        operationId=f"capture{'DropOff' if slug == 'dropoff' else 'Pickup'}",
        summary=f"Capture the {'drop-off' if slug == 'dropoff' else 'pickup'} photo set",
        description=(
            f"Records a `{stage}` inspection against a custody session.\n\n{extra}\n\n"
            "One angle is sent per photo as a **parallel `angles` list**. A count "
            "mismatch is rejected rather than guessed, because applying one angle "
            "to a mixed set would compare unrelated viewpoints and flag the whole "
            "vehicle.\n\n"
            "Findings are candidates for human review, never a confirmed "
            "assessment."
        ),
        auth="bearer",
        roles="staff",
        role_note="Enforced by `_require_inspection_role` (`INSPECTION_ROLES`).",
        request_body={
            "required": True,
            "content_type": "multipart/form-data",
            "schema": {
                "type": "object",
                "required": ["custody_id", "images", "angles"],
                "properties": {
                    "custody_id": {"type": "string", "description": "The custody **session** id."},
                    "images": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                    },
                    "angles": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "FRONT", "REAR", "LEFT_SIDE", "RIGHT_SIDE",
                                "TOP", "WHEEL", "INTERIOR", "GENERAL",
                            ],
                        },
                        "description": "One entry per image, in the same order.",
                    },
                    "notes": {"type": "string", "maxLength": 1000},
                    "odometer": {"type": "integer"},
                    "location": {"type": "string"},
                },
            },
        },
        example_request={
            "custody_id": "<custody session uuid>",
            "images": "<binary>",
            "angles": ["FRONT", "REAR"],
        },
        example_from=("api_write_shapes.json", f"POST /inspections/{slug}"),
        errors=[
            ("403", "The caller is a customer."),
            ("404", "No such custody session."),
            ("415", "A file is not an allowed image type."),
            ("422", "The angle count does not match the image count, or an image is below the pixel floor."),
        ],
    )

add(
    group="Inspections",
    method="get",
    path="/api/v1/inspections/{custody_id}",
    operationId="getConditionReport",
    summary="The full condition report for a custody session",
    description=(
        "Both photo sets, every finding, and the rendered evidence. The response "
        "is **flat** — `drop_off` and `pickup` sit at the top level rather than "
        "inside a wrapper list.\n\n"
        "`detector_disclaimer` travels with every report, so a client cannot "
        "render the findings without the caveat that produced them."
    ),
    auth="bearer",
    roles="auth",
    params=[{"name": "custody_id", "in": "path", "required": True, "schema": UUID,
             "description": "The custody **session** UUID — not an inspection id or a ticket id."}],
    example_from=("api_shapes.json", "GET /inspections/<custody_id>"),
    errors=[("401", "Unauthenticated."), ("404", "No such custody session, or it is not yours.")],
)

add(
    group="Inspections",
    method="patch",
    path="/api/v1/inspections/detections/{detection_id}/verification",
    operationId="verifyDetection",
    summary="Confirm or reject a damage finding",
    description=(
        "Moves a finding between `UNVERIFIED`, `CONFIRMED` and `REJECTED`.\n\n"
        "Deliberately narrower than capture: the person who photographed the car "
        "should not be the only person who judges whether the algorithm was right "
        "about it.\n\n"
        "`presentation_label` changes with the status — `Potential Damage`, "
        "`Confirmed Damage`, `Dismissed - Not Damage`."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_verifier` (`VERIFICATION_ROLES`).",
    params=[{"name": "detection_id", "in": "path", "required": True, "schema": UUID,
             "description": "The damage finding's UUID."}],
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["status"],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["UNVERIFIED", "CONFIRMED", "REJECTED"],
                },
                "notes": {"type": "string", "maxLength": 500},
            },
        },
    },
    example_request={"status": "CONFIRMED", "notes": "Confirmed on review."},
    example_from=("api_verify_shapes.json", "PATCH /inspections/detections/<id>/verification"),
    errors=[
        ("403", "The caller is an operator or a customer."),
        ("404", "No such detection."),
        ("422", "Unknown status value."),
    ],
)

add(
    group="Inspections",
    method="get",
    path="/api/v1/inspections/demo/scenarios",
    operationId="listDemoScenarios",
    summary="The synthetic scenarios the demo can run",
    description=(
        "The scenario list for `POST /api/v1/inspections/demo`, served by the API "
        "rather than hard-coded in the client so the two cannot drift."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_shapes.json", "GET /inspections/demo/scenarios"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="Inspections",
    method="post",
    path="/api/v1/inspections/demo",
    operationId="runDemoComparison",
    summary="Run the real pipeline over synthetic photographs",
    description=(
        "There are no vehicle cameras in an evaluation environment, so rather "
        "than fake the results the demo synthesises the **input** and lets the "
        "real detector run on it.\n\n"
        "Both the photographs and the response say so plainly: `synthetic: true` "
        "and a `disclaimer` string. `planted_count` and `ground_truth` are "
        "returned so a detection can be checked against what was actually "
        "planted.\n\n"
        "**Nothing is stored.** No inspection rows are written, so this can be "
        "run repeatedly without polluting real custody history."
    ),
    auth="bearer",
    roles="auth",
    request_body={
        "required": False,
        "schema": {
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "string",
                    "default": "multiple",
                    "description": "A name from `GET /api/v1/inspections/demo/scenarios`.",
                }
            },
        },
    },
    example_request={"scenario": "multiple"},
    example_from=("api_write_shapes.json", "POST /inspections/demo"),
    errors=[("401", "Unauthenticated."), ("422", "Unknown scenario name.")],
)

# ---------------------------------------------------------------------------
# GPS
# ---------------------------------------------------------------------------

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "id": UUID,
        "custody_session_id": UUID,
        "latitude": NUM,
        "longitude": NUM,
        "speed_kmph": {"type": "number", "nullable": True},
        "heading_degrees": {"type": "number", "nullable": True},
        "accuracy_metres": {"type": "number", "nullable": True},
        "recorded_at": DATETIME,
        "source": {
            "type": "string",
            "enum": ["SIMULATED", "HARDWARE", "MANUAL"],
            "description": "Per-fix provenance. A session may contain both.",
        },
        "is_simulated": BOOL,
        "device_id": {"type": "string", "nullable": True},
        "movement_status": {
            "type": "string",
            "enum": ["NORMAL", "WARNING", "SUSPICIOUS", "BREACH"],
        },
        "is_within_zone": {"type": "boolean", "nullable": True},
        "distance_from_zone_metres": {"type": "number", "nullable": True},
        "zone_id": {"allOf": [UUID], "nullable": True},
        "ml": {
            "type": "object",
            "nullable": True,
            "description": "The prototype classifier's opinion, recorded alongside the rule-based verdict.",
        },
    },
}

add(
    group="GPS",
    method="post",
    path="/api/v1/gps/events",
    operationId="ingestGpsEvents",
    summary="Record position fixes",
    description=(
        "Accepts either a single fix object or `{\"fixes\": [...]}`. Both shapes "
        "reach the same service path, so a batch is classified exactly as its "
        "members would be individually.\n\n"
        "Every fix is passed through the real geofence classifier on the way in; "
        "the response reports the verdict, the rules that fired and any alerts "
        "raised.\n\n"
        "The `gps_events` table is append-only: a recorded fix cannot be edited "
        "or deleted afterwards."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_role(TELEMETRY_ROLES)` in `services/geofence.py`.",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "properties": {
                "fixes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["custody_session_id", "latitude", "longitude"],
                        "properties": {
                            "custody_session_id": UUID,
                            "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                            "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                            "speed_kmph": NUM,
                            "heading_degrees": NUM,
                            "accuracy_metres": NUM,
                            "recorded_at": DATETIME,
                        },
                    },
                },
                "source": {
                    "type": "string",
                    "enum": ["SIMULATED", "HARDWARE", "MANUAL"],
                    "default": "MANUAL",
                },
                "device_id": {"type": "string"},
            },
        },
    },
    example_request={
        "source": "SIMULATED",
        "fixes": [
            {
                "custody_session_id": "<custody session uuid>",
                "latitude": 28.6139,
                "longitude": 77.209,
                "speed_kmph": 0.0,
            }
        ],
    },
    example_from=("api_write_shapes.json", "POST /gps/events"),
    errors=[
        ("403", "The caller is a customer."),
        ("404", "No such custody session."),
        ("422", "A coordinate is missing or out of range."),
    ],
)

add(
    group="GPS",
    method="get",
    path="/api/v1/gps/simulate/scenarios",
    operationId="listSimulatorScenarios",
    summary="The tracks the simulator can generate",
    description=(
        "The scenario identifiers accepted by `POST /api/v1/gps/simulate` "
        "(`arrival`, `jitter`, `parked`, `retrieval`, `telemetry_loss`, "
        "`unauthorised`), served by the API rather than duplicated in the client."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_shapes.json", "GET /gps/simulate/scenarios"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="GPS",
    method="post",
    path="/api/v1/gps/simulate",
    operationId="simulateTrack",
    summary="Generate a simulated track and ingest it",
    description=(
        "Generates a track and ingests it as `SIMULATED` telemetry. Every fix "
        "passes through the **real** classification path, so the verdicts this "
        "produces are computed rather than scripted.\n\n"
        "The response is labelled honestly and repeatedly: `source: "
        "\"SIMULATED\"`, `is_simulated: true`, and a `notes` array stating that "
        "the track must not be presented as hardware data. The tracking view "
        "separates `has_hardware_telemetry` from `has_simulated_telemetry` for "
        "the same reason."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_role(TELEMETRY_ROLES)` in `services/geofence.py`.",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["custody_id"],
            "properties": {
                "custody_id": {"allOf": [UUID], "description": "Also accepted as `custody_session_id`."},
                "scenario": {
                    "type": "string",
                    "enum": ["arrival", "jitter", "parked", "retrieval", "telemetry_loss", "unauthorised"],
                    "default": "parked",
                },
                "seed": {"type": "integer", "description": "Fix the seed for a reproducible track."},
                "zone_id": UUID,
            },
        },
    },
    example_request={"custody_id": "<custody session uuid>", "scenario": "parked", "seed": 20260912},
    example_from=("api_write_shapes.json", "POST /gps/simulate"),
    errors=[
        ("403", "The caller is a customer."),
        ("404", "No such custody session."),
        ("422", "Unknown scenario name. `details.allowed` lists the valid ones."),
    ],
)

add(
    group="GPS",
    method="get",
    path="/api/v1/gps/{custody_id}",
    operationId="getTracking",
    summary="The live tracking view for a custody session",
    description=(
        "Position, zone, geofence verdict, status and alerts for one session.\n\n"
        "Note the path: this is `GET /api/v1/gps/{custody_id}` — there is no "
        "`/tracking` suffix.\n\n"
        "`has_hardware_telemetry` and `has_simulated_telemetry` are reported "
        "separately so a client can never present simulated data as hardware "
        "data by accident."
    ),
    auth="bearer",
    roles="auth",
    params=[{"name": "custody_id", "in": "path", "required": True, "schema": UUID,
             "description": "The custody **session** UUID — not an inspection id or a ticket id."}],
    example_from=("api_shapes.json", "GET /gps/<custody_id>"),
    errors=[("401", "Unauthenticated."), ("404", "No such custody session, or it is not yours.")],
)

add(
    group="GPS",
    method="get",
    path="/api/v1/gps/{custody_id}/fixes",
    operationId="listFixes",
    summary="The recorded fixes for a custody session",
    description=(
        "Paginated, newest first. Each row carries its own `source` and "
        "`is_simulated`, so provenance is visible per fix rather than only in "
        "aggregate."
    ),
    auth="bearer",
    roles="auth",
    params=[
        {"name": "custody_id", "in": "path", "required": True, "schema": UUID,
             "description": "The custody **session** UUID — not an inspection id or a ticket id."},
        {
            "name": "status",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "enum": ["NORMAL", "WARNING", "SUSPICIOUS", "BREACH"]},
        },
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 1},
        },
        {
            "name": "page_size",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 100, "maximum": 200},
        },
    ],
    example_from=("api_shapes.json", "GET /gps/<custody_id>/fixes"),
    errors=[("401", "Unauthenticated."), ("404", "No such custody session, or it is not yours.")],
)


# ---------------------------------------------------------------------------
# Geofencing
# ---------------------------------------------------------------------------

ZONE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": UUID,
        "code": {"type": "string", "example": "ZONE-A"},
        "name": STRING,
        "description": {"type": "string", "nullable": True},
        "status": {"type": "string", "enum": ["ACTIVE", "INACTIVE"]},
        "capacity": INT,
        "current_occupancy": INT,
        "available_slots": INT,
        "centre": {
            "type": "object",
            "properties": {"latitude": NUM, "longitude": NUM},
        },
        "radius_metres": {
            "type": "number",
            "nullable": True,
            "description": "Set for a generated circular zone; `null` for an irregular polygon.",
        },
        "buffer_metres": {
            "type": "number",
            "example": 25.0,
            "description": "A fix within this distance of the boundary is a warning, not a breach.",
        },
        "effective_radius_metres": {"type": "number", "nullable": True},
        "geofence_polygon": {
            "type": "array",
            "description": "A closed ring of `[longitude, latitude]` pairs (GeoJSON order). This is the boundary the engine actually evaluates against.",
            "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        },
    },
}

add(
    group="Geofencing",
    method="get",
    path="/api/v1/geofences",
    operationId="listGeofences",
    summary="List parking zones and their geofences",
    description=(
        "Every zone with its boundary ring, capacity and current occupancy. "
        "`viewer_role` is echoed back so a client can see which projection of the "
        "data it was given."
    ),
    auth="bearer",
    roles="auth",
    params=[
        {
            "name": "status",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "enum": ["ACTIVE", "INACTIVE"]},
        }
    ],
    example_from=("api_shapes.json", "GET /geofences"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="Geofencing",
    method="post",
    path="/api/v1/geofences",
    operationId="createGeofence",
    summary="Create a geofenced zone",
    description=(
        "Accepts the brief's `centre + radius` description and generates a "
        "circular ring into `geofence_polygon`, so the API matches the "
        "specification while the stored shape stays a polygon — one "
        "point-in-polygon test then serves both this and an irregular real-world "
        "yard.\n\n"
        "Pass an explicit `geofence_polygon` instead for a non-circular yard. "
        "`radius` is ignored when a polygon is supplied."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_role(GEOFENCE_ROLES)` in `services/geofence.py`.",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["code", "name", "latitude", "longitude"],
            "properties": {
                "code": {"type": "string", "maxLength": 32, "description": "Upper-cased on store. Must be unique."},
                "name": {"type": "string", "maxLength": 80},
                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                "radius": {"type": "number", "description": "Metres. Generates a circular ring."},
                "capacity": {"type": "integer", "minimum": 1, "default": 1},
                "buffer_metres": {"type": "number"},
                "description": {"type": "string"},
                "geofence_polygon": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                    "description": "Optional explicit `[longitude, latitude]` ring, for an irregular yard.",
                },
            },
        },
    },
    example_request={
        "code": "ZONE-D",
        "name": "Zone D - Overflow",
        "latitude": 28.6139,
        "longitude": 77.209,
        "radius": 60,
        "capacity": 10,
    },
    example_from=("api_write_shapes.json", "POST /geofences"),
    errors=[
        ("403", "The caller is an operator or a customer."),
        ("409", "A zone with that code already exists."),
        ("422", "Missing or out-of-range field."),
    ],
)

add(
    group="Geofencing",
    method="patch",
    path="/api/v1/geofences/{zone_id}",
    operationId="updateGeofence",
    summary="Modify a zone or its geofence",
    description=(
        "Partial update. Every change is written to the audit log with its old "
        "and new values, because silently widening a geofence would hide a "
        "breach that the narrower boundary would have caught."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_role(GEOFENCE_ROLES)` in `services/geofence.py`.",
    params=[{"name": "zone_id", "in": "path", "required": True, "schema": UUID,
             "description": "The parking zone's UUID."}],
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 80},
                "description": {"type": "string"},
                "capacity": {"type": "integer", "minimum": 1},
                "buffer_metres": {"type": "number"},
                "radius": {"type": "number"},
                "status": {"type": "string", "enum": ["ACTIVE", "INACTIVE"]},
                "geofence_polygon": {"type": "array", "items": {"type": "array", "items": NUM}},
            },
        },
    },
    example_request={"name": "Zone D - Overflow (renamed)", "capacity": 12},
    example_from=("api_write_shapes.json", "PATCH /geofences/<id>"),
    errors=[
        ("403", "The caller is an operator or a customer."),
        ("404", "No such zone."),
        ("422", "Malformed field."),
    ],
)

add(
    group="Geofencing",
    method="get",
    path="/api/v1/parking-zones",
    operationId="listParkingZones",
    summary="Parking zones for the check-in picker",
    description=(
        "A lighter projection than `/geofences`, intended for the check-in zone "
        "picker. Includes `available_slots` so a full zone can be shown as "
        "unavailable before the valet tries to assign it."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_shapes.json", "GET /parking-zones"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="Geofencing",
    method="get",
    path="/api/v1/ml/model",
    operationId="getSecurityModelInfo",
    summary="Security classifier metadata",
    description=(
        "Reports what the classifier actually is: the algorithm, its version, "
        "the features it was trained on, its held-out metrics, and a disclaimer.\n\n"
        "`available: false` is not an error — it says plainly what is missing and "
        "which script to run. The model is a prototype trained on **synthetic** "
        "telemetry, and the response says so."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_shapes.json", "GET /ml/model"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="Geofencing",
    method="post",
    path="/api/v1/ml/classify",
    operationId="classifyMovement",
    summary="Score one movement sample",
    description=(
        "An inspection tool, not a telemetry endpoint — **nothing is persisted**.\n\n"
        "Every feature the model was trained on must be supplied. Omitting one is "
        "refused with `422` listing the missing features, because silently "
        "defaulting a feature to zero would produce a confident prediction from "
        "partly-guessed input.\n\n"
        "The response explains itself: `reason`, `top_factors` with each "
        "feature's contribution and direction, and `severity_basis` stating that "
        "severity is graded from P(SUSPICIOUS) rather than from measured risk."
    ),
    auth="bearer",
    roles="auth",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": [
                "speed_kmph", "distance_from_zone_metres", "movement_duration_seconds",
                "direction_change_degrees", "distance_travelled_metres", "zone_breach",
                "time_sin", "time_cos",
            ],
            "properties": {
                "speed_kmph": NUM,
                "distance_from_zone_metres": NUM,
                "movement_duration_seconds": NUM,
                "direction_change_degrees": NUM,
                "distance_travelled_metres": NUM,
                "zone_breach": {"type": "integer", "enum": [0, 1]},
                "time_sin": NUM,
                "time_cos": NUM,
            },
        },
    },
    example_request={
        "speed_kmph": 14.0,
        "distance_from_zone_metres": 320.0,
        "movement_duration_seconds": 95.0,
        "direction_change_degrees": 140.0,
        "distance_travelled_metres": 380.0,
        "zone_breach": 1,
        "time_sin": 0.87,
        "time_cos": -0.49,
    },
    example_from=("api_write_shapes.json", "POST /ml/classify"),
    errors=[
        ("422", "A trained feature is missing (`details.missing_features`), or no classifier is loaded."),
    ],
)

# ---------------------------------------------------------------------------
# Security Alerts
# ---------------------------------------------------------------------------

SECURITY_ALERT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": UUID,
        "title": STRING,
        "description": STRING,
        "alert_type": {
            "type": "string",
            "example": "GEOFENCE_BREACH",
            "description": "The geofence detector's own vocabulary, preserved as raised.",
        },
        "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        "status": {
            "type": "string",
            "enum": ["OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "DISMISSED"],
        },
        "is_open": BOOL,
        "reason": {"type": "string", "description": "The specific measurement that triggered it."},
        "confidence": NUM,
        "custody_session_id": UUID,
        "vehicle_id": {"allOf": [UUID], "nullable": True},
        "zone_id": {"allOf": [UUID], "nullable": True},
        "gps_event_id": {"allOf": [UUID], "nullable": True},
        "occurred_at": DATETIME,
        "acknowledged_at": {"allOf": [DATETIME], "nullable": True},
        "acknowledged_by_id": {"allOf": [UUID], "nullable": True},
        "acknowledged_by_email": {"type": "string", "nullable": True},
        "resolved_at": {"allOf": [DATETIME], "nullable": True},
        "resolved_by_id": {"allOf": [UUID], "nullable": True},
        "resolved_by_email": {"type": "string", "nullable": True},
        "resolution_notes": {"type": "string", "nullable": True},
        "evidence": {
            "type": "object",
            "description": "The measurements behind the verdict, so the alert can be argued with.",
        },
    },
}

add(
    group="Security Alerts",
    method="get",
    path="/api/v1/alerts",
    operationId="listSecurityAlerts",
    summary="Geofence security alerts",
    description=(
        "The alerts raised by the geofence detector, each linked to the GPS fix "
        "and the evidence that produced it.\n\n"
        "This is the evidence-linked surface. The centralized cross-source feed "
        "lives at `/api/v1/feed`; both are kept because this one carries the "
        "detector's own `alert_type` and measurement evidence."
    ),
    auth="bearer",
    roles="staff",
    role_note="Non-staff callers are refused in `list_alerts`.",
    params=[
        {
            "name": "custody_session_id",
            "in": "query",
            "required": False,
            "schema": UUID,
        },
        {
            "name": "status",
            "in": "query",
            "required": False,
            "schema": {
                "type": "string",
                "enum": ["OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "DISMISSED"],
            },
        },
        {
            "name": "severity",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        },
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 1},
        },
        {
            "name": "page_size",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 50, "maximum": 200},
        },
    ],
    example_from=("api_shapes.json", "GET /alerts"),
    errors=[("401", "Unauthenticated."), ("403", "The caller is a customer.")],
)

add(
    group="Security Alerts",
    method="post",
    path="/api/v1/alerts/{alert_id}/acknowledge",
    operationId="acknowledgeSecurityAlert",
    summary="Acknowledge a security alert",
    description=(
        "Records that a person has seen the alert, without claiming it is "
        "resolved. Moves an `OPEN` alert to `ACKNOWLEDGED` and stamps who did it."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_role(ALERT_TRIAGE_ROLES)`.",
    params=[{"name": "alert_id", "in": "path", "required": True, "schema": UUID,
             "description": "The alert's UUID."}],
    example_from=("api_write_shapes.json", "POST /alerts/<id>/acknowledge"),
    errors=[
        ("403", "The caller is an operator or a customer."),
        ("404", "No such alert."),
        ("409", "The alert is already acknowledged, resolved or dismissed."),
    ],
)

add(
    group="Security Alerts",
    method="post",
    path="/api/v1/alerts/{alert_id}/resolve",
    operationId="resolveSecurityAlert",
    summary="Resolve or dismiss a security alert",
    description=(
        "Closes the alert. `dismiss: true` records it as `DISMISSED` — judged to "
        "need no action — which is a different claim from `RESOLVED`, and the two "
        "are kept apart deliberately.\n\n"
        "Resolution is attributed: the resolving user and any notes are stored "
        "and written to the audit log."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_role(ALERT_TRIAGE_ROLES)`.",
    params=[{"name": "alert_id", "in": "path", "required": True, "schema": UUID,
             "description": "The alert's UUID."}],
    request_body={
        "required": False,
        "schema": {
            "type": "object",
            "properties": {
                "notes": {"type": "string", "maxLength": 500},
                "dismiss": {"type": "boolean", "default": False},
            },
        },
    },
    example_request={"notes": "Investigated; vehicle returned by the owner's driver."},
    example_from=("api_write_shapes.json", "POST /alerts/<id>/resolve"),
    errors=[
        ("403", "The caller is an operator or a customer."),
        ("404", "No such alert."),
        ("409", "The alert is already closed."),
    ],
)

FEED_ALERT_SCHEMA = {
    "type": "object",
    "properties": {
        "alert_id": UUID,
        "type": {
            "type": "string",
            "enum": ["GEOFENCE_BREACH", "SUSPICIOUS_MOVEMENT", "OVERSPEED", "POTENTIAL_DAMAGE", "RETRIEVAL_READY"],
            "description": "The five categories the alert centre exposes. The originating detector's own type is preserved in `source.type`.",
        },
        "message": STRING,
        "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        "status": {"type": "string", "enum": ["OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"]},
        "is_open": BOOL,
        "is_read": {
            "type": "boolean",
            "description": "Per-recipient. Read state is stored per user in `alert_reads`, so one person reading an alert does not clear it for everyone else.",
        },
        "read_at": {"allOf": [DATETIME], "nullable": True},
        "audiences": {
            "type": "array",
            "items": {"type": "string", "enum": ["CUSTOMER", "VALET_OPERATOR", "VALET_MANAGER", "ADMIN"]},
        },
        "custody_id": {"allOf": [UUID], "nullable": True},
        "vehicle_id": {"allOf": [UUID], "nullable": True},
        "vehicle": {"type": "object", "nullable": True},
        "source": {
            "type": "object",
            "properties": {"entity_type": STRING, "id": STRING, "type": STRING},
            "description": "Where the alert came from, including the originating detector's own type.",
        },
        "created_at": DATETIME,
        "resolved_at": {"allOf": [DATETIME], "nullable": True},
        "resolved_by": {"type": "string", "nullable": True},
        "resolution_notes": {"type": "string", "nullable": True},
    },
}

add(
    group="Security Alerts",
    method="get",
    path="/api/v1/feed",
    operationId="listAlertFeed",
    summary="The centralized alert feed",
    description=(
        "Every alert this actor may see, newest and worst first, across all "
        "sources.\n\n"
        "Role scoping is applied server-side to the query, so a customer cannot "
        "widen their view by omitting a parameter.\n\n"
        "`delivery: \"polled\"` is part of the response: this is a polled feed, "
        "not a push channel, and the client labels it that way rather than "
        "implying real-time delivery.\n\n"
        "`total` is in the body as well as in `meta`, because a client cannot "
        "render \"page 2 of 9\" from headers alone."
    ),
    auth="bearer",
    roles="auth",
    params=[
        {
            "name": "status",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "enum": ["OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"]},
        },
        {
            "name": "type",
            "in": "query",
            "required": False,
            "schema": {
                "type": "string",
                "enum": ["GEOFENCE_BREACH", "SUSPICIOUS_MOVEMENT", "OVERSPEED", "POTENTIAL_DAMAGE", "RETRIEVAL_READY"],
            },
        },
        {
            "name": "severity",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        },
        {"name": "vehicle_id", "in": "query", "required": False, "schema": UUID},
        {"name": "unread_only", "in": "query", "required": False, "schema": BOOL},
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 1},
        },
        {
            "name": "page_size",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 25, "maximum": 200},
        },
    ],
    example_from=("api_shapes.json", "GET /feed"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="Security Alerts",
    method="get",
    path="/api/v1/feed/summary",
    operationId="getAlertFeedSummary",
    summary="Unread and open counts for the notification bell",
    description=(
        "A compact count payload for the bell badge. `can_resolve` tells the "
        "client whether to offer the resolve action at all, so the button is not "
        "rendered for a role that would only receive a `403`."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_shapes.json", "GET /feed/summary"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="Security Alerts",
    method="post",
    path="/api/v1/feed",
    operationId="createAlertFeedItem",
    summary="Raise an alert into the feed",
    description=(
        "Staff-only. Lets a valet or manager raise something the detectors do not "
        "cover.\n\n"
        "Creating the same alert twice is idempotent where the source allows it: "
        "the response reports whether a new row was created."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `is_staff(actor)` in the route.",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["GEOFENCE_BREACH", "SUSPICIOUS_MOVEMENT", "OVERSPEED", "POTENTIAL_DAMAGE", "RETRIEVAL_READY"],
                },
                "message": {"type": "string"},
                "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"], "default": "MEDIUM"},
                "custody_id": {"allOf": [UUID], "description": "Also accepted as `custody_session_id`."},
                "vehicle_id": UUID,
                "audiences": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["CUSTOMER", "VALET_OPERATOR", "VALET_MANAGER", "ADMIN"]},
                    "description": "Role names. A bare `STAFF` is not accepted.",
                },
                "source_entity_type": {"type": "string"},
                "source_id": {"type": "string"},
                "source_type": {"type": "string"},
            },
        },
    },
    example_request={
        "type": "RETRIEVAL_READY",
        "severity": "LOW",
        "message": "Vehicle ready at the handover point.",
        "audiences": ["CUSTOMER", "VALET_MANAGER"],
    },
    example_from=("api_write_shapes2.json", "POST /feed"),
    errors=[
        ("403", "The caller is a customer."),
        ("422", "Unknown `type`, unknown audience, or a malformed body."),
    ],
)

add(
    group="Security Alerts",
    method="get",
    path="/api/v1/feed/{alert_id}",
    operationId="getAlertFeedItem",
    summary="Fetch one feed alert",
    description="The alert with this actor's own read state attached.",
    auth="bearer",
    roles="auth",
    params=[{"name": "alert_id", "in": "path", "required": True, "schema": UUID,
             "description": "The alert's UUID."}],
    example_from=("api_write_shapes2.json", "GET /feed/<id>"),
    errors=[("401", "Unauthenticated."), ("404", "No such alert, or it is not addressed to you.")],
)

add(
    group="Security Alerts",
    method="post",
    path="/api/v1/feed/{alert_id}/read",
    operationId="markAlertRead",
    summary="Mark one alert read",
    description=(
        "Read state is **per recipient**, stored in the append-only `alert_reads` "
        "table. One person reading an alert does not clear it for anyone else.\n\n"
        "`already_read` in the response distinguishes a first read from a repeat, "
        "so a client can avoid counting the same alert twice."
    ),
    auth="bearer",
    roles="auth",
    params=[{"name": "alert_id", "in": "path", "required": True, "schema": UUID,
             "description": "The alert's UUID."}],
    example_from=("api_write_shapes2.json", "POST /feed/<id>/read"),
    errors=[("401", "Unauthenticated."), ("404", "No such alert, or it is not addressed to you.")],
)

add(
    group="Security Alerts",
    method="post",
    path="/api/v1/feed/read-all",
    operationId="markAllAlertsRead",
    summary="Mark every visible alert read",
    description=(
        "Marks all alerts addressed to this actor as read, in one write. Returns "
        "how many rows were affected."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_write_shapes2.json", "POST /feed/read-all"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="Security Alerts",
    method="post",
    path="/api/v1/feed/{alert_id}/resolve",
    operationId="resolveAlertFeedItem",
    summary="Resolve or dismiss a feed alert",
    description=(
        "Closes the alert and attributes the closure. `dismiss: true` records "
        "`DISMISSED` rather than `RESOLVED` — \"we judged this needed no action\" "
        "is a different claim from \"we dealt with it\", and collapsing them "
        "would make the log useless for review.\n\n"
        "Resolving again returns `409` naming the current status."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `TRIAGE_ROLES` in `services/alerts.py`.",
    params=[{"name": "alert_id", "in": "path", "required": True, "schema": UUID,
             "description": "The alert's UUID."}],
    request_body={
        "required": False,
        "schema": {
            "type": "object",
            "properties": {
                "notes": {"type": "string", "maxLength": 500},
                "dismiss": {"type": "boolean", "default": False},
            },
        },
    },
    example_request={"notes": "Closed after checking with the customer."},
    example_from=("api_write_shapes2.json", "POST /feed/<id>/resolve"),
    errors=[
        ("403", "The caller is an operator or a customer."),
        ("404", "No such alert."),
        ("409", "The alert is already resolved or dismissed."),
    ],
)


# ---------------------------------------------------------------------------
# Retrieval Prediction
# ---------------------------------------------------------------------------

ESTIMATE_SCHEMA = {
    "type": "object",
    "properties": {
        "prediction_id": UUID,
        "custody_session_id": UUID,
        "estimated_minutes": {"type": "integer", "example": 6},
        "estimated_minutes_exact": {"type": "number", "example": 6.12},
        "min_wait_minutes": {"type": "integer", "example": 4},
        "max_wait_minutes": {"type": "integer", "example": 8},
        "estimated_retrieval_time": {"type": "string"},
        "confidence": {"type": "number", "example": 0.682},
        "queue_position": {"type": "integer", "example": 1},
        "queue_size": {"type": "integer", "example": 1},
        "recommended_dispatch": {"type": "string", "example": "Now"},
        "recommended_dispatch_time": {"type": "string"},
        "dispatch_delay_minutes": {"type": "integer"},
        "fetch_minutes": {"type": "integer"},
        "method": {"type": "string", "example": "linear-ridge-v1"},
        "model_version": {"type": "string", "example": "retrieval-baseline/1.0.0"},
        "reason": {
            "type": "string",
            "description": "Plain-language explanation of the estimate.",
        },
        "contributions": {
            "type": "object",
            "description": "Per-feature contribution in minutes, so the number can be argued with.",
        },
        "inputs": {"type": "object", "description": "The feature values the estimate was computed from."},
        "trained_on_synthetic_data": {
            "type": "boolean",
            "example": True,
            "description": "Always `true` for this MVP. The model has never seen a real yard.",
        },
        "data_basis": {"type": "string"},
        "disclaimer": {
            "type": "string",
            "description": "Travels with every estimate. It is not optional and cannot be cropped out.",
        },
        "persisted": {"type": "boolean"},
    },
}

add(
    group="Retrieval Prediction",
    method="post",
    path="/api/v1/retrieval/predict",
    operationId="predictRetrieval",
    summary="Predict how long a retrieval will take",
    description=(
        "Scores the queue length, staffing and zone occupancy for a custody "
        "session and returns an estimated wait with its reasoning.\n\n"
        "The estimate is a **range as well as a point**. A single figure implies "
        "precision the model does not have.\n\n"
        "Pass `persist: false` to score without writing a row.\n\n"
        "The model is a baseline trained on **synthetic** historical records "
        "generated for this MVP. `trained_on_synthetic_data`, `data_basis` and "
        "`disclaimer` are always present so the figure is never presented as an "
        "operational forecast."
    ),
    auth="bearer",
    roles="auth",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "properties": {
                "custody_session_id": {"allOf": [UUID], "description": "Also accepted as `custody_id`."},
                "features": {
                    "type": "object",
                    "description": "Optional explicit feature overrides. Omitted features are derived from the session.",
                },
                "persist": {"type": "boolean", "default": True},
            },
        },
    },
    example_request={"custody_session_id": "<custody session uuid>"},
    example_from=("api_write_shapes.json", "POST /retrieval/predict"),
    errors=[
        ("404", "No such custody session."),
        ("422", "Missing `custody_session_id`."),
    ],
)

add(
    group="Retrieval Prediction",
    method="post",
    path="/api/v1/retrieval/request",
    operationId="requestRetrieval",
    summary="Ask for the vehicle back",
    description=(
        "Moves the custody session to `RETRIEVAL_REQUESTED`, produces an estimate "
        "and notifies the valet staff.\n\n"
        "This is a **custody transition**, so it obeys the same transition table "
        "as `PATCH /custody-sessions/{id}`: from `IN_TRANSIT` the permitted "
        "targets no longer include `RETRIEVAL_REQUESTED`, and attempting it "
        "returns `409` with the allowed set.\n\n"
        "`previous_status` is returned so a client can undo or explain the move. "
        "`estimate_available: false` is not an error — it means no estimate could "
        "be produced, and `estimate_error` says why."
    ),
    auth="bearer",
    roles="auth",
    role_note="The customer who owns the vehicle, or staff.",
    request_body={
        "required": True,
        "schema": {
            "type": "object",
            "properties": {
                "custody_session_id": {"allOf": [UUID], "description": "Also accepted as `custody_id`."},
            },
        },
    },
    example_request={"custody_session_id": "<custody session uuid>"},
    example_from=("api_write_shapes.json", "POST /retrieval/request"),
    errors=[
        ("404", "No such custody session."),
        ("409", "The transition is not permitted from the current status. `details.allowed` lists the valid targets."),
    ],
)

add(
    group="Retrieval Prediction",
    method="get",
    path="/api/v1/retrieval/queue",
    operationId="getRetrievalQueue",
    summary="The live retrieval queue",
    description=(
        "Every session waiting for or undergoing retrieval, in dispatch order.\n\n"
        "`delivery: \"polled\"` and `delivery_note` are part of the response, so "
        "the UI can label itself as a poll rather than implying a live push."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by the `require_role(...)` decorator on the route.",
    example_from=("api_shapes.json", "GET /retrieval/queue"),
    errors=[("401", "Unauthenticated."), ("403", "The caller is a customer.")],
)

add(
    group="Retrieval Prediction",
    method="get",
    path="/api/v1/retrieval/{custody_id}",
    operationId="getRetrievalPrediction",
    summary="The stored estimate for a custody session",
    description=(
        "Returns the most recent persisted prediction for the session.\n\n"
        "A `404` here means **nobody has asked for an estimate yet**, not that "
        "something failed. Clients should render \"no estimate yet\" rather than a "
        "zero wait — an empty card reads as an immediate pickup."
    ),
    auth="bearer",
    roles="auth",
    params=[{"name": "custody_id", "in": "path", "required": True, "schema": UUID,
             "description": "The custody **session** UUID — not an inspection id or a ticket id."}],
    example_from=("api_shapes.json", "GET /retrieval/<custody_id>"),
    errors=[
        ("401", "Unauthenticated."),
        ("404", "No prediction has been recorded for this session yet."),
    ],
)

add(
    group="Retrieval Prediction",
    method="get",
    path="/api/v1/retrieval/model",
    operationId="getRetrievalModelInfo",
    summary="Retrieval model metadata",
    description=(
        "The algorithm, its coefficients, the features it uses and its "
        "held-out metrics against a naive baseline.\n\n"
        "The disclaimer states plainly that the model is trained on synthetic "
        "historical records generated for this MVP and is indicative only."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_shapes.json", "GET /retrieval/model"),
    errors=[("401", "Unauthenticated.")],
)

# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

add(
    group="Reports",
    method="get",
    path="/api/v1/customers/me/dashboard",
    operationId="getCustomerDashboard",
    summary="The signed-in customer's dashboard",
    description=(
        "A single server-composed payload: the customer's vehicle, its custody "
        "session, live location, retrieval estimate, security posture and the "
        "actions available to them right now.\n\n"
        "The path uses `/me` deliberately — there is no `customer_id` parameter "
        "to tamper with, so a customer cannot ask for somebody else's dashboard.\n\n"
        "The dashboard **writes nothing**. It reads state that the workflow "
        "endpoints already own."
    ),
    auth="bearer",
    roles="auth",
    example_from=("api_shapes.json", "GET /customers/me/dashboard"),
    errors=[("401", "Unauthenticated.")],
)

add(
    group="Reports",
    method="get",
    path="/api/v1/staff/dashboard",
    operationId="getStaffDashboard",
    summary="The valet operations dashboard",
    description=(
        "Active vehicles, incoming handovers, the retrieval queue, pending "
        "pickups and inspection state, composed server-side.\n\n"
        "Includes `capabilities` and `display_status_labels` so the client "
        "renders the vocabulary the backend actually uses rather than keeping a "
        "second copy of the mapping that can drift."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_staff` in `services/staff_dashboard.py`.",
    example_from=("api_shapes.json", "GET /staff/dashboard"),
    errors=[("401", "Unauthenticated."), ("403", "The caller is a customer.")],
)

add(
    group="Reports",
    method="get",
    path="/api/v1/staff/custody/{custody_id}/condition",
    operationId="getStaffConditionReport",
    summary="Condition report for one custody session",
    description=(
        "The same shape as `GET /api/v1/inspections/{custody_id}`, scoped to the "
        "staff surface so an operator can reach it from the custody view without "
        "a second client code path."
    ),
    auth="bearer",
    roles="staff",
    role_note="Enforced by `_require_staff` in `services/staff_dashboard.py`.",
    params=[{"name": "custody_id", "in": "path", "required": True, "schema": UUID,
             "description": "The custody **session** UUID — not an inspection id or a ticket id."}],
    example_from=("api_shapes.json", "GET /staff/custody/<id>/condition"),
    errors=[
        ("401", "Unauthenticated."),
        ("403", "The caller is a customer."),
        ("404", "No such custody session."),
    ],
)

add(
    group="Reports",
    method="get",
    path="/api/v1/manager/dashboard",
    operationId="getManagerDashboard",
    summary="The manager dashboard",
    description=(
        "Metrics, charts and tables for a date range, composed server-side.\n\n"
        "`filter_options` is part of the response, so the filter dropdowns are "
        "built from what the backend can actually serve rather than from a "
        "hard-coded list in the client. `provenance` records what the numbers "
        "were computed from."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `MANAGER_VIEW_ROLES` in `services/manager_dashboard.py`.",
    params=[
        {"name": "date_from", "in": "query", "required": False, "schema": {"type": "string", "format": "date"}},
        {"name": "date_to", "in": "query", "required": False, "schema": {"type": "string", "format": "date"}},
        {"name": "status", "in": "query", "required": False, "schema": {"type": "string"}},
        {"name": "severity", "in": "query", "required": False, "schema": {"type": "string"}},
        {"name": "vehicle_id", "in": "query", "required": False, "schema": UUID},
    ],
    example_from=("api_shapes.json", "GET /manager/dashboard"),
    errors=[
        ("401", "Unauthenticated."),
        ("403", "The caller is an operator or a customer."),
        ("422", "A malformed date."),
    ],
)

add(
    group="Reports",
    method="get",
    path="/api/v1/manager/report",
    operationId="getManagerReport",
    summary="Operational report for a date range",
    description=(
        "Metrics, time series and tables for export or review. `generated_by` and "
        "`generated_at` are recorded so a printed report can be attributed.\n\n"
        "Read-only: generating a report writes nothing."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `MANAGER_VIEW_ROLES` in `services/manager_dashboard.py`.",
    params=[
        {"name": "date_from", "in": "query", "required": False, "schema": {"type": "string", "format": "date"}},
        {"name": "date_to", "in": "query", "required": False, "schema": {"type": "string", "format": "date"}},
    ],
    example_from=("api_shapes.json", "GET /manager/report"),
    errors=[
        ("401", "Unauthenticated."),
        ("403", "The caller is an operator or a customer."),
        ("422", "A malformed date."),
    ],
)

# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------

AUDIT_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": UUID,
        "seq": {
            "type": "integer",
            "description": "A monotonically increasing sequence number. This is the only reliable ordering key; `created_at` can collide within a millisecond.",
        },
        "action": {
            "type": "string",
            "example": "VEHICLE_CHECKED_IN",
            "description": "From the fixed audit vocabulary. Retired actions are kept readable but cannot be written again.",
        },
        "label": {"type": "string", "example": "Vehicle checked in"},
        "description": {"type": "string", "nullable": True},
        "is_lifecycle_event": {"type": "boolean"},
        "outcome": {"type": "string", "enum": ["SUCCESS", "FAILURE"]},
        "failure_reason": {"type": "string", "nullable": True},
        "entity_type": {"type": "string", "example": "CUSTODY_SESSION"},
        "entity_id": {"type": "string", "nullable": True},
        "custody_session_id": {"allOf": [UUID], "nullable": True},
        "actor_id": {"allOf": [UUID], "nullable": True},
        "actor_role": {"type": "string", "nullable": True},
        "actor_email": {"type": "string", "nullable": True},
        "actor_deleted_at": {
            "allOf": [DATETIME],
            "nullable": True,
            "description": "Set when the actor's account no longer exists. The entry survives, because deleting a user must not erase what they did.",
        },
        "old_values": {"type": "object", "nullable": True},
        "new_values": {"type": "object", "nullable": True},
        "metadata": {"type": "object", "nullable": True},
        "ip_address": {"type": "string", "nullable": True},
        "user_agent": {"type": "string", "nullable": True},
        "request_id": {
            "type": "string",
            "nullable": True,
            "description": "Ties the audit entry to the API response that caused it.",
        },
        "occurred_at": DATETIME,
        "created_at": DATETIME,
    },
}

add(
    group="Audit Logs",
    method="get",
    path="/api/v1/audit",
    operationId="listAuditLog",
    summary="Query the audit trail",
    description=(
        "The append-only log of every significant action, newest first.\n\n"
        "`mutable: false` and `mutability_note` are part of the response: the "
        "table has a database-level trigger that refuses `UPDATE` and `DELETE`, "
        "so immutability is enforced by PostgreSQL rather than by convention.\n\n"
        "`vocabulary` lists the action values in use, so a client can build a "
        "filter from what actually exists."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_viewer` (`AUDIT_VIEW_ROLES`).",
    params=[
        {"name": "action", "in": "query", "required": False, "schema": {"type": "string"}},
        {"name": "entity_type", "in": "query", "required": False, "schema": {"type": "string"}},
        {"name": "entity_id", "in": "query", "required": False, "schema": {"type": "string"}},
        {"name": "actor_id", "in": "query", "required": False, "schema": UUID},
        {"name": "custody_session_id", "in": "query", "required": False, "schema": UUID},
        {"name": "outcome", "in": "query", "required": False, "schema": {"type": "string", "enum": ["SUCCESS", "FAILURE"]}},
        {"name": "date_from", "in": "query", "required": False, "schema": {"type": "string", "format": "date"}},
        {"name": "date_to", "in": "query", "required": False, "schema": {"type": "string", "format": "date"}},
        {
            "name": "lifecycle_only",
            "in": "query",
            "required": False,
            "schema": {"type": "boolean", "default": False},
            "description": "Restrict to the custody lifecycle events.",
        },
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 1},
        },
        {
            "name": "page_size",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 50, "maximum": 200},
        },
    ],
    example_from=("api_shapes.json", "GET /audit"),
    errors=[
        ("401", "Unauthenticated."),
        ("403", "The caller is an operator or a customer."),
        ("422", "A malformed date or UUID."),
    ],
)

add(
    group="Audit Logs",
    method="get",
    path="/api/v1/audit/filters",
    operationId="getAuditFilters",
    summary="The values the audit filters can take",
    description=(
        "The distinct actions, entity types, lifecycle actions and outcomes "
        "present in the log, plus human labels.\n\n"
        "Served by the API so the filter dropdowns cannot offer a value that "
        "would return nothing."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_viewer` (`AUDIT_VIEW_ROLES`).",
    example_from=("api_shapes.json", "GET /audit/filters"),
    errors=[("401", "Unauthenticated."), ("403", "The caller is an operator or a customer.")],
)

add(
    group="Audit Logs",
    method="get",
    path="/api/v1/audit/timeline/{custody_id}",
    operationId="getCustodyTimeline",
    summary="The audit timeline for one custody session",
    description=(
        "Every event for one session, ordered by `seq`.\n\n"
        "`lifecycle_progress` and `lifecycle_missing` report which of the "
        "expected lifecycle events are present and which are not, so an "
        "incomplete handover is visible rather than merely short.\n\n"
        "`had_security_incident` summarises whether any security alert touched "
        "this session."
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_viewer` (`AUDIT_VIEW_ROLES`).",
    params=[{"name": "custody_id", "in": "path", "required": True, "schema": UUID,
             "description": "The custody **session** UUID — not an inspection id or a ticket id."}],
    example_from=("api_shapes.json", "GET /audit/timeline/<custody_id>"),
    errors=[
        ("401", "Unauthenticated."),
        ("403", "The caller is an operator or a customer."),
        ("404", "No such custody session."),
    ],
)

add(
    group="Audit Logs",
    method="get",
    path="/api/v1/audit/vehicles/{vehicle_id}",
    operationId="getVehicleAuditHistory",
    summary="Every custody timeline for one vehicle",
    description=(
        "The full history of a vehicle across all its custody sessions, with a "
        "timeline per session. Used to answer \"what happened to this car every "
        "time we held it?\""
    ),
    auth="bearer",
    roles="manager",
    role_note="Enforced by `_require_viewer` (`AUDIT_VIEW_ROLES`).",
    params=[{"name": "vehicle_id", "in": "path", "required": True, "schema": UUID,
             "description": "The vehicle's UUID."}],
    example_from=("api_shapes.json", "GET /audit/vehicles/<id>"),
    errors=[
        ("401", "Unauthenticated."),
        ("403", "The caller is an operator or a customer."),
        ("404", "No such vehicle."),
    ],
)

# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

add(
    group="System",
    method="get",
    path="/api/health",
    operationId="health",
    summary="Liveness and dependency health",
    description=(
        "The contract with load balancers and uptime monitors, so it lives "
        "outside the versioned surface and must not move when the API versions.\n\n"
        "Reports the database check with its latency and server version, plus the "
        "configured `telemetry_source` for the installation."
    ),
    auth="none",
    roles="public",
    example_from=("api_shapes.json", "GET /api/health"),
    errors=[("503", "A dependency is unhealthy.")],
)

add(
    group="System",
    method="get",
    path="/api/readyz",
    operationId="readiness",
    summary="Readiness probe",
    description=(
        "Answers whether the service is ready to take traffic. Kept separate "
        "from `/api/health` so an orchestrator can distinguish \"alive\" from "
        "\"able to serve\"."
    ),
    auth="none",
    roles="public",
    example_from=("api_shapes.json", "GET /api/readyz"),
    errors=[("503", "Not ready — a dependency check failed.")],
)

add(
    group="System",
    method="get",
    path="/media/{reference}",
    operationId="serveMedia",
    summary="Serve one stored image",
    description=(
        "Serves a stored photograph, QR code or rendered comparison by the "
        "reference returned from the API.\n\n"
        "**This route is unauthenticated**, because an `<img>` tag cannot send an "
        "`Authorization` header. The reference is a server-issued opaque path "
        "validated against traversal; a missing or invalid reference answers "
        "`404` rather than `422`, since \"not here\" is the useful answer to an "
        "image fetch.\n\n"
        "The security audit records this as an accepted, documented risk, with "
        "signed URLs named as the remediation for a production deployment."
    ),
    auth="none",
    roles="public",
    params=[
        {
            "name": "reference",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "The stored path, e.g. `qr/bccfb577e36d488f8d8bf6977abb32a4.png`.",
        }
    ],
    errors=[("404", "No such stored object, or the reference is invalid.")],
)

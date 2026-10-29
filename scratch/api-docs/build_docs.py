"""Render ``openapi.yaml`` and ``API-REFERENCE.md`` from the catalogue.

Both artefacts come from one source, so they cannot disagree with each other.
``verify_spec.py`` then checks the rendered paths against the live URL map.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yaml  # noqa: E402

from catalog import ENDPOINTS, ROLE_TEXT  # noqa: E402

SHAPES_DIR = Path(__file__).parent
OUT_DIR = Path("/home/user/docs/api")

GROUP_ORDER = [
    "Authentication",
    "Users",
    "Vehicles",
    "Valet Tickets",
    "Custody",
    "Inspections",
    "GPS",
    "Geofencing",
    "Security Alerts",
    "Retrieval Prediction",
    "Reports",
    "Audit Logs",
    "System",
]

GROUP_DESCRIPTIONS = {
    "Authentication": "Sign-in, sign-out and the signed-in identity.",
    "Users": "Administrator-managed accounts and the customer directory.",
    "Vehicles": "Customer vehicle records and their custody history.",
    "Valet Tickets": "The digital ticket lifecycle: issue, check in, complete, cancel.",
    "Custody": "The custody session state machine and parking zones.",
    "Inspections": "Drop-off and pickup photo capture, comparison and verification.",
    "GPS": "Telemetry ingestion, simulation and the live tracking view.",
    "Geofencing": "Zones, boundaries and the movement classifier.",
    "Security Alerts": "Detector alerts and the centralized alert feed.",
    "Retrieval Prediction": "Retrieval-time estimates and the live queue.",
    "Reports": "Composed dashboards for customers, staff and managers.",
    "Audit Logs": "The append-only record of every significant action.",
    "System": "Health, readiness and stored media.",
}

#: Shared error responses, keyed by status.
ERROR_RESPONSES = {
    "401": (
        "Unauthorized",
        "No token, a malformed token, an expired token, or a token with a bad "
        "signature, audience or issuer.",
        {
            "code": "AUTHENTICATION_ERROR",
            "message": "Authentication is required to access this resource.",
            "details": None,
        },
    ),
    "403": (
        "Forbidden",
        "The caller is authenticated but their role may not perform this action.",
        {
            "code": "AUTHORIZATION_ERROR",
            "message": "Role 'CUSTOMER' cannot read the audit trail. Required: VALET_MANAGER, ADMIN.",
            "details": {"role": "CUSTOMER", "required": ["VALET_MANAGER", "ADMIN"]},
        },
    ),
    "404": (
        "Not Found",
        "The resource does not exist, or it exists but is not visible to the "
        "caller. The two are deliberately indistinguishable, so a response never "
        "confirms that an id exists.",
        {"code": "NOT_FOUND", "message": "No vehicle was found with that id.", "details": None},
    ),
    "409": (
        "Conflict",
        "The request conflicts with the current state — a duplicate unique value, "
        "an illegal state transition, or a repeat of an action already taken.",
        {
            "code": "CONFLICT",
            "message": "Cannot move a session from IN_TRANSIT to RETRIEVAL_REQUESTED.",
            "details": {
                "from": "IN_TRANSIT",
                "to": "RETRIEVAL_REQUESTED",
                "allowed": ["PARKED", "RETRIEVAL_IN_PROGRESS", "READY_FOR_PICKUP"],
            },
        },
    ),
    "413": (
        "Payload Too Large",
        "The upload exceeds `MAX_CONTENT_LENGTH_MB` (15 MB by default).",
        {"code": "PAYLOAD_TOO_LARGE", "message": "The request body is too large.", "details": None},
    ),
    "415": (
        "Unsupported Media Type",
        "A file is not in the allowed image extension list.",
        {
            "code": "UNSUPPORTED_MEDIA_TYPE",
            "message": "Unsupported file type '.gif'. Allowed: jpg, jpeg, png, webp.",
            "details": {"field": "images"},
        },
    ),
    "422": (
        "Unprocessable Entity",
        "The request is syntactically valid but semantically wrong — a missing "
        "required field, a malformed value, or a value outside its allowed set. "
        "`details.field` names the offending field where there is one.",
        {
            "code": "VALIDATION_ERROR",
            "message": "'plate_number' does not look like a valid Indian registration number.",
            "details": {"field": "plate_number"},
        },
    ),
    "429": (
        "Too Many Requests",
        "The login rate limiter refused the attempt after 5 consecutive failures "
        "for that email address.",
        {
            "code": "RATE_LIMITED",
            "message": "Too many failed sign-in attempts. Try again later.",
            "details": {"retry_after_seconds": 300},
        },
    ),
    "500": (
        "Internal Server Error",
        "An unexpected failure. The response still carries a `request_id` in "
        "`meta`, which is what makes the failure traceable in the server log.",
        {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred.",
            "details": None,
        },
    ),
    "503": (
        "Service Unavailable",
        "A dependency is unhealthy — for the health and readiness probes, that "
        "means a check such as the database did not pass.",
        {
            "code": "SERVICE_UNAVAILABLE",
            "message": "Service unavailable: database check failed.",
            "details": {"checks": {"database": "down"}},
        },
    ),
}


def load_shape(source: str, key: str):
    """Pull a real captured payload out of a probe dump."""
    path = SHAPES_DIR / source
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    entry = data.get(key)
    if not entry:
        return None
    body = entry.get("body")
    if not isinstance(body, dict):
        return None
    return body


def envelope(data_example):
    """Wrap a captured body in the response envelope, using the real capture."""
    if data_example and "success" in data_example:
        # Already a full envelope from the probe.
        return data_example
    return {
        "success": True,
        "data": data_example if data_example is not None else {},
        "meta": {"request_id": "2336852f3fc14b8bbeb85e12cf6c85d7", "server_time": 1789225490.85},
    }


#: Operations that create a resource and therefore answer 201 rather than 200.
CREATING_OPERATIONS = {
    "register",
    "login",
    "createUser",
    "createVehicle",
    "issueTicket",
    "uploadImages",
    "captureDropOff",
    "capturePickup",
    "ingestGpsEvents",
    "simulateTrack",
    "createGeofence",
    "createAlertFeedItem",
    "predictRetrieval",
    "requestRetrieval",
}


def success_status_for(ep: dict) -> str:
    """The success status this operation answers with.

    Shared by both renderers so the OpenAPI spec and the Markdown reference can
    never disagree about whether an endpoint returns 200 or 201.
    """
    if ep["method"] == "delete":
        return "204"
    if ep["method"] == "post" and ep["operationId"] in CREATING_OPERATIONS:
        return "201"
    return "200"


def build_openapi() -> dict:
    paths: dict = {}

    for ep in ENDPOINTS:
        item = paths.setdefault(ep["path"], {})
        operation: dict = {
            "operationId": ep["operationId"],
            "summary": ep["summary"],
            "description": ep["description"],
            "tags": [ep["group"]],
        }

        if ep["params"]:
            operation["parameters"] = ep["params"]

        security = [] if ep["auth"] == "none" else [{"bearerAuth": []}]
        operation["security"] = security

        # --- request body ---
        rb = ep.get("request_body")
        if rb:
            content_type = rb.get("content_type", "application/json")
            media: dict = {"schema": rb["schema"]}
            if ep.get("example_request") is not None:
                media["example"] = ep["example_request"]
            operation["requestBody"] = {"required": rb["required"], "content": {content_type: media}}

        # --- responses ---
        responses: dict = {}
        captured = None
        if ep.get("example_from"):
            captured = load_shape(*ep["example_from"])

        success_status = success_status_for(ep)

        if success_status == "204":
            responses["204"] = {"description": "Succeeded. No body is returned."}
        else:
            resp: dict = {"description": "Succeeded."}
            resp["content"] = {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/SuccessEnvelope"},
                    "example": envelope(captured),
                }
            }
            responses[success_status] = resp

        for code, note in ep.get("errors", []):
            title, _generic, example = ERROR_RESPONSES[code]
            responses[code] = {
                "description": f"{title}. {note}",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ErrorEnvelope"},
                        "example": {
                            "success": False,
                            "error": example,
                            "meta": {
                                "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
                                "server_time": 1789225630.95,
                            },
                        },
                    }
                },
                "x-note": note,
            }

        operation["responses"] = responses

        # Record the role requirement as an extension so tooling can surface it.
        operation["x-auth"] = "Bearer token required" if ep["auth"] == "bearer" else "None (public)"
        operation["x-roles"] = ROLE_TEXT[ep["roles"]]
        if ep.get("role_note"):
            operation["x-role-enforced-by"] = ep["role_note"]

        item[ep["method"]] = operation

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "AI-Powered Valet Transparency and Vehicle Security Platform API",
            "version": "1.0.0",
            "description": (
                "REST API for the custody window between valet drop-off and pickup.\n\n"
                "## Conventions\n\n"
                "**Envelope.** Every JSON response is wrapped:\n\n"
                "```json\n"
                "{\n"
                '  "success": true,\n'
                '  "data": { },\n'
                '  "meta": { "request_id": "…", "server_time": 1789225490.85 }\n'
                "}\n"
                "```\n\n"
                "On failure `data` is replaced by `error: {code, message, details}`. "
                "`meta.request_id` is also returned as the `X-Request-Id` header — quote "
                "it when reporting a problem.\n\n"
                "**Authentication.** `POST /api/v1/auth/login` returns a JWT access "
                "token. Send it as `Authorization: Bearer <token>`. Tokens are HS256 and "
                "carry `sub`, `role`, `typ`, `jti`, `iss`, `aud`, `iat`, `nbf` and `exp`; "
                "`alg: none` is rejected.\n\n"
                "**Roles.** `CUSTOMER`, `VALET_OPERATOR`, `VALET_MANAGER`, `ADMIN`. "
                "Inheritance runs `ADMIN → VALET_MANAGER → VALET_OPERATOR`; a `CUSTOMER` "
                "is not part of that chain and holds capabilities staff do not (such as "
                "registering their own vehicle).\n\n"
                "**Versioning and aliases.** `/api/v1/...` is canonical. Several modules "
                "are additionally mounted unversioned at `/api/...` — `auth`, "
                "`inspections`, `gps`, `geofences`, `alerts`, `ml`, `retrieval` — because "
                "their contracts were originally specified that way. The handlers are "
                "identical; only the prefix differs. `/api/health` and `/media/...` are "
                "unversioned by design.\n\n"
                "**Honesty.** Simulated telemetry is labelled as such on every fix "
                "(`source`, `is_simulated`) and in aggregate "
                "(`has_simulated_telemetry`). ML outputs carry a disclaimer and "
                "`trained_on_synthetic_data`. Unverified damage findings are labelled "
                "*Potential Damage*, never confirmed damage."
            ),
            "contact": {"name": "Project documentation", "url": "https://localhost:5000/api/health"},
            "license": {"name": "Academic use"},
        },
        "servers": [
            {"url": "http://localhost:5000", "description": "Local development"},
            {"url": "http://127.0.0.1:5000", "description": "Local development (loopback)"},
        ],
        "tags": [
            {"name": g, "description": GROUP_DESCRIPTIONS[g]} for g in GROUP_ORDER
        ],
        "security": [{"bearerAuth": []}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "Access token from `POST /api/v1/auth/login`.",
                }
            },
            "schemas": {
                "Meta": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string"},
                        "server_time": {"type": "number"},
                    },
                },
                "SuccessEnvelope": {
                    "type": "object",
                    "required": ["success", "data", "meta"],
                    "properties": {
                        "success": {"type": "boolean", "example": True},
                        "data": {"type": "object"},
                        "meta": {"$ref": "#/components/schemas/Meta"},
                    },
                },
                "Error": {
                    "type": "object",
                    "required": ["code", "message"],
                    "properties": {
                        "code": {"type": "string", "example": "VALIDATION_ERROR"},
                        "message": {"type": "string"},
                        "details": {
                            "type": "object",
                            "nullable": True,
                            "description": "Machine-readable context. Often carries `field`.",
                        },
                    },
                },
                "ErrorEnvelope": {
                    "type": "object",
                    "required": ["success", "error", "meta"],
                    "properties": {
                        "success": {"type": "boolean", "example": False},
                        "error": {"$ref": "#/components/schemas/Error"},
                        "meta": {"$ref": "#/components/schemas/Meta"},
                    },
                },
            },
        },
    }


def slugify(text: str) -> str:
    return (
        text.lower()
        .replace("/", "-")
        .replace("{", "")
        .replace("}", "")
        .replace(" ", "-")
    )


def build_markdown(spec: dict) -> str:
    out: list[str] = []
    out.append("# API Reference")
    out.append("")
    out.append(
        "Every endpoint implemented by the platform, grouped by domain. This page "
        "is generated from the same catalogue as "
        "[`openapi.yaml`](openapi.yaml), and both were checked against the live "
        "application — see [`verify_spec.py`](../../scratch/api-docs/verify_spec.py)."
    )
    out.append("")
    out.append("**Nothing here is aspirational.** If a route is not in `app.url_map` it is not documented, and no route in the map is missing.")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## Conventions")
    out.append("")
    out.append("### Base URL")
    out.append("")
    out.append("```")
    out.append("http://localhost:5000")
    out.append("```")
    out.append("")
    out.append("`/api/v1/...` is the canonical versioned surface.")
    out.append("")
    out.append("### Response envelope")
    out.append("")
    out.append("Every JSON response is wrapped in the same envelope:")
    out.append("")
    out.append("```json")
    out.append("{")
    out.append('  "success": true,')
    out.append('  "data": { "…": "…" },')
    out.append('  "meta": { "request_id": "2336852f…", "server_time": 1789225490.85 }')
    out.append("}")
    out.append("```")
    out.append("")
    out.append("On failure, `data` is replaced by `error`:")
    out.append("")
    out.append("```json")
    out.append("{")
    out.append('  "success": false,')
    out.append('  "error": {')
    out.append('    "code": "VALIDATION_ERROR",')
    out.append('    "message": "\'plate_number\' does not look like a valid Indian registration number.",')
    out.append('    "details": { "field": "plate_number" }')
    out.append("  },")
    out.append('  "meta": { "request_id": "9cbb5045…", "server_time": 1789225630.95 }')
    out.append("}")
    out.append("```")
    out.append("")
    out.append(
        "`meta.request_id` is also returned as the `X-Request-Id` response header, "
        "and is written to the audit log. Quote it when reporting a problem — it "
        "is what ties a user's complaint to a line in the server log."
    )
    out.append("")
    out.append("### Authentication")
    out.append("")
    out.append("Sign in and send the access token on every subsequent request:")
    out.append("")
    out.append("```bash")
    out.append("curl -X POST http://localhost:5000/api/v1/auth/login \\")
    out.append("  -H 'Content-Type: application/json' \\")
    out.append("  -d '{\"email\":\"manager.karan@example.com\",\"password\":\"Demo#Passw0rd\"}'")
    out.append("")
    out.append("curl http://localhost:5000/api/v1/auth/me \\")
    out.append("  -H 'Authorization: Bearer <access_token>'")
    out.append("```")
    out.append("")
    out.append(
        "Tokens are HS256 JWTs carrying `sub`, `role`, `typ`, `jti`, `iss`, `aud`, "
        "`iat`, `nbf` and `exp`. A wrong audience, a wrong issuer, an expired "
        "token, a tampered signature, an unknown role, `alg: none`, or a refresh "
        "token presented as an access token are all rejected."
    )
    out.append("")
    out.append("### Roles")
    out.append("")
    out.append("| Role | Scope |")
    out.append("|---|---|")
    out.append("| `CUSTOMER` | Their own vehicles, tickets, custody and alerts. |")
    out.append("| `VALET_OPERATOR` | Check-in, custody transitions, telemetry, photo capture. |")
    out.append("| `VALET_MANAGER` | Everything an operator can do, plus alert triage, geofence edits, damage verification, reports and the audit trail. |")
    out.append("| `ADMIN` | Everything, plus creating staff accounts. |")
    out.append("")
    out.append(
        "Inheritance runs `ADMIN → VALET_MANAGER → VALET_OPERATOR`. A `CUSTOMER` is "
        "**not** part of that chain: customers hold capabilities staff do not (such "
        "as registering a vehicle to themselves), so the capability sets do not "
        "nest. Adding a role grants nothing unless it is listed."
    )
    out.append("")
    out.append("### Pagination")
    out.append("")
    out.append(
        "List endpoints accept `?page=` (1-based) and `?page_size=` (capped at "
        "200). Paginated responses echo `page`, `page_size` and `total` in `meta`."
    )
    out.append("")
    out.append("### Unversioned aliases")
    out.append("")
    out.append(
        "Several modules are mounted twice. The handlers are identical; only the "
        "prefix differs. The unversioned paths exist because those contracts were "
        "originally specified without a version, and keeping them means an older "
        "client keeps working."
    )
    out.append("")
    out.append("| Canonical | Alias |")
    out.append("|---|---|")
    out.append("| `/api/v1/auth/...` | `/api/auth/...` |")
    out.append("| `/api/v1/inspections/...` | `/api/inspections/...` |")
    out.append("| `/api/v1/gps/...` | `/api/gps/...` |")
    out.append("| `/api/v1/geofences/...` | `/api/geofences/...` |")
    out.append("| `/api/v1/alerts/...` | `/api/alerts/...` |")
    out.append("| `/api/v1/ml/...` | `/api/ml/...` |")
    out.append("| `/api/v1/retrieval/...` | `/api/retrieval/...` |")
    out.append("")
    out.append("`/api/health`, `/api/readyz` and `/media/...` are unversioned by design and have no `/api/v1` counterpart.")
    out.append("")
    out.append("### Error codes")
    out.append("")
    out.append("| Status | `error.code` | Meaning |")
    out.append("|---|---|---|")
    for code in sorted(ERROR_RESPONSES, key=int):
        title, generic, example = ERROR_RESPONSES[code]
        out.append(f"| `{code}` | `{example['code']}` | {title} — {generic.split('.')[0]}. |")
    out.append("")
    out.append("---")
    out.append("")

    # --- table of contents ---
    out.append("## Contents")
    out.append("")
    for group in GROUP_ORDER:
        out.append(f"- [{group}](#{slugify(group)})")
    out.append("")
    out.append("---")
    out.append("")

    # --- endpoint sections ---
    for group in GROUP_ORDER:
        out.append(f"## {group}")
        out.append("")
        out.append(GROUP_DESCRIPTIONS[group])
        out.append("")

        for ep in [e for e in ENDPOINTS if e["group"] == group]:
            method = ep["method"].upper()
            out.append(f"### `{method} {ep['path']}`")
            out.append("")
            out.append(f"**{ep['summary']}**")
            out.append("")
            out.append(ep["description"])
            out.append("")

            out.append("| | |")
            out.append("|---|---|")
            out.append(f"| **Authentication** | {ROLE_TEXT[ep['roles']] if ep['auth'] == 'bearer' else 'None — public route.'} |")
            if ep["auth"] == "bearer":
                out.append("| **Token** | `Authorization: Bearer <access_token>` |")
            if ep.get("role_note"):
                out.append(f"| **Enforced by** | {ep['role_note']} |")
            out.append("")

            # parameters
            params = ep.get("params") or []
            path_params = [p for p in params if p["in"] == "path"]
            query_params = [p for p in params if p["in"] == "query"]

            if path_params:
                out.append("**Path parameters**")
                out.append("")
                out.append("| Name | Type | Description |")
                out.append("|---|---|---|")
                for p in path_params:
                    typ = p["schema"].get("format") or p["schema"].get("type", "string")
                    out.append(f"| `{p['name']}` | `{typ}` | {p.get('description', '—')} |")
                out.append("")

            if query_params:
                out.append("**Query parameters**")
                out.append("")
                out.append("| Name | Type | Default | Description |")
                out.append("|---|---|---|---|")
                for p in query_params:
                    sch = p["schema"]
                    typ = sch.get("format") or sch.get("type", "string")
                    if "enum" in sch:
                        typ = ", ".join(f"`{v}`" for v in sch["enum"])
                    default = sch.get("default", "—")
                    desc = p.get("description", "")
                    if "maximum" in sch:
                        desc = f"{desc} Max {sch['maximum']}.".strip()
                    out.append(f"| `{p['name']}` | {typ} | `{default}` | {desc or '—'} |")
                out.append("")

            # request body
            rb = ep.get("request_body")
            if rb:
                ctype = rb.get("content_type", "application/json")
                out.append(f"**Request body** (`{ctype}`)")
                out.append("")
                props = rb["schema"].get("properties", {})
                required = set(rb["schema"].get("required", []))
                if props:
                    out.append("| Field | Type | Required | Notes |")
                    out.append("|---|---|---|---|")
                    for name, sch in props.items():
                        inner = sch.get("allOf", [sch])[0] if "allOf" in sch else sch
                        typ = inner.get("format") or inner.get("type", "object")
                        if "enum" in inner:
                            typ = "enum"
                        note = inner.get("description", "") or sch.get("description", "")
                        if "enum" in inner:
                            note = (note + " " if note else "") + "One of: " + ", ".join(
                                f"`{v}`" for v in inner["enum"]
                            )
                        for k in ("maxLength", "minLength", "minimum", "maximum"):
                            if k in inner:
                                note = f"{note} ({k} {inner[k]})".strip()
                        req = "**yes**" if name in required else "no"
                        out.append(f"| `{name}` | `{typ}` | {req} | {note or '—'} |")
                    out.append("")

                if ep.get("example_request") is not None:
                    out.append("*Example request*")
                    out.append("")
                    out.append("```bash")
                    out.append(f"curl -X {method} http://localhost:5000{ep['path']} \\")
                    out.append("  -H 'Authorization: Bearer <access_token>' \\")
                    if ctype == "application/json":
                        out.append("  -H 'Content-Type: application/json' \\")
                        out.append(
                            "  -d '"
                            + json.dumps(ep["example_request"])
                            + "'"
                        )
                    else:
                        out.append("  -F 'custody_id=<uuid>' \\")
                        out.append("  -F 'images=@front.png' \\")
                        out.append("  -F 'angles=FRONT'")
                    out.append("```")
                    out.append("")
            else:
                out.append("*No request body.*")
                out.append("")

            # responses
            captured = None
            if ep.get("example_from"):
                captured = load_shape(*ep["example_from"])

            out.append("**Responses**")
            out.append("")
            success_status = success_status_for(ep)

            rows = [f"| `{success_status}` | Success. |"]
            for code, note in ep.get("errors", []):
                rows.append(f"| `{code}` | {note} |")
            out.append("| Status | Meaning |")
            out.append("|---|---|")
            out.extend(rows)
            out.append("")

            if success_status != "204" and captured:
                out.append(f"*Example `{success_status}` response* (captured from the running server)")
                out.append("")
                out.append("```json")
                out.append(json.dumps(envelope(captured), indent=2, default=str)[:4000])
                out.append("```")
                out.append("")
            elif success_status == "204":
                out.append("*`204` returns no body.*")
                out.append("")

            if ep.get("errors"):
                first_code = ep["errors"][0][0]
                _, _, example = ERROR_RESPONSES[first_code]
                out.append(f"*Example `{first_code}` response*")
                out.append("")
                out.append("```json")
                out.append(
                    json.dumps(
                        {
                            "success": False,
                            "error": example,
                            "meta": {
                                "request_id": "9cbb50455cdc4fabb7832bd0c04f5fd3",
                                "server_time": 1789225630.95,
                            },
                        },
                        indent=2,
                    )
                )
                out.append("```")
                out.append("")

            out.append("---")
            out.append("")

    return "\n".join(out)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    spec = build_openapi()

    yaml_path = OUT_DIR / "openapi.yaml"
    yaml_path.write_text(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )

    md_path = OUT_DIR / "API-REFERENCE.md"
    md_path.write_text(build_markdown(spec), encoding="utf-8")

    n_ops = sum(len(v) for v in spec["paths"].values())
    print(f"wrote {yaml_path} ({len(spec['paths'])} paths, {n_ops} operations)")
    print(f"wrote {md_path}")


if __name__ == "__main__":
    main()

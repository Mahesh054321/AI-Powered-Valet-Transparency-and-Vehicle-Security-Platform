"""Capture live request/response shapes for the API documentation.

Talks to the running API on :5000 and writes a JSON report of what each
endpoint actually accepts and returns. The documentation is then written from
this, so it cannot drift from the implementation.
"""

import json
import os
import sys
from pathlib import Path

import requests

BASE = "http://127.0.0.1:5000"
DEMO_PASSWORD = "Demo#Passw0rd"

ACCOUNTS = {
    "customer": "customer.aisha@example.com",
    "operator": "valet.suresh@example.com",
    "manager": "manager.karan@example.com",
    "admin": os.environ["SEED_ADMIN_EMAIL"],
}


def login(email, password):
    r = requests.post(
        f"{BASE}/api/v1/auth/login", json={"email": email, "password": password}, timeout=30
    )
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def main():
    tokens = {}
    tokens["admin"] = login(ACCOUNTS["admin"], os.environ["SEED_ADMIN_PASSWORD"])
    for role, email in ACCOUNTS.items():
        if role == "admin":
            continue
        tokens[role] = login(email, DEMO_PASSWORD)

    def H(role):
        return {"Authorization": f"Bearer {tokens[role]}"}

    out = {}

    def probe(name, method, path, role="admin", **kwargs):
        url = path if path.startswith("http") else BASE + path
        try:
            r = requests.request(method, url, headers=H(role), timeout=60, **kwargs)
        except Exception as exc:  # noqa: BLE001
            out[name] = {"error": str(exc)}
            return None
        try:
            body = r.json()
        except ValueError:
            body = r.text[:300]
        out[name] = {
            "method": method,
            "path": path,
            "role": role,
            "status": r.status_code,
            "body": body,
        }
        return body

    # ---- discovery: we need real ids to probe the parameterised routes ----
    vehicles = probe("GET /vehicles", "GET", "/api/v1/vehicles?limit=3")
    tickets = probe("GET /tickets", "GET", "/api/v1/tickets?limit=3")
    probe("GET /parking-zones", "GET", "/api/v1/parking-zones")
    geofences = probe("GET /geofences", "GET", "/api/v1/geofences")
    feed = probe("GET /feed", "GET", "/api/v1/feed?limit=3")
    alerts = probe("GET /alerts", "GET", "/api/v1/alerts?limit=3")
    probe("GET /audit", "GET", "/api/v1/audit?limit=3")
    probe("GET /audit/filters", "GET", "/api/v1/audit/filters")
    probe("GET /customers", "GET", "/api/v1/customers?limit=3")
    probe("GET /retrieval/queue", "GET", "/api/v1/retrieval/queue")
    probe("GET /gps/simulate/scenarios", "GET", "/api/v1/gps/simulate/scenarios")
    probe(
        "GET /inspections/demo/scenarios", "GET", "/api/v1/inspections/demo/scenarios"
    )
    probe("GET /ml/model", "GET", "/api/v1/ml/model")
    probe("GET /retrieval/model", "GET", "/api/v1/retrieval/model")
    probe("GET /feed/summary", "GET", "/api/v1/feed/summary")
    probe("GET /staff/dashboard", "GET", "/api/v1/staff/dashboard")
    probe(
        "GET /customers/me/dashboard", "GET", "/api/v1/customers/me/dashboard", role="customer"
    )
    probe("GET /manager/dashboard", "GET", "/api/v1/manager/dashboard")
    probe("GET /manager/report", "GET", "/api/v1/manager/report")
    probe("GET /auth/me", "GET", "/api/v1/auth/me")
    probe("GET /api/health", "GET", "/api/health")
    probe("GET /api/readyz", "GET", "/api/readyz")

    # Vehicles and tickets are paginated under `items`; the alert feeds use `alerts`.
    vehicle_id = None
    if vehicles and vehicles.get("data", {}).get("items"):
        vehicle_id = vehicles["data"]["items"][0]["id"]
    ticket_id = None
    if tickets and tickets.get("data", {}).get("items"):
        # Prefer a ticket that actually has a custody session, so the
        # parameterised GPS/inspection/retrieval routes have something to show.
        with_session = [
            t for t in tickets["data"]["items"] if t.get("custody_session")
        ]
        ticket_id = (with_session or tickets["data"]["items"])[0]["id"]
    zone_id = None
    if geofences and geofences.get("data", {}).get("zones"):
        zone_id = geofences["data"]["zones"][0]["id"]
    alert_id = None
    if feed and feed.get("data", {}).get("alerts"):
        alert_id = feed["data"]["alerts"][0]["alert_id"]
    geo_alert_id = None
    if alerts and alerts.get("data", {}).get("alerts"):
        geo_alert_id = alerts["data"]["alerts"][0]["id"]

    print(f"ids: vehicle={vehicle_id} ticket={ticket_id} zone={zone_id} "
          f"alert={alert_id} geo_alert={geo_alert_id}", file=sys.stderr)

    if vehicle_id:
        probe("GET /vehicles/<id>", "GET", f"/api/v1/vehicles/{vehicle_id}")
        probe(
            "GET /vehicles/<id>/sessions", "GET", f"/api/v1/vehicles/{vehicle_id}/sessions"
        )
    if ticket_id:
        probe("GET /tickets/<id>", "GET", f"/api/v1/tickets/{ticket_id}")
        # custody session id comes from the ticket
        t = out["GET /tickets/<id>"]["body"]["data"]["ticket"]
        sess = (t.get("custody_session") or {}).get("id")
        print(f"session={sess}", file=sys.stderr)
        if sess:
            probe("GET /custody-sessions/<id>", "GET", f"/api/v1/custody-sessions/{sess}")
            probe("GET /gps/<custody_id>", "GET", f"/api/v1/gps/{sess}")
            probe("GET /gps/<custody_id>/fixes", "GET", f"/api/v1/gps/{sess}/fixes?limit=3")
            probe("GET /inspections/<custody_id>", "GET", f"/api/v1/inspections/{sess}")
            probe("GET /retrieval/<custody_id>", "GET", f"/api/v1/retrieval/{sess}")
            probe(
                "GET /audit/timeline/<custody_id>", "GET", f"/api/v1/audit/timeline/{sess}"
            )
            probe(
                "GET /staff/custody/<id>/condition",
                "GET",
                f"/api/v1/staff/custody/{sess}/condition",
            )
    if vehicle_id:
        probe(
            "GET /audit/vehicles/<id>", "GET", f"/api/v1/audit/vehicles/{vehicle_id}"
        )
    if alert_id:
        probe("GET /feed/<id>", "GET", f"/api/v1/feed/{alert_id}")
    if zone_id:
        pass

    # ---- error shapes: deliberately bad input ----
    probe(
        "ERR unauthenticated",
        "GET",
        "/api/v1/vehicles",
        role="admin",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    out["ERR unauthenticated"]["headers"] = {"Authorization": "Bearer <bad>"}
    probe(
        "ERR forbidden (customer -> audit)",
        "GET",
        "/api/v1/audit",
        role="customer",
    )
    probe(
        "ERR validation (bad plate)",
        "POST",
        "/api/v1/vehicles",
        role="customer",
        json={"plate_number": "XX", "make": "T"},
    )
    probe(
        "ERR not found",
        "GET",
        "/api/v1/vehicles/00000000-0000-0000-0000-000000000000",
    )
    probe(
        "ERR register staff role",
        "POST",
        "/api/v1/auth/register",
        json={
            "email": "nope@example.com",
            "password": "Str0ng#Passw0rd",
            "full_name": "Nope",
            "role": "ADMIN",
        },
    )

    # Written beside the module, into the folder build_docs.py reads from. An
    # earlier version wrote to /tmp and had to be copied across by hand, which
    # is how the generated examples went silently stale.
    dest = Path(__file__).resolve().parent / "shapes" / "api_shapes.json"
    dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"wrote {len(out)} probes to {dest}", file=sys.stderr)


if __name__ == "__main__":
    main()

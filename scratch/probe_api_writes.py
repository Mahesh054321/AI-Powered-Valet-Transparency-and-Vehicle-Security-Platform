"""Capture live request/response shapes for the write endpoints.

Creates a throwaway customer + vehicle + ticket via the API and walks it through
the custody lifecycle, so every POST/PATCH/DELETE is exercised for real and the
documentation is written from observed behaviour rather than from the source.
"""

import io
import json
import os
import sys
import uuid

import requests
from PIL import Image

BASE = "http://127.0.0.1:5000"
DEMO_PASSWORD = "Demo#Passw0rd"

ACCOUNTS = {
    "customer": "customer.aisha@example.com",
    "operator": "valet.suresh@example.com",
    "manager": "manager.karan@example.com",
    "admin": os.environ["SEED_ADMIN_EMAIL"],
}

out = {}


def login(email, password):
    r = requests.post(
        f"{BASE}/api/v1/auth/login", json={"email": email, "password": password}, timeout=30
    )
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def png_bytes(size=120):
    """A real PNG above both pixel floors (upload 64, assessment 96)."""
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (40, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


def record(name, r, extra=None):
    try:
        body = r.json()
    except ValueError:
        body = r.text[:400]
    out[name] = {"status": r.status_code, "body": body}
    if extra:
        out[name]["extra"] = extra
    print(f"  {r.status_code}  {name}", file=sys.stderr)
    return body


def main():
    tok = {
        "admin": login(ACCOUNTS["admin"], os.environ["SEED_ADMIN_PASSWORD"]),
    }
    for role, email in ACCOUNTS.items():
        if role != "admin":
            tok[role] = login(email, DEMO_PASSWORD)

    def hdr(role, extra=None):
        h = {"Authorization": f"Bearer {tok[role]}"}
        if extra:
            h.update(extra)
        return h

    # ---- register a throwaway customer ----
    email = f"docprobe.{uuid.uuid4().hex[:8]}@example.com"
    record(
        "POST /auth/register",
        requests.post(
            f"{BASE}/api/v1/auth/register",
            json={
                "email": email,
                "password": "Doc#Probe123",
                "full_name": "Doc Probe",
                "phone": "9876543210",
            },
            timeout=30,
        ),
    )
    cust_token = login(email, "Doc#Probe123")

    # ---- admin creates a staff user ----
    staff_email = f"docstaff.{uuid.uuid4().hex[:8]}@example.com"
    record(
        "POST /auth/users",
        requests.post(
            f"{BASE}/api/v1/auth/users",
            headers=hdr("admin"),
            json={
                "email": staff_email,
                "password": "Doc#Staff123",
                "full_name": "Doc Staff",
                "role": "VALET_OPERATOR",
            },
            timeout=30,
        ),
    )

    # ---- vehicle CRUD ----
    plate = f"UP9{uuid.uuid4().int % 10}AB{1000 + uuid.uuid4().int % 8999}"
    create = record(
        "POST /vehicles",
        requests.post(
            f"{BASE}/api/v1/vehicles",
            headers={"Authorization": f"Bearer {cust_token}"},
            json={
                "plate_number": plate,
                "make": "Tata",
                "model": "Nexon",
                "year": 2022,
                "color": "Silver",
                "notes": "Documentation probe vehicle.",
            },
            timeout=30,
        ),
    )
    vehicle_id = create["data"]["vehicle"]["id"]

    record(
        "PATCH /vehicles/<id>",
        requests.patch(
            f"{BASE}/api/v1/vehicles/{vehicle_id}",
            headers={"Authorization": f"Bearer {cust_token}"},
            json={"color": "Graphite", "notes": "Colour corrected."},
            timeout=30,
        ),
    )

    # ---- zone to check in against ----
    zones = requests.get(
        f"{BASE}/api/v1/parking-zones", headers=hdr("operator"), timeout=30
    ).json()["data"]["items"]
    zone_id = zones[0]["id"]

    # ---- issue a ticket (staff) ----
    issue = record(
        "POST /tickets",
        requests.post(
            f"{BASE}/api/v1/tickets",
            headers=hdr("operator"),
            json={
                "vehicle_id": vehicle_id,
                "expected_return_minutes": 120,
                "notes": "Documentation probe ticket.",
            },
            timeout=30,
        ),
    )
    ticket_id = issue["data"]["ticket"]["id"]

    # ---- check in ----
    checkin = record(
        "POST /tickets/<id>/check-in",
        requests.post(
            f"{BASE}/api/v1/tickets/{ticket_id}/check-in",
            headers=hdr("operator"),
            json={
                "zone_id": zone_id,
                "odometer": 41200,
                "fuel_level": "FULL",
                "notes": "Keys and RC received.",
            },
            timeout=30,
        ),
    )
    session_id = checkin["data"]["ticket"]["custody_session"]["id"]
    print(f"  session={session_id}", file=sys.stderr)

    # ---- retrieval request (while the session is still PARKED) ----
    # Requesting retrieval is a custody transition, so it must happen before the
    # session is moved to IN_TRANSIT: from IN_TRANSIT the allowed targets no
    # longer include RETRIEVAL_REQUESTED.
    record(
        "POST /retrieval/request",
        requests.post(
            f"{BASE}/api/v1/retrieval/request",
            headers={"Authorization": f"Bearer {cust_token}"},
            json={"custody_session_id": session_id},
            timeout=60,
        ),
    )

    # ---- custody transition ----
    record(
        "PATCH /custody-sessions/<id>",
        requests.patch(
            f"{BASE}/api/v1/custody-sessions/{session_id}",
            headers=hdr("operator"),
            json={"status": "RETRIEVAL_IN_PROGRESS", "notes": "Valet fetching the car."},
            timeout=30,
        ),
    )
    # illegal transition -> 409
    record(
        "ERR illegal custody transition",
        requests.patch(
            f"{BASE}/api/v1/custody-sessions/{session_id}",
            headers=hdr("operator"),
            json={"status": "CHECKED_OUT"},
            timeout=30,
        ),
    )

    # ---- image upload ----
    record(
        "POST /uploads/images",
        requests.post(
            f"{BASE}/api/v1/uploads/images",
            headers=hdr("operator"),
            files={"images": ("front.png", png_bytes(), "image/png")},
            data={"context": "inspection"},
            timeout=60,
        ),
    )

    # ---- inspections: drop-off then pickup (triggers comparison) ----
    record(
        "POST /inspections/dropoff",
        requests.post(
            f"{BASE}/api/v1/inspections/dropoff",
            headers=hdr("operator"),
            files=[
                ("images", ("front.png", png_bytes(140), "image/png")),
                ("images", ("rear.png", png_bytes(140), "image/png")),
            ],
            data={"custody_id": session_id, "angles": ["FRONT", "REAR"], "notes": "Clean."},
            timeout=120,
        ),
    )
    pickup = record(
        "POST /inspections/pickup",
        requests.post(
            f"{BASE}/api/v1/inspections/pickup",
            headers=hdr("operator"),
            files=[
                ("images", ("front.png", png_bytes(140), "image/png")),
                ("images", ("rear.png", png_bytes(140), "image/png")),
            ],
            data={"custody_id": session_id, "angles": ["FRONT", "REAR"]},
            timeout=120,
        ),
    )

    detection_id = None
    for d in pickup.get("data", {}).get("detections", []) or []:
        detection_id = d.get("id")
        if detection_id:
            break
    if detection_id:
        record(
            "PATCH /inspections/detections/<id>/verification",
            requests.patch(
                f"{BASE}/api/v1/inspections/detections/{detection_id}/verification",
                headers=hdr("manager"),
                json={"status": "CONFIRMED", "notes": "Confirmed on review."},
                timeout=30,
            ),
        )
        record(
            "ERR verification by operator (403)",
            requests.patch(
                f"{BASE}/api/v1/inspections/detections/{detection_id}/verification",
                headers=hdr("operator"),
                json={"status": "REJECTED"},
                timeout=30,
            ),
        )

    # ---- demo inspection ----
    record(
        "POST /inspections/demo",
        requests.post(
            f"{BASE}/api/v1/inspections/demo",
            headers=hdr("operator"),
            json={"scenario": "multiple"},
            timeout=120,
        ),
    )

    # ---- GPS ----
    record(
        "POST /gps/events",
        requests.post(
            f"{BASE}/api/v1/gps/events",
            headers=hdr("operator"),
            json={
                "source": "SIMULATED",
                "device_id": "doc-probe-device",
                "fixes": [
                    {
                        "custody_session_id": session_id,
                        "latitude": 28.6139,
                        "longitude": 77.2090,
                        "speed_kmph": 0.0,
                        "heading_degrees": 90.0,
                        "recorded_at": "2026-09-12T10:00:00Z",
                    }
                ],
            },
            timeout=60,
        ),
    )
    record(
        "POST /gps/simulate",
        requests.post(
            f"{BASE}/api/v1/gps/simulate",
            headers=hdr("operator"),
            json={"custody_id": session_id, "scenario": "parked", "seed": 20260912},
            timeout=120,
        ),
    )

    # ---- ML classify ----
    record(
        "POST /ml/classify",
        requests.post(
            f"{BASE}/api/v1/ml/classify",
            headers=hdr("operator"),
            json={
                "speed_kmph": 14.0,
                "distance_from_zone_metres": 320.0,
                "movement_duration_seconds": 95.0,
                "direction_change_degrees": 140.0,
                "distance_travelled_metres": 380.0,
                "zone_breach": 1,
                "time_sin": 0.87,
                "time_cos": -0.49,
            },
            timeout=60,
        ),
    )

    # ---- geofence create + update (manager) ----
    geo = record(
        "POST /geofences",
        requests.post(
            f"{BASE}/api/v1/geofences",
            headers=hdr("manager"),
            json={
                "code": f"DOC{uuid.uuid4().hex[:5].upper()}",
                "name": "Documentation probe zone",
                "latitude": 28.6139,
                "longitude": 77.2090,
                "radius": 60,
                "capacity": 10,
            },
            timeout=30,
        ),
    )
    # POST /geofences returns the zone flat under `data`, not nested under a
    # `zone` key -- reading data.zone.id silently yielded None and skipped the
    # PATCH probe entirely.
    new_zone = geo.get("data", {}).get("id")
    if new_zone:
        record(
            "PATCH /geofences/<id>",
            requests.patch(
                f"{BASE}/api/v1/geofences/{new_zone}",
                headers=hdr("manager"),
                json={"name": "Documentation probe zone (renamed)", "capacity": 12},
                timeout=30,
            ),
        )
    record(
        "ERR geofence create by operator (403)",
        requests.post(
            f"{BASE}/api/v1/geofences",
            headers=hdr("operator"),
            json={
                "code": f"NOPE{uuid.uuid4().hex[:4].upper()}",
                "name": "Should be refused",
                "latitude": 28.6,
                "longitude": 77.2,
                "radius": 50,
            },
            timeout=30,
        ),
    )

    # ---- retrieval prediction ----
    record(
        "POST /retrieval/predict",
        requests.post(
            f"{BASE}/api/v1/retrieval/predict",
            headers=hdr("operator"),
            json={"custody_session_id": session_id},
            timeout=60,
        ),
    )

    # ---- alerts: acknowledge / resolve (legacy geofence feed) ----
    legacy = requests.get(
        f"{BASE}/api/v1/alerts?limit=5", headers=hdr("manager"), timeout=30
    ).json()["data"]["alerts"]
    if legacy:
        aid = legacy[0]["id"]
        record(
            "POST /alerts/<id>/acknowledge",
            requests.post(
                f"{BASE}/api/v1/alerts/{aid}/acknowledge",
                headers=hdr("manager"),
                timeout=30,
            ),
        )
        record(
            "POST /alerts/<id>/resolve",
            requests.post(
                f"{BASE}/api/v1/alerts/{aid}/resolve",
                headers=hdr("manager"),
                json={"resolution_notes": "Investigated; vehicle returned."},
                timeout=30,
            ),
        )

    # ---- centralized feed: create / read / read-all / resolve ----
    created = record(
        "POST /feed",
        requests.post(
            f"{BASE}/api/v1/feed",
            headers=hdr("manager"),
            json={
                "custody_id": session_id,
                "type": "RETRIEVAL_READY",
                "severity": "LOW",
                "message": "Documentation probe alert.",
                "audiences": ["CUSTOMER", "VALET_MANAGER"],
            },
            timeout=30,
        ),
    )
    feed_id = created.get("data", {}).get("alert", {}).get("alert_id")
    if feed_id:
        record(
            "POST /feed/<id>/read",
            requests.post(
                f"{BASE}/api/v1/feed/{feed_id}/read",
                headers={"Authorization": f"Bearer {cust_token}"},
                timeout=30,
            ),
        )
        record(
            "POST /feed/read-all",
            requests.post(
                f"{BASE}/api/v1/feed/read-all",
                headers={"Authorization": f"Bearer {cust_token}"},
                timeout=30,
            ),
        )
        record(
            "ERR feed resolve by customer (403)",
            requests.post(
                f"{BASE}/api/v1/feed/{feed_id}/resolve",
                headers={"Authorization": f"Bearer {cust_token}"},
                json={"notes": "Should be refused."},
                timeout=30,
            ),
        )
        record(
            "POST /feed/<id>/resolve",
            requests.post(
                f"{BASE}/api/v1/feed/{feed_id}/resolve",
                headers=hdr("manager"),
                json={"notes": "Closed during documentation pass."},
                timeout=30,
            ),
        )

    # ---- complete the ticket ----
    record(
        "POST /tickets/<id>/complete",
        requests.post(
            f"{BASE}/api/v1/tickets/{ticket_id}/complete",
            headers=hdr("operator"),
            json={"odometer": 41320, "condition_accepted": True},
            timeout=30,
        ),
    )

    # ---- cancel a second ticket, then archive the vehicle ----
    issue2 = requests.post(
        f"{BASE}/api/v1/tickets",
        headers=hdr("operator"),
        json={"vehicle_id": vehicle_id},
        timeout=30,
    ).json()
    t2 = issue2["data"]["ticket"]["id"]
    record(
        "POST /tickets/<id>/cancel",
        requests.post(
            f"{BASE}/api/v1/tickets/{t2}/cancel",
            headers=hdr("operator"),
            json={"reason": "Customer changed plans."},
            timeout=30,
        ),
    )
    record(
        "DELETE /vehicles/<id>",
        requests.delete(
            f"{BASE}/api/v1/vehicles/{vehicle_id}",
            headers={"Authorization": f"Bearer {cust_token}"},
            timeout=30,
        ),
    )

    # ---- logout ----
    record(
        "POST /auth/logout",
        requests.post(
            f"{BASE}/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {cust_token}"},
            json={"refresh_token": "ignored"},
            timeout=30,
        ),
    )

    with open("/tmp/api_write_shapes.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"wrote {len(out)} probes", file=sys.stderr)


if __name__ == "__main__":
    main()

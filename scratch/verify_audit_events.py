#!/usr/bin/env python3
"""Live check: does a real custody actually emit all fourteen audit events?

The unit tests assert the vocabulary exists and the viewer reads it. They cannot
prove the *emitters* fire, because they insert log rows directly. This script
drives a genuine custody through the HTTP API end to end and then reads the
timeline back, so a green run means the events are produced by the real code
paths rather than declared somewhere.
"""

from __future__ import annotations

import io
import os
import sys
import uuid

import requests

sys.path.insert(0, "/home/user/backend")

from ai.synthetic.vehicle_photos import build_scenario  # noqa: E402

B = os.environ.get("API_BASE_URL", "http://127.0.0.1:5000")
S = requests.Session()
TAG = uuid.uuid4().hex[:6].upper()
PW = "Demo#Passw0rd"

EXPECTED = [
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
    "CUSTODY_COMPLETED",
]


def login(email: str, password: str = PW) -> str:
    r = S.post(f"{B}/api/v1/auth/login", json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    admin = login("admin@valet.local", os.environ.get("SEED_ADMIN_PASSWORD", ""))
    print(f"admin logged in (run {TAG})")

    # --- accounts ---------------------------------------------------------
    ce = f"audit.customer.{TAG}@example.com"
    ve = f"audit.valet.{TAG}@example.com"
    me = f"audit.manager.{TAG}@example.com"
    S.post(f"{B}/api/v1/auth/register", json={
        "email": ce, "password": PW, "full_name": "Audit Customer",
    }, timeout=30).raise_for_status()
    for email, role in ((ve, "VALET_OPERATOR"), (me, "VALET_MANAGER")):
        S.post(f"{B}/api/v1/auth/users", headers=hdr(admin), json={
            "email": email, "password": PW, "full_name": role.title(), "role": role,
        }, timeout=30).raise_for_status()

    cust = login(ce)
    valet = login(ve)
    mgr = login(me)

    # --- zone --------------------------------------------------------------
    zone_resp = S.post(f"{B}/api/v1/geofences", headers=hdr(mgr), json={
        "code": f"AUD-{TAG}", "name": "Audit Zone",
        "latitude": 28.6896, "longitude": 76.9113, "radius": 60.0, "capacity": 10,
    }, timeout=30)
    assert zone_resp.status_code in (200, 201), zone_resp.text[:400]
    zone = zone_resp.json()["data"].get("zone") or zone_resp.json()["data"]

    # --- vehicle + ticket --------------------------------------------------
    plate = f"UP78GH{1000 + int(TAG[:2], 16) % 9000}"
    veh = S.post(f"{B}/api/v1/vehicles", headers=hdr(cust), json={
        "plate_number": plate, "make": "Maruti", "model": "Swift",
        "color": "silver", "vehicle_type": "HATCHBACK", "registration_year": 2021,
    }, timeout=30).json()["data"]["vehicle"]

    ticket_resp = S.post(f"{B}/api/v1/tickets", headers=hdr(valet),
                         json={"vehicle_id": veh["id"]}, timeout=30)
    assert ticket_resp.status_code in (200, 201), ticket_resp.text[:400]
    ticket = ticket_resp.json()["data"].get("ticket") or ticket_resp.json()["data"]

    # --- check-in ----------------------------------------------------------
    ci = S.post(f"{B}/api/v1/tickets/{ticket['id']}/check-in", headers=hdr(valet),
                json={"zone_id": zone["id"], "odometer_at_dropoff": 41200},
                timeout=30).json()["data"]
    custody = ci["ticket"]["custody_session"]["id"]
    print(f"custody {custody} open in zone {zone['code']}")

    # --- drop-off images ---------------------------------------------------
    pair = build_scenario("scratch")
    S.post(f"{B}/api/v1/inspections/dropoff", headers=hdr(valet),
           data={"custody_session_id": custody, "notes": "audit drop-off"},
           files=[("images", ("d.png", io.BytesIO(pair.drop_off_png), "image/png"))],
           timeout=60).raise_for_status()

    # --- GPS: parked, then a breach ---------------------------------------
    S.post(f"{B}/api/v1/gps/simulate", headers=hdr(valet),
           json={"custody_id": custody, "scenario": "parked", "seed": 7},
           timeout=60).raise_for_status()
    breach = S.post(f"{B}/api/v1/gps/simulate", headers=hdr(valet),
                    json={"custody_id": custody, "scenario": "unauthorised", "seed": 11},
                    timeout=60)
    breach.raise_for_status()
    print(f"breach track ingested; alerts raised: "
          f"{breach.json()['data'].get('alerts_raised')}")

    # --- customer requests pickup -----------------------------------------
    S.post(f"{B}/api/v1/retrieval/request", headers=hdr(cust),
           json={"custody_session_id": custody}, timeout=60).raise_for_status()

    # --- prepare -----------------------------------------------------------
    for status in ("RETRIEVAL_IN_PROGRESS", "READY_FOR_PICKUP"):
        S.patch(f"{B}/api/v1/custody-sessions/{custody}", headers=hdr(valet),
                json={"status": status}, timeout=30).raise_for_status()

    # --- pickup images (runs the comparison) -------------------------------
    S.post(f"{B}/api/v1/inspections/pickup", headers=hdr(valet),
           data={"custody_session_id": custody, "notes": "audit pickup"},
           files=[("images", ("p.png", io.BytesIO(pair.pickup_png), "image/png"))],
           timeout=90).raise_for_status()

    # --- verify the finding as manager -------------------------------------
    report = S.get(f"{B}/api/v1/inspections/{custody}", headers=hdr(mgr),
                   timeout=30).json()["data"]
    dets = (report.get("pickup") or {}).get("detections") or []
    if dets:
        S.patch(f"{B}/api/v1/inspections/detections/{dets[0]['id']}/verification",
                headers=hdr(mgr), json={"status": "CONFIRMED", "notes": "audit check"},
                timeout=30).raise_for_status()
        print(f"verified finding {dets[0]['damage_type']} on {dets[0]['region']}")
    else:
        print("no detection to verify (pipeline found nothing on this pair)")

    # --- complete ----------------------------------------------------------
    S.post(f"{B}/api/v1/tickets/{ticket['id']}/complete", headers=hdr(valet),
           json={"odometer_at_pickup": 41263, "condition_accepted": True},
           timeout=30).raise_for_status()
    print("custody completed")

    # --- read the timeline back -------------------------------------------
    tl = S.get(f"{B}/api/v1/audit/timeline/{custody}", headers=hdr(mgr),
               timeout=30).json()["data"]
    present = [e["action"] for e in tl["events"]]
    ordered = sorted(set(present), key=present.index)

    print(f"\ntimeline: {tl['event_count']} events, oldest first")
    for action in EXPECTED:
        mark = "PASS" if action in present else "MISS"
        print(f"  [{mark}] {action}")

    missing = [a for a in EXPECTED if a not in present]
    print(f"\nactual order: {ordered}")

    # --- field completeness ------------------------------------------------
    sample = next((e for e in tl["events"] if e["action"] == "VEHICLE_CHECKED_IN"), None)
    if sample:
        print("\nsample entry (VEHICLE_CHECKED_IN):")
        for field in ("actor_email", "actor_role", "action", "entity_type",
                      "entity_id", "occurred_at", "ip_address", "request_id", "metadata"):
            print(f"  {field:14s} = {sample.get(field)!r}")

    # --- immutability from the client side ---------------------------------
    r = S.get(f"{B}/api/v1/audit", headers=hdr(mgr), timeout=30).json()["data"]
    print(f"\nviewer declares mutable={r['mutable']}")
    for method in ("post", "patch", "delete"):
        code = getattr(S, method)(f"{B}/api/v1/audit", headers=hdr(mgr), timeout=30).status_code
        print(f"  {method.upper():6s} /api/v1/audit -> {code}")

    # --- access control ----------------------------------------------------
    for token, label in ((cust, "customer"), (valet, "operator")):
        code = S.get(f"{B}/api/v1/audit", headers=hdr(token), timeout=30).status_code
        print(f"  GET as {label:9s} -> {code}")

    print()
    if missing:
        print(f"MISSING {len(missing)}: {missing}")
        return 1
    print("ALL 14 LIFECYCLE EVENTS EMITTED BY REAL CODE PATHS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

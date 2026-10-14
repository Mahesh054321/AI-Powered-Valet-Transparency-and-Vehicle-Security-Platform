"""Live end-to-end walk of the geofencing API against the running server.

Deliberately uses plain HTTP rather than the Flask test client: it proves the
mounted routes, the auth decorators and the JSON envelope all work over a real
socket, not just in-process.
"""

from __future__ import annotations

import json
import random
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta


def ago(**delta) -> str:
    return (datetime.now(tz=UTC) - timedelta(**delta)).isoformat()

BASE = "http://127.0.0.1:5000/api"
PASSWORD = "Demo#Passw0rd"


def call(method, path, token=None, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode())


def login(email):
    status, payload = call("POST", "/auth/login", body={"email": email, "password": PASSWORD})
    assert status == 200, (status, payload)
    return payload["data"]["access_token"]


def north_of(latitude, longitude, metres):
    import math

    radius = 6_371_008.8
    lat = math.radians(latitude)
    angular = metres / radius
    return math.degrees(
        math.asin(math.sin(lat) * math.cos(angular) + math.cos(lat) * math.sin(angular))
    )


manager = login("manager.karan@example.com")
valet = login("valet.suresh@example.com")
owner = login("customer.aisha@example.com")
stranger = login("customer.rohit@example.com")
print(f"tokens acquired: manager={len(manager)} valet={len(valet)} customer={len(owner)}")

print("\n1) POST /api/geofences  (manager, centre + radius)")
status, payload = call(
    "POST",
    "/geofences",
    manager,
    {
        "code": "ZONE-LIVE",
        "name": "Live demo yard",
        "latitude": 27.4924,
        "longitude": 77.6737,
        "radius": 100,
        "buffer_metres": 25,
        "capacity": 10,
    },
)
if status == 409:  # re-running the walk: reuse the existing zone
    status, listing = call("GET", "/geofences", manager)
    zone = next(z for z in listing["data"]["zones"] if z["code"] == "ZONE-LIVE")
else:
    assert status == 201, (status, payload)
    zone = payload["data"]
zone_id = zone["id"]
print(f"   zone={zone['code']} id={zone_id[:8]}…")
print(f"   generated polygon vertices={len(zone['geofence_polygon'])} radius={zone['radius_metres']}")

print("\n2) operator tries to create a geofence  -> expect 403")
status, payload = call(
    "POST", "/geofences", valet,
    {"code": "NOPE", "name": "x", "latitude": 27.4, "longitude": 77.6, "radius": 50},
)
print(f"   HTTP {status} | {payload['error']['message'][:72]}")
assert status == 403

print("\n3) issue ticket + check the vehicle into the zone")
# Register a fresh vehicle rather than picking one of the customer's existing
# ones. This walk used to select "a vehicle that is not already in custody",
# which passed on a clean seed and then failed on the second run in a different
# way: a vehicle can be out of custody and still have an *unstarted* ticket, and
# that 409 carries `details.ticket_id`, not `custody_session_id`. Creating its
# own input makes the walk repeatable whatever state the database is in. The
# vehicle belongs to the customer who is later asked to view it, so the
# ownership check in step 10 is still testing ownership.
_plate = "HR16" + "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=2)) + str(
    random.randint(1000, 9999)
)
status, payload = call(
    "POST", "/v1/vehicles", owner,
    {"plate_number": _plate, "make": "Maruti", "model": "Swift"},
)
assert status == 201, (status, payload)
vehicle = payload["data"]["vehicle"]
vehicle_id = vehicle["id"]

status, payload = call("POST", "/v1/tickets", valet, {"vehicle_id": vehicle_id})
assert status == 201, (status, payload)
ticket_id = payload["data"]["ticket"]["id"]
status, payload = call("POST", f"/v1/tickets/{ticket_id}/check-in", valet, {"zone_id": zone_id})
assert status == 200, (status, payload)
_, payload = call("GET", f"/v1/tickets/{ticket_id}", valet)
custody_id = payload["data"]["ticket"]["custody_session"]["id"]
print(f"   custody={custody_id[:8]}…  vehicle={vehicle['plate_number']}")

print("\n4) POST /api/gps/events  -- fix at the zone centre")
status, payload = call(
    "POST", "/gps/events", valet,
    {
        "custody_id": custody_id,
        "latitude": 27.4924,
        "longitude": 77.6737,
        "speed": 2.0,
        "timestamp": ago(minutes=20),
    },
)
assert status == 201, (status, payload)
fix = payload["data"]
print(
    f"   status={fix['movement_status']} within_zone={fix['gps_event']['is_within_zone']} "
    f"dist={fix['gps_event']['distance_from_zone_metres']} "
    f"source={fix['gps_event']['source']} alerts={len(fix['alerts_raised'])}"
)
assert fix["movement_status"] == "NORMAL"

print("\n5) POST /api/gps/events  -- fix 500 m north, outside the geofence")
outside_lat = north_of(27.4924, 77.6737, 500.0)
status, payload = call(
    "POST", "/gps/events", valet,
    {
        "custody_id": custody_id,
        "latitude": outside_lat,
        "longitude": 77.6737,
        "speed": 5.0,
        "timestamp": ago(minutes=15),
    },
)
assert status == 201, (status, payload)
breach = payload["data"]
print(
    f"   status={breach['movement_status']} "
    f"dist={breach['gps_event']['distance_from_zone_metres']} m beyond the boundary"
)
for alert in breach["alerts_raised"]:
    print(f"   ALERT {alert['alert_type']} {alert['severity']} | {alert['reason'][:66]}")
assert breach["movement_status"] == "BREACH"
assert any(a["alert_type"] == "GEOFENCE_BREACH" for a in breach["alerts_raised"])
alert_id = next(a["id"] for a in breach["alerts_raised"] if a["alert_type"] == "GEOFENCE_BREACH")

print("\n6) the same breach one minute later  -> notification throttled")
status, payload = call(
    "POST", "/gps/events", valet,
    {
        "custody_id": custody_id,
        "latitude": outside_lat,
        "longitude": 77.6737,
        "speed": 5.0,
        "timestamp": ago(minutes=14),
    },
)
repeat = payload["data"]
print(
    f"   status={repeat['movement_status']} new_alerts={len(repeat['alerts_raised'])} "
    f"suppressed={repeat['alerts_suppressed']}"
)
assert "GEOFENCE_BREACH" in repeat["alerts_suppressed"]

print("\n6b) a fix timestamped two hours ahead  -> refused")
status, payload = call(
    "POST", "/gps/events", valet,
    {
        "custody_id": custody_id,
        "latitude": 27.4924,
        "longitude": 77.6737,
        "timestamp": (datetime.now(tz=UTC) + timedelta(hours=2)).isoformat(),
    },
)
print(f"   HTTP {status} | {payload['error']['message'][:76]}…")
assert status == 422

print("\n7) POST /api/gps/simulate  (scenario=unauthorised)")
status, payload = call(
    "POST", "/gps/simulate", valet,
    {"custody_id": custody_id, "scenario": "unauthorised", "seed": 7},
)
assert status == 201, (status, payload)
sim = payload["data"]
print(f"   source={sim['source']} fixes={sim['fix_count']} statuses={sim['status_counts']}")
print(
    "   alerts=" + str(sorted({a['alert_type'] for a in sim['alerts_raised']}))
)
print(f"   warning carried in the response: {sim['notes'][0][:70]}…")
assert sim["source"] == "SIMULATED" and sim["is_simulated"] is True
assert "BREACH" in sim["status_counts"]

print("\n8) GET /api/gps/{custody_id}  -- the tracking view")
status, payload = call("GET", f"/gps/{custody_id}", valet)
assert status == 200, (status, payload)
view = payload["data"]
print(
    f"   vehicle={view['vehicle']['plate_number']} zone={view['zone']['code']} "
    f"effective_radius={view['zone']['effective_radius_metres']} m"
)
print(f"   latest={view['movement_status']} fixes={view['fix_count']} counts={view['status_counts']}")
print(f"   sources={view['sources']} has_simulated={view['has_simulated_telemetry']}")
print(f"   telemetry_note={view['telemetry_note'][:66]}…")
print(f"   open_alert_count={view['open_alert_count']}  last_updated={view['last_updated_at']}")
assert view["has_simulated_telemetry"] is True
assert all(f["is_simulated"] for f in view["track"] if f["source"] == "SIMULATED")

print("\n9) GET /api/alerts and triage")
status, payload = call("GET", "/alerts?severity=HIGH", manager)
print(f"   HIGH alerts={payload['data']['total']}")
status, payload = call("POST", f"/alerts/{alert_id}/acknowledge", valet)
print(f"   operator acknowledges -> HTTP {status} (expect 403)")
assert status == 403
status, payload = call("POST", f"/alerts/{alert_id}/acknowledge", manager)
print(
    f"   manager acknowledges -> {payload['data']['status']} by "
    f"{payload['data']['acknowledged_by_email']} is_open={payload['data']['is_open']}"
)
assert payload["data"]["status"] == "ACKNOWLEDGED" and payload["data"]["is_open"] is True
status, payload = call(
    "POST", f"/alerts/{alert_id}/resolve", manager,
    {"notes": "Valet relocated the vehicle between bays."},
)
print(f"   manager resolves -> {payload['data']['status']} notes={payload['data']['resolution_notes']}")
assert payload["data"]["status"] == "RESOLVED"
status, _ = call("POST", f"/alerts/{alert_id}/resolve", manager, {})
print(f"   resolving again -> HTTP {status} (expect 409)")
assert status == 409

print("\n10) ownership on the tracking view")
status, payload = call("GET", f"/gps/{custody_id}", owner)
print(
    f"    owning customer -> HTTP {status}, plate="
    f"{payload['data']['vehicle']['plate_number']}"
)
assert status == 200
status, payload = call("GET", f"/gps/{custody_id}", stranger)
print(f"    another customer -> HTTP {status} (expect 404, not 403)")
assert status == 404
status, payload = call("GET", "/alerts", stranger)
print(f"    stranger's alert list -> HTTP {status}, sees {payload['data']['total']} alerts")

print("\n11) unversioned aliases resolve identically")
for path, token in (("/geofences", manager), ("/alerts", manager), (f"/gps/{custody_id}", valet)):
    status, _ = call("GET", path, token)
    versioned, _ = call("GET", f"/v1{path}", token)
    print(f"    GET {path:<22} HTTP {status}   GET /v1{path:<18} HTTP {versioned}")
    assert status == versioned == 200

print("\n12) audit trail written by this walk")
import subprocess  # noqa: E402

rows = subprocess.run(
    [
        "psql", "-h", "127.0.0.1", "-U", "valet_user", "-d", "valet_db", "-t", "-A", "-F", "|",
        "-c",
        "SELECT action, count(*) FROM audit_logs "
        "WHERE action IN ('GEOFENCE_CONFIGURED','GPS_TRACK_SIMULATED','ALERT_RAISED',"
        "'ALERT_ACKNOWLEDGED','ALERT_RESOLVED') GROUP BY action ORDER BY action;",
    ],
    capture_output=True, text=True, env={"PGPASSWORD": "change-me-locally", "PATH": "/usr/bin:/bin"},
)
for line in rows.stdout.strip().splitlines():
    print(f"    {line}")

print("\nALL CHECKS PASSED")

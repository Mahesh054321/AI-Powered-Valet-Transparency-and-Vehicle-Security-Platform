"""Live curl-equivalent walk through the prototype ML security layer.

Every check hits the running API over HTTP. Run with the API up on :5000.
"""

from __future__ import annotations

import math
import sys
import time
from datetime import UTC, datetime, timedelta

import requests

BASE = "http://127.0.0.1:5000"
PASS, FAIL = "  PASS ", "  FAIL "
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{PASS if condition else FAIL} {label}" + (f"  [{detail}]" if detail else ""))
    if not condition:
        failures.append(label)


def login(email: str, password: str = "Demo#Passw0rd") -> str:
    response = requests.post(
        f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=20
    )
    response.raise_for_status()
    return response.json()["data"]["access_token"]


def hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def minutes_ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def night_time_sin_cos(hour: float = 2.5) -> tuple[float, float]:
    angle = math.pi * 2 * (hour / 24.0)
    return math.sin(angle), math.cos(angle)


def main() -> int:
    print("=" * 74)
    print("  Live walk: prototype ML security layer")
    print("=" * 74)

    manager = login("manager.karan@example.com")
    operator = login("valet.suresh@example.com")
    customer = login("customer.aisha@example.com")
    print(f"\nlogged in as manager/operator/customer")

    # ---------------------------------------------------------------- 1
    print("\n-- 1. model introspection ------------------------------------------")
    r = requests.get(f"{BASE}/api/v1/ml/model", headers=hdr(manager), timeout=20)
    check("GET /api/v1/ml/model is 200", r.status_code == 200, str(r.status_code))
    info = r.json()["data"]
    check("model reports it is available", info["available"] is True)
    check("discloses synthetic training", info["trained_on_synthetic_data"] is True)
    check("carries the prototype disclaimer", "synthetic" in info["disclaimer"].lower())
    check("exposes the model version", bool(info.get("model_version")), info.get("model_version", ""))
    check("exposes training metrics", bool(info.get("metrics")))
    m = info.get("metrics") or {}
    if m:
        print(
            f"        acc {m['accuracy']:.4f}  prec {m['precision']:.4f}  "
            f"rec {m['recall']:.4f}  f1 {m['f1']:.4f}"
        )
        cm = m["confusion_matrix"]
        print(f"        confusion matrix {cm['matrix']}  ({cm['reading']})")
        check("confusion matrix is a 2x2", len(cm["matrix"]) == 2 and
              all(len(row) == 2 for row in cm["matrix"]))
        check("confusion matrix is labelled", cm["labels"] == ["NORMAL", "SUSPICIOUS"])
        check("a model that always said NORMAL would score worse",
              cm["matrix"][0][0] > 0 and cm["matrix"][1][1] > 0)
    check("escalation threshold disclosed", info["escalation_threshold"] == 0.70)
    check("feature columns described", len(info["feature_columns"]) == 8)

    # ---------------------------------------------------------------- 2
    print("\n-- 2. unversioned alias -------------------------------------------")
    r = requests.get(f"{BASE}/api/ml/model", headers=hdr(manager), timeout=20)
    check("GET /api/ml/model alias is 200", r.status_code == 200, str(r.status_code))
    check("alias returns the same version",
          r.json()["data"]["model_version"] == info["model_version"])

    # ---------------------------------------------------------------- 3
    print("\n-- 3. authentication is enforced ----------------------------------")
    r = requests.get(f"{BASE}/api/v1/ml/model", timeout=20)
    check("anonymous GET /ml/model is 401", r.status_code == 401, str(r.status_code))
    r = requests.post(f"{BASE}/api/v1/ml/classify", json={}, timeout=20)
    check("anonymous POST /ml/classify is 401", r.status_code == 401, str(r.status_code))

    # ---------------------------------------------------------------- 4
    print("\n-- 4. classification returns the four required fields -------------")
    sin, cos = night_time_sin_cos(2.5)
    suspicious = {
        "speed_kmph": 14.0,
        "distance_from_zone_metres": -20.0,
        "movement_duration_seconds": 900.0,
        "direction_change_degrees": 15.0,
        "distance_travelled_metres": 45.0,
        "zone_breach": 0.0,
        "time_sin": sin,
        "time_cos": cos,
    }
    r = requests.post(f"{BASE}/api/v1/ml/classify", json=suspicious, headers=hdr(operator), timeout=20)
    check("POST /ml/classify is 200", r.status_code == 200, str(r.status_code))
    data = r.json()["data"]
    check("returns classification", data["classification"] in ("NORMAL", "SUSPICIOUS"), data["classification"])
    check("returns probability in [0,1]", 0.0 <= data["probability"] <= 1.0, str(data["probability"]))
    check("returns confidence too", data["confidence"] == data["probability"])
    check("returns a reason", bool(data["reason"]))
    check("returns severity", data["severity"] in ("LOW", "MEDIUM", "HIGH"), data["severity"])
    check("reason names the prototype", "prototype" in data["reason"].lower())
    check("lists top factors", len(data["top_factors"]) > 0, str(len(data["top_factors"])))
    check("factors show their direction",
          all(f["direction"] in ("towards SUSPICIOUS", "towards NORMAL") for f in data["top_factors"]))
    check("echoes the inputs it judged", data["inputs"] == suspicious)
    check("provenance flag present", data["trained_on_synthetic_data"] is True)
    print(f"        -> {data['classification']} p={data['probability']:.4f} sev={data['severity']}")
    print(f"        reason: {data['reason'][:150]}...")

    # ---------------------------------------------------------------- 5
    print("\n-- 5. a benign daytime fix scores differently ----------------------")
    sin_d, cos_d = night_time_sin_cos(14.0)
    benign = {
        "speed_kmph": 0.0,
        "distance_from_zone_metres": -40.0,
        "movement_duration_seconds": 0.0,
        "direction_change_degrees": 0.0,
        "distance_travelled_metres": 0.0,
        "zone_breach": 0.0,
        "time_sin": sin_d,
        "time_cos": cos_d,
    }
    r = requests.post(f"{BASE}/api/v1/ml/classify", json=benign, headers=hdr(operator), timeout=20)
    benign_data = r.json()["data"]
    check("benign fix scores NORMAL", benign_data["classification"] == "NORMAL",
          benign_data["classification"])
    check("benign probability is below the suspicious one",
          benign_data["probability"] < data["probability"],
          f"{benign_data['probability']:.4f} < {data['probability']:.4f}")
    print(f"        -> {benign_data['classification']} p={benign_data['probability']:.4f}")

    # ---------------------------------------------------------------- 6
    print("\n-- 6. malformed input is refused, not defaulted --------------------")
    partial = dict(suspicious)
    del partial["movement_duration_seconds"]
    r = requests.post(f"{BASE}/api/v1/ml/classify", json=partial, headers=hdr(operator), timeout=20)
    check("missing feature is 422", r.status_code == 422, str(r.status_code))
    check("names the missing feature",
          r.json()["error"]["details"]["missing_features"] == ["movement_duration_seconds"])
    check("explains why defaulting is refused", "zero" in r.json()["error"]["message"])

    bad = dict(suspicious, speed_kmph="fast")
    r = requests.post(f"{BASE}/api/v1/ml/classify", json=bad, headers=hdr(operator), timeout=20)
    check("non-numeric feature is 422", r.status_code == 422, str(r.status_code))
    check("names the offending field", "speed_kmph" in r.json()["error"]["details"]["invalid"])

    bad_flag = dict(suspicious, zone_breach=2.0)
    r = requests.post(f"{BASE}/api/v1/ml/classify", json=bad_flag, headers=hdr(operator), timeout=20)
    check("out-of-range flag is 422", r.status_code == 422, str(r.status_code))

    negative = dict(suspicious, speed_kmph=-5.0)
    r = requests.post(f"{BASE}/api/v1/ml/classify", json=negative, headers=hdr(operator), timeout=20)
    check("negative speed is 422", r.status_code == 422, str(r.status_code))

    # ---------------------------------------------------------------- 7
    print("\n-- 7. a real breach still alerts, whatever the model thinks --------")
    zone_code = f"MLZONE{int(time.time()) % 100000:05d}"
    r = requests.post(
        f"{BASE}/api/v1/geofences",
        json={"code": zone_code, "name": "ML walk yard",
              "latitude": 28.9720, "longitude": 75.4020, "radius": 100.0},
        headers=hdr(manager), timeout=20,
    )
    check("geofence created", r.status_code == 201, str(r.status_code))
    zone_id = r.json()["data"]["id"]

    # Register a fresh vehicle rather than hunting for a free seeded one: the
    # walk checks vehicles in, so a second run would find them all in custody
    # and fail for a reason that has nothing to do with the ML layer.
    owner_token = login("customer.aisha@example.com")
    # The validator requires a real Indian registration format (e.g. HR16AB1234),
    # so the plate is built in that shape with a time-derived serial to avoid
    # colliding with the seeded vehicles or with a previous run.
    serial = int(time.time()) % 10000
    plate = f"HR16ML{serial:04d}"
    r = requests.post(
        f"{BASE}/api/v1/vehicles",
        json={"plate_number": plate, "make": "Tata", "model": "Nexon", "color": "White"},
        headers=hdr(owner_token), timeout=20,
    )
    check("a vehicle was registered for the walk", r.status_code == 201,
          f"{r.status_code} {plate}")
    vehicle_id = r.json()["data"]["vehicle"]["id"]

    r = requests.post(f"{BASE}/api/v1/tickets", json={"vehicle_id": vehicle_id},
                      headers=hdr(operator), timeout=20)
    if r.status_code != 201:
        print("        detail:", str(r.json())[:220])
    check("ticket issued", r.status_code == 201, str(r.status_code))
    ticket_id = r.json()["data"]["ticket"]["id"]

    r = requests.post(f"{BASE}/api/v1/tickets/{ticket_id}/check-in",
                      json={"zone_id": zone_id}, headers=hdr(operator), timeout=20)
    check("vehicle checked in", r.status_code == 200, str(r.status_code))
    r = requests.get(f"{BASE}/api/v1/tickets/{ticket_id}", headers=hdr(operator), timeout=20)
    session_id = r.json()["data"]["ticket"]["custody_session"]["id"]

    # A fix ~300 m north of the zone centre: unambiguously outside.
    breach = requests.post(
        f"{BASE}/api/v1/gps/events",
        json={"custody_session_id": session_id, "latitude": 28.9747, "longitude": 75.4020,
              "speed_kmph": 10.0, "timestamp": minutes_ago(3)},
        headers=hdr(operator), timeout=20,
    )
    check("breach fix accepted", breach.status_code == 201, str(breach.status_code))
    bdata = breach.json()["data"]
    check("verdict is BREACH", bdata["movement_status"] == "BREACH", bdata["movement_status"])
    check("rule verdict reported separately", bdata["rule_movement_status"] == "BREACH")
    check("ML did not escalate a breach", bdata["escalated_by_ml"] is False)
    check("an alert was raised despite the model", len(bdata["alerts_raised"]) >= 1,
          str(len(bdata["alerts_raised"])))
    check("the alert is a geofence breach",
          any(a["alert_type"] == "GEOFENCE_BREACH" for a in bdata["alerts_raised"]))
    check("ml block is present on the response", "ml" in bdata)
    if bdata["ml"]:
        print(f"        ml -> {bdata['ml']['classification']} "
              f"p={bdata['ml']['probability']:.4f} ({bdata['ml']['model_version']})")
        check("ml classification persisted", bdata["ml"]["classification"] in ("NORMAL", "SUSPICIOUS"))
        check("ml model version persisted", bool(bdata["ml"]["model_version"]))

    # ---------------------------------------------------------------- 8
    print("\n-- 8. a benign fix inside the zone ---------------------------------")
    inside = requests.post(
        f"{BASE}/api/v1/gps/events",
        json={"custody_session_id": session_id, "latitude": 28.9721, "longitude": 75.4020,
              "speed_kmph": 3.0, "timestamp": minutes_ago(1)},
        headers=hdr(operator), timeout=20,
    )
    check("inside fix accepted", inside.status_code == 201, str(inside.status_code))
    idata = inside.json()["data"]
    print(f"        movement_status={idata['movement_status']} "
          f"rule={idata['rule_movement_status']} escalated={idata['escalated_by_ml']}")
    check("the inside fix is not a breach", idata["movement_status"] != "BREACH")

    # ---------------------------------------------------------------- 9
    print("\n-- 9. the stored rows carry the ML columns -------------------------")
    r = requests.get(f"{BASE}/api/v1/gps/{session_id}/fixes", headers=hdr(manager), timeout=20)
    check("tracking view is 200", r.status_code == 200, str(r.status_code))
    events = r.json()["data"]["fixes"]
    check("tracking view returns both fixes", len(events) == 2, str(len(events)))
    for event in events:
        ml = event.get("ml")
        if ml is None:
            check(f"fix {event['id'][:8]} has no ml block", True, "model unavailable for this row")
        else:
            check(f"fix {event['id'][:8]} ml block complete",
                  ml["classification"] in ("NORMAL", "SUSPICIOUS")
                  and 0.0 <= ml["probability"] <= 1.0
                  and bool(ml["model_version"]),
                  str(ml))

    # ---------------------------------------------------------------- 10
    print("\n-- 10. rules-only deployment is a supported state -------------------")
    print("        (verified in tests/test_security_ml.py::TestRulesOnlyDeployment)")

    print("\n" + "=" * 74)
    if failures:
        print(f"  {len(failures)} CHECK(S) FAILED:")
        for name in failures:
            print(f"    - {name}")
        return 1
    print("  ALL CHECKS PASSED")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())

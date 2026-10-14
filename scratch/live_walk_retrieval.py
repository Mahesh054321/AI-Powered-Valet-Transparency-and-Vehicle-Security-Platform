"""Live end-to-end walk of the retrieval-prediction endpoints.

Run against a live API and a seeded database:

    cd backend && python3 utils/scripts/run_api.py        # terminal 1
    python3 scratch/live_walk_retrieval.py                # terminal 2

Creates its own inputs (a vehicle, a ticket, a custody session) rather than
consuming seeded fixture state, so it is repeatable. Exercises both the
versioned and the unversioned prefix, because the brief specifies the
unversioned paths and they are the ones a client was actually given.
"""

from __future__ import annotations

import random
import sys
import uuid

import requests

BASE = "http://127.0.0.1:5000"
PASSWORD = "Demo#Passw0rd"

failures: list[str] = []


def make_plate() -> str:
    """A registration the platform's validator accepts.

    Two letters, two digits, two letters and four digits. Randomising the final
    group only is not enough -- the series letters collide too, and this script
    is meant to be re-runnable.
    """
    letters = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=2))
    return f"HR16{letters}{random.randint(1000, 9999)}"


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def login(email: str) -> dict[str, str]:
    response = requests.post(
        f"{BASE}/api/auth/login", json={"email": email, "password": PASSWORD}, timeout=20
    )
    response.raise_for_status()
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    print("=" * 74)
    print("  LIVE WALK - Predictive Vehicle Retrieval (phase 9b)")
    print("=" * 74)

    # -- 1. model metadata --------------------------------------------------
    print("\n1. Model metadata")
    operator = login("valet.suresh@example.com")
    customer = login("customer.aisha@example.com")

    info = requests.get(f"{BASE}/api/v1/retrieval/model", headers=operator, timeout=20)
    check("GET /api/v1/retrieval/model is 200", info.status_code == 200, str(info.status_code))
    body = info.json()["data"]
    check("model reports itself available", body.get("available") is True)
    check(
        "primary metric is MAE",
        body.get("primary_metric") == "mae_minutes",
        str(body.get("primary_metric")),
    )
    metrics = body.get("metrics") or {}
    mae, baseline = metrics.get("mae_minutes"), metrics.get("baseline_mae_minutes")
    check(
        f"MAE {mae} beats median baseline {baseline}",
        bool(mae and baseline and mae < baseline),
    )
    check(
        "response declares synthetic training data",
        body.get("trained_on_synthetic_data") is True,
    )
    check("disclaimer is present", bool(body.get("disclaimer")))
    print(f"      held-out MAE {mae} min vs baseline {baseline} min "
          f"({metrics.get('improvement_over_baseline', 0):.1%} better)")

    # -- 2. anonymous is refused -------------------------------------------
    print("\n2. Authentication")
    anon = requests.post(
        f"{BASE}/api/retrieval/predict",
        json={"custody_session_id": str(uuid.uuid4())},
        timeout=20,
    )
    check("anonymous POST is 401", anon.status_code == 401, str(anon.status_code))

    # -- 3. create real inputs ---------------------------------------------
    print("\n3. Creating a vehicle, ticket and custody session")
    plate = make_plate()
    created = requests.post(
        f"{BASE}/api/v1/vehicles",
        json={"plate_number": plate, "make": "Maruti", "model": "Swift", "colour": "White"},
        headers=customer,
        timeout=20,
    )
    check(f"vehicle {plate} created", created.status_code == 201, str(created.status_code))
    if created.status_code != 201:
        print(f"      server said: {created.text[:300]}")
        return 1
    vehicle_id = created.json()["data"]["vehicle"]["id"]

    issued = requests.post(
        f"{BASE}/api/v1/tickets", json={"vehicle_id": vehicle_id}, headers=operator, timeout=20
    )
    check("ticket issued", issued.status_code == 201, str(issued.status_code))
    ticket_id = issued.json()["data"]["ticket"]["id"]

    # A zone is required at check-in, and supplying one is what makes the
    # historical-zone feature a real measurement rather than a default.
    zones = requests.get(f"{BASE}/api/v1/parking-zones", headers=operator, timeout=20)
    zone_list = (zones.json().get("data") or {}).get("items") or zones.json().get("data") or []
    zone_id = zone_list[0]["id"] if zone_list else None
    check("a parking zone is available", zone_id is not None,
          f"{len(zone_list)} zone(s)")

    checked_in = requests.post(
        f"{BASE}/api/v1/tickets/{ticket_id}/check-in",
        json={"odometer_reading": 12_480, "fuel_level_percent": 62, "zone_id": zone_id},
        headers=operator,
        timeout=20,
    )
    check("ticket checked in", checked_in.status_code == 200, str(checked_in.status_code))
    if checked_in.status_code != 200:
        print(f"      server said: {checked_in.text[:300]}")
        return 1
    # The check-in response nests the session inside the ticket.
    custody_id = checked_in.json()["data"]["ticket"]["custody_session"]["id"]
    print(f"      custody session {custody_id} in zone {zone_id}")

    # -- 4. predict (versioned) -------------------------------------------
    print("\n4. POST /api/v1/retrieval/predict")
    predicted = requests.post(
        f"{BASE}/api/v1/retrieval/predict",
        json={"custody_session_id": custody_id},
        headers=customer,
        timeout=20,
    )
    check("prediction accepted", predicted.status_code == 201, str(predicted.status_code))
    data = predicted.json()["data"]

    required = {
        "estimated_retrieval_time": "estimated retrieval time",
        "estimated_minutes": "estimated minutes",
        "queue_position": "queue position",
        "recommended_dispatch_time": "recommended dispatch time",
    }
    for field, label in required.items():
        check(f"response includes {label}", field in data, str(data.get(field)))

    check("estimated_minutes is a positive int",
          isinstance(data.get("estimated_minutes"), int) and data["estimated_minutes"] >= 1,
          str(data.get("estimated_minutes")))
    check("queue_position >= 1", (data.get("queue_position") or 0) >= 1,
          str(data.get("queue_position")))
    check("recommended_dispatch is human-readable", bool(data.get("recommended_dispatch")),
          str(data.get("recommended_dispatch")))
    check("declares synthetic training data", data.get("trained_on_synthetic_data") is True)
    check("data_basis names the synthetic source",
          "synthetic" in str(data.get("data_basis", "")).lower())
    check("an explanation is returned", bool(data.get("reason")))
    check("per-feature contributions returned", bool(data.get("contributions")))

    print(f"\n      Estimated Retrieval  : {data['estimated_minutes']} minutes")
    print(f"      Queue Position       : {data['queue_position']}")
    print(f"      Recommended Dispatch : {data['recommended_dispatch']}")
    print(f"      Ready at             : {data['estimated_retrieval_time']}")
    print(f"      Confidence           : {data['confidence']}")

    # -- 5. read it back, both prefixes -----------------------------------
    print("\n5. GET /api/retrieval/{custody_id} at both prefixes")
    for prefix in ("/api/v1/retrieval", "/api/retrieval"):
        got = requests.get(f"{BASE}{prefix}/{custody_id}", headers=customer, timeout=20)
        check(f"GET {prefix}/<id> is 200", got.status_code == 200, str(got.status_code))
        if got.status_code == 200:
            stored = got.json()["data"]
            check(
                f"{prefix}: stored minutes match",
                stored.get("predicted_wait_minutes") == data["estimated_minutes"],
                f"{stored.get('predicted_wait_minutes')} vs {data['estimated_minutes']}",
            )
            check(f"{prefix}: declares synthetic data",
                  stored.get("trained_on_synthetic_data") is True)

    # -- 6. unversioned predict alias (the brief's path) -------------------
    print("\n6. The unversioned alias the brief specifies")
    aliased = requests.post(
        f"{BASE}/api/retrieval/predict",
        json={"custody_session_id": custody_id},
        headers=customer,
        timeout=20,
    )
    check("POST /api/retrieval/predict works", aliased.status_code == 201,
          str(aliased.status_code))
    if aliased.status_code == 201:
        again = aliased.json()["data"]
        check("second estimate is a distinct row",
              again.get("prediction_id") != data.get("prediction_id"))
        # Proves persistence rather than just a new id: reading the session back
        # must return the SECOND estimate. Before the service committed its
        # transaction this returned 404, because neither row had been written.
        reread = requests.get(
            f"{BASE}/api/retrieval/{custody_id}", headers=customer, timeout=20
        )
        check("the session's latest estimate is the second one",
              reread.status_code == 200
              and reread.json()["data"].get("id") == again.get("prediction_id"),
              str(reread.status_code))

    # -- 7. what-if mode ---------------------------------------------------
    print("\n7. What-if scoring without a custody session")
    features = {
        "queue_size": 5, "queue_position": 4, "active_valets": 3, "zone_capacity": 30,
        "zone_occupancy_percent": 80.0, "hour_sin": -0.5, "hour_cos": -0.866,
        "dow_sin": 0.975, "dow_cos": -0.223, "historical_zone_mean_minutes": 9.0,
        "vehicle_flagged": 0,
    }
    whatif = requests.post(
        f"{BASE}/api/retrieval/predict", json={"features": features}, headers=operator, timeout=20
    )
    check("what-if scored", whatif.status_code == 200, str(whatif.status_code))
    if whatif.status_code == 200:
        body = whatif.json()["data"]
        check("what-if mode is labelled", body.get("mode") == "what_if")
        check("what-if is not persisted", body.get("persisted") is False)
        print(f"      queue of 5, 4th in line -> {body['estimated_minutes']} minutes")

    # -- 8. validation -----------------------------------------------------
    print("\n8. Validation")
    partial = dict(features)
    del partial["queue_position"]
    refused = requests.post(
        f"{BASE}/api/retrieval/predict", json={"features": partial}, headers=operator, timeout=20
    )
    check("incomplete features are 422", refused.status_code == 422, str(refused.status_code))
    if refused.status_code == 422:
        details = refused.json()["error"].get("details", {})
        check("the missing field is named",
              details.get("missing_features") == ["queue_position"],
              str(details.get("missing_features")))

    neither = requests.post(
        f"{BASE}/api/retrieval/predict", json={}, headers=operator, timeout=20
    )
    check("empty payload is 422", neither.status_code == 422, str(neither.status_code))

    both = requests.post(
        f"{BASE}/api/retrieval/predict",
        json={"custody_session_id": custody_id, "features": features},
        headers=operator,
        timeout=20,
    )
    check("supplying both is 422", both.status_code == 422, str(both.status_code))

    missing = requests.post(
        f"{BASE}/api/retrieval/predict",
        json={"custody_session_id": str(uuid.uuid4())},
        headers=operator,
        timeout=20,
    )
    check("unknown custody session is 404", missing.status_code == 404,
          str(missing.status_code))

    # -- 9. the estimate moves with the queue -----------------------------
    print("\n9. A busier queue produces a longer estimate")
    baseline_minutes = data["estimated_minutes"]
    others = []
    for index in range(3):
        other_plate = make_plate()
        other = requests.post(
            f"{BASE}/api/v1/vehicles",
            json={"plate_number": other_plate, "make": "Hyundai", "model": "i20"},
            headers=customer,
            timeout=20,
        )
        if other.status_code != 201:
            continue
        other_vehicle = other.json()["data"]["vehicle"]["id"]
        ticket = requests.post(
            f"{BASE}/api/v1/tickets", json={"vehicle_id": other_vehicle},
            headers=operator, timeout=20,
        )
        if ticket.status_code != 201:
            continue
        check_in = requests.post(
            f"{BASE}/api/v1/tickets/{ticket.json()['data']['ticket']['id']}/check-in",
            json={
                "odometer_reading": 5000 + index,
                "fuel_level_percent": 50,
                "zone_id": zone_id,
            },
            headers=operator,
            timeout=20,
        )
        if check_in.status_code != 200:
            continue
        other_session = check_in.json()["data"]["ticket"]["custody_session"]["id"]
        # Checking a car in does not put it in the retrieval queue; it has to
        # actually ask for the car back. Without this the queue stayed at 1 and
        # the growth assertion passed trivially.
        requests.patch(
            f"{BASE}/api/v1/custody-sessions/{other_session}",
            json={"status": "RETRIEVAL_REQUESTED"},
            headers=operator,
            timeout=20,
        )
        others.append(other_session)

    busy = requests.post(
        f"{BASE}/api/retrieval/predict",
        json={"custody_session_id": custody_id},
        headers=customer,
        timeout=20,
    )
    check(f"{len(others)} other retrievals were queued", len(others) == 3,
          f"{len(others)} queued")
    if busy.status_code == 201:
        busy_data = busy.json()["data"]
        check(
            f"estimate grew from {baseline_minutes} to {busy_data['estimated_minutes']} min",
            busy_data["estimated_minutes"] > baseline_minutes,
            "strictly greater, not merely equal",
        )
        check(
            f"queue position moved behind {len(others)} others",
            busy_data.get("queue_position", 0) > 1,
            str(busy_data.get("queue_position")),
        )
        check(
            "dispatch is now deferred rather than immediate",
            busy_data.get("recommended_dispatch") != "Now",
            str(busy_data.get("recommended_dispatch")),
        )
        print(f"      Estimated Retrieval  : {busy_data['estimated_minutes']} minutes")
        print(f"      Queue Position       : {busy_data['queue_position']}")
        print(f"      Recommended Dispatch : {busy_data['recommended_dispatch']}")

    # -- summary ----------------------------------------------------------
    print("\n" + "=" * 74)
    if failures:
        print(f"  {len(failures)} CHECK(S) FAILED:")
        for name in failures:
            print(f"    - {name}")
        print("=" * 74)
        return 1
    print("  ALL CHECKS PASSED")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Capture the remaining endpoint shapes: feed read/resolve and finding verification.

The earlier probe read the feed id from `data.alert.alert_id`, but the response
is flat (`data.alert_id`), so those probes never ran. This one also locates a
persisted damage detection from the seeded demo data, because verification needs
a stored finding rather than the demo endpoint's transient ones.
"""

import json
import os
import sys

import requests

BASE = "http://127.0.0.1:5000"
DEMO_PASSWORD = "Demo#Passw0rd"

out = {}


def login(email, password):
    r = requests.post(
        f"{BASE}/api/v1/auth/login", json={"email": email, "password": password}, timeout=30
    )
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def record(name, r):
    try:
        body = r.json()
    except ValueError:
        body = r.text[:300]
    out[name] = {"status": r.status_code, "body": body}
    print(f"  {r.status_code}  {name}", file=sys.stderr)
    return body


def main():
    tok = {
        "admin": login(os.environ["SEED_ADMIN_EMAIL"], os.environ["SEED_ADMIN_PASSWORD"]),
        "customer": login("customer.aisha@example.com", DEMO_PASSWORD),
        "operator": login("valet.suresh@example.com", DEMO_PASSWORD),
        "manager": login("manager.karan@example.com", DEMO_PASSWORD),
    }

    def hdr(role):
        return {"Authorization": f"Bearer {tok[role]}"}

    # ---- find a persisted damage detection via the seeded condition report ----
    tickets = requests.get(
        f"{BASE}/api/v1/tickets?limit=10", headers=hdr("admin"), timeout=30
    ).json()["data"]["items"]

    detection_id = None
    for t in tickets:
        sess = (t.get("custody_session") or {}).get("id")
        if not sess:
            continue
        report = requests.get(
            f"{BASE}/api/v1/inspections/{sess}", headers=hdr("admin"), timeout=30
        )
        if report.status_code != 200:
            continue
        data = report.json()["data"]
        for stage in ("pickup", "drop_off"):
            block = data.get(stage) or {}
            for det in block.get("detections") or []:
                detection_id = det.get("id")
                if detection_id:
                    break
            if detection_id:
                break
        if detection_id:
            break
    print(f"  detection={detection_id}", file=sys.stderr)

    if detection_id:
        record(
            "PATCH /inspections/detections/<id>/verification",
            requests.patch(
                f"{BASE}/api/v1/inspections/detections/{detection_id}/verification",
                headers=hdr("manager"),
                json={"status": "CONFIRMED", "notes": "Confirmed during documentation pass."},
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

    # ---- centralized feed: create, detail, read, read-all, resolve ----
    created = record(
        "POST /feed",
        requests.post(
            f"{BASE}/api/v1/feed",
            headers=hdr("manager"),
            json={
                "type": "RETRIEVAL_READY",
                "severity": "LOW",
                "message": "Documentation probe alert (shapes pass).",
                "audiences": ["CUSTOMER", "VALET_MANAGER"],
            },
            timeout=30,
        ),
    )
    # The response is flat: data.alert_id, not data.alert.alert_id.
    feed_id = created.get("data", {}).get("alert_id")
    print(f"  feed_id={feed_id}", file=sys.stderr)

    if feed_id:
        record(
            "GET /feed/<id>",
            requests.get(f"{BASE}/api/v1/feed/{feed_id}", headers=hdr("manager"), timeout=30),
        )
        record(
            "POST /feed/<id>/read",
            requests.post(
                f"{BASE}/api/v1/feed/{feed_id}/read", headers=hdr("manager"), timeout=30
            ),
        )
        record(
            "POST /feed/read-all",
            requests.post(f"{BASE}/api/v1/feed/read-all", headers=hdr("manager"), timeout=30),
        )
        record(
            "ERR feed resolve by customer (403)",
            requests.post(
                f"{BASE}/api/v1/feed/{feed_id}/resolve",
                headers=hdr("customer"),
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
        record(
            "POST /feed/<id>/resolve (dismiss)",
            requests.post(
                f"{BASE}/api/v1/feed/{feed_id}/resolve",
                headers=hdr("manager"),
                json={"dismiss": True, "notes": "No action required."},
                timeout=30,
            ),
        )

    with open("/home/user/scratch/api-docs/api_write_shapes2.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"wrote {len(out)} probes", file=sys.stderr)


if __name__ == "__main__":
    main()

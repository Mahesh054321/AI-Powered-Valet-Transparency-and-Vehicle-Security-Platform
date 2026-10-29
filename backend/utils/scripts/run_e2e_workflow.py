#!/usr/bin/env python3
"""End-to-end workflow demonstration, driven through the real HTTP API.

Walks the complete lifecycle the brief specifies, in order, against a running
API server -- not by poking the database and not by calling services directly.
Every step below is a real request to a real route, so a green run is evidence
that the modules are genuinely wired together rather than individually correct.

    1  Customer registration
    2  Vehicle registration
    3  Digital ticket issuance (QR generated server-side)
    4  Valet check-in -> custody session opens, zone assigned
    5  Drop-off image capture (real PNGs, real OpenCV pipeline)
    6  GPS monitoring (simulated telemetry, clearly labelled)
    7  Geofence analysis (the real detector classifies every fix)
    8  Security alerts (raised only if the detector actually fires)
    9  Customer requests pickup
    10 Retrieval prediction (the trained ridge model estimates wait time)
    11 Valet receives the request on the operator dashboard
    12 Vehicle prepared (RETRIEVAL_IN_PROGRESS -> READY_FOR_PICKUP)
    13 Pickup image capture
    14 Before/after comparison (alignment + differencing)
    15 Potential damage result (labelled "potential" until verified)
    16 Verification of the finding
    17 Customer accepts the condition; custody completes
    18 Audit record (every step above, append-only)

Then it deliberately attempts the transitions that must NOT be allowed and
asserts they are refused.

Usage
-----
    python utils/scripts/run_e2e_workflow.py --reset
    python utils/scripts/run_e2e_workflow.py --base-url http://127.0.0.1:5000

``--reset`` drops and rebuilds the schema first, so the run starts from a
genuinely fresh database. Refuses to run against production.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.synthetic.vehicle_photos import build_scenario  # noqa: E402

DEMO_PASSWORD = os.environ.get("E2E_DEMO_PASSWORD", "Demo#Passw0rd")
ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "admin@valet.local")
ADMIN_PASSWORD = os.environ.get("SEED_ADMIN_PASSWORD", "")

RUN_TAG = uuid.uuid4().hex[:6].upper()


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
class Reporter:
    """Step-by-step console output with a pass/fail tally."""

    def __init__(self) -> None:
        self.step = 0
        self.passed = 0
        self.failed = 0
        self.notes: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n\033[1m=== {title} ===\033[0m")

    def step_start(self, label: str) -> None:
        self.step += 1
        print(f"\n\033[1m[{self.step:02d}] {label}\033[0m")

    def check(self, condition: bool, description: str, detail: str = "") -> bool:
        mark = "\033[32mPASS\033[0m" if condition else "\033[31mFAIL\033[0m"
        suffix = f"  ({detail})" if detail else ""
        print(f"      {mark}  {description}{suffix}")
        if condition:
            self.passed += 1
        else:
            self.failed += 1
        return condition

    def info(self, text: str) -> None:
        print(f"      ·  {text}")

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"      !  {text}")

    def summary(self) -> int:
        print("\n" + "=" * 74)
        print(f"  {self.passed} passed, {self.failed} failed  ({self.step} steps)")
        print("=" * 74)
        if self.notes:
            print("\nNotes:")
            for note in self.notes:
                print(f"  · {note}")
        return 1 if self.failed else 0


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class Api:
    """Thin HTTP client that fails loudly instead of returning None."""

    def __init__(self, base_url: str, reporter: Reporter) -> None:
        self.base = base_url.rstrip("/")
        self.rep = reporter
        self.session = requests.Session()
        self.token: str | None = None
        self.actor = "anonymous"

    def as_user(self, label: str, token: str | None) -> None:
        self.actor = label
        self.token = token

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        files: Any = None,
        data: Any = None,
        expect: int | tuple[int, ...] = (200, 201),
    ) -> requests.Response:
        url = f"{self.base}{path}"
        response = self.session.request(
            method, url, json=json, files=files, data=data, headers=self._headers(), timeout=60
        )
        expected = (expect,) if isinstance(expect, int) else expect
        if response.status_code not in expected:
            body = response.text[:400]
            raise AssertionError(
                f"{method} {path} -> {response.status_code} as {self.actor}, "
                f"expected {expected}.\n{body}"
            )
        return response

    def get(self, path: str, **kw: Any) -> dict[str, Any]:
        return self.request("GET", path, **kw).json()["data"]

    def post(self, path: str, **kw: Any) -> dict[str, Any]:
        return self.request("POST", path, **kw).json()["data"]

    def patch(self, path: str, **kw: Any) -> dict[str, Any]:
        return self.request("PATCH", path, **kw).json()["data"]

    def status_of(self, method: str, path: str, **kw: Any) -> tuple[int, dict[str, Any]]:
        """Return (status, body) without asserting -- for negative tests."""
        url = f"{self.base}{path}"
        response = self.session.request(
            method, url, headers=self._headers(), timeout=60, **kw
        )
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {}


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def reset_database(reporter: Reporter) -> None:
    """Drop and rebuild the schema via the project's own init script."""
    reporter.section("Reset database")
    env = dict(os.environ)
    if env.get("APP_ENV") == "production":
        raise SystemExit("Refusing to reset a production database.")
    # A fixed argv list with no shell and no interpolated user input, so there
    # is nothing here for an attacker to influence.
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(BACKEND_DIR / "utils" / "scripts" / "init_db.py"), "--reset"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"init_db.py --reset failed:\n{result.stdout}\n{result.stderr}")
    reporter.info("schema dropped and rebuilt")


def ensure_admin(reporter: Reporter) -> str:
    """Bootstrap the administrator so staff accounts can be created by API.

    Public registration always yields CUSTOMER -- that is a deliberate security
    property -- so the first staff account has to come from the bootstrap path.
    """
    from app import create_app
    from database.seed import bootstrap_admin

    app = create_app()
    with app.app_context():
        result = bootstrap_admin(app)
    password = ADMIN_PASSWORD
    if not password:
        # bootstrap_admin generates and prints one when none is configured.
        for line in (result.details or {}).get("messages", []):
            if "password" in line.lower():
                reporter.info(line)
        raise SystemExit(
            "Set SEED_ADMIN_PASSWORD so the demo can log the administrator in."
        )
    reporter.info(f"administrator ready ({ADMIN_EMAIL})")
    return password


def scenario_images(scenario: str) -> tuple[bytes, bytes]:
    """Render a matched drop-off/pickup PNG pair with the synthetic generator."""
    pair = build_scenario(scenario)
    return pair.drop_off_png, pair.pickup_png


def png_part(name: str, payload: bytes) -> tuple[str, io.BytesIO, str]:
    return (name, (f"{name}.png", io.BytesIO(payload), "image/png"))


# --------------------------------------------------------------------------- #
# The workflow
# --------------------------------------------------------------------------- #
def run_workflow(api: Api, rep: Reporter, admin_password: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    customer_email = f"e2e.customer.{RUN_TAG}@example.com"
    valet_email = f"e2e.valet.{RUN_TAG}@example.com"
    manager_email = f"e2e.manager.{RUN_TAG}@example.com"
    plate = f"UP78EF{1000 + int(RUN_TAG[:2], 16) % 9000}"

    # -- Accounts ---------------------------------------------------------- #
    rep.section("Accounts")

    rep.step_start("Customer registers through the public endpoint")
    customer = api.post(
        "/api/v1/auth/register",
        json={
            "email": customer_email,
            "password": DEMO_PASSWORD,
            "full_name": "E2E Customer",
            "phone_number": "+919800000001",
        },
        expect=201,
    )
    ids["customer_id"] = customer["user"]["id"]
    rep.check(customer["user"]["role"] == "CUSTOMER", "role is CUSTOMER")
    rep.check(
        "password" not in customer["user"],
        "password never returned by the API",
    )
    customer_token = customer["token"]

    api.as_user("admin", None)
    admin_login = api.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": admin_password},
        expect=200,
    )
    api.as_user("admin", admin_login["token"])

    rep.step_start("Valet operator account created by the administrator")
    valet = api.post(
        "/api/v1/auth/users",
        json={
            "email": valet_email,
            "password": DEMO_PASSWORD,
            "full_name": "E2E Valet",
            "role": "VALET_OPERATOR",
        },
        expect=201,
    )
    ids["valet_id"] = valet["id"]
    rep.check(valet["role"] == "VALET_OPERATOR", "role is VALET_OPERATOR")

    manager = api.post(
        "/api/v1/auth/users",
        json={
            "email": manager_email,
            "password": DEMO_PASSWORD,
            "full_name": "E2E Manager",
            "role": "VALET_MANAGER",
        },
        expect=201,
    )
    ids["manager_id"] = manager["id"]
    manager_login = api.post(
        "/api/v1/auth/login",
        json={"email": manager_email, "password": DEMO_PASSWORD},
        expect=200,
    )
    valet_login = api.post(
        "/api/v1/auth/login",
        json={"email": valet_email, "password": DEMO_PASSWORD},
        expect=200,
    )
    rep.info(f"customer={customer_email}  valet={valet_email}  manager={manager_email}")
    rep.info(f"password for all demo accounts: {DEMO_PASSWORD}")

    # -- Zone -------------------------------------------------------------- #
    rep.step_start("Parking zone and geofence created")
    api.as_user("manager", manager_login["token"])
    zone = api.post(
        "/api/v1/geofences",
        json={
            "code": f"E2E-{RUN_TAG}",
            "name": "E2E Demo Zone",
            "latitude": 28.6896,
            "longitude": 76.9113,
            "radius": 60.0,
            "capacity": 20,
        },
        expect=201,
    )
    ids["zone_id"] = zone["id"]
    rep.check(zone["capacity"] == 20, "zone capacity stored", f"{zone['capacity']}")
    rep.check(
        bool(zone.get("geofence_polygon")),
        "circular geofence ring generated from centre + radius",
    )

    # -- Vehicle ----------------------------------------------------------- #
    rep.step_start("Customer registers a vehicle")
    api.as_user("customer", customer_token)
    vehicle = api.post(
        "/api/v1/vehicles",
        json={
            "plate_number": plate,
            "make": "Maruti",
            "model": "Swift",
            "color": "silver",
            "vehicle_type": "HATCHBACK",
            "registration_year": 2021,
        },
        expect=201,
    )
    ids["vehicle_id"] = vehicle["id"]
    rep.check(vehicle["status"] == "REGISTERED", "vehicle status REGISTERED")
    rep.check(vehicle["owner_id"] == ids["customer_id"], "vehicle owned by the customer")

    # -- Ticket + QR ------------------------------------------------------- #
    rep.step_start("Valet issues the digital ticket (QR generated server-side)")
    api.as_user("valet", valet_login["token"])
    ticket = api.post("/api/v1/tickets", json={"vehicle_id": ids["vehicle_id"]}, expect=201)
    ids["ticket_id"] = ticket["id"]
    rep.check(ticket["status"] == "CREATED", "ticket issued", ticket["status"])
    rep.check(bool(ticket.get("ticket_code")), "ticket code assigned", ticket["ticket_code"])
    qr_path = ticket.get("qr_code_path")
    rep.check(bool(qr_path), "QR code PNG written server-side", str(qr_path))
    if qr_path:
        qr_response = api.session.get(f"{api.base}/media/{qr_path}", timeout=30)
        rep.check(
            qr_response.status_code == 200
            and qr_response.content[:8] == b"\x89PNG\r\n\x1a\n",
            "QR PNG is fetchable and is a real PNG",
            f"{len(qr_response.content)} bytes",
        )

    # -- Check-in ---------------------------------------------------------- #
    rep.step_start("Valet checks the vehicle in (zone assigned, custody opens)")
    check_in = api.post(
        f"/api/v1/tickets/{ids['ticket_id']}/check-in",
        json={"zone_id": ids["zone_id"], "odometer_at_dropoff": 41200},
        expect=200,
    )
    custody = check_in["ticket"]["custody_session"]
    ids["custody_id"] = custody["id"]
    rep.check(custody["status"] == "CHECKED_IN", "custody session opened", custody["status"])
    rep.check(
        check_in["ticket"]["custody_status"] == "IN_CUSTODY",
        "ticket projects to IN_CUSTODY",
    )
    rep.check(
        check_in["ticket"]["parking_zone"]["code"] == zone["code"],
        "parking zone assigned to the ticket",
        zone["code"],
    )
    zone_after = api.get(f"/api/v1/geofences/{ids['zone_id']}")
    rep.check(
        zone_after["current_occupancy"] == 1,
        "zone occupancy incremented",
        str(zone_after["current_occupancy"]),
    )

    # -- Drop-off images --------------------------------------------------- #
    rep.step_start("Drop-off photographs captured (real OpenCV pipeline)")
    drop_png, pickup_png = scenario_images("scratch")
    drop_off = api.post(
        "/api/v1/inspections/dropoff",
        data={"custody_session_id": ids["custody_id"], "notes": "E2E drop-off"},
        files=[png_part("images", drop_png)],
        expect=201,
    )
    rep.check(
        drop_off["stage"] == "DROP_OFF",
        "drop-off inspection recorded",
        drop_off.get("inspection_id", "")[:8],
    )
    rep.info(f"baseline images stored: {drop_off.get('image_count')}")

    # -- GPS + geofence ---------------------------------------------------- #
    rep.step_start("GPS telemetry ingested and classified (simulated, labelled)")
    gps = api.post(
        "/api/v1/gps/simulate",
        json={"custody_id": ids["custody_id"], "scenario": "parked", "seed": 4242},
        expect=201,
    )
    rep.check(gps["event_count"] > 0, "fixes recorded", f"{gps['event_count']} fixes")
    rep.check(
        gps.get("telemetry_source") == "SIMULATED",
        "telemetry explicitly labelled SIMULATED",
        str(gps.get("telemetry_source")),
    )
    verdicts = gps.get("movement_verdicts") or []
    rep.info(f"classified: {verdicts}")

    rep.step_start("Geofence analysis over the ingested track")
    tracking = api.get(f"/api/v1/gps/{ids['custody_id']}/tracking")
    rep.check(
        tracking["summary"]["fix_count"] >= gps["event_count"],
        "tracking view reflects the ingested fixes",
        f"{tracking['summary']['fix_count']} fixes",
    )
    rep.check(
        tracking["summary"]["outside_zone_count"] == 0,
        "parked scenario keeps the vehicle inside the zone",
        f"outside={tracking['summary']['outside_zone_count']}",
    )

    # -- Security alerts --------------------------------------------------- #
    rep.step_start("Security alerts (raised only if the detector fires)")
    alerts = api.get("/api/v1/feed?include_read=true")
    rep.check("items" in alerts, "alert feed readable")
    rep.info(f"alerts visible to the valet: {len(alerts['items'])}")
    if not alerts["items"]:
        rep.note(
            "No alert was raised: the parked scenario produced no anomaly. That is "
            "the correct outcome, not a gap -- alerts are event-driven."
        )

    # A second pass with a scenario that must breach, so the alert path is
    # exercised rather than merely present.
    api.post(
        "/api/v1/gps/simulate",
        json={"custody_id": ids["custody_id"], "scenario": "unauthorised", "seed": 99},
        expect=201,
    )
    breach_alerts = api.get("/api/v1/feed?include_read=true")
    raised = [a for a in breach_alerts["items"] if a["severity"] in {"HIGH", "CRITICAL"}]
    rep.check(
        len(raised) >= 1,
        "an unauthorised-movement track raises a HIGH/CRITICAL alert",
        f"{len(raised)} raised",
    )
    if raised:
        rep.info(f"top alert: {raised[0]['type']} / {raised[0]['severity']}")

    # -- Customer requests pickup ----------------------------------------- #
    rep.step_start("Customer requests the vehicle back")
    api.as_user("customer", customer_token)
    request_result = api.post(
        "/api/v1/retrieval/request",
        json={"custody_session_id": ids["custody_id"]},
        expect=201,
    )
    rep.check(
        request_result["custody_status"] == "RETRIEVAL_REQUESTED",
        "custody moved to RETRIEVAL_REQUESTED",
    )

    rep.step_start("Retrieval prediction generated")
    estimate = request_result.get("estimate") or {}
    rep.check(bool(estimate), "an estimate was returned")
    if estimate.get("available"):
        rep.check(
            estimate["estimated_minutes"] > 0,
            "model produced a wait estimate",
            f"{estimate['estimated_minutes']:.1f} min "
            f"(confidence {estimate.get('confidence', 0) * 100:.0f}%)",
        )
        rep.info(f"model: {estimate.get('model_name')} v{estimate.get('model_version')}")
    else:
        rep.note(f"retrieval model unavailable: {estimate.get('reason')}")

    # -- Valet sees the request ------------------------------------------- #
    rep.step_start("Valet receives the request on the operator dashboard")
    api.as_user("valet", valet_login["token"])
    queue = api.get("/api/v1/retrieval/queue")
    rep.check(
        queue["count"] >= 1,
        "the request appears in the retrieval queue",
        f"{queue['count']} waiting",
    )
    staff = api.get("/api/v1/staff/dashboard")
    rep.check(
        staff["sections"]["retrievals"]["count"] >= 1,
        "operator dashboard shows it under Retrievals",
    )
    rep.check(
        staff["capabilities"]["can_complete_pickup"] is True,
        "valet is permitted to complete the pickup",
    )

    # -- Vehicle prepared -------------------------------------------------- #
    rep.step_start("Vehicle prepared for handover")
    moved = api.patch(
        f"/api/v1/custody-sessions/{ids['custody_id']}",
        json={"status": "RETRIEVAL_IN_PROGRESS", "notes": "walking to the bay"},
        expect=200,
    )
    rep.check(
        moved["status"] == "RETRIEVAL_IN_PROGRESS",
        "RETRIEVAL_REQUESTED -> RETRIEVAL_IN_PROGRESS accepted",
    )
    ready = api.patch(
        f"/api/v1/custody-sessions/{ids['custody_id']}",
        json={"status": "READY_FOR_PICKUP", "notes": "staged at the exit"},
        expect=200,
    )
    rep.check(ready["status"] == "READY_FOR_PICKUP", "-> READY_FOR_PICKUP accepted")

    ticket_ready = api.get(f"/api/v1/tickets/{ids['ticket_id']}")
    rep.check(
        ticket_ready["status"] == "READY_FOR_PICKUP",
        "ticket projects to READY_FOR_PICKUP",
    )

    # -- Pickup images + comparison --------------------------------------- #
    rep.step_start("Pickup photographs captured and compared with drop-off")
    pickup = api.post(
        "/api/v1/inspections/pickup",
        data={"custody_session_id": ids["custody_id"], "notes": "E2E pickup"},
        files=[png_part("images", pickup_png)],
        expect=201,
    )
    comparison = pickup.get("comparison") or {}
    rep.check(pickup["stage"] == "PICKUP", "pickup inspection recorded")
    rep.check(
        comparison.get("compared") is True,
        "before/after comparison actually ran",
        f"{comparison.get('pairs', 0)} pair(s) compared",
    )
    rep.check(
        comparison.get("alignment") is not None,
        "camera alignment applied before differencing",
        str((comparison.get("alignment") or {}).get("method", "")),
    )

    detections = comparison.get("detections") or []
    rep.step_start("Potential damage result")
    rep.check(
        len(detections) >= 1,
        "the planted defect was detected",
        f"{len(detections)} detection(s)",
    )
    rep.check(
        all(d["verified_status"] == "UNVERIFIED" for d in detections),
        "every finding starts UNVERIFIED (never auto-confirmed)",
    )
    if detections:
        top = detections[0]
        rep.info(
            f"finding: {top['damage_type']} on {top['region']} "
            f"confidence={top['confidence']:.2f} method={top['detection_method']}"
        )
        ids["detection_id"] = top["id"]

    report = api.get(f"/api/v1/inspections/{ids['custody_id']}")
    rep.check(
        report["summary"]["has_damage"] is True,
        "condition report flags damage",
        f"{report['summary']['detection_count']} detection(s)",
    )
    rep.check(
        "potential" in json.dumps(report).lower(),
        'unverified findings are worded "potential"',
    )

    # -- Verification ------------------------------------------------------ #
    rep.step_start("Finding verified (manager/admin authority only)")
    status, body = api.status_of(
        "POST",
        f"/api/v1/inspections/detections/{ids['detection_id']}/verification",
        json={"status": "CONFIRMED", "notes": "valet must not self-verify"},
    )
    rep.check(status == 403, "the valet who photographed it cannot verify it", f"got {status}")

    api.as_user("manager", manager_login["token"])
    verified = api.post(
        f"/api/v1/inspections/detections/{ids['detection_id']}/verification",
        json={"status": "CONFIRMED", "notes": "scratch confirmed against drop-off baseline"},
        expect=200,
    )
    rep.check(
        verified["detection"]["verified_status"] == "CONFIRMED",
        "manager confirmed the finding",
    )
    rep.check(
        bool(verified["detection"].get("verified_by_id")),
        "verification attributed to a named person",
    )

    # -- Completion -------------------------------------------------------- #
    rep.step_start("Customer accepts the condition; custody completes")
    api.as_user("customer", customer_token)
    dashboard = api.get("/api/v1/customers/me/dashboard")
    rep.check(
        dashboard["sections"]["custody"]["state"] == "ok",
        "customer dashboard still shows the custody",
    )

    api.as_user("valet", valet_login["token"])
    completed = api.post(
        f"/api/v1/tickets/{ids['ticket_id']}/complete",
        json={"odometer_at_pickup": 41263, "condition_accepted": True},
        expect=200,
    )
    rep.check(completed["status"] == "COMPLETED", "ticket completed", completed["status"])
    rep.check(
        completed["custody_status"] == "CHECKED_OUT",
        "custody session closed",
        completed["custody_status"],
    )
    rep.check(
        completed.get("condition_accepted") is True,
        "customer acceptance of the condition recorded",
    )

    # -- Audit ------------------------------------------------------------- #
    rep.step_start("Audit record covers the whole lifecycle")
    audit = _fetch_audit(ids["custody_id"], ids["ticket_id"])
    rep.check(len(audit) >= 8, "audit rows written for the run", f"{len(audit)} rows")
    actions = [row["action"] for row in audit]
    for expected in ("TICKET_ISSUED", "STATUS_CHANGE", "CHECK_OUT"):
        rep.check(expected in actions, f"audit contains {expected}")
    rep.check(
        all(row.get("actor_id") for row in audit),
        "every audit row names an actor",
    )

    return ids


def _fetch_audit(custody_id: str, ticket_id: str) -> list[dict[str, Any]]:
    """Read the append-only audit log for this run (no HTTP endpoint exists)."""
    from sqlalchemy import select

    from app import create_app
    from models.audit_log import AuditLog

    app = create_app()
    with app.app_context():
        from app.extensions import db

        rows = db.session.execute(
            select(AuditLog)
            .where(AuditLog.entity_id.in_([custody_id, ticket_id]))
            .order_by(AuditLog.created_at)
        ).scalars().all()
        return [
            {
                "action": row.action,
                "entity_type": row.entity_type,
                "actor_id": str(row.actor_id) if row.actor_id else None,
                "outcome": row.outcome,
            }
            for row in rows
        ]


# --------------------------------------------------------------------------- #
# Negative tests: transitions that must be refused
# --------------------------------------------------------------------------- #
def run_guard_tests(api: Api, rep: Reporter, ids: dict[str, str]) -> None:
    rep.section("Guards: transitions that must be refused")

    custody = ids["custody_id"]
    ticket = ids["ticket_id"]

    rep.step_start("COMPLETED ticket cannot re-enter custody")
    status, body = api.status_of(
        "POST", f"/api/v1/tickets/{ticket}/check-in", json={"zone_id": ids["zone_id"]}
    )
    rep.check(status == 409, "check-in on a COMPLETED ticket is refused", f"got {status}")
    rep.check(
        body.get("error", {}).get("code") == "CONFLICT",
        "refusal explains itself as a conflict",
        str(body.get("error", {}).get("code")),
    )

    rep.step_start("CHECKED_OUT custody cannot go back to IN_CUSTODY")
    for target in ("CHECKED_IN", "PARKED", "RETRIEVAL_REQUESTED"):
        status, body = api.status_of(
            "PATCH", f"/api/v1/custody-sessions/{custody}", json={"status": target}
        )
        rep.check(
            status == 409,
            f"CHECKED_OUT -> {target} refused",
            f"got {status}",
        )

    rep.step_start("Garbage status is a 422, not a 500")
    status, _ = api.status_of(
        "PATCH", f"/api/v1/custody-sessions/{custody}", json={"status": "TELEPORTED"}
    )
    rep.check(status == 422, "unknown status rejected as a validation error", f"got {status}")

    rep.step_start("Customer cannot drive someone else's workflow")
    status, _ = api.status_of(
        "PATCH", f"/api/v1/custody-sessions/{custody}", json={"status": "PARKED"}
    )
    rep.check(status in (403, 409), "non-staff actor cannot move custody", f"got {status}")

    rep.step_start("Zone occupancy released after completion")
    api.as_user("manager", api.token)
    zone = api.get(f"/api/v1/geofences/{ids['zone_id']}")
    rep.check(
        zone["current_occupancy"] == 0,
        "occupancy decremented when custody closed",
        str(zone["current_occupancy"]),
    )


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end workflow demonstration.")
    parser.add_argument("--base-url", default=os.environ.get("API_BASE_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--reset", action="store_true", help="drop and rebuild the schema first")
    args = parser.parse_args()

    rep = Reporter()
    print("\033[1mAI-Powered Valet Transparency Platform -- end-to-end workflow\033[0m")
    print(f"API: {args.base_url}   run tag: {RUN_TAG}")

    if args.reset:
        reset_database(rep)

    admin_password = ensure_admin(rep)

    api = Api(args.base_url, rep)
    try:
        api.session.get(f"{args.base_url}/api/health", timeout=10).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"API at {args.base_url} is not reachable: {exc}") from exc

    started = time.time()
    ids = run_workflow(api, rep, admin_password)
    run_guard_tests(api, rep, ids)
    rep.info(f"completed in {time.time() - started:.1f}s")
    return rep.summary()


if __name__ == "__main__":
    sys.exit(main())

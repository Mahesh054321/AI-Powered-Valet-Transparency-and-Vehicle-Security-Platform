"""Helpers for the workflow tests: users, zones, vehicles, tokens, images.

Kept in one module so the vehicle and ticket suites build their fixtures the
same way. Nothing here fakes a result - every row is a real database row and
every token is signed by the real ``create_token``.
"""

from __future__ import annotations

import io
import secrets
import string
import uuid

import cv2
import numpy as np
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from models.enums import VehicleStatus, ZoneStatus
from models.parking_zone import ParkingZone
from models.user import User
from models.vehicle import Vehicle
from utils.security import Role, create_token

PASSWORD = "Str0ng#Passw0rd"

#: A small square around a plausible Mathura coordinate. The exact shape does
#: not matter to these tests; the column is NOT NULL and must be a ring.
SAMPLE_POLYGON = [
    [77.6730, 27.4920],
    [77.6760, 27.4920],
    [77.6760, 27.4950],
    [77.6730, 27.4950],
    [77.6730, 27.4920],
]


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def make_user(role: str = Role.CUSTOMER, *, email: str | None = None, full_name: str | None = None):
    """Create and flush a real account."""
    user = User(
        email=email or f"{unique(role.value.lower())}@example.com",
        full_name=full_name or f"Test {role.value.title()}",
        role=str(role),
        status="ACTIVE",
        is_active=True,
    )
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    return user


def make_zone(*, code: str | None = None, capacity: int = 5, status: str = ZoneStatus.ACTIVE):
    """Create a parkable zone with a geofence ring."""
    zone = ParkingZone(
        code=code or unique("ZONE").upper()[:32],
        name=f"Zone {uuid.uuid4().hex[:4].upper()}",
        geofence_polygon=SAMPLE_POLYGON,
        centre_latitude=27.4935,
        centre_longitude=77.6745,
        capacity=capacity,
        current_occupancy=0,
        status=str(status),
    )
    db.session.add(zone)
    db.session.flush()
    return zone


def _random_plate() -> str:
    """A registration the validator accepts, drawn at random.

    Two letters, two digits, two letters, four digits. The series letters are
    randomised too: four digits alone give 9,000 combinations, and committed
    rows survive between tests in this suite.
    """
    series = "".join(secrets.choice(string.ascii_uppercase) for _ in range(2))
    return f"UP85{series}{secrets.randbelow(9000) + 1000}"


def make_vehicle(owner_id, *, plate: str | None = None, status: str = VehicleStatus.REGISTERED):
    """Register a vehicle directly, bypassing the API."""
    if plate is not None:
        return _add_vehicle(owner_id, plate, status)

    # The test database is never reset, so it accumulates vehicles across every
    # run and a random plate eventually collides with one already there -- which
    # surfaced as a spurious failure in an unrelated pagination test. Rather
    # than widen the plate space and wait for it to happen again, retry: the
    # unique index is the authority on what is taken, and asking it is cheaper
    # than reasoning about the collision probability.
    for _ in range(8):
        try:
            return _add_vehicle(owner_id, _random_plate(), status)
        except IntegrityError:
            db.session.rollback()
    raise AssertionError("could not allocate a unique plate after 8 attempts")


def _add_vehicle(owner_id, plate: str, status: str) -> Vehicle:
    vehicle = Vehicle(
        owner_id=owner_id,
        plate_number=plate,
        normalized_plate=Vehicle.normalize_plate(plate),
        make="Tata",
        model="Nexon",
        color="Silver",
        status=str(status),
    )
    db.session.add(vehicle)
    db.session.flush()
    return vehicle


def bearer(user: User) -> dict:
    """A real signed access token for ``user``."""
    token, _ = create_token(user_id=str(user.id), role=user.role, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def png_bytes(width: int = 96, height: int = 72) -> bytes:
    """A genuine PNG, encoded by OpenCV rather than hand-crafted bytes.

    The default size sits above ``MIN_IMAGE_PIXELS`` (64) on both sides. It used
    to be 64x48, which the upload policy rejects -- enforcing that policy is what
    surfaced it, and a fixture that violates the documented rules is a fixture
    waiting to hide a real rejection.
    """
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:, :] = (40, 120, 200)
    succeeded, buffer = cv2.imencode(".png", canvas)
    assert succeeded
    return buffer.tobytes()


def file_part(filename: str = "photo.jpg", blob: bytes | None = None):
    """A multipart file part, shaped the way a browser sends one.

    Werkzeug only treats ``(stream, filename)`` as an uploaded *file*;
    ``(bytes, filename)`` becomes a plain form field, which would make the
    server correctly report "no image was uploaded" and hide the behaviour
    under test. Wrapping in ``BytesIO`` is what makes the test honest.
    """
    return (io.BytesIO(blob if blob is not None else png_bytes()), filename)

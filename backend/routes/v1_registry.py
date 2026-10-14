"""Version 1 of the domain API.

Blueprints are attached here as they are implemented:

    auth         registration, login, logout, profile, staff creation   Phase 2 (done)
    vehicles     customer vehicle CRUD                                   Phase 3 (done)
    tickets      digital valet ticket + QR                               Phase 4 (done)
    custody      custody session lifecycle                               Phase 4 (done)
    directory    customer lookup + parking zones (check-in support)      Phase 4 (done)
    uploads      image storage, returning validated references           Phase 4 (done)
    inspections  photo capture, OpenCV comparison, manual verification   Phase 5-6 (done)
    alerts       security alerts                                         Phase 7 (done)
    geofences    zones and breach detection                              Phase 7 (done)
    telemetry    GPS/telemetry events                                    Phase 7 (done)
    retrieval    retrieval prediction                                    Phase 9b (done)
    reports      manager/admin reporting                                 Phase 10
    audit        immutable audit log                                     Phase 10
"""

from __future__ import annotations

from flask import Blueprint

from routes.alert_center import create_alert_center_blueprint
from routes.alerts import create_alerts_blueprint
from routes.audit import create_audit_blueprint
from routes.auth import create_auth_blueprint
from routes.dashboard import create_dashboard_blueprint
from routes.directory import create_directory_blueprint
from routes.geofences import create_geofences_blueprint
from routes.gps import create_gps_blueprint
from routes.inspections import create_inspections_blueprint
from routes.manager import create_manager_blueprint
from routes.ml import create_ml_blueprint
from routes.retrieval import create_retrieval_blueprint
from routes.staff import create_staff_blueprint
from routes.tickets import create_custody_blueprint, create_tickets_blueprint
from routes.uploads import create_uploads_blueprint
from routes.vehicles import create_vehicles_blueprint


def register_v1_routes() -> Blueprint:
    """Build a fresh versioned blueprint with every feature blueprint attached.

    A new ``Blueprint`` is created per call rather than reusing a module-level
    instance: Flask forbids re-registering a blueprint that has already been
    attached to an application, and the test suite builds several application
    instances per session. The same reasoning applies to ``auth``, which is
    additionally mounted unversioned in ``routes/__init__.py``.
    """
    api_v1 = Blueprint("api_v1", __name__)

    api_v1.register_blueprint(create_auth_blueprint(), url_prefix="/auth")
    api_v1.register_blueprint(create_vehicles_blueprint(), url_prefix="/vehicles")
    api_v1.register_blueprint(create_tickets_blueprint(), url_prefix="/tickets")
    api_v1.register_blueprint(create_custody_blueprint(), url_prefix="/custody-sessions")
    api_v1.register_blueprint(create_directory_blueprint())
    # Mounted under /customers so the path reads as /customers/me/dashboard.
    # `/me` is deliberate: there is no customer_id to tamper with.
    api_v1.register_blueprint(create_dashboard_blueprint(), url_prefix="/customers")
    api_v1.register_blueprint(create_staff_blueprint(), url_prefix="/staff")
    api_v1.register_blueprint(create_manager_blueprint(), url_prefix="/manager")
    # The centralized alert feed. Mounted beside the legacy `/alerts`
    # (geofence-only) blueprint, which stays for its evidence-linked rows.
    api_v1.register_blueprint(create_alert_center_blueprint(), url_prefix="/feed")
    api_v1.register_blueprint(create_uploads_blueprint(), url_prefix="/uploads")
    api_v1.register_blueprint(create_inspections_blueprint(), url_prefix="/inspections")
    api_v1.register_blueprint(create_gps_blueprint(), url_prefix="/gps")
    api_v1.register_blueprint(create_geofences_blueprint(), url_prefix="/geofences")
    api_v1.register_blueprint(create_alerts_blueprint(), url_prefix="/alerts")
    api_v1.register_blueprint(create_ml_blueprint(), url_prefix="/ml")
    api_v1.register_blueprint(create_retrieval_blueprint(), url_prefix="/retrieval")

    # The audit trail. Read-only: the log viewer and the per-vehicle custody
    # timeline. Mounted last because nothing else depends on it.
    api_v1.register_blueprint(create_audit_blueprint(), url_prefix="/audit")

    return api_v1

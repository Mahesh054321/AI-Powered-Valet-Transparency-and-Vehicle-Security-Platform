"""Domain services.

A service owns one concern, is called only by routes, and never imports from
``routes``. Planned modules:

    services/health.py     Phase 1 - liveness/readiness probes
    services/auth.py       Phase 2 - registration, login, token refresh
    services/vehicles.py   Phase 3 - vehicle lifecycle
    services/tickets.py    Phase 4 - digital ticket and custody session
    services/vision.py     Phase 5-6 - capture, comparison, damage detection
    services/geofence.py   Phase 7 - zones, telemetry, breach rules
    services/alerts.py     Phase 8 - alert generation and acknowledgement
    services/retrieval.py  Phase 9 - retrieval-time prediction
    services/reporting.py  Phase 10 - aggregate reports
    services/audit.py      Phase 10 - append-only audit trail
"""

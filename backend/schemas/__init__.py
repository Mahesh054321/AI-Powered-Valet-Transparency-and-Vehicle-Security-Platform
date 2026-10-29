"""Request and response contracts.

A schema is the single place that declares what an endpoint accepts and what it
returns. Keeping them separate from routes means:

* a route handler stays short - validate, call a service, serialise;
* the same contract can be reused by several endpoints;
* the API documentation (Phase 11) can be generated from these definitions
  instead of being written by hand and drifting.

Phase 1 ships the shared envelope types only. Domain schemas arrive with their
modules: ``auth.py`` (Phase 2), ``vehicles.py`` (Phase 3), ``tickets.py``
(Phase 4), ``inspections.py`` (Phase 5), ``telemetry.py`` (Phase 7).
"""

from schemas.common import ErrorSchema, MetaSchema, SuccessEnvelope

__all__ = ["ErrorSchema", "MetaSchema", "SuccessEnvelope"]

"""The single writer for the audit trail.

Every auditable event in the platform goes through :func:`record_event`. That
is a deliberate constraint rather than a convenience.

**Why one writer.** Five service modules previously each carried their own
copy of a small ``_audit`` helper. They had drifted: ``services.auth`` captured
the caller's IP address, the other four did not. ``services.tickets`` accepted
either a ``User`` row or a JWT context, ``services.inspections`` only a JWT
context. ``custody_session_id`` -- the column that makes "everything that
happened to this car" a single indexed query -- was populated by none of them.
A reviewer asking for the IP behind a state change would have found it on
logins and nowhere else, which is worse than not recording it at all because it
looks like a policy rather than an omission.

One writer means one answer to "what does an audit entry contain?".

**Request context is captured automatically.** ``ip_address``, ``user_agent``
and ``request_id`` are read from the live Flask request when there is one, so a
service function does not have to thread them through every call. Events that
happen outside a request -- the seeder, a background detector -- simply get
``None``, which is the truth. ``capture_request_context=False`` opts out
explicitly for callers that want to assert nothing was captured.

**Attribution survives account deletion.** The actor is snapshotted as
``actor_id`` plus ``actor_role``/``actor_email``. Deleting or deactivating the
user cannot erase their trail, and the snapshot means a historical entry still
reads correctly after the account it names is gone.

**It does not commit.** Like every other service writer here, this adds to the
session and lets the calling service own the transaction. An audit entry must
land or not land *with* the change it describes -- a commit here would let an
entry survive a change that was subsequently rolled back.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flask import g, has_request_context, request

from app.extensions import db
from models.audit_log import (
    AuditAction,
    AuditLog,
    AuditOutcome,
    assert_valid_entity_type,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from models.user import User
    from utils.security import AuthContext

#: Headers checked, in order, for the client address. A reverse proxy in front
#: of the API sets the first; a direct local connection has only the second.
#: The forwarded value is trusted because this project's documented deployment
#: is a single trusted proxy on localhost -- worth stating, because trusting it
#: blindly in a public deployment would let a caller forge their own audit IP.
_FORWARDED_FOR_HEADER = "X-Forwarded-For"


def _client_ip() -> str | None:
    """Best available client address, or None outside a request."""
    if not has_request_context():
        return None
    forwarded = request.headers.get(_FORWARDED_FOR_HEADER)
    if forwarded:
        # The left-most entry is the original client; the rest are proxies it
        # passed through.
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:45]
    return (request.remote_addr or None) and request.remote_addr[:45]


def _request_id() -> str | None:
    """The request id the API also returns in ``X-Request-Id``.

    Correlating an audit entry with the access-log line for the same request is
    what lets a reviewer follow one action end to end.
    """
    if not has_request_context():
        return None
    return getattr(g, "request_id", None)


def _user_agent() -> str | None:
    if not has_request_context():
        return None
    agent = request.headers.get("User-Agent")
    return agent[:255] if agent else None


def _as_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def record_event(
    *,
    action: AuditAction | str,
    entity_type: str,
    entity_id: uuid.UUID | str | None,
    description: str,
    actor: User | AuthContext | None = None,
    actor_id: uuid.UUID | str | None = None,
    actor_role: str | None = None,
    actor_email: str | None = None,
    outcome: AuditOutcome | str = AuditOutcome.SUCCESS,
    failure_reason: str | None = None,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    custody_session_id: uuid.UUID | str | None = None,
    ip_address: str | None = None,
    capture_request_context: bool = True,
) -> AuditLog:
    """Append one entry to the audit trail.

    ``actor`` may be a loaded :class:`~models.user.User` (preferred, when the
    service already has the row) or an :class:`~utils.security.AuthContext`
    from the JWT. Both produce the same snapshot. ``None`` is legitimate and
    means the event had no authenticated actor -- a failed login before the
    account was identified, for instance.

    ``metadata`` holds context about the moment rather than a diff of the row:
    a model's confidence, the scenario a simulated track used, how far outside
    the fence a fix landed. ``new_values`` answers "what changed?"; this
    answers "what else should a reviewer know?".
    """
    action_value = action.value if isinstance(action, AuditAction) else str(action)
    outcome_value = outcome.value if isinstance(outcome, AuditOutcome) else str(outcome)

    # The actor snapshot. A User row and an AuthContext expose the same three
    # facts under slightly different attribute names; normalising here is what
    # lets callers pass whichever they happen to hold.
    # The actor snapshot. A User row and an AuthContext expose the same three
    # facts under slightly different attribute names; normalising here is what
    # lets callers pass whichever they happen to hold. Callers that have
    # neither -- a prediction computed from split values threaded down from a
    # route -- pass the three explicitly instead.
    resolved_id = _as_uuid(actor_id)
    resolved_role = actor_role
    resolved_email = actor_email
    if actor is not None:
        raw_id = getattr(actor, "id", None) or getattr(actor, "user_id", None)
        resolved_id = _as_uuid(raw_id)
        resolved_role = getattr(actor, "role", None)
        resolved_email = getattr(actor, "email", None)

    entry = AuditLog(
        actor_id=resolved_id,
        actor_role=resolved_role,
        actor_email=resolved_email,
        action=action_value,
        outcome=outcome_value,
        entity_type=assert_valid_entity_type(entity_type),
        entity_id=_as_uuid(entity_id),
        description=description[:500] if description else None,
        failure_reason=failure_reason[:500] if failure_reason else None,
        old_values=old_values,
        new_values=new_values,
        metadata_=metadata,
        custody_session_id=_as_uuid(custody_session_id),
        ip_address=ip_address or (_client_ip() if capture_request_context else None),
        user_agent=_user_agent() if capture_request_context else None,
        request_id=_request_id() if capture_request_context else None,
    )
    db.session.add(entry)
    return entry


__all__ = ["record_event"]

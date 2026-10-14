"""Revoked access tokens.

JWTs are stateless, so signing one out cannot by itself invalidate it. This
table is the small amount of state that makes logout real: on logout the
token's ``jti`` is recorded here, and ``load_current_user`` rejects any token
whose ``jti`` appears.

Why a table rather than an in-memory set:

* the API may run under several workers (``gunicorn``/``waitress``), which do
  not share memory, so an in-process denylist would only revoke the token on
  whichever worker handled the logout;
* a denylist must survive a restart, or a restart silently un-revokes every
  token that was signed out.

Only the ``jti`` and the expiry are stored -- never the token itself, and never
anything derived from the password. Rows are pruned once their ``expires_at``
has passed, because after that the token is rejected by its own expiry anyway
and the row is pure dead weight.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, UUIDPrimaryKeyMixin
from models.enums import check_constraint

JTI_MAX_LENGTH = 64
REASON_MAX_LENGTH = 40


class RevocationReason(StrEnum):
    """Why a token was revoked. Recorded so an audit can tell them apart."""

    LOGOUT = "logout"
    # Not a credential: the reason label recorded when a change forced logout.
    PASSWORD_CHANGED = "password_changed"  # noqa: S105
    ADMIN_REVOKED = "admin_revoked"


class RevokedToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "revoked_tokens"
    __table_args__ = (
        check_constraint("reason", RevocationReason),
        # One row per token id: logging out twice must not create a second row.
        Index("uq_revoked_tokens_jti", "jti", unique=True),
        Index("ix_revoked_tokens_expires_at", "expires_at"),
        {
            "comment": (
                "Denylist of signed-out access tokens, keyed by jti. Holds no "
                "token value and no credential material; rows are prunable "
                "once expires_at has passed."
            )
        },
    )

    #: Row-insertion time. Deliberately not TimestampMixin: a revocation is a
    #: fact about a moment, so there is no meaningful ``updated_at``.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    jti: Mapped[str] = mapped_column(
        String(JTI_MAX_LENGTH),
        nullable=False,
        comment="The `jti` claim of the revoked access token.",
    )
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_revoked_tokens_user_id_users"),
        index=True,
        comment="Whose token it was. SET by CASCADE on user deletion, since a "
        "deleted account's tokens are rejected anyway.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="The token's own expiry. After this instant the row is prunable.",
    )
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reason: Mapped[str] = mapped_column(
        String(REASON_MAX_LENGTH),
        nullable=False,
        default="logout",
        server_default="logout",
        comment="logout | password_changed | admin_revoked",
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))

    user: Mapped[User | None] = relationship("User")  # noqa: F821

    @property
    def is_prunable(self) -> bool:
        """True once the underlying token would be rejected by its own expiry."""
        return self.expires_at <= datetime.now(tz=UTC)

    def to_dict(self) -> dict:
        return {
            "jti": self.jti,
            "user_id": str(self.user_id) if self.user_id else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "reason": self.reason,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RevokedToken jti={self.jti[:8]}… reason={self.reason}>"


__all__ = ["RevocationReason", "RevokedToken"]

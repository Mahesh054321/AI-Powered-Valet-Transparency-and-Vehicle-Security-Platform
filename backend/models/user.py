"""Platform accounts: customers, valet operators, managers and administrators.

One row per identity across all four roles. A customer owns vehicles; the other
three roles act on vehicles during custody. Accounts are never hard-deleted --
``DEACTIVATED`` preserves the actor referenced by audit logs and custody
history, which matters for an auditable platform.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import UserStatus, check_constraint, status_column
from utils.security import Role, hash_password, verify_password

EMAIL_MAX_LENGTH = 254  # RFC 5321 practical maximum
NAME_MAX_LENGTH = 120
PHONE_MAX_LENGTH = 24
PASSWORD_HASH_MAX_LENGTH = 255  # generous for future schemes (argon2 is ~97)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        check_constraint("role", Role),
        check_constraint("status", UserStatus),
        # A suspended or deactivated account cannot also be active.
        CheckConstraint(
            "NOT (status = 'ACTIVE' AND deactivated_at IS NOT NULL)",
            name="ck_users_active_consistency",
        ),
        Index("ix_users_role_status", "role", "status"),
        Index("ix_users_full_name", "full_name"),
        {
            "comment": (
                "Platform accounts for all four roles. Passwords are stored "
                "only as PBKDF2-SHA256 hashes; plaintext is never persisted."
            )
        },
    )

    # -- identity -----------------------------------------------------------
    email: Mapped[str] = mapped_column(
        String(EMAIL_MAX_LENGTH), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(PASSWORD_HASH_MAX_LENGTH), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=str(Role.CUSTOMER))
    status: Mapped[str] = status_column(UserStatus, UserStatus.PENDING)

    full_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(PHONE_MAX_LENGTH))

    # -- state --------------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=func.true(),
        comment="Fast path for authentication checks; `status` is authoritative.",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(500))
    notification_preferences: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default='{"email_enabled": true, "sms_enabled": false, "min_severity": "LOW"}',
    )

    # -- relationships ------------------------------------------------------
    vehicles: Mapped[list[Vehicle]] = relationship(  # noqa: F821
        "Vehicle",
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tickets: Mapped[list[ValetTicket]] = relationship(  # noqa: F821
        "ValetTicket", back_populates="customer", passive_deletes=True
    )

    # -- helpers ------------------------------------------------------------
    @property
    def is_staff(self) -> bool:
        """True for the three roles that operate the facility."""
        return self.role in {
            Role.VALET_OPERATOR.value,
            Role.VALET_MANAGER.value,
            Role.ADMIN.value,
        }

    @property
    def can_authenticate(self) -> bool:
        return self.is_active and self.status == UserStatus.ACTIVE.value

    def set_password(self, password: str) -> None:
        self.password_hash = hash_password(password)

    def check_password(self, password: str) -> bool:
        return verify_password(password, self.password_hash)

    def to_dict(self, *, include_contact: bool = True) -> dict:
        """Serialise without ever exposing the password hash."""
        payload: dict = {
            "id": str(self.id),
            "email": self.email,
            "role": self.role,
            "status": self.status,
            "full_name": self.full_name,
            "is_active": self.is_active,
            "is_staff": self.is_staff,
            "notification_preferences": self.notification_preferences or {
                "email_enabled": True,
                "sms_enabled": False,
                "min_severity": "LOW",
            },
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_contact:
            payload["phone"] = self.phone
        return payload

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} role={self.role} status={self.status}>"


__all__ = ["User"]

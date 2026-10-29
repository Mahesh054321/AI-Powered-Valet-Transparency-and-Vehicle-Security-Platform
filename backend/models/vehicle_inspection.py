"""Condition capture: the drop-off and pickup photo sets.

A custody session has at most one inspection per stage -- ``DROP_OFF`` and
``PICKUP`` -- enforced by a unique index on ``(custody_session_id, stage)``.
Comparing those two sets is what produces the before/after view and flags new
damage.

**No image bytes are stored in this table.** ``photo_refs`` is a JSONB array of
references, each validated by :mod:`utils.image_ref` before it is written:

.. code-block:: json

    {
      "path": "inspections/2026/09/front-left.jpg",
      "byte_size": 248371,
      "width": 3024,
      "height": 4032,
      "mime_type": "image/jpeg",
      "sha256": "9f2c…",
      "captured_at": "2026-09-07T09:12:44+00:00",
      "label": "front_left"
    }

The SHA-256 digest lets an auditor prove the bytes behind a damage finding have
not changed since the finding was recorded.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import InspectionOutcome, InspectionStage, check_constraint, status_column

INSPECTOR_NOTES_MAX_LENGTH = 1000


class VehicleInspection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicle_inspections"
    __table_args__ = (
        check_constraint("stage", InspectionStage),
        check_constraint("outcome", InspectionOutcome),
        # Exactly one drop-off and one pickup inspection per custody session.
        Index(
            "uq_vehicle_inspections_session_stage",
            "custody_session_id",
            "stage",
            unique=True,
        ),
        CheckConstraint("photo_count >= 0", name="ck_vehicle_inspections_photo_count_non_negative"),
        CheckConstraint(
            "damage_count >= 0", name="ck_vehicle_inspections_damage_count_non_negative"
        ),
        # A set recorded as damaged must actually carry detections.
        CheckConstraint(
            "outcome <> 'DAMAGE_FOUND' OR damage_count > 0",
            name="ck_vehicle_inspections_damage_found_requires_count",
        ),
        CheckConstraint(
            "overall_condition_score IS NULL "
            "OR (overall_condition_score >= 0 AND overall_condition_score <= 100)",
            name="ck_vehicle_inspections_condition_score_range",
        ),
        CheckConstraint(
            "captured_at <= COALESCE(reviewed_at, captured_at)",
            name="ck_vehicle_inspections_review_after_capture",
        ),
        Index("ix_vehicle_inspections_outcome", "outcome"),
        Index("ix_vehicle_inspections_session_stage", "custody_session_id", "stage"),
        {
            "comment": (
                "Condition capture at drop-off and pickup. Stores JSONB "
                "references to images in storage (path, size, dimensions, MIME "
                "type, SHA-256) - never the image bytes themselves."
            )
        },
    )

    custody_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "custody_sessions.id",
            ondelete="CASCADE",
            name="fk_vehicle_inspections_custody_session_id_custody_sessions",
        ),
        nullable=False,
        index=True,
    )

    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = status_column(InspectionOutcome, InspectionOutcome.PENDING_REVIEW)

    inspected_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_vehicle_inspections_inspected_by_id_users"
        ),
        index=True,
    )
    reviewed_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_vehicle_inspections_reviewed_by_id_users"
        ),
    )

    # -- capture metadata ---------------------------------------------------
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String(120))

    #: JSONB array of validated image references. Never image bytes.
    photo_refs: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=sa_text("'[]'::jsonb"),
    )
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    damage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    overall_condition_score: Mapped[float | None] = mapped_column(
        Numeric(5, 2),
        comment="0 = total loss, 100 = showroom. Explainable, never a black box.",
    )
    odometer_reading: Mapped[int | None] = mapped_column(Integer)
    inspector_notes: Mapped[str | None] = mapped_column(String(INSPECTOR_NOTES_MAX_LENGTH))
    is_new_damage: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="True when a pickup inspection found damage absent at drop-off.",
    )

    # -- relationships ------------------------------------------------------
    custody_session: Mapped[CustodySession] = relationship(  # noqa: F821
        "CustodySession", back_populates="inspections"
    )
    inspected_by: Mapped[User | None] = relationship("User", foreign_keys=[inspected_by_id])  # noqa: F821
    reviewed_by: Mapped[User | None] = relationship("User", foreign_keys=[reviewed_by_id])  # noqa: F821
    damage_detections: Mapped[list[DamageDetection]] = relationship(  # noqa: F821
        "DamageDetection",
        back_populates="inspection",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "custody_session_id": str(self.custody_session_id),
            "stage": self.stage,
            "outcome": self.outcome,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "photo_count": self.photo_count,
            "photo_refs": self.photo_refs,
            "damage_count": self.damage_count,
            "overall_condition_score": (
                float(self.overall_condition_score)
                if self.overall_condition_score is not None
                else None
            ),
            "is_new_damage": self.is_new_damage,
            "inspector_notes": self.inspector_notes,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<VehicleInspection {self.stage} outcome={self.outcome}>"


__all__ = ["VehicleInspection"]

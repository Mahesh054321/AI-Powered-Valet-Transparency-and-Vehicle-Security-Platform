"""Damage findings from the vision pipeline.

Each row is one detected defect with the region it was found in, a confidence
score, and -- critically for an explainable system -- the *reasoning* that
produced it plus a reference to the annotated evidence image.

Two honesty invariants are enforced by CHECK constraints:

* ``confidence`` is a probability, so it lives in ``[0, 1]``;
* ``method`` must be one of the declared methods. Nothing here may claim a
  finding came from a trained model unless ``method`` says so -- the current
  pipeline is classical OpenCV (colour differencing, gradient and edge
  comparison, contour analysis), and ``method='CLASSICAL_CV'`` says exactly
  that. See ``docs/AI-INTEGRITY.md``.

The bounding box is a JSONB object in *normalised image coordinates*
(``x``, ``y``, ``width``, ``height`` each in ``[0, 1]``), so a region stays
correct if the image is later re-rendered at a different resolution.

As with inspections, **no image bytes are stored** -- only
``evidence_image_path`` and ``source_image_path``, both validated references.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import (
    DamageSeverity,
    VerificationStatus,
    check_constraint,
    status_column,
)


class DetectionMethod(StrEnum):
    """How a finding was produced. Never overstate this."""

    CLASSICAL_CV = "CLASSICAL_CV"  # OpenCV differencing / edges / contours
    RULE_BASED = "RULE_BASED"  # deterministic thresholds, no learning
    MANUAL = "MANUAL"  # recorded by a human inspector
    ML_MODEL = "ML_MODEL"  # a trained model - requires `model_version`


class DamageType(StrEnum):
    SCRATCH = "SCRATCH"
    DENT = "DENT"
    CRACK = "CRACK"
    PAINT_CHIP = "PAINT_CHIP"
    RUST = "RUST"
    GLASS_DAMAGE = "GLASS_DAMAGE"
    TYRE_DAMAGE = "TYRE_DAMAGE"
    MISSING_PART = "MISSING_PART"
    DIRT_STAIN = "DIRT_STAIN"
    OTHER = "OTHER"


class VehicleRegion(StrEnum):
    """Body zones a finding can be mapped to, for the annotated comparison."""

    FRONT_BUMPER = "FRONT_BUMPER"
    REAR_BUMPER = "REAR_BUMPER"
    HOOD = "HOOD"
    ROOF = "ROOF"
    BOOT = "BOOT"
    LEFT_FRONT_DOOR = "LEFT_FRONT_DOOR"
    LEFT_REAR_DOOR = "LEFT_REAR_DOOR"
    RIGHT_FRONT_DOOR = "RIGHT_FRONT_DOOR"
    RIGHT_REAR_DOOR = "RIGHT_REAR_DOOR"
    LEFT_FENDER = "LEFT_FENDER"
    RIGHT_FENDER = "RIGHT_FENDER"
    WINDSHIELD = "WINDSHIELD"
    REAR_WINDSHIELD = "REAR_WINDSHIELD"
    INTERIOR = "INTERIOR"
    WHEELS = "WHEELS"
    UNDERCARRIAGE = "UNDERCARRIAGE"
    OTHER = "OTHER"


class DamageDetection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "damage_detections"
    __table_args__ = (
        check_constraint("severity", DamageSeverity),
        check_constraint("damage_type", DamageType),
        check_constraint("region", VehicleRegion),
        check_constraint("method", DetectionMethod),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_damage_detections_confidence_is_probability",
        ),
        # Explainability is a requirement, not a nicety.
        CheckConstraint(
            "detection_reasoning IS NOT NULL AND length(detection_reasoning) > 0",
            name="ck_damage_detections_reasoning_required",
        ),
        # A finding attributed to a trained model must name its version.
        CheckConstraint(
            "method <> 'ML_MODEL' OR model_version IS NOT NULL",
            name="ck_damage_detections_ml_requires_model_version",
        ),
        CheckConstraint(
            "is_pre_existing IS NOT NULL", name="ck_damage_detections_pre_existing_present"
        ),
        CheckConstraint(
            "pixel_area IS NULL OR pixel_area > 0",
            name="ck_damage_detections_pixel_area_positive",
        ),
        # -- verification lifecycle -----------------------------------------
        check_constraint("verification_status", VerificationStatus),
        # A finding cannot claim a human reviewed it without naming who and when.
        CheckConstraint(
            "verification_status = 'UNVERIFIED' OR (verified_by_id IS NOT NULL "
            "AND verified_at IS NOT NULL)",
            name="ck_damage_detections_verified_requires_reviewer",
        ),
        CheckConstraint(
            "verification_status <> 'UNVERIFIED' OR verified_by_id IS NULL",
            name="ck_damage_detections_unverified_has_no_reviewer",
        ),
        # The label is a weaker claim than the region, so it gets its own
        # probability column and its own [0, 1] bound.
        CheckConstraint(
            "type_confidence IS NULL OR (type_confidence >= 0 AND type_confidence <= 1)",
            name="ck_damage_detections_type_confidence_is_probability",
        ),
        Index("ix_damage_detections_inspection_severity", "inspection_id", "severity"),
        Index("ix_damage_detections_damage_type", "damage_type"),
        Index("ix_damage_detections_verification_status", "verification_status"),
        Index("ix_damage_detections_verified_by", "verified_by_id"),
        Index(
            "ix_damage_detections_new_only",
            "inspection_id",
            postgresql_where="is_pre_existing = false",
        ),
        # The review queue: everything still awaiting a human.
        Index(
            "ix_damage_detections_awaiting_review",
            "inspection_id",
            postgresql_where="verification_status = 'UNVERIFIED'",
        ),
        {
            "comment": (
                "Individual damage findings with region, confidence, reasoning "
                "and an evidence-image reference. Stores no image bytes; "
                "`method` records honestly how the finding was produced."
            )
        },
    )

    inspection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "vehicle_inspections.id",
            ondelete="CASCADE",
            name="fk_damage_detections_inspection_id_inspections",
        ),
        nullable=False,
        index=True,
    )

    # -- what was found -----------------------------------------------------
    damage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = status_column(DamageSeverity, DamageSeverity.COSMETIC)
    confidence: Mapped[float] = mapped_column(
        Numeric(4, 3),
        nullable=False,
        comment="Probability in [0, 1]. Never presented as certainty.",
    )
    is_pre_existing: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
        comment="True when the same defect was present at drop-off.",
    )
    requires_customer_attention: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )

    # -- explainability -----------------------------------------------------
    #: Normalised bounding box: {"x","y","width","height"} each in [0, 1].
    bounding_box: Mapped[dict] = mapped_column(JSONB, nullable=False)
    detection_reasoning: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        comment="Human-readable explanation of why this region was flagged.",
    )
    method: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=str(DetectionMethod.CLASSICAL_CV),
        server_default=str(DetectionMethod.CLASSICAL_CV),
    )
    model_version: Mapped[str | None] = mapped_column(
        String(64), comment="Required when method = ML_MODEL."
    )
    algorithm_params: Mapped[dict | None] = mapped_column(
        JSONB,
        comment="The thresholds and parameters that produced this finding, so "
        "the result is reproducible.",
    )

    # -- evidence -----------------------------------------------------------
    source_image_path: Mapped[str | None] = mapped_column(
        String(1024), comment="Reference to the input photo. Not the bytes."
    )
    evidence_image_path: Mapped[str | None] = mapped_column(
        String(1024), comment="Reference to the annotated crop. Not the bytes."
    )
    comparison_image_path: Mapped[str | None] = mapped_column(
        String(1024), comment="Reference to the before/after composite."
    )
    pixel_area: Mapped[int | None] = mapped_column(
        Integer, comment="Area of the detected region in source pixels."
    )

    #: Confidence in the damage_type LABEL only. Deliberately separate from
    #: ``confidence``: differencing tells us a region changed with reasonable
    #: reliability, whereas naming that change a scratch or a dent is a shape
    #: and colour heuristic that is normally much less certain. Collapsing the
    #: two would let a well-measured region imply a well-guessed label.
    type_confidence: Mapped[float | None] = mapped_column(
        Numeric(4, 3),
        comment="Confidence in the damage_type label. Lower than `confidence` "
        "by design - see the column comment.",
    )

    # -- manual verification ------------------------------------------------
    verification_status: Mapped[str] = status_column(
        VerificationStatus, VerificationStatus.UNVERIFIED
    )
    verified_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_damage_detections_verified_by_id_users",
        ),
        index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_notes: Mapped[str | None] = mapped_column(String(500))

    # -- relationships ------------------------------------------------------
    inspection: Mapped[VehicleInspection] = relationship(  # noqa: F821
        "VehicleInspection", back_populates="damage_detections"
    )
    verified_by: Mapped[User | None] = relationship("User")  # noqa: F821

    @property
    def is_verified(self) -> bool:
        """True once a human has confirmed or rejected the finding."""
        return self.verification_status != str(VerificationStatus.UNVERIFIED)

    @property
    def presentation_label(self) -> str:
        """The wording the UI must use for this finding.

        Lives on the model so no view can quietly upgrade an unreviewed guess
        into a fact: only a confirmed finding may be called damage without a
        qualifier.
        """
        if self.verification_status == str(VerificationStatus.CONFIRMED):
            return "Confirmed Damage"
        if self.verification_status == str(VerificationStatus.REJECTED):
            return "Dismissed - Not Damage"
        return "Potential Damage"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "inspection_id": str(self.inspection_id),
            "damage_type": self.damage_type,
            "region": self.region,
            "severity": self.severity,
            "confidence": float(self.confidence),
            "is_pre_existing": self.is_pre_existing,
            "requires_customer_attention": self.requires_customer_attention,
            "bounding_box": self.bounding_box,
            "detection_reasoning": self.detection_reasoning,
            "method": self.method,
            "model_version": self.model_version,
            "algorithm_params": self.algorithm_params,
            "type_confidence": (
                float(self.type_confidence) if self.type_confidence is not None else None
            ),
            "evidence_image_path": self.evidence_image_path,
            "comparison_image_path": self.comparison_image_path,
            # -- verification ------------------------------------------------
            "verification_status": self.verification_status,
            "is_verified": self.is_verified,
            "presentation_label": self.presentation_label,
            "verified_by_id": str(self.verified_by_id) if self.verified_by_id else None,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "verification_notes": self.verification_notes,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DamageDetection {self.damage_type} at {self.region} "
            f"confidence={self.confidence}>"
        )


__all__ = ["DamageDetection", "DamageType", "DetectionMethod", "VehicleRegion"]

"""Retrieval predictions: when the customer's car will be ready.

A prediction is made when a customer requests retrieval, and again if the
estimate goes stale. Each row stores the estimate, its confidence, the queue
position at the time, and the inputs that produced it.

The honesty requirement applies here more than anywhere: the historical data
behind these estimates is synthetic, so every prediction records
``inputs_snapshot`` and ``method``. A reviewer can see exactly what was fed in
and how the number was derived, and the UI can state plainly that the model is
a heuristic over demo data rather than something trained on real operations.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from models.enums import PredictionStatus, check_constraint, status_column


class RetrievalPrediction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "retrieval_predictions"
    __table_args__ = (
        check_constraint("status", PredictionStatus),
        CheckConstraint(
            "predicted_wait_minutes >= 0",
            name="ck_retrieval_predictions_wait_non_negative",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_retrieval_predictions_confidence_is_probability",
        ),
        CheckConstraint(
            "queue_position IS NULL OR queue_position >= 1",
            name="ck_retrieval_predictions_queue_position_positive",
        ),
        CheckConstraint(
            "min_wait_minutes IS NULL OR max_wait_minutes IS NULL "
            "OR min_wait_minutes <= max_wait_minutes",
            name="ck_retrieval_predictions_wait_interval_ordered",
        ),
        CheckConstraint(
            "predicted_wait_minutes IS NULL OR min_wait_minutes IS NULL "
            "OR predicted_wait_minutes >= min_wait_minutes",
            name="ck_retrieval_predictions_wait_within_interval_low",
        ),
        CheckConstraint(
            "predicted_wait_minutes IS NULL OR max_wait_minutes IS NULL "
            "OR predicted_wait_minutes <= max_wait_minutes",
            name="ck_retrieval_predictions_wait_within_interval_high",
        ),
        CheckConstraint(
            "dispatch_after IS NULL OR requested_at IS NULL OR dispatch_after >= requested_at",
            name="ck_retrieval_predictions_dispatch_after_request",
        ),
        CheckConstraint(
            "actual_ready_at IS NULL OR requested_at IS NULL OR actual_ready_at >= requested_at",
            name="ck_retrieval_predictions_ready_after_request",
        ),
        Index(
            "ix_retrieval_predictions_session_requested",
            text("custody_session_id, requested_at DESC"),
        ),
        Index("ix_retrieval_predictions_status", "status"),
        {
            "comment": (
                "Predicted retrieval times with confidence, queue position and "
                "the inputs that produced the estimate, so the reasoning is "
                "reviewable. Historical inputs are synthetic in this MVP."
            )
        },
    )

    custody_session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "custody_sessions.id",
            ondelete="CASCADE",
            name="fk_retrieval_predictions_custody_session_id_custody_sessions",
        ),
        nullable=False,
        index=True,
    )
    requested_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_retrieval_predictions_requested_by_id_users",
        ),
    )
    assigned_valet_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_retrieval_predictions_assigned_valet_id_users",
        ),
        index=True,
    )

    status: Mapped[str] = status_column(PredictionStatus, PredictionStatus.PENDING)

    # -- timeline -----------------------------------------------------------
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    predicted_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="Recommended earliest dispatch time for the assigned valet.",
    )

    # -- the estimate -------------------------------------------------------
    predicted_wait_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    min_wait_minutes: Mapped[int | None] = mapped_column(Integer)
    max_wait_minutes: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    queue_position: Mapped[int | None] = mapped_column(Integer)

    # -- explainability -----------------------------------------------------
    method: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="heuristic-queue-v1",
        server_default="heuristic-queue-v1",
        comment="Names the estimator. 'heuristic-queue-v1' is not a trained model.",
    )
    model_version: Mapped[str | None] = mapped_column(String(64))
    #: The features used, so the estimate can be re-derived and audited.
    inputs_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(
        String(1000),
        comment="Human-readable summary of the factors behind the estimate.",
    )

    # -- relationships ------------------------------------------------------
    custody_session: Mapped[CustodySession] = relationship(  # noqa: F821
        "CustodySession", back_populates="predictions"
    )
    requested_by: Mapped[User | None] = relationship("User", foreign_keys=[requested_by_id])  # noqa: F821
    assigned_valet: Mapped[User | None] = relationship("User", foreign_keys=[assigned_valet_id])  # noqa: F821

    @property
    def wait_interval(self) -> tuple[int | None, int | None]:
        return (self.min_wait_minutes, self.max_wait_minutes)

    def to_dict(self) -> dict:
        def _iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.id),
            "custody_session_id": str(self.custody_session_id),
            "status": self.status,
            "requested_at": _iso(self.requested_at),
            "predicted_ready_at": _iso(self.predicted_ready_at),
            "actual_ready_at": _iso(self.actual_ready_at),
            "dispatch_after": _iso(self.dispatch_after),
            "predicted_wait_minutes": self.predicted_wait_minutes,
            "min_wait_minutes": self.min_wait_minutes,
            "max_wait_minutes": self.max_wait_minutes,
            "confidence": float(self.confidence),
            "queue_position": self.queue_position,
            "method": self.method,
            "inputs_snapshot": self.inputs_snapshot,
            "explanation": self.explanation,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<RetrievalPrediction wait={self.predicted_wait_minutes}m "
            f"confidence={self.confidence}>"
        )


__all__ = ["RetrievalPrediction"]

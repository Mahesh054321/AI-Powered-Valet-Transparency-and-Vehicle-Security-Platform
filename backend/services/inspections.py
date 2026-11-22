"""Vehicle condition assessment: capture, compare, report, verify.

This module owns the workflow around the vision pipeline. It holds no image
processing itself -- that lives in :mod:`ai.vision`, which knows nothing about
Flask or SQLAlchemy -- and no SQL beyond the models, so the two concerns stay
separately testable.

Three rules shape everything here.

**Angles are matched, not assumed.** A drop-off set and a pickup set are compared
angle by angle: ``left_side`` against ``left_side``, ``front`` against ``front``.
Comparing a front photo to a side photo would report the entire vehicle as
changed, so an angle with no counterpart is reported as unmatched rather than
silently paired with whatever was available.

**Outcome follows verification, not detection.** The pipeline produces
candidates. An inspection whose findings nobody has reviewed stays
``PENDING_REVIEW`` even though ``damage_count`` is non-zero; it becomes
``DAMAGE_FOUND`` only once a manager has confirmed at least one, and ``CLEAN``
once every finding has been reviewed and none confirmed. Recording
``DAMAGE_FOUND`` the moment a heuristic fires would put an unverified guess into
a column that reads like a fact, and that column is what a billing dispute would
turn on.

**Findings are never deleted on re-run.** Re-comparing replaces the candidates
for an angle but leaves an audit trail, because a reviewer may already have
looked at the previous set.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from ai.synthetic import DEMO_SCENARIOS, build_scenario, scenario_names
from ai.vision import compare_images
from ai.vision.config import DEFAULT_CONFIG
from ai.vision.regions import SUPPORTED_ANGLES, normalise_viewpoint
from app.extensions import db
from models.audit_log import AuditAction, AuditOutcome
from models.custody_session import CustodySession
from models.damage_detection import DamageDetection, DetectionMethod
from models.enums import (
    CustodyStatus,
    DamageSeverity,
    InspectionOutcome,
    InspectionStage,
    VerificationStatus,
    choices,
)
from models.vehicle_inspection import VehicleInspection
from services import media
from services.audit import record_event
from utils.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from utils.security import AuthContext, Role

#: Photos per stage. Generous enough for a thorough walk-around, bounded so a
#: single request cannot exhaust storage.
MAX_PHOTOS_PER_STAGE = 12

#: Angles the comparison will attempt. Others are stored but not compared.
COMPARABLE_ANGLES = tuple(SUPPORTED_ANGLES)

#: Roles allowed to capture an inspection.
INSPECTION_ROLES = (
    Role.VALET_OPERATOR.value,
    Role.VALET_MANAGER.value,
    Role.ADMIN.value,
)

#: Roles allowed to verify a finding. Deliberately narrower than capture: the
#: person who photographed the car should not be the only person who judges
#: whether the algorithm was right about it.
VERIFICATION_ROLES = (Role.VALET_MANAGER.value, Role.ADMIN.value)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _audit(
    *,
    action: str,
    outcome: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    description: str,
    actor_context: AuthContext,
    failure_reason: str | None = None,
    new_values: dict | None = None,
    metadata: dict | None = None,
    custody_session_id: uuid.UUID | str | None = None,
) -> None:
    """Append an audit entry. A thin delegate to :func:`services.audit.record_event`."""
    record_event(
        action=action,
        outcome=outcome,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        actor=actor_context,
        failure_reason=failure_reason,
        new_values=new_values,
        metadata=metadata,
        custody_session_id=custody_session_id,
    )


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------
def _load_session(custody_id: uuid.UUID | str) -> CustodySession:
    session = db.session.get(CustodySession, custody_id)
    if session is None:
        # 404 rather than 403: the existence of someone else's custody session
        # is not information this API should leak.
        raise NotFoundError(
            "No custody session was found with that id.",
            details={"custody_id": str(custody_id)},
        )
    return session


def _require_inspection_role(actor: AuthContext) -> None:
    if actor.role not in INSPECTION_ROLES:
        raise AuthorizationError(
            f"Role '{actor.role}' cannot capture a vehicle inspection. "
            f"Required: {', '.join(INSPECTION_ROLES)}.",
            details={"role": actor.role, "required": list(INSPECTION_ROLES)},
        )


def _require_verifier(actor: AuthContext) -> None:
    if actor.role not in VERIFICATION_ROLES:
        raise AuthorizationError(
            f"Only a manager or administrator can verify a damage finding; "
            f"'{actor.role}' cannot. Verification is deliberately restricted so "
            "the person who photographed the vehicle is not the only one judging "
            "the algorithm's output.",
            details={"role": actor.role, "required": list(VERIFICATION_ROLES)},
        )


def _require_view_access(actor: AuthContext, session: CustodySession) -> None:
    """A customer sees their own vehicle's condition; staff see any."""
    if actor.role in INSPECTION_ROLES:
        return

    ticket = session.ticket
    if ticket is not None and str(ticket.customer_id) == actor.user_id:
        return

    # Same 404 as an unknown id: do not confirm the session exists.
    raise NotFoundError(
        "No custody session was found with that id.",
        details={"custody_id": str(session.id)},
    )


# ---------------------------------------------------------------------------
# Photo handling
# ---------------------------------------------------------------------------
def _resolve_angles(files: list[FileStorage], form: dict[str, Any]) -> list[str | None]:
    """Pair each uploaded file with a camera angle.

    Accepts either a parallel ``angles`` list or a single ``angle`` applied to
    every file. Mismatched counts are rejected rather than guessed: silently
    applying one angle to a mixed set would produce comparisons between
    unrelated viewpoints, which is the failure this whole design avoids.
    """
    single = (form.get("angle") or None)
    multiple = form.get("angles")

    if multiple:
        values = multiple if isinstance(multiple, list) else [multiple]
        if len(values) != len(files):
            raise ValidationError(
                f"Received {len(files)} image(s) but {len(values)} angle(s). "
                "Send one angle per image, or a single 'angle' for all of them.",
                details={"images": len(files), "angles": len(values)},
            )
        return [normalise_viewpoint(value).value for value in values]

    return [normalise_viewpoint(single).value] * len(files)


def _store_photos(
    files: list[FileStorage], angles: list[str | None], *, stage: str
) -> list[dict[str, Any]]:
    """Validate and store each photo, returning its reference with the angle."""
    subdir = f"inspections/{stage.lower()}"
    references: list[dict[str, Any]] = []

    for index, (file, angle) in enumerate(zip(files, angles, strict=True)):
        reference = media.store_dated_upload(
            file,
            subdir=subdir,
            label=angle,
            field=f"images[{index}]",
        )
        references.append(reference)

    return references


def _read_blob(reference: dict[str, Any]) -> bytes:
    """Read a stored image's bytes back for comparison."""
    return media.resolve_media_path(reference["path"]).read_bytes()


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def _index_by_angle(references: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group photo references by their angle label.

    Where one angle was photographed twice the last one wins, and the fact is
    reported by the caller as an unmatched-angle warning rather than being
    hidden: comparing only one of two ``left_side`` photos is a real limitation
    of this version.
    """
    indexed: dict[str, dict[str, Any]] = {}
    for reference in references:
        raw_angle = reference.get("label") or reference.get("angle") or "GENERAL"
        norm_angle = str(normalise_viewpoint(raw_angle))
        indexed[norm_angle] = reference
    return indexed


def _run_angle_comparison(
    before_ref: dict[str, Any],
    after_ref: dict[str, Any],
    angle: str,
) -> dict[str, Any]:
    """Compare one matched angle pair and store the rendered evidence."""
    result = compare_images(
        _read_blob(before_ref),
        _read_blob(after_ref),
        angle=angle,
        config=DEFAULT_CONFIG,
    )

    stamp = f"{_now():%Y/%m/%d}"
    comparison_ref = media.store_bytes(
        result.comparison_png,
        subdir=f"comparisons/{stamp}",
        extension="png",
        label=f"comparison:{angle}",
    )
    overlay_ref = None
    if getattr(result, "overlay_png", None):
        overlay_ref = media.store_bytes(
            result.overlay_png,
            subdir=f"overlays/{stamp}",
            extension="png",
            label=f"overlay:{angle}",
        )

    findings: list[dict[str, Any]] = []
    for index, finding in enumerate(result.findings):
        evidence_ref = None
        if index < len(result.evidence):
            evidence_ref = media.store_bytes(
                result.evidence[index].png,
                subdir=f"evidence/{stamp}",
                extension="png",
                label=f"evidence:{angle}:{index}",
            )

        findings.append(
            {
                "finding": finding,
                "comparison_path": comparison_ref["path"],
                "comparison_url": media.media_url(comparison_ref["path"]),
                "overlay_path": overlay_ref["path"] if overlay_ref else None,
                "overlay_url": media.media_url(overlay_ref["path"]) if overlay_ref else None,
                "evidence_path": evidence_ref["path"] if evidence_ref else None,
                "evidence_url": (
                    media.media_url(evidence_ref["path"]) if evidence_ref else None
                ),
            }
        )

    return {
        "angle": angle,
        "findings": findings,
        "damage_detected": result.damage_detected,
        "alignment": result.alignment.describe(),
        "warnings": result.warnings,
        "statistics": result.statistics,
        "parameters": result.parameters,
        "comparison_path": comparison_ref["path"],
        "comparison_url": media.media_url(comparison_ref["path"]),
        "overlay_path": overlay_ref["path"] if overlay_ref else None,
        "overlay_url": media.media_url(overlay_ref["path"]) if overlay_ref else None,
        "drop_off_path": before_ref.get("path"),
        "drop_off_url": media.media_url(before_ref["path"]) if before_ref.get("path") else None,
        "pickup_path": after_ref.get("path"),
        "pickup_url": media.media_url(after_ref["path"]) if after_ref.get("path") else None,
    }


def _persist_findings(
    inspection: VehicleInspection, comparison: dict[str, Any], *, actor: AuthContext
) -> list[DamageDetection]:
    """Write candidate findings for one angle as UNVERIFIED detections."""
    created: list[DamageDetection] = []

    for entry in comparison["findings"]:
        finding = entry["finding"]
        # Appended to the relationship rather than added with an explicit
        # `inspection_id`. Setting the foreign key directly leaves the parent's
        # in-memory collection stale, so `_recompute_outcome` -- which counts
        # `inspection.damage_detections` -- would see zero findings and record
        # the inspection as CLEAN. The cascade sets the key either way.
        detection = DamageDetection(
            damage_type=finding.damage_type,
            region=finding.region,
            severity=finding.severity,
            confidence=finding.confidence,
            type_confidence=finding.type_confidence,
            # Everything a drop-off/pickup comparison finds is by definition
            # absent from the drop-off photo, so it is not pre-existing.
            is_pre_existing=False,
            requires_customer_attention=finding.confidence >= 0.5,
            bounding_box=finding.bounding_box,
            detection_reasoning=finding.reasoning[:1000],
            method=getattr(finding, "method", str(DetectionMethod.CLASSICAL_CV)),
            model_version=getattr(finding, "model_version", None),
            algorithm_params={
                "pipeline": comparison["parameters"],
                "region_statistics": finding.statistics,
                "region_mapping": finding.region_mapping,
                "angle": comparison["angle"],
                "alignment": comparison["alignment"],
            },
            source_image_path=comparison.get("pickup_path"),
            evidence_image_path=entry["evidence_path"],
            comparison_image_path=entry["comparison_path"],
            pixel_area=finding.pixel_area,
            verification_status=str(VerificationStatus.UNVERIFIED),
        )
        inspection.damage_detections.append(detection)
        created.append(detection)

    # One entry per finding rather than one per capture. A reviewer asking
    # "when was this scratch reported, and what was the algorithm's confidence
    # at the time?" needs the finding's own row, not a summary line that has to
    # be cross-referenced against the photograph upload.
    #
    # Every entry is UNVERIFIED by construction: the detector proposes, a
    # manager disposes, and DAMAGE_VERIFIED records the second half.
    db.session.flush()
    for detection in created:
        record_event(
            action=AuditAction.DAMAGE_DETECTED,
            outcome=AuditOutcome.SUCCESS,
            entity_type="damage_detections",
            entity_id=detection.id,
            actor=actor,
            custody_session_id=inspection.custody_session_id,
            description=(
                f"Potential {str(detection.damage_type).lower()} on "
                f"{str(detection.region).lower()} "
                f"(confidence {float(detection.confidence):.2f}, unverified)"
            ),
            metadata={
                "damage_type": detection.damage_type,
                "region": detection.region,
                "severity": detection.severity,
                "confidence": float(detection.confidence),
                "pixel_area": detection.pixel_area,
                "angle": comparison.get("angle"),
                "verification_status": detection.verification_status,
                # Stated on the record itself, so the log cannot be read as a
                # claim that the damage is confirmed.
                "presentation_label": "Potential Damage",
            },
        )

    return created


def _recompute_outcome(inspection: VehicleInspection) -> None:
    """Derive outcome and counts from the findings' verification state.

    This is the function that keeps an unverified guess out of a column that
    reads like a fact. See the module docstring.
    """
    detections = list(inspection.damage_detections)
    inspection.damage_count = len(detections)

    if not detections:
        inspection.outcome = str(InspectionOutcome.CLEAN)
        inspection.is_new_damage = False
        return

    confirmed = [d for d in detections if d.verification_status == "CONFIRMED"]
    unverified = [d for d in detections if d.verification_status == "UNVERIFIED"]

    inspection.is_new_damage = bool(confirmed)

    if unverified:
        inspection.outcome = str(InspectionOutcome.PENDING_REVIEW)
    elif confirmed:
        inspection.outcome = str(InspectionOutcome.DAMAGE_FOUND)
    else:
        # Every finding reviewed, none confirmed: a human looked and disagreed.
        inspection.outcome = str(InspectionOutcome.CLEAN)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _publish_potential_damage(inspection: VehicleInspection, *, actor=None) -> None:
    """Raise POTENTIAL_DAMAGE alerts for this inspection's unverified findings.

    Only UNVERIFIED findings are published. A CONFIRMED finding has already been
    through a manager, and a REJECTED one was judged a false positive -- raising
    alerts for either would put closed questions back in the queue.

    The wording stays "potential" all the way to the user. The detector flagged
    it; nobody has confirmed it, and saying otherwise would overstate what the
    algorithm established.

    Best-effort: a notification failure must not roll back an inspection that
    has already been captured and audited.
    """
    from models.enums import VerificationStatus

    pending = [
        detection
        for detection in inspection.damage_detections
        if detection.verification_status == VerificationStatus.UNVERIFIED.value
    ]
    if not pending:
        return

    try:
        from services.alerts import raise_potential_damage

        for detection in pending:
            raise_potential_damage(detection, actor=actor)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception(
            "Failed to raise potential-damage alerts for inspection %s", inspection.id
        )


def capture_inspection(
    actor: AuthContext,
    *,
    custody_id: uuid.UUID | str,
    stage: InspectionStage,
    files: list[FileStorage],
    form: dict[str, Any],
) -> dict[str, Any]:
    """Record a drop-off or pickup photo set, comparing on pickup.

    Capturing is idempotent per stage: re-submitting appends photos to the
    existing set rather than failing, because a valet adding one more angle
    should not have to re-upload the rest.
    """
    _require_inspection_role(actor)

    if not files:
        raise ValidationError(
            "No image was uploaded. Send at least one file in the 'images' field.",
            details={"field": "images"},
        )
    if len(files) > MAX_PHOTOS_PER_STAGE:
        raise ValidationError(
            f"At most {MAX_PHOTOS_PER_STAGE} images per inspection stage.",
            details={"count": len(files), "max": MAX_PHOTOS_PER_STAGE},
        )

    session = _load_session(custody_id)
    angles = _resolve_angles(files, form)
    references = _store_photos(files, angles, stage=stage.value)

    inspection = db.session.execute(
        select(VehicleInspection).where(
            VehicleInspection.custody_session_id == session.id,
            VehicleInspection.stage == stage.value,
        )
    ).scalar_one_or_none()

    created_now = inspection is None
    if inspection is None:
        inspection = VehicleInspection(
            custody_session_id=session.id,
            stage=stage.value,
            inspected_by_id=uuid.UUID(actor.user_id),
            photo_refs=[],
        )
        db.session.add(inspection)

    inspection.photo_refs = [*inspection.photo_refs, *references]
    inspection.photo_count = len(inspection.photo_refs)
    inspection.captured_at = _now()
    inspection.inspected_by_id = uuid.UUID(actor.user_id)

    if form.get("location"):
        inspection.location = str(form["location"])[:120]
    if form.get("notes"):
        inspection.inspector_notes = str(form["notes"])[:1000]
    if form.get("odometer"):
        try:
            inspection.odometer_reading = int(form["odometer"])
        except (TypeError, ValueError) as error:
            raise ValidationError(
                "'odometer' must be a whole number of kilometres.",
                details={"field": "odometer"},
            ) from error

    db.session.flush()

    comparisons: list[dict[str, Any]] = []
    unmatched: list[str] = []

    if stage is InspectionStage.PICKUP:
        comparisons, unmatched = _compare_against_drop_off(
            session, inspection, actor=actor
        )

    _recompute_outcome(inspection)
    db.session.flush()

    # Unverified findings become POTENTIAL_DAMAGE alerts, so a manager has a
    # queue to work through rather than having to hunt through inspections.
    # Raised before the commit so the alerts share the transaction: either the
    # capture and its alerts both land, or neither does.
    _publish_potential_damage(inspection, actor=actor)

    _audit(
        action=(
            AuditAction.PICKUP_IMAGE_CAPTURED
            if stage is InspectionStage.PICKUP
            else AuditAction.INSPECTION_CAPTURED
        ),
        outcome="SUCCESS",
        entity_type="vehicle_inspections",
        entity_id=inspection.id,
        description=(
            f"Captured {len(references)} {stage.value.lower()} photo(s) for "
            f"custody session {session.id}"
            + (
                f"; {sum(len(c['findings']) for c in comparisons)} candidate "
                "region(s) flagged for review"
                if comparisons
                else ""
            )
        ),
        actor_context=actor,
        custody_session_id=session.id,
        new_values={
            "stage": stage.value,
            "photos_added": len(references),
            "angles": angles,
            "comparisons_run": len(comparisons),
        },
        metadata={
            "stage": stage.value,
            "photo_count": len(references),
            "angles": angles,
            "candidate_findings": sum(len(c["findings"]) for c in comparisons),
        },
    )
    # The photographs themselves are a separate auditable fact from the
    # inspection record that references them: storage location, size and digest
    # are what let someone later confirm the image on screen is the one that
    # was uploaded.
    _audit(
        action=AuditAction.IMAGE_UPLOADED,
        outcome="SUCCESS",
        entity_type="vehicle_inspections",
        entity_id=inspection.id,
        description=(
            f"Stored {len(references)} {stage.value.lower()} image(s) for "
            f"custody session {session.id}"
        ),
        actor_context=actor,
        custody_session_id=session.id,
        metadata={
            "stage": stage.value,
            "image_count": len(references),
            "total_bytes": sum(int(ref.get("byte_size") or 0) for ref in references),
            "paths": [ref.get("path") for ref in references],
        },
    )
    db.session.commit()

    return inspection_dict(
        inspection,
        comparisons=comparisons,
        unmatched_angles=unmatched,
        created=created_now,
    )


def _compare_against_drop_off(
    session: CustodySession,
    pickup: VehicleInspection,
    *,
    actor: AuthContext,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compare every angle present in both photo sets.

    Angles captured at pickup but missing from drop-off are returned as
    unmatched. They are not compared against a substitute angle: a front photo
    and a side photo share almost no pixels, so pairing them would flag the
    whole car.
    """
    drop_off = db.session.execute(
        select(VehicleInspection).where(
            VehicleInspection.custody_session_id == session.id,
            VehicleInspection.stage == str(InspectionStage.DROP_OFF),
        )
    ).scalar_one_or_none()

    if drop_off is None or not drop_off.photo_refs:
        # Check-in creates an empty drop-off inspection, so "exists" is not the
        # same as "has photographs". Saying which one is missing matters: the
        # remedy for an absent record differs from the remedy for an unshot one.
        reason = (
            "No drop-off inspection exists for this session, so nothing could be "
            "compared."
            if drop_off is None
            else "The drop-off inspection has no photographs, so there is no "
            "baseline to compare against. Capture drop-off photos first."
        )
        return [], [reason]

    before_by_angle = _index_by_angle(drop_off.photo_refs)
    after_by_angle = _index_by_angle(pickup.photo_refs)

    # Re-running must not duplicate findings for an angle.
    replaced = False
    for existing in list(pickup.damage_detections):
        angle = (existing.algorithm_params or {}).get("angle")
        if angle in after_by_angle and angle in before_by_angle:
            db.session.delete(existing)
            replaced = True
    if replaced:
        db.session.flush()
        # `session.delete()` leaves the object in the parent's collection until
        # the identity map is refreshed, so `_recompute_outcome` would still
        # count the row that is about to disappear and report double the
        # findings. Expiring forces a reload from the database.
        db.session.expire(pickup, ["damage_detections"])

    comparisons: list[dict[str, Any]] = []
    unmatched: list[str] = []
    comparison_by_angle: dict[str, dict[str, Any]] = {}

    for angle, after_ref in sorted(after_by_angle.items()):
        before_ref = before_by_angle.get(angle)
        if before_ref is None:
            unmatched.append(
                f"'{angle}' was photographed at pickup but not at drop-off, so "
                "it could not be compared."
            )
            continue

        comparison = _run_angle_comparison(before_ref, after_ref, angle)
        _persist_findings(pickup, comparison, actor=actor)
        comparisons.append(comparison)
        comparison_by_angle[angle] = comparison

    for angle in sorted(set(before_by_angle) - set(after_by_angle)):
        unmatched.append(
            f"'{angle}' was photographed at drop-off but not at pickup, so it "
            "could not be compared."
        )

    # Enrich pickup photo references with comparison references for complete viewing
    enriched_photos = []
    for ref in pickup.photo_refs:
        item = dict(ref)
        raw_angle = item.get("label") or item.get("angle") or "GENERAL"
        norm_angle = str(normalise_viewpoint(raw_angle))
        if norm_angle in comparison_by_angle:
            comp = comparison_by_angle[norm_angle]
            item["comparison_path"] = comp.get("comparison_path")
            item["overlay_path"] = comp.get("overlay_path")
            item["alignment"] = comp.get("alignment")
            item["statistics"] = comp.get("statistics")
            item["parameters"] = comp.get("parameters")
            item["drop_off_path"] = comp.get("drop_off_path")
        enriched_photos.append(item)
    pickup.photo_refs = enriched_photos

    return comparisons, unmatched


def inspection_dict(
    inspection: VehicleInspection,
    *,
    comparisons: list[dict[str, Any]] | None = None,
    unmatched_angles: list[str] | None = None,
    created: bool = False,
) -> dict[str, Any]:
    """Serialise an inspection with its findings and rendered evidence."""
    detections = sorted(
        inspection.damage_detections, key=lambda d: d.confidence, reverse=True
    )

    return {
        "id": str(inspection.id),
        "custody_session_id": str(inspection.custody_session_id),
        "stage": inspection.stage,
        "outcome": inspection.outcome,
        "captured_at": inspection.captured_at.isoformat() if inspection.captured_at else None,
        "reviewed_at": inspection.reviewed_at.isoformat() if inspection.reviewed_at else None,
        "photo_count": inspection.photo_count,
        "damage_count": inspection.damage_count,
        "is_new_damage": inspection.is_new_damage,
        "inspector_notes": inspection.inspector_notes,
        "odometer_reading": inspection.odometer_reading,
        "location": inspection.location,
        "created": created,
        "photos": [
            {
                "url": media.media_url(ref["path"]),
                "angle": ref.get("label"),
                "width": ref.get("width"),
                "height": ref.get("height"),
                "byte_size": ref.get("byte_size"),
                "sha256": ref.get("sha256"),
                "captured_at": ref.get("captured_at"),
                "comparison_url": (
                    media.media_url(ref["comparison_path"])
                    if ref.get("comparison_path")
                    else None
                ),
                "overlay_url": (
                    media.media_url(ref["overlay_path"])
                    if ref.get("overlay_path")
                    else None
                ),
            }
            for ref in inspection.photo_refs
        ],
        "detections": [_detection_dict(d) for d in detections],
        "unverified_count": sum(
            1 for d in detections if d.verification_status == "UNVERIFIED"
        ),
        "confirmed_count": sum(
            1 for d in detections if d.verification_status == "CONFIRMED"
        ),
        "rejected_count": sum(
            1 for d in detections if d.verification_status == "REJECTED"
        ),
        "comparisons": comparisons or [],
        "unmatched_angles": unmatched_angles or [],
    }


def _detection_dict(detection: DamageDetection) -> dict[str, Any]:
    """A finding, with the wording its verification state requires."""
    payload = detection.to_dict()
    payload.update(
        {
            "evidence_url": (
                media.media_url(detection.evidence_image_path)
                if detection.evidence_image_path
                else None
            ),
            "comparison_url": (
                media.media_url(detection.comparison_image_path)
                if detection.comparison_image_path
                else None
            ),
            "source_image_url": (
                media.media_url(detection.source_image_path)
                if detection.source_image_path
                else None
            ),
            "angle": (detection.algorithm_params or {}).get("angle"),
            "method": detection.method,
            "model_version": detection.model_version,
        }
    )
    return payload


def get_inspection_report(
    actor: AuthContext, custody_id: uuid.UUID | str
) -> dict[str, Any]:
    """The full condition report for a custody session: both stages and findings."""
    session = _load_session(custody_id)
    _require_view_access(actor, session)

    inspections = db.session.execute(
        select(VehicleInspection)
        .where(VehicleInspection.custody_session_id == session.id)
        .order_by(VehicleInspection.stage)
    ).scalars().all()

    by_stage = {item.stage: item for item in inspections}
    drop_off = by_stage.get(str(InspectionStage.DROP_OFF))
    pickup = by_stage.get(str(InspectionStage.PICKUP))

    # Rebuild the comparison list from stored paths so the report is complete on
    # a fresh page load, not only in the response that created it.
    comparisons = _rebuild_comparisons(pickup, drop_off) if pickup else []

    all_detections = [d for item in inspections for d in item.damage_detections]

    return {
        "custody_session_id": str(session.id),
        "custody_status": session.status,
        "vehicle": _vehicle_summary(session),
        "ticket_code": session.ticket.ticket_code if session.ticket else None,
        "drop_off": inspection_dict(drop_off) if drop_off else None,
        "pickup": inspection_dict(pickup, comparisons=comparisons) if pickup else None,
        "comparisons": comparisons,
        "comparison_ready": pickup is not None and drop_off is not None,
        "total_detections": len(all_detections),
        "unverified_count": sum(
            1 for d in all_detections if d.verification_status == "UNVERIFIED"
        ),
        "confirmed_count": sum(
            1 for d in all_detections if d.verification_status == "CONFIRMED"
        ),
        "requires_review": any(
            d.verification_status == "UNVERIFIED" for d in all_detections
        ),
        # Stated on every report, not only in the docs.
        "detector_disclaimer": (
            "Findings are produced by classical OpenCV image differencing, not a "
            "trained damage classifier. Unverified findings are potential damage "
            "and require human confirmation."
        ),
    }


def _rebuild_comparisons(
    pickup: VehicleInspection, drop_off: VehicleInspection | None = None
) -> list[dict[str, Any]]:
    """Group stored findings and photos by angle so the report page can render each pair."""
    before_by_angle = _index_by_angle(drop_off.photo_refs) if (drop_off and drop_off.photo_refs) else {}
    after_by_angle = _index_by_angle(pickup.photo_refs) if (pickup and pickup.photo_refs) else {}

    grouped: dict[str, dict[str, Any]] = {}

    for angle, after_ref in after_by_angle.items():
        before_ref = before_by_angle.get(angle)
        comp_url = (
            media.media_url(after_ref["comparison_path"])
            if after_ref.get("comparison_path")
            else None
        )
        overlay_url = (
            media.media_url(after_ref["overlay_path"])
            if after_ref.get("overlay_path")
            else None
        )
        drop_off_url = media.media_url(before_ref["path"]) if before_ref else None
        pickup_url = media.media_url(after_ref["path"])

        grouped[angle] = {
            "angle": angle,
            "findings": [],
            "damage_detected": False,
            "alignment": after_ref.get("alignment"),
            "warnings": [],
            "statistics": after_ref.get("statistics"),
            "parameters": after_ref.get("parameters"),
            "comparison_url": comp_url,
            "overlay_url": overlay_url,
            "drop_off_url": drop_off_url,
            "pickup_url": pickup_url,
        }

    for detection in pickup.damage_detections:
        params = detection.algorithm_params or {}
        raw_angle = params.get("angle", "GENERAL")
        angle = str(normalise_viewpoint(raw_angle))

        if angle not in grouped:
            before_ref = before_by_angle.get(angle)
            after_ref = after_by_angle.get(angle)
            grouped[angle] = {
                "angle": angle,
                "findings": [],
                "damage_detected": True,
                "alignment": params.get("alignment"),
                "warnings": [],
                "statistics": None,
                "parameters": params.get("pipeline"),
                "comparison_url": None,
                "overlay_url": None,
                "drop_off_url": media.media_url(before_ref["path"]) if before_ref else None,
                "pickup_url": media.media_url(after_ref["path"]) if after_ref else None,
            }

        bucket = grouped[angle]
        bucket["damage_detected"] = True
        bucket["findings"].append(_detection_dict(detection))
        if detection.comparison_image_path and not bucket["comparison_url"]:
            bucket["comparison_url"] = media.media_url(detection.comparison_image_path)

    return [grouped[key] for key in sorted(grouped)]


def _vehicle_summary(session: CustodySession) -> dict[str, Any] | None:
    vehicle = session.vehicle
    if vehicle is None:
        return None
    return {
        "id": str(vehicle.id),
        "plate_number": vehicle.plate_number,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "year": vehicle.year,
    }


def verify_detection(
    actor: AuthContext,
    *,
    detection_id: uuid.UUID | str,
    status: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Confirm or reject a finding. Manager or administrator only."""
    _require_verifier(actor)

    allowed = (
        str(VerificationStatus.CONFIRMED),
        str(VerificationStatus.REJECTED),
        str(VerificationStatus.UNVERIFIED),
    )
    if status not in allowed:
        raise ValidationError(
            f"'status' must be one of {', '.join(allowed)}.",
            details={"field": "status", "allowed": list(allowed)},
        )

    detection = db.session.get(DamageDetection, detection_id)
    if detection is None:
        raise NotFoundError(
            "No damage finding was found with that id.",
            details={"detection_id": str(detection_id)},
        )

    if notes is not None and len(notes) > 500:
        raise ValidationError(
            "'notes' must be at most 500 characters.",
            details={"field": "notes", "max_length": 500},
        )

    previous = detection.verification_status
    detection.verification_status = status

    if status == str(VerificationStatus.UNVERIFIED):
        # Reopening a finding clears the reviewer, so the CHECK constraint that
        # forbids an unverified row carrying a reviewer stays satisfied.
        detection.verified_by_id = None
        detection.verified_at = None
    else:
        detection.verified_by_id = uuid.UUID(actor.user_id)
        detection.verified_at = _now()

    detection.verification_notes = notes

    inspection = detection.inspection
    inspection.reviewed_by_id = uuid.UUID(actor.user_id)
    inspection.reviewed_at = _now()
    _recompute_outcome(inspection)

    db.session.flush()
    _audit(
        action="DAMAGE_VERIFIED",
        outcome="SUCCESS",
        entity_type="damage_detections",
        entity_id=detection.id,
        description=(
            f"{previous} -> {status}: {detection.damage_type} at {detection.region} "
            f"(algorithm confidence {float(detection.confidence):.2f})"
        ),
        actor_context=actor,
        new_values={"status": status, "notes": notes},
    )
    db.session.commit()

    return _detection_dict(detection)


def run_demo_comparison(actor: AuthContext, *, scenario: str) -> dict[str, Any]:
    """Run the real pipeline over a synthetic photo pair.

    Persists no inspection and no findings: this exists so the module can be
    demonstrated without a car, and a demo that wrote damage records into the
    database would pollute real custody history. The rendered images are stored
    so their URLs work in the report page.

    The images are synthetic and the response says so. The *findings* are not
    synthetic -- they come from the same ``compare_images`` the production path
    calls, which is what makes this a demonstration rather than a mock.
    """
    if scenario not in DEMO_SCENARIOS:
        raise ValidationError(
            f"Unknown demo scenario '{scenario}'.",
            details={"field": "scenario", "allowed": scenario_names()},
        )

    pair = build_scenario(scenario)
    result = compare_images(pair.drop_off_png, pair.pickup_png, angle=pair.angle)

    stamp = f"{_now():%Y/%m/%d}"
    comparison_ref = media.store_bytes(
        result.comparison_png,
        subdir=f"demo/comparisons/{stamp}",
        extension="png",
        label=f"demo:{scenario}",
    )
    overlay_ref = None
    if getattr(result, "overlay_png", None):
        overlay_ref = media.store_bytes(
            result.overlay_png,
            subdir=f"demo/overlays/{stamp}",
            extension="png",
            label=f"demo-overlay:{scenario}",
        )
    drop_off_ref = media.store_bytes(
        pair.drop_off_png, subdir=f"demo/photos/{stamp}", extension="png",
        label=f"demo-dropoff:{scenario}",
    )
    pickup_ref = media.store_bytes(
        pair.pickup_png, subdir=f"demo/photos/{stamp}", extension="png",
        label=f"demo-pickup:{scenario}",
    )

    findings: list[dict[str, Any]] = []
    for index, finding in enumerate(result.findings):
        evidence_url = None
        if index < len(result.evidence):
            evidence_ref = media.store_bytes(
                result.evidence[index].png,
                subdir=f"demo/evidence/{stamp}",
                extension="png",
                label=f"demo-evidence:{scenario}:{index}",
            )
            evidence_url = media.media_url(evidence_ref["path"])

        findings.append(
            {
                "bounding_box": finding.bounding_box,
                "region": finding.region,
                "damage_type": finding.damage_type,
                "severity": finding.severity,
                "confidence": finding.confidence,
                "type_confidence": finding.type_confidence,
                "detection_reasoning": finding.reasoning,
                "method": getattr(finding, "method", str(DetectionMethod.CLASSICAL_CV)),
                "pixel_area": finding.pixel_area,
                "statistics": finding.statistics,
                "region_mapping": finding.region_mapping,
                # A demo finding has no reviewer and must not look verified.
                "verification_status": str(VerificationStatus.UNVERIFIED),
                "is_verified": False,
                "presentation_label": "Potential Damage",
                "evidence_url": evidence_url,
                "comparison_url": media.media_url(comparison_ref["path"]),
                "overlay_url": media.media_url(overlay_ref["path"]) if overlay_ref else None,
            }
        )

    _audit(
        action="INSPECTION_DEMO_RUN",
        outcome="SUCCESS",
        entity_type="vehicle_inspections",
        entity_id=None,
        description=(
            f"Ran the condition pipeline on synthetic scenario '{scenario}'; "
            f"{len(findings)} candidate region(s)"
        ),
        actor_context=actor,
        new_values={"scenario": scenario, "synthetic": True},
    )
    db.session.commit()

    return {
        "synthetic": True,
        "scenario": scenario,
        "scenario_label": DEMO_SCENARIOS[scenario]["label"],
        "scenario_description": DEMO_SCENARIOS[scenario]["description"],
        "available_scenarios": [
            {"name": name, "label": spec["label"], "description": spec["description"]}
            for name, spec in DEMO_SCENARIOS.items()
        ],
        "ground_truth": pair.describe(),
        "angle": pair.angle,
        "damage_detected": result.damage_detected,
        "detections": findings,
        "detected_count": len(findings),
        "planted_count": len(pair.defects),
        "alignment": result.alignment.describe(),
        "warnings": result.warnings,
        "statistics": result.statistics,
        "parameters": result.parameters,
        "drop_off_url": media.media_url(drop_off_ref["path"]),
        "pickup_url": media.media_url(pickup_ref["path"]),
        "comparison_url": media.media_url(comparison_ref["path"]),
        "overlay_url": media.media_url(overlay_ref["path"]) if overlay_ref else None,
        "disclaimer": (
            "Both photographs are synthetic, generated by OpenCV for this demo. "
            "The findings are real pipeline output computed from those pixels, "
            "not canned results - but they are unverified candidates from a "
            "heuristic, not a confirmed assessment."
        ),
    }


def dispute_inspection(
    actor: AuthContext,
    custody_id: uuid.UUID | str,
    *,
    reason: str,
    notes: str | None = None,
    disputed_detection_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Submit a customer dispute regarding vehicle condition report findings."""
    session = _load_session(custody_id)
    _require_view_access(actor, session)
    if not session.is_open:
        raise ConflictError(
            "This custody session is already complete and cannot be disputed.",
            details={"custody_id": str(session.id), "status": session.status},
        )
    if session.status == CustodyStatus.DISPUTED.value:
        raise ConflictError(
            "This custody session already has an open dispute awaiting management review.",
            details={"custody_id": str(session.id), "status": session.status},
        )

    if not reason or not reason.strip():
        raise ValidationError(
            "'reason' is required to file a condition dispute.",
            details={"field": "reason"},
        )
    if len(reason) > 400:
        raise ValidationError(
            "'reason' must be at most 400 characters.",
            details={"field": "reason", "max_length": 400},
        )
    if notes is not None and len(notes) > 400:
        raise ValidationError(
            "'notes' must be at most 400 characters.",
            details={"field": "notes", "max_length": 400},
        )

    previous_status = session.status
    session.status = str(CustodyStatus.DISPUTED)

    disputed_findings = []
    if disputed_detection_ids:
        for det_id in disputed_detection_ids:
            try:
                valid_id = uuid.UUID(str(det_id))
            except ValueError:
                continue
            det = db.session.get(DamageDetection, valid_id)
            if det and str(det.inspection.custody_session_id) == str(session.id):
                prefix = f"[DISPUTED by customer]: {reason}."
                det.verification_notes = f"{prefix} {notes or ''}".strip()
                disputed_findings.append(str(det.id))

    db.session.flush()

    _audit(
        action=AuditAction.STATUS_CHANGE,
        outcome="SUCCESS",
        entity_type="custody_sessions",
        entity_id=session.id,
        description=(
            f"Condition dispute filed for session {session.id}: {reason}"
        ),
        actor_context=actor,
        custody_session_id=session.id,
        new_values={
            "status": str(CustodyStatus.DISPUTED),
            "reason": reason,
            "notes": notes,
            "disputed_detection_ids": disputed_findings,
            "previous_status": previous_status,
        },
    )
    db.session.commit()
    return get_inspection_report(actor, custody_id)


def resolve_dispute(
    actor: AuthContext,
    custody_id: uuid.UUID | str,
    *,
    notes: str,
) -> dict[str, Any]:
    """Resolve a disputed custody session and close its ticket. Manager only."""
    _require_verifier(actor)
    session = _load_session(custody_id)
    if session.status != CustodyStatus.DISPUTED.value:
        raise ConflictError(
            "This custody session has no open dispute to resolve.",
            details={"custody_id": str(session.id), "status": session.status},
        )

    if not isinstance(notes, str):
        raise ValidationError("'notes' must be a string.", details={"field": "notes"})
    resolution_notes = notes.strip()
    if not resolution_notes:
        raise ValidationError(
            "Resolution notes are required to close a disputed custody session.",
            details={"field": "notes"},
        )
    if len(resolution_notes) > 500:
        raise ValidationError(
            "'notes' must be at most 500 characters.",
            details={"field": "notes", "max_length": 500},
        )

    # Reuse the ticket completion transaction so the session, ticket, vehicle,
    # zone occupancy and custody audit all reach their closed states together.
    from services.tickets import complete_ticket

    ticket = complete_ticket(
        actor,
        session.ticket_id,
        {"condition_notes": f"Dispute resolution: {resolution_notes}"},
    )
    return {
        "custody_session": session.to_dict(),
        "ticket_id": str(ticket.id),
        "ticket_status": ticket.status,
        "message": "Dispute resolved and custody session completed.",
    }


def recompare_inspection(
    actor: AuthContext,
    custody_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Re-run the OpenCV/ML comparison pipeline on drop-off and pickup photos."""
    _require_verifier(actor)
    session = _load_session(custody_id)

    pickup = db.session.execute(
        select(VehicleInspection).where(
            VehicleInspection.custody_session_id == session.id,
            VehicleInspection.stage == str(InspectionStage.PICKUP),
        )
    ).scalar_one_or_none()

    if not pickup:
        raise NotFoundError(
            "No pickup inspection exists to recompare.",
            details={"custody_session_id": str(custody_id)},
        )

    comparisons, unmatched = _compare_against_drop_off(
        session, pickup, actor=actor
    )
    _recompute_outcome(pickup)
    db.session.commit()

    return get_inspection_report(actor, custody_id)


def annotate_detection(
    actor: AuthContext,
    *,
    detection_id: uuid.UUID | str,
    notes: str,
    severity: str | None = None,
) -> dict[str, Any]:
    """Annotate or update inspector notes on a candidate finding."""
    _require_verifier(actor)
    detection = db.session.get(DamageDetection, detection_id)
    if detection is None:
        raise NotFoundError("No damage finding was found with that id.")

    if notes is not None and len(notes) > 500:
        raise ValidationError(
            "'notes' must be at most 500 characters.",
            details={"field": "notes", "max_length": 500},
        )

    if severity:
        if severity not in choices(DamageSeverity):
            raise ValidationError(
                f"'severity' must be one of {', '.join(choices(DamageSeverity))}.",
                details={"field": "severity"},
            )
        detection.severity = severity

    detection.verification_notes = notes
    db.session.flush()

    _audit(
        action=AuditAction.UPDATE,
        outcome="SUCCESS",
        entity_type="damage_detections",
        entity_id=detection.id,
        description=f"Annotated finding {detection.id}: notes={notes}, severity={detection.severity}",
        actor_context=actor,
        new_values={"notes": notes, "severity": detection.severity},
    )
    db.session.commit()
    return _detection_dict(detection)


def demo_scenarios() -> list[dict[str, str]]:
    """The demo catalogue, for the UI to build a picker."""
    return [
        {"name": name, "label": spec["label"], "description": spec["description"]}
        for name, spec in DEMO_SCENARIOS.items()
    ]


__all__ = [
    "COMPARABLE_ANGLES",
    "INSPECTION_ROLES",
    "MAX_PHOTOS_PER_STAGE",
    "VERIFICATION_ROLES",
    "annotate_detection",
    "capture_inspection",
    "demo_scenarios",
    "dispute_inspection",
    "get_inspection_report",
    "inspection_dict",
    "recompare_inspection",
    "resolve_dispute",
    "run_demo_comparison",
    "verify_detection",
]

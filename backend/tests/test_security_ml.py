"""Tests for the prototype ML layer and, above all, for the rule that binds it.

The combination rule in ``services/security_ml.py`` is one sentence long and
load-bearing: **the rule engine is a floor; the classifier may raise a verdict
and may never lower it.** Everything else in the ML layer is a prototype whose
accuracy is measured on synthetic data. This one property is what makes that
acceptable, so it is tested hardest.

Two classes of test:

* **``combine()`` unit tests** - the invariant, proven without a database. Each
  "ML cannot lower it" case is paired with the opposite case proving the
  classifier *can* raise a verdict. A one-sided suite would pass on a
  combination function that ignored the classifier entirely, which is the
  vacuous-pass trap these tests are written to avoid.
* **endpoint and persistence tests** - the model reached through HTTP, its
  verdict stored on the GPS row, and the breach alert still raised when the
  classifier disagrees.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from ai.anomaly import geofence as engine
from ai.ml.features import PriorObservation
from app.extensions import db
from models.enums import MlClassification, MovementStatus
from services import security_ml
from services.security_ml import (
    DEFAULT_ESCALATION_THRESHOLD,
    MlOpinion,
    combine,
    features_from_verdict,
    security_model,
    severity_for_probability,
)
from tests.workflow_helpers import bearer, make_user, make_vehicle, unique
from utils.security import Role

GEOFENCES = "/api/v1/geofences"
GPS = "/api/v1/gps"
ML = "/api/v1/ml"

CENTRE = (27.4935, 77.6745)
RADIUS = 100.0

#: A fix ~60 m inside a 100 m zone: unambiguously within the boundary.
INSIDE = engine.destination_point(CENTRE[0], CENTRE[1], 0.0, 40.0)
#: A fix ~300 m outside it: unambiguously a breach.
OUTSIDE = engine.destination_point(CENTRE[0], CENTRE[1], 0.0, 300.0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def opinion(probability: float, *, model_version: str = "test-model/1.0.0") -> MlOpinion:
    """A classifier opinion with a stated confidence, for combination tests."""
    label = (
        MlClassification.SUSPICIOUS.value
        if probability >= 0.5
        else MlClassification.NORMAL.value
    )
    return MlOpinion(
        classification=label,
        probability=probability,
        severity=severity_for_probability(probability),
        reason=f"test opinion at {probability:.0%}",
        model_version=model_version,
        top_factors=(("movement_duration_seconds", 3.0), ("time_sin", 1.0)),
    )


def rule_verdict(status: str, *, distance: float, inside: bool) -> engine.MovementVerdict:
    """A rule verdict with the finding its status implies."""
    findings: tuple[engine.Finding, ...] = ()
    if status == MovementStatus.BREACH.value:
        findings = (
            engine.Finding(
                alert_type=engine.ALERT_GEOFENCE_BREACH,
                severity=engine.SEVERITY_CRITICAL,
                title="Vehicle left its assigned geofence",
                reason="Fix is outside the zone boundary.",
                confidence=0.99,
                status=MovementStatus.BREACH.value,
            ),
        )
    elif status == MovementStatus.SUSPICIOUS.value:
        findings = (
            engine.Finding(
                alert_type=engine.ALERT_ABNORMAL_MOVEMENT,
                severity=engine.SEVERITY_MEDIUM,
                title="Unexpected movement",
                reason="Moving with no task in progress.",
                confidence=0.9,
                status=MovementStatus.SUSPICIOUS.value,
            ),
        )
    elif status == MovementStatus.WARNING.value:
        findings = (
            engine.Finding(
                alert_type=engine.ALERT_UNUSUAL_SPEED,
                severity=engine.SEVERITY_LOW,
                title="Speed above the zone limit",
                reason="Moving fast for a yard.",
                confidence=0.8,
                status=MovementStatus.WARNING.value,
            ),
        )
    return engine.MovementVerdict(
        status=status,
        distance_from_boundary_metres=distance,
        is_within_zone=inside,
        findings=findings,
        evidence={"rule": "unit-test fixture"},
    )


# ---------------------------------------------------------------------------
# the safety invariant: ML may never lower a verdict
# ---------------------------------------------------------------------------
class TestMlCannotLowerAVerdict:
    """The property that makes a synthetic-data prototype acceptable here.

    Each case pairs the refusal to downgrade with proof that the same classifier
    *can* escalate. Without that pairing, every test below would also pass on a
    ``combine()`` that simply returned the rule verdict unchanged -- a
    completely broken implementation.
    """

    def test_a_breach_stays_a_breach_when_the_classifier_calls_it_benign(self):
        verdict = rule_verdict(MovementStatus.BREACH.value, distance=200.0, inside=False)
        combined = combine(verdict, opinion(0.0))

        assert combined.status == MovementStatus.BREACH.value
        assert combined.rule_status == MovementStatus.BREACH.value
        assert combined.escalated is False
        # The classifier's dissent is recorded, not acted on.
        assert combined.opinion is not None
        assert combined.opinion.classification == MlClassification.NORMAL.value
        assert "never downgraded" in combined.rationale

    def test_the_breach_finding_survives_so_the_alert_still_fires(self):
        """The reason a breach cannot be downgraded: its alert depends on it.

        ``_raise_alerts`` iterates ``verdict.findings``. If the breach finding
        were dropped, no alert would be created -- the exact failure the rule
        exists to prevent. This asserts on the finding, not just the status,
        because a status alone would still read as BREACH with an empty list.
        """
        verdict = rule_verdict(MovementStatus.BREACH.value, distance=200.0, inside=False)
        combined = combine(verdict, opinion(0.0))

        types = [finding.alert_type for finding in combined.verdict.findings]
        assert engine.ALERT_GEOFENCE_BREACH in types
        assert combined.verdict.findings == verdict.findings

    def test_a_suspicious_verdict_is_not_downgraded_by_a_normal_opinion(self):
        """Tested at both ends of the confidence range.

        A low confidence trips the escalation threshold first and a high one
        reaches the rank check, so the two are reached by different branches and
        produce different rationales. Asserting only one would leave the other
        branch unproven, and a bug in it would be invisible.
        """
        verdict = rule_verdict(MovementStatus.SUSPICIOUS.value, distance=-40.0, inside=True)

        low = combine(verdict, opinion(0.02))
        assert low.status == MovementStatus.SUSPICIOUS.value
        assert low.escalated is False
        assert "below the" in low.rationale

        high = combine(verdict, opinion(0.99))
        assert high.status == MovementStatus.SUSPICIOUS.value
        assert high.escalated is False
        assert "never lowered" in high.rationale

    def test_a_warning_is_not_downgraded_by_a_normal_opinion(self):
        verdict = rule_verdict(MovementStatus.WARNING.value, distance=-40.0, inside=True)
        combined = combine(verdict, opinion(0.10))

        assert combined.status == MovementStatus.WARNING.value
        assert combined.escalated is False

    def test_no_status_pair_can_produce_a_lower_verdict(self):
        """The invariant as a table, not as a handful of chosen cases.

        Exhaustive over every rule status and a spread of confidences: the
        combined rank must never fall below the rule rank. This is the test that
        would catch a future "simplification" into a plain max over both inputs.
        """
        statuses = [
            MovementStatus.NORMAL.value,
            MovementStatus.WARNING.value,
            MovementStatus.SUSPICIOUS.value,
            MovementStatus.BREACH.value,
        ]
        for status in statuses:
            for probability in (0.0, 0.25, 0.5, 0.75, 0.99):
                verdict = rule_verdict(status, distance=-10.0, inside=True)
                combined = combine(verdict, opinion(probability))
                assert engine._rank(combined.status) >= engine._rank(  # noqa: SLF001
                    status
                ), f"{status} was lowered to {combined.status} at p={probability}"


class TestMlCanEscalate:
    """The other half: if these failed, the class above would pass vacuously."""

    def test_a_normal_fix_escalates_on_a_confident_opinion(self):
        verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-40.0, inside=True)
        combined = combine(verdict, opinion(0.95))

        assert combined.rule_status == MovementStatus.NORMAL.value
        assert combined.status == MovementStatus.SUSPICIOUS.value
        assert combined.escalated is True
        assert "raised from NORMAL to SUSPICIOUS" in combined.rationale

    def test_escalation_adds_a_finding_the_alert_layer_can_act_on(self):
        verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-40.0, inside=True)
        combined = combine(verdict, opinion(0.95))

        types = [finding.alert_type for finding in combined.verdict.findings]
        assert security_ml.ML_ALERT_TYPE in types
        # An escalation must not invent a boundary crossing it never measured.
        assert engine.ALERT_GEOFENCE_BREACH not in types

    def test_the_escalated_finding_carries_the_probability_as_its_confidence(self):
        verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-40.0, inside=True)
        combined = combine(verdict, opinion(0.87))
        added = combined.verdict.findings[-1]

        assert added.confidence == pytest.approx(0.87)
        assert added.status == MovementStatus.SUSPICIOUS.value
        assert "87%" in added.reason
        # The reason must say it is a prototype, because it reaches the alert row.
        assert "prototype" in added.reason.lower()

    def test_a_warning_escalates_to_suspicious_but_not_beyond(self):
        verdict = rule_verdict(MovementStatus.WARNING.value, distance=-40.0, inside=True)
        combined = combine(verdict, opinion(0.99))

        assert combined.status == MovementStatus.SUSPICIOUS.value
        assert combined.escalated is True

    def test_escalation_keeps_the_rule_finding_alongside_the_new_one(self):
        """The classifier adds an opinion; it does not replace the measurement.

        A ``WARNING`` already carries the rule that produced it. Rebuilding the
        finding list from scratch would drop that rule's reason from the alert
        stream, and a reviewer would see "the model was worried" with no record
        of what the rules actually measured. Both must be present, in order,
        rule first.
        """
        verdict = rule_verdict(MovementStatus.WARNING.value, distance=-40.0, inside=True)
        assert verdict.findings, "the fixture must start with a finding for this to mean anything"

        combined = combine(verdict, opinion(0.99))
        types = [finding.alert_type for finding in combined.verdict.findings]

        assert types[0] == verdict.findings[0].alert_type
        assert engine.ALERT_UNUSUAL_SPEED in types
        assert security_ml.ML_ALERT_TYPE in types
        assert len(types) == len(verdict.findings) + 1

    def test_escalation_preserves_the_geometric_evidence(self):
        """The verdict's measured distance and containment must survive the merge.

        These are what the map draws. Reconstructing the verdict without them
        would leave a fix with a status but no position evidence attached.
        """
        verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-42.5, inside=True)
        combined = combine(verdict, opinion(0.99))

        assert combined.verdict.distance_from_boundary_metres == pytest.approx(-42.5)
        assert combined.verdict.is_within_zone is True

    def test_ml_escalation_never_reaches_breach(self):
        """BREACH is reserved for a measured boundary crossing.

        A classifier cannot produce one however confident it is, because it never
        measured a boundary. Asserting the ceiling as well as the floor: the
        model's influence is bounded on both sides.
        """
        for status in (
            MovementStatus.NORMAL.value,
            MovementStatus.WARNING.value,
            MovementStatus.SUSPICIOUS.value,
        ):
            verdict = rule_verdict(status, distance=-10.0, inside=True)
            combined = combine(verdict, opinion(1.0))
            assert combined.status != MovementStatus.BREACH.value


# ---------------------------------------------------------------------------
# the confidence threshold
# ---------------------------------------------------------------------------
class TestEscalationThreshold:
    def test_an_opinion_below_the_threshold_does_not_escalate(self):
        verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-40.0, inside=True)
        combined = combine(
            verdict,
            opinion(DEFAULT_ESCALATION_THRESHOLD - 0.01),
            escalation_threshold=DEFAULT_ESCALATION_THRESHOLD,
        )

        assert combined.status == MovementStatus.NORMAL.value
        assert combined.escalated is False
        # Recorded anyway: an unacted-on opinion is still evidence.
        assert combined.opinion is not None
        assert "below the" in combined.rationale

    def test_an_opinion_at_the_threshold_does_escalate(self):
        verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-40.0, inside=True)
        combined = combine(
            verdict,
            opinion(DEFAULT_ESCALATION_THRESHOLD),
            escalation_threshold=DEFAULT_ESCALATION_THRESHOLD,
        )

        assert combined.status == MovementStatus.SUSPICIOUS.value
        assert combined.escalated is True

    def test_the_threshold_is_above_a_coin_flip(self):
        """The default must not be 0.5.

        Escalating on a coin flip would fill the alert queue with noise and
        teach reviewers to ignore it. This is a guard on the constant itself,
        because nothing else in the suite would notice it being lowered.
        """
        assert DEFAULT_ESCALATION_THRESHOLD > 0.5


# ---------------------------------------------------------------------------
# degradation: no model, or a broken one
# ---------------------------------------------------------------------------
class TestRulesOnlyDeployment:
    def test_no_opinion_means_the_rule_verdict_passes_through_untouched(self):
        verdict = rule_verdict(MovementStatus.BREACH.value, distance=200.0, inside=False)
        combined = combine(verdict, None)

        assert combined.verdict is verdict
        assert combined.opinion is None
        assert combined.escalated is False
        assert combined.evidence == {}
        assert "No ML model available" in combined.rationale

    def test_an_unavailable_service_reports_itself_honestly(self, pg_app):
        """A missing artifact is a supported state, not an error.

        The health response must say what is missing and how to fix it, because
        a dashboard showing "ML: off" with no reason reads as a bug.
        """
        security_model.reset()
        pg_app.config["ML_MODEL_PATH"] = pg_app.config["ML_MODEL_PATH"].parent / "absent.json"
        try:
            assert security_model.available is False
            info = security_model.describe()
            assert info["available"] is False
            assert "train_security_model.py" in info["reason"]
        finally:
            security_model.reset()
            pg_app.config["ML_MODEL_PATH"] = (
                pg_app.config["ML_MODEL_PATH"].parent / "security_classifier.json"
            )

    def test_a_disabled_model_is_reported_as_disabled_not_broken(self, pg_app):
        security_model.reset()
        pg_app.config["ML_ENABLED"] = False
        try:
            assert security_model.available is False
            assert "disabled" in security_model.load_error.lower()
        finally:
            pg_app.config["ML_ENABLED"] = True
            security_model.reset()


# ---------------------------------------------------------------------------
# severity bands and feature extraction
# ---------------------------------------------------------------------------
class TestSeverityAndFeatures:
    def test_severity_rises_with_confidence(self):
        assert severity_for_probability(0.95) == "HIGH"
        assert severity_for_probability(0.80) == "MEDIUM"
        assert severity_for_probability(0.30) == "LOW"

    def test_the_classifier_and_the_rules_judge_the_same_measurement(self):
        """Features come from the verdict, not from a second geometry pass.

        If the classifier computed its own distance it could disagree with the
        rule engine for a reason that had nothing to do with behaviour, and a
        disagreement would mean nothing. This pins the shared inputs.
        """
        verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-42.5, inside=True)
        features = features_from_verdict(
            verdict=verdict,
            latitude=INSIDE[0],
            longitude=INSIDE[1],
            speed_kmph=8.0,
            heading_degrees=None,
            recorded_at=datetime(2026, 9, 8, 14, 0, tzinfo=UTC),
            priors=(),
            buffer_metres=25.0,
        )

        assert features["distance_from_zone_metres"] == pytest.approx(-42.5)
        assert features["zone_breach"] == 0.0

    def test_a_fix_outside_the_tolerance_band_reads_as_a_breach_feature(self):
        verdict = rule_verdict(MovementStatus.BREACH.value, distance=180.0, inside=False)
        features = features_from_verdict(
            verdict=verdict,
            latitude=OUTSIDE[0],
            longitude=OUTSIDE[1],
            speed_kmph=8.0,
            heading_degrees=None,
            recorded_at=datetime(2026, 9, 8, 14, 0, tzinfo=UTC),
            priors=(),
            buffer_metres=25.0,
        )

        assert features["zone_breach"] == 1.0

    def test_priors_are_converted_so_movement_duration_is_measured(self):
        """``PriorFix`` has no heading; the conversion must not drop the history.

        Guards the mapping itself: a silent failure here would report zero
        movement duration for every fix and quietly remove the model's strongest
        feature.
        """
        base = datetime(2026, 9, 8, 2, 0, tzinfo=UTC)
        priors = tuple(
            engine.PriorFix(
                latitude=CENTRE[0],
                longitude=CENTRE[1],
                recorded_at=base + timedelta(minutes=index),
                speed_kmph=12.0,
            )
            for index in range(6)
        )
        verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-40.0, inside=True)
        features = features_from_verdict(
            verdict=verdict,
            latitude=CENTRE[0],
            longitude=CENTRE[1],
            speed_kmph=12.0,
            heading_degrees=None,
            recorded_at=base + timedelta(minutes=6),
            priors=priors,
            buffer_metres=25.0,
        )

        assert features["movement_duration_seconds"] > 0.0

    def test_time_of_day_is_encoded_as_a_pair_not_an_hour(self):
        """An hour number would make 23:00 adjacent to 00:00 in the wrong way.

        The sin/cos pair keeps midnight continuous. Checked at the two extremes.
        """
        midnight = features_from_verdict(
            verdict=rule_verdict(MovementStatus.NORMAL.value, distance=-40.0, inside=True),
            latitude=CENTRE[0],
            longitude=CENTRE[1],
            speed_kmph=0.0,
            heading_degrees=None,
            recorded_at=datetime(2026, 9, 8, 0, 0, tzinfo=UTC),
            priors=(),
            buffer_metres=25.0,
        )
        assert midnight["time_sin"] == pytest.approx(0.0, abs=1e-9)
        assert midnight["time_cos"] == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# end to end, through HTTP
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _clean(pg_app):
    security_model.reset()
    yield
    db.session.rollback()
    security_model.reset()


def _setup(client):
    """A checked-in vehicle inside a manager-created geofence."""
    manager = make_user(Role.VALET_MANAGER)
    operator = make_user(Role.VALET_OPERATOR)
    customer = make_user()
    vehicle = make_vehicle(customer.id)
    db.session.commit()

    zone = client.post(
        GEOFENCES,
        json={
            "code": unique("ZONE").upper()[:32],
            "name": "ML test yard",
            "latitude": CENTRE[0],
            "longitude": CENTRE[1],
            "radius": RADIUS,
        },
        headers=bearer(manager),
    )
    assert zone.status_code == 201, zone.get_json()

    ticket = client.post(
        "/api/v1/tickets", json={"vehicle_id": str(vehicle.id)}, headers=bearer(operator)
    )
    assert ticket.status_code == 201, ticket.get_json()
    checked_in = client.post(
        f"/api/v1/tickets/{ticket.get_json()['data']['ticket']['id']}/check-in",
        json={"zone_id": zone.get_json()["data"]["id"]},
        headers=bearer(operator),
    )
    assert checked_in.status_code == 200, checked_in.get_json()
    fetched = client.get(
        f"/api/v1/tickets/{ticket.get_json()['data']['ticket']['id']}", headers=bearer(operator)
    )
    return {
        "manager": manager,
        "operator": operator,
        "session_id": fetched.get_json()["data"]["ticket"]["custody_session"]["id"],
        "vehicle": vehicle,
    }


def _minutes_ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


class TestStationaryNoiseIsNotBehaviour:
    """A parked car must not be flagged because its receiver wanders.

    Regression guard. A stationary GPS still reports a heading, and with no
    motion to average over that heading drifts - the ``parked`` simulator
    scenario produces successive headings of 141, 172, 70, 213, 54 degrees on a
    car that never moved. Fed to the classifier as a real "change of direction",
    that read as evasive manoeuvring and escalated a parked vehicle to
    P(suspicious) = 0.78.

    The failure was also invisible in CI: it only appeared when the suite ran at
    night, because time-of-day pushes the same features over the threshold. A
    test that passes at 4pm and fails at 2am is worse than no test, so this one
    pins the clock rather than trusting it.
    """

    @staticmethod
    def _parked_features(hour: float, minute: float) -> dict[str, float]:
        """The feature profile of a genuinely stationary fix, at a given hour."""
        angle = math.pi * 2 * ((hour + minute / 60.0) / 24.0)
        return {
            "speed_kmph": 0.6,
            "distance_from_zone_metres": -30.7,
            "movement_duration_seconds": 0.0,
            # The heading of a parked car swings wildly between fixes. Before
            # the fix this arrived here as a 140-degree "direction change".
            "direction_change_degrees": 142.9,
            "distance_travelled_metres": 1.9,
            "zone_breach": 0.0,
            "time_sin": math.sin(angle),
            "time_cos": math.cos(angle),
        }

    def test_a_stationary_vehicle_is_not_escalated_at_any_hour(self):
        """Swept across the clock, not sampled at whatever time CI runs."""
        for hour in range(24):
            features = self._parked_features(hour, 30)
            verdict = rule_verdict(MovementStatus.NORMAL.value, distance=-30.7, inside=True)
            opinion = security_model.classify(features)
            assert opinion is not None, "a trained artifact is required for this guard"

            combined = combine(verdict, opinion)
            assert combined.escalated is False, (
                f"a parked car was escalated at {hour:02d}:30 "
                f"(p={opinion.probability:.3f})"
            )
            assert combined.status == MovementStatus.NORMAL.value

    def test_a_heading_on_a_stationary_vehicle_produces_no_direction_change(self):
        """The root cause, asserted at the feature layer rather than the outcome.

        A receiver with no motion to average over does not know which way the
        vehicle faces. Reporting 142 degrees of "change" from that is inventing
        an observation, so the feature must stay at zero.
        """
        from ai.ml.features import STATIONARY_SPEED_KMPH, extract_features

        base = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)
        priors = tuple(
            PriorObservation(
                latitude=CENTRE[0],
                longitude=CENTRE[1],
                recorded_at=base + timedelta(minutes=index),
                speed_kmph=0.6,
                heading_degrees=90.0 if index % 2 == 0 else 270.0,
            )
            for index in range(5)
        )
        stationary = extract_features(
            latitude=CENTRE[0],
            longitude=CENTRE[1],
            recorded_at=base + timedelta(minutes=5),
            speed_kmph=STATIONARY_SPEED_KMPH - 1.0,
            heading_degrees=0.0,
            distance_from_zone_metres=-30.0,
            is_within_zone=True,
            buffer_metres=25.0,
            priors=priors,
        ).values
        assert stationary["direction_change_degrees"] == 0.0

        # And the opposite case: once the vehicle is genuinely moving, the same
        # swinging headings ARE a direction change and must be reported. Without
        # this half the test would also pass on a feature hard-wired to zero.
        moving = extract_features(
            latitude=CENTRE[0],
            longitude=CENTRE[1],
            recorded_at=base + timedelta(minutes=5),
            speed_kmph=STATIONARY_SPEED_KMPH + 10.0,
            heading_degrees=0.0,
            distance_from_zone_metres=-30.0,
            is_within_zone=True,
            buffer_metres=25.0,
            priors=priors,
        ).values
        assert moving["direction_change_degrees"] == pytest.approx(90.0)

    def test_a_sub_noise_displacement_infers_no_bearing(self):
        """A 1 m shift on a fix with metres of stated accuracy has no direction.

        Deriving a bearing from it invents one, which is the same mistake as
        reading a stationary compass.
        """
        from ai.ml.features import GPS_NOISE_METRES, extract_features

        base = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)
        tiny = engine.destination_point(CENTRE[0], CENTRE[1], 90.0, GPS_NOISE_METRES / 2.0)
        priors = (
            PriorObservation(
                latitude=CENTRE[0],
                longitude=CENTRE[1],
                recorded_at=base,
                speed_kmph=8.0,
            ),
        )
        features = extract_features(
            latitude=tiny[0],
            longitude=tiny[1],
            recorded_at=base + timedelta(seconds=30),
            speed_kmph=8.0,
            heading_degrees=0.0,
            distance_from_zone_metres=-30.0,
            is_within_zone=True,
            buffer_metres=25.0,
            priors=priors,
        ).values

        assert features["distance_travelled_metres"] < GPS_NOISE_METRES
        assert features["direction_change_degrees"] == 0.0


class TestModelEndpoint:
    def test_the_model_endpoint_discloses_its_synthetic_origins(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        response = pg_client.get(f"{ML}/model", headers=bearer(manager))
        assert response.status_code == 200, response.get_json()
        data = response.get_json()["data"]

        assert data["trained_on_synthetic_data"] is True
        assert "synthetic" in data["disclaimer"].lower()
        assert "model_version" in data
        # The metrics are exposed so a reviewer can judge the model rather than
        # take a headline number on trust.
        if data["available"]:
            assert data["metrics"]["confusion_matrix"]
            assert data["escalation_threshold"] == DEFAULT_ESCALATION_THRESHOLD

    def test_the_model_endpoint_requires_authentication(self, pg_client):
        response = pg_client.get(f"{ML}/model")
        assert response.status_code == 401


class TestClassifyEndpoint:
    def features(self, **overrides):
        base = {
            "speed_kmph": 12.0,
            "distance_from_zone_metres": -30.0,
            "movement_duration_seconds": 600.0,
            "direction_change_degrees": 10.0,
            "distance_travelled_metres": 20.0,
            "zone_breach": 0.0,
            "time_sin": math.sin(math.pi * 2 * (2.5 / 24)),
            "time_cos": math.cos(math.pi * 2 * (2.5 / 24)),
        }
        base.update(overrides)
        return base

    def test_it_returns_the_four_fields_the_brief_asks_for(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        response = pg_client.post(f"{ML}/classify", json=self.features(), headers=bearer(manager))
        if response.status_code == 422 and "No security classifier" in response.get_json()[
            "error"
        ]["message"]:
            pytest.skip("no trained artifact in this checkout")
        assert response.status_code == 200, response.get_json()
        data = response.get_json()["data"]

        assert data["classification"] in (
            MlClassification.NORMAL.value,
            MlClassification.SUSPICIOUS.value,
        )
        assert 0.0 <= data["probability"] <= 1.0
        assert data["reason"]
        assert data["severity"] in ("LOW", "MEDIUM", "HIGH")
        # Provenance travels with every prediction.
        assert data["trained_on_synthetic_data"] is True
        assert data["disclaimer"]
        assert data["model_version"]
        assert data["top_factors"]

    def test_a_missing_feature_is_refused_rather_than_defaulted(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()
        payload = self.features()
        del payload["movement_duration_seconds"]

        response = pg_client.post(f"{ML}/classify", json=payload, headers=bearer(manager))

        assert response.status_code == 422, response.get_json()
        details = response.get_json()["error"]["details"]
        assert details["missing_features"] == ["movement_duration_seconds"]
        assert "required_features" in details

    def test_a_non_numeric_feature_is_refused(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        response = pg_client.post(
            f"{ML}/classify", json=self.features(speed_kmph="fast"), headers=bearer(manager)
        )

        assert response.status_code == 422, response.get_json()
        assert "speed_kmph" in response.get_json()["error"]["details"]["invalid"]

    def test_an_out_of_range_flag_is_refused(self, pg_client):
        manager = make_user(Role.VALET_MANAGER)
        db.session.commit()

        response = pg_client.post(
            f"{ML}/classify", json=self.features(zone_breach=2.0), headers=bearer(manager)
        )

        assert response.status_code == 422, response.get_json()
        assert response.get_json()["error"]["details"]["field"] == "zone_breach"


class TestIngestionStoresTheVerdict:
    def test_a_breach_raises_its_alert_even_though_the_classifier_is_ignored(self, pg_client):
        """The requirement, tested through the real HTTP path.

        A geofence breach must produce an alert whatever the model thinks. The
        model is not stubbed here: whatever it says, the alert must exist.
        """
        context = _setup(pg_client)
        lat, lon = OUTSIDE

        response = pg_client.post(
            f"{GPS}/events",
            json={
                "custody_session_id": context["session_id"],
                "latitude": lat,
                "longitude": lon,
                "speed_kmph": 10.0,
                "timestamp": _minutes_ago(2),
            },
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 201, response.get_json()
        data = response.get_json()["data"]

        assert data["movement_status"] == MovementStatus.BREACH.value
        assert data["rule_movement_status"] == MovementStatus.BREACH.value
        assert data["escalated_by_ml"] is False
        assert data["alerts_raised"], "a breach must always raise an alert"
        assert any(
            alert["alert_type"] == engine.ALERT_GEOFENCE_BREACH
            for alert in data["alerts_raised"]
        )

    def test_the_ml_verdict_is_persisted_on_the_gps_row(self, pg_client):
        context = _setup(pg_client)
        lat, lon = INSIDE

        response = pg_client.post(
            f"{GPS}/events",
            json={
                "custody_session_id": context["session_id"],
                "latitude": lat,
                "longitude": lon,
                "speed_kmph": 4.0,
                "timestamp": _minutes_ago(2),
            },
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 201, response.get_json()

        from models.gps_event import GpsEvent

        row = db.session.execute(
            db.select(GpsEvent).where(
                GpsEvent.custody_session_id == uuid.UUID(context["session_id"])
            )
        ).scalar_one()

        # Either all three columns are populated or all three are null: the
        # CHECK constraints forbid a partial write, and this proves the service
        # respects them.
        if row.ml_classification is None:
            assert row.ml_probability is None
            assert row.ml_model_version is None
        else:
            assert row.ml_classification in ("NORMAL", "SUSPICIOUS")
            assert 0.0 <= float(row.ml_probability) <= 1.0
            assert row.ml_model_version

    def test_the_rule_verdict_is_reported_separately_from_the_final_one(self, pg_client):
        """A reviewer must be able to see what the rules said on their own.

        If the response only carried the post-ML status, an escalation would be
        indistinguishable from a rule finding -- and the whole point of storing
        both is that the measurement stays auditable.
        """
        context = _setup(pg_client)
        lat, lon = INSIDE

        response = pg_client.post(
            f"{GPS}/events",
            json={
                "custody_session_id": context["session_id"],
                "latitude": lat,
                "longitude": lon,
                "speed_kmph": 4.0,
                "timestamp": _minutes_ago(2),
            },
            headers=bearer(context["operator"]),
        )
        assert response.status_code == 201, response.get_json()
        data = response.get_json()["data"]

        assert "rule_movement_status" in data
        assert "escalated_by_ml" in data
        assert "ml" in data


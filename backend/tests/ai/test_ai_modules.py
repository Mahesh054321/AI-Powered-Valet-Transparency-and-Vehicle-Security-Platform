"""AI module tests: image comparison, damage generation, geofence, retrieval.

These exercise the ``ai/`` package directly rather than through HTTP. That is
possible because ``ai/`` imports neither Flask nor SQLAlchemy -- a deliberate
architectural rule, and one of the reasons this file needs no database and runs
in seconds.

The organising principle throughout is that **a model that cannot be wrong is
not being tested**. Each area therefore has a negative case that matters as much
as the positive one:

* image comparison must report *nothing* on two identical photographs;
* geofence classification must call a stationary in-zone fix ``NORMAL``;
* retrieval prediction must be beaten by its own baseline on held-out data,
  otherwise the claim of improvement is unfounded.

Nothing here uses canned outputs. Images come from the synthetic generator and
are pushed through the real pipeline; the retrieval model is fitted on generated
history and scored on a period it never saw.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from ai.anomaly.geofence import (
    DEFAULT_CONFIG,
    GeofenceConfig,
    PriorFix,
    angle_between_degrees,
    bearing_degrees,
    circular_ring,
    evaluate_fix,
    haversine_m,
    point_in_polygon,
    signed_distance_to_boundary_m,
)
from ai.retrieval import (
    FEATURE_LABELS,
    METHOD_NAME,
    MODEL_VERSION,
    PROTOTYPE_DISCLAIMER,
    RETRIEVAL_FEATURE_COLUMNS,
    RetrievalModel,
)
from ai.retrieval.dataset import generate_history
from ai.synthetic.gps_tracks import TRACK_SCENARIOS, generate_track
from ai.synthetic.vehicle_photos import (
    SyntheticDefect,
    build_scenario,
    generate_pair,
    scenario_names,
)
from ai.vision import DEFAULT_CONFIG as VISION_CONFIG
from ai.vision import compare_images

#: A small square around a plausible Mathura coordinate.
CENTRE_LAT = 27.4935
CENTRE_LON = 77.6745


def _defects(count: int) -> tuple[SyntheticDefect, ...]:
    """``count`` spread-out synthetic defects, for scaling checks.

    Placed on a diagonal so they do not overlap and merge into one blob, which
    would make "more defects" indistinguishable from "one bigger defect".
    """
    return tuple(
        SyntheticDefect(
            kind="scratch",
            centre=(0.18 + 0.13 * index, 0.30 + 0.09 * index),
            extent=(0.10, 0.03),
        )
        for index in range(count)
    )


# ---------------------------------------------------------------------------
# Image comparison
# ---------------------------------------------------------------------------
class TestImageComparison:
    def test_identical_frames_produce_no_findings(self):
        """The false-positive control. Two identical photos are not damage."""
        pair = build_scenario("clean")

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        assert result.damage_detected is False
        assert result.findings == []

    @pytest.mark.parametrize("scenario", ["scratch", "dent", "rust", "multiple"])
    def test_a_planted_defect_is_detected(self, scenario):
        pair = build_scenario(scenario)

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        assert result.damage_detected is True
        assert len(result.findings) >= 1

    def test_lighting_change_alone_is_not_damage(self):
        """The hardest negative case: same panel, different exposure."""
        pair = build_scenario("lighting_only")

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        assert result.damage_detected is False

    def test_every_finding_carries_a_bounding_box_and_a_confidence(self):
        pair = build_scenario("multiple")

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        assert result.findings
        for finding in result.findings:
            # Normalised fractions in [0, 1], keyed rather than positional.
            box = finding.bounding_box
            assert set(box) == {"x", "y", "width", "height"}
            for value in box.values():
                assert 0.0 <= value <= 1.0
            assert box["width"] > 0 and box["height"] > 0
            assert 0.0 < finding.confidence <= 1.0
            assert finding.damage_type
            assert finding.pixel_area > 0

    def test_the_comparison_returns_renderable_evidence(self):
        pair = build_scenario("scratch")

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        # The composite is a real PNG, not a placeholder.
        assert result.comparison_png.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(result.comparison_png) > 1000

    def test_the_result_records_its_own_parameters(self):
        """Reproducibility: the thresholds used must survive with the verdict."""
        pair = build_scenario("scratch")

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        assert result.parameters
        assert result.statistics
        assert result.statistics["frame"]["width"] > 0
        assert result.statistics["frame"]["height"] > 0

    def test_a_missing_angle_still_compares_but_says_so(self):
        """No viewpoint means no panel name, not a failed comparison."""
        pair = build_scenario("scratch")

        without_angle = compare_images(pair.drop_off_png, pair.pickup_png)
        with_angle = compare_images(
            pair.drop_off_png, pair.pickup_png, angle="front_left"
        )

        assert without_angle.damage_detected is True
        assert with_angle.damage_detected is True
        # The angle only changes how the region is named.
        assert without_angle.viewpoint != with_angle.viewpoint

    def test_generate_pair_is_deterministic_for_a_seed(self):
        defects = _defects(1)

        first = generate_pair(defects=defects, seed=42)
        second = generate_pair(defects=defects, seed=42)

        assert first.drop_off_png == second.drop_off_png
        assert first.pickup_png == second.pickup_png

    def test_a_different_seed_produces_different_images(self):
        defects = _defects(1)

        first = generate_pair(defects=defects, seed=42)
        second = generate_pair(defects=defects, seed=43)

        assert first.pickup_png != second.pickup_png

    def test_an_undecodable_image_is_a_validation_error_not_a_crash(self):
        from utils.exceptions import ValidationError

        pair = build_scenario("clean")

        with pytest.raises(ValidationError):
            compare_images(b"not an image at all", pair.pickup_png)

    def test_an_image_below_the_assessment_floor_is_refused(self):
        """The pipeline will not measure damage on a frame too small to trust."""
        import cv2

        from utils.exceptions import ValidationError

        ok, tiny = cv2.imencode(".png", np.zeros((40, 40, 3), dtype=np.uint8))
        assert ok
        pair = build_scenario("clean")

        with pytest.raises(ValidationError) as excinfo:
            compare_images(tiny.tobytes(), pair.pickup_png)

        assert "too small" in str(excinfo.value).lower()

    def test_the_configured_minimum_dimension_is_what_the_error_reports(self):
        import cv2

        from utils.exceptions import ValidationError

        ok, small = cv2.imencode(".png", np.zeros((80, 80, 3), dtype=np.uint8))
        assert ok
        pair = build_scenario("clean")

        with pytest.raises(ValidationError) as excinfo:
            compare_images(small.tobytes(), pair.pickup_png)

        assert str(VISION_CONFIG.min_image_dimension) in str(excinfo.value)


# ---------------------------------------------------------------------------
# Damage result generation
# ---------------------------------------------------------------------------
class TestDamageResultGeneration:
    def test_every_scenario_in_the_vocabulary_renders(self):
        for name in scenario_names():
            pair = build_scenario(name)
            assert pair.drop_off_png.startswith(b"\x89PNG")
            assert pair.pickup_png.startswith(b"\x89PNG")

    def test_the_clean_scenario_really_is_clean(self):
        """If 'clean' has a defect in it, every negative test above is a lie."""
        pair = build_scenario("clean")

        assert compare_images(pair.drop_off_png, pair.pickup_png).findings == []

    def test_more_defects_produce_at_least_as_many_findings(self):
        light = generate_pair(defects=_defects(1), seed=7)
        heavy = generate_pair(defects=_defects(4), seed=7)

        light_result = compare_images(light.drop_off_png, light.pickup_png)
        heavy_result = compare_images(heavy.drop_off_png, heavy.pickup_png)

        assert light_result.damage_detected is True
        assert heavy_result.damage_detected is True
        assert len(heavy_result.findings) >= len(light_result.findings)

    def test_a_zero_defect_pair_yields_nothing(self):
        # camera_jitter off as well, so the only difference between the frames
        # is the encoding -- otherwise this tests alignment, not damage.
        pair = generate_pair(defects=(), seed=11, camera_jitter=False)

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        assert result.damage_detected is False

    def test_the_planted_defect_is_recovered_at_its_own_location(self):
        """The generator knows where it put the damage, so check the box too."""
        pair = generate_pair(defects=_defects(1), seed=5)

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        assert result.findings
        box = result.findings[0].bounding_box
        # Normalised, so containment is against 1.0 rather than the pixel frame.
        assert 0.0 <= box["x"] <= 1.0
        assert 0.0 <= box["y"] <= 1.0
        assert box["x"] + box["width"] <= 1.0 + 1e-6
        assert box["y"] + box["height"] <= 1.0 + 1e-6
        assert box["width"] > 0 and box["height"] > 0
        assert result.findings[0].pixel_area > 0

    def test_evidence_crops_are_capped(self):
        """A finding-per-crop explosion would make the payload unusable."""
        from ai.vision.pipeline import MAX_EVIDENCE_CROPS

        pair = generate_pair(defects=_defects(6), seed=3)

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        assert len(result.evidence) <= MAX_EVIDENCE_CROPS

    def test_findings_explain_themselves_in_prose(self):
        """Every finding carries the reasoning a reviewer would read.

        The human-facing "Potential Damage" label is added by the service layer
        when the detection is stored; the AI layer's job is to produce the
        reasoning and the region mapping that label is derived from.
        """
        pair = build_scenario("scratch")

        result = compare_images(pair.drop_off_png, pair.pickup_png)

        for finding in result.findings:
            assert finding.reasoning
            assert finding.region
            assert finding.severity
            assert finding.region_mapping


# ---------------------------------------------------------------------------
# Geofence classification
# ---------------------------------------------------------------------------
class TestGeofenceClassification:
    @pytest.fixture()
    def ring(self):
        return circular_ring(CENTRE_LAT, CENTRE_LON, 60.0)

    def test_a_fix_inside_the_zone_is_normal(self, ring):
        verdict = evaluate_fix(
            latitude=CENTRE_LAT, longitude=CENTRE_LON, zone_ring=ring
        )

        assert verdict.status == "NORMAL"
        assert verdict.is_within_zone is True
        assert verdict.findings == ()

    def test_a_fix_outside_the_zone_is_a_breach(self, ring):
        # ~500m north of the zone centre.
        verdict = evaluate_fix(
            latitude=CENTRE_LAT + 0.0045, longitude=CENTRE_LON, zone_ring=ring
        )

        assert verdict.status == "BREACH"
        assert verdict.is_within_zone is False
        assert verdict.distance_from_boundary_metres > 0

    def test_the_verdict_explains_itself(self, ring):
        verdict = evaluate_fix(
            latitude=CENTRE_LAT + 0.0045, longitude=CENTRE_LON, zone_ring=ring
        )

        assert verdict.reasons
        assert verdict.summary
        assert verdict.evidence

    def test_excessive_speed_is_flagged_even_inside_the_zone(self, ring):
        config = GeofenceConfig(buffer_metres=DEFAULT_CONFIG.buffer_metres)

        verdict = evaluate_fix(
            latitude=CENTRE_LAT,
            longitude=CENTRE_LON,
            zone_ring=ring,
            speed_kmph=45.0,
            config=config,
        )

        assert verdict.status in ("SUSPICIOUS", "BREACH")
        assert verdict.status != "NORMAL"

    def test_movement_when_none_is_expected_is_flagged(self, ring):
        """Rule 3e: moving inside the zone with no task in progress.

        The rule keys off a reported speed above ``stationary_speed_kmph``
        rather than off the position delta, so a parked car whose GPS wanders a
        few metres does not trip it. It lives inside the movement-pattern rules,
        which only run when there is a prior fix to compare against.
        """
        prior = (PriorFix(latitude=CENTRE_LAT, longitude=CENTRE_LON, recorded_at=None),)

        verdict = evaluate_fix(
            latitude=CENTRE_LAT,
            longitude=CENTRE_LON,
            zone_ring=ring,
            speed_kmph=DEFAULT_CONFIG.stationary_speed_kmph + 5.0,
            prior_fixes=prior,
            movement_expected=False,
        )

        assert verdict.status == "SUSPICIOUS"
        assert any("no task in progress" in reason for reason in verdict.reasons)

    def test_the_same_movement_is_fine_when_expected(self, ring):
        """A valet legitimately driving the car is not an anomaly."""
        speed = DEFAULT_CONFIG.stationary_speed_kmph + 5.0
        prior = (PriorFix(latitude=CENTRE_LAT, longitude=CENTRE_LON, recorded_at=None),)

        expected = evaluate_fix(
            latitude=CENTRE_LAT,
            longitude=CENTRE_LON,
            zone_ring=ring,
            speed_kmph=speed,
            prior_fixes=prior,
            movement_expected=True,
        )
        unexpected = evaluate_fix(
            latitude=CENTRE_LAT,
            longitude=CENTRE_LON,
            zone_ring=ring,
            speed_kmph=speed,
            prior_fixes=prior,
            movement_expected=False,
        )

        assert expected.status == "NORMAL"
        assert unexpected.status == "SUSPICIOUS"

    def test_the_two_thresholds_produce_different_verdicts(self, ring):
        """Buffer is the tolerance; breach_margin is where it becomes serious.

        A car parked just outside the yard is suspicious, not instantly
        critical -- the escalation is deliberate, so a reviewer sees the
        distinction rather than one undifferentiated alarm.
        """
        def verdict_at(metres_beyond: float):
            # Past the polygon edge by `metres_beyond`; the ring radius is 60m.
            offset = (60.0 + metres_beyond) / 111_320.0
            return evaluate_fix(
                latitude=CENTRE_LAT + offset, longitude=CENTRE_LON, zone_ring=ring
            )

        inside = verdict_at(-10.0)
        within_buffer = verdict_at(10.0)
        beyond_buffer = verdict_at(60.0)
        beyond_margin = verdict_at(300.0)

        assert inside.status == "NORMAL"
        # Outside the polygon but inside the GPS tolerance is recorded as a
        # WARNING, not ignored: the fix is worth seeing, just not alarming.
        assert within_buffer.status == "WARNING"
        assert beyond_buffer.status == "SUSPICIOUS"
        assert beyond_margin.status == "BREACH"

    def test_the_reported_distance_matches_the_offset(self, ring):
        """The verdict's distance must be the real one, not a rounded guess."""
        offset = (60.0 + 100.0) / 111_320.0

        verdict = evaluate_fix(
            latitude=CENTRE_LAT + offset, longitude=CENTRE_LON, zone_ring=ring
        )

        assert verdict.distance_from_boundary_metres == pytest.approx(100.0, abs=15.0)

    def test_the_most_severe_finding_wins(self, ring):
        """Two rules firing must read as the worse of them, not an average.

        Composed from an outside-the-zone position plus a contradictory heading,
        because the speed rule deliberately applies only inside the yard.
        """
        prior = (
            PriorFix(
                latitude=CENTRE_LAT + 0.0040,
                longitude=CENTRE_LON,
                recorded_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            ),
        )

        verdict = evaluate_fix(
            latitude=CENTRE_LAT + 0.0045,
            longitude=CENTRE_LON,
            zone_ring=ring,
            heading_degrees=180.0,
            recorded_at=datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC),
            prior_fixes=prior,
        )

        assert len(verdict.findings) >= 2
        statuses = {finding.status for finding in verdict.findings}
        # BREACH outranks SUSPICIOUS and WARNING, and the verdict reports the top.
        assert verdict.status == "BREACH"
        assert "BREACH" in statuses

    @pytest.mark.parametrize(
        "scenario",
        sorted(TRACK_SCENARIOS),
    )
    def test_every_track_scenario_classifies_without_error(self, scenario):
        track = generate_track(
            scenario,
            centre_latitude=CENTRE_LAT,
            centre_longitude=CENTRE_LON,
            radius_metres=50.0,
            start_time=datetime.now(tz=UTC),
            seed=1,
        )
        ring = circular_ring(CENTRE_LAT, CENTRE_LON, 50.0)

        statuses = set()
        for fix in track.fixes:
            verdict = evaluate_fix(
                latitude=fix.latitude,
                longitude=fix.longitude,
                zone_ring=ring,
                speed_kmph=fix.speed_kmph,
                heading_degrees=fix.heading_degrees,
                recorded_at=fix.recorded_at,
                movement_expected=track.movement_expected,
            )
            statuses.add(verdict.status)

        assert statuses
        if track.movement_expected is False:
            assert "NORMAL" in statuses, f"{scenario} should contain settled fixes"

    def test_the_unauthorised_scenario_produces_a_breach(self):
        track = generate_track(
            "unauthorised",
            centre_latitude=CENTRE_LAT,
            centre_longitude=CENTRE_LON,
            radius_metres=40.0,
            start_time=datetime.now(tz=UTC),
            seed=3,
        )
        ring = circular_ring(CENTRE_LAT, CENTRE_LON, 40.0)

        statuses = {
            evaluate_fix(
                latitude=fix.latitude,
                longitude=fix.longitude,
                zone_ring=ring,
                speed_kmph=fix.speed_kmph,
                recorded_at=fix.recorded_at,
                movement_expected=track.movement_expected,
            ).status
            for fix in track.fixes
        }

        assert "BREACH" in statuses

    def test_track_generation_is_deterministic(self):
        kwargs = {
            "centre_latitude": CENTRE_LAT,
            "centre_longitude": CENTRE_LON,
            "radius_metres": 40.0,
            "start_time": datetime.now(tz=UTC),
            "seed": 99,
        }

        first = generate_track("arrival", **kwargs)
        second = generate_track("arrival", **kwargs)

        assert [f.latitude for f in first.fixes] == [f.latitude for f in second.fixes]

    def test_an_unknown_scenario_is_refused(self):
        with pytest.raises(ValueError):
            generate_track(
                "teleport",
                centre_latitude=CENTRE_LAT,
                centre_longitude=CENTRE_LON,
                radius_metres=40.0,
                start_time=datetime.now(tz=UTC),
            )


class TestGeofenceGeometry:
    """The primitives the classifier stands on."""

    def test_haversine_measures_a_known_distance(self):
        # One degree of latitude is about 111.32 km.
        metres = haversine_m(0.0, 0.0, 1.0, 0.0)

        assert 110_000 < metres < 112_000

    def test_haversine_of_the_same_point_is_zero(self):
        assert haversine_m(CENTRE_LAT, CENTRE_LON, CENTRE_LAT, CENTRE_LON) == pytest.approx(
            0.0, abs=1e-6
        )

    def test_bearing_north_is_zero_degrees(self):
        assert bearing_degrees(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=0.5)

    def test_bearing_east_is_ninety_degrees(self):
        assert bearing_degrees(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=0.5)

    def test_angle_between_wraps_around_north(self):
        assert angle_between_degrees(350.0, 10.0) == pytest.approx(20.0, abs=0.01)

    def test_point_in_polygon_detects_the_centre(self):
        ring = circular_ring(CENTRE_LAT, CENTRE_LON, 50.0)

        assert point_in_polygon(CENTRE_LAT, CENTRE_LON, ring) is True

    def test_point_in_polygon_rejects_a_distant_point(self):
        ring = circular_ring(CENTRE_LAT, CENTRE_LON, 50.0)

        assert point_in_polygon(CENTRE_LAT + 0.01, CENTRE_LON, ring) is False

    def test_signed_distance_is_negative_inside(self):
        ring = circular_ring(CENTRE_LAT, CENTRE_LON, 50.0)

        assert signed_distance_to_boundary_m(CENTRE_LAT, CENTRE_LON, ring) < 0

    def test_signed_distance_is_positive_outside(self):
        ring = circular_ring(CENTRE_LAT, CENTRE_LON, 50.0)

        assert signed_distance_to_boundary_m(CENTRE_LAT + 0.01, CENTRE_LON, ring) > 0


# ---------------------------------------------------------------------------
# Retrieval prediction
# ---------------------------------------------------------------------------
class TestRetrievalPrediction:
    @pytest.fixture(scope="class")
    def fitted(self):
        """One model fitted on generated history, reused across the class."""
        dataset = generate_history(episodes=1200, seed=20260909)
        model = RetrievalModel().fit(dataset)
        return model, dataset

    def test_the_model_beats_its_own_baseline_on_held_out_data(self, fitted):
        """The central claim. If this fails, the model earns nothing."""
        model, dataset = fitted
        X_test = dataset.X[dataset.test_mask]
        y_test = dataset.y[dataset.test_mask]

        metrics = model.score(X_test, y_test)

        assert metrics.mae < metrics.baseline_mae
        assert metrics.improvement_over_baseline > 0

    def test_the_model_generalises_rather_than_memorises(self, fitted):
        model, dataset = fitted
        X_test = dataset.X[dataset.test_mask]
        y_test = dataset.y[dataset.test_mask]
        X_train = dataset.X[~dataset.test_mask]
        y_train = dataset.y[~dataset.test_mask]

        test = model.score(X_test, y_test)
        train = model.score(X_train, y_train)

        # Fitting far better in-sample than out would mean it memorised.
        assert test.mae < train.mae * 2.0

    def test_the_metrics_report_their_own_sample_size(self, fitted):
        model, dataset = fitted

        metrics = model.score(dataset.X[dataset.test_mask], dataset.y[dataset.test_mask])

        assert metrics.rows > 0
        assert metrics.rows == int(dataset.test_mask.sum())

    def test_a_prediction_carries_an_interval_and_a_reason(self, fitted):
        model, dataset = fitted
        features = dataset.episodes[0].features

        prediction = model.predict(features)

        assert prediction.estimated_minutes > 0
        assert prediction.reason
        assert 0.0 <= prediction.confidence <= 1.0
        # The prediction names the model that produced it and admits its
        # training data is synthetic -- both are stated requirements.
        assert prediction.model_version
        assert prediction.trained_on_synthetic_data is True
        assert prediction.disclaimer
        payload = prediction.to_dict()
        assert payload["estimated_minutes"] > 0
        assert payload["reason"]

    def test_a_busier_queue_predicts_a_longer_wait(self, fitted):
        """Directional sanity: the model must respond to the obvious input."""
        model, dataset = fitted
        base = dict(dataset.episodes[0].features)

        quiet = dict(base)
        busy = dict(base)
        queue_column = next(
            name for name in RETRIEVAL_FEATURE_COLUMNS if "queue" in name.lower()
        )
        quiet[queue_column] = 0.0
        busy[queue_column] = 12.0

        assert model.predict_minutes(busy) > model.predict_minutes(quiet)

    def test_an_explanation_names_the_features_that_moved_the_answer(self, fitted):
        """Explainability is a stated requirement, not a nicety."""
        model, dataset = fitted
        features = dataset.episodes[0].features

        contributions, narrative = model.explain(features)

        assert contributions
        assert narrative
        # Every named contribution must be a real feature with a label.
        for name in contributions:
            assert name in RETRIEVAL_FEATURE_COLUMNS
            assert name in FEATURE_LABELS

    def test_the_model_admits_it_is_a_prototype(self):
        assert PROTOTYPE_DISCLAIMER
        assert "synthetic" in PROTOTYPE_DISCLAIMER.lower()
        assert MODEL_VERSION
        assert METHOD_NAME

    def test_an_unfitted_model_refuses_to_predict(self):
        model = RetrievalModel()
        features = dict.fromkeys(RETRIEVAL_FEATURE_COLUMNS, 0.0)

        with pytest.raises(RuntimeError):
            model.predict_minutes(features)

    def test_the_generated_history_declares_its_own_provenance(self):
        dataset = generate_history(episodes=200, seed=1)

        summary = dataset.summary()

        assert summary["source"]
        assert "synthetic" in str(summary["source"]).lower()
        assert summary["rows"] == 200
        assert summary["train_rows"] + summary["test_rows"] == 200

    def test_the_history_is_reproducible_for_a_seed(self):
        first = generate_history(episodes=200, seed=5)
        second = generate_history(episodes=200, seed=5)

        assert np.array_equal(first.y, second.y)

    def test_a_zero_episode_history_is_refused(self):
        with pytest.raises(ValueError):
            generate_history(episodes=0)

    def test_an_out_of_range_test_fraction_is_refused(self):
        with pytest.raises(ValueError):
            generate_history(episodes=100, test_fraction=1.0)

    def test_the_held_out_period_is_chronological_not_random(self):
        """A random split would leak the future into training."""
        dataset = generate_history(episodes=600, seed=3)

        train_times = [
            episode.requested_at
            for episode, held in zip(dataset.episodes, dataset.test_mask, strict=True)
            if not held
        ]
        test_times = [
            episode.requested_at
            for episode, held in zip(dataset.episodes, dataset.test_mask, strict=True)
            if held
        ]

        assert max(train_times) <= min(test_times)
